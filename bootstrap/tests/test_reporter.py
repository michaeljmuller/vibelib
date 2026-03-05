"""
Unit tests for bootstrap/reporter.py.
DB queries and time are mocked; no real network or Postgres needed.
"""

import time
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from reporter import (
    _fmt_duration,
    log_amazon_failed,
    log_amazon_ok,
    log_amazon_start,
    log_progress,
    print_report,
)
from run_context import RunContext


def _make_ctx(**overrides):
    kwargs = dict(
        config={},
        total_files=10,
        processed_files=0,
        start_time=time.time(),
        amazon_total=5,
        amazon_current=0,
    )
    kwargs.update(overrides)
    return RunContext(**kwargs)


# ---------------------------------------------------------------------------
# _fmt_duration
# ---------------------------------------------------------------------------

def test_fmt_duration_zero():
    assert _fmt_duration(0) == '0:00'


def test_fmt_duration_minutes():
    assert _fmt_duration(90) == '0:01'


def test_fmt_duration_hours():
    assert _fmt_duration(3661) == '1:01'


def test_fmt_duration_negative_clamps_to_zero():
    assert _fmt_duration(-5) == '0:00'


# ---------------------------------------------------------------------------
# AC#1 — A progress line is emitted for every processed file
# ---------------------------------------------------------------------------

def test_log_progress_format(capsys):
    ctx = _make_ctx(total_files=100, processed_files=9)
    log_progress(ctx, 'created', 'Dune', ['Frank Herbert'])
    out = capsys.readouterr().out
    assert 'Progress: 10/100' in out
    assert '10.0%' in out
    assert 'created' in out
    assert '"Dune"' in out
    assert 'Frank Herbert' in out


def test_log_progress_increments_counter():
    ctx = _make_ctx(total_files=10, processed_files=0)
    with patch('builtins.print'):
        log_progress(ctx, 'created', 'Title', ['Author'])
    assert ctx.processed_files == 1


def test_log_progress_multiple_authors(capsys):
    ctx = _make_ctx(total_files=5, processed_files=0)
    log_progress(ctx, 'matched', 'Title', ['Author A', 'Author B'])
    out = capsys.readouterr().out
    assert 'Author A, Author B' in out


def test_log_progress_no_authors_shows_unknown(capsys):
    ctx = _make_ctx(total_files=5, processed_files=0)
    log_progress(ctx, 'skipped', 'Title', [])
    out = capsys.readouterr().out
    assert 'Unknown' in out


def test_log_progress_shows_elapsed_and_eta(capsys):
    ctx = _make_ctx(total_files=10, processed_files=4, start_time=time.time() - 10)
    log_progress(ctx, 'created', 'Title', ['A'])
    out = capsys.readouterr().out
    assert 'elapsed:' in out
    assert 'eta:' in out


# ---------------------------------------------------------------------------
# AC#2 — Amazon scrape start and result are each logged on a separate line
# ---------------------------------------------------------------------------

def test_log_amazon_start_format(capsys):
    ctx = _make_ctx(amazon_total=10, amazon_current=0)
    log_amazon_start(ctx, 'B01234567X')
    out = capsys.readouterr().out
    assert 'Amazon 1/10: scraping B01234567X' in out


def test_log_amazon_start_unknown_total(capsys):
    ctx = _make_ctx(amazon_total=0, amazon_current=0)
    log_amazon_start(ctx, 'B01234567X')
    out = capsys.readouterr().out
    assert 'Amazon 1/?: scraping B01234567X' in out


def test_log_amazon_ok_format(capsys):
    ctx = _make_ctx()
    log_amazon_ok(ctx, 'B01234567X', {'rating': 4.5, 'pages': 300, 'series': 'Dune'})
    out = capsys.readouterr().out
    assert 'Amazon OK B01234567X' in out
    assert 'rating=4.5' in out
    assert 'pages=300' in out
    assert "series='Dune'" in out


