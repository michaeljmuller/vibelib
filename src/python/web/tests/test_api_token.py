"""The API token: it reaches the acquisition-date operations and nothing else."""

import os

os.environ.setdefault("OAUTH_SESSION_SECRET", "test-secret")
os.environ.setdefault("OAUTH_GOOGLE_CLIENT_ID", "test-client")
os.environ.setdefault("OAUTH_GOOGLE_CLIENT_SECRET", "test-client-secret")

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from web import auth

TOKEN = "t" * 40


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(auth.RequireLogin)
    app.add_middleware(SessionMiddleware, secret_key="test-secret")

    @app.get("/api/books")
    def books(request: Request):
        return {"user": auth.current_user(request)["email"]}

    @app.get("/api/books/{book_id}")
    def book(book_id: int):
        return {"id": book_id}

    @app.put("/api/admin/assets/{asset_type}/{asset_id}/acquired-on")
    def set_date(asset_type: str, asset_id: int, admin: dict = Depends(auth.require_admin)):
        return {"ok": True}

    @app.put("/api/admin/books/{book_id}/publication-date")
    def set_pub_date(book_id: int, admin: dict = Depends(auth.require_admin)):
        return {"ok": True}

    @app.get("/api/users")
    def users(admin: dict = Depends(auth.require_admin)):
        return []

    @app.get("/download/{asset_type}/{asset_id}")
    def download(asset_type: str, asset_id: int):
        return {"bytes": "..."}

    return app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "API_TOKEN", TOKEN)
    monkeypatch.setattr(auth, "DEV_USER", None)
    # No database: a token request must never look anyone up.
    monkeypatch.setattr(auth.db, "get_user", lambda email: pytest.fail("looked up a user"))
    return TestClient(_app())


def bearer(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


def test_scope_allows_the_four_operations(client):
    assert client.get("/api/books?q=archmage", headers=bearer()).json() == {"user": "api-token"}
    assert client.get("/api/books/1311", headers=bearer()).status_code == 200
    r = client.put("/api/admin/assets/m4b/42/acquired-on", headers=bearer(), json={})
    assert r.status_code == 200
    r = client.put("/api/admin/books/1311/publication-date", headers=bearer(), json={})
    assert r.status_code == 200


@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/api/users"),
        ("POST", "/api/users"),
        ("GET", "/download/epub/1"),
        ("PUT", "/api/admin/books/1"),
        ("GET", "/api/admin/books/1/publication-date"),
        ("PUT", "/api/admin/assets/pdf/1/acquired-on"),
        ("DELETE", "/api/admin/assets/epub/1/acquired-on"),
        ("GET", "/api/books/1/extra"),
    ],
)
def test_everything_else_is_refused(client, method, path):
    assert client.request(method, path, headers=bearer()).status_code == 403


def test_wrong_token_is_refused(client):
    assert client.get("/api/books", headers=bearer("x" * 40)).status_code == 401


def test_unset_token_refuses_every_token(client, monkeypatch):
    monkeypatch.setattr(auth, "API_TOKEN", None)
    assert client.get("/api/books", headers=bearer()).status_code == 401


def test_token_sets_no_session_cookie(client):
    r = client.get("/api/books", headers=bearer())
    assert "set-cookie" not in r.headers


def test_without_a_token_the_session_gate_still_applies(client):
    assert client.get("/api/books").status_code == 401


def test_short_token_stops_startup(monkeypatch):
    monkeypatch.setattr(auth, "API_TOKEN", "short")
    with pytest.raises(RuntimeError, match="at least 32"):
        auth.check_api_token()
