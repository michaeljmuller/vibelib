#!/bin/bash
# A psql shell on the library database. Arguments are passed through, so it also
# works for one-off queries and for feeding it a script:
#   util/psql.sh
#   util/psql.sh -c "SELECT count(*) FROM books"
#   util/psql.sh -v apply=1 < some-script.sql
set -e
cd "$(dirname "$0")/../src/docker"

# A TTY when there is one to give (the interactive shell needs it), and -T when
# input is a pipe: with a TTY attached the engine translates newlines on the way
# in, which mangles a script being fed on stdin. See the same note in dump.sh.
if [ -t 0 ]; then
  docker compose exec db psql -U vibelib vibelib "$@"
else
  docker compose exec -T db psql -U vibelib vibelib "$@"
fi
