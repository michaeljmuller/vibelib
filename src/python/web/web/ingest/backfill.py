"""Fill in publication_date / language on a book that lacks them.

Run right after a proposal is approved. A proposal written before those fields
existed creates a book without them, and a book created from an m4b often has
neither -- an audiobook file carries an edition year at best. Language comes
free from a linked epub when there is one; only what is still missing costs an
LLM call.

Only NULL columns are ever written, so this never overwrites a fact somebody
curated by hand, and running it again is a no-op.
"""

import logging

import anthropic
import psycopg

from . import llm
from .normalize import norm_language, parse_pub_date

log = logging.getLogger("uvicorn.error")


def _book_missing_facts(conn: psycopg.Connection, book_id: int) -> dict | None:
    return conn.execute(
        """
        SELECT b.id, b.title, b.publication_date, b.language,
               s.name AS series, b.series_position,
               ARRAY(SELECT p.name FROM book_authors ba
                     JOIN people p ON p.id = ba.author_id
                     WHERE ba.book_id = b.id ORDER BY ba.position) AS authors,
               ARRAY(SELECT DISTINCT e.language FROM book_epubs be
                     JOIN epubs e ON e.id = be.epub_id
                     WHERE be.book_id = b.id AND e.language IS NOT NULL) AS epub_languages,
               ARRAY(SELECT DISTINCT e.published_date FROM book_epubs be
                     JOIN epubs e ON e.id = be.epub_id
                     WHERE be.book_id = b.id AND e.published_date IS NOT NULL) AS raw_dates,
               ARRAY(SELECT DISTINCT m.date FROM book_m4bs bm
                     JOIN m4bs m ON m.id = bm.m4b_id
                     WHERE bm.book_id = b.id AND m.date IS NOT NULL) AS m4b_dates
        FROM books b
        LEFT JOIN series s ON s.id = b.series_id
        WHERE b.id = %s AND (b.publication_date IS NULL OR b.language IS NULL)
        """,
        (book_id,),
    ).fetchone()


def backfill_book(
    conn: psycopg.Connection, book_id: int, client: anthropic.Anthropic | None = None
) -> bool:
    """Returns True if anything was written. No-op when the book already has
    both facts, which is the common case for a book created from an epub."""
    book = _book_missing_facts(conn, book_id)
    if book is None:
        return False

    pub_date = book["publication_date"]
    language = book["language"]

    # Deterministic: a linked epub already knows the language.
    if language is None and len(book["epub_languages"]) == 1:
        language = norm_language(book["epub_languages"][0])

    if pub_date is None or language is None:
        if client is None:
            client = anthropic.Anthropic()
        facts, _usage = llm.book_facts(
            client,
            {
                "title": book["title"],
                "authors": book["authors"],
                "series": book["series"],
                "series_position": book["series_position"],
                "edition_date_hints": book["raw_dates"] + book["m4b_dates"],
                "edition_language_hints": book["epub_languages"],
            },
        )
        if pub_date is None:
            pub_date = parse_pub_date(facts.publication_date)
        if language is None:
            language = norm_language(facts.language)

    new_date = pub_date if book["publication_date"] is None and pub_date else None
    new_lang = language if book["language"] is None and language else None
    if not new_date and not new_lang:
        log.info("book %s %r: no missing facts could be filled", book_id, book["title"])
        return False

    conn.execute(
        """UPDATE books
           SET publication_date = COALESCE(publication_date, %s),
               language = COALESCE(language, %s)
           WHERE id = %s""",
        (new_date, new_lang, book_id),
    )
    log.info(
        "book %s %r -> date=%s lang=%s",
        book_id, book["title"], new_date or "(kept)", new_lang or "(kept)",
    )
    return True
