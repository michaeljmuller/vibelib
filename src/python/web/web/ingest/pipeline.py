"""Turning a file into rows: read it, put it in the bucket, record it.

Two ways in, and they differ only in which direction the bytes move first. A
browser upload arrives on local disk and has to be stored; a bucket object is
already stored and has to be fetched. Both end at _record(), so an object that
reached the bucket by some other route is read by exactly the same code as one
uploaded here.

Order matters in both: the file is parsed *before* anything is written, so a
file we cannot read never reaches the bucket and never leaves a row. The one
remaining seam is store-then-insert, and it fails in the harmless direction --
the object is in the bucket with no row, which is the definition of the work
queue, so the next scan simply picks it up again.

These are the bodies of the two job kinds in worker.py. They know the order of
the steps, so they are what announces each one, through a `report` callback --
the worker only records what it is told and shows it.
"""

import datetime
import logging
import os
import re
from dataclasses import dataclass

import psycopg

from .. import covers, s3
from . import epub, m4b, store

log = logging.getLogger("uvicorn.error")

SUFFIXES = {".epub": "epub", ".m4b": "m4b"}

# What an item is doing, in the order it does it. Only the transfers carry a
# fraction: reading an epub is milliseconds and ffprobe on a local m4b is a few
# seconds with nothing to report, so those phases show no number rather than a
# made-up one. UPLOADING is here for completeness but is reported by the browser
# -- it is the one phase the server does not own.
QUEUED = "queued"
UPLOADING = "uploading"      # browser -> here
DOWNLOADING = "downloading"  # bucket -> here
STORING = "storing"          # here -> bucket
READING = "reading"          # parsing metadata, writing rows
DONE = "done"
FAILED = "failed"

WITH_PROGRESS = (UPLOADING, DOWNLOADING, STORING)


def _quiet(phase: str, fraction: float | None = None) -> None:
    """Default reporter: used when nobody is watching (tests, direct calls)."""

# Keys are flat at the bucket root and human-readable ("11_22_63 - Stephen
# King.epub"). Directory separators would silently create a prefix, and control
# characters make a key that is a nuisance to handle from the shell.
_UNSAFE = re.compile(r"[\x00-\x1f/\\]")


class Unsupported(Exception):
    """Not an epub or an m4b, so there is nothing here we know how to read."""


class Unreadable(Exception):
    """It is the right kind of file and we still could not read it: a truncated
    upload, an epub with no dc:title, an m4b whose atoms are corrupt."""


class AlreadyPresent(Exception):
    """The bucket already holds this key. Carries the existing asset, if any."""

    def __init__(self, s3_key: str, asset_type: str, existing: dict | None):
        super().__init__(s3_key)
        self.s3_key = s3_key
        self.asset_type = asset_type
        self.existing = existing


@dataclass
class Ingested:
    asset_type: str
    asset_id: int
    s3_key: str
    title: str | None


def asset_type_for(filename: str) -> str:
    suffix = os.path.splitext(filename)[1].lower()
    if suffix not in SUFFIXES:
        raise Unsupported(f"{filename}: only .epub and .m4b files can be added")
    return SUFFIXES[suffix]


def key_for(filename: str) -> str:
    """The object key an uploaded file gets: its own name, made safe."""
    name = _UNSAFE.sub("_", os.path.basename(filename)).strip()
    if not name:
        raise Unsupported("that file has no usable name")
    return name


def _parse(asset_type: str, path: str):
    """Every way a file can fail to be read, under one name.

    The parsers raise whatever their libraries raise -- zipfile.BadZipFile for a
    truncated epub, MutagenError for a corrupt m4b, ValueError for an OPF with
    no dc:title. To the person who just dropped the file in, those are all the
    same event, and the alternative to catching them here is a 500 for a case
    that is entirely the file's fault.
    """
    try:
        return epub.parse(path) if asset_type == "epub" else m4b.parse(path)
    except Exception as exc:
        raise Unreadable(str(exc) or type(exc).__name__) from exc


