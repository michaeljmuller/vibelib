#!/usr/bin/env python3
"""
Loader: iterates all EPUBs and M4Bs in S3, extracts metadata and cover images,
and writes everything to PostgreSQL. Downloads and parses files concurrently,
then writes results to the database serially from the main thread.
"""

import concurrent.futures
import json
import os
import subprocess
import tempfile
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import boto3
import psycopg2
from botocore.client import Config
from mutagen.mp4 import MP4, MP4Cover

# ── Configuration ────────────────────────────────────────────────────────────

S3_ENDPOINT = os.environ["OBJECT_STORE_BUCKET_ENDPOINT"]
S3_KEY      = os.environ["OBJECT_STORE_ACCESS_KEY_ID"]
S3_SECRET   = os.environ["OBJECT_STORE_SECRET_ACCESS_KEY"]
S3_BUCKET   = os.environ["OBJECT_STORE_BUCKET_NAME"]
S3_REGION   = os.environ["OBJECT_STORE_BUCKET_REGION"]

PG_HOST     = os.environ.get("POSTGRES_HOST", "db")
PG_PORT     = int(os.environ.get("POSTGRES_PORT", "5432"))
PG_DB       = os.environ.get("POSTGRES_DB", "vibelib")
PG_USER     = os.environ.get("POSTGRES_USER", "vibelib")
PG_PASSWORD = os.environ.get("POSTGRES_PASSWORD")

COVERS_DIR       = os.environ.get("COVERS_DIR", "/covers")
SCHEMA_FILE      = "/app/schema.sql"
DOWNLOAD_WORKERS = int(os.environ.get("DOWNLOAD_WORKERS", "8"))
POLL_INTERVAL    = int(os.environ.get("POLL_INTERVAL", "300"))
SKIP_SCHEMA      = os.environ.get("SKIP_SCHEMA", "").lower() in ("1", "true", "yes")

# ── S3 ────────────────────────────────────────────────────────────────────────

def make_s3():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{S3_ENDPOINT}",
        aws_access_key_id=S3_KEY,
        aws_secret_access_key=S3_SECRET,
        region_name=S3_REGION,
        config=Config(signature_version="s3v4"),
    )

# ── Database ──────────────────────────────────────────────────────────────────

def wait_for_db():
    print("Waiting for database...", flush=True)
    for _ in range(30):
        try:
            conn = psycopg2.connect(
                host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                user=PG_USER, password=PG_PASSWORD,
            )
            conn.close()
            print("Database ready.", flush=True)
            return
        except psycopg2.OperationalError:
            time.sleep(2)
    raise RuntimeError("Database did not become ready.")

def connect_db():
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=PG_DB,
        user=PG_USER, password=PG_PASSWORD,
    )

