"""FastAPI app: JSON API over the curated library, plus the static browse UI.

The API is the same surface a mobile client would use; the UI in static/ is one
consumer of it. Everything below is private -- see auth.py for the gate and who
gets through it.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import auth, covers, db, migrate, s3

STATIC_DIR = Path(__file__).parent / "static"

AssetType = Literal["epub", "m4b"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Before anything is served: refuse to run in a configuration that would leave
    # the library open to the world.
    auth.check_dev_user()
    db.pool.open(wait=True, timeout=30)
    # And do not serve until the schema matches the code that is about to run
    # against it. A failure here is a failure to start, on purpose.
    migrate.run(db.pool)
    yield
    db.pool.close()


app = FastAPI(title="vibelib", lifespan=lifespan)

# Order matters, and reads backwards: add_middleware puts each new layer *outside*
# the previous one, so the session must be added last to run first. RequireLogin
# reads request.session, which does not exist until SessionMiddleware has decoded
# the cookie.
app.add_middleware(auth.RequireLogin)
app.add_middleware(
    SessionMiddleware,
    secret_key=auth.SESSION_SECRET,
    same_site="lax",  # survives the redirect back from Google; blocks cross-site sends
    # Secure cookies over https, plain over a localhost dev origin -- where a Secure
    # cookie would simply never be stored, and login would fail in a baffling way.
    https_only=auth.BASE_URL.startswith("https://"),
    max_age=30 * 24 * 3600,
)
app.include_router(auth.router)


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
def download(asset_type: AssetType, asset_id: int, request: Request):
    s3_key = db.get_s3_key(asset_type, asset_id)
    if s3_key is None:
        raise HTTPException(404, "no such asset")

    try:
        obj = s3.fetch(s3_key, asset_type, request.headers.get("range"))
    except s3.NotFound:
        # The row says the file is there and the bucket disagrees. Whoever asked for
        # it cannot do anything about that, so it is a 404 to them either way.
        raise HTTPException(404, "no such asset") from None
    except s3.BadRange:
        raise HTTPException(416, "requested range not satisfiable") from None

    headers = {
        "Content-Disposition": f'attachment; filename="{obj.filename}"',
        "Content-Length": str(obj.length),
        # Advertised so audiobook players know they may seek rather than pulling a
        # whole m4b to play the middle of it.
        "Accept-Ranges": "bytes",
    }
    if obj.content_range:
        headers["Content-Range"] = obj.content_range

    return StreamingResponse(
        obj.body,
        status_code=206 if obj.content_range else 200,
        media_type=obj.media_type,
        headers=headers,
    )


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login")
def login_page():
    return FileResponse(STATIC_DIR / "login.html")


# Public, unavoidably: the login page is made of these. They are the client-side
# half of an API that is itself gated, so they give away nothing but layout.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
