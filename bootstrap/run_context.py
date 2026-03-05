"""
RunContext: in-memory cache of existing DB records for the bootstrap pipeline.

Loaded once at startup from the database, then updated incrementally as new
books and authors are created, so that later files can match against them.
"""

from dataclasses import dataclass, field


@dataclass
class RunContext:
    config: dict
    # (book_id, title, [author_primary_names]) — one entry per book
    book_records: list = field(default_factory=list)
    # (author_id, primary_name) — one entry per author
    author_records: list = field(default_factory=list)
    # Run counters
    created: int = 0
    matched: int = 0
    errors: int = 0
    skipped: int = 0

    def add_book(self, book_id, title, author_names):
        """Add a newly created book to the in-memory cache."""
        self.book_records.append((book_id, title, list(author_names)))

    def add_author(self, author_id, primary_name):
        """Add a newly created author to the in-memory cache."""
        self.author_records.append((author_id, primary_name))


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

    return ctx