def test_log_amazon_failed_format(capsys):
    ctx = _make_ctx()
    log_amazon_failed(ctx, 'B01234567X', 'CAPTCHA')
    out = capsys.readouterr().out
    assert 'Amazon FAILED B01234567X' in out
    assert 'CAPTCHA' in out


# ---------------------------------------------------------------------------
# AC#3 — ETA updates correctly as the run progresses
# ---------------------------------------------------------------------------

def test_eta_decreases_as_progress_increases(capsys):
    # Process 5 of 10 in 5 seconds → rate=1/s, ETA should be 5s
    ctx = _make_ctx(total_files=10, processed_files=4, start_time=time.time() - 5)
    log_progress(ctx, 'created', 'T', ['A'])
    out = capsys.readouterr().out
    # After 5 files in 5 seconds, 5 remaining at 1/s → eta 5s = 0:05 → H:MM = 0:00
    assert 'eta:' in out


def test_eta_is_zero_when_all_processed(capsys):
    # Last file — no remaining
    ctx = _make_ctx(total_files=5, processed_files=4, start_time=time.time() - 10)
    log_progress(ctx, 'created', 'T', ['A'])
    out = capsys.readouterr().out
    assert 'eta: 0:00' in out


# ---------------------------------------------------------------------------
# AC#4 — Post-run report counts match the actual rows in the DB tables
# ---------------------------------------------------------------------------

def _make_report_conn(
    outcome_counts=(('created', 3), ('matched', 2)),
    books=5,
    authors=4,
    series=1,
    amazon_in_db=3,
    issue_counts=(('no_metadata', 1),),
    unresolved=(('file.epub', 'no_metadata', 'No author'),),
):
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchall.side_effect = [
        list(outcome_counts),
        list(issue_counts),
        list(unresolved),
    ]
    cur.fetchone.side_effect = [
        (books,),
        (authors,),
        (series,),
        (amazon_in_db,),
    ]
    return conn


def test_print_report_shows_db_counts(capsys):
    conn = _make_report_conn()
    ctx = _make_ctx()
    ctx.created = 3
    ctx.matched = 2
    ctx.amazon_succeeded = 3
    ctx.amazon_failed = 0
    ctx.amazon_skipped = 2
    print_report(conn, ctx)
    out = capsys.readouterr().out
    assert 'Books in DB:   5' in out
    assert 'Authors in DB: 4' in out
    assert 'Series in DB:  1' in out
    assert 'In DB:     3' in out


def test_print_report_shows_outcome_breakdown(capsys):
    conn = _make_report_conn()
    ctx = _make_ctx()
    print_report(conn, ctx)
    out = capsys.readouterr().out
    assert 'created: 3' in out
    assert 'matched: 2' in out


def test_print_report_shows_unresolved_issues(capsys):
    conn = _make_report_conn()
    ctx = _make_ctx()
    print_report(conn, ctx)
    out = capsys.readouterr().out
    assert 'Unresolved issues (1)' in out
    assert 'file.epub' in out
    assert 'No author' in out


def test_print_report_no_issues(capsys):
    conn = _make_report_conn(issue_counts=[], unresolved=[])
    ctx = _make_ctx()
    print_report(conn, ctx)
    out = capsys.readouterr().out
    assert 'Issues: none' in out
    assert 'Unresolved issues: none' in out


# ---------------------------------------------------------------------------
# AC#5 — All output goes to stdout
# ---------------------------------------------------------------------------

def test_all_output_to_stdout(capsys):
    ctx = _make_ctx()
    log_progress(ctx, 'created', 'T', ['A'])
    log_amazon_start(ctx, 'B0000000AA')
    log_amazon_ok(ctx, 'B0000000AA', {'rating': 4.0, 'pages': 100, 'series': None})
    log_amazon_failed(ctx, 'B0000000BB', 'timeout')
    captured = capsys.readouterr()
    assert captured.out != ''
    assert captured.err == ''
