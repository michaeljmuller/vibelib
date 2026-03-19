import os
from pathlib import Path
from mutagen.mp4 import MP4

M4B_DIR = os.environ.get("M4B_DIR", "/m4bs")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "/output/audiobooks-metadata.txt")

# Human-readable labels for common MP4 atoms
ATOM_LABELS = {
    "\xa9nam": "title",
    "\xa9alb": "album",
    "\xa9ART": "artist",
    "aART":    "album_artist",
    "\xa9wrt": "composer",
    "\xa9day": "date",
    "\xa9gen": "genre",
    "\xa9cmt": "comment",
    "desc":    "description",
    "ldes":    "long_description",
    "cprt":    "copyright",
    "soal":    "sort_album",
    "soar":    "sort_artist",
    "sonm":    "sort_name",
    "purl":    "podcast_url",
    "trkn":    "track_number",
    "disk":    "disk_number",
    "tmpo":    "tempo",
    "covr":    "cover_art",
    "stik":    "media_kind",
    "pgap":    "gapless",
    "pcst":    "podcast",
    "catg":    "category",
    "keyw":    "keywords",
    "hdvd":    "hd_video",
    "rtng":    "rating",
    "apID":    "apple_id",
    "sfID":    "storefront_id",
    "atID":    "artist_id",
    "plID":    "playlist_id",
    "cnID":    "catalog_id",
    "geID":    "genre_id",
    "akID":    "itunes_account_kind",
    "xid ":    "xid",
}

def format_value(key, val):
    if key == "covr":
        return f"[{len(val)} cover image(s)]"
    if isinstance(val, list):
        parts = []
        for v in val:
            if hasattr(v, 'reference_index'):
                parts.append(f"{v.reference_index}")
            elif isinstance(v, bytes):
                parts.append(v.decode("utf-8", errors="replace"))
            else:
                parts.append(str(v))
        return ", ".join(parts)
    return str(val)

def extract(path):
    result = {"file": Path(path).name, "tags": {}, "raw_keys": [], "error": None}
    try:
        audio = MP4(path)
        info = audio.info
        result["duration_s"] = round(info.length)
        result["bitrate_kbps"] = info.bitrate // 1000
        result["sample_rate"] = info.sample_rate
        result["channels"] = info.channels

        for key, val in audio.tags.items() if audio.tags else []:
            # freeform iTunes atoms: ----:com.apple.iTunes:TAGNAME
            if key.startswith("----:com.apple.iTunes:"):
                tag_name = key.split(":")[-1].lower()
                result["tags"][f"itunes:{tag_name}"] = format_value(key, val)
                continue
            label = ATOM_LABELS.get(key, key)
            result["tags"][label] = format_value(key, val)
            if key not in ATOM_LABELS:
                result["raw_keys"].append(key)

    except Exception as e:
        result["error"] = str(e)
    return result

m4bs = sorted(Path(M4B_DIR).glob("*.m4b"))
records = [extract(str(p)) for p in m4bs]

os.makedirs(Path(OUTPUT_FILE).parent, exist_ok=True)
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write(f"M4B Metadata Findings\n")
    f.write(f"{'=' * 60}\n")
    f.write(f"Inspected {len(records)} file(s).\n\n")

    for r in records:
        f.write(f"File: {r['file']}\n")
        f.write(f"{'-' * 60}\n")
        if r.get("error"):
            f.write(f"  ERROR: {r['error']}\n")
        else:
            f.write(f"  duration:    {r.get('duration_s', '?')}s\n")
            f.write(f"  bitrate:     {r.get('bitrate_kbps', '?')} kbps\n")
            f.write(f"  sample_rate: {r.get('sample_rate', '?')} Hz\n")
            f.write(f"  channels:    {r.get('channels', '?')}\n")
            for label, val in r["tags"].items():
                # truncate long values in the report
                display = val if len(str(val)) <= 120 else str(val)[:117] + "..."
                f.write(f"  {label:<20} {display}\n")
            if r["raw_keys"]:
                f.write(f"  [unrecognised atoms: {', '.join(r['raw_keys'])}]\n")
        f.write("\n")

    # Summary: which tags appeared and how often
    from collections import Counter
    tag_counts = Counter()
    for r in records:
        for label in r["tags"]:
            tag_counts[label] += 1

    f.write(f"\nTag frequency across {len(records)} file(s):\n")
    f.write(f"{'-' * 40}\n")
    for tag, count in tag_counts.most_common():
        f.write(f"  {tag:<25} {count}/{len(records)}\n")

print(f"Wrote metadata for {len(records)} audiobooks to {OUTPUT_FILE}")
