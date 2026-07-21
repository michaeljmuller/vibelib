#!/bin/bash
# Back up and restore the library's curated metadata.
#
# This is the only portable copy of the database. The data now lives in a named
# volume rather than a directory you can see, and a Postgres data directory could
# not have been copied between machines anyway (it is architecture-specific: an
# arm64 Mac and an x86_64 server do not agree on its layout). A pg_dump is
# portable, so this is how the library moves -- and how it survives.
#
#   util/dump.sh                    dump to data/backups/vibelib-<timestamp>.dump
#   util/dump.sh dump <file>        dump somewhere specific
#   util/dump.sh restore <file>     load a dump into the running database
#   util/dump.sh verify             what is actually in there right now
#
# What is worth protecting here is not the books -- those are in the object store,
# and could be re-ingested. It is the curation: the resolved authors, the pseudonym
# links, the series ordering, the acquisition dates mined out of the old library.
# That work exists nowhere else. Run this on a schedule.
#
# Dumps land in data/backups, beside the older ones, rather than in the home
# directory: they belong to the project, and data/ is gitignored so they never risk
# being committed. The name carries the time as well as the date, so two runs on
# one day are two files -- the sequence that would otherwise bite is dump, change
# something, dump again "to be safe", and overwrite the copy you wanted back.
set -e
# Both resolved before the cd below: the default output path is relative to the
# project, while a path given on the command line should mean what it meant in the
# shell you typed it in.
here=$PWD
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root/src/docker"

abspath() { case "$1" in /*) printf '%s' "$1" ;; *) printf '%s/%s' "$here" "$1" ;; esac; }

# -T: no TTY. Without it the engine translates newlines and quietly corrupts the
# binary stream, producing a dump that restores to nothing.
db() { docker compose exec -T db "$@"; }

verify() {
  db psql -U vibelib vibelib -c \
    "SELECT (SELECT count(*) FROM books)   AS books,
            (SELECT count(*) FROM epubs)   AS epubs,
            (SELECT count(*) FROM m4bs)    AS m4bs,
            (SELECT count(*) FROM people)  AS people,
            (SELECT count(*) FROM series)  AS series,
            (SELECT count(*) FROM users)   AS users"
}

case "${1:-dump}" in
  dump)
    if [ -n "${2:-}" ]; then
      out=$(abspath "$2")   # you named it, so overwriting it is your call
    else
      # A timestamp to the second, and then a suffix if that still collides: two
      # runs back to back really do land in the same second, and the whole point of
      # the default name is that it never quietly replaces an earlier backup.
      base="$root/data/backups/vibelib-$(date +%Y%m%d-%H%M%S)"
      out="$base.dump"; n=2
      while [ -e "$out" ]; do out="$base-$n.dump"; n=$((n + 1)); done
    fi
    mkdir -p "$(dirname "$out")"
    db pg_dump -U vibelib -Fc vibelib > "$out"
    echo "wrote $out ($(du -h "$out" | cut -f1))"
    ;;

  restore)
    in=$(abspath "${2:-}")
    [ -n "${2:-}" ] && [ -f "$in" ] || { echo "usage: $0 restore <file>" >&2; exit 1; }
    # --clean --if-exists so this is safe to re-run: it drops what it is about to
    # recreate, and does not complain when there is nothing to drop.
    db pg_restore -U vibelib -d vibelib --clean --if-exists < "$in"
    echo "restored from $in"
    verify
    ;;

  verify) verify ;;

  *)
    echo "usage: $0 [dump [file]|restore <file>|verify]" >&2
    exit 1
    ;;
esac
