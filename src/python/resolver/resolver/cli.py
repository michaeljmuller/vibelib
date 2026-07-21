"""CLI wrapper: `resolver resolve` and `resolver review`."""

import argparse
import json
import logging
import sys
from pathlib import Path

from . import acquisitions, apply, audit, backfill, db, pipeline, review


def _cmd_audit(args: argparse.Namespace) -> int:
    checks = tuple(args.check) if args.check else audit.CHECKS
    if args.json:
        # main() logs at INFO to stdout; httpx's per-request lines would corrupt
        # the JSON document the caller is about to parse.
        logging.getLogger().setLevel(logging.WARNING)
    with db.connect() as conn:
        findings = audit.run(conn, checks=checks, use_llm=not args.no_llm)

    if args.json:
        print(json.dumps([f.to_dict() for f in findings], indent=2, ensure_ascii=False))
        return 0

    print(audit.render(findings))
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.verdict] = counts.get(f.verdict, 0) + 1
    print("== summary ==")
    for verdict, n in sorted(counts.items(), key=lambda kv: audit.ORDER[kv[0]]):
        print(f"  {verdict}: {n}")
    return 0


def _cmd_resolve(args: argparse.Namespace) -> int:
    with db.connect() as conn:
        if args.asset:
            try:
                asset_type, raw_id = args.asset.split(":", 1)
                asset_id = int(raw_id)
                assert asset_type in ("epub", "m4b")
            except (ValueError, AssertionError):
                print(f"--asset must look like epub:123 or m4b:45, got {args.asset!r}")
                return 2
            outcome = pipeline.resolve_asset(
                conn, asset_type, asset_id, dry_run=args.dry_run
            )
            print(f"{asset_type}:{asset_id} -> {outcome.status} "
                  f"(method={outcome.method}, confidence={outcome.confidence})")
            if outcome.notes:
                print(f"  notes: {outcome.notes}")
            if outcome.proposal and (args.dry_run or outcome.status == "pending"):
                print(json.dumps(outcome.proposal, indent=2, ensure_ascii=False))
            return 0

        summary = pipeline.resolve_all(conn, limit=args.limit, dry_run=args.dry_run)
        print("\n== summary ==")
        for key, n in sorted(summary["counts"].items()):
            print(f"  {key}: {n}")
        if summary["usage"]:
            print(f"  tokens: {summary['usage']}")
        return 0


def _cmd_backfill(args: argparse.Namespace) -> int:
    with db.connect() as conn:
        summary = backfill.backfill_books(conn, limit=args.limit, dry_run=args.dry_run)
    print("\n== summary ==")
    for key, n in sorted(summary["counts"].items()):
        print(f"  {key}: {n}")
    if summary["usage"]:
        print(f"  tokens: {summary['usage']}")
    return 0


