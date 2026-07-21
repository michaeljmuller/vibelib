"""Interactive review: walk the pending queue item-by-item with
accept / edit / skip / reject / quit. `render_card` is a pure function so the
presentation is unit-testable."""

import logging
from typing import Any

import anthropic
import psycopg

from . import apply, backfill, candidates, db, llm
from .pipeline import AUTO_CONFIDENCE

log = logging.getLogger("resolver")

WIDTH = 72


def _named(kind: str, entity_id: int, names: dict[str, dict[int, str]]) -> str:
    name = names.get(kind, {}).get(entity_id)
    return f"#{entity_id} “{name}”" if name else f"#{entity_id}"


def _fmt_person(action: dict[str, Any], names: dict[str, dict[int, str]]) -> str:
    if "link" in action:
        name = names.get("people", {}).get(action["link"])
        who = f"#{action['link']} {name}" if name else f"#{action['link']}"
        return f"LINK    {who}  (raw: {action.get('raw_name', '?')})"
    spec = action["create"]
    out = f"CREATE  {spec['name']}"
    if spec.get("disambiguator"):
        out += f"  ({spec['disambiguator']})"
    return out


def _series_lines(
    series: dict[str, Any], names: dict[str, Any], label: str = " series   "
) -> list[str]:
    """Render a series ref the way `apply` will actually execute it.

    A `create` whose name already exists is silently linked by
    `apply._resolve_series`, so showing it as CREATE misleads the reviewer.
    A create with no exact match but a close one gets a warning, because that
    is precisely how a series gets split in two (Longmire / Walt Longmire).
    """
    if "link" in series:
        return [f"{label} LINK    existing series {_named('series', series['link'], names)}"]

    name = series["create"]["name"]
    match = names.get("series_match", {}).get(name)
    if match:
        return [
            f"{label} LINK    existing series #{match['id']} “{match['name']}”"
            "  (matched by name)"
        ]

    lines = [f"{label} CREATE  “{name}”"]
    near = names.get("series_near", {}).get(name) or []
    if near:
        hits = ", ".join(f"#{n['id']} “{n['name']}”" for n in near)
        lines.append(f"           ⚠ near-match: {hits} — edit to link instead?")
    return lines


def _proposal_lines(
    proposal: dict[str, Any], names: dict[str, dict[int, str]]
) -> list[str]:
    lines: list[str] = []
    book = proposal.get("book", {})
    if "link" in book:
        lines.append(f" book      LINK    existing book {_named('books', book['link'], names)}")
        upd = book.get("update")
        if upd:
            if "title" in upd:
                lines.append(f" update    title → “{upd['title']}”")
            if "series" in upd:
                lines += _series_lines(upd["series"], names, label=" update   ")
            if "series_position" in upd:
                lines.append(f" update    position → {upd['series_position']}")
    else:
        spec = book.get("create", {})
        lines.append(f" book      CREATE  “{spec.get('title', '?')}”")
        series = spec.get("series")
        if series is not None:
            lines += _series_lines(series, names)
            pos = spec.get("series_position")
            lines.append(
                f" position  {pos}" if pos is not None else " position  none (interstitial)"
            )
        lines.append(f" published {spec.get('publication_date') or '?'}")
        lines.append(f" language  {spec.get('language') or '?'}")
    for a in proposal.get("authors", []):
        lines.append(f" author    {_fmt_person(a, names)}")
    for n in proposal.get("narrators", []):
        lines.append(f" narrator  {_fmt_person(n, names)}")
    for p in proposal.get("pseudonyms", []):
        reals = " + ".join(p.get("real_person_names", []))
        lines.append(f" pseudonym {p.get('pseudonym_name', '?')} → {reals}")
    return lines


