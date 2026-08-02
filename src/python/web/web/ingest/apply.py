"""Executes a proposal's actions in one transaction.

Every `create` re-checks for an existing row by normalized key first, so
applying is idempotent and proposals approved long after they were written
don't duplicate entities created in the meantime.
"""

import re
from typing import Any

import psycopg

from .candidates import find_person_exact, find_series_exact
from .normalize import sort_name as make_sort_name

# A guessed year is usually close to the truth; a reissue is decades off. That
# gap is the only thing separating the two cases below, so it is the guard.
_MAX_GUESS_DRIFT_YEARS = 5
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _adopt_epub_publication_date(
    conn: psycopg.Connection, book_id: int, epub_id: int
) -> None:
    """Let a newly linked epub supply the publication date the book never had.

    An m4b carries no real publication date, so a book created from one gets a
    *guessed* year and the YYYY-01-01 placeholder. When its ebook finally
    arrives it states a real date — a recorded fact, which should beat a guess.
    ("Upping the Ante" sat at a guessed 2019 while its epub said 2022-09-06.)

    Deliberately narrow, because the same move is destructive in a case that
    looks identical from here: for a reissued classic the epub's date is the
    *reissue* ("The Pothunters", first published 1902, in a 2011 edition), and
    overwriting would replace a correct date with a wrong one. Hence three
    conditions — this epub must be the book's first (a book that already had one
    took its date from that epub), the existing date must be absent or a
    placeholder, and the epub's date must be near it rather than decades away.
    """
    row = conn.execute(
        """SELECT b.publication_date AS current, e.published_date AS raw,
                  (SELECT count(*) FROM book_epubs WHERE book_id = b.id) AS n_epubs
             FROM books b, epubs e
            WHERE b.id = %s AND e.id = %s""",
        (book_id, epub_id),
    ).fetchone()
    if row is None or row["n_epubs"] != 1:
        return

    current = row["current"]
    if current is not None and (current.month, current.day) != (1, 1):
        return  # a real, precise date is already recorded — leave it alone

    m = _ISO_DATE.match(row["raw"] or "")
    if not m or m.group().endswith("-01-01"):
        return  # junk, year-only, or no better than the placeholder we have

    new = m.group()
    if current is not None and abs(int(new[:4]) - current.year) > _MAX_GUESS_DRIFT_YEARS:
        return  # far-off date: a reissue, not a correction

    conn.execute(
        "UPDATE books SET publication_date = %s WHERE id = %s", (new, book_id)
    )


def _resolve_person(conn: psycopg.Connection, ref: dict[str, Any]) -> int:
    if "link" in ref:
        row = conn.execute(
            "SELECT id FROM people WHERE id = %s", (ref["link"],)
        ).fetchone()
        if row is None:
            raise ValueError(f"proposal links missing person id {ref['link']}")
        return row["id"]
    spec = ref["create"]
    existing = find_person_exact(conn, spec["name"])
    if existing is not None:
        return existing["id"]
    row = conn.execute(
        "INSERT INTO people (name, sort_name, disambiguator) VALUES (%s, %s, %s) RETURNING id",
        (
            spec["name"],
            spec.get("sort_name") or make_sort_name(spec["name"]),
            spec.get("disambiguator"),
        ),
    ).fetchone()
    return row["id"]


def _resolve_series(conn: psycopg.Connection, ref: dict[str, Any] | None) -> int | None:
    if ref is None:
        return None
    if "link" in ref:
        row = conn.execute(
            "SELECT id FROM series WHERE id = %s", (ref["link"],)
        ).fetchone()
        if row is None:
            raise ValueError(f"proposal links missing series id {ref['link']}")
        return row["id"]
    spec = ref["create"]
    existing = find_series_exact(conn, spec["name"])
    if existing is not None:
        return existing["id"]
    row = conn.execute(
        "INSERT INTO series (name, sort_name) VALUES (%s, %s) RETURNING id",
        (spec["name"], spec["sort_name"]),
    ).fetchone()
    return row["id"]


