"""Per-asset orchestration. Library-first: `resolve_asset` is the public entry
point a future web API can call directly; the CLI is a thin wrapper."""

import logging
from dataclasses import dataclass, field
from typing import Any

import anthropic
import psycopg

from . import apply, candidates, db, llm
from .normalize import title_is_junk

log = logging.getLogger("resolver")

AUTO_CONFIDENCE = 0.9
COVER_FALLBACK_CONFIDENCE = 0.5


@dataclass
class Outcome:
    asset_type: str
    asset_id: int
    status: str  # 'auto' | 'pending' | 'skipped' | 'dry-run' | 'error'
    method: str | None = None
    confidence: float | None = None
    proposal: dict[str, Any] | None = None
    notes: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


def _add_usage(total: dict[str, int], usage: dict[str, int]) -> None:
    for k, v in usage.items():
        total[k] = total.get(k, 0) + v


def resolve_asset(
    conn: psycopg.Connection,
    asset_type: str,
    asset_id: int,
    *,
    client: anthropic.Anthropic | None = None,
    dry_run: bool = False,
) -> Outcome:
    """Resolve one raw asset: tier 1 (exact) -> tier 2 (LLM) -> apply or queue.

    Idempotent: an already-linked asset or one with an existing resolutions row
    is skipped. Returns the outcome; commits unless dry_run.
    """
    if db.is_linked(conn, asset_type, asset_id):
        return Outcome(asset_type, asset_id, "skipped", notes="already linked")
    existing = conn.execute(
        "SELECT id, status FROM resolutions WHERE asset_type = %s AND asset_id = %s",
        (asset_type, asset_id),
    ).fetchone()
    if existing is not None:
        return Outcome(
            asset_type, asset_id, "skipped",
            notes=f"resolution #{existing['id']} already {existing['status']}",
        )

    meta = db.get_asset_meta(conn, asset_type, asset_id)
    if meta is None:
        return Outcome(asset_type, asset_id, "error", notes="no such asset")

    # Tier 1: free exact match.
    proposal = candidates.exact_match(conn, meta)
    if proposal is not None:
        return _commit(
            conn, asset_type, asset_id, proposal,
            method="exact", confidence=1.0, notes=None, dry_run=dry_run,
        )

    # Tier 2: LLM adjudication.
    if client is None:
        client = anthropic.Anthropic()
    usage_total: dict[str, int] = {}
    cands = candidates.get_candidates(conn, meta)
    method = "llm"
    cover = llm.find_cover(asset_type, asset_id) if title_is_junk(meta.get("title")) else None
    if cover is not None:
        method = "llm_cover"

    try:
        adj, usage = llm.adjudicate(client, meta, cands, cover_path=cover)
        _add_usage(usage_total, usage)

        # Fallback: text metadata was insufficient and we have a cover we
        # didn't already send.
        if (
            method == "llm"
            and (adj.metadata_insufficient or adj.confidence < COVER_FALLBACK_CONFIDENCE)
        ):
            cover = llm.find_cover(asset_type, asset_id)
            if cover is not None:
                adj, usage = llm.adjudicate(client, meta, cands, cover_path=cover)
                _add_usage(usage_total, usage)
                method = "llm_cover"

        proposal = llm.to_proposal(adj)
    except anthropic.APIError:
        raise  # let the caller's loop decide whether to continue
    except Exception as exc:  # malformed adjudication -> review, not crash
        log.warning("%s:%s adjudication failed: %s", asset_type, asset_id, exc)
        return Outcome(asset_type, asset_id, "error", notes=str(exc), usage=usage_total)

    # Never auto-commit proposals that carry pseudonym links or modify an
    # existing book — both touch shared records and require a human.
    auto = (
        adj.confidence >= AUTO_CONFIDENCE
        and not adj.pseudonym_proposals
        and "update" not in proposal["book"]
    )
    outcome = _commit(
        conn, asset_type, asset_id, proposal,
        method=method, confidence=adj.confidence, notes=adj.notes,
        dry_run=dry_run, auto=auto,
    )
    outcome.usage = usage_total
    return outcome


def _commit(
    conn: psycopg.Connection,
    asset_type: str,
    asset_id: int,
    proposal: dict[str, Any],
    *,
    method: str,
    confidence: float,
    notes: str | None,
    dry_run: bool,
    auto: bool = True,
) -> Outcome:
    status = "auto" if auto else "pending"
    if dry_run:
        return Outcome(
            asset_type, asset_id, "dry-run",
            method=method, confidence=confidence, proposal=proposal,
            notes=f"would be {status}" + (f"; {notes}" if notes else ""),
        )
    if auto:
        apply.apply_proposal(conn, asset_type, asset_id, proposal)
    db.insert_resolution(
        conn, asset_type, asset_id, status, method, confidence, proposal, notes
    )
    conn.commit()
    return Outcome(
        asset_type, asset_id, status,
        method=method, confidence=confidence, proposal=proposal, notes=notes,
    )


def resolve_all(
    conn: psycopg.Connection,
    *,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Resolve every unresolved asset (epubs first, then m4bs — so audiobook
    editions tend to exact-match the book their epub already created)."""
    client = anthropic.Anthropic()
    counts: dict[str, int] = {}
    usage_total: dict[str, int] = {}
    remaining = limit

    for asset_type in ("epub", "m4b"):
        ids = db.get_unresolved(conn, asset_type, remaining)
        for asset_id in ids:
            outcome = resolve_asset(
                conn, asset_type, asset_id, client=client, dry_run=dry_run
            )
            key = f"{outcome.method or 'none'}/{outcome.status}"
            counts[key] = counts.get(key, 0) + 1
            _add_usage(usage_total, outcome.usage)
            log.info(
                "%s:%s -> %s (%s, conf=%s)%s",
                asset_type, asset_id, outcome.status, outcome.method,
                outcome.confidence, f" — {outcome.notes}" if outcome.notes else "",
            )
        if remaining is not None:
            remaining -= len(ids)
            if remaining <= 0:
                break

    return {"counts": counts, "usage": usage_total}
