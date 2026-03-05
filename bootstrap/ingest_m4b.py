"""
M4B audiobook ingestion pipeline for the bootstrap tool.

process_m4b() processes a single M4B S3 object:
  1. Skip if already in bootstrap_progress (idempotency).
  2. Download via ETag cache.
  3. Extract metadata with mutagen (©nam, ©ART, ©day, duration_seconds).
  4. Fuzzy-match against existing books in RunContext.
  5. In a single DB transaction: create or reuse books record; create/match
     authors; insert audiobook_files, bootstrap_progress.

M4B-specific rules vs EPUB:
  - No title → use filename silently (no no_metadata issue recorded).
  - No author → record a no_metadata issue.
"""

import logging
import re

import mutagen.mp4

from enrich_amazon import enrich_book_amazon
from db_helpers import (
    check_already_processed,
    create_book,
    find_matching_book_candidates,
    find_or_create_author,
    insert_audiobook_file,
    insert_book_author,
    record_issue,
    record_progress,
)
from s3_cache import get_cached_file

logger = logging.getLogger(__name__)


def _title_from_key(s3_key):
    """Derive a title from the S3 key filename, without the extension."""
    filename = s3_key.rsplit('/', 1)[-1]
    return filename.rsplit('.', 1)[0] if '.' in filename else filename


def _extract_m4b_metadata(m4b_path):
    """
    Extract metadata from an M4B file using mutagen.

    Returns a dict with keys: title, authors (list), year, duration_seconds.
    Any field may be None/empty if not present in the file tags.
    """
    audio = mutagen.mp4.MP4(str(m4b_path))
    tags = audio.tags or {}

    def _tag(key):
        vals = tags.get(key)
        return vals[0] if vals else None

    title = _tag('\xa9nam')
    artist = _tag('\xa9ART')
    year_raw = _tag('\xa9day')

    authors = [artist] if artist else []

    year = None
    if year_raw:
        match = re.search(r'\b(\d{4})\b', str(year_raw))
        if match:
            y = int(match.group(1))
            if 1000 <= y <= 2100:
                year = y

    duration_seconds = None
    if audio.info and audio.info.length:
        duration_seconds = int(audio.info.length)

    return {
        'title': title,
        'authors': authors,
        'year': year,
        'duration_seconds': duration_seconds,
    }


def process_m4b(conn, ctx, s3_key, size, last_modified, etag):
    """
    Process a single M4B file from S3.

    Args:
        conn:          Open psycopg2 connection.
        ctx:           RunContext with existing DB records and config.
        s3_key:        S3 object key for the M4B.
        size:          File size in bytes (from S3 object metadata).
        last_modified: datetime of S3 last-modified (used as acquisition_date).
        etag:          ETag from S3 listing (for ETag cache invalidation).
    """
    # 1. Idempotency: skip if already processed
    if check_already_processed(conn, s3_key):
        logger.info('Skipping already-processed: %s', s3_key)
        ctx.skipped += 1
        return

    # 2. Download via ETag cache
    m4b_path = get_cached_file(s3_key, etag, ctx.config['s3_bucket'], ctx.config['cache_dir'])

    # 3. Extract metadata
    meta = _extract_m4b_metadata(m4b_path)

    raw_title = meta['title']
    title = raw_title or _title_from_key(s3_key)
    authors = meta['authors']
    duration_seconds = meta['duration_seconds']
    acquisition_date = last_modified.date() if hasattr(last_modified, 'date') else None

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
            book_id = create_book(conn, title, None, None, meta['year'], acquisition_date)
            outcome = 'created'
        elif len(candidates) == 1:
            book_id = candidates[0][0]
            outcome = 'matched'
        else:
            book_id = create_book(conn, title, None, None, meta['year'], acquisition_date)
            outcome = 'created'

        # M4B: no author → record no_metadata; no title → silent filename fallback
        if not authors:
            record_issue(
                conn, s3_key, book_id, 'no_metadata',
                'No author metadata found in M4B tags',
            )

        # Create/match authors and link to book
        for order, author_name in enumerate(authors, 1):
            author_id = find_or_create_author(conn, ctx, author_name)
            insert_book_author(conn, book_id, author_id, order)

        insert_audiobook_file(conn, book_id, s3_key, duration_seconds, size)
        record_progress(conn, s3_key, outcome, book_id)

    if outcome == 'created':
        ctx.add_book(book_id, title, authors)
        ctx.created += 1
    else:
        ctx.matched += 1

    logger.info('%s %s → book_id=%d', outcome.upper(), s3_key, book_id)

    # M4B files carry no ASIN; always skip Amazon enrichment
    ctx.amazon_skipped += 1
