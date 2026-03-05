"""
Amazon metadata enrichment for the bootstrap pipeline.

enrich_book_amazon() scrapes Amazon for a book with a known ASIN and writes
the results into amazon_metadata, and optionally series/book_series.

build_catchup_queue() finds books that have an ASIN but no amazon_metadata
row, to be processed after the main S3 pass.
"""

import datetime
import logging
import random
import re
import time

from common.matching import normalize_title
from db_helpers import record_issue
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)


def parse_series_text(text):
    """
    Parse an Amazon series widget string into (series_name, sort_order, display_number).

    Handles formats observed in the wild:
      "Book 3 of 7: The Wheel of Time"  → ("The Wheel of Time", 3.0, "3")
      "Book 1.5: Series Name"           → ("Series Name", 1.5, "1.5")
      "The Dark Tower (Book 7)"         → ("The Dark Tower", 7.0, "7")
      "Series Name, Book 3"             → ("Series Name", 3.0, "3")
      "The Wheel of Time"               → ("The Wheel of Time", None, None)

    Returns (series_name, sort_order, display_number).  sort_order is float,
    display_number is a string.  If no number is found, the last two are None.
    """
    if not text:
        return None, None, None
    text = text.strip()

    def _parse_num(num_str):
        n = float(num_str)
        disp = num_str.rstrip('0').rstrip('.') if '.' in num_str else num_str
        return n, disp

    # "Book N [of M]: Series Name"
    m = re.match(
        r'^Book\s+([\d.]+)(?:\s+of\s+[\d.]+)?\s*[:\-]\s*(.+)$', text, re.IGNORECASE
    )
    if m:
        sort_order, display_number = _parse_num(m.group(1))
        return m.group(2).strip(), sort_order, display_number

    # "Series Name (Book N)"
    m = re.match(r'^(.+?)\s*\(Book\s+([\d.]+)\)$', text, re.IGNORECASE)
    if m:
        sort_order, display_number = _parse_num(m.group(2))
        return m.group(1).strip(), sort_order, display_number

    # "Series Name, Book N"
    m = re.match(r'^(.+?),\s*Book\s+([\d.]+)$', text, re.IGNORECASE)
    if m:
        sort_order, display_number = _parse_num(m.group(2))
        return m.group(1).strip(), sort_order, display_number

    return text, None, None


def _parse_pub_date(date_str):
    """Parse an Amazon date string into a datetime.date, or None."""
    if not date_str:
        return None
    for fmt in ('%B %d, %Y', '%b %d, %Y', '%Y-%m-%d', '%B %Y', '%b %Y', '%Y'):
        try:
            return datetime.datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _find_or_create_series(conn, ctx, series_name, threshold=85):
    """Find an existing series by fuzzy name match, or create a new one."""
    norm_candidate = normalize_title(series_name)
    best_id = None
    best_score = -1

    for series_id, name in ctx.series_records:
        norm_existing = normalize_title(name)
        score = 100 if norm_candidate == norm_existing else fuzz.ratio(norm_candidate, norm_existing)
        if score > best_score:
            best_score = score
            best_id = series_id

    if best_id is not None and best_score >= threshold:
        return best_id

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO series (name) VALUES (%s) RETURNING series_id",
            (series_name,),
        )
        series_id = cur.fetchone()[0]
    ctx.add_series(series_id, series_name)
    return series_id


def enrich_book_amazon(conn, ctx, s3_key, book_id, asin, config):
    """
    Scrape Amazon for a book and write results to amazon_metadata and
    (if series data is present) series/book_series.

    Rate-limiting delay is applied before each request.  CAPTCHA and other
    errors are recorded as bootstrap_issues without aborting the run.

    Args:
        conn:     Open psycopg2 connection.
        ctx:      RunContext (updated with any new series records and counters).
        s3_key:   The originating S3 key (used as the issue anchor).
        book_id:  The books.book_id to enrich.
        asin:     The ASIN to scrape.
        config:   The run config dict.
    """
    # Skip if already enriched
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM amazon_metadata WHERE book_id = %s", (book_id,))
        if cur.fetchone():
            return

    # Rate-limiting delay
    delay = random.uniform(config['amazon_delay_min'], config['amazon_delay_max'])
    logger.info('Amazon: sleeping %.1fs then scraping ASIN %s', delay, asin)
    time.sleep(delay)

    # Lazy import so the module is importable without playwright installed
    from common.amazon import scrape_amazon_metadata  # noqa: PLC0415

    try:
        data = scrape_amazon_metadata(asin)
    except RuntimeError as exc:
        if 'CAPTCHA' in str(exc).upper():
            logger.warning('Amazon CAPTCHA for ASIN %s', asin)
            with conn:
                record_issue(conn, s3_key, book_id, 'amazon_captcha', str(exc))
        else:
            logger.warning('Amazon error for ASIN %s: %s', asin, exc)
            with conn:
                record_issue(conn, s3_key, book_id, 'amazon_error', str(exc))
        ctx.amazon_failed += 1
        return
    except Exception as exc:
        logger.warning('Amazon error for ASIN %s: %s', asin, exc)
        with conn:
            record_issue(conn, s3_key, book_id, 'amazon_error', str(exc))
        ctx.amazon_failed += 1
        return

    pub_date = _parse_pub_date(data.get('publication_date'))
    series_text = data.get('series')
    series_name, sort_order, display_number = (
        parse_series_text(series_text) if series_text else (None, None, None)
    )

    threshold = config.get('fuzzy_match_threshold', 85)

    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO amazon_metadata
                    (book_id, asin, sample_time, rating, num_ratings,
                     publication_date, page_count)
                VALUES (%s, %s, NOW(), %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (book_id, asin, data.get('rating'), data.get('num_ratings'),
                 pub_date, data.get('pages')),
            )

        if series_name:
            series_id = _find_or_create_series(conn, ctx, series_name, threshold)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO book_series
                        (book_id, series_id, sort_order, display_number)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (book_id, series_id,
                     sort_order if sort_order is not None else 1.0,
                     display_number),
                )

    ctx.amazon_succeeded += 1
    logger.info(
        'Amazon OK ASIN %s — rating=%s, pages=%s, series=%r',
        asin, data.get('rating'), data.get('pages'), series_text,
    )


def build_catchup_queue(conn):
    """
    Return a list of (book_id, asin, s3_key) for books that have an ASIN
    but no amazon_metadata row, so they can be enriched after the main S3 pass.
    Uses the first bootstrap_progress s3_key for each book as the issue anchor.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (b.book_id)
                b.book_id, b.asin, bp.s3_object_key
            FROM books b
            JOIN bootstrap_progress bp ON bp.book_id = b.book_id
            WHERE b.asin IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM amazon_metadata am WHERE am.book_id = b.book_id
              )
            ORDER BY b.book_id
        """)
        return cur.fetchall()
