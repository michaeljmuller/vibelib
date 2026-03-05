"""
RunContext: in-memory cache of existing DB records for the bootstrap pipeline.

Loaded once at startup from the database, then updated incrementally as new
books and authors are created, so that later files can match against them.
"""

import time
from dataclasses import dataclass, field


@dataclass
class RunContext:
    config: dict
    # (book_id, title, [author_primary_names]) — one entry per book
    book_records: list = field(default_factory=list)
    # (author_id, primary_name) — one entry per author
    author_records: list = field(default_factory=list)
    # (series_id, name) — one entry per series
    series_records: list = field(default_factory=list)
    # File-processing counters
    created: int = 0
    matched: int = 0
    errors: int = 0
    skipped: int = 0
    # Amazon-enrichment counters
    amazon_succeeded: int = 0
    amazon_failed: int = 0
    amazon_skipped: int = 0  # no ASIN
    # Progress tracking (set by bootstrap.py before the ingestion loop)
    total_files: int = 0
    processed_files: int = 0
    start_time: float = field(default_factory=time.time)
    amazon_total: int = 0    # set before the catch-up Amazon loop
    amazon_current: int = 0  # incremented by log_amazon_start

    def add_book(self, book_id, title, author_names):
        """Add a newly created book to the in-memory cache."""
        self.book_records.append((book_id, title, list(author_names)))

    def add_author(self, author_id, primary_name):
        """Add a newly created author to the in-memory cache."""
        self.author_records.append((author_id, primary_name))

    def add_series(self, series_id, name):
        """Add a newly created series to the in-memory cache."""
        self.series_records.append((series_id, name))


def load_context(conn, config):
    """
    Build a RunContext pre-populated with all existing books and authors from
    the database.  Called once before the ingestion loop starts.
    """
    ctx = RunContext(config=config)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT b.book_id, b.title,
                   COALESCE(
                       array_agg(a.primary_name ORDER BY ba.author_order)
                       FILTER (WHERE a.primary_name IS NOT NULL),
                       '{}'
                   )
            FROM books b
            LEFT JOIN book_authors ba ON ba.book_id = b.book_id
            LEFT JOIN authors a ON a.author_id = ba.author_id
            GROUP BY b.book_id, b.title
        """)
        for book_id, title, author_names in cur.fetchall():
            ctx.book_records.append((book_id, title, list(author_names)))

        cur.execute("SELECT author_id, primary_name FROM authors")
        for author_id, primary_name in cur.fetchall():
            ctx.author_records.append((author_id, primary_name))

        cur.execute("SELECT series_id, name FROM series")
        for series_id, name in cur.fetchall():
            ctx.series_records.append((series_id, name))

    return ctx