def apply_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables WHERE table_name = 'epubs'
            )
        """)
        if cur.fetchone()[0]:
            print("Schema already applied.", flush=True)
            return
        print("Applying schema...", flush=True)
        with open(SCHEMA_FILE) as f:
            cur.execute(f.read())
    conn.commit()
    print("Schema applied.", flush=True)

# ── EPUB parsing ──────────────────────────────────────────────────────────────

OPF_NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc":  "http://purl.org/dc/elements/1.1/",
}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

def find_opf_path(zf):
    try:
        root = ET.fromstring(zf.read("META-INF/container.xml").decode("utf-8", errors="replace"))
        for el in root.iter():
            if el.tag.endswith("rootfile"):
                return el.attrib.get("full-path")
    except Exception:
        pass
    return next((n for n in zf.namelist() if n.endswith(".opf")), None)

def opf_base(opf_path):
    parts = opf_path.split("/")
    return "/".join(parts[:-1]) + "/" if len(parts) > 1 else ""

def resolve_href(base, href):
    return base + href if not href.startswith("/") else href.lstrip("/")

def dc_text(md, tag):
    el = md.find(f"dc:{tag}", OPF_NS)
    if el is None:
        el = md.find(f"{{{OPF_NS['dc']}}}{tag}")
    return el.text.strip() if el is not None and el.text else None

def parse_epub(path):
    """Returns (meta, authors, cover_bytes, cover_ext)."""
    meta, authors, cover_bytes, cover_ext = {}, [], None, None
    try:
        with zipfile.ZipFile(path) as zf:
            opf_path = find_opf_path(zf)
            if not opf_path:
                return meta, authors, None, None

            base = opf_base(opf_path)
            root = ET.fromstring(zf.read(opf_path).decode("utf-8", errors="replace"))
            md = root.find("opf:metadata", OPF_NS)
            if md is None:
                md = root.find("metadata")
            if md is None:
                return meta, authors, None, None

            meta["title"]          = dc_text(md, "title")
            meta["publisher"]      = dc_text(md, "publisher")
            meta["published_date"] = dc_text(md, "date")
            meta["language"]       = dc_text(md, "language")
            meta["description"]    = dc_text(md, "description")
            meta["subject"]        = dc_text(md, "subject")

            # Authors — repeated dc:creator elements
            creators = (
                md.findall("dc:creator", OPF_NS) or
                md.findall(f"{{{OPF_NS['dc']}}}creator")
            )
            for i, el in enumerate(creators, 1):
                name = el.text.strip() if el.text else None
                role = el.attrib.get(f"{{{OPF_NS['opf']}}}role", "author")
                if name:
                    authors.append((name, role, i))

            # Series from Calibre meta tags
            for el in list(md.findall("opf:meta", OPF_NS)) + list(md.findall("meta")):
                name = el.attrib.get("name", "")
                if name == "calibre:series":
                    meta["series"] = el.attrib.get("content")
                elif name == "calibre:series_index":
                    try:
                        meta["series_position"] = float(el.attrib.get("content", ""))
                    except ValueError:
                        pass

            # Identifiers
            all_ids = (
                md.findall("dc:identifier", OPF_NS) +
                md.findall(f"{{{OPF_NS['dc']}}}identifier")
            )
            for el in all_ids:
                scheme = (
                    el.attrib.get("scheme") or
                    el.attrib.get(f"{{{OPF_NS['opf']}}}scheme", "")
                ).lower()
                val = el.text.strip() if el.text else ""
                if not val:
                    continue
                if "asin" in scheme or "asin" in val.lower():
                    meta.setdefault("asin", val.split(":")[-1])
                elif "isbn" in scheme:
                    meta.setdefault("isbn", val)
                else:
                    meta.setdefault("identifier", val)

            # Cover image — build manifest then find cover
            manifest = {}
            for item in root.iter():
                if item.tag.endswith("}item") or item.tag == "item":
                    manifest[item.attrib.get("id", "")] = {
                        "href":       resolve_href(base, item.attrib.get("href", "")),
                        "properties": item.attrib.get("properties", ""),
                    }

            cover_path = None
            # EPUB3: properties=cover-image
            for item in manifest.values():
                if "cover-image" in item["properties"]:
                    cover_path = item["href"]
                    break
            # EPUB2: meta name=cover -> manifest id
            if not cover_path:
                for el in md:
                    if el.attrib.get("name") == "cover":
                        item = manifest.get(el.attrib.get("content", ""))
                        if item:
                            cover_path = item["href"]
                            break

            if cover_path:
                meta["cover_path"] = cover_path
                ext = Path(cover_path).suffix.lower()
                if ext in IMAGE_EXTS:
                    try:
                        cover_bytes = zf.read(cover_path)
                        cover_ext = "jpg" if ext in (".jpg", ".jpeg") else ext.lstrip(".")
                    except KeyError:
                        pass

    except Exception as e:
        print(f"  EPUB parse error: {e}", flush=True)

    return meta, authors, cover_bytes, cover_ext

# ── M4B parsing ───────────────────────────────────────────────────────────────

def parse_m4b(path):
    """Returns (meta, cover_bytes, cover_ext, chapters)."""
    meta, cover_bytes, cover_ext, chapters = {}, None, None, []
    try:
        audio = MP4(path)
        info  = audio.info
        meta["duration_s"]   = round(info.length)
        meta["bitrate_kbps"] = info.bitrate // 1000
        meta["sample_rate"]  = info.sample_rate
        meta["channels"]     = info.channels

        tags = audio.tags or {}

        def tag(key):
            val = tags.get(key)
            if not val:
                return None
            v = val[0]
            if isinstance(v, bytes):
                return v.decode("utf-8", errors="replace").strip()
            return str(v).strip()

        meta["title"]     = tag("\xa9nam")
        meta["artist"]    = tag("\xa9ART")
        meta["narrator"]  = tag("\xa9wrt")
        meta["album"]     = tag("\xa9alb")
        meta["date"]      = tag("\xa9day")
        meta["genre"]     = tag("\xa9gen")
        meta["comment"]   = tag("\xa9cmt")
        meta["copyright"] = tag("cprt")

        desc = tags.get("ldes") or tags.get("desc")
        if desc:
            v = desc[0]
            meta["description"] = (
                v.decode("utf-8", errors="replace") if isinstance(v, bytes) else str(v)
            ).strip()

        # ASIN from freeform iTunes atom
        asin_raw = tags.get("----:com.apple.iTunes:ASIN")
        if asin_raw:
            v = asin_raw[0]
            meta["asin"] = (v.decode("utf-8", errors="replace") if isinstance(v, bytes) else str(v)).strip()

        meta["has_cover"] = "covr" in tags

        if "covr" in tags:
            img = tags["covr"][0]
            cover_bytes = bytes(img)
            cover_ext = "jpg" if img.imageformat == MP4Cover.FORMAT_JPEG else "png"

        # Chapters via ffprobe
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_chapters", path],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            for i, ch in enumerate(json.loads(result.stdout).get("chapters", []), 1):
                chapters.append((
                    i,
                    ch.get("tags", {}).get("title"),
                    int(float(ch["start_time"]) * 1000),
                ))

    except Exception as e:
        print(f"  M4B parse error: {e}", flush=True)

    return meta, cover_bytes, cover_ext, chapters

# ── Database inserts ──────────────────────────────────────────────────────────

def insert_epub(conn, s3_key, meta, authors):
    params = {
        "s3_key":          s3_key,
        "asin":            meta.get("asin"),
        "isbn":            meta.get("isbn"),
        "title":           meta.get("title") or Path(s3_key).stem,
        "publisher":       meta.get("publisher"),
        "published_date":  meta.get("published_date"),
        "language":        meta.get("language"),
        "description":     meta.get("description"),
        "series":          meta.get("series"),
        "series_position": meta.get("series_position"),
        "identifier":      meta.get("identifier"),
        "subject":         meta.get("subject"),
        "cover_path":      meta.get("cover_path"),
    }
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO epubs (
                s3_key, asin, isbn, title, publisher, published_date,
                language, description, series, series_position,
                identifier, subject, cover_path
            ) VALUES (
                %(s3_key)s, %(asin)s, %(isbn)s, %(title)s, %(publisher)s,
                %(published_date)s, %(language)s, %(description)s,
                %(series)s, %(series_position)s, %(identifier)s,
                %(subject)s, %(cover_path)s
            )
            ON CONFLICT (s3_key) DO UPDATE SET
                asin            = EXCLUDED.asin,
                isbn            = EXCLUDED.isbn,
                title           = EXCLUDED.title,
                publisher       = EXCLUDED.publisher,
                published_date  = EXCLUDED.published_date,
                language        = EXCLUDED.language,
                description     = EXCLUDED.description,
                series          = EXCLUDED.series,
                series_position = EXCLUDED.series_position,
                identifier      = EXCLUDED.identifier,
                subject         = EXCLUDED.subject,
                cover_path      = EXCLUDED.cover_path,
                updated_at      = now()
            RETURNING id
        """, params)
        epub_id = cur.fetchone()[0]
        cur.execute("DELETE FROM epub_authors WHERE epub_id = %s", (epub_id,))
        for name, role, position in authors:
            cur.execute(
                "INSERT INTO epub_authors (epub_id, author, role, position) VALUES (%s, %s, %s, %s)",
                (epub_id, name, role, position),
            )
    conn.commit()
    return epub_id

