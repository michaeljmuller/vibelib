#!/bin/bash
set -e
cd "$(dirname "$0")/../src/docker"
docker compose exec db psql -U vibelib vibelib
