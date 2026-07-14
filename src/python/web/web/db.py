"""Read-only queries backing the browse UI. Connection parameters come from the
standard libpq environment variables (PGHOST, PGDATABASE, PGUSER, PGPASSWORD, ...)."""

from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

pool = ConnectionPool(kwargs={"row_factory": dict_row}, open=False, min_size=1, max_size=8)


# A book's grid-level shape: identity, series placement, and which raw assets back
# it. The epub/m4b ids double as the cover reference (see covers.py).
BOOK_COLUMNS = """
    b.id, b.title, b.language, b.publication_date,
    b.series_position, s.id AS series_id, s.name AS series_name,
    COALESCE(a.authors, ARRAY[]::text[]) AS authors,
    e.epub_id, m.m4b_id, acq.acquired_on
"""

BOOK_JOINS = """
    FROM books b
    LEFT JOIN series s ON s.id = b.series_id
    LEFT JOIN LATERAL (
        SELECT array_agg(p.name ORDER BY ba.position) AS authors
        FROM book_authors ba JOIN people p ON p.id = ba.author_id
        WHERE ba.book_id = b.id
    ) a ON TRUE
    LEFT JOIN LATERAL (
        SELECT be.epub_id FROM book_epubs be
        WHERE be.book_id = b.id ORDER BY be.epub_id LIMIT 1
    ) e ON TRUE
    LEFT JOIN LATERAL (
        SELECT bm.m4b_id FROM book_m4bs bm
        WHERE bm.book_id = b.id ORDER BY bm.m4b_id LIMIT 1
    ) m ON TRUE
    -- Acquisition is recorded per asset, but it is a fact about the book: the
    -- ebook and audiobook of one book were bought together and share a date.
    -- Where they somehow differ, the earliest is when the book entered the
    -- library, which is what "acquired" means to someone browsing.
    LEFT JOIN LATERAL (
        SELECT min(acquired_on) AS acquired_on FROM (
            SELECT ea.acquired_on
            FROM book_epubs be2 JOIN epub_acquisitions ea ON ea.epub_id = be2.epub_id
            WHERE be2.book_id = b.id
            UNION ALL
            SELECT ma.acquired_on
            FROM book_m4bs bm2 JOIN m4b_acquisitions ma ON ma.m4b_id = bm2.m4b_id
            WHERE bm2.book_id = b.id
        ) dates
    ) acq ON TRUE
"""

SORTS = {
    "title": "b.sort_title ASC",
    "date": "b.publication_date DESC NULLS LAST, b.sort_title ASC",
    "series": "b.series_position ASC NULLS LAST, b.publication_date ASC NULLS LAST",
    "acquired": "acq.acquired_on DESC NULLS LAST, b.sort_title ASC",
}


