"""CLI wrapper: `loader scan` and `loader load`."""

import argparse
import logging
import sys

from . import db, pipeline


def _cmd_scan(args: argparse.Namespace) -> int:
    with db.connect() as conn:
        new = pipeline.scan(conn)

    total = sum(len(v) for v in new.values())
    if not total:
        print("nothing new — every epub and m4b in the bucket is already loaded")
        return 0

    for asset_type, keys in new.items():
        if not keys:
            continue
        print(f"\n{len(keys)} new {asset_type}(s):")
        for key in keys:
            print(f"  {key}")
    print(f"\n{total} object(s) to load; run: util/loader.sh load")
    return 0


def _cmd_load(args: argparse.Namespace) -> int:
    with db.connect() as conn:
        summary = pipeline.load(
            conn, limit=args.limit, dry_run=args.dry_run, only=args.type
        )

    print("\n== summary ==")
    for key, n in summary["counts"].items():
        print(f"  {key}: {n}")
    if summary["errors"]:
        print(f"\n{len(summary['errors'])} file(s) failed and were NOT loaded:")
        for key, err in summary["errors"]:
            print(f"  {key}\n      {err}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="loader")
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser(
        "scan", help="list bucket objects that have no row yet (read-only)"
    )
    p_scan.set_defaults(func=_cmd_scan)

    p_load = sub.add_parser("load", help="ingest new objects into epubs/m4bs")
    p_load.add_argument("--limit", type=int, default=None, help="load at most N assets")
    p_load.add_argument("--dry-run", action="store_true", help="list, write nothing")
    p_load.add_argument(
        "--type", choices=pipeline.ASSET_TYPES, help="only this asset type"
    )
    p_load.set_defaults(func=_cmd_load)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
