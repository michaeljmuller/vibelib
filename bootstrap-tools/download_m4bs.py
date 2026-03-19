import os
import boto3
from botocore.client import Config

ENDPOINT = os.environ["OBJECT_STORE_BUCKET_ENDPOINT"]
ACCESS_KEY = os.environ["OBJECT_STORE_ACCESS_KEY_ID"]
SECRET_KEY = os.environ["OBJECT_STORE_SECRET_ACCESS_KEY"]
BUCKET = os.environ["OBJECT_STORE_BUCKET_NAME"]
REGION = os.environ["OBJECT_STORE_BUCKET_REGION"]
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/output")
LIMIT = int(os.environ.get("M4B_LIMIT", "15"))

s3 = boto3.client(
    "s3",
    endpoint_url=f"https://{ENDPOINT}",
    aws_access_key_id=ACCESS_KEY,
    aws_secret_access_key=SECRET_KEY,
    region_name=REGION,
    config=Config(signature_version="s3v4"),
)

os.makedirs(OUTPUT_DIR, exist_ok=True)

paginator = s3.get_paginator("list_objects_v2")
downloaded = 0

for page in paginator.paginate(Bucket=BUCKET):
    for obj in page.get("Contents", []):
        if downloaded >= LIMIT:
            break
        key = obj["Key"]
        if not key.lower().endswith(".m4b"):
            continue
        filename = os.path.basename(key)
        dest = os.path.join(OUTPUT_DIR, filename)
        print(f"Downloading {key} -> {dest}")
        s3.download_file(BUCKET, key, dest)
        downloaded += 1
    if downloaded >= LIMIT:
        break

print(f"Done. Downloaded {downloaded} m4b(s) to {OUTPUT_DIR}.")
