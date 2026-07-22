"""Book files, fetched from the object store and streamed out through the app.

The app is the only thing that ever talks to the bucket. Handing the browser a
presigned URL would be less work for us, but it makes the URL itself a bearer
token -- anyone holding it can fetch the object, logged in or not, until it
expires -- and it leans on the corners of the S3 protocol that vary most between
implementations (signature details, path- vs virtual-hosted addressing, whether
the response-header overrides that set the download filename are honored).
Proxying uses get_object and nothing else, which every S3 implementation gets
right, so moving the library to another provider is a change of endpoint and
region and nothing more.

The bucket is the same one util/s3.sh talks to; s3_key is a flat object key at
the bucket root.
"""

import os
from dataclasses import dataclass
from functools import cache
from typing import Callable, Iterator

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# Big enough that a large audiobook is not a million tiny reads, small enough
# that a stalled client is not holding a lot of memory hostage.
CHUNK_BYTES = 1 << 20

MEDIA_TYPES = {"epub": "application/epub+zip", "m4b": "audio/mp4"}


@cache
def _client():
    endpoint = os.environ["OBJECT_STORE_BUCKET_ENDPOINT"]
    if not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=os.environ.get("OBJECT_STORE_BUCKET_REGION"),
        aws_access_key_id=os.environ["OBJECT_STORE_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["OBJECT_STORE_SECRET_ACCESS_KEY"],
        config=Config(
            signature_version="s3v4",
            connect_timeout=5,
            # No read timeout: this client now streams whole audiobooks, and a slow
            # download is not a broken one. Connect still fails fast.
            retries={"max_attempts": 2},
        ),
    )


def _bucket() -> str:
    return os.environ["OBJECT_STORE_BUCKET_NAME"]


class NotFound(Exception):
    """The key is not in the bucket."""


class BadRange(Exception):
    """The client asked for bytes that do not exist in the object."""


@dataclass
class Download:
    body: Iterator[bytes]
    length: int  # bytes in *this* response, which for a range request is not the object size
    media_type: str
    filename: str
    content_range: str | None  # set only when the store served a partial response


def fetch(s3_key: str, asset_type: str, range_header: str | None = None) -> Download:
    """Open the object for streaming. Range is passed through untouched and its
    interpretation left to the store -- audiobook players seek by asking for byte
    ranges, and re-implementing that parsing here would only add a way to get it
    wrong."""
    params: dict[str, str] = {"Bucket": _bucket(), "Key": s3_key}
    if range_header:
        params["Range"] = range_header

    try:
        obj = _client().get_object(**params)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("NoSuchKey", "404"):
            raise NotFound(s3_key) from e
        if code in ("InvalidRange", "416"):
            raise BadRange(range_header) from e
        raise

    def chunks() -> Iterator[bytes]:
        # Not `with obj["Body"] as body`: StreamingBody.__enter__ hands back the raw
        # urllib3 response underneath, which has no iter_chunks. Close it by hand.
        # It must be closed even when the client hangs up mid-download, or the
        # connection is never returned to the pool.
        body = obj["Body"]
        try:
            yield from body.iter_chunks(CHUNK_BYTES)
        finally:
            body.close()

    return Download(
        body=chunks(),
        length=obj["ContentLength"],
        media_type=MEDIA_TYPES.get(asset_type, "application/octet-stream"),
        filename=s3_key.rsplit("/", 1)[-1],
        content_range=obj.get("ContentRange"),
    )


def size(s3_key: str) -> int | None:
    """Object size in bytes, or None if the object store can't be reached — a
    missing size only costs the UI a label, so it must never fail the request."""
    try:
        return _client().head_object(Bucket=_bucket(), Key=s3_key)["ContentLength"]
    except Exception:
        return None


# --- adding to the bucket (see web.ingest) ----------------------------------


def exists(s3_key: str) -> bool:
    """Whether the key is taken. Distinct from size(): this one must tell a
    genuine 404 from a store it could not reach, because the caller is deciding
    whether to overwrite somebody's book."""
    try:
        _client().head_object(Bucket=_bucket(), Key=s3_key)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "NoSuchBucket", "404"):
            return False
        raise


def _fraction_reporter(total: int | None, on_progress) -> Callable[[int], None] | None:
    """Adapt boto3's Callback -- which is handed a byte count per chunk, not a
    running total -- to the 0.0-1.0 fraction the UI wants. Returns None when
    there is nothing to report against, so a missing size costs a progress bar
    and never the transfer."""
    if on_progress is None or not total:
        return None
    moved = 0

    def report(chunk_bytes: int) -> None:
        nonlocal moved
        moved += chunk_bytes
        on_progress(min(1.0, moved / total))

    return report


def upload(path: str, s3_key: str, on_progress=None) -> None:
    """Put a local file in the bucket. upload_file multiparts anything large on
    its own, which an audiobook always is."""
    reporter = _fraction_reporter(os.path.getsize(path), on_progress)
    _client().upload_file(path, _bucket(), s3_key, Callback=reporter)


def download_to(s3_key: str, path: str, on_progress=None) -> None:
    reporter = _fraction_reporter(size(s3_key), on_progress)
    try:
        _client().download_file(_bucket(), s3_key, path, Callback=reporter)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            raise NotFound(s3_key) from e
        raise


def list_book_keys() -> dict[str, list[str]]:
    """Every epub and m4b key in the bucket, by asset type.

    Paginated: the bucket holds thousands of objects and a bare list_objects_v2
    silently stops at 1000, which would make new files invisible forever.
    """
    found: dict[str, list[str]] = {"epub": [], "m4b": []}
    paginator = _client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=_bucket()):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            lower = key.lower()
            if lower.endswith(".epub"):
                found["epub"].append(key)
            elif lower.endswith(".m4b"):
                found["m4b"].append(key)
    return found
