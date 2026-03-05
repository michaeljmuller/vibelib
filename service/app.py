import logging
import os
import time
from functools import wraps

import requests
from botocore.exceptions import ClientError
from flask import Flask, Response, g, jsonify, request

from common.amazon import scrape_amazon_metadata
from common.epub import extract_epub_cover, extract_epub_metadata, get_epub_asin
from common.s3 import get_cached_epub, get_s3_client

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
logger = logging.getLogger(__name__)

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


@app.route("/api/ebooks/<path:s3_key>/metadata", methods=["GET"])
@auth_required
def get_ebook_metadata(s3_key):
    """Return metadata extracted from an EPUB file."""
    if not s3_key.lower().endswith(".epub"):
        return jsonify({"error": "Only EPUB files are supported"}), 400

    try:
        epub_path, _ = get_cached_epub(s3_key)
        metadata = extract_epub_metadata(epub_path)
        metadata["s3_key"] = s3_key
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        app.logger.error(f"Error extracting metadata from {s3_key}: {e}")
        return jsonify({"error": "Failed to extract metadata"}), 500

    return jsonify(metadata)


@app.route("/api/ebooks/<path:s3_key>/amazon", methods=["GET"])
@auth_required
def get_ebook_amazon(s3_key):
    """Return Amazon metadata for an EPUB file."""
    if not s3_key.lower().endswith(".epub"):
        return jsonify({"error": "Only EPUB files are supported"}), 400

    try:
        epub_path, _ = get_cached_epub(s3_key)
        asin = get_epub_asin(epub_path)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        app.logger.error(f"Error reading EPUB for ASIN lookup {s3_key}: {e}")
        return jsonify({"error": "Failed to read EPUB"}), 500

    logger.info("Amazon lookup for %s: ASIN=%r", s3_key, asin)
    if not asin:
        return jsonify({"error": "No ASIN found for this book"}), 404

    try:
        result = scrape_amazon_metadata(asin)
        return jsonify(result)
    except Exception as e:
        app.logger.error(f"Amazon scrape failed for ASIN {asin}: {e}")
        return jsonify({"error": "Failed to scrape Amazon metadata"}), 502


@app.route("/api/ebooks/<path:s3_key>/cover", methods=["GET"])
@auth_required
def get_ebook_cover(s3_key):
    """Return cover image from an EPUB file."""
    if not s3_key.lower().endswith(".epub"):
        return jsonify({"error": "Only EPUB files are supported"}), 400

    try:
        epub_path, _ = get_cached_epub(s3_key)
        image_data, content_type = extract_epub_cover(epub_path)

        if image_data is None:
            return jsonify({"error": "No cover image found"}), 404

        return Response(image_data, mimetype=content_type)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        app.logger.error(f"Error extracting cover from {s3_key}: {e}")
        return jsonify({"error": "Failed to extract cover image"}), 500


@app.route("/api/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
