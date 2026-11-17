#!/bin/bash
# Correct acquisition dates from Amazon digital orders. See docs/amazon-dates.txt.
#   util/amazon-dates.sh match ~/Downloads/amazon-digital-orders-*.txt
#   util/amazon-dates.sh apply plan-YYYYMMDD-HHMMSS.json
#
# match copies the order files into data/amazon-dates/ (gitignored) and writes
# the plan and its report there. apply takes a plan file name from that folder.
# The token comes from ADMIN_API_TOKEN in src/docker/.env and goes to the
# container as an environment variable, never on a command line.
# VIBELIB_URL overrides the server (default https://vibelib.themullers.org).
set -e
root="$(cd "$(dirname "$0")/.." && pwd)"
work="$root/data/amazon-dates"
mkdir -p "$work"

ADMIN_API_TOKEN=$(sed -n 's/^ADMIN_API_TOKEN=//p' "$root/src/docker/.env" | tr -d '"'"'"' \r')
export ADMIN_API_TOKEN VIBELIB_URL

cmd="$1"; shift || true
args=()
case "$cmd" in
  match)
    [ $# -ge 1 ] || { echo "usage: $0 match FILE..." >&2; exit 2; }
    for f in "$@"; do
      cp "$f" "$work/"
      args+=("/work/$(basename "$f")")
    done ;;
  apply)
    [ $# -eq 1 ] || { echo "usage: $0 apply PLAN" >&2; exit 2; }
    args=("/work/$(basename "$1")") ;;
  *) echo "usage: $0 match FILE... | apply PLAN" >&2; exit 2 ;;
esac

docker run --rm -e ADMIN_API_TOKEN -e VIBELIB_URL \
  -v "$root/util/amazon_dates.py:/app/amazon_dates.py:ro" \
  -v "$work:/work" \
  python:3.12-slim python -I /app/amazon_dates.py "$cmd" "${args[@]}"
