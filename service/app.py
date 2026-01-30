import hashlib
import os
import re
import time
from functools import wraps
from pathlib import Path

import boto3
import requests
from botocore.exceptions import ClientError
from ebooklib import epub
from flask import Flask, Response, g, jsonify, request

app = Flask(__name__)

S3_BUCKET = os.environ.get("S3_BUCKET")
DEFAULT_LIMIT = 100

# Simple token cache: {token: (user_info, expiry_time)}
TOKEN_CACHE = {}
CACHE_TTL = 300  # 5 minutes

# EPUB cache directory
EPUB_CACHE_DIR = Path(os.environ.get("EPUB_CACHE_DIR", "/tmp/epub_cache"))
EPUB_CACHE_DIR.mkdir(parents=True, exist_ok=True)


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


def get_cache_path(s3_key):
    """Get the cache directory path for an S3 key."""
    # Use hash of key to avoid filesystem issues with special characters
    key_hash = hashlib.sha256(s3_key.encode()).hexdigest()[:16]
    return EPUB_CACHE_DIR / key_hash


def get_cached_epub(s3_key):
    """
    Get cached EPUB, downloading if needed or if ETag changed.
    Returns tuple of (epub_path, was_downloaded).
    """
    if not S3_BUCKET:
        raise ValueError("S3_BUCKET not configured")

    cache_path = get_cache_path(s3_key)
    epub_file = cache_path / "book.epub"
    etag_file = cache_path / "etag"

    s3 = get_s3_client()

    # Get current ETag from S3
    try:
        head_response = s3.head_object(Bucket=S3_BUCKET, Key=s3_key)
        current_etag = head_response.get("ETag", "").strip('"')
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            raise FileNotFoundError(f"S3 object not found: {s3_key}")
        raise

    # Check if we have a valid cached version
    if epub_file.exists() and etag_file.exists():
        cached_etag = etag_file.read_text().strip()
        if cached_etag == current_etag:
            return epub_file, False

    # Download and cache
    cache_path.mkdir(parents=True, exist_ok=True)
    response = s3.get_object(Bucket=S3_BUCKET, Key=s3_key)
    epub_data = response["Body"].read()

    epub_file.write_bytes(epub_data)
    etag_file.write_text(current_etag)

    return epub_file, True


def extract_isbn_from_content(book):
    """
    Extract ISBN from copyright page content.
    Returns a single ISBN string, preferring ebook ISBN if multiple found.
    """
    # ISBN-13: starts with 978 or 979, 13 digits total (with optional hyphens/spaces)
    # ISBN-10: 10 digits (last can be X), with optional hyphens/spaces
    isbn_pattern = re.compile(
        r'(?:ISBN[-:\s]*)?'
        r'(97[89](?:[-\s]?\d){10}|'  # ISBN-13: 978/979 + 10 more digits
        r'(?:\d[-\s]?){9}[\dXx])',  # ISBN-10: 9 digits + check digit
        re.IGNORECASE
    )

    # Keywords indicating copyright/legal pages
    copyright_keywords = ['copyright', 'colophon', 'imprint', 'legal', 'rights']
    # Keywords indicating ebook format (check text after ISBN)
    ebook_keywords = ['ebook', 'e-book', 'epub', 'digital', 'electronic']

    found_isbns = []  # List of (isbn, is_ebook)

    for item in book.get_items():
        if item.media_type not in ['application/xhtml+xml', 'text/html']:
            continue

        item_name = (item.get_name() or '').lower()
        item_id = (item.get_id() or '').lower()

        # Check if this looks like a copyright page
        is_copyright_page = any(
            kw in item_name or kw in item_id
            for kw in copyright_keywords
        )

        try:
            content = item.get_content().decode('utf-8', errors='ignore')
        except Exception:
            continue

        # Also check content for copyright indicators
        content_lower = content.lower()
        if not is_copyright_page:
            is_copyright_page = 'copyright' in content_lower or '©' in content

        if not is_copyright_page:
            continue

        # Find all ISBNs and check text immediately following for format indicator
        for match in isbn_pattern.finditer(content):
            isbn_raw = match.group(1)
            # Normalize: remove hyphens and spaces
            isbn = re.sub(r'[-\s]', '', isbn_raw)

            # Look at text after ISBN until end of line or closing tag
            after_pos = match.end()
            end_pos = after_pos + 50
            # Find first newline or < (tag start) to limit context to same line
            after_text = content[after_pos:end_pos]
            for delim in ['\n', '<', '\r']:
                delim_pos = after_text.find(delim)
                if delim_pos != -1:
                    after_text = after_text[:delim_pos]
            after_isbn = after_text.lower()

            # Check if this ISBN is marked as ebook
            is_ebook = any(kw in after_isbn for kw in ebook_keywords)

            found_isbns.append((isbn, is_ebook))

    if not found_isbns:
        return None

    # Prefer ebook ISBN
    ebook_isbns = [isbn for isbn, is_ebook in found_isbns if is_ebook]
    if ebook_isbns:
        return ebook_isbns[0]

    # Otherwise return first ISBN found
    return found_isbns[0][0]


