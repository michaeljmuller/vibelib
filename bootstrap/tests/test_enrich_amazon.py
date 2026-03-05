"""
Unit tests for bootstrap/enrich_amazon.py.
Amazon, DB, and sleep calls are mocked; no real network or Postgres needed.
"""

from unittest.mock import MagicMock, call, patch

import pytest

from enrich_amazon import (
    _parse_pub_date,
    build_catchup_queue,
    enrich_book_amazon,
    parse_series_text,
)
from run_context import RunContext


def _make_ctx(**overrides):
    kwargs = dict(
        config={
            's3_bucket': 'test-bucket',
            'cache_dir': '/tmp/cache',
            'fuzzy_match_threshold': 85,
            'amazon_delay_min': 0,
            'amazon_delay_max': 0,
        },
        book_records=[],
        author_records=[],
        series_records=[],
    )
    kwargs.update(overrides)
    return RunContext(**kwargs)


def _make_conn():
    return MagicMock()


# ---------------------------------------------------------------------------
# parse_series_text
# ---------------------------------------------------------------------------

def test_parse_series_book_n_of_m_colon():
    name, order, disp = parse_series_text('Book 3 of 7: The Wheel of Time')
    assert name == 'The Wheel of Time'
    assert order == 3.0
    assert disp == '3'


def test_parse_series_book_n_colon():
    name, order, disp = parse_series_text('Book 2: The Dark Tower')
    assert name == 'The Dark Tower'
    assert order == 2.0
    assert disp == '2'


def test_parse_series_fractional():
    name, order, disp = parse_series_text('Book 1.5: Series Name')
    assert name == 'Series Name'
    assert order == 1.5
    assert disp == '1.5'


def test_parse_series_parens():
    name, order, disp = parse_series_text('The Dark Tower (Book 7)')
    assert name == 'The Dark Tower'
    assert order == 7.0
    assert disp == '7'


def test_parse_series_comma():
    name, order, disp = parse_series_text('The Dark Tower, Book 7')
    assert name == 'The Dark Tower'
    assert order == 7.0
    assert disp == '7'


def test_parse_series_no_number():
    name, order, disp = parse_series_text('The Wheel of Time')
    assert name == 'The Wheel of Time'
    assert order is None
    assert disp is None


def test_parse_series_empty():
    name, order, disp = parse_series_text('')
    assert name is None
    assert order is None
    assert disp is None


# ---------------------------------------------------------------------------
# AC#1 — A book with a valid ASIN gets an amazon_metadata row
# ---------------------------------------------------------------------------

AMAZON_DATA = {
    'asin': 'B01234567X',
    'rating': 4.5,
    'num_ratings': 1000,
    'pages': 300,
    'publication_date': 'January 1, 2020',
    'series': None,
}


@patch('enrich_amazon.time.sleep')
def test_inserts_amazon_metadata(mock_sleep):
    conn = _make_conn()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = None
    ctx = _make_ctx()

    with patch('common.amazon.scrape_amazon_metadata', return_value=AMAZON_DATA):
        enrich_book_amazon(conn, ctx, 'book.epub', 42, 'B01234567X', ctx.config)

    assert ctx.amazon_succeeded == 1


# ---------------------------------------------------------------------------
# AC#2 — Already-enriched book is not re-scraped
# ---------------------------------------------------------------------------

@patch('enrich_amazon.time.sleep')
def test_skips_already_enriched(mock_sleep):
    conn = _make_conn()
    # Simulate "already enriched" check returning a row
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (1,)
    ctx = _make_ctx()

    with patch('common.amazon.scrape_amazon_metadata') as mock_scrape:
        enrich_book_amazon(conn, ctx, 'book.epub', 42, 'B01234567X', ctx.config)
        mock_scrape.assert_not_called()

    assert ctx.amazon_succeeded == 0


# ---------------------------------------------------------------------------
# AC#3 — Randomized delay between every Amazon request
# ---------------------------------------------------------------------------

@patch('enrich_amazon.time.sleep')
@patch('enrich_amazon.random.uniform', return_value=7.3)
def test_delay_applied(mock_uniform, mock_sleep):
    conn = _make_conn()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = None
    ctx = _make_ctx()

    with patch('common.amazon.scrape_amazon_metadata', return_value=AMAZON_DATA):
        enrich_book_amazon(conn, ctx, 'book.epub', 42, 'B01234567X', ctx.config)

    mock_sleep.assert_called_once_with(7.3)


