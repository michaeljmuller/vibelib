#!/bin/bash
# Ingest new epub/m4b objects from the object store into the raw asset tables.
#   util/loader.sh scan            # what's in the bucket but not the database
#   util/loader.sh load            # ingest all of it
#   util/loader.sh load --limit 5  # ingest a few first
set -e
cd "$(dirname "$0")/../src/docker"
exec docker compose run --rm loader "$@"
