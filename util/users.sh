#!/bin/bash
# Who may sign in to the library. Google says who someone is; this list says
# whether they are welcome (see src/sql/migrations/006_users.sql).
#
# Day to day, an admin does this from the browser: /admin, or the "Accounts"
# link in the header. What lives only here are the two things the web UI refuses
# to do -- promote an admin, and remove one -- because an admin with a slip of the
# mouse could otherwise leave the library with no admins at all and no way back in
# through the front door. Making that a shell operation is the speed bump.
#
#   util/users.sh                     list every allowed account
#   util/users.sh add <email> [name]  let this Google account in
#   util/users.sh remove <email>      take it back off the list
#   util/users.sh promote <email>     make them an admin
#   util/users.sh demote <email>      take it away
set -e
cd "$(dirname "$0")/../src/docker"

# SQL arrives on stdin rather than via -c: psql only interpolates :'vars' when it
# lexes the input itself, and -c bypasses that, sending the string as written.
psql() { docker compose exec -T db psql -U vibelib vibelib -v ON_ERROR_STOP=1 "$@"; }

# Guard against the one irreversible mistake: an admin-less library can only be
# fixed from here, and only if you still have a shell.
refuse_if_last_admin() {
  local remaining
  remaining=$(psql -tAq -v email="$1" <<<"
    SELECT count(*) FROM users WHERE is_admin AND lower(email) <> lower(:'email')")
  if [ "$remaining" -eq 0 ]; then
    echo "refusing: $1 is the only admin. Promote someone else first." >&2
    exit 1
  fi
}

case "${1:-list}" in
  list)
    psql <<<"SELECT email, name, is_admin, added_on, last_login_at
             FROM users ORDER BY added_on, email"
    ;;
  add)
    [ -n "$2" ] || { echo "usage: $0 add <email> [name]" >&2; exit 1; }
    # Lowercased on the way in: the app folds case before it looks anyone up, so a
    # row stored with capitals would be an entry nobody could ever match.
    psql -q -v email="$2" -v name="${3:-}" <<<"
      INSERT INTO users (email, name) VALUES (lower(:'email'), nullif(:'name', ''))
      ON CONFLICT (email) DO NOTHING"
    echo "added: $2"
    ;;
  remove)
    [ -n "$2" ] || { echo "usage: $0 remove <email>" >&2; exit 1; }
    refuse_if_last_admin "$2"
    # Takes effect immediately: the gate checks this table on every request, so a
    # removed user's next click sends them to the login page, session or no session.
    psql -q -v email="$2" <<<"DELETE FROM users WHERE email = lower(:'email')"
    echo "removed: $2"
    ;;
  promote)
    [ -n "$2" ] || { echo "usage: $0 promote <email>" >&2; exit 1; }
    psql -q -v email="$2" <<<"UPDATE users SET is_admin = true WHERE email = lower(:'email')"
    echo "promoted: $2"
    ;;
  demote)
    [ -n "$2" ] || { echo "usage: $0 demote <email>" >&2; exit 1; }
    refuse_if_last_admin "$2"
    psql -q -v email="$2" <<<"UPDATE users SET is_admin = false WHERE email = lower(:'email')"
    echo "demoted: $2"
    ;;
  *)
    echo "usage: $0 [list|add <email> [name]|remove <email>|promote <email>|demote <email>]" >&2
    exit 1
    ;;
esac