def _resolve_book(
    conn: psycopg.Connection, book_ref: dict[str, Any], author_ids: list[int]
) -> int:
    if "link" in book_ref:
        row = conn.execute(
            "SELECT id FROM books WHERE id = %s", (book_ref["link"],)
        ).fetchone()
        if row is None:
            raise ValueError(f"proposal links missing book id {book_ref['link']}")
        # Reviewer-approved corrections to the existing record (never applied
        # via the auto path — see pipeline).
        update = book_ref.get("update")
        if update:
            if "title" in update:
                conn.execute(
                    "UPDATE books SET title = %s, sort_title = %s WHERE id = %s",
                    (update["title"], update["sort_title"], row["id"]),
                )
            if "series" in update:
                series_id = _resolve_series(conn, update["series"])
                conn.execute(
                    "UPDATE books SET series_id = %s WHERE id = %s",
                    (series_id, row["id"]),
                )
            if "series_position" in update:
                conn.execute(
                    "UPDATE books SET series_position = %s WHERE id = %s",
                    (update["series_position"], row["id"]),
                )
        # A linked book that has no author credits yet inherits the resolved
        # ones (books curated before the resolver existed may lack them).
        # Books that already have credits are left untouched.
        has_authors = conn.execute(
            "SELECT 1 FROM book_authors WHERE book_id = %s LIMIT 1", (row["id"],)
        ).fetchone()
        if not has_authors:
            for pos, author_id in enumerate(author_ids, start=1):
                conn.execute(
                    """INSERT INTO book_authors (book_id, author_id, position)
                       VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                    (row["id"], author_id, pos),
                )
        return row["id"]
    spec = book_ref["create"]

    # Re-check: same normalized title + same author set already present?
    from .candidates import author_sets_match, _book_candidates

    if author_ids:
        names = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM people WHERE id = ANY(%s)", (author_ids,)
            ).fetchall()
        ]
        for cand in _book_candidates(conn, spec["title"], limit=10):
            from .normalize import norm_title

            if norm_title(cand["title"]) == norm_title(spec["title"]) and author_sets_match(
                names, cand["authors"]
            ):
                return cand["id"]

    series_id = _resolve_series(conn, spec.get("series"))
    row = conn.execute(
        """INSERT INTO books
               (title, sort_title, series_id, series_position, publication_date, language)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (
            spec["title"],
            spec["sort_title"],
            series_id,
            spec.get("series_position"),
            spec.get("publication_date"),
            spec.get("language"),
        ),
    ).fetchone()
    book_id = row["id"]
    for pos, author_id in enumerate(author_ids, start=1):
        conn.execute(
            """INSERT INTO book_authors (book_id, author_id, position)
               VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
            (book_id, author_id, pos),
        )
    return book_id


def apply_correction(
    conn: psycopg.Connection, book_id: int, correction: dict[str, Any]
) -> dict[str, Any]:
    """Apply a reviewed correction to a book already in the catalog.

    Only the keys present are touched -- see llm.to_correction, which puts a key
    in only for a field actually being changed. That is the whole safety story
    for this route: a correction about a publication date cannot reach the
    title, because there is no title key in the dict to act on.

    Authors are the exception to "update a column": the credit list is replaced
    wholesale, in order, because that is what correcting an author means. People
    the book no longer credits are left in `people` -- they may well author or
    narrate something else, and this is not the place to decide they don't.
    """
    if not correction:
        raise ValueError("that correction would change nothing")

    with conn.transaction():
        row = conn.execute(
            "SELECT id FROM books WHERE id = %s", (book_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"no book {book_id}")

        if "title" in correction:
            conn.execute(
                "UPDATE books SET title = %s, sort_title = %s WHERE id = %s",
                (correction["title"], correction["sort_title"], book_id),
            )
        if "series" in correction:
            conn.execute(
                "UPDATE books SET series_id = %s WHERE id = %s",
                (_resolve_series(conn, correction["series"]), book_id),
            )
        if "series_position" in correction:
            conn.execute(
                "UPDATE books SET series_position = %s WHERE id = %s",
                (correction["series_position"], book_id),
            )
        if "publication_date" in correction:
            conn.execute(
                "UPDATE books SET publication_date = %s WHERE id = %s",
                (correction["publication_date"], book_id),
            )
        if "language" in correction:
            conn.execute(
                "UPDATE books SET language = %s WHERE id = %s",
                (correction["language"], book_id),
            )

        author_ids: list[int] = []
        if "authors" in correction:
            author_ids = [_resolve_person(conn, ref) for ref in correction["authors"]]
            conn.execute("DELETE FROM book_authors WHERE book_id = %s", (book_id,))
            for pos, author_id in enumerate(author_ids, start=1):
                conn.execute(
                    """INSERT INTO book_authors (book_id, author_id, position)
                       VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                    (book_id, author_id, pos),
                )

    return {"book_id": book_id, "author_ids": author_ids}


def apply_proposal(
    conn: psycopg.Connection, asset_type: str, asset_id: int, proposal: dict[str, Any]
) -> dict[str, Any]:
    """Apply all actions atomically; returns the resolved entity ids."""
    with conn.transaction():
        author_ids = [_resolve_person(conn, ref) for ref in proposal.get("authors", [])]
        book_id = _resolve_book(conn, proposal["book"], author_ids)

        join, fk = {
            "epub": ("book_epubs", "epub_id"),
            "m4b": ("book_m4bs", "m4b_id"),
        }[asset_type]
        conn.execute(
            f"INSERT INTO {join} (book_id, {fk}) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (book_id, asset_id),
        )
        if asset_type == "epub":
            _adopt_epub_publication_date(conn, book_id, asset_id)

        narrator_ids: list[int] = []
        if asset_type == "m4b":
            for pos, ref in enumerate(proposal.get("narrators", []), start=1):
                narrator_id = _resolve_person(conn, ref)
                narrator_ids.append(narrator_id)
                conn.execute(
                    """INSERT INTO m4b_narrators (m4b_id, narrator_id, position)
                       VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                    (asset_id, narrator_id, pos),
                )

        # Pseudonym links only ever arrive here via an approved review —
        # the pipeline never auto-commits a proposal that contains any.
        for pseu in proposal.get("pseudonyms", []):
            pseudonym_id = _resolve_person(conn, {"create": {"name": pseu["pseudonym_name"]}})
            for real_name in pseu["real_person_names"]:
                real_id = _resolve_person(conn, {"create": {"name": real_name}})
                conn.execute(
                    """INSERT INTO author_pseudonyms (pseudonym_id, author_id)
                       VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                    (pseudonym_id, real_id),
                )

    return {"book_id": book_id, "author_ids": author_ids, "narrator_ids": narrator_ids}
