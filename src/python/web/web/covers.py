"""Cover images, in the tree ingestion extracts them into:
{WEB_COVERS_DIR}/{epub,m4b}/{asset_id}.{jpg,jpeg,png}
{WEB_COVERS_DIR}/{epub,m4b}/thumb/{asset_id}.jpg

Read by the browse UI and by the resolver's cover-image fallback; written by
web.ingest.pipeline, which is why the mount is no longer read-only.

The originals run to 1200x1920 and average 200KB, but the grid draws them into
a 140px-wide tile. Sending the original to fill a thumbnail meant ~12MB for a
first screen of 60 books, so save() writes a downscaled copy alongside it and
the grid asks for that instead. The original stays untouched -- it is what the
detail card shows, and re-deriving a thumbnail from it is always possible."""

import io
import logging
import os
from pathlib import Path

from PIL import Image

log = logging.getLogger("uvicorn.error")

# Enough for a 140px tile on a 2x display, with room for the wider tiles that
# a large window's auto-fill columns stretch to.
THUMB_EDGE = 400
THUMB_QUALITY = 82


def find_cover(asset_type: str, asset_id: int) -> Path | None:
    covers_dir = os.environ.get("WEB_COVERS_DIR")
    if not covers_dir or asset_type not in ("epub", "m4b"):
        return None
    for ext in ("jpg", "jpeg", "png"):
        p = Path(covers_dir) / asset_type / f"{asset_id}.{ext}"
        if p.is_file():
            return p
    return None


def find_thumb(asset_type: str, asset_id: int) -> Path | None:
    """The downscaled copy, or None if there is not one: thumbnailing is allowed
    to fail without failing the ingest, so an unreadable image or a full disk
    leaves a cover with no thumbnail. util/thumbs.sh fills those in. Callers
    fall back to the original, which makes a missing thumbnail slow, not
    broken."""
    covers_dir = os.environ.get("WEB_COVERS_DIR")
    if not covers_dir or asset_type not in ("epub", "m4b"):
        return None
    p = Path(covers_dir) / asset_type / "thumb" / f"{asset_id}.jpg"
    return p if p.is_file() else None


def media_type(path: Path) -> str:
    return "image/png" if path.suffix.lower() == ".png" else "image/jpeg"


def discard(asset_type: str, asset_id: int) -> None:
    """Remove the extracted cover for an asset whose row is being deleted.

    Derived data, so losing it costs nothing -- reading the file again puts it
    back. Leaving it behind, on the other hand, means a later asset that happens
    to reuse the id would wear the wrong picture."""
    covers_dir = os.environ.get("WEB_COVERS_DIR")
    if not covers_dir or asset_type not in ("epub", "m4b"):
        return
    # Every extension, not just the one find_cover would have picked: a file
    # ingested before save() settled on .jpg can be sitting under another one.
    stale = [Path(covers_dir) / asset_type / f"{asset_id}.{ext}" for ext in ("jpg", "jpeg", "png")]
    stale.append(Path(covers_dir) / asset_type / "thumb" / f"{asset_id}.jpg")
    for p in stale:
        try:
            p.unlink(missing_ok=True)
        except OSError as exc:  # noqa: BLE001 — a stray file is not worth a 500
            log.warning("could not remove cover for %s %s: %s", asset_type, asset_id, exc)


def save(asset_type: str, asset_id: int, data: bytes | None) -> bool:
    """Write an extracted cover and its thumbnail. Always .jpg: whatever the
    source format, this is what find_cover looks for first and what the detail
    card draws. Failure here is logged and swallowed -- a missing cover costs a
    placeholder tile, which is not worth failing an upload over."""
    covers_dir = os.environ.get("WEB_COVERS_DIR")
    if not data or not covers_dir:
        return False
    directory = Path(covers_dir) / asset_type
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{asset_id}.jpg").write_bytes(data)
    except OSError as exc:
        log.warning("could not write cover for %s %s: %s", asset_type, asset_id, exc)
        return False

    # Separately guarded: a cover Pillow cannot decode is still a perfectly good
    # cover for the detail card, and the grid falls back to the original.
    write_thumb(asset_type, asset_id, data)
    return True


def write_thumb(asset_type: str, asset_id: int, data: bytes) -> bool:
    """Downscale one cover into thumb/{asset_id}.jpg. Used by save() and by
    web.thumbs; returns whether a thumbnail is now on disk."""
    covers_dir = os.environ.get("WEB_COVERS_DIR")
    if not covers_dir:
        return False
    directory = Path(covers_dir) / asset_type / "thumb"
    try:
        with Image.open(io.BytesIO(data)) as img:
            # Covers arrive as palette PNGs and the odd CMYK JPEG; the encoder
            # below takes neither. Alpha is dropped rather than composited: the
            # tile is opaque, so anything transparent would show the tile's own
            # background either way.
            img = img.convert("RGB")
            img.thumbnail((THUMB_EDGE, THUMB_EDGE))
            directory.mkdir(parents=True, exist_ok=True)
            img.save(
                directory / f"{asset_id}.jpg",
                "JPEG",
                quality=THUMB_QUALITY,
                optimize=True,
                progressive=True,
            )
        return True
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        log.warning("could not thumbnail cover for %s %s: %s", asset_type, asset_id, exc)
        return False
