"""Catch-up pass: fill missing publication_date / language on books created
before those columns existed (or before their values were known).

Language is filled deterministically from a linked epub's language field when
possible; only books still missing a fact get one small LLM call. Only NULL
columns are ever written — existing values are never overwritten — so the
command is idempotent and safe to re-run."""

import logging
from typing import Any

import anthropic
import psycopg

from . import llm
from .normalize import norm_language, parse_pub_date

log = logging.getLogger("resolver")


def _books_missing_facts(
    conn: psycopg.Connection, limit: int | None, book_id: int | None = None
) -> list[dict]:
    sql = """
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
        WHERE (b.publication_date IS NULL OR b.language IS NULL)
        ORDER BY b.id
    """
    params: list = []
    if book_id is not None:
        sql = sql.replace("ORDER BY b.id", "AND b.id = %s ORDER BY b.id")
        params.append(book_id)
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    return conn.execute(sql, params or None).fetchall()


def backfill_book(
    conn: psycopg.Connection, book_id: int, client: anthropic.Anthropic | None = None
) -> bool:
    """Fill missing facts for one book (used right after approving a proposal
    that predates the publication_date/language fields). Returns True if
    anything was written. No-op when the book already has both facts."""
    rows = _books_missing_facts(conn, limit=None, book_id=book_id)
    if not rows:
        return False
    if client is None:
        client = anthropic.Anthropic()
    summary = _fill_books(conn, client, rows, dry_run=False)
    return summary["counts"]["dates_set"] + summary["counts"]["languages_set"] > 0


def backfill_books(
    conn: psycopg.Connection, *, limit: int | None = None, dry_run: bool = False
) -> dict[str, Any]:
    return _fill_books(
        conn, anthropic.Anthropic(), _books_missing_facts(conn, limit), dry_run=dry_run
    )


def _fill_books(
    conn: psycopg.Connection,
    client: anthropic.Anthropic,
    books: list[dict],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    counts = {"language_from_epub": 0, "llm_calls": 0, "dates_set": 0,
              "languages_set": 0, "unknown": 0}
    usage_total: dict[str, int] = {}

    for book in books:
        pub_date = book["publication_date"]
        language = book["language"]

        # Deterministic: a linked epub already knows the language.
        if language is None and len(book["epub_languages"]) == 1:
            language = norm_language(book["epub_languages"][0])
            if language is not None:
                counts["language_from_epub"] += 1

        if pub_date is None or language is None:
            info = {
                "title": book["title"],
                "authors": book["authors"],
                "series": book["series"],
                "series_position": book["series_position"],
                "edition_date_hints": book["raw_dates"] + book["m4b_dates"],
                "edition_language_hints": book["epub_languages"],
            }
            facts, usage = llm.book_facts(client, info)
            counts["llm_calls"] += 1
            for k, v in usage.items():
                usage_total[k] = usage_total.get(k, 0) + v
            if pub_date is None:
                pub_date = parse_pub_date(facts.publication_date)
            if language is None:
                language = norm_language(facts.language)

        new_date = pub_date if book["publication_date"] is None and pub_date else None
        new_lang = language if book["language"] is None and language else None
        if new_date:
            counts["dates_set"] += 1
        if new_lang:
            counts["languages_set"] += 1
        if not new_date and not new_lang:
            counts["unknown"] += 1

        log.info(
            "book %s %r -> date=%s lang=%s%s",
            book["id"], book["title"],
            new_date or book["publication_date"] or "?",
            new_lang or book["language"] or "?",
            " (dry-run)" if dry_run else "",
        )
        if not dry_run and (new_date or new_lang):
            conn.execute(
                """UPDATE books
                   SET publication_date = COALESCE(publication_date, %s),
                       language = COALESCE(language, %s)
                   WHERE id = %s""",
                (new_date, new_lang, book["id"]),
            )
            conn.commit()

    return {"counts": counts, "usage": usage_total}
