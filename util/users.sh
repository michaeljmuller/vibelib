#!/bin/bash
# Who may sign in to the library. Google says who someone is; this list says
# whether they are welcome (see src/sql/migrations/006_users.sql).
#   util/users.sh                     list everyone invited
#   util/users.sh add <email> [name]  invite someone
#   util/users.sh remove <email>      revoke an invitation
set -e
cd "$(dirname "$0")/../src/docker"

# SQL arrives on stdin rather than via -c: psql only interpolates :'vars' when it
# lexes the input itself, and -c bypasses that, sending the string as written.
psql() { docker compose exec -T db psql -U vibelib vibelib -v ON_ERROR_STOP=1 "$@"; }

case "${1:-list}" in
  list)
    psql <<<"SELECT email, name, invited_on, last_login_at FROM users ORDER BY email"
    ;;
  add)
    [ -n "$2" ] || { echo "usage: $0 add <email> [name]" >&2; exit 1; }
    # Lowercased on the way in: the app folds case before it looks anyone up, so a
    # row stored with capitals would be an invitation nobody could ever redeem.
    psql -q -v email="$2" -v name="${3:-}" <<<"
      INSERT INTO users (email, name) VALUES (lower(:'email'), nullif(:'name', ''))
      ON CONFLICT (email) DO NOTHING"
    echo "invited: $2"
    ;;
  remove)
    [ -n "$2" ] || { echo "usage: $0 remove <email>" >&2; exit 1; }
    # Only bars future logins. Any session they already hold stays valid until it
    # expires; rotate OAUTH_SESSION_SECRET to end every session everywhere, now.
    psql -q -v email="$2" <<<"DELETE FROM users WHERE email = lower(:'email')"
    echo "revoked: $2"
    ;;
  *)
    echo "usage: $0 [list|add <email> [name]|remove <email>]" >&2
    exit 1
    ;;
esac
