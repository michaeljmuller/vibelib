"""The worker's bookkeeping: what the page is told while a file is coming in.

No database and no object store here -- the pipeline is stubbed, so what is
under test is the part that has actually been wrong before: which phase is
showing, whether a percentage belongs to it, and what survives a failure.
"""

import pytest

from web.ingest import pipeline, worker as worker_module


@pytest.fixture
def w(monkeypatch):
    """A worker with its thread and its side effects removed."""
    monkeypatch.setattr(worker_module.staging, "sweep", lambda: None)
    return worker_module.Worker()


def test_a_new_job_starts_queued_with_no_percentage(w):
    job = w.enqueue_stage("/staging/x.epub", "x.epub", "epub")
    assert job.phase == pipeline.QUEUED
    assert job.percent is None
    assert w.busy() is True


def test_the_reporter_records_phase_and_fraction(w):
    job = w.enqueue_stage("/staging/x.m4b", "x.m4b", "m4b")
    report = w._reporter(job)

    report(pipeline.STORING, 0.0)
    assert (job.phase, job.percent) == (pipeline.STORING, 0.0)

    report(pipeline.STORING, 0.42)
    assert job.percent == 0.42

    # Reading has no number, and the stale one must not linger beside it.
    report(pipeline.READING)
    assert (job.phase, job.percent) == (pipeline.READING, None)


def test_only_transfers_claim_a_percentage():
    assert set(pipeline.WITH_PROGRESS) == {
        pipeline.UPLOADING, pipeline.DOWNLOADING, pipeline.STORING
    }
    for quiet in (pipeline.QUEUED, pipeline.READING, pipeline.DONE, pipeline.FAILED):
        assert quiet not in pipeline.WITH_PROGRESS


def test_a_key_is_only_claimed_once(w):
    assert w.enqueue_fetch("Book.epub") is not None
    assert w.enqueue_fetch("Book.epub") is None  # a rescan must not double it


def test_a_key_that_is_not_a_book_is_ignored(w):
    assert w.enqueue_fetch("cover-art.jpg") is None


def test_busy_is_false_once_everything_has_settled(w):
    job = w.enqueue_stage("/staging/x.epub", "x.epub", "epub")
    w._set(job, phase=pipeline.DONE)
    assert w.busy() is False


def test_clearing_a_failed_fetch_releases_it_for_another_try(w):
    job = w.enqueue_fetch("Broken.m4b")
    w._set(job, phase=pipeline.FAILED, error="not an MP4 file")

    # Still claimed while the failure is on screen: re-fetching a gigabyte to
    # fail at it again is not something to do on a page load.
    assert w.enqueue_fetch("Broken.m4b") is None

    w.forget_finished()
    assert w.jobs() == []
    assert w.enqueue_fetch("Broken.m4b") is not None


def test_clearing_keeps_a_successful_fetch_claimed(w):
    job = w.enqueue_fetch("Fine.epub")
    w._set(job, phase=pipeline.DONE)
    w.forget_finished()
    # It has a row now; re-fetching would only rediscover that.
    assert w.enqueue_fetch("Fine.epub") is None


def test_opening_the_page_drops_finished_jobs_and_keeps_failures(w):
    done = w.enqueue_fetch("Fine.epub")
    failed = w.enqueue_fetch("Broken.m4b")
    running = w.enqueue_stage("/staging/x.epub", "x.epub", "epub")
    w._set(done, phase=pipeline.DONE)
    w._set(failed, phase=pipeline.FAILED, error="not an MP4 file")

    w.forget_done()

    assert [j["id"] for j in w.jobs()] == [failed.id, running.id]


def test_discarding_a_record_lets_the_bucket_check_find_it_again(w):
    job = w.enqueue_fetch("Wrong.epub")
    w._set(job, phase=pipeline.DONE)
    # Claimed, and rightly so while the row it made exists.
    assert w.enqueue_fetch("Wrong.epub") is None

    w.release("Wrong.epub")  # what /discard does once the row is gone

    # The object is still in the bucket with no row, which is work again.
    assert w.enqueue_fetch("Wrong.epub") is not None


def test_a_failure_survives_a_page_load_still_claimed(w):
    job = w.enqueue_fetch("Broken.m4b")
    w._set(job, phase=pipeline.FAILED, error="not an MP4 file")

    w.forget_done()

    # The whole point of the speed bump: opening a page must not re-download a
    # file that has already proved unreadable.
    assert w.enqueue_fetch("Broken.m4b") is None
