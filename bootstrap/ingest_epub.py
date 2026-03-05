"""
EPUB ingestion pipeline for the bootstrap tool.

process_epub() processes a single EPUB S3 object:
  1. Skip if already in bootstrap_progress (idempotency).
  2. Download via ETag cache.
  3. Extract metadata with ebooklib.
  4. Fuzzy-match against existing books in RunContext.
  5. In a single DB transaction: create or reuse books record; create/match
     authors; insert book_authors, book_tags, ebook_files, bootstrap_progress.
"""

import logging
import re

from common.epub import extract_epub_metadata
from enrich_amazon import enrich_book_amazon
from reporter import log_progress
from db_helpers import (
    check_already_processed,
    create_book,
    find_matching_book_candidates,
    find_or_create_author,
    insert_book_author,
    insert_book_tag,
    insert_ebook_file,
    record_issue,
    record_progress,
    update_book_asin,
)
from s3_cache import get_cached_file

logger = logging.getLogger(__name__)


def _title_from_key(s3_key):
    """Derive a title from the S3 key filename, without the extension."""
    filename = s3_key.rsplit('/', 1)[-1]
    return filename.rsplit('.', 1)[0] if '.' in filename else filename


def _parse_pub_year(date_str):
    """Extract a 4-digit publication year from an EPUB date string, or None."""
    if not date_str:
        return None
    match = re.search(r'\b(\d{4})\b', date_str)
    if match:
        year = int(match.group(1))
        if 1000 <= year <= 2100:
            return year
    return None


def process_epub(conn, ctx, s3_key, size, last_modified, etag):
    """
    Process a single EPUB file from S3.

    Args:
        conn:          Open psycopg2 connection.
        ctx:           RunContext with existing DB records and config.
        s3_key:        S3 object key for the EPUB.
        size:          File size in bytes (from S3 object metadata).
        last_modified: datetime of S3 last-modified (used as acquisition_date).
        etag:          ETag from S3 listing (for ETag cache invalidation).
    """
    # 1. Idempotency: skip if already processed
    if check_already_processed(conn, s3_key):
        logger.info('Skipping already-processed: %s', s3_key)
        ctx.skipped += 1
        log_progress(ctx, 'skipped', _title_from_key(s3_key), [])
        return

    # 2. Download via ETag cache
    epub_path = get_cached_file(s3_key, etag, ctx.config['s3_bucket'], ctx.config['cache_dir'])

    # 3. Extract metadata
    try:
        meta = extract_epub_metadata(epub_path)
    except Exception as exc:
        logger.warning('Metadata extraction failed for %s: %s', s3_key, exc)
        with conn:
            record_issue(conn, s3_key, None, 'extract_error', str(exc))
            record_progress(conn, s3_key, 'error', None)
        ctx.errors += 1
        log_progress(ctx, 'error', _title_from_key(s3_key), [])
        return

    raw_title = meta.get('title')
    title = raw_title or _title_from_key(s3_key)
    authors = meta.get('authors') or []
    subjects = meta.get('subjects') or []
    language = meta.get('language')
    isbn = meta.get('isbn')
    pub_year = _parse_pub_year(meta.get('date'))
    acquisition_date = last_modified.date() if hasattr(last_modified, 'date') else None

    identifiers = meta.get('identifiers') or {}
    asin = identifiers.get('asin') or identifiers.get('mobi-asin')

    # 4. Fuzzy-match against existing books
    threshold = ctx.config.get('fuzzy_match_threshold', 85)
    candidates = find_matching_book_candidates(ctx, title, threshold)

    # 5. Single transaction: all DB writes
    with conn:
        if len(candidates) > 1:
            detail = (
                f'Match conflict: {len(candidates)} title candidates above threshold '
                f'({[c[1] for c in candidates]})'
            )
            record_issue(conn, s3_key, None, 'match_conflict', detail)
            book_id = create_book(conn, title, language, isbn, pub_year, acquisition_date)
            outcome = 'created'
        elif len(candidates) == 1:
            book_id = candidates[0][0]
            outcome = 'matched'
        else:
            book_id = create_book(conn, title, language, isbn, pub_year, acquisition_date)
            outcome = 'created'

        # Record no_metadata issues for missing title or authors
        if not raw_title:
            record_issue(
                conn, s3_key, book_id, 'no_metadata',
                'No title found in EPUB metadata; filename used as fallback',
            )
        if not authors:
            record_issue(
                conn, s3_key, book_id, 'no_metadata',
                'No author metadata found in EPUB',
            )

        # Create/match authors and link to book
        for order, author_name in enumerate(authors, 1):
            author_id = find_or_create_author(conn, ctx, author_name)
            insert_book_author(conn, book_id, author_id, order)

        # Insert tags
        for tag in subjects:
            insert_book_tag(conn, book_id, tag)

        # Handle ASIN: set on ebook_files and propagate to books if null
        if asin:
            update_book_asin(conn, book_id, asin)

        insert_ebook_file(conn, book_id, s3_key, size, asin)
        record_progress(conn, s3_key, outcome, book_id)

    if outcome == 'created':
        ctx.add_book(book_id, title, authors)
        ctx.created += 1
    else:
        ctx.matched += 1

    logger.info('%s %s → book_id=%d', outcome.upper(), s3_key, book_id)
    log_progress(ctx, outcome, title, authors)

    if asin and not ctx.config.get('dry_run'):
        enrich_book_amazon(conn, ctx, s3_key, book_id, asin, ctx.config)
    elif not asin:
        ctx.amazon_skipped += 1
