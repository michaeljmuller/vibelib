"""OPDS: devices sign in from an htpasswd file, reach only ebooks, and see feeds
a reader can parse."""

import base64
import os
from datetime import date
from xml.etree import ElementTree as ET

os.environ.setdefault("OAUTH_SESSION_SECRET", "test-secret")
os.environ.setdefault("OAUTH_GOOGLE_CLIENT_ID", "test-client")
os.environ.setdefault("OAUTH_GOOGLE_CLIENT_SECRET", "test-client-secret")

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from web import auth, opds

A = "{http://www.w3.org/2005/Atom}"


def line(name: str, password: str, prefix: str = "$2y$") -> str:
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()
    return f"{name}:{prefix}{hashed[4:]}\n"


def basic(name="kindle-alex", password="correct horse"):
    token = base64.b64encode(f"{name}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


BOOKS = [
    {"id": 1, "title": "The Archmage", "authors": ["R. A. Salvatore"], "language": "en",
     "publication_date": date(2019, 10, 1), "series_name": "Homecoming", "series_position": 1,
     "acquired_on": date(2020, 1, 2)},
    {"id": 2, "title": "Gone", "authors": [], "language": None, "publication_date": None,
     "series_name": None, "series_position": None, "acquired_on": None},
]
EPUBS = {
    1: [{"id": 10, "description": "<p>Drizzt &amp; friends.</p>"}, {"id": 11, "description": None}],
    2: [],  # unlinked between the two queries: must not be listed
}


@pytest.fixture
def htpasswd(tmp_path, monkeypatch):
    path = tmp_path / "htpasswd"
    path.write_text("# devices\n" + line("kindle-alex", "correct horse"))
    monkeypatch.setattr(auth, "devices", auth.Htpasswd(path))
    return path


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def list_books(**kwargs):
        seen.append(kwargs)
        return {"total": 120, "items": [dict(b) for b in BOOKS]}

    monkeypatch.setattr(opds.db, "list_books", list_books)
    monkeypatch.setattr(opds.db, "list_book_epubs", lambda ids: {i: EPUBS[i] for i in ids})
    monkeypatch.setattr(opds.db, "list_authors",
                        lambda epub_only: seen.append({"authors": epub_only}) or
                        [{"id": n, "name": f"Author {n}", "book_count": 1} for n in range(60)])
    monkeypatch.setattr(opds.db, "list_series",
                        lambda epub_only: seen.append({"series": epub_only}) or
                        [{"id": 3, "name": "Homecoming", "book_count": 2}])
    monkeypatch.setattr(opds.db, "get_person_name", lambda i: "R. A. Salvatore" if i == 5 else None)
    monkeypatch.setattr(opds.db, "get_series_name", lambda i: "Homecoming" if i == 3 else None)
    monkeypatch.setattr(opds.covers, "find_cover", lambda t, i: None)
    return seen


@pytest.fixture
def client(htpasswd, calls, monkeypatch):
    monkeypatch.setattr(auth, "DEV_USER", None)
    monkeypatch.setattr(auth, "API_TOKEN", None)
    monkeypatch.setattr(auth.db, "get_user", lambda email: pytest.fail("looked up a user"))

    app = FastAPI()
    app.add_middleware(auth.RequireLogin)
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    app.include_router(opds.router)

    @app.get("/download/{asset_type}/{asset_id}")
    def download(asset_type: str, asset_id: int):
        return {"ok": True}

    @app.get("/covers/{asset_type}/thumb/{asset_id}")
    def thumb(asset_type: str, asset_id: int):
        return {"ok": True}

    @app.get("/api/books")
    def books():
        return []

    return TestClient(app)


def feed(response) -> ET.Element:
    assert response.status_code == 200, response.text
    return ET.fromstring(response.content)


# --- who gets in ----------------------------------------------------------------


def test_device_reaches_the_catalog_and_epubs(client):
    assert client.get("/opds", headers=basic()).status_code == 200
    assert client.get("/download/epub/10", headers=basic()).status_code == 200
    assert client.get("/covers/epub/thumb/10", headers=basic()).status_code == 200


@pytest.mark.parametrize("path", ["/download/m4b/1", "/covers/m4b/thumb/1", "/api/books"])
def test_device_is_refused_audiobooks_and_the_api(client, path):
    assert client.get(path, headers=basic()).status_code == 403


def test_no_credentials_gets_a_challenge(client):
    r = client.get("/opds")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic ")


@pytest.mark.parametrize("headers", [
    basic(password="wrong"),
    basic(name="kindle-bob"),
    {"Authorization": "Basic not-base64!"},
])
def test_bad_credentials_are_refused(client, headers):
    r = client.get("/opds", headers=headers)
    assert r.status_code == 401
    assert "www-authenticate" in r.headers


def test_removing_the_line_revokes_at_once(client, htpasswd):
    assert client.get("/opds", headers=basic()).status_code == 200  # now remembered
    htpasswd.write_text(line("kindle-bob", "other"))
    assert client.get("/opds", headers=basic()).status_code == 401
    assert client.get("/opds", headers=basic("kindle-bob", "other")).status_code == 200


def test_missing_file_lets_no_device_in(client, htpasswd):
    htpasswd.unlink()
    assert client.get("/opds", headers=basic()).status_code == 401


def test_non_bcrypt_lines_are_ignored(tmp_path):
    path = tmp_path / "htpasswd"
    path.write_text("old:{SHA}W6ph5Mm5Pz8GgiULbPgzG37mj9g=\nplain:password\n"
                    + line("new", "pw", prefix="$2b$"))
    devices = auth.Htpasswd(path)
    assert devices.names() == ["new"]
    assert devices.check("new", "pw")
    assert not devices.check("plain", "password")


def test_device_sets_no_session_cookie(client):
    assert "set-cookie" not in client.get("/opds", headers=basic()).headers


# --- what the feeds say ---------------------------------------------------------


def test_root_links_the_four_sections_and_search(client):
    root = feed(client.get("/opds", headers=basic()))
    hrefs = [e.find(f"{A}link").get("href") for e in root.findall(f"{A}entry")]
    assert hrefs == ["/opds/recent", "/opds/titles", "/opds/authors", "/opds/series"]
    searches = [l.get("href") for l in root.findall(f"{A}link") if l.get("rel") == "search"]
    assert "/opds/opensearch.xml" in searches


def test_book_feed_is_epub_only_and_offers_every_epub(client, calls):
    root = feed(client.get("/opds/recent", headers=basic()))
    assert calls[-1]["fmt"] == "epub"
    entries = root.findall(f"{A}entry")
    assert [e.find(f"{A}title").text for e in entries] == ["The Archmage"]
    links = [l.get("href") for l in entries[0].findall(f"{A}link")
             if l.get("rel") == "http://opds-spec.org/acquisition"]
    assert links == ["/download/epub/10", "/download/epub/11"]
    summary = entries[0].find(f"{A}summary").text
    assert summary == "Homecoming #1\n\nDrizzt & friends."


def test_book_feed_pages(client):
    root = feed(client.get("/opds/titles?offset=50", headers=basic()))
    rels = {l.get("rel"): l.get("href") for l in root.findall(f"{A}link")}
    assert rels["next"] == "/opds/titles?offset=100"
    assert rels["previous"] == "/opds/titles"


def test_search_keeps_the_query_across_pages(client, calls):
    root = feed(client.get("/opds/search?q=drizzt", headers=basic()))
    assert calls[-1]["q"] == "drizzt"
    rels = {l.get("rel"): l.get("href") for l in root.findall(f"{A}link")}
    assert rels["next"] == "/opds/search?offset=50&q=drizzt"


def test_author_and_series_lists_are_epub_only_and_paged(client, calls):
    root = feed(client.get("/opds/authors", headers=basic()))
    assert {"authors": True} in calls
    assert len(root.findall(f"{A}entry")) == 50
    feed(client.get("/opds/series", headers=basic()))
    assert {"series": True} in calls


def test_series_feed_uses_reading_order(client, calls):
    feed(client.get("/opds/series/3", headers=basic()))
    assert calls[-1]["series_id"] == 3
    assert "sort" not in calls[-1]


def test_unknown_author_is_a_404(client):
    assert client.get("/opds/authors/999", headers=basic()).status_code == 404
