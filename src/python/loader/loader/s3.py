"""Object store access: list the bucket, download an object.

Same bucket and credentials as util/s3.sh and the web app; s3_key is a flat
object key at the bucket root.
"""

import os
from functools import cache

import boto3
from botocore.config import Config

EPUB_SUFFIX = ".epub"
M4B_SUFFIX = ".m4b"


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
            connect_timeout=10,
            # An m4b runs to hundreds of MB; the default 60s read timeout aborts
            # mid-download on a slow link.
            read_timeout=300,
            retries={"max_attempts": 3},
        ),
    )


def _bucket() -> str:
    return os.environ["OBJECT_STORE_BUCKET_NAME"]


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
            if lower.endswith(EPUB_SUFFIX):
                found["epub"].append(key)
            elif lower.endswith(M4B_SUFFIX):
                found["m4b"].append(key)
    return found


def download(s3_key: str, dest: str) -> None:
    _client().download_file(_bucket(), s3_key, dest)
