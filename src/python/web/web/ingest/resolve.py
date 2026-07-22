"""Per-asset orchestration: map one raw asset onto the abstract catalog.

Tier 1 is a free deterministic exact match; tier 2 is one structured Claude call
against short candidate lists. Either way the decision is written to
`resolutions`, which is both the audit log and the review queue.

This module writes nothing at all -- not to the catalog, not even a record that
it ran. It computes a proposal and hands it back; api.accept is the only thing
that applies one, and it runs because a person clicked. That is the difference
from the CLI this came from, which committed anything at confidence >= 0.9
unattended and then had no way to undo it.

It also means a proposal cannot go stale in storage waiting for a decision,
because it is not in storage. An admin who just added a file is standing right
there: a certain match costs them one click, and an abandoned one costs nothing
and leaves nothing behind.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import anthropic
import psycopg

from .. import covers
from . import candidates, llm, store
from .normalize import title_is_junk

log = logging.getLogger("uvicorn.error")

# Where the reviewer's attention is worth spending. Purely advisory: it changes
# what the card says, never what happens. (It used to be the auto-commit
# threshold, which is why the model's own confidence is scaled around it.)
CLOSE_LOOK_CONFIDENCE = 0.9

# This one is real behaviour, and the only threshold left that is: an
# adjudication this unsure gets a second attempt with the asset's cover image
# attached, which is what rescues a file whose title is a filename.
COVER_FALLBACK_CONFIDENCE = 0.5


@dataclass
class Outcome:
    """A proposal and how much to trust it. `proposal is None` means it could
    not be made -- the asset is gone, or the model returned nothing usable."""

    asset_type: str
    asset_id: int
    proposal: dict[str, Any] | None = None
    method: str | None = None
    confidence: float | None = None
    notes: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


def _add_usage(total: dict[str, int], usage: dict[str, int]) -> None:
    for k, v in usage.items():
        total[k] = total.get(k, 0) + v


def propose(
    conn: psycopg.Connection,
    asset_type: str,
    asset_id: int,
    *,
    client: anthropic.Anthropic | None = None,
) -> Outcome:
    """Work out how one raw asset maps onto the catalog: tier 1 (free, exact)
    then tier 2 (one structured Claude call). Returns the proposal; writes
    nothing, so calling it twice costs a second call and nothing else."""
    meta = store.get_asset_meta(conn, asset_type, asset_id)
    if meta is None:
        return Outcome(asset_type, asset_id, notes="no such asset")

    # Tier 1: free exact match.
    proposal = candidates.exact_match(conn, meta)
    if proposal is not None:
        return Outcome(
            asset_type, asset_id, proposal=proposal, method="exact", confidence=1.0
        )

    # Tier 2: LLM adjudication.
    if client is None:
        client = anthropic.Anthropic()
    usage_total: dict[str, int] = {}
    cands = candidates.get_candidates(conn, meta)
    method = "llm"
    cover = (
        covers.find_cover(asset_type, asset_id)
        if title_is_junk(meta.get("title"))
        else None
    )
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
            cover = covers.find_cover(asset_type, asset_id)
            if cover is not None:
                adj, usage = llm.adjudicate(client, meta, cands, cover_path=cover)
                _add_usage(usage_total, usage)
                method = "llm_cover"

        proposal = llm.to_proposal(adj)
    except anthropic.APIError:
        raise  # the caller turns this into a 502; it is worth retrying
    except Exception as exc:  # malformed adjudication -> say so, do not crash
        log.warning("%s:%s adjudication failed: %s", asset_type, asset_id, exc)
        return Outcome(asset_type, asset_id, notes=str(exc), usage=usage_total)

    log.info(
        "proposed %s:%s (%s, conf=%.2f)", asset_type, asset_id, method, adj.confidence
    )
    return Outcome(
        asset_type, asset_id, proposal=proposal, method=method,
        confidence=adj.confidence, notes=adj.notes, usage=usage_total,
    )
