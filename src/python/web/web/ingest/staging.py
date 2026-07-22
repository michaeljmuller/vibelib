"""Where a file sits while it is being read.

A bind-mounted directory rather than the container's own filesystem, because an
audiobook is up to a gigabyte and the writable layer is the wrong place to put
one -- under rootless podman that layer lives in the graph root, where filling
the disk takes more than this app down with it. On a bind mount the same file is
visible from the host, which also makes "what is it doing with 900MB?" a
question you can answer with ls.

Everything in here is disposable by construction. A file is either being worked
on right now or was left behind by a process that died, and the second kind is
swept at startup: nothing here is the only copy of anything, since a staged
upload that never finished was never recorded, and a fetch can always be redone
from the bucket.
"""

import logging
import os
import re
import tempfile
from pathlib import Path

log = logging.getLogger("uvicorn.error")

DEFAULT_DIR = "/staging"
PREFIX = "ingest-"

# Only what makes a sane filename fragment; the real name is carried separately.
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def directory() -> Path:
    path = Path(os.environ.get("INGEST_STAGING_DIR", DEFAULT_DIR))
    path.mkdir(parents=True, exist_ok=True)
    return path


def reserve(label: str) -> str:
    """An empty file to work in, named after what it holds so the directory can
    be read by a human mid-transfer."""
    stem, suffix = os.path.splitext(os.path.basename(label))
    fd, path = tempfile.mkstemp(
        dir=directory(),
        prefix=f"{PREFIX}{_SAFE.sub('_', stem)[:60]}-",
        suffix=suffix.lower() or None,
    )
    os.close(fd)
    return path


def discard(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def sweep() -> None:
    """Delete whatever an interrupted run left behind. Called at worker start,
    which is the only moment nothing here can be in use."""
    removed = 0
    try:
        for entry in directory().iterdir():
            if entry.is_file() and entry.name.startswith(PREFIX):
                try:
                    entry.unlink()
                    removed += 1
                except OSError as exc:
                    log.warning("could not sweep %s: %s", entry, exc)
    except OSError as exc:
        log.warning("staging directory unavailable: %s", exc)
        return
    if removed:
        log.info("staging: swept %d file(s) left by an earlier run", removed)
