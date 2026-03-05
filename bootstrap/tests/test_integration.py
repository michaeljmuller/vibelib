"""
Integration tests for end-to-end resume and idempotency (TASK-26).

These tests require a running PostgreSQL instance with the full schema applied.
Set TEST_DATABASE_URL to enable them, e.g.:
    TEST_DATABASE_URL=postgresql://library:library@localhost:5432/library_test pytest -m integration

Without TEST_DATABASE_URL, all tests in this file are skipped.

S3, file extraction, and Amazon scraping are fully mocked so no external
services are needed beyond Postgres.
"""

import datetime
import os
import pathlib

import pytest

pytest.importorskip('psycopg2')
import psycopg2  # noqa: E402

TEST_DB_URL = os.environ.get('TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(
    not TEST_DB_URL,
    reason='integration tests require TEST_DATABASE_URL',
)


# ---------------------------------------------------------------------------
# Schema fixture — applies and tears down the schema around each test
# ---------------------------------------------------------------------------

_SCHEMA_PATH = (
    pathlib.Path(__file__).parent.parent.parent / 'sql' / 'schema.sql'
)

# Tables to truncate between tests (in dependency order, children first)
_TRUNCATE_TABLES = [
    'bootstrap_issues',
    'bootstrap_progress',
    'amazon_metadata',
    'book_series',
    'book_authors',
    'book_tags',
    'ebook_files',
    'audiobook_files',
    'books',
    'authors',
    'series',
]


@pytest.fixture(scope='session')
def pg_conn():
    """Session-scoped connection to the integration test database."""
    conn = psycopg2.connect(TEST_DB_URL)
    conn.autocommit = True

    # Apply schema (CREATE IF NOT EXISTS is not used in schema.sql, so we
    # rely on the tables already existing or use IF NOT EXISTS workaround)
    schema_sql = _SCHEMA_PATH.read_text()
    with conn.cursor() as cur:
        try:
            cur.execute(schema_sql)
        except psycopg2.errors.DuplicateTable:
            # Schema already exists — that's fine
            pass
    conn.autocommit = False
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def clean_tables(pg_conn):
    """Truncate all bootstrap-relevant tables before each test."""
    with pg_conn:
        with pg_conn.cursor() as cur:
            cur.execute(
                f'TRUNCATE {", ".join(_TRUNCATE_TABLES)} RESTART IDENTITY CASCADE'
            )
    yield


@pytest.fixture()
def ctx(pg_conn):
    """A RunContext pre-loaded from the (empty) test database."""
    from run_context import load_context
    config = {
        'fuzzy_match_threshold': 85,
        'amazon_delay_min': 0,
        'amazon_delay_max': 0,
        'dry_run': False,
        's3_bucket': 'test-bucket',
        'cache_dir': '/tmp',
    }
    return load_context(pg_conn, config)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_epub_meta(title='Test Book', authors=None, asin=None):
    """Return a metadata dict as extract_epub_metadata would."""
    return {
        'title': title,
        'authors': authors or ['Test Author'],
        'subjects': [],
        'language': 'en',
        'isbn': None,
        'date': '2020-01-01',
        'identifiers': {'asin': asin} if asin else {},
    }


def _ingest_epub(conn, ctx, s3_key, meta, *, dry_run=False):
    """Call process_epub with mocked file download and metadata extraction."""
    from unittest.mock import MagicMock, patch
    from ingest_epub import process_epub

    last_modified = datetime.datetime(2024, 1, 1)
    with (
        patch('ingest_epub.check_already_processed',
              wraps=lambda c, k: _already_processed(c, k)),
        patch('ingest_epub.get_cached_file', return_value='/tmp/fake.epub'),
        patch('ingest_epub.extract_epub_metadata', return_value=meta),
        patch('ingest_epub.enrich_book_amazon'),
        patch('ingest_epub.log_progress'),
    ):
        process_epub(conn, ctx, s3_key, size=1000, last_modified=last_modified, etag='abc')


def _already_processed(conn, s3_key):
    """Check bootstrap_progress directly."""
    with conn.cursor() as cur:
        cur.execute('SELECT 1 FROM bootstrap_progress WHERE s3_object_key = %s', (s3_key,))
        return cur.fetchone() is not None


def _count(conn, table):
    with conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM {table}')
        return cur.fetchone()[0]


# ---------------------------------------------------------------------------
# AC#1 — Scenario 1: restart after mid-run interruption → same final state
# ---------------------------------------------------------------------------

