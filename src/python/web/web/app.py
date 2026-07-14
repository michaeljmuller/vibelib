"""FastAPI app: JSON API over the curated library, plus the static browse UI.

The API is the same surface a mobile client would use; the UI in static/ is one
consumer of it.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import covers, db, s3

STATIC_DIR = Path(__file__).parent / "static"

AssetType = Literal["epub", "m4b"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.pool.open(wait=True, timeout=30)
    yield
    db.pool.close()


app = FastAPI(title="vibelib", lifespan=lifespan)


def _with_cover(book: dict[str, Any]) -> dict[str, Any]:
    """Attach the cover reference the UI should fetch: the book's epub cover if
    one was extracted, else its m4b cover, else nothing."""
    for asset_type, asset_id in (("epub", book["epub_id"]), ("m4b", book["m4b_id"])):
        if asset_id and covers.find_cover(asset_type, asset_id):
            book["cover"] = {"type": asset_type, "id": asset_id}
            return book
    book["cover"] = None
    return book


@app.get("/api/books")
def api_books(
    q: str | None = None,
    author_id: int | None = None,
    series_id: int | None = None,
    language: str | None = None,
    format: AssetType | None = None,
    sort: Literal["title", "date", "series", "acquired"] | None = None,
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    result = db.list_books(
        q=q,
        author_id=author_id,
        series_id=series_id,
        language=language,
        fmt=format,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    result["items"] = [_with_cover(b) for b in result["items"]]
    return result


@app.get("/api/books/{book_id}")
def api_book(book_id: int):
    book = db.get_book(book_id)
    if book is None:
        raise HTTPException(404, "no such book")
    _with_cover(book)
    book["siblings"] = [_with_cover(s) for s in book["siblings"]]
    for edition in (*book["epubs"], *book["m4bs"]):
        edition["size"] = s3.size(edition["s3_key"])
    return book


@app.get("/api/authors")
def api_authors():
    return db.list_authors()


@app.get("/api/series")
def api_series():
    return db.list_series()


@app.get("/api/languages")
def api_languages():
    return db.list_languages()


@app.get("/covers/{asset_type}/{asset_id}")
def cover(asset_type: AssetType, asset_id: int):
    path = covers.find_cover(asset_type, asset_id)
    if path is None:
        raise HTTPException(404, "no cover")
    # Cover files are immutable for a given asset id.
    return FileResponse(
        path,
        media_type=covers.media_type(path),
        headers={"Cache-Control": "public, max-age=604800"},
    )


@app.get("/download/{asset_type}/{asset_id}")
def download(asset_type: AssetType, asset_id: int):
    s3_key = db.get_s3_key(asset_type, asset_id)
    if s3_key is None:
        raise HTTPException(404, "no such asset")
    return RedirectResponse(s3.presign(s3_key), status_code=302)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
