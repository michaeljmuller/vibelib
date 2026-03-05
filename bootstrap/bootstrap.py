#!/usr/bin/env python3
"""
Bootstrap tool: one-shot population of the library database from existing S3 objects.

Reads all .epub and .m4b files from S3, extracts metadata, and populates the
Postgres database. Idempotent and resumable via the bootstrap_progress table.
"""

import os
import sys

import boto3
import psycopg2

# Verify common/ is importable at startup (shared library used throughout the pipeline)
import common  # noqa: F401

from enrich_amazon import build_catchup_queue, enrich_book_amazon
from ingest_epub import process_epub
from ingest_m4b import process_m4b
from run_context import load_context
from s3_cache import list_s3_objects


REQUIRED_ENV_VARS = [
    'S3_BUCKET',
    'S3_ENDPOINT',
    'AWS_ACCESS_KEY_ID',
    'AWS_SECRET_ACCESS_KEY',
    'DATABASE_URL',
]


def load_config():
    """Load environment variables, failing fast with a clear message on any missing required var."""
    missing = [var for var in REQUIRED_ENV_VARS if not os.environ.get(var)]
    if missing:
        for var in missing:
            print(f'ERROR: Required environment variable {var} is not set', file=sys.stderr)
        sys.exit(1)

    limit_keys_raw = os.environ.get('LIMIT_KEYS')
    max_files_raw = os.environ.get('MAX_FILES')

    if max_files_raw is not None:
        try:
            max_files = int(max_files_raw)
            if max_files <= 0:
                raise ValueError
        except ValueError:
            print(f'ERROR: MAX_FILES must be a positive integer, got: {max_files_raw!r}', file=sys.stderr)
            sys.exit(1)
    else:
        max_files = None

    return {
        's3_bucket': os.environ['S3_BUCKET'],
        's3_endpoint': os.environ['S3_ENDPOINT'],
        's3_region': os.environ.get('S3_REGION', 'us-east-1'),
        'aws_access_key_id': os.environ['AWS_ACCESS_KEY_ID'],
        'aws_secret_access_key': os.environ['AWS_SECRET_ACCESS_KEY'],
        'database_url': os.environ['DATABASE_URL'],
        'cache_dir': os.environ.get('CACHE_DIR', '/tmp/bootstrap_cache'),
        'amazon_delay_min': float(os.environ.get('AMAZON_DELAY_MIN', '5')),
        'amazon_delay_max': float(os.environ.get('AMAZON_DELAY_MAX', '15')),
        'fuzzy_match_threshold': float(os.environ.get('FUZZY_MATCH_THRESHOLD', '85')),
        'dry_run': bool(os.environ.get('DRY_RUN')),
        'limit_keys': set(k.strip() for k in limit_keys_raw.split(',') if k.strip()) if limit_keys_raw else None,
        'max_files': max_files,
    }


def check_postgres(config):
    """Verify Postgres connectivity; exit on failure."""
    try:
        conn = psycopg2.connect(config['database_url'])
        conn.close()
        print('Postgres: OK')
    except Exception as e:
        print(f'Postgres: FAILED — {e}', file=sys.stderr)
        sys.exit(1)


def check_s3(config):
    """Verify S3 connectivity by checking the bucket exists; exit on failure."""
    try:
        client = boto3.client(
            's3',
            endpoint_url=config['s3_endpoint'],
            region_name=config['s3_region'],
            aws_access_key_id=config['aws_access_key_id'],
            aws_secret_access_key=config['aws_secret_access_key'],
        )
        client.head_bucket(Bucket=config['s3_bucket'])
        print('S3: OK')
    except Exception as e:
        print(f'S3: FAILED — {e}', file=sys.stderr)
        sys.exit(1)


def main():
    config = load_config()

    if config['dry_run']:
        print('DRY RUN mode — no database writes will be made')
    if config['limit_keys'] is not None:
        print(f"LIMIT_KEYS: restricting to {len(config['limit_keys'])} specified key(s)")
    if config['max_files'] is not None:
        print(f"MAX_FILES: stopping after {config['max_files']} file(s)")

    check_postgres(config)
    check_s3(config)

    print('Startup checks passed.')

    # Ingestion pipeline — implemented in subsequent tasks.
    run(config)


def run(config):
    """Main ingestion pipeline: enumerate S3 objects and ingest each file."""
    conn = psycopg2.connect(config['database_url'])
    try:
        ctx = load_context(conn, config)
        print(
            f'Loaded context: {len(ctx.book_records)} books, '
            f'{len(ctx.author_records)} authors'
        )

        for s3_key, size, last_modified, etag in list_s3_objects(
            config['s3_bucket'],
            limit_keys=config['limit_keys'],
            max_files=config['max_files'],
        ):
            if s3_key.lower().endswith('.epub'):
                process_epub(conn, ctx, s3_key, size, last_modified, etag)
            elif s3_key.lower().endswith('.m4b'):
                process_m4b(conn, ctx, s3_key, size, last_modified, etag)

        # Catch-up queue: enrich any books with an ASIN that were missed
        if not config.get('dry_run'):
            catchup = build_catchup_queue(conn)
            if catchup:
                print(f'Catch-up: {len(catchup)} book(s) with ASIN missing amazon_metadata')
                for book_id, asin, s3_key in catchup:
                    enrich_book_amazon(conn, ctx, s3_key, book_id, asin, config)

        print(
            f'Done: {ctx.created} created, {ctx.matched} matched, '
            f'{ctx.errors} errors, {ctx.skipped} skipped | '
            f'Amazon: {ctx.amazon_succeeded} ok, {ctx.amazon_failed} failed, '
            f'{ctx.amazon_skipped} skipped'
        )
    finally:
        conn.close()


if __name__ == '__main__':
    main()
