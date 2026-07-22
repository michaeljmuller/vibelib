"""Cover images, in the tree ingestion extracts them into:
{WEB_COVERS_DIR}/{epub,m4b}/{asset_id}.{jpg,jpeg,png}

Read by the browse UI and by the resolver's cover-image fallback; written by
web.ingest.pipeline, which is why the mount is no longer read-only."""

import logging
import os
from pathlib import Path

log = logging.getLogger("uvicorn.error")


def find_cover(asset_type: str, asset_id: int) -> Path | None:
    covers_dir = os.environ.get("WEB_COVERS_DIR")
    if not covers_dir or asset_type not in ("epub", "m4b"):
        return None
    for ext in ("jpg", "jpeg", "png"):
        p = Path(covers_dir) / asset_type / f"{asset_id}.{ext}"
        if p.is_file():
            return p
    return None


def media_type(path: Path) -> str:
    return "image/png" if path.suffix.lower() == ".png" else "image/jpeg"


def save(asset_type: str, asset_id: int, data: bytes | None) -> bool:
    """Write an extracted cover. Always .jpg: whatever the source format, this
    is what find_cover looks for first and what the grid draws. Failure here is
    logged and swallowed -- a missing cover costs a placeholder tile, which is
    not worth failing an upload over."""
    covers_dir = os.environ.get("WEB_COVERS_DIR")
    if not data or not covers_dir:
        return False
    directory = Path(covers_dir) / asset_type
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{asset_id}.jpg").write_bytes(data)
        return True
    except OSError as exc:
        log.warning("could not write cover for %s %s: %s", asset_type, asset_id, exc)
        return False