def _cover_bytes(asset_type: str, path: str, data) -> bytes | None:
    """Never fails an ingest: a missing cover costs a placeholder tile, and for
    an m4b it also costs the fallback that identifies a junk-titled file from
    its artwork -- both survivable, neither worth losing the file over."""
    try:
        if asset_type == "epub":
            return epub.cover_bytes(path, data.cover_path) if data.cover_path else None
        return m4b.cover_bytes(path) if data.has_cover else None
    except Exception as exc:  # noqa: BLE001 — a broken image is not a broken file
        log.warning("could not read cover from %s: %s", path, exc)
        return None


def _record(
    conn: psycopg.Connection, asset_type: str, s3_key: str, path: str, data
) -> Ingested:
    """Write the raw rows for a parsed file that is now in the bucket at s3_key."""
    insert = store.insert_epub if asset_type == "epub" else store.insert_m4b
    asset_id = insert(conn, s3_key, data)
    store.set_acquired_on(conn, asset_type, asset_id, datetime.date.today())
    conn.commit()

    covers.save(asset_type, asset_id, _cover_bytes(asset_type, path, data))
    log.info("ingested %s %s: %s", asset_type, asset_id, s3_key)
    return Ingested(asset_type, asset_id, s3_key, data.title)


# --- the two job bodies ------------------------------------------------------


def store_uploaded(
    conn: psycopg.Connection, path: str, filename: str, report=_quiet
) -> Ingested:
    """A file already on local disk: read it, push it to the bucket, record it.

    The caller owns `path` and deletes it; this may be retried against the same
    file, which is why it does not.
    """
    asset_type = asset_type_for(filename)
    s3_key = key_for(filename)

    report(READING)
    data = _parse(asset_type, path)  # fail before anything is stored

    if s3.exists(s3_key):
        raise AlreadyPresent(s3_key, asset_type, store.find_by_key(conn, asset_type, s3_key))

    report(STORING, 0.0)
    s3.upload(path, s3_key, on_progress=lambda f: report(STORING, f))
    log.info("stored %s (%d bytes)", s3_key, os.path.getsize(path))

    report(READING)
    return _record(conn, asset_type, s3_key, path, data)


def fetch_and_record(
    conn: psycopg.Connection, s3_key: str, temp_path: str, report=_quiet
) -> Ingested:
    """An object already in the bucket: fetch it, read it, record it."""
    asset_type = asset_type_for(s3_key)
    existing = store.find_by_key(conn, asset_type, s3_key)
    if existing is not None:
        raise AlreadyPresent(s3_key, asset_type, existing)

    report(DOWNLOADING, 0.0)
    s3.download_to(s3_key, temp_path, on_progress=lambda f: report(DOWNLOADING, f))

    report(READING)
    return _record(conn, asset_type, s3_key, temp_path, _parse(asset_type, temp_path))


# --- the two lists -----------------------------------------------------------


def unrecorded_keys(conn: psycopg.Connection) -> list[str]:
    """Bucket objects with no row in epubs/m4bs — the work the scan enqueues.

    This is list A's source, and the reason the queue needs no persistence: the
    bucket and the tables are both durable, so the difference between them can
    be recomputed at any time and always names exactly the work left to do.
    """
    in_bucket = s3.list_book_keys()
    out: list[str] = []
    for asset_type in ("epub", "m4b"):
        known = store.existing_keys(conn, asset_type)
        out += sorted(k for k in in_bucket[asset_type] if k not in known)
    return out


def ready_to_add(conn: psycopg.Connection) -> list[dict]:
    """List B: raw assets that exist but belong to no book yet."""
    out: list[dict] = []
    for asset_type in ("epub", "m4b"):
        for row in store.get_unresolved(conn, asset_type):
            out.append(
                {
                    "asset_type": asset_type,
                    "asset_id": row["id"],
                    "s3_key": row["s3_key"],
                    "title": row["title"],
                }
            )
    out.sort(key=lambda r: (r["title"] or r["s3_key"]).lower())
    return out
