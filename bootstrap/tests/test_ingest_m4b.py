"""
Unit tests for bootstrap/ingest_m4b.py.
DB and S3 calls are mocked; no real Postgres or S3 needed.
"""

import datetime
from unittest.mock import MagicMock, patch

import pytest

from ingest_m4b import process_m4b
from run_context import RunContext

TS = datetime.datetime(2024, 1, 1)

M4B_META = {
    'title': 'Great Audiobook',
    'authors': ['John Smith'],
    'year': 2021,
    'duration_seconds': 36000,
}


def _make_ctx(**overrides):
    kwargs = dict(
        config={
            's3_bucket': 'test-bucket',
            'cache_dir': '/tmp/cache',
            'fuzzy_match_threshold': 85,
        },
        book_records=[],
        author_records=[],
    )
    kwargs.update(overrides)
    return RunContext(**kwargs)


def _make_conn():
    return MagicMock()


# ---------------------------------------------------------------------------
# AC#1 — M4B matching an existing EPUB book → one books record, two file records
# ---------------------------------------------------------------------------

@patch('ingest_m4b.record_progress')
@patch('ingest_m4b.insert_audiobook_file')
@patch('ingest_m4b.insert_book_author')
@patch('ingest_m4b.find_or_create_author', return_value=5)
@patch('ingest_m4b.find_matching_book_candidates',
       return_value=[(7, 'Great Audiobook', ['John Smith'], 100)])
@patch('ingest_m4b._extract_m4b_metadata', return_value=M4B_META)
@patch('ingest_m4b.get_cached_file', return_value='/tmp/audio.m4b')
@patch('ingest_m4b.check_already_processed', return_value=False)
def test_matches_existing_epub_book(
    mock_cap, mock_gcf, mock_meta, mock_fmbc,
    mock_foca, mock_iba, mock_iaf, mock_rp,
):
    conn = _make_conn()
    ctx = _make_ctx(book_records=[(7, 'Great Audiobook', ['John Smith'])])

    process_m4b(conn, ctx, 'audio.m4b', 50_000_000, TS, 'etag1')

    mock_iaf.assert_called_once_with(conn, 7, 'audio.m4b', 36000, 50_000_000)
    mock_rp.assert_called_once_with(conn, 'audio.m4b', 'matched', 7)
    assert ctx.matched == 1
    assert ctx.created == 0


# ---------------------------------------------------------------------------
# AC#2 — M4B with no existing match creates a new books record
# ---------------------------------------------------------------------------

@patch('ingest_m4b.record_progress')
@patch('ingest_m4b.insert_audiobook_file')
@patch('ingest_m4b.insert_book_author')
@patch('ingest_m4b.find_or_create_author', return_value=5)
@patch('ingest_m4b.find_matching_book_candidates', return_value=[])
@patch('ingest_m4b.create_book', return_value=20)
@patch('ingest_m4b._extract_m4b_metadata', return_value=M4B_META)
@patch('ingest_m4b.get_cached_file', return_value='/tmp/audio.m4b')
@patch('ingest_m4b.check_already_processed', return_value=False)
def test_creates_new_book_when_no_match(
    mock_cap, mock_gcf, mock_meta, mock_cb,
    mock_fmbc, mock_foca, mock_iba, mock_iaf, mock_rp,
):
    conn = _make_conn()
    ctx = _make_ctx()

    process_m4b(conn, ctx, 'audio.m4b', 50_000_000, TS, 'etag1')

    mock_cb.assert_called_once()
    mock_iaf.assert_called_once_with(conn, 20, 'audio.m4b', 36000, 50_000_000)
    mock_rp.assert_called_once_with(conn, 'audio.m4b', 'created', 20)
    assert ctx.created == 1


# ---------------------------------------------------------------------------
# AC#3 — duration_seconds is populated from mutagen stream info
# ---------------------------------------------------------------------------

@patch('ingest_m4b.record_progress')
@patch('ingest_m4b.insert_audiobook_file')
@patch('ingest_m4b.insert_book_author')
@patch('ingest_m4b.find_or_create_author', return_value=1)
@patch('ingest_m4b.find_matching_book_candidates', return_value=[])
@patch('ingest_m4b.create_book', return_value=1)
@patch('ingest_m4b._extract_m4b_metadata',
       return_value={**M4B_META, 'duration_seconds': 12345})
@patch('ingest_m4b.get_cached_file', return_value='/tmp/audio.m4b')
@patch('ingest_m4b.check_already_processed', return_value=False)
def test_duration_seconds_populated(
    mock_cap, mock_gcf, mock_meta, mock_cb, mock_fmbc,
    mock_foca, mock_iba, mock_iaf, mock_rp,
):
    conn = _make_conn()
    ctx = _make_ctx()

    process_m4b(conn, ctx, 'audio.m4b', 50_000_000, TS, 'etag1')

    mock_iaf.assert_called_once_with(conn, 1, 'audio.m4b', 12345, 50_000_000)


# ---------------------------------------------------------------------------
# AC#4 — file_size_bytes is from S3 object metadata (the size parameter)
# ---------------------------------------------------------------------------

