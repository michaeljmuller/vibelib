"""SQL for the raw asset tables and the log of what was added.

The two CLI tools this came from each opened their own connection; here every
caller is inside a request and passes one from the pool in web.db, so the
connect() helpers are gone and these are plain functions over a connection.

Writes here stay on the raw side (epubs, m4bs and their children, plus the
acquisition and resolutions rows). Everything that touches the abstract catalog
-- books, people, series and the joins between them -- goes through apply.py.
"""

import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .epub import Epub
from .m4b import M4b

TABLES = {"epub": "epubs", "m4b": "m4bs"}
ACQUISITIONS = {"epub": ("epub_acquisitions", "epub_id"), "m4b": ("m4b_acquisitions", "m4b_id")}
LINKS = {"epub": ("book_epubs", "epub_id"), "m4b": ("book_m4bs", "m4b_id")}


# --- raw assets --------------------------------------------------------------


def existing_keys(conn: psycopg.Connection, asset_type: str) -> set[str]:
    table = TABLES[asset_type]
    rows = conn.execute(f"SELECT s3_key FROM {table}").fetchall()
    return {r["s3_key"] for r in rows}


def find_by_key(conn: psycopg.Connection, asset_type: str, s3_key: str) -> dict | None:
    table = TABLES[asset_type]
    return conn.execute(
        f"SELECT id, title FROM {table} WHERE s3_key = %s", (s3_key,)
    ).fetchone()


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


def set_acquired_on(
    conn: psycopg.Connection, asset_type: str, asset_id: int, on: datetime.date
) -> None:
    """When the asset entered the library. Written at ingest as today's date,
    which is the truth for something being added right now, and correctable in
    the review card for a file bought long ago and only now uploaded."""
    table, fk = ACQUISITIONS[asset_type]
    conn.execute(
        f"""INSERT INTO {table} ({fk}, acquired_on) VALUES (%s, %s)
            ON CONFLICT ({fk}) DO UPDATE SET acquired_on = EXCLUDED.acquired_on""",
        (asset_id, on),
    )


def get_acquired_on(
    conn: psycopg.Connection, asset_type: str, asset_id: int
) -> datetime.date | None:
    table, fk = ACQUISITIONS[asset_type]
    row = conn.execute(
        f"SELECT acquired_on FROM {table} WHERE {fk} = %s", (asset_id,)
    ).fetchone()
    return row["acquired_on"] if row else None


# --- what still needs resolving ---------------------------------------------


def get_unresolved(conn: psycopg.Connection, asset_type: str) -> list[dict]:
    """List B: raw assets that belong to no book yet.

    Nothing about resolutions enters into it. A proposal that was never accepted
    left no trace, so "has a row, has no book" is the whole definition -- which
    is why an abandoned card puts the asset straight back here, unchanged.
    """
    table = TABLES[asset_type]
    join, fk = LINKS[asset_type]
    return conn.execute(
        f"""SELECT a.id, a.s3_key, a.title
              FROM {table} a
              LEFT JOIN {join} j ON j.{fk} = a.id
             WHERE j.book_id IS NULL
             ORDER BY a.id"""
    ).fetchall()


def is_linked(conn: psycopg.Connection, asset_type: str, asset_id: int) -> bool:
    join, fk = LINKS[asset_type]
    row = conn.execute(f"SELECT 1 FROM {join} WHERE {fk} = %s", (asset_id,)).fetchone()
    return row is not None


# --- raw metadata, as the resolver sees it -----------------------------------


def get_epub_meta(conn: psycopg.Connection, epub_id: int) -> dict[str, Any] | None:
    epub = conn.execute(
        """SELECT id, s3_key, asin, isbn, title, publisher, published_date,
                  language, description, series, series_position, subject
           FROM epubs WHERE id = %s""",
        (epub_id,),
    ).fetchone()
    if epub is None:
        return None
    creators = conn.execute(
        """SELECT author, role FROM epub_authors
           WHERE epub_id = %s ORDER BY position""",
        (epub_id,),
    ).fetchall()
    # dc:creator roles vary: MARC relator code 'aut' and free-text 'author'/'Author'
    author_roles = {"aut", "author"}
    meta = dict(epub)
    meta["asset_type"] = "epub"
    meta["authors"] = [
        c["author"] for c in creators if c["role"].casefold() in author_roles
    ]
    meta["other_creators"] = [
        {"name": c["author"], "role": c["role"]}
        for c in creators
        if c["role"].casefold() not in author_roles
    ]
    meta["narrators"] = []
    if meta["series_position"] is not None:
        meta["series_position"] = float(meta["series_position"])
    return meta


def get_m4b_meta(conn: psycopg.Connection, m4b_id: int) -> dict[str, Any] | None:
    m4b = conn.execute(
        """SELECT id, s3_key, asin, title, artist, narrator, album, date,
                  description, comment, genre, has_cover
           FROM m4bs WHERE id = %s""",
        (m4b_id,),
    ).fetchone()
    if m4b is None:
        return None
    from .normalize import split_authors

    meta = dict(m4b)
    meta["asset_type"] = "m4b"
    meta["authors"] = split_authors(m4b["artist"])
    meta["narrators"] = split_authors(m4b["narrator"])
    meta["series"] = None  # the album field is the raw series signal for m4bs
    meta["series_position"] = None
    return meta


def get_asset_meta(
    conn: psycopg.Connection, asset_type: str, asset_id: int
) -> dict[str, Any] | None:
    if asset_type == "epub":
        return get_epub_meta(conn, asset_id)
    return get_m4b_meta(conn, asset_id)


# --- resolutions: the audit log ----------------------------------------------
#
# One row per asset that was added, written at the moment it was added and never
# before. It records the mapping that was applied, which is the only thing that
# says afterwards what a given book_epubs/book_m4bs row was based on.
#
# This used to double as a review queue, with rows written speculatively and a
# status tracking whether a human had got to them yet. Nothing is written before
# acceptance any more, so the queue is gone and only the log remains.


def log_resolution(
    conn: psycopg.Connection, asset_type: str, asset_id: int, proposal: dict
) -> int:
    """Record an applied mapping. Upserts, because an asset can be unlinked and
    added again -- the current mapping is the interesting one, and the UNIQUE
    constraint would otherwise reject the second attempt."""
    row = conn.execute(
        """INSERT INTO resolutions (asset_type, asset_id, status, method, proposal, reviewed_at)
           VALUES (%s, %s, 'approved', 'llm', %s, now())
           ON CONFLICT (asset_type, asset_id) DO UPDATE
               SET proposal = EXCLUDED.proposal, reviewed_at = now(), status = 'approved'
           RETURNING id""",
        (asset_type, asset_id, Jsonb(proposal)),
    ).fetchone()
    return row["id"]
