"""Apply pending schema migrations when the app starts.

The deploy is `git pull` then `compose up --build` (docs/release-process.txt). Any
schema change in that pull has to reach the database somehow, and the alternative
to doing it here is remembering to do it by hand on the server, at the right
moment, every time. That is a step you get away with skipping until the once you
don't -- and the failure is the new code meeting the old schema, in production,
in front of the family.

So: each migration runs exactly once, in filename order, and the app does not
begin serving until they have all landed. If one fails, startup fails. A library
that is down is a bad afternoon; a library running new code against a half-
migrated schema is a corrupted one.

Conventions, for whoever adds 007:

  * Name it NNN_what_it_does.sql, zero-padded. Order is filename order.
  * Do not write BEGIN/COMMIT. The runner wraps each file in a transaction
    together with the bookkeeping that records it as applied, so a migration
    either lands and is remembered, or does neither. A file that commits itself
    breaks that seam -- it could apply and then be forgotten, and be applied
    again on the next boot.
  * Postgres does DDL transactionally, so a migration that fails halfway leaves
    no trace. That is the whole reason this is safe to run automatically.

What this deliberately does not do is build a database from nothing. It brings an
existing library up to date. A new one comes from a dump (util/dump.sh restore),
which is how production was built -- and note that src/sql/schema.sql has drifted
into a hybrid (it already contains migration 001's outcome), so it is no longer a
clean base to replay from. Rebuilding from a dump avoids that question entirely.
"""

import logging
import os
from pathlib import Path

from psycopg_pool import ConnectionPool

log = logging.getLogger("uvicorn.error")

MIGRATIONS_DIR = Path(os.environ.get("WEB_MIGRATIONS_DIR", "/sql/migrations"))

# Everything up to and including this file was applied by hand, before this runner
# existed -- so on a database that predates it, they are recorded as already-done
# rather than re-run. Re-running them would mostly fail loudly (the tables are
# there), which is not the worry; the worry is the one that would not.
BASELINE = "006_users.sql"

# Two web containers starting at once would otherwise both see the same work
# pending and both try to do it. The lock makes the second wait, and by the time
# it looks, the first has recorded everything and it finds nothing to do.
LOCK_KEY = 0x7669626C  # "vibl"


def run(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        conn.autocommit = True  # each migration manages its own transaction, below
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            _bootstrap(conn)
            _apply_pending(conn)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


def _bootstrap(conn) -> None:
    """Create the ledger, and on a database that predates it, write down what was
    already true."""
    if not conn.execute(
        "SELECT to_regclass('public.schema_migrations') IS NULL AS missing"
    ).fetchone()["missing"]:
        return

    library_exists = conn.execute(
        "SELECT to_regclass('public.books') IS NOT NULL AS yes"
    ).fetchone()["yes"]
    if not library_exists:
        raise RuntimeError(
            f"The database is empty. This runner brings an existing library up to "
            f"date; it does not create one. Restore a dump first: "
            f"util/dump.sh restore <file>. (Looked for migrations in {MIGRATIONS_DIR}.)"
        )

    with conn.transaction():
        conn.execute(
            """CREATE TABLE schema_migrations (
                   filename    TEXT PRIMARY KEY,
                   applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
               )"""
        )
        # The library exists, so everything through BASELINE is already in it.
        baseline = [p.name for p in _migrations() if p.name <= BASELINE]
        conn.execute(
            "INSERT INTO schema_migrations (filename) SELECT unnest(%s::text[])",
            (baseline,),
        )
    log.info(
        "migrations: first run against an existing library; %d migration(s) through "
        "%s recorded as already applied",
        len(baseline),
        BASELINE,
    )


def _apply_pending(conn) -> None:
    applied = {
        r["filename"] for r in conn.execute("SELECT filename FROM schema_migrations")
    }
    pending = [p for p in _migrations() if p.name not in applied]
    if not pending:
        log.info("migrations: up to date (%d applied)", len(applied))
        return

    log.info("migrations: %d pending", len(pending))
    for path in pending:
        log.info("migrations: applying %s", path.name)
        # The migration and the record of it, in one transaction: it lands and is
        # remembered, or neither.
        with conn.transaction():
            conn.execute(path.read_text())
            conn.execute(
                "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
            )
        log.info("migrations: applied %s", path.name)


def _migrations() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))
