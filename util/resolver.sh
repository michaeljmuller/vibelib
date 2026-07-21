#!/bin/bash
# Run the resolver CLI, e.g.:
#   util/resolver.sh resolve --limit 10 --dry-run
#   util/resolver.sh resolve
#   util/resolver.sh review list
#   util/resolver.sh review approve 42
#   util/resolver.sh acquisitions --dry-run   # amazon order dates: report
#   util/resolver.sh acquisitions             # ...and write src/sql/fixes/acquisitions.sql
set -e
cd "$(dirname "$0")/../src/docker"
docker compose run --rm resolver "$@"
