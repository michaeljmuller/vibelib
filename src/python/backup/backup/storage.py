"""The backup bucket. It sits beside the book bucket in the same Hetzner project,
so it uses the same OBJECT_STORE_* endpoint, region and credentials as
web/s3.py, and adds only its own bucket name.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REQUIRED = (
    "OBJECT_STORE_BACKUP_BUCKET_NAME",
    "OBJECT_STORE_BUCKET_ENDPOINT",
    "OBJECT_STORE_ACCESS_KEY_ID",
    "OBJECT_STORE_SECRET_ACCESS_KEY",
)


def missing_settings(env: dict[str, str]) -> list[str]:
    return [name for name in REQUIRED if not env.get(name)]


def endpoint_url(env: dict[str, str]) -> str:
    # web/s3.py accepts a bare host, so the shared setting may be one.
    endpoint = env.get("OBJECT_STORE_BUCKET_ENDPOINT", "")
    if endpoint and not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"
    return endpoint


def config_summary(env: dict[str, str]) -> list[tuple[str, str]]:
    """Label and value pairs for display. The secret key is never included."""
    key = env.get("OBJECT_STORE_ACCESS_KEY_ID", "")
    return [
        ("endpoint", endpoint_url(env) or "(unset)"),
        ("bucket", env.get("OBJECT_STORE_BACKUP_BUCKET_NAME") or "(unset)"),
        ("region", env.get("OBJECT_STORE_BUCKET_REGION") or "(unset)"),
        ("access key", f"{key[:4]}..." if key else "(unset)"),
        ("keep", env.get("BACKUP_RETAIN", "30")),
    ]


def _code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", ""))
        if code:
            return code
    # boto3 wraps a failed upload in S3UploadFailedError, which keeps the code
    # only in its message.
    text = str(exc)
    for code in ("InvalidAccessKeyId", "SignatureDoesNotMatch", "NoSuchBucket", "AccessDenied"):
        if code in text:
            return code
    return ""


def diagnose(exc: BaseException) -> str:
    """One line of advice for the failure that just happened."""
    code = _code(exc)
    if code in ("InvalidAccessKeyId", "SignatureDoesNotMatch", "403"):
        return "The credentials are wrong. Check OBJECT_STORE_ACCESS_KEY_ID and OBJECT_STORE_SECRET_ACCESS_KEY."
    if code in ("NoSuchBucket", "404"):
        return ("The bucket does not exist at this endpoint. Check OBJECT_STORE_BACKUP_BUCKET_NAME "
                "and OBJECT_STORE_BUCKET_ENDPOINT.")
    if code == "AccessDenied":
        return "The credentials do not permit this. They need read, write and delete on the bucket."
    name = type(exc).__name__
    if name in ("EndpointConnectionError", "ConnectTimeoutError", "ConnectionError"):
        return "The endpoint did not answer. Check OBJECT_STORE_BUCKET_ENDPOINT."
    return "Read the error above, and the OBJECT_STORE_* settings in .env."


@dataclass
class ObjectStore:
    bucket: str
    client: object

    @classmethod
    def from_env(cls) -> "ObjectStore | None":
        """None when unconfigured, so a dev machine without the bucket skips."""
        env = dict(os.environ)
        if missing_settings(env):
            return None

        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url(env),
            region_name=env.get("OBJECT_STORE_BUCKET_REGION"),
            aws_access_key_id=env["OBJECT_STORE_ACCESS_KEY_ID"],
            aws_secret_access_key=env["OBJECT_STORE_SECRET_ACCESS_KEY"],
            config=Config(
                signature_version="s3v4",
                connect_timeout=10,
                read_timeout=60,
                retries={"max_attempts": 2},
            ),
        )
        return cls(bucket=env["OBJECT_STORE_BACKUP_BUCKET_NAME"], client=client)

    def upload(self, path: Path, key: str) -> None:
        self.client.upload_file(str(path), self.bucket, key)

    def download(self, key: str, path: Path) -> None:
        self.client.download_file(self.bucket, key, str(path))

    def list_keys(self) -> list[str]:
        keys: list[str] = []
        kwargs = {"Bucket": self.bucket}
        while True:
            page = self.client.list_objects_v2(**kwargs)
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
            if not page.get("IsTruncated"):
                return keys
            kwargs["ContinuationToken"] = page["NextContinuationToken"]

    def delete(self, keys: list[str]) -> None:
        for key in keys:
            self.client.delete_object(Bucket=self.bucket, Key=key)
