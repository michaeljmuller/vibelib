"""Direct edits to the curated layer: the book, its credits, its files' dates.

The other way to change a book is corrections.py, where you say what is wrong in
words and a model works out the edit. This is the plain one, and it is the
default for a reason: when you already know the right answer, describing it to a
model so the model can type it for you is a worse way to type it. No call, no
proposal, no confidence score, no review step -- the form shows what is stored
and stores what the form shows.

That equivalence is also the difference from a correction. A correction sends
only the fields it means to change, and absence means "leave alone", so it can
never clear anything. A form submits the whole record every time, so a blank
field is a real value: emptying the position box removes the position. Clearing
is the thing a correction structurally cannot do, and the thing a form gets for
free.

What is NOT editable here is the raw epubs/m4bs rows. They record what the file
itself said (see schema.sql) -- evidence, not curation. If an epub's dc:date is
a reissue date, the book's publication_date is the thing to fix; rewriting the
epub's would be falsifying the record of the file. Acquisition dates sit outside
those tables for exactly this reason (migration 005), which is why they are here.
"""

import datetime
import logging

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import auth, db
from .ingest import store
from .ingest.apply import _resolve_person, _resolve_series
from .ingest.normalize import norm_language, sort_title

log = logging.getLogger("uvicorn.error")

router = APIRouter(
    prefix="/api/admin",
    dependencies=[Depends(auth.require_admin)],
)


# --- what the form sends -----------------------------------------------------


class PersonInput(BaseModel):
    """Either an existing person the form picked, or a name to resolve.

    The browser sends an id when what was typed matched a name it already had,
    which is the common case and the unambiguous one. A bare name still goes
    through find_person_exact server-side, so typing an existing person's name
    exactly links them rather than making a second row -- the picker is a
    convenience, not the thing that keeps people unique.
    """

    id: int | None = None
    name: str | None = None

    def to_action(self) -> dict:
        if self.id is not None:
            return {"link": self.id}
        name = (self.name or "").strip()
        if not name:
            raise ValueError("an author needs a name")
        return {"create": {"name": name}}


class SeriesInput(BaseModel):
    id: int | None = None
    name: str | None = None

    def to_action(self) -> dict | None:
        if self.id is not None:
            return {"link": self.id}
        name = (self.name or "").strip()
        if not name:
            return None
        return {"create": {"name": name, "sort_name": sort_title(name)}}


class BookEdit(BaseModel):
    title: str
    series: SeriesInput | None = None
    series_position: int | None = None
    publication_date: datetime.date | None = None
    language: str | None = None
    authors: list[PersonInput] = []


class AcquiredOn(BaseModel):
    acquired_on: datetime.date


# --- the work ----------------------------------------------------------------


def update_book(conn: psycopg.Connection, book_id: int, edit: BookEdit) -> None:
    """Write the submitted record over the stored one, in one transaction.

    Every column the form owns is written every time, including to NULL. That is
    what makes the form able to clear a field, and it is safe here in a way it
    would not be from a model: these values came from a person looking at the
    current ones.
    """
    title = edit.title.strip()
    if not title:
        raise ValueError("a book needs a title")

    with conn.transaction():
        if conn.execute(
            "SELECT 1 FROM books WHERE id = %s", (book_id,)
        ).fetchone() is None:
            raise ValueError(f"no book {book_id}")

        series_action = edit.series.to_action() if edit.series else None
        conn.execute(
            """UPDATE books
                  SET title = %s, sort_title = %s, series_id = %s,
                      series_position = %s, publication_date = %s, language = %s
                WHERE id = %s""",
            (
                title,
                sort_title(title),
                _resolve_series(conn, series_action),
                edit.series_position,
                edit.publication_date,
                norm_language(edit.language),
                book_id,
            ),
        )

        # Replaced rather than merged: the form submitted the whole credit list
        # in the order it should end up in, and there is no way to express
        # "remove this author" by merging.
        author_ids = [_resolve_person(conn, a.to_action()) for a in edit.authors]
        conn.execute("DELETE FROM book_authors WHERE book_id = %s", (book_id,))
        for position, author_id in enumerate(author_ids, start=1):
            conn.execute(
                """INSERT INTO book_authors (book_id, author_id, position)
                   VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                (book_id, author_id, position),
            )


# --- routes ------------------------------------------------------------------


@router.put("/books/{book_id}")
def api_update_book(book_id: int, edit: BookEdit):
    with db.pool.connection() as conn:
        try:
            update_book(conn, book_id, edit)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        conn.commit()
    log.info("edited book %s", book_id)
    return {"book_id": book_id}


@router.put("/assets/{asset_type}/{asset_id}/acquired-on")
def api_set_acquired_on(asset_type: str, asset_id: int, body: AcquiredOn):
    """When one file was acquired.

    Per asset, and deliberately not per book: the date is recorded against the
    file (migration 005), the card shows the earliest across a book's files, and
    for roughly one book in nine the ebook and the audiobook carry genuinely
    different dates. "The book's acquisition date" would have to pick one of
    them to overwrite, so this asks which file instead of guessing.
    """
    if asset_type not in store.TABLES:
        raise HTTPException(400, "asset_type must be epub or m4b")
    with db.pool.connection() as conn:
        exists = conn.execute(
            f"SELECT 1 FROM {store.TABLES[asset_type]} WHERE id = %s", (asset_id,)
        ).fetchone()
        if exists is None:
            raise HTTPException(404, "no such asset")
        store.set_acquired_on(conn, asset_type, asset_id, body.acquired_on)
        conn.commit()
    log.info("set %s:%s acquired_on to %s", asset_type, asset_id, body.acquired_on)
    return {"acquired_on": body.acquired_on}
