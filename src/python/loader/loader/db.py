"""Database access. Connection parameters come from the standard libpq
environment variables (PGHOST, PGDATABASE, PGUSER, PGPASSWORD, ...).

Writes only the raw asset tables. The curation joins (book_epubs, book_m4bs,
m4b_narrators) belong to the resolver — the loader never touches a book.
"""

import psycopg
from psycopg.rows import dict_row

from .epub import Epub
from .m4b import M4b

TABLES = {"epub": "epubs", "m4b": "m4bs"}


def connect() -> psycopg.Connection:
    return psycopg.connect(row_factory=dict_row)


def existing_keys(conn: psycopg.Connection, asset_type: str) -> set[str]:
    table = TABLES[asset_type]
    rows = conn.execute(f"SELECT s3_key FROM {table}").fetchall()
    return {r["s3_key"] for r in rows}


def insert_epub(conn: psycopg.Connection, s3_key: str, data: Epub) -> int:
    row = conn.execute(
        """
        INSERT INTO epubs (s3_key, asin, isbn, title, publisher, published_date,
                           language, description, series, series_position,
                           identifier, subject, cover_path)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            s3_key,
            data.asin,
            data.isbn,
            data.title,
            data.publisher,
            data.published_date,
            data.language,
            data.description,
            data.series,
            data.series_position,
            data.identifier,
            data.subject,
            data.cover_path,
        ),
    ).fetchone()
    epub_id = row["id"]  # type: ignore[index]

    for position, (name, role) in enumerate(data.authors, start=1):
        conn.execute(
            "INSERT INTO epub_authors (epub_id, author, role, position)"
            " VALUES (%s, %s, %s, %s)",
            (epub_id, name, role, position),
        )
    return epub_id


def insert_m4b(conn: psycopg.Connection, s3_key: str, data: M4b) -> int:
    row = conn.execute(
        """
        INSERT INTO m4bs (s3_key, asin, title, artist, narrator, album, date,
                          description, comment, genre, copyright, has_cover,
                          duration_s, bitrate_kbps, sample_rate, channels)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            s3_key,
            data.asin,
            data.title,
            data.artist,
            data.narrator,
            data.album,
            data.date,
            data.description,
            data.comment,
            data.genre,
            data.copyright,
            data.has_cover,
            data.duration_s,
            data.bitrate_kbps,
            data.sample_rate,
            data.channels,
        ),
    ).fetchone()
    m4b_id = row["id"]  # type: ignore[index]

    for ch in data.chapters:
        conn.execute(
            "INSERT INTO m4b_chapters (m4b_id, position, title, start_ms)"
            " VALUES (%s, %s, %s, %s)",
            (m4b_id, ch.position, ch.title, ch.start_ms),
        )
    return m4b_id
