"""OPDS 1.2 catalog: the library as an e-reader sees it.

OPDS is a set of Atom feeds. Navigation feeds are menus (authors, series);
acquisition feeds list books, each with a link to download it. A reader app
such as KOReader browses them and downloads what it is asked to.

Ebooks only: an audiobook is never listed, and a book is listed only if it has
an epub. Devices sign in with HTTP Basic auth against an htpasswd file; see
auth.py, which also limits them to /opds, epub downloads and epub covers.

Links are absolute paths ("/opds/series/3"), resolved by the reader against the
URL it fetched, so the feeds are correct behind any host name.
"""

import html
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

from fastapi import APIRouter, HTTPException, Query, Response

from . import covers, db

ATOM = "http://www.w3.org/2005/Atom"
DC = "http://purl.org/dc/terms/"
OPDS = "http://opds-spec.org/2010/catalog"
OPENSEARCH = "http://a9.com/-/spec/opensearch/1.1/"

NAV = "application/atom+xml;profile=opds-catalog;kind=navigation"
ACQ = "application/atom+xml;profile=opds-catalog;kind=acquisition"
OPENSEARCH_TYPE = "application/opensearchdescription+xml"
EPUB = "application/epub+zip"

PAGE = 50

router = APIRouter(prefix="/opds")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sub(parent: ET.Element, tag: str, text: str | None = None, **attrs: str) -> ET.Element:
    el = ET.SubElement(parent, tag, attrs)
    if text is not None:
        el.text = text
    return el


def _link(parent: ET.Element, rel: str, href: str, type: str, title: str | None = None) -> None:
    attrs = {"rel": rel, "href": href, "type": type}
    if title:
        attrs["title"] = title
    ET.SubElement(parent, "link", attrs)


def _href(path: str, **params: Any) -> str:
    params = {k: v for k, v in params.items() if v not in (None, "", 0)}
    return f"{path}?{urlencode(params)}" if params else path


def _feed(feed_id: str, title: str, self_href: str, kind: str) -> ET.Element:
    feed = ET.Element("feed", {"xmlns": ATOM, "xmlns:dc": DC, "xmlns:opds": OPDS,
                               "xmlns:opensearch": OPENSEARCH})
    _sub(feed, "id", f"urn:vibelib:{feed_id}")
    _sub(feed, "title", title)
    _sub(feed, "updated", _now())
    _link(feed, "self", self_href, kind)
    _link(feed, "start", "/opds", NAV)
    _link(feed, "up", "/opds", NAV)
    _link(feed, "search", "/opds/opensearch.xml", OPENSEARCH_TYPE)
    # The same search as a plain template, for readers that skip the
    # OpenSearch description document.
    _link(feed, "search", "/opds/search?q={searchTerms}", ACQ)
    return feed


def _pages(feed: ET.Element, path: str, kind: str, offset: int, total: int, **params: Any) -> None:
    _sub(feed, "opensearch:totalResults", str(total))
    _sub(feed, "opensearch:itemsPerPage", str(PAGE))
    _sub(feed, "opensearch:startIndex", str(offset + 1))
    if offset > 0:
        _link(feed, "previous", _href(path, offset=max(offset - PAGE, 0), **params), kind)
    if offset + PAGE < total:
        _link(feed, "next", _href(path, offset=offset + PAGE, **params), kind)


def _respond(feed: ET.Element, kind: str) -> Response:
    return Response(ET.tostring(feed, encoding="utf-8", xml_declaration=True), media_type=kind)


def _nav_entry(feed: ET.Element, entry_id: str, title: str, href: str, kind: str,
               content: str | None = None) -> None:
    entry = _sub(feed, "entry")
    _sub(entry, "title", title)
    _sub(entry, "id", f"urn:vibelib:{entry_id}")
    _sub(entry, "updated", _now())
    if content:
        _sub(entry, "content", content, type="text")
    _link(entry, "subsection", href, kind)


def _plain(description: str | None) -> str | None:
    """Epub descriptions are often HTML. Readers show the summary as plain text,
    so markup becomes spaces and entities become characters."""
    if not description:
        return None
    text = html.unescape(re.sub(r"<[^>]+>", " ", description))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _timestamp(value: date | datetime | None) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return f"{value.isoformat()}T00:00:00Z"
    return _now()


