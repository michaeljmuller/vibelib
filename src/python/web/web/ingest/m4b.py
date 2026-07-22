"""Extract raw metadata from an m4b audiobook.

Tags and cover art come from mutagen; chapters come from ffprobe. Chapters are
the reason for the ffmpeg dependency: an m4b may carry them as a Nero `chpl`
atom or as a QuickTime chapter text track, mutagen exposes neither, and this
library contains both shapes.
"""

import json
import logging
import subprocess
from dataclasses import dataclass, field

from mutagen.mp4 import MP4

log = logging.getLogger(__name__)

# mutagen's MP4 tag atoms; see the column comments in schema.sql.
TAGS = {
    "title": "\xa9nam",
    "artist": "\xa9ART",
    "narrator": "\xa9wrt",
    "album": "\xa9alb",
    "date": "\xa9day",
    "comment": "\xa9cmt",
    "genre": "\xa9gen",
    "copyright": "cprt",
}
ASIN_ATOM = "----:com.apple.iTunes:ASIN"


@dataclass
class Chapter:
    position: int
    title: str | None
    start_ms: int


@dataclass
class M4b:
    title: str | None = None
    artist: str | None = None
    narrator: str | None = None
    album: str | None = None
    date: str | None = None
    description: str | None = None
    comment: str | None = None
    genre: str | None = None
    copyright: str | None = None
    asin: str | None = None
    has_cover: bool = False
    duration_s: int | None = None
    bitrate_kbps: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    chapters: list[Chapter] = field(default_factory=list)


def _first(tags, atom: str) -> str | None:
    values = tags.get(atom)
    if not values:
        return None
    value = values[0]
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    value = str(value).strip()
    return value or None


def _chapters(path: str) -> list[Chapter]:
    """ffprobe reads both chapter conventions. A file with no chapters is normal
    (a single-file audiobook), so failure here is logged, never fatal."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_chapters", path],
            capture_output=True,
            check=True,
            timeout=120,
        ).stdout
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("ffprobe failed on %s: %s", path, exc)
        return []

    chapters = []
    for i, ch in enumerate(json.loads(out).get("chapters", []), start=1):
        try:
            start_ms = int(float(ch["start_time"]) * 1000)
        except (KeyError, ValueError):
            continue
        chapters.append(
            Chapter(
                position=i,
                title=(ch.get("tags") or {}).get("title") or None,
                start_ms=start_ms,
            )
        )
    return chapters


def parse(path: str) -> M4b:
    mp4 = MP4(path)
    tags = mp4.tags or {}

    out = M4b(**{name: _first(tags, atom) for name, atom in TAGS.items()})
    out.description = _first(tags, "ldes") or _first(tags, "desc")
    out.asin = _first(tags, ASIN_ATOM)
    out.has_cover = bool(tags.get("covr"))

    info = mp4.info
    if info is not None:
        out.duration_s = int(round(info.length)) if info.length else None
        out.bitrate_kbps = int(info.bitrate / 1000) if info.bitrate else None
        out.sample_rate = info.sample_rate or None
        out.channels = info.channels or None

    out.chapters = _chapters(path)
    return out


def cover_bytes(path: str) -> bytes | None:
    tags = MP4(path).tags or {}
    covers = tags.get("covr")
    return bytes(covers[0]) if covers else None
