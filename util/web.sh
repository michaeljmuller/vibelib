#!/bin/bash
# Run the library web UI on http://localhost:8000
#   util/web.sh          build (if needed) and start it in the background
#   util/web.sh logs     follow its logs
#   util/web.sh stop     stop it
set -e
cd "$(dirname "$0")/../src/docker"

case "${1:-up}" in
  up)   docker compose up -d --build web && echo "vibelib: http://localhost:8000" ;;
  logs) docker compose logs -f web ;;
  stop) docker compose stop web ;;
  *)    echo "usage: $0 [up|logs|stop]" >&2; exit 1 ;;
esac
