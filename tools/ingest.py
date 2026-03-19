#!/usr/bin/env python3
"""
Ingest epubs and m4bs into the vibelib library.
"""

import argparse
import os

import psycopg2

import library

PG_HOST     = os.environ.get("POSTGRES_HOST", "db")
PG_PORT     = int(os.environ.get("POSTGRES_PORT", "5432"))
PG_DB       = os.environ.get("POSTGRES_DB", "vibelib")
PG_USER     = os.environ.get("POSTGRES_USER", "vibelib")
PG_PASSWORD = os.environ.get("POSTGRES_PASSWORD")


def fetch_epubs(conn):
    """Yield one dict per epub with all relevant metadata fields."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT e.id, e.title, e.series, e.series_position,
                   array_agg(ea.author ORDER BY ea.position) FILTER (WHERE ea.author IS NOT NULL) AS authors
            FROM epubs e
            LEFT JOIN epub_authors ea ON ea.epub_id = e.id AND ea.role = 'author'
            GROUP BY e.id
            ORDER BY e.id
        """)
        for row in cur.fetchall():
            yield {
                "epub_id":         row[0],
                "title":           row[1],
                "series":          row[2],
                "series_position": row[3],
                "authors":         row[4] or [],
            }


def fetch_m4bs(conn):
    """Yield one dict per m4b with all relevant metadata fields."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, title, artist, narrator, album
            FROM m4bs
            ORDER BY id
        """)
        for row in cur.fetchall():
            yield {
                "m4b_id":   row[0],
                "title":    row[1],
                "artist":   row[2],
                "narrator": row[3],
                "album":    row[4],
            }


def main():
    parser = argparse.ArgumentParser(description="Ingest epubs and m4bs into the library.")
    parser.add_argument("--output", default="author_map.json", metavar="FILE",
                        help="Mapping file to write (default: author_map.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simulate without writing to the database")
    args = parser.parse_args()

    conn  = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                              user=PG_USER, password=PG_PASSWORD)
    state = library.IngestionState(conn, dry_run=args.dry_run)

    if args.dry_run:
        print("(dry run — no DB writes)\n")

    for epub in fetch_epubs(conn):
        library.process_epub(conn, epub, state)

    for m4b in fetch_m4bs(conn):
        library.process_m4b(conn, m4b, state)

    conn.close()
    state.write_mapping(args.output)


if __name__ == "__main__":
    main()
