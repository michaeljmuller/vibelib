"""Nightly pg_dump of the library database to object storage.

    python3 -m backup               the nightly loop (the container default)
    python3 -m backup now           back up once
    python3 -m backup list          list the dumps in the bucket
    python3 -m backup fetch <key>   download one dump to /backups (data/backups)
    python3 -m backup s3-test       write, read and delete one object

A dump is the same pg_dump custom format that util/dump.sh writes, so a fetched
dump restores with `util/dump.sh restore data/backups/<key>`.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .plan import dump_name, is_ours, parse_hhmm, schedule_disabled, seconds_until, select_prunable
from .storage import ObjectStore, config_summary, diagnose, missing_settings

log = logging.getLogger("backup")

RUN_AT = os.environ.get("RUN_AT", "03:30")
RETAIN = int(os.environ.get("BACKUP_RETAIN", "30"))
FETCH_DIR = Path(os.environ.get("FETCH_DIR", "/backups"))


def dump(out: Path) -> None:
    """pg_dump over the compose network. PGHOST, PGUSER, PGPASSWORD etc. come
    from the environment, as libpq reads them."""
    subprocess.run(["pg_dump", "-Fc", "--no-password", "-f", str(out)], check=True)


def backup_once() -> int:
    store = ObjectStore.from_env()
    if store is None:
        missing = ", ".join(missing_settings(dict(os.environ)))
        log.warning("object storage is not configured (%s); no backup taken", missing)
        return 1

    now = datetime.now(timezone.utc)
    key = dump_name(now)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / key
            dump(path)
            size = path.stat().st_size
            store.upload(path, key)
        log.info("uploaded %s (%.1f MiB)", key, size / 2**20)

        # Only after a successful upload, so a run of failures never prunes the
        # good dumps away.
        pruned = select_prunable(store.list_keys(), RETAIN)
        store.delete(pruned)
        if pruned:
            log.info("deleted %d old dump(s), keeping %d", len(pruned), RETAIN)
    except Exception as exc:
        log.error("backup FAILED: %s: %s", type(exc).__name__, exc)
        if not isinstance(exc, subprocess.CalledProcessError):
            log.error("%s", diagnose(exc))
        return 1
    return 0


def list_dumps() -> int:
    store = ObjectStore.from_env()
    if store is None:
        log.error("object storage is not configured")
        return 1
    for key in sorted(k for k in store.list_keys() if is_ours(k)):
        print(key)
    return 0


def fetch(key: str) -> int:
    store = ObjectStore.from_env()
    if store is None:
        log.error("object storage is not configured")
        return 1
    FETCH_DIR.mkdir(parents=True, exist_ok=True)
    out = FETCH_DIR / Path(key).name
    if out.exists():
        log.error("%s already exists; move it aside first", out)
        return 1
    store.download(key, out)
    log.info("wrote data/backups/%s. Restore it with: util/dump.sh restore data/backups/%s",
             out.name, out.name)
    return 0


def s3_test() -> int:
    """The round trip covers every permission a backup needs. A credential that
    can write but not delete passes a connection check, then fails at retention."""
    env = dict(os.environ)
    for label, value in config_summary(env):
        log.info("%-11s %s", label, value)
    missing = missing_settings(env)
    if missing:
        log.error("missing: %s", ", ".join(missing))
        return 1

    key = f"s3-test-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.txt"
    payload = f"vibelib backup storage test {key}\n".encode()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            store = ObjectStore.from_env()
            local = Path(tmp) / "out"
            local.write_bytes(payload)
            store.upload(local, key)
            log.info("wrote %s", key)
            count = sum(1 for k in store.list_keys() if is_ours(k))
            log.info("listed the bucket: %d dump(s)", count)
            back = Path(tmp) / "back"
            store.download(key, back)
            if back.read_bytes() != payload:
                log.error("the object read back does not match")
                return 1
            log.info("read it back")
            store.delete([key])
            log.info("deleted it")
        except Exception as exc:
            log.error("%s: %s", type(exc).__name__, exc)
            log.error("%s", diagnose(exc))
            return 1
    log.info("object storage works")
    return 0


def watch() -> int:
    if schedule_disabled(RUN_AT):
        # Idle rather than exit, so the restart policy does not loop it.
        log.info('RUN_AT=never: the nightly backup is off. Run one with '
                 '`docker compose run --rm backup python3 -m backup now`.')
        while True:
            time.sleep(3600)
    try:
        parse_hhmm(RUN_AT)
    except ValueError as exc:
        log.error("%s", exc)
        return 2
    log.info("backing up nightly at %s (%s), keeping %d", RUN_AT, os.environ.get("TZ", "UTC"), RETAIN)
    while True:
        time.sleep(seconds_until(RUN_AT, datetime.now()))
        backup_once()
        time.sleep(60)  # out of the target minute, so it does not fire twice


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    parser = argparse.ArgumentParser(prog="backup")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("watch", help="the nightly loop (default)")
    sub.add_parser("now", help="back up once")
    sub.add_parser("list", help="list the dumps in the bucket")
    fetch_cmd = sub.add_parser("fetch", help="download one dump to data/backups")
    fetch_cmd.add_argument("key")
    sub.add_parser("s3-test", help="check the object storage settings")
    args = parser.parse_args(argv)

    if args.command == "now":
        return backup_once()
    if args.command == "list":
        return list_dumps()
    if args.command == "fetch":
        return fetch(args.key)
    if args.command == "s3-test":
        return s3_test()
    return watch()


if __name__ == "__main__":
    raise SystemExit(main())
