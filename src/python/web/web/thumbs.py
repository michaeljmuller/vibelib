"""Write the thumbnails that are missing for covers already on disk.

The batch driver only; covers.py owns what a thumbnail is and how one is made,
and this walks the tree calling covers.write_thumb. Kept out of covers.py
because that module is imported on every request path and this one is a shell
command with logging and argument handling.

New covers get a thumbnail at ingest (see covers.save), so this is not part of
the normal path -- it is the repair for the cases that leave a cover without
one:

  * an ingest where thumbnailing failed (unreadable image, full disk); the
    cover still serves, at full size, until this is run
  * a change to covers.THUMB_EDGE, which makes every existing thumbnail the
    wrong size -- that is what --force is for
  * the initial run against a library ingested before thumbnails existed

Idempotent and resumable: it skips any asset that already has a thumbnail, so
re-running after an interruption costs a directory scan and nothing else.
Originals are opened read-only and never written.

    util/thumbs.sh          fill in what is missing
    util/thumbs.sh --force  rebuild every thumbnail
"""

import logging
import os
import sys
from pathlib import Path

from . import covers

log = logging.getLogger("thumbs")


def run(force: bool = False) -> int:
    covers_dir = os.environ.get("WEB_COVERS_DIR")
    if not covers_dir:
        log.error("WEB_COVERS_DIR is not set")
        return 1

    made = skipped = failed = 0
    for asset_type in ("epub", "m4b"):
        directory = Path(covers_dir) / asset_type
        if not directory.is_dir():
            continue
        # Sorted so the progress line means something on a big library; the
        # thumb/ subdirectory is skipped by the is_file test.
        for original in sorted(p for p in directory.iterdir() if p.is_file()):
            if original.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue
            try:
                asset_id = int(original.stem)
            except ValueError:
                continue  # not one of ours

            if not force and covers.find_thumb(asset_type, asset_id):
                skipped += 1
                continue
            if covers.write_thumb(asset_type, asset_id, original.read_bytes()):
                made += 1
            else:
                failed += 1
            if (made + failed) % 100 == 0 and made + failed:
                log.info("%s: %d done", asset_type, made + failed)

    log.info("thumbnails written %d, already present %d, failed %d", made, skipped, failed)
    # Failures are reported but not fatal: an undecodable cover falls back to
    # the original at request time, which is the pre-existing behaviour.
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return run(force="--force" in sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
