"""The background queue that turns files into rows — list A, and the thing that
does its work.

Why a thread and not a request: every step here is slow in a way a browser tab
should not be responsible for. Fetching an audiobook out of the object store is
a gigabyte over someone else's network; pushing one up is the same in reverse.
Owned by a request, that work dies when the page is closed, and the person who
closed it has no way of knowing what they interrupted. Owned by the app, it
finishes, and the page is free to be just a view of it.

Why one thread: the work is serial anyway (one temp file, one transfer at a
time is kinder to both ends), and it makes the queue trivially safe without a
lock around the actual ingestion.

Nothing here is persisted. It does not need to be: list A is the difference
between what is in the bucket and what is in the tables, both durable, so after
a restart the scan recomputes exactly the work that is left. Job records are
progress display and nothing more -- losing them loses a progress bar, never a
file and never a row.

The app is synchronous throughout (`def` endpoints over a sync psycopg pool), so
a thread fits the grain; there is no event loop here to schedule onto.
"""

import itertools
import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any

from .. import db
from . import pipeline, staging
from .pipeline import DONE, FAILED, QUEUED

log = logging.getLogger("uvicorn.error")

@dataclass
class Job:
    id: int
    kind: str  # 'fetch' | 'stage'
    label: str
    asset_type: str | None = None
    phase: str = QUEUED
    percent: float | None = None
    error: str | None = None
    # Set once the rows exist, so the UI can say which list B entry this became.
    asset_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "asset_type": self.asset_type,
            "phase": self.phase,
            "percent": self.percent,
            "error": self.error,
            "asset_id": self.asset_id,
        }


class Worker:
    """One thread, one queue, and a dict of what it is doing for the UI."""

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self._jobs: dict[int, Job] = {}
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        # Keys already queued or done, so a rescan does not enqueue them twice.
        self._claimed: set[str] = set()

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        staging.sweep()  # temp files from a run that did not get to finish
        self._thread = threading.Thread(target=self._run, name="ingest", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._q.put(None)

    # --- what the UI reads --------------------------------------------------

    def jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [j.as_dict() for j in self._jobs.values()]

    def busy(self) -> bool:
        with self._lock:
            return any(j.phase not in (DONE, FAILED) for j in self._jobs.values())

    def forget_finished(self) -> None:
        """Drop done/failed jobs from the display. The rows a finished job made
        are in list B, and its failures have been read by now or never will be.

        Dropping a *failed* fetch also releases its key, which is the only way
        one gets retried. Deliberately not automatic: a file that cannot be read
        would otherwise be re-downloaded on every page load, and re-fetching a
        gigabyte to fail at it again is not a thing to do by accident.
        """
        with self._lock:
            for job_id, job in list(self._jobs.items()):
                if job.phase not in (DONE, FAILED):
                    continue
                if job.kind == "fetch" and job.phase == FAILED:
                    self._claimed.discard(job.label)
                del self._jobs[job_id]

    # --- putting work in ----------------------------------------------------

    def _new_job(self, kind: str, label: str, asset_type: str | None) -> Job:
        job = Job(id=next(self._ids), kind=kind, label=label, asset_type=asset_type)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def enqueue_fetch(self, s3_key: str) -> Job | None:
        """An object in the bucket with no row. Returns None if already claimed."""
        with self._lock:
            if s3_key in self._claimed:
                return None
            self._claimed.add(s3_key)
        try:
            asset_type = pipeline.asset_type_for(s3_key)
        except pipeline.Unsupported:
            return None
        job = self._new_job("fetch", s3_key, asset_type)
        self._q.put((job, {"s3_key": s3_key}))
        return job

    def enqueue_stage(self, path: str, filename: str, asset_type: str) -> Job:
        """A file the upload route has already put on local disk."""
        job = self._new_job("stage", filename, asset_type)
        self._q.put((job, {"path": path, "filename": filename}))
        return job

    def scan(self) -> int:
        """Enqueue every bucket object that has no row. Idempotent -- anything
        already queued, running or finished this process is skipped."""
        with db.pool.connection() as conn:
            keys = pipeline.unrecorded_keys(conn)
        return sum(1 for key in keys if self.enqueue_fetch(key) is not None)

    # --- doing it -----------------------------------------------------------

    def _set(self, job: Job, **changes) -> None:
        with self._lock:
            for k, v in changes.items():
                setattr(job, k, v)

    def _reporter(self, job: Job):
        """Handed to the pipeline, which knows the order of its own steps. The
        worker does not infer phases -- it is told them, so the two cannot drift."""
        def report(phase: str, fraction: float | None = None) -> None:
            self._set(job, phase=phase, percent=fraction)
        return report

    def _run(self) -> None:
        log.info("ingest worker started")
        while not self._stop.is_set():
            item = self._q.get()
            if item is None:
                break
            job, args = item
            try:
                self._do(job, args)
            except Exception as exc:  # noqa: BLE001 — one bad file must not stop the queue
                log.warning("ingest job %s (%s) failed: %s", job.id, job.label, exc)
                self._set(job, phase=FAILED, percent=None, error=str(exc))
            finally:
                self._q.task_done()
        log.info("ingest worker stopped")

    def _do(self, job: Job, args: dict) -> None:
        with db.pool.connection() as conn:
            report = self._reporter(job)
            if job.kind == "fetch":
                path = staging.reserve(args["s3_key"])
                try:
                    result = pipeline.fetch_and_record(conn, args["s3_key"], path, report)
                finally:
                    staging.discard(path)
            else:
                path = args["path"]
                try:
                    result = pipeline.store_uploaded(conn, path, args["filename"], report)
                finally:
                    staging.discard(path)

        self._set(
            job, phase=DONE, percent=None, asset_id=result.asset_id,
            label=result.title or result.s3_key,
        )


worker = Worker()