def _raw_lines(meta: dict[str, Any]) -> list[str]:
    lines = [f" raw title    {meta.get('title') or '(none)'}"]
    authors = meta.get("authors") or []
    lines.append(f" raw authors  {'; '.join(authors) if authors else '(none)'}")
    if meta.get("asset_type") == "epub":
        series = meta.get("series") or "(none)"
        pos = meta.get("series_position")
        series_str = f"{series} #{pos}" if pos is not None else series
        lines.append(f" raw series   {series_str}")
        if meta.get("published_date"):
            lines.append(f" raw date     {meta['published_date']} (edition)")
        if meta.get("language"):
            lines.append(f" raw language {meta['language']}")
    else:
        if meta.get("narrators"):
            lines.append(f" raw narrator {'; '.join(meta['narrators'])}")
        if meta.get("album"):
            lines.append(f" raw album    {meta['album']}")
        if meta.get("date"):
            lines.append(f" raw date     {meta['date']} (edition)")
    return lines


def review_reason(proposal: dict[str, Any], confidence: float | None) -> str:
    if proposal.get("pseudonyms"):
        return "pseudonym link proposed — never auto-committed"
    if proposal.get("book", {}).get("update"):
        return "modifies an existing book — never auto-committed"
    if confidence is not None:
        return f"confidence {confidence:.2f} below {AUTO_CONFIDENCE:.2f}"
    return "unknown"


def _series_near(conn: psycopg.Connection, name: str) -> list[dict[str, Any]]:
    """Existing series close enough to `name` that creating it would probably
    be a split rather than a genuinely new series. Advisory only."""
    rows = conn.execute(
        """SELECT id, name FROM series
           WHERE similarity(lower(name), lower(%(n)s)) > %(th)s
              OR position(lower(name) IN lower(%(n)s)) > 0
              OR position(lower(%(n)s) IN lower(name)) > 0
           ORDER BY similarity(lower(name), lower(%(n)s)) DESC
           LIMIT 3""",
        {"n": name, "th": candidates.NAME_SIM_THRESHOLD},
    ).fetchall()
    return [dict(r) for r in rows]


def link_names(
    conn: psycopg.Connection, proposal: dict[str, Any]
) -> dict[str, Any]:
    """Look up display names for every LINK action in a proposal, so the card
    can show what an id points at — plus, for every proposed series CREATE,
    what `apply` will really do with it (link to an existing row, or split)."""
    book = proposal.get("book", {})
    book_ids = [book["link"]] if "link" in book else []
    series_ids = []
    series_creates: list[str] = []
    for src in ((book.get("create") or {}).get("series"), (book.get("update") or {}).get("series")):
        if not src:
            continue
        if "link" in src:
            series_ids.append(src["link"])
        else:
            series_creates.append(src["create"]["name"])
    people_ids = [
        a["link"]
        for a in proposal.get("authors", []) + proposal.get("narrators", [])
        if "link" in a
    ]

    names: dict[str, Any] = {
        "books": {}, "series": {}, "people": {},
        "series_match": {}, "series_near": {},
    }
    for name in series_creates:
        # Same call apply._resolve_series makes, so the card cannot disagree
        # with what approving it actually does.
        existing = candidates.find_series_exact(conn, name)
        if existing is not None:
            names["series_match"][name] = dict(existing)
        else:
            near = _series_near(conn, name)
            if near:
                names["series_near"][name] = near
    if book_ids:
        for r in conn.execute(
            "SELECT id, title FROM books WHERE id = ANY(%s)", (book_ids,)
        ).fetchall():
            names["books"][r["id"]] = r["title"]
    if series_ids:
        for r in conn.execute(
            "SELECT id, name FROM series WHERE id = ANY(%s)", (series_ids,)
        ).fetchall():
            names["series"][r["id"]] = r["name"]
    if people_ids:
        for r in conn.execute(
            "SELECT id, name FROM people WHERE id = ANY(%s)", (people_ids,)
        ).fetchall():
            names["people"][r["id"]] = r["name"]
    return names


