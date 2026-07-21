"""Cover images, served off the tree the ingestion tooling extracts them into:
{WEB_COVERS_DIR}/{epub,m4b}/{asset_id}.{jpg,jpeg,png}"""

import os
from pathlib import Path


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
