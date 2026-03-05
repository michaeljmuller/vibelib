"""
S3 object enumeration and local ETag-keyed file cache for the bootstrap pipeline.

list_s3_objects() yields all .epub and .m4b objects in the bucket, EPUBs first.
get_cached_file()  downloads a file from S3 only when the ETag has changed.
"""

import hashlib
from pathlib import Path

from common.s3 import get_s3_client


def list_s3_objects(s3_bucket, limit_keys=None, max_files=None):
    """
    Paginate through all objects in s3_bucket, yielding (key, size, last_modified, etag)
    tuples for .epub and .m4b files only.  EPUBs are yielded before M4Bs.

    Args:
        s3_bucket:  Bucket name.
        limit_keys: Optional set of S3 keys to restrict processing to.
        max_files:  Optional maximum number of files to yield.
    """
    s3 = get_s3_client()
    epubs = []
    m4bs = []

    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=s3_bucket):
        for obj in page.get('Contents', []):
            key = obj['Key']
            ext = key.lower().rsplit('.', 1)[-1] if '.' in key else ''
            if ext not in ('epub', 'm4b'):
                continue
            if limit_keys is not None and key not in limit_keys:
                continue
            entry = (key, obj['Size'], obj['LastModified'], obj['ETag'].strip('"'))
            if ext == 'epub':
                epubs.append(entry)
            else:
                m4bs.append(entry)

    yielded = 0
    for entry in epubs + m4bs:
        if max_files is not None and yielded >= max_files:
            return
        yield entry
        yielded += 1


def _cache_path(s3_key, cache_dir):
    """Return the directory path for a cached file, keyed by a hash of the S3 key."""
    key_hash = hashlib.sha256(s3_key.encode()).hexdigest()[:16]
    return Path(cache_dir) / key_hash


def get_cached_file(s3_key, etag, s3_bucket, cache_dir):
    """
    Return a local Path to the cached copy of s3_key, downloading from S3 only
    if the file is absent or the ETag differs from the cached value.

    Args:
        s3_key:    S3 object key.
        etag:      Current ETag from the S3 listing (used to detect changes).
        s3_bucket: Bucket name.
        cache_dir: Root directory for the local cache.

    Returns:
        Path to the local file.
    """
    cache_dir = Path(cache_dir)
    entry_dir = _cache_path(s3_key, cache_dir)
    ext = s3_key.rsplit('.', 1)[-1].lower() if '.' in s3_key else 'bin'
    file_path = entry_dir / f'file.{ext}'
    etag_path = entry_dir / 'etag'

    if file_path.exists() and etag_path.exists():
        if etag_path.read_text().strip() == etag:
            return file_path

    entry_dir.mkdir(parents=True, exist_ok=True)
    s3 = get_s3_client()
    response = s3.get_object(Bucket=s3_bucket, Key=s3_key)
    file_path.write_bytes(response['Body'].read())
    etag_path.write_text(etag)

    return file_path