@patch('ingest_m4b.record_progress')
@patch('ingest_m4b.insert_audiobook_file')
@patch('ingest_m4b.insert_book_author')
@patch('ingest_m4b.find_or_create_author', return_value=1)
@patch('ingest_m4b.find_matching_book_candidates', return_value=[])
@patch('ingest_m4b.create_book', return_value=1)
@patch('ingest_m4b._extract_m4b_metadata', return_value=M4B_META)
@patch('ingest_m4b.get_cached_file', return_value='/tmp/audio.m4b')
@patch('ingest_m4b.check_already_processed', return_value=False)
def test_file_size_from_s3_metadata(
    mock_cap, mock_gcf, mock_meta, mock_cb, mock_fmbc,
    mock_foca, mock_iba, mock_iaf, mock_rp,
):
    conn = _make_conn()
    ctx = _make_ctx()

    process_m4b(conn, ctx, 'audio.m4b', 99999, TS, 'etag1')

    # file_size_bytes must be the S3-provided size, not derived from the local file
    assert mock_iaf.call_args[0][4] == 99999


# ---------------------------------------------------------------------------
# AC#5 — Re-processing the same M4B creates no new rows
# ---------------------------------------------------------------------------

@patch('ingest_m4b.check_already_processed', return_value=True)
def test_idempotency_skips_already_processed(mock_cap):
    conn = _make_conn()
    ctx = _make_ctx()

    process_m4b(conn, ctx, 'audio.m4b', 50_000_000, TS, 'etag1')

    assert ctx.skipped == 1
    assert ctx.created == 0
    assert ctx.matched == 0


# ---------------------------------------------------------------------------
# AC#6 — No author → no_metadata issue; no title → silent filename fallback
# ---------------------------------------------------------------------------

@patch('ingest_m4b.record_progress')
@patch('ingest_m4b.insert_audiobook_file')
@patch('ingest_m4b.record_issue')
@patch('ingest_m4b.find_matching_book_candidates', return_value=[])
@patch('ingest_m4b.create_book', return_value=1)
@patch('ingest_m4b._extract_m4b_metadata',
       return_value={**M4B_META, 'authors': []})
@patch('ingest_m4b.get_cached_file', return_value='/tmp/audio.m4b')
@patch('ingest_m4b.check_already_processed', return_value=False)
def test_no_author_records_no_metadata_issue(
    mock_cap, mock_gcf, mock_meta, mock_cb, mock_fmbc, mock_ri, mock_iaf, mock_rp,
):
    conn = _make_conn()
    ctx = _make_ctx()

    process_m4b(conn, ctx, 'audio.m4b', 50_000_000, TS, 'etag1')

    issue_categories = [c[0][3] for c in mock_ri.call_args_list]
    assert 'no_metadata' in issue_categories


@patch('ingest_m4b.record_progress')
@patch('ingest_m4b.insert_audiobook_file')
@patch('ingest_m4b.insert_book_author')
@patch('ingest_m4b.find_or_create_author', return_value=1)
@patch('ingest_m4b.record_issue')
@patch('ingest_m4b.find_matching_book_candidates', return_value=[])
@patch('ingest_m4b.create_book', return_value=1)
@patch('ingest_m4b._extract_m4b_metadata',
       return_value={**M4B_META, 'title': None})
@patch('ingest_m4b.get_cached_file', return_value='/tmp/audio.m4b')
@patch('ingest_m4b.check_already_processed', return_value=False)
def test_no_title_uses_filename_silently_no_issue(
    mock_cap, mock_gcf, mock_meta, mock_cb, mock_fmbc, mock_ri,
    mock_foca, mock_iba, mock_iaf, mock_rp,
):
    conn = _make_conn()
    ctx = _make_ctx()

    process_m4b(conn, ctx, 'Great Story.m4b', 50_000_000, TS, 'etag1')

    # create_book should receive the filename-derived title
    title_arg = mock_cb.call_args[0][1]
    assert title_arg == 'Great Story'

    # No no_metadata issue should be recorded (M4B missing title is silent)
    issue_categories = [c[0][3] for c in mock_ri.call_args_list]
    assert 'no_metadata' not in issue_categories


# ---------------------------------------------------------------------------
# Match conflict — records issue and creates a new book
# ---------------------------------------------------------------------------

@patch('ingest_m4b.record_progress')
@patch('ingest_m4b.insert_audiobook_file')
@patch('ingest_m4b.insert_book_author')
@patch('ingest_m4b.find_or_create_author', return_value=1)
@patch('ingest_m4b.record_issue')
@patch('ingest_m4b.find_matching_book_candidates', return_value=[
    (1, 'Great Audiobook', ['John Smith'], 92),
    (2, 'Great Audiobooks', ['John Smith'], 88),
])
@patch('ingest_m4b.create_book', return_value=99)
@patch('ingest_m4b._extract_m4b_metadata', return_value=M4B_META)
@patch('ingest_m4b.get_cached_file', return_value='/tmp/audio.m4b')
@patch('ingest_m4b.check_already_processed', return_value=False)
def test_match_conflict_records_issue_and_creates_new_book(
    mock_cap, mock_gcf, mock_meta, mock_cb, mock_fmbc, mock_ri,
    mock_foca, mock_iba, mock_iaf, mock_rp,
):
    conn = _make_conn()
    ctx = _make_ctx()

    process_m4b(conn, ctx, 'audio.m4b', 50_000_000, TS, 'etag1')

    issue_categories = [c[0][3] for c in mock_ri.call_args_list]
    assert 'match_conflict' in issue_categories
    mock_cb.assert_called_once()
    mock_rp.assert_called_once_with(conn, 'audio.m4b', 'created', 99)