def _cmd_acquisitions(args: argparse.Namespace) -> int:
    with db.connect() as conn:
        result = acquisitions.run(
            conn, export=args.export, out=args.out, dry_run=args.dry_run
        )
    print(result["report"])
    if args.dry_run:
        print(f"\ndry run: {args.out} not written")
    else:
        print(f"\nwrote {args.out} -- replay it with:\n"
              f"  util/psql.sh            < src/sql/fixes/acquisitions.sql   # report only\n"
              f"  util/psql.sh -v apply=1 < src/sql/fixes/acquisitions.sql   # and keep it")
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    with db.connect() as conn:
        if args.action is None:
            return review.interactive(conn, clear_screen=args.clear)

        if args.action == "rejected":
            rows = db.list_rejected(conn)
            if not rows:
                print("no rejected resolutions")
                return 0
            for r in rows:
                when = r["reviewed_at"].date() if r["reviewed_at"] else "?"
                print(f"#{r['id']:>4}  {r['asset_type']}:{r['asset_id']:<6} rejected {when}")
                print(f"       title: {r['asset_title'] or '(none)'}")
                print(f"       s3:    {r['s3_key']}")
                if r["notes"]:
                    print(f"       notes: {r['notes']}")
            return 0

        if args.action == "list":
            rows = db.list_pending(conn)
            if not rows:
                print("no pending resolutions")
                return 0
            for r in rows:
                book = r["proposal"].get("book", {})
                what = (
                    f"link book {book['link']}"
                    if "link" in book
                    else f"create \"{book.get('create', {}).get('title', '?')}\""
                )
                pseu = " [pseudonym]" if r["proposal"].get("pseudonyms") else ""
                print(f"#{r['id']:>4}  {r['asset_type']}:{r['asset_id']:<6} "
                      f"conf={r['confidence']:.2f}  {what}{pseu}")
                if r["notes"]:
                    print(f"       {r['notes']}")
            return 0

        r = db.get_resolution(conn, args.id)
        if r is None:
            print(f"no resolution #{args.id}")
            return 2

        if args.action == "show":
            meta = db.get_asset_meta(conn, r["asset_type"], r["asset_id"])
            print(review.render_card(
                r, meta, r["proposal"], 1, 1,
                names=review.link_names(conn, r["proposal"]),
            ))
            print(f"\n status={r['status']} method={r['method']}")
            print("\n-- proposal JSON --")
            print(json.dumps(r["proposal"], indent=2, ensure_ascii=False))
            return 0

        if r["status"] != "pending":
            print(f"resolution #{r['id']} is {r['status']}, not pending")
            return 2

        if args.action == "approve":
            result = apply.apply_proposal(
                conn, r["asset_type"], r["asset_id"], r["proposal"]
            )
            db.set_resolution_status(conn, r["id"], "approved")
            conn.commit()
            if backfill.backfill_book(conn, result["book_id"]):
                conn.commit()
            print(f"approved #{r['id']}: {result}")
            return 0

        if args.action == "reject":
            db.set_resolution_status(conn, r["id"], "rejected")
            conn.commit()
            print(f"rejected #{r['id']} (asset stays unlinked; clear the row to retry)")
            return 0

    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="resolver")
    sub = parser.add_subparsers(dest="command", required=True)

    p_resolve = sub.add_parser("resolve", help="resolve unlinked assets")
    p_resolve.add_argument("--limit", type=int, default=None)
    p_resolve.add_argument("--dry-run", action="store_true")
    p_resolve.add_argument("--asset", help="single asset, e.g. epub:123")
    p_resolve.set_defaults(func=_cmd_resolve)

    p_backfill = sub.add_parser(
        "backfill", help="fill missing publication_date/language on existing books"
    )
    p_backfill.add_argument("--limit", type=int, default=None)
    p_backfill.add_argument("--dry-run", action="store_true")
    p_backfill.set_defaults(func=_cmd_backfill)

    p_acq = sub.add_parser(
        "acquisitions",
        help="match an Amazon order export to the library and write "
             "src/sql/fixes/acquisitions.sql (never writes to the database)",
    )
    p_acq.add_argument(
        "--export", type=Path, default=acquisitions.DEFAULT_EXPORT,
        help="the unpacked Amazon 'Your Orders' export",
    )
    p_acq.add_argument("--out", type=Path, default=acquisitions.DEFAULT_OUT)
    p_acq.add_argument(
        "--dry-run", action="store_true", help="report only; write no SQL file"
    )
    p_acq.set_defaults(func=_cmd_acquisitions)

    p_audit = sub.add_parser(
        "audit", help="report suspect epub/m4b -> book associations (read-only)"
    )
    p_audit.add_argument(
        "--check",
        action="append",
        choices=list(audit.CHECKS),
        help="run only this check (repeatable); default is all",
    )
    p_audit.add_argument(
        "--no-llm", action="store_true", help="deterministic rules only, no LLM tier"
    )
    p_audit.add_argument("--json", action="store_true")
    p_audit.set_defaults(func=_cmd_audit)

    p_review = sub.add_parser(
        "review",
        help="review pending proposals (no action = interactive walk-through)",
    )
    p_review.add_argument(
        "action", choices=["list", "rejected", "show", "approve", "reject"], nargs="?"
    )
    p_review.add_argument("id", type=int, nargs="?")
    p_review.add_argument(
        "--clear",
        action="store_true",
        help="interactive mode: clear the screen so each card starts at the top",
    )
    p_review.set_defaults(func=_cmd_review)

    args = parser.parse_args(argv)
    if (
        args.command == "review"
        and args.action in ("show", "approve", "reject")
        and args.id is None
    ):
        parser.error(f"review {args.action} requires a resolution id")

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
