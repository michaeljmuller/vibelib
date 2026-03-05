"""
Database helper functions for the bootstrap ingestion pipeline.

All write helpers expect to be called inside an active transaction
(the caller manages the `with conn:` block).
"""

import logging

from common.matching import match_author, normalize_title, prefer_longer_name
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)


def check_already_processed(conn, s3_key):
    """Return True if s3_key already has a row in bootstrap_progress."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM bootstrap_progress WHERE s3_object_key = %s",
            (s3_key,),
        )
        return cur.fetchone() is not None


def find_matching_book_candidates(ctx, title, threshold=85):
    """
    Find all books in ctx.book_records whose normalized title scores at or
    above threshold against the candidate title.

    Returns a list of (book_id, title, author_names, score) tuples.
    """
    norm_candidate = normalize_title(title)
    if not norm_candidate:
        return []

    results = []
    for book_id, book_title, book_authors in ctx.book_records:
        norm_existing = normalize_title(book_title)
        if norm_candidate == norm_existing:
            score = 100
        else:
            score = fuzz.ratio(norm_candidate, norm_existing)
        if score >= threshold:
            results.append((book_id, book_title, book_authors, score))

    return results


def find_or_create_author(conn, ctx, name):
    """
    Find an existing author matching name, or create a new one.

    If a match is found and the candidate name is longer than the stored
    primary_name, update the DB and in-memory cache (prefer_longer_name rule).

    Returns the author_id.
    """
    author_names = [primary_name for _, primary_name in ctx.author_records]
    match = match_author(name, author_names)

    if match is not None:
        matched_name, _ = match
        author_id = next(
            aid for aid, pname in ctx.author_records if pname == matched_name
        )
        better_name = prefer_longer_name(name, matched_name)
        if better_name != matched_name:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE authors SET primary_name = %s WHERE author_id = %s",
                    (better_name, author_id),
                )
            idx = next(
                i for i, (aid, _) in enumerate(ctx.author_records) if aid == author_id
            )
            ctx.author_records[idx] = (author_id, better_name)
        return author_id

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO authors (primary_name) VALUES (%s) RETURNING author_id",
            (name,),
        )
        author_id = cur.fetchone()[0]
    ctx.add_author(author_id, name)
    return author_id


def create_book(conn, title, language, isbn, publication_year, acquisition_date):
    """Insert a new books row and return the book_id."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO books
                (title, language_code, isbn, publication_year, acquisition_date)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING book_id
            """,
            (title, language, isbn, publication_year, acquisition_date),
        )
        return cur.fetchone()[0]


def update_book_asin(conn, book_id, asin):
    """Set books.asin if it is currently NULL."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE books SET asin = %s WHERE book_id = %s AND asin IS NULL",
            (asin, book_id),
        )


def insert_book_author(conn, book_id, author_id, author_order):
    """Insert a book_authors row; silently skip if the pair already exists."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO book_authors (book_id, author_id, author_order)
            VALUES (%s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (book_id, author_id, author_order),
        )


def insert_book_tag(conn, book_id, tag):
    """Insert a book_tags row; silently skip if the tag already exists."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO book_tags (book_id, tag)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (book_id, tag),
        )


def insert_ebook_file(conn, book_id, s3_key, file_size_bytes, asin):
    """Insert an ebook_files row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ebook_files
                (book_id, s3_object_key, file_format, file_size_bytes, asin)
            VALUES (%s, %s, 'epub', %s, %s)
            """,
            (book_id, s3_key, file_size_bytes, asin),
        )


def insert_audiobook_file(conn, book_id, s3_key, duration_seconds, file_size_bytes):
    """Insert an audiobook_files row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audiobook_files
                (book_id, s3_object_key, file_format, duration_seconds, file_size_bytes)
            VALUES (%s, %s, 'm4b', %s, %s)
            """,
            (book_id, s3_key, duration_seconds, file_size_bytes),
        )


def record_progress(conn, s3_key, outcome, book_id):
    """Insert a bootstrap_progress row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO bootstrap_progress (s3_object_key, outcome, book_id)
            VALUES (%s, %s, %s)
            """,
            (s3_key, outcome, book_id),
        )


def record_issue(conn, s3_key, book_id, category, detail):
    """Insert a bootstrap_issues row."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO bootstrap_issues (s3_object_key, book_id, category, detail)
            VALUES (%s, %s, %s, %s)
            """,
            (s3_key, book_id, category, detail),
        )
