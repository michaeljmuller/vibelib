#!/bin/bash
# Shell into the restored legacy library database (MariaDB). With arguments,
# runs them as SQL and exits, e.g.:
#   util/mysql.sh
#   util/mysql.sh "SELECT count(*) FROM assets WHERE acq_date IS NOT NULL"
set -e
cd "$(dirname "$0")/../src/docker"

PW="$(grep -E '^LEGACY_DB_PASSWORD=' .env 2>/dev/null | cut -d= -f2-)"
PW="${PW:-legacy}"

if [ $# -eq 0 ]; then
  exec docker compose exec legacy-db mariadb -uroot -p"$PW" library
fi
exec docker compose exec -T legacy-db mariadb -uroot -p"$PW" library -e "$*"