def _book_entry(feed: ET.Element, book: dict[str, Any], epubs: list[dict[str, Any]]) -> None:
    entry = _sub(feed, "entry")
    _sub(entry, "title", book["title"])
    _sub(entry, "id", f"urn:vibelib:book:{book['id']}")
    _sub(entry, "updated", _timestamp(book.get("acquired_on")))
    for name in book.get("authors") or []:
        _sub(_sub(entry, "author"), "name", name)
    if book.get("language"):
        _sub(entry, "dc:language", book["language"])
    if book.get("publication_date"):
        _sub(entry, "dc:issued", str(book["publication_date"]))

    lines = []
    if book.get("series_name"):
        position = book.get("series_position")
        lines.append(f"{book['series_name']} #{position}" if position else book["series_name"])
    description = next((d for d in (_plain(e["description"]) for e in epubs) if d), None)
    if description:
        lines.append(description)
    if lines:
        _sub(entry, "summary", "\n\n".join(lines), type="text")

    found = ((e["id"], covers.find_cover("epub", e["id"])) for e in epubs)
    cover, path = next(((i, p) for i, p in found if p), (None, None))
    if cover is not None:
        _link(entry, "http://opds-spec.org/image", f"/covers/epub/{cover}", covers.media_type(path))
        # The thumbnail route serves the original when there is no thumbnail,
        # so it may be a PNG, but readers sniff the image and do not mind.
        _link(entry, "http://opds-spec.org/image/thumbnail",
              f"/covers/epub/thumb/{cover}", "image/jpeg")

    many = len(epubs) > 1
    for n, epub in enumerate(epubs, 1):
        _link(entry, "http://opds-spec.org/acquisition", f"/download/epub/{epub['id']}", EPUB,
              title=f"EPUB {n}" if many else "EPUB")


def _book_feed(feed_id: str, title: str, path: str, offset: int, **filters: Any) -> Response:
    query = {k: v for k, v in filters.items() if k == "q"}
    result = db.list_books(fmt="epub", limit=PAGE, offset=offset, **filters)
    feed = _feed(feed_id, title, _href(path, offset=offset, **query), ACQ)
    _pages(feed, path, ACQ, offset, result["total"], **query)
    epubs = db.list_book_epubs([b["id"] for b in result["items"]])
    for book in result["items"]:
        # A book whose only epub was unlinked between the two queries: skip it
        # rather than list a book with nothing to download.
        if epubs.get(book["id"]):
            _book_entry(feed, book, epubs[book["id"]])
    return _respond(feed, ACQ)


@router.get("")
def root():
    feed = _feed("root", "vibelib", "/opds", NAV)
    _nav_entry(feed, "recent", "Recently added", "/opds/recent", ACQ)
    _nav_entry(feed, "titles", "Titles A–Z", "/opds/titles", ACQ)
    _nav_entry(feed, "authors", "Authors", "/opds/authors", NAV)
    _nav_entry(feed, "series", "Series", "/opds/series", NAV)
    return _respond(feed, NAV)


@router.get("/recent")
def recent(offset: int = Query(0, ge=0)):
    return _book_feed("recent", "Recently added", "/opds/recent", offset, sort="acquired")


@router.get("/titles")
def titles(offset: int = Query(0, ge=0)):
    return _book_feed("titles", "Titles A–Z", "/opds/titles", offset, sort="title")


@router.get("/search")
def search(q: str = Query(..., min_length=1), offset: int = Query(0, ge=0)):
    return _book_feed(f"search:{q}", f"Search: {q}", "/opds/search", offset, q=q, sort="title")


def _list_feed(feed_id: str, title: str, path: str, offset: int,
               rows: list[dict[str, Any]]) -> Response:
    feed = _feed(feed_id, title, _href(path, offset=offset), NAV)
    _pages(feed, path, NAV, offset, len(rows))
    for row in rows[offset:offset + PAGE]:
        count = row["book_count"]
        _nav_entry(feed, f"{feed_id}:{row['id']}", row["name"], f"{path}/{row['id']}", ACQ,
                   content=f"{count} book{'' if count == 1 else 's'}")
    return _respond(feed, NAV)


@router.get("/authors")
def authors(offset: int = Query(0, ge=0)):
    return _list_feed("authors", "Authors", "/opds/authors", offset,
                      db.list_authors(epub_only=True))


@router.get("/authors/{author_id}")
def author(author_id: int, offset: int = Query(0, ge=0)):
    name = db.get_person_name(author_id)
    if name is None:
        raise HTTPException(404, "no such author")
    return _book_feed(f"authors:{author_id}", name, f"/opds/authors/{author_id}", offset,
                      author_id=author_id, sort="title")


@router.get("/series")
def series_list(offset: int = Query(0, ge=0)):
    return _list_feed("series", "Series", "/opds/series", offset,
                      db.list_series(epub_only=True))


@router.get("/series/{series_id}")
def series(series_id: int, offset: int = Query(0, ge=0)):
    name = db.get_series_name(series_id)
    if name is None:
        raise HTTPException(404, "no such series")
    # No sort: list_books puts a single series in reading order by default.
    return _book_feed(f"series:{series_id}", name, f"/opds/series/{series_id}", offset,
                      series_id=series_id)


@router.get("/opensearch.xml")
def opensearch():
    doc = ET.Element("OpenSearchDescription", {"xmlns": OPENSEARCH})
    _sub(doc, "ShortName", "vibelib")
    _sub(doc, "Description", "Search the library by title, series or author")
    _sub(doc, "InputEncoding", "UTF-8")
    _sub(doc, "OutputEncoding", "UTF-8")
    _sub(doc, "Url", type=ACQ, template="/opds/search?q={searchTerms}")
    return Response(ET.tostring(doc, encoding="utf-8", xml_declaration=True),
                    media_type=OPENSEARCH_TYPE)
