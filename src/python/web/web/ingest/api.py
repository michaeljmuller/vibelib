"""Admin-only routes for adding books.

Two lists, and the routes divide along them:

  List A  files with no row yet -- /state, /scan, /upload. Slow work, done by
          the worker, so closing the page interrupts nothing that was running
          on this side of the wire.
  List B  rows with no book -- /resolve, /revise, /accept, /discard. Fast work,
          done in the request, because someone is sitting there waiting for it.

Only /accept writes to the catalog. /resolve and /revise compute a proposal and
hand it back; the browser holds it until it is accepted or abandoned. That is
what makes cancel free -- there is no route for it, because there is nothing to
undo -- and it is what removed the whole pending-review queue that used to live
here: a proposal that has not been accepted has changed nothing, so it cannot go
stale and nothing has to be cleaned up after it.
"""

import datetime
import logging

import anthropic
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from .. import auth, covers, db
from . import apply, backfill, candidates, llm, pipeline, resolve, staging, store, summary
from .worker import worker

log = logging.getLogger("uvicorn.error")

router = APIRouter(
    prefix="/api/admin/ingest",
    dependencies=[Depends(auth.require_admin)],
)


# --- request bodies ----------------------------------------------------------


class AssetRef(BaseModel):
    asset_type: str
    asset_id: int


class Revision(AssetRef):
    proposal: dict
    instruction: str


class Acceptance(AssetRef):
    proposal: dict
    acquired_on: datetime.date | None = None


def _check(ref: AssetRef) -> None:
    if ref.asset_type not in ("epub", "m4b"):
        raise HTTPException(400, "asset_type must be epub or m4b")


# --- the card ----------------------------------------------------------------


def _card(conn, asset_type: str, asset_id: int, proposal: dict, confidence, notes) -> dict:
    """Everything the browser needs to render one proposal for judgment -- and,
    since nothing is stored, everything it needs to hand back to accept it."""
    meta = store.get_asset_meta(conn, asset_type, asset_id)
    names = summary.link_names(conn, proposal)
    return {
        "asset_type": asset_type,
        "asset_id": asset_id,
        "proposal": proposal,
        "confidence": confidence,
        "notes": notes,
        "reason": summary.review_reason(proposal, confidence),
        "raw_rows": summary.raw_rows(meta) if meta else [],
        "proposal_rows": summary.proposal_rows(proposal, names),
        "cover": (
            {"type": asset_type, "id": asset_id}
            if covers.find_cover(asset_type, asset_id)
            else None
        ),
        "acquired_on": store.get_acquired_on(conn, asset_type, asset_id),
    }


# --- list A: getting files in ------------------------------------------------


@router.get("/state")
def api_state():
    """One poll drives the whole page."""
    with db.pool.connection() as conn:
        return {
            "list_a": worker.jobs(),
            "list_b": pipeline.ready_to_add(conn),
            "busy": worker.busy(),
        }


@router.post("/scan")
def api_scan():
    """Look for bucket objects with no row and queue them. Runs on page load and
    on Refresh -- not on the poll, because listing the bucket takes seconds."""
    return {"queued": worker.scan()}


@router.post("/clear-finished", status_code=204)
def api_clear_finished():
    worker.forget_finished()


@router.post("/upload", status_code=202)
def api_upload(file: UploadFile = File(...)):
    """Take the bytes, put them in staging, hand the rest to the worker.

    Returns as soon as the file is on disk rather than when it is in the bucket:
    pushing a gigabyte up takes minutes, and holding the request open for it
    would mean the browser learns nothing until the end and a proxy timeout
    loses the lot. The upload the browser *does* own -- its own bytes arriving
    here -- is the one thing it must stay on the page for.
    """
    filename = file.filename or ""
    try:
        asset_type = pipeline.asset_type_for(filename)
        s3_key = pipeline.key_for(filename)
    except pipeline.Unsupported as exc:
        raise HTTPException(415, str(exc)) from None

    # Checked here rather than in the job so a duplicate is refused while the
    # person is still looking at the page, not silently in the queue afterwards.
    with db.pool.connection() as conn:
        existing = store.find_by_key(conn, asset_type, s3_key)
    if existing is not None:
        raise HTTPException(409, f"“{s3_key}” is already in the library.")

    path = staging.reserve(filename)
    try:
        with open(path, "wb") as out:
            while chunk := file.file.read(1 << 20):
                out.write(chunk)
    except Exception:
        staging.discard(path)
        raise

    job = worker.enqueue_stage(path, filename, asset_type)
    return {"job_id": job.id}


