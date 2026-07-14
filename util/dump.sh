#!/bin/bash
# Back up and restore the library's curated metadata.
#
# This is the only portable copy of the database. The data now lives in a named
# volume rather than a directory you can see, and a Postgres data directory could
# not have been copied between machines anyway (it is architecture-specific: an
# arm64 Mac and an x86_64 server do not agree on its layout). A pg_dump is
# portable, so this is how the library moves -- and how it survives.
#
#   util/dump.sh                    dump to ~/vibelib-<today>.dump
#   util/dump.sh dump <file>        dump somewhere specific
#   util/dump.sh restore <file>     load a dump into the running database
#   util/dump.sh verify             what is actually in there right now
#
# What is worth protecting here is not the books -- those are in the object store,
# and could be re-ingested. It is the curation: the resolved authors, the pseudonym
# links, the series ordering, the acquisition dates mined out of the old library.
# That work exists nowhere else. Run this on a schedule.
set -e
cd "$(dirname "$0")/../src/docker"

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
    out="${2:-$HOME/vibelib-$(date +%F).dump}"
    db pg_dump -U vibelib -Fc vibelib > "$out"
    echo "wrote $out ($(du -h "$out" | cut -f1))"
    ;;

  restore)
    [ -f "${2:-}" ] || { echo "usage: $0 restore <file>" >&2; exit 1; }
    # --clean --if-exists so this is safe to re-run: it drops what it is about to
    # recreate, and does not complain when there is nothing to drop.
    db pg_restore -U vibelib -d vibelib --clean --if-exists < "$2"
    echo "restored from $2"
    verify
    ;;

  verify) verify ;;

  *)
    echo "usage: $0 [dump [file]|restore <file>|verify]" >&2
    exit 1
    ;;
esac
