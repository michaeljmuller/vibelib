#!/bin/bash
# Write the thumbnails that are missing for covers already on disk. New covers
# get one at ingest, so this is the repair tool, not part of the normal path:
# run it when an ingest failed to thumbnail (that cover serves full-size until
# you do), and with --force after changing covers.THUMB_EDGE.
#
#   util/thumbs.sh          fill in what is missing (safe to re-run)
#   util/thumbs.sh --force  rebuild every thumbnail
#
# Originals are only read. Thumbnails land in data/covers/{epub,m4b}/thumb/.
set -e
cd "$(dirname "$0")/../src/docker"

# --no-deps: this walks the covers mount and touches neither the database nor
# the bucket, so there is no healthy db to wait on.
exec docker compose run --rm --no-deps --build \
  --entrypoint python web -m web.backfill_thumbs "$@"
