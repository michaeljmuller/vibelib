import os
import time
from functools import wraps

import boto3
import requests
from botocore.exceptions import ClientError
from flask import Flask, g, jsonify, request

app = Flask(__name__)

S3_BUCKET = os.environ.get("S3_BUCKET")
DEFAULT_LIMIT = 100

# Simple token cache: {token: (user_info, expiry_time)}
TOKEN_CACHE = {}
CACHE_TTL = 300  # 5 minutes


def validate_github_token(token):
    """Validate token with GitHub API, with caching."""
    now = time.time()

    # Check cache
    if token in TOKEN_CACHE:
        user_info, expiry = TOKEN_CACHE[token]
        if now < expiry:
            return user_info
        del TOKEN_CACHE[token]

    # Validate with GitHub
    try:
        resp = requests.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if resp.status_code == 200:
            user_info = resp.json()
            TOKEN_CACHE[token] = (user_info, now + CACHE_TTL)
            return user_info
    except requests.RequestException as e:
        app.logger.error(f"GitHub API error: {e}")

    return None


def auth_required(f):
    """Decorator to require valid GitHub token."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header"}), 401

        token = auth_header[7:]  # Strip "Bearer "
        user_info = validate_github_token(token)
        if not user_info:
            return jsonify({"error": "Invalid or expired token"}), 401

        g.user = user_info
        return f(*args, **kwargs)
    return decorated_function


def get_s3_client():
    """Create and return an S3 client."""
    endpoint_url = os.environ.get("S3_ENDPOINT")
    return boto3.client(
        "s3",
        region_name=os.environ.get("S3_REGION", "us-east-1"),
        endpoint_url=endpoint_url,
    )


@app.route("/api/objects", methods=["GET"])
@auth_required
def list_objects():
    """Return a limited number of S3 object keys."""
    if not S3_BUCKET:
        return jsonify({"error": "S3_BUCKET not configured"}), 500

    limit = request.args.get("limit", DEFAULT_LIMIT, type=int)
    limit = min(limit, 1000)  # Cap at 1000

    try:
        s3 = get_s3_client()
        response = s3.list_objects_v2(Bucket=S3_BUCKET, MaxKeys=limit)
        objects = [obj["Key"] for obj in response.get("Contents", [])]
        return jsonify({"objects": objects, "count": len(objects)})
    except ClientError as e:
        app.logger.error(f"S3 error: {e}")
        return jsonify({"error": "Failed to list S3 objects"}), 500


@app.route("/api/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
