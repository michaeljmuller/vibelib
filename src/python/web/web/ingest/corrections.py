"""Correcting a book that is already in the catalog.

The resolver next door answers "what is this file?" while a file is being added.
This answers the question that only comes up later, looking at a book's own page:
"that's wrong." An admin says what is wrong in plain language -- "the publication
date is wrong", "you got the author wrong, it's Ursula K. Le Guin" -- one model
call turns it into a corrected record, and nothing is written until they accept
it.

Same bargain as the review card, for the same reason: a proposal that has not
been accepted has changed nothing, so there is no pending state to store, go
stale, or clean up. The difference is what is at stake. Adding a file writes new
rows; correcting a book overwrites rows the whole library already reads, and the
one field that cannot be recovered by asking again is the one that was right
before. Hence `to_correction` emitting only the fields actually being changed,
and `apply_correction` touching only the keys it is given.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import anthropic
import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, db
from . import apply, llm, summary

log = logging.getLogger("uvicorn.error")

NAME_SIM_THRESHOLD = 0.4

# Capitalised runs, which is what a name looks like in a sentence typed by a
# person: "it's Ursula K. Le Guin" -> "Ursula K", "Le Guin". Crude on purpose --
# these are only seeds for a trigram lookup, so a false positive costs one row
# in a candidate list and a missed one costs nothing that `apply` won't catch.
_NAME_LIKE = re.compile(r"[A-Z][\w'’.\-]*(?:\s+[A-Z][\w'’.\-]*)*")

# Words that start a sentence rather than a name.
_NOT_NAMES = {"i", "it", "its", "the", "this", "that", "there", "these", "those",
              "he", "she", "they", "we", "you", "a", "an", "and", "but", "no",
              "not", "book", "author", "series", "date", "title", "language"}


@dataclass
class Proposed:
    """A correction and how much to trust it. `correction is None` means there
    is nothing to do -- either the book is gone, or the model could not act on
    what it was told, in which case `notes` says why."""

    book_id: int
    correction: dict[str, Any] | None = None
    confidence: float | None = None
    notes: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


# --- what the model is shown -------------------------------------------------


def current_record(conn: psycopg.Connection, book_id: int) -> dict[str, Any] | None:
    """The book as it stands, plus what its files say.

    The raw asset rows are included because half of these corrections are about
    a date, and the files are the only evidence in the database about where the
    current one came from.
    """
    book = conn.execute(
        """SELECT b.id, b.title, b.series_position, b.publication_date, b.language,
                  s.id AS series_id, s.name AS series_name,
                  ARRAY(SELECT p.name FROM book_authors ba
                         JOIN people p ON p.id = ba.author_id
                        WHERE ba.book_id = b.id ORDER BY ba.position) AS authors
             FROM books b LEFT JOIN series s ON s.id = b.series_id
            WHERE b.id = %s""",
        (book_id,),
    ).fetchone()
    if book is None:
        return None

    record = dict(book)
    record["files"] = conn.execute(
        """SELECT 'epub' AS kind, e.title, e.published_date AS raw_date, e.publisher
             FROM book_epubs be JOIN epubs e ON e.id = be.epub_id
            WHERE be.book_id = %(id)s
            UNION ALL
           SELECT 'm4b', m.title, m.date, m.album
             FROM book_m4bs bm JOIN m4bs m ON m.id = bm.m4b_id
            WHERE bm.book_id = %(id)s""",
        {"id": book_id},
    ).fetchall()
    return record


def _seed_names(instruction: str, record: dict[str, Any]) -> list[str]:
    """Names worth looking up: whatever the correction capitalised, plus what
    the book already credits, so the model can link rather than duplicate."""
    seeds = [
        m.group().strip()
        for m in _NAME_LIKE.finditer(instruction)
        if m.group().strip().casefold() not in _NOT_NAMES and len(m.group().strip()) > 2
    ]
    seeds += list(record.get("authors") or [])
    if record.get("series_name"):
        seeds.append(record["series_name"])
    return list(dict.fromkeys(seeds))  # de-duplicated, order kept


def candidates_for(
    conn: psycopg.Connection, record: dict[str, Any], instruction: str
) -> dict[str, Any]:
    """Short candidate lists for the prompt. Empty is fine and common -- a date
    correction names nobody, and `apply` re-checks every create by normalized
    key anyway, so a missed candidate costs a near-duplicate at worst."""
    seeds = _seed_names(instruction, record)
    if not seeds:
        return {"people": [], "series": []}

    args = {"seeds": seeds, "th": NAME_SIM_THRESHOLD}
    people = conn.execute(
        """SELECT DISTINCT p.id, p.name, p.disambiguator
             FROM people p
            WHERE EXISTS (SELECT 1 FROM unnest(%(seeds)s::text[]) AS s
                           WHERE similarity(lower(p.name), lower(s)) > %(th)s)
            ORDER BY p.name LIMIT 12""",
        args,
    ).fetchall()
    series = conn.execute(
        """SELECT DISTINCT s.id, s.name
             FROM series s
            WHERE EXISTS (SELECT 1 FROM unnest(%(seeds)s::text[]) AS w
                           WHERE similarity(lower(s.name), lower(w)) > %(th)s)
            ORDER BY s.name LIMIT 8""",
        args,
    ).fetchall()
    return {"people": [dict(r) for r in people], "series": [dict(r) for r in series]}


# --- the two halves ----------------------------------------------------------


def propose(
    conn: psycopg.Connection,
    book_id: int,
    instruction: str,
    *,
    client: anthropic.Anthropic | None = None,
) -> Proposed:
    """One model call. Writes nothing, so asking twice costs a second call and
    nothing else."""
    record = current_record(conn, book_id)
    if record is None:
        return Proposed(book_id, notes="no such book")

    if client is None:
        client = anthropic.Anthropic()
    cands = candidates_for(conn, record, instruction)

    try:
        result, usage = llm.correct(client, record, cands, instruction)
        correction = llm.to_correction(result)
    except anthropic.APIError:
        raise  # the caller turns this into a 502; it is worth retrying
    except Exception as exc:  # a malformed correction leaves the book untouched
        log.warning("correction of book %s failed: %s", book_id, exc)
        return Proposed(book_id, notes=str(exc))

    if not correction:
        # The model read the correction and changed nothing. Usually it did not
        # know the right value and said so; its notes are the whole answer.
        return Proposed(book_id, confidence=result.confidence, notes=result.notes,
                        usage=usage)

    log.info(
        "proposed correction to book %s (%s, conf=%.2f)",
        book_id, ", ".join(sorted(correction)), result.confidence,
    )
    return Proposed(book_id, correction=correction, confidence=result.confidence,
                    notes=result.notes, usage=usage)


# --- routes ------------------------------------------------------------------

router = APIRouter(
    prefix="/api/admin/books",
    dependencies=[Depends(auth.require_admin)],
)


class CorrectionRequest(BaseModel):
    instruction: str


class CorrectionAcceptance(BaseModel):
    correction: dict


@router.post("/{book_id}/correction")
def api_correction(book_id: int, body: CorrectionRequest):
    """Work out what the correction means. Writes nothing."""
    instruction = body.instruction.strip()
    if not instruction:
        raise HTTPException(400, "say what is wrong with it")

    with db.pool.connection() as conn:
        try:
            outcome = propose(conn, book_id, instruction)
        except anthropic.APIError as exc:
            log.warning("correction of book %s failed: %s", book_id, exc)
            raise HTTPException(502, "the model could not be reached; try again") from None
        if outcome.correction is None:
            raise HTTPException(422, outcome.notes or "could not act on that correction")

        record = current_record(conn, book_id)
        return {
            "book_id": book_id,
            "correction": outcome.correction,
            "confidence": outcome.confidence,
            "notes": outcome.notes,
            "rows": summary.correction_rows(conn, record, outcome.correction),
        }


@router.post("/{book_id}/correction/accept")
def api_correction_accept(book_id: int, body: CorrectionAcceptance):
    """Apply a reviewed correction. The only route here that writes."""
    with db.pool.connection() as conn:
        try:
            apply.apply_correction(conn, book_id, body.correction)
        except (ValueError, KeyError) as exc:
            # Names an entity that has since been deleted, or came back shaped
            # wrong. Nothing has been written: apply_correction is one
            # transaction.
            raise HTTPException(409, f"could not apply that correction: {exc}") from None
        conn.commit()

    log.info("corrected book %s (%s)", book_id, ", ".join(sorted(body.correction)))
    return {"book_id": book_id}