def test_scenario1_restart_after_interruption(pg_conn, ctx):
    """
    Process two files; simulate interruption after the first.
    On restart, the first file is skipped and the second is processed.
    Final state is identical to a clean run of both files.
    """
    meta1 = _fake_epub_meta(title='Book One', authors=['Author A'])
    meta2 = _fake_epub_meta(title='Book Two', authors=['Author B'])

    # Process only the first file (simulates partial run)
    _ingest_epub(pg_conn, ctx, 'books/book1.epub', meta1)

    assert _count(pg_conn, 'books') == 1
    assert _count(pg_conn, 'bootstrap_progress') == 1

    # Simulate restart: reload context and process both files
    from run_context import load_context
    ctx2 = load_context(pg_conn, ctx.config)
    _ingest_epub(pg_conn, ctx2, 'books/book1.epub', meta1)  # skipped
    _ingest_epub(pg_conn, ctx2, 'books/book2.epub', meta2)  # processed

    assert _count(pg_conn, 'books') == 2
    assert _count(pg_conn, 'bootstrap_progress') == 2
    # No duplicates
    with pg_conn.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM books WHERE title = %s', ('Book One',))
        assert cur.fetchone()[0] == 1
    assert ctx2.skipped == 1


# ---------------------------------------------------------------------------
# AC#2 — Scenario 2: restart after Amazon-scraping interruption
# ---------------------------------------------------------------------------

def test_scenario2_catchup_after_amazon_interruption(pg_conn, ctx):
    """
    Process a file with an ASIN through the ingestion pipeline (no Amazon
    enrichment mocked in). Simulate Amazon interruption by leaving
    amazon_metadata empty. The catch-up queue should find and enrich it.
    """
    from unittest.mock import patch
    from enrich_amazon import build_catchup_queue, enrich_book_amazon

    asin = 'B01234567X'
    meta = _fake_epub_meta(title='Sci-Fi Novel', authors=['Jane Doe'], asin=asin)
    _ingest_epub(pg_conn, ctx, 'books/scifi.epub', meta)

    # Verify book exists with ASIN but no amazon_metadata
    assert _count(pg_conn, 'books') == 1
    assert _count(pg_conn, 'amazon_metadata') == 0

    catchup = build_catchup_queue(pg_conn)
    assert len(catchup) == 1
    assert catchup[0][1] == asin

    amazon_data = {
        'asin': asin,
        'rating': 4.2,
        'num_ratings': 500,
        'pages': 250,
        'publication_date': 'March 2020',
        'series': None,
    }
    with patch('common.amazon.scrape_amazon_metadata', return_value=amazon_data):
        with patch('enrich_amazon.time.sleep'):
            with patch('enrich_amazon.log_amazon_start'):
                with patch('enrich_amazon.log_amazon_ok'):
                    book_id, asin_val, s3_key = catchup[0]
                    enrich_book_amazon(pg_conn, ctx, s3_key, book_id, asin_val, ctx.config)

    assert _count(pg_conn, 'amazon_metadata') == 1

    # Running catch-up again should find no pending books
    catchup2 = build_catchup_queue(pg_conn)
    assert len(catchup2) == 0


# ---------------------------------------------------------------------------
# AC#3 — Scenario 3: full re-run creates no new rows
# ---------------------------------------------------------------------------

def test_scenario3_full_rerun_creates_no_new_rows(pg_conn, ctx):
    """
    Run the ingestion for two files, then run again.
    The second run should create no new rows in any table.
    """
    files = [
        ('books/alpha.epub', _fake_epub_meta(title='Alpha', authors=['A'])),
        ('books/beta.epub',  _fake_epub_meta(title='Beta',  authors=['B'])),
    ]

    # First run
    for s3_key, meta in files:
        _ingest_epub(pg_conn, ctx, s3_key, meta)

    counts_after_first = {
        t: _count(pg_conn, t)
        for t in ('books', 'authors', 'book_authors', 'ebook_files', 'bootstrap_progress')
    }

    # Second run — reload context, re-process same files
    from run_context import load_context
    ctx2 = load_context(pg_conn, ctx.config)
    for s3_key, meta in files:
        _ingest_epub(pg_conn, ctx2, s3_key, meta)

    for table, count in counts_after_first.items():
        assert _count(pg_conn, table) == count, (
            f'{table}: expected {count} rows after re-run, got {_count(pg_conn, table)}'
        )
    assert ctx2.skipped == 2
    assert ctx2.created == 0
    assert ctx2.matched == 0