def _filters(
    q: str | None,
    author_id: int | None,
    series_id: int | None,
    language: str | None,
    fmt: str | None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if q:
        # lower(col) LIKE lower('%q%') is the form the gin_trgm indexes from
        # migration 002 can serve (idx_books_title_trgm, _people_, _series_).
        pattern = f"%{q}%"
        clauses.append(
            """(lower(b.title) LIKE lower(%s)
                OR lower(COALESCE(s.name, '')) LIKE lower(%s)
                OR EXISTS (SELECT 1 FROM book_authors ba2
                           JOIN people p2 ON p2.id = ba2.author_id
                           WHERE ba2.book_id = b.id AND lower(p2.name) LIKE lower(%s)))"""
        )
        params += [pattern, pattern, pattern]
    if author_id:
        clauses.append(
            "EXISTS (SELECT 1 FROM book_authors ba3 "
            "WHERE ba3.book_id = b.id AND ba3.author_id = %s)"
        )
        params.append(author_id)
    if series_id:
        clauses.append("b.series_id = %s")
        params.append(series_id)
    if language:
        clauses.append("b.language = %s")
        params.append(language)
    if fmt == "epub":
        clauses.append("e.epub_id IS NOT NULL")
    elif fmt == "m4b":
        clauses.append("m.m4b_id IS NOT NULL")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def list_books(
    q: str | None = None,
    author_id: int | None = None,
    series_id: int | None = None,
    language: str | None = None,
    fmt: str | None = None,
    sort: str | None = None,
    limit: int = 60,
    offset: int = 0,
) -> dict[str, Any]:
    where, params = _filters(q, author_id, series_id, language, fmt)
    # Browsing a single series, reading order is the useful order.
    if sort is None:
        sort = "series" if series_id else "title"
    order = SORTS.get(sort, SORTS["title"])

    with pool.connection() as conn:
        total = conn.execute(
            f"SELECT count(*) AS n {BOOK_JOINS} {where}", params
        ).fetchone()["n"]
        items = conn.execute(
            f"SELECT {BOOK_COLUMNS} {BOOK_JOINS} {where} "
            f"ORDER BY {order} LIMIT %s OFFSET %s",
            params + [limit, offset],
        ).fetchall()
    return {"total": total, "items": items}


def get_book(book_id: int) -> dict[str, Any] | None:
    with pool.connection() as conn:
        book = conn.execute(
            f"SELECT {BOOK_COLUMNS} {BOOK_JOINS} WHERE b.id = %s", (book_id,)
        ).fetchone()
        if book is None:
            return None

        book["epubs"] = conn.execute(
            """SELECT e.id, e.s3_key, e.title, e.publisher, e.published_date,
                      e.isbn, e.asin, e.description
               FROM book_epubs be JOIN epubs e ON e.id = be.epub_id
               WHERE be.book_id = %s ORDER BY e.id""",
            (book_id,),
        ).fetchall()

        book["m4bs"] = conn.execute(
            """SELECT m.id, m.s3_key, m.title, m.duration_s, m.description,
                      COALESCE(n.narrators, ARRAY[]::text[]) AS narrators,
                      (SELECT count(*) FROM m4b_chapters c WHERE c.m4b_id = m.id) AS chapters
               FROM book_m4bs bm
               JOIN m4bs m ON m.id = bm.m4b_id
               LEFT JOIN LATERAL (
                   SELECT array_agg(p.name ORDER BY mn.position) AS narrators
                   FROM m4b_narrators mn JOIN people p ON p.id = mn.narrator_id
                   WHERE mn.m4b_id = m.id
               ) n ON TRUE
               WHERE bm.book_id = %s ORDER BY m.id""",
            (book_id,),
        ).fetchall()

        # Prose lives only on the raw assets; take the first one that has any.
        book["description"] = next(
            (
                a["description"]
                for a in (*book["epubs"], *book["m4bs"])
                if a["description"] and a["description"].strip()
            ),
            None,
        )

        if book["series_id"]:
            book["siblings"] = conn.execute(
                f"SELECT {BOOK_COLUMNS} {BOOK_JOINS} WHERE b.series_id = %s "
                f"ORDER BY {SORTS['series']}",
                (book["series_id"],),
            ).fetchall()
        else:
            book["siblings"] = []

    return book


def list_authors() -> list[dict[str, Any]]:
    with pool.connection() as conn:
        return conn.execute(
            """SELECT p.id, p.name, count(*) AS book_count
               FROM book_authors ba JOIN people p ON p.id = ba.author_id
               GROUP BY p.id, p.name, p.sort_name
               ORDER BY p.sort_name"""
        ).fetchall()


def list_series() -> list[dict[str, Any]]:
    with pool.connection() as conn:
        return conn.execute(
            """SELECT s.id, s.name, s.highest_position, s.is_complete,
                      count(b.id) AS book_count
               FROM series s LEFT JOIN books b ON b.series_id = s.id
               GROUP BY s.id, s.name, s.sort_name, s.highest_position, s.is_complete
               ORDER BY s.sort_name"""
        ).fetchall()


def list_languages() -> list[dict[str, Any]]:
    with pool.connection() as conn:
        return conn.execute(
            """SELECT language, count(*) AS book_count
               FROM books WHERE language IS NOT NULL
               GROUP BY language ORDER BY count(*) DESC"""
        ).fetchall()


def get_s3_key(asset_type: str, asset_id: int) -> str | None:
    table = {"epub": "epubs", "m4b": "m4bs"}[asset_type]
    with pool.connection() as conn:
        row = conn.execute(
            f"SELECT s3_key FROM {table} WHERE id = %s", (asset_id,)
        ).fetchone()
    return row["s3_key"] if row else None
