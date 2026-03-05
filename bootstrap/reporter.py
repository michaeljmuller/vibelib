"""
Operator-facing progress output and post-run report for the bootstrap pipeline.

All output goes to stdout so it can be captured and monitored by the operator.
"""

import datetime
import time


def _now():
    return datetime.datetime.now().strftime('%H:%M:%S')


def _fmt_duration(seconds):
    """Format a duration in seconds as H:MM."""
    s = max(0, int(seconds))
    return f'{s // 3600}:{(s % 3600) // 60:02d}'


def log_progress(ctx, outcome, title, authors):
    """
    Emit a progress line to stdout and increment ctx.processed_files.

    Format:
        [HH:MM:SS] Progress: N/TOTAL (PCT%) | elapsed: H:MM | eta: H:MM | OUTCOME "TITLE" by AUTHOR
    """
    ctx.processed_files += 1
    n = ctx.processed_files
    total = ctx.total_files
    pct = (n / total * 100) if total else 0.0

    elapsed = time.time() - ctx.start_time
    if elapsed > 0 and n > 0 and total > n:
        rate = n / elapsed
        eta = (total - n) / rate
    else:
        eta = 0

    author_str = ', '.join(authors) if authors else 'Unknown'
    print(
        f'[{_now()}] Progress: {n}/{total} ({pct:.1f}%) | '
        f'elapsed: {_fmt_duration(elapsed)} | '
        f'eta: {_fmt_duration(eta)} | '
        f'{outcome} "{title}" by {author_str}'
    )


def log_amazon_start(ctx, asin):
    """Emit an Amazon scrape-start line to stdout and increment ctx.amazon_current."""
    ctx.amazon_current += 1
    n = ctx.amazon_current
    total = ctx.amazon_total
    total_str = str(total) if total else '?'
    print(f'[{_now()}] Amazon {n}/{total_str}: scraping {asin}')


def log_amazon_ok(ctx, asin, data):
    """Emit an Amazon success line to stdout."""
    print(
        f'[{_now()}] Amazon OK {asin} -- '
        f'rating={data.get("rating")}, '
        f'pages={data.get("pages")}, '
        f'series={data.get("series")!r}'
    )


def log_amazon_failed(ctx, asin, reason):
    """Emit an Amazon failure line to stdout."""
    print(f'[{_now()}] Amazon FAILED {asin} -- {reason}')


def print_report(conn, ctx):
    """
    Print the post-run summary report to stdout.

    Counts are sourced from the database to ensure accuracy.
    """
    with conn.cursor() as cur:
        cur.execute(
            'SELECT outcome, COUNT(*) FROM bootstrap_progress GROUP BY outcome'
        )
        outcome_counts = dict(cur.fetchall())

        cur.execute('SELECT COUNT(*) FROM books')
        books_total = cur.fetchone()[0]

        cur.execute('SELECT COUNT(*) FROM authors')
        authors_total = cur.fetchone()[0]

        cur.execute('SELECT COUNT(*) FROM series')
        series_total = cur.fetchone()[0]

        cur.execute('SELECT COUNT(*) FROM amazon_metadata')
        amazon_in_db = cur.fetchone()[0]

        cur.execute(
            'SELECT category, COUNT(*) FROM bootstrap_issues '
            'GROUP BY category ORDER BY category'
        )
        issue_counts = cur.fetchall()

        cur.execute(
            'SELECT s3_object_key, category, detail '
            'FROM bootstrap_issues WHERE resolved = FALSE '
            'ORDER BY created_at'
        )
        unresolved = cur.fetchall()

    total_processed = sum(outcome_counts.values())

    print()
    print('=' * 60)
    print('Bootstrap Run Report')
    print('=' * 60)
    print(f'S3 objects processed: {total_processed}')
    for outcome in sorted(outcome_counts):
        print(f'  {outcome}: {outcome_counts[outcome]}')
    print()
    print(
        f'Books in DB:   {books_total}'
        f'  (this run: {ctx.created} created, {ctx.matched} matched)'
    )
    print(f'Authors in DB: {authors_total}')
    print(f'Series in DB:  {series_total}')
    print()
    print('Amazon enrichment:')
    print(f'  Succeeded: {ctx.amazon_succeeded}')
    print(f'  Failed:    {ctx.amazon_failed}')
    print(f'  Skipped:   {ctx.amazon_skipped}  (no ASIN)')
    print(f'  In DB:     {amazon_in_db}')
    print()
    if issue_counts:
        print('Issues by category:')
        for category, count in issue_counts:
            print(f'  {category}: {count}')
    else:
        print('Issues: none')
    print()
    if unresolved:
        print(f'Unresolved issues ({len(unresolved)}):')
        for s3_key, category, detail in unresolved:
            print(f'  [{category}] {s3_key}')
            print(f'    {detail}')
    else:
        print('Unresolved issues: none')
    print('=' * 60)
