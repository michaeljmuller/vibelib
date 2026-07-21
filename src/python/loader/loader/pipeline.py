"""Ingest new object-store files into the raw asset tables.

The bucket is the source of truth for what exists; the `s3_key` columns record
what has been ingested. Anything in the former and not the latter is new work.
Each asset commits on its own, so a single unreadable file costs one book, not
the batch — the March import apparently lost 81 files silently, and a run that
reports its failures is the point of this being a first-class component.
"""

import logging
import os
import tempfile

import psycopg

from . import db, epub, m4b, s3

log = logging.getLogger(__name__)

ASSET_TYPES = ("epub", "m4b")


def covers_dir() -> str:
    return os.environ.get("LOADER_COVERS_DIR", "/covers")


def scan(conn: psycopg.Connection) -> dict[str, list[str]]:
    """Keys present in the bucket but absent from the database, by asset type."""
    in_bucket = s3.list_book_keys()
    new: dict[str, list[str]] = {}
    for asset_type in ASSET_TYPES:
        known = db.existing_keys(conn, asset_type)
        new[asset_type] = sorted(k for k in in_bucket[asset_type] if k not in known)
    return new


def _write_cover(asset_type: str, asset_id: int, data: bytes | None) -> bool:
    """Covers are served off disk by the web app, keyed by asset id. A missing
    cover costs a placeholder image, so failure here never fails the ingest."""
    if not data:
        return False
    directory = os.path.join(covers_dir(), asset_type)
    try:
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, f"{asset_id}.jpg"), "wb") as fh:
            fh.write(data)
        return True
    except OSError as exc:
        log.warning("could not write cover for %s %s: %s", asset_type, asset_id, exc)
        return False


def _ingest_one(conn: psycopg.Connection, asset_type: str, key: str, path: str) -> int:
    if asset_type == "epub":
        data = epub.parse(path)
        asset_id = db.insert_epub(conn, key, data)
        conn.commit()
        cover = epub.cover_bytes(path, data.cover_path) if data.cover_path else None
        _write_cover("epub", asset_id, cover)
    else:
        data = m4b.parse(path)
        asset_id = db.insert_m4b(conn, key, data)
        conn.commit()
        _write_cover("m4b", asset_id, m4b.cover_bytes(path) if data.has_cover else None)
    return asset_id


def load(
    conn: psycopg.Connection,
    limit: int | None = None,
    dry_run: bool = False,
    only: str | None = None,
) -> dict:
    new = scan(conn)
    types = (only,) if only else ASSET_TYPES

    counts = {"loaded": 0, "errors": 0, "skipped": 0}
    errors: list[tuple[str, str]] = []

    for asset_type in types:
        keys = new[asset_type]
        if limit is not None:
            keys = keys[: max(0, limit - counts["loaded"] - counts["errors"])]

        for key in keys:
            if dry_run:
                log.info("would load %s: %s", asset_type, key)
                counts["skipped"] += 1
                continue

            suffix = f".{asset_type}"
            fd, path = tempfile.mkstemp(suffix=suffix)
            os.close(fd)
            try:
                s3.download(key, path)
                asset_id = _ingest_one(conn, asset_type, key, path)
                log.info("loaded %s %s: %s", asset_type, asset_id, key)
                counts["loaded"] += 1
            except Exception as exc:  # noqa: BLE001 — one bad file must not stop the run
                conn.rollback()
                log.error("FAILED %s: %s (%s)", key, exc, type(exc).__name__)
                errors.append((key, f"{type(exc).__name__}: {exc}"))
                counts["errors"] += 1
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    return {"counts": counts, "errors": errors, "new": {k: len(v) for k, v in new.items()}}