def extract_epub_metadata(epub_path):
    """Extract metadata from an EPUB file."""
    book = epub.read_epub(str(epub_path))

    metadata = {
        "title": None,
        "authors": [],
        "description": None,
        "language": None,
        "publisher": None,
        "date": None,
        "identifiers": {},
        "subjects": [],
        "rights": None,
    }

    # Title
    titles = book.get_metadata("DC", "title")
    if titles:
        metadata["title"] = titles[0][0]

    # Authors
    creators = book.get_metadata("DC", "creator")
    for creator in creators:
        metadata["authors"].append(creator[0])

    # Description
    descriptions = book.get_metadata("DC", "description")
    if descriptions:
        metadata["description"] = descriptions[0][0]

    # Language
    languages = book.get_metadata("DC", "language")
    if languages:
        metadata["language"] = languages[0][0]

    # Publisher
    publishers = book.get_metadata("DC", "publisher")
    if publishers:
        metadata["publisher"] = publishers[0][0]

    # Date
    dates = book.get_metadata("DC", "date")
    if dates:
        metadata["date"] = dates[0][0]

    # Identifiers (ISBN, ASIN, etc.)
    identifiers = book.get_metadata("DC", "identifier")
    for identifier in identifiers:
        value = identifier[0]
        attrs = identifier[1] if len(identifier) > 1 else {}
        scheme = attrs.get("scheme", attrs.get("{http://www.idpf.org/2007/opf}scheme"))

        # Try to extract scheme from URN prefix if not in attributes
        if not scheme and value:
            if value.startswith("urn:"):
                # Parse urn:scheme:value format
                parts = value.split(":", 2)
                if len(parts) >= 3:
                    scheme = parts[1]
                    value = parts[2]

        if scheme:
            metadata["identifiers"][scheme.lower()] = value
        else:
            metadata["identifiers"]["unknown"] = value

    # Subjects/tags
    subjects = book.get_metadata("DC", "subject")
    for subject in subjects:
        metadata["subjects"].append(subject[0])

    # Rights
    rights = book.get_metadata("DC", "rights")
    if rights:
        metadata["rights"] = rights[0][0]

    # Try to extract ISBN from copyright page content
    content_isbn = extract_isbn_from_content(book)
    if content_isbn:
        metadata["isbn"] = format_isbn(content_isbn)
    elif "isbn" in metadata["identifiers"]:
        # Fall back to ISBN from DC metadata
        metadata["isbn"] = format_isbn(metadata["identifiers"]["isbn"])

    return metadata


def format_isbn(isbn):
    """Format ISBN for display (3-1-4-4-1 for ISBN-13, 1-4-4-1 for ISBN-10)."""
    # Remove any existing hyphens/spaces
    isbn = re.sub(r'[-\s]', '', isbn)

    if len(isbn) == 13:
        # ISBN-13: 978-X-XXXX-XXXX-X
        return f"{isbn[:3]}-{isbn[3]}-{isbn[4:8]}-{isbn[8:12]}-{isbn[12]}"
    elif len(isbn) == 10:
        # ISBN-10: X-XXXX-XXXX-X
        return f"{isbn[0]}-{isbn[1:5]}-{isbn[5:9]}-{isbn[9]}"
    else:
        # Return as-is if unexpected length
        return isbn


def extract_epub_cover(epub_path):
    """Extract cover image from an EPUB file. Returns (image_data, content_type) or (None, None)."""
    book = epub.read_epub(str(epub_path))

    # Try to find cover image via metadata
    cover_id = None

    # Check for cover in metadata - handles two formats:
    # Format 1: [('cover-image-id', {})]
    # Format 2: [(None, {'name': 'cover', 'content': 'image.jpg'})]
    meta_covers = book.get_metadata("OPF", "cover")
    if meta_covers:
        if meta_covers[0][0]:
            cover_id = meta_covers[0][0]
        elif isinstance(meta_covers[0][1], dict):
            cover_id = meta_covers[0][1].get('content')

    # Look for cover item by ID or name matching cover_id
    if cover_id:
        for item in book.get_items():
            if item.get_id() == cover_id or item.get_name() == cover_id:
                if item.media_type and item.media_type.startswith("image/"):
                    return item.get_content(), item.media_type

    # Check by common cover item IDs/names
    for item in book.get_items():
        item_id = item.get_id().lower() if item.get_id() else ""
        item_name = item.get_name().lower() if item.get_name() else ""

        if any(x in item_id for x in ["cover", "cover-image"]) or \
           any(x in item_name for x in ["cover", "cover-image"]):
            if item.media_type and item.media_type.startswith("image/"):
                return item.get_content(), item.media_type

    # Fallback: find first image in spine or items
    for item in book.get_items():
        if item.media_type and item.media_type.startswith("image/"):
            name = item.get_name().lower() if item.get_name() else ""
            if "cover" in name:
                return item.get_content(), item.media_type

    return None, None


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
        return jsonify(metadata)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        app.logger.error(f"Error extracting metadata from {s3_key}: {e}")
        return jsonify({"error": "Failed to extract metadata"}), 500


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