# ---------------------------------------------------------------------------
# AC#4 — CAPTCHA records amazon_captcha issue, does not abort
# ---------------------------------------------------------------------------

@patch('enrich_amazon.time.sleep')
@patch('enrich_amazon.record_issue')
def test_captcha_records_issue_and_continues(mock_ri, mock_sleep):
    conn = _make_conn()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = None
    ctx = _make_ctx()

    with patch(
        'common.amazon.scrape_amazon_metadata',
        side_effect=RuntimeError('Amazon returned CAPTCHA for ASIN B01234567X'),
    ):
        enrich_book_amazon(conn, ctx, 'book.epub', 42, 'B01234567X', ctx.config)

    categories = [c[0][3] for c in mock_ri.call_args_list]
    assert 'amazon_captcha' in categories
    assert ctx.amazon_failed == 1
    assert ctx.amazon_succeeded == 0


# ---------------------------------------------------------------------------
# AC#5 — Series data creates series + book_series records
# ---------------------------------------------------------------------------

AMAZON_DATA_WITH_SERIES = {
    **AMAZON_DATA,
    'series': 'Book 3 of 7: The Wheel of Time',
}


@patch('enrich_amazon.time.sleep')
def test_series_data_inserted(mock_sleep):
    conn = _make_conn()
    conn.cursor.return_value.__enter__.return_value.fetchone.side_effect = [
        None,       # not yet enriched check
        (99,),      # INSERT series RETURNING series_id
    ]
    ctx = _make_ctx()

    with patch('common.amazon.scrape_amazon_metadata', return_value=AMAZON_DATA_WITH_SERIES):
        enrich_book_amazon(conn, ctx, 'book.epub', 42, 'B01234567X', ctx.config)

    assert ctx.amazon_succeeded == 1
    # Series should have been added to context
    assert len(ctx.series_records) == 1
    assert ctx.series_records[0][1] == 'The Wheel of Time'


# ---------------------------------------------------------------------------
# AC#6 — Fuzzy series matching reuses existing series record
# ---------------------------------------------------------------------------

@patch('enrich_amazon.time.sleep')
def test_fuzzy_series_match_reuses_existing(mock_sleep):
    conn = _make_conn()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = None
    ctx = _make_ctx(series_records=[(7, 'The Wheel of Time')])

    with patch('common.amazon.scrape_amazon_metadata',
               return_value={**AMAZON_DATA, 'series': 'Book 1: Wheel of Time'}):
        enrich_book_amazon(conn, ctx, 'book.epub', 42, 'B01234567X', ctx.config)

    # No new series should have been added (reused existing)
    assert len(ctx.series_records) == 1
    assert ctx.series_records[0][0] == 7


# ---------------------------------------------------------------------------
# AC#7 — build_catchup_queue returns books missing amazon_metadata
# ---------------------------------------------------------------------------

def test_build_catchup_queue_returns_pending_books():
    conn = _make_conn()
    conn.cursor.return_value.__enter__.return_value.fetchall.return_value = [
        (1, 'B01234567X', 'book1.epub'),
        (2, 'B09ABCDEFG', 'book2.epub'),
    ]

    result = build_catchup_queue(conn)

    assert len(result) == 2
    assert result[0] == (1, 'B01234567X', 'book1.epub')
    assert result[1] == (2, 'B09ABCDEFG', 'book2.epub')


# ---------------------------------------------------------------------------
# AC#8 — Series widget text correctly parsed for name, sort_order, display_number
# ---------------------------------------------------------------------------

def test_parse_series_extracts_number_and_name():
    # Verify the formats seen in real Amazon responses
    cases = [
        ('Book 3 of 7: The Wheel of Time', 'The Wheel of Time', 3.0, '3'),
        ('Book 1.5: Mistborn Era 2',        'Mistborn Era 2',    1.5, '1.5'),
        ('The Dark Tower (Book 7)',          'The Dark Tower',    7.0, '7'),
        ('Discworld, Book 9',               'Discworld',         9.0, '9'),
    ]
    for text, exp_name, exp_order, exp_disp in cases:
        name, order, disp = parse_series_text(text)
        assert name == exp_name, f'name mismatch for {text!r}'
        assert order == exp_order, f'order mismatch for {text!r}'
        assert disp == exp_disp, f'disp mismatch for {text!r}'