# --- list B: turning a file into a book --------------------------------------


@router.post("/resolve")
def api_resolve(ref: AssetRef):
    """Propose how this asset maps onto the catalog. Writes nothing."""
    _check(ref)
    with db.pool.connection() as conn:
        try:
            outcome = resolve.propose(conn, ref.asset_type, ref.asset_id)
        except anthropic.APIError as exc:
            log.warning("resolve %s:%s failed: %s", ref.asset_type, ref.asset_id, exc)
            raise HTTPException(502, "the model could not be reached; try again") from None
        if outcome.proposal is None:
            raise HTTPException(422, outcome.notes or "could not resolve that asset")
        return _card(
            conn, ref.asset_type, ref.asset_id,
            outcome.proposal, outcome.confidence, outcome.notes,
        )


@router.post("/revise")
def api_revise(body: Revision):
    """Correct a proposal in plain language ("the series is Dragonriders of
    Pern, position 11") and get the revised one back. Still writes nothing."""
    _check(body)
    instruction = body.instruction.strip()
    if not instruction:
        raise HTTPException(400, "say what should change")

    with db.pool.connection() as conn:
        meta = store.get_asset_meta(conn, body.asset_type, body.asset_id)
        if meta is None:
            raise HTTPException(404, "no such asset")
        cands = candidates.get_candidates(conn, meta)
        try:
            adj, _usage = llm.revise(
                anthropic.Anthropic(), meta, cands, body.proposal, instruction
            )
            proposal = llm.to_proposal(adj)
        except anthropic.APIError:
            raise HTTPException(502, "the model could not be reached; try again") from None
        except Exception as exc:  # a malformed revision leaves the old one standing
            log.warning("revision of %s:%s failed: %s", body.asset_type, body.asset_id, exc)
            raise HTTPException(422, f"could not apply that change: {exc}") from None

        return _card(
            conn, body.asset_type, body.asset_id,
            proposal, adj.confidence, f"{adj.notes} [edited: {instruction}]",
        )


@router.post("/discard", status_code=204)
def api_discard(ref: AssetRef):
    """Forget a file we read: delete its raw row, leave the bucket alone.

    For the file that should not have been read in the first place -- the wrong
    upload, the stray object -- which otherwise sits in list B forever, since
    the list is a query and there is nothing to mark. Deleting the object is a
    back-end job on purpose: the row is something this app made and can make
    again, the object is the only copy of the file.
    """
    _check(ref)
    with db.pool.connection() as conn:
        if store.is_linked(conn, ref.asset_type, ref.asset_id):
            raise HTTPException(409, "that asset belongs to a book; it is not waiting to be added")
        if not store.delete_asset(conn, ref.asset_type, ref.asset_id):
            raise HTTPException(404, "no such asset")
        conn.commit()
    covers.discard(ref.asset_type, ref.asset_id)
    log.info("discarded the record of %s:%s", ref.asset_type, ref.asset_id)


@router.post("/accept")
def api_accept(body: Acceptance):
    """Apply the proposal. The only route here that writes to the catalog, and
    the only moment an asset becomes part of the library."""
    _check(body)
    with db.pool.connection() as conn:
        if store.is_linked(conn, body.asset_type, body.asset_id):
            raise HTTPException(409, "that asset already belongs to a book")
        try:
            result = apply.apply_proposal(
                conn, body.asset_type, body.asset_id, body.proposal
            )
        except (ValueError, KeyError) as exc:
            # The proposal names an entity that has since been deleted, or came
            # back shaped wrong.
            raise HTTPException(409, f"could not apply that proposal: {exc}") from None

        store.log_resolution(conn, body.asset_type, body.asset_id, body.proposal)
        if body.acquired_on is not None:
            store.set_acquired_on(conn, body.asset_type, body.asset_id, body.acquired_on)
        conn.commit()

        # A book created from an m4b usually has neither a publication date nor
        # a language; fill them now rather than leaving a hole in the catalog.
        try:
            if backfill.backfill_book(conn, result["book_id"]):
                conn.commit()
        except Exception as exc:  # noqa: BLE001 — the link is made; this is a garnish
            conn.rollback()
            log.warning("backfill of book %s failed: %s", result["book_id"], exc)

        log.info(
            "added %s:%s to book %s", body.asset_type, body.asset_id, result["book_id"]
        )
        return {"book_id": result["book_id"]}
