"""
Unit tests for bootstrap/ingest_epub.py.
DB and S3 calls are mocked; no real Postgres or S3 needed.
"""

import datetime
from unittest.mock import MagicMock, call, patch

import pytest

from ingest_epub import process_epub
from run_context import RunContext

TS = datetime.datetime(2024, 1, 1)

EPUB_META = {
    'title': 'The Great Novel',
    'authors': ['Jane Doe'],
    'subjects': ['Fiction', 'Adventure'],
    'language': 'en',
    'isbn': None,
    'date': '2020',
    'identifiers': {'asin': 'B01234567X'},
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
    conn = MagicMock()
    # MagicMock.__exit__ returns False by default (exceptions propagate)
    return conn


# ---------------------------------------------------------------------------
# AC#1 — Processing an EPUB creates correct rows
# ---------------------------------------------------------------------------

@patch('ingest_epub.record_progress')
@patch('ingest_epub.insert_ebook_file')
@patch('ingest_epub.insert_book_tag')
@patch('ingest_epub.insert_book_author')
@patch('ingest_epub.find_or_create_author', return_value=99)
@patch('ingest_epub.update_book_asin')
@patch('ingest_epub.find_matching_book_candidates', return_value=[])
@patch('ingest_epub.create_book', return_value=42)
@patch('ingest_epub.extract_epub_metadata', return_value=EPUB_META)
@patch('ingest_epub.get_cached_file', return_value='/tmp/book.epub')
@patch('ingest_epub.check_already_processed', return_value=False)
def test_creates_new_book(
    mock_cap, mock_gcf, mock_meta, mock_cb, mock_fmbc,
    mock_uba, mock_foca, mock_iba, mock_ibt, mock_ief, mock_rp,
):
    conn = _make_conn()
    ctx = _make_ctx()

    process_epub(conn, ctx, 'book.epub', 1000, TS, 'etag1')

    mock_cb.assert_called_once()
    mock_foca.assert_called_once_with(conn, ctx, 'Jane Doe')
    mock_iba.assert_called_once_with(conn, 42, 99, 1)
    mock_ibt.assert_any_call(conn, 42, 'Fiction')
    mock_ibt.assert_any_call(conn, 42, 'Adventure')
    mock_uba.assert_called_once_with(conn, 42, 'B01234567X')
    mock_ief.assert_called_once_with(conn, 42, 'book.epub', 1000, 'B01234567X')
    mock_rp.assert_called_once_with(conn, 'book.epub', 'created', 42)
    assert ctx.created == 1
    assert len(ctx.book_records) == 1


# ---------------------------------------------------------------------------
# AC#2 — Re-processing the same EPUB creates no new rows (idempotency)
# ---------------------------------------------------------------------------

@patch('ingest_epub.check_already_processed', return_value=True)
def test_idempotency_skips_already_processed(mock_cap):
    conn = _make_conn()
    ctx = _make_ctx()

    process_epub(conn, ctx, 'book.epub', 1000, TS, 'etag1')

    assert ctx.skipped == 1
    assert ctx.created == 0
    assert ctx.matched == 0
    assert ctx.errors == 0


# ---------------------------------------------------------------------------
# AC#3 — Corrupt EPUB records extract_error and is skipped
# ---------------------------------------------------------------------------

@patch('ingest_epub.record_progress')
@patch('ingest_epub.record_issue')
@patch('ingest_epub.extract_epub_metadata', side_effect=Exception('corrupt file'))
@patch('ingest_epub.get_cached_file', return_value='/tmp/bad.epub')
@patch('ingest_epub.check_already_processed', return_value=False)
def test_extract_error_records_issue_and_skips(
    mock_cap, mock_gcf, mock_meta, mock_ri, mock_rp,
):
    conn = _make_conn()
    ctx = _make_ctx()

    process_epub(conn, ctx, 'bad.epub', 500, TS, 'etag1')

    # Should record an extract_error issue with book_id=None
    mock_ri.assert_called_once()
    ri_args = mock_ri.call_args[0]
    assert ri_args[2] is None         # book_id
    assert ri_args[3] == 'extract_error'

    # Should record an 'error' progress entry
    mock_rp.assert_called_once()
    rp_args = mock_rp.call_args[0]
    assert rp_args[2] == 'error'

    assert ctx.errors == 1
    assert ctx.created == 0


# ---------------------------------------------------------------------------
# AC#4 — All DB writes succeed or fail together (transaction rollback)
# ---------------------------------------------------------------------------

@patch('ingest_epub.find_or_create_author', side_effect=RuntimeError('DB error'))
@patch('ingest_epub.create_book', return_value=42)
@patch('ingest_epub.find_matching_book_candidates', return_value=[])
@patch('ingest_epub.extract_epub_metadata', return_value=EPUB_META)
@patch('ingest_epub.get_cached_file', return_value='/tmp/book.epub')
@patch('ingest_epub.check_already_processed', return_value=False)
def test_transaction_rollback_on_mid_write_error(
    mock_cap, mock_gcf, mock_meta, mock_fmbc, mock_cb, mock_foca,
):
    conn = _make_conn()
    ctx = _make_ctx()

    with pytest.raises(RuntimeError, match='DB error'):
        process_epub(conn, ctx, 'book.epub', 1000, TS, 'etag1')

    # The context manager __exit__ must have been called with exception info
    conn.__exit__.assert_called_once()
    exc_type = conn.__exit__.call_args[0][0]
    assert exc_type is RuntimeError


# ---------------------------------------------------------------------------
# AC#5 — ebook_files.asin and books.asin are populated from EPUB identifier
# ---------------------------------------------------------------------------

EPUB_META_ASIN = {**EPUB_META, 'identifiers': {'asin': 'B09ABCDEFG'}}


@patch('ingest_epub.record_progress')
@patch('ingest_epub.insert_ebook_file')
@patch('ingest_epub.insert_book_tag')
@patch('ingest_epub.insert_book_author')
@patch('ingest_epub.find_or_create_author', return_value=1)
@patch('ingest_epub.update_book_asin')
@patch('ingest_epub.find_matching_book_candidates', return_value=[])
@patch('ingest_epub.create_book', return_value=5)
@patch('ingest_epub.extract_epub_metadata', return_value=EPUB_META_ASIN)
@patch('ingest_epub.get_cached_file', return_value='/tmp/book.epub')
@patch('ingest_epub.check_already_processed', return_value=False)
def test_asin_populated_on_ebook_file_and_book(
    mock_cap, mock_gcf, mock_meta, mock_cb, mock_fmbc,
    mock_uba, mock_foca, mock_iba, mock_ibt, mock_ief, mock_rp,
):
    conn = _make_conn()
    ctx = _make_ctx()

    process_epub(conn, ctx, 'book.epub', 1000, TS, 'etag1')

    mock_uba.assert_called_once_with(conn, 5, 'B09ABCDEFG')
    mock_ief.assert_called_once_with(conn, 5, 'book.epub', 1000, 'B09ABCDEFG')


# ---------------------------------------------------------------------------
# AC#6 — No title uses filename fallback and records no_metadata issue
# ---------------------------------------------------------------------------

EPUB_META_NO_TITLE = {**EPUB_META, 'title': None, 'identifiers': {}}


@patch('ingest_epub.record_progress')
@patch('ingest_epub.insert_ebook_file')
@patch('ingest_epub.insert_book_tag')
@patch('ingest_epub.insert_book_author')
@patch('ingest_epub.find_or_create_author', return_value=1)
@patch('ingest_epub.update_book_asin')
@patch('ingest_epub.record_issue')
@patch('ingest_epub.find_matching_book_candidates', return_value=[])
@patch('ingest_epub.create_book', return_value=10)
@patch('ingest_epub.extract_epub_metadata', return_value=EPUB_META_NO_TITLE)
@patch('ingest_epub.get_cached_file', return_value='/tmp/book.epub')
@patch('ingest_epub.check_already_processed', return_value=False)
def test_no_title_uses_filename_and_records_no_metadata_issue(
    mock_cap, mock_gcf, mock_meta, mock_cb, mock_fmbc, mock_ri,
    mock_uba, mock_foca, mock_iba, mock_ibt, mock_ief, mock_rp,
):
    conn = _make_conn()
    ctx = _make_ctx()

    process_epub(conn, ctx, 'My Book Title.epub', 1000, TS, 'etag1')

    # create_book should receive the filename-derived title
    title_arg = mock_cb.call_args[0][1]
    assert title_arg == 'My Book Title'

    # A no_metadata issue should be recorded for the missing title
    issue_categories = [c[0][3] for c in mock_ri.call_args_list]
    assert 'no_metadata' in issue_categories

    # Book should still be created (stub record)
    mock_cb.assert_called_once()


# ---------------------------------------------------------------------------
# Matched book — no new books record, counter incremented correctly
# ---------------------------------------------------------------------------

@patch('ingest_epub.record_progress')
@patch('ingest_epub.insert_ebook_file')
@patch('ingest_epub.insert_book_tag')
@patch('ingest_epub.insert_book_author')
@patch('ingest_epub.find_or_create_author', return_value=1)
@patch('ingest_epub.update_book_asin')
@patch('ingest_epub.find_matching_book_candidates',
       return_value=[(7, 'The Great Novel', ['Jane Doe'], 100)])
@patch('ingest_epub.extract_epub_metadata', return_value=EPUB_META)
@patch('ingest_epub.get_cached_file', return_value='/tmp/book.epub')
@patch('ingest_epub.check_already_processed', return_value=False)
def test_matches_existing_book(
    mock_cap, mock_gcf, mock_meta, mock_fmbc,
    mock_uba, mock_foca, mock_iba, mock_ibt, mock_ief, mock_rp,
):
    conn = _make_conn()
    ctx = _make_ctx()

    process_epub(conn, ctx, 'book.epub', 1000, TS, 'etag1')

    mock_ief.assert_called_once_with(conn, 7, 'book.epub', 1000, 'B01234567X')
    mock_rp.assert_called_once_with(conn, 'book.epub', 'matched', 7)
    assert ctx.matched == 1
    assert ctx.created == 0
    # A matched book is not re-added to book_records
    assert len(ctx.book_records) == 0


# ---------------------------------------------------------------------------
# Match conflict — records issue and creates a new book
# ---------------------------------------------------------------------------

@patch('ingest_epub.record_progress')
@patch('ingest_epub.insert_ebook_file')
@patch('ingest_epub.insert_book_tag')
@patch('ingest_epub.insert_book_author')
@patch('ingest_epub.find_or_create_author', return_value=1)
@patch('ingest_epub.update_book_asin')
@patch('ingest_epub.record_issue')
@patch('ingest_epub.find_matching_book_candidates', return_value=[
    (1, 'The Great Novel', ['Jane Doe'], 90),
    (2, 'The Great Novel II', ['Jane Doe'], 88),
])
@patch('ingest_epub.create_book', return_value=99)
@patch('ingest_epub.extract_epub_metadata', return_value=EPUB_META)
@patch('ingest_epub.get_cached_file', return_value='/tmp/book.epub')
@patch('ingest_epub.check_already_processed', return_value=False)
def test_match_conflict_records_issue_and_creates_new_book(
    mock_cap, mock_gcf, mock_meta, mock_cb, mock_fmbc, mock_ri,
    mock_uba, mock_foca, mock_iba, mock_ibt, mock_ief, mock_rp,
):
    conn = _make_conn()
    ctx = _make_ctx()

    process_epub(conn, ctx, 'book.epub', 1000, TS, 'etag1')

    # A match_conflict issue should be recorded
    issue_categories = [c[0][3] for c in mock_ri.call_args_list]
    assert 'match_conflict' in issue_categories

    # A new book should still be created
    mock_cb.assert_called_once()
    mock_rp.assert_called_once_with(conn, 'book.epub', 'created', 99)
