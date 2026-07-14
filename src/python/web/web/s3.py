"""Presigned download URLs for the book files. The bucket is the same one
util/s3.sh talks to; s3_key is a flat object key at the bucket root."""

import os
from functools import cache

import boto3
from botocore.config import Config

PRESIGN_TTL_S = 300


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
            read_timeout=10,
            retries={"max_attempts": 2},
        ),
    )


def _bucket() -> str:
    return os.environ["OBJECT_STORE_BUCKET_NAME"]


def presign(s3_key: str) -> str:
    filename = s3_key.rsplit("/", 1)[-1]
    return _client().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": _bucket(),
            "Key": s3_key,
            "ResponseContentDisposition": f'attachment; filename="{filename}"',
        },
        ExpiresIn=PRESIGN_TTL_S,
    )


def size(s3_key: str) -> int | None:
    """Object size in bytes, or None if the object store can't be reached — a
    missing size only costs the UI a label, so it must never fail the request."""
    try:
        return _client().head_object(Bucket=_bucket(), Key=s3_key)["ContentLength"]
    except Exception:
        return None