def insert_m4b(conn, s3_key, meta, chapters):
    params = {
        "s3_key":       s3_key,
        "asin":         meta.get("asin"),
        "title":        meta.get("title") or Path(s3_key).stem,
        "artist":       meta.get("artist"),
        "narrator":     meta.get("narrator"),
        "album":        meta.get("album"),
        "date":         meta.get("date"),
        "description":  meta.get("description"),
        "comment":      meta.get("comment"),
        "genre":        meta.get("genre"),
        "copyright":    meta.get("copyright"),
        "has_cover":    meta.get("has_cover", False),
        "duration_s":   meta.get("duration_s"),
        "bitrate_kbps": meta.get("bitrate_kbps"),
        "sample_rate":  meta.get("sample_rate"),
        "channels":     meta.get("channels"),
    }
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO m4bs (
                s3_key, asin, title, artist, narrator, album, date,
                description, comment, genre, copyright, has_cover,
                duration_s, bitrate_kbps, sample_rate, channels
            ) VALUES (
                %(s3_key)s, %(asin)s, %(title)s, %(artist)s, %(narrator)s,
                %(album)s, %(date)s, %(description)s, %(comment)s,
                %(genre)s, %(copyright)s, %(has_cover)s,
                %(duration_s)s, %(bitrate_kbps)s, %(sample_rate)s, %(channels)s
            )
            ON CONFLICT (s3_key) DO UPDATE SET
                asin         = EXCLUDED.asin,
                title        = EXCLUDED.title,
                artist       = EXCLUDED.artist,
                narrator     = EXCLUDED.narrator,
                album        = EXCLUDED.album,
                date         = EXCLUDED.date,
                description  = EXCLUDED.description,
                comment      = EXCLUDED.comment,
                genre        = EXCLUDED.genre,
                copyright    = EXCLUDED.copyright,
                has_cover    = EXCLUDED.has_cover,
                duration_s   = EXCLUDED.duration_s,
                bitrate_kbps = EXCLUDED.bitrate_kbps,
                sample_rate  = EXCLUDED.sample_rate,
                channels     = EXCLUDED.channels,
                updated_at   = now()
            RETURNING id
        """, params)
        m4b_id = cur.fetchone()[0]
        cur.execute("DELETE FROM m4b_chapters WHERE m4b_id = %s", (m4b_id,))
        for position, title, start_ms in chapters:
            cur.execute(
                "INSERT INTO m4b_chapters (m4b_id, position, title, start_ms) VALUES (%s, %s, %s, %s)",
                (m4b_id, position, title, start_ms),
            )
    conn.commit()
    return m4b_id

# ── Cover images ──────────────────────────────────────────────────────────────

def save_cover(data, kind, record_id, ext):
    dest_dir = Path(COVERS_DIR) / kind
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / f"{record_id}.{ext}").write_bytes(data)

# ── Worker ────────────────────────────────────────────────────────────────────

def process_key(key):
    """Download and parse one file. Each call creates its own S3 client so
    this function is safe to run concurrently from multiple threads."""
    s3   = make_s3()
    kind = "epub" if key.lower().endswith(".epub") else "m4b"
    print(f"  [{kind}] downloading {Path(key).name}...", flush=True)
    with tempfile.NamedTemporaryFile(suffix=f".{kind}", delete=True) as tmp:
        s3.download_file(S3_BUCKET, key, tmp.name)
        print(f"  [{kind}] parsing {Path(key).name}...", flush=True)
        if kind == "epub":
            meta, authors, cover_bytes, cover_ext = parse_epub(tmp.name)
            return (key, kind, meta, authors, cover_bytes, cover_ext)
        else:
            meta, cover_bytes, cover_ext, chapters = parse_m4b(tmp.name)
            return (key, kind, meta, cover_bytes, cover_ext, chapters)

# ── Main ──────────────────────────────────────────────────────────────────────

def run_once():
    conn = connect_db()
    s3 = make_s3()

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Listing bucket...", flush=True)
    paginator = s3.get_paginator("list_objects_v2")
    objects = []
    for page in paginator.paginate(Bucket=S3_BUCKET):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            lower = key.lower()
            if lower.endswith(".epub") or lower.endswith(".m4b"):
                objects.append(key)

    epub_total = sum(1 for k in objects if k.lower().endswith(".epub"))
    m4b_total  = sum(1 for k in objects if k.lower().endswith(".m4b"))
    total = len(objects)

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM epubs")
        db_epubs = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM m4bs")
        db_m4bs = cur.fetchone()[0]

    # Pre-filter already-loaded keys in the main thread before spawning workers
    keys_to_process = []
    for key in objects:
        kind  = "epub" if key.lower().endswith(".epub") else "m4b"
        table = "epubs" if kind == "epub" else "m4bs"
        with conn.cursor() as cur:
            cur.execute(f"SELECT 1 FROM {table} WHERE s3_key = %s", (key,))
            if not cur.fetchone():
                keys_to_process.append(key)

    new_count = len(keys_to_process)
    print(
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
        f"Bucket: {total} objects ({epub_total} epubs, {m4b_total} m4bs). "
        f"DB: {db_epubs} epubs, {db_m4bs} m4bs. "
        f"{new_count} new.",
        flush=True,
    )

    if not new_count:
        conn.close()
        return

    print(f"Downloading {new_count} files with {DOWNLOAD_WORKERS} workers.", flush=True)

    epub_count = m4b_count = error_count = 0
    completed = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as executor:
        future_to_key = {executor.submit(process_key, key): key for key in keys_to_process}
        for future in concurrent.futures.as_completed(future_to_key):
            key = future_to_key[future]
            completed += 1
            kind = "epub" if key.lower().endswith(".epub") else "m4b"
            print(f"[{completed}/{new_count}] [{kind}] {key}", flush=True)
            try:
                result = future.result()
                _, kind, *rest = result
                if kind == "epub":
                    meta, authors, cover_bytes, cover_ext = rest
                    record_id = insert_epub(conn, key, meta, authors)
                    if cover_bytes:
                        save_cover(cover_bytes, "epub", record_id, cover_ext)
                        print(f"  cover saved -> epub/{record_id}.{cover_ext}", flush=True)
                    epub_count += 1
                else:
                    meta, cover_bytes, cover_ext, chapters = rest
                    record_id = insert_m4b(conn, key, meta, chapters)
                    if cover_bytes:
                        save_cover(cover_bytes, "m4b", record_id, cover_ext)
                        print(f"  cover saved -> m4b/{record_id}.{cover_ext}", flush=True)
                    m4b_count += 1
                print(f"  done -> id={record_id}", flush=True)
            except Exception as e:
                print(f"  ERROR: {e}", flush=True)
                error_count += 1
                conn.rollback()

    conn.close()
    print(
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
        f"Run complete: {epub_count} epubs, {m4b_count} m4bs, {error_count} errors.",
        flush=True,
    )


def main():
    wait_for_db()
    if not SKIP_SCHEMA:
        conn = connect_db()
        apply_schema(conn)
        conn.close()

    print(f"Polling every {POLL_INTERVAL}s. Set POLL_INTERVAL to change.", flush=True)
    while True:
        run_once()
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
