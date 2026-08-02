#!/bin/bash
# Build the grid thumbnails for covers that predate thumbnailing. One-off after
# deploying that change; new covers get one at ingest.
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