def render_card(
    resolution: dict[str, Any],
    meta: dict[str, Any],
    proposal: dict[str, Any],
    position: int,
    total: int,
    *,
    edited: bool = False,
    names: dict[str, dict[int, str]] | None = None,
) -> str:
    rid = resolution["id"]
    head = f"── #{rid} · {resolution['asset_type']}:{resolution['asset_id']} "
    tail = f" {position} of {total} ──"
    bar = head + "─" * max(1, WIDTH - len(head) - len(tail)) + tail

    conf = resolution.get("confidence")
    conf_str = f"confidence {conf:.2f}" if conf is not None else "confidence ?"
    if edited:
        conf_str += " · EDITED"

    lines = [bar]
    lines += _raw_lines(meta)
    lines.append("")
    lines.append(f" PROPOSAL ({conf_str})")
    lines += _proposal_lines(proposal, names or {})
    lines.append("")
    status = resolution.get("status", "pending")
    if status == "pending":
        lines.append(f" needs review: {review_reason(proposal, conf)}")
    else:
        lines.append(f" status: {status}")
    if resolution.get("notes"):
        lines.append(f" model notes:  {resolution['notes']}")
    return "\n".join(lines)


def _read_choice() -> str:
    try:
        raw = input("\n [a]ccept  [e]dit  [s]kip  [r]eject  [q]uit → ").strip().lower()
    except EOFError:
        return "q"
    return raw[:1]


def interactive(conn: psycopg.Connection, *, clear_screen: bool = False) -> int:
    pending = db.list_pending(conn)
    if not pending:
        print("no pending resolutions")
        return 0

    client: anthropic.Anthropic | None = None
    total = len(pending)
    stats = {"approved": 0, "rejected": 0, "skipped": 0}
    flash: str | None = None  # outcome of the previous action, shown atop the next card

    for i, r in enumerate(pending, start=1):
        r = dict(r)  # local copy — notes get replaced by revisions
        meta = db.get_asset_meta(conn, r["asset_type"], r["asset_id"])
        if meta is None:
            print(f"#{r['id']}: asset {r['asset_type']}:{r['asset_id']} missing, skipping")
            continue
        proposal = r["proposal"]
        edits: list[str] = []

        while True:
            if clear_screen:
                print("\033[2J\033[H", end="")
                if flash:
                    print(flash)
                flash = None
            print()
            print(
                render_card(
                    r, meta, proposal, i, total,
                    edited=bool(edits), names=link_names(conn, proposal),
                )
            )
            choice = _read_choice()

            if choice == "a":
                if edits:
                    notes = (r.get("notes") or "") + f" [edited: {'; '.join(edits)}]"
                    db.update_resolution_proposal(conn, r["id"], proposal, notes)
                result = apply.apply_proposal(
                    conn, r["asset_type"], r["asset_id"], proposal
                )
                db.set_resolution_status(conn, r["id"], "approved")
                conn.commit()
                # Proposals written before the publication_date/language fields
                # existed create books without them — fill in the gap now.
                if backfill.backfill_book(conn, result["book_id"], client=client):
                    conn.commit()
                    print("   (filled missing publication date/language)")
                stats["approved"] += 1
                flash = f" ✓ approved #{r['id']}"
                print(flash)
                break

            if choice == "e":
                try:
                    instruction = input(" what should change? → ").strip()
                except EOFError:
                    instruction = ""
                if not instruction:
                    continue
                if client is None:
                    client = anthropic.Anthropic()
                cands = candidates.get_candidates(conn, meta)
                try:
                    adj, _usage = llm.revise(client, meta, cands, proposal, instruction)
                    proposal = llm.to_proposal(adj)
                except Exception as exc:
                    print(f" ! revision failed: {exc}")
                    continue
                r["notes"] = adj.notes  # show the revision's rationale, not the stale one
                edits.append(instruction)
                continue  # re-render the revised card

            if choice == "s":
                stats["skipped"] += 1
                flash = f" → skipped #{r['id']} (still pending)"
                break  # leave pending; discard any un-accepted edits

            if choice == "r":
                db.set_resolution_status(conn, r["id"], "rejected")
                conn.commit()
                stats["rejected"] += 1
                flash = f" ✗ rejected #{r['id']}"
                print(flash)
                break

            if choice == "q":
                left = total - i + 1  # the current item stays pending too
                print(
                    f"\ndone: {stats['approved']} approved, {stats['rejected']} rejected, "
                    f"{stats['skipped']} skipped, {left} left pending"
                )
                return 0

            # anything else: re-prompt
    print(
        f"\ndone: {stats['approved']} approved, {stats['rejected']} rejected, "
        f"{stats['skipped']} skipped"
    )
    return 0
