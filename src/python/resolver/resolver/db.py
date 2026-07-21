"""Database access. Connection parameters come from the standard libpq
environment variables (PGHOST, PGDATABASE, PGUSER, PGPASSWORD, ...)."""

from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def connect() -> psycopg.Connection:
    return psycopg.connect(row_factory=dict_row)


# --- raw asset metadata ------------------------------------------------------


def get_unresolved(
    conn: psycopg.Connection, asset_type: str, limit: int | None = None
) -> list[int]:
    """IDs of raw assets with no abstract link and no resolutions row."""
    table, join, fk = {
        "epub": ("epubs", "book_epubs", "epub_id"),
        "m4b": ("m4bs", "book_m4bs", "m4b_id"),
    }[asset_type]
    sql = f"""
        SELECT a.id FROM {table} a
        LEFT JOIN {join} j ON j.{fk} = a.id
        LEFT JOIN resolutions r ON r.asset_type = %s AND r.asset_id = a.id
        WHERE j.book_id IS NULL AND r.id IS NULL
        ORDER BY a.id
    """
    if limit is not None:
        sql += " LIMIT %s"
        rows = conn.execute(sql, (asset_type, limit)).fetchall()
    else:
        rows = conn.execute(sql, (asset_type,)).fetchall()
    return [r["id"] for r in rows]


def is_linked(conn: psycopg.Connection, asset_type: str, asset_id: int) -> bool:
    join, fk = {"epub": ("book_epubs", "epub_id"), "m4b": ("book_m4bs", "m4b_id")}[
        asset_type
    ]
    row = conn.execute(
        f"SELECT 1 FROM {join} WHERE {fk} = %s", (asset_id,)
    ).fetchone()
    return row is not None


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


# --- resolutions -------------------------------------------------------------


def insert_resolution(
    conn: psycopg.Connection,
    asset_type: str,
    asset_id: int,
    status: str,
    method: str,
    confidence: float | None,
    proposal: dict,
    notes: str | None,
) -> int:
    row = conn.execute(
        """INSERT INTO resolutions
               (asset_type, asset_id, status, method, confidence, proposal, notes)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           RETURNING id""",
        (asset_type, asset_id, status, method, confidence, Jsonb(proposal), notes),
    ).fetchone()
    return row["id"]


def get_resolution(conn: psycopg.Connection, resolution_id: int) -> dict | None:
    return conn.execute(
        "SELECT * FROM resolutions WHERE id = %s", (resolution_id,)
    ).fetchone()


def list_pending(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        "SELECT * FROM resolutions WHERE status = 'pending' ORDER BY id"
    ).fetchall()


def list_rejected(conn: psycopg.Connection) -> list[dict]:
    """Rejected resolutions with the raw asset's title and s3_key, so the
    underlying files can be located for possible removal from the library."""
    return conn.execute(
        """SELECT r.*,
                  COALESCE(e.title, m.title) AS asset_title,
                  COALESCE(e.s3_key, m.s3_key) AS s3_key
           FROM resolutions r
           LEFT JOIN epubs e ON r.asset_type = 'epub' AND e.id = r.asset_id
           LEFT JOIN m4bs m ON r.asset_type = 'm4b' AND m.id = r.asset_id
           WHERE r.status = 'rejected'
           ORDER BY r.reviewed_at DESC NULLS LAST, r.id"""
    ).fetchall()


def update_resolution_proposal(
    conn: psycopg.Connection, resolution_id: int, proposal: dict, notes: str | None
) -> None:
    conn.execute(
        "UPDATE resolutions SET proposal = %s, notes = %s WHERE id = %s",
        (Jsonb(proposal), notes, resolution_id),
    )


def set_resolution_status(
    conn: psycopg.Connection, resolution_id: int, status: str
) -> None:
    conn.execute(
        "UPDATE resolutions SET status = %s, reviewed_at = now() WHERE id = %s",
        (status, resolution_id),
    )
