import hashlib
import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def get_s3_client():
    """Create and return an S3 client."""
    return boto3.client(
        's3',
        region_name=os.environ.get('S3_REGION', 'us-east-1'),
        endpoint_url=os.environ.get('S3_ENDPOINT'),
    )


def _get_cache_path(s3_key, cache_dir):
    """Get the cache directory path for an S3 key, keyed by a hash of the key."""
    key_hash = hashlib.sha256(s3_key.encode()).hexdigest()[:16]
    return Path(cache_dir) / key_hash


def get_cached_epub(s3_key, s3_bucket=None, cache_dir=None):
    """
    Get a cached EPUB, downloading from S3 if missing or if the ETag changed.
    Returns (epub_path, was_downloaded).
    """
    s3_bucket = s3_bucket or os.environ.get('S3_BUCKET')
    if not s3_bucket:
        raise ValueError('S3_BUCKET not configured')
    if cache_dir is None:
        cache_dir = Path(os.environ.get('EPUB_CACHE_DIR', '/tmp/epub_cache'))
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_path = _get_cache_path(s3_key, cache_dir)
    epub_file = cache_path / 'book.epub'
    etag_file = cache_path / 'etag'

    s3 = get_s3_client()

    try:
        head_response = s3.head_object(Bucket=s3_bucket, Key=s3_key)
        current_etag = head_response.get('ETag', '').strip('"')
    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            raise FileNotFoundError(f'S3 object not found: {s3_key}')
        raise

    if epub_file.exists() and etag_file.exists():
        cached_etag = etag_file.read_text().strip()
        if cached_etag == current_etag:
            return epub_file, False

    cache_path.mkdir(parents=True, exist_ok=True)
    response = s3.get_object(Bucket=s3_bucket, Key=s3_key)
    epub_data = response['Body'].read()

    epub_file.write_bytes(epub_data)
    etag_file.write_text(current_etag)

    return epub_file, True
