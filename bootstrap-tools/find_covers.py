import os
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

EPUB_DIR = os.environ.get("EPUB_DIR", "/epubs")

NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc":  "http://purl.org/dc/elements/1.1/",
}

def find_opf(zf):
    try:
        container = zf.read("META-INF/container.xml").decode("utf-8", errors="replace")
        root = ET.fromstring(container)
        for rf in root.iter():
            if rf.tag.endswith("rootfile"):
                return rf.attrib.get("full-path")
    except Exception:
        pass
    for name in zf.namelist():
        if name.endswith(".opf"):
            return name
    return None

def opf_dir(opf_path):
    """Return the directory prefix for paths relative to the OPF file."""
    parts = opf_path.split("/")
    return "/".join(parts[:-1]) + "/" if len(parts) > 1 else ""

def resolve(base_dir, href):
    """Resolve an href relative to the OPF directory."""
    if href.startswith("/"):
        return href.lstrip("/")
    return base_dir + href

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}

def is_image(path):
    return Path(path).suffix.lower() in IMAGE_EXTS

def find_cover(epub_path):
    result = {"file": Path(epub_path).name, "found": False, "method": None, "cover_path": None}
    try:
        with zipfile.ZipFile(epub_path, "r") as zf:
            names_lower = {n.lower(): n for n in zf.namelist()}

            opf_path = find_opf(zf)
            if not opf_path:
                result["error"] = "No OPF found"
                return result

            base = opf_dir(opf_path)
            opf_xml = zf.read(opf_path).decode("utf-8", errors="replace")
            root = ET.fromstring(opf_xml)

            # Build manifest: id -> href
            manifest = {}
            for item in root.iter():
                if item.tag.endswith("}item") or item.tag == "item":
                    item_id = item.attrib.get("id", "")
                    href = item.attrib.get("href", "")
                    media_type = item.attrib.get("media-type", "")
                    props = item.attrib.get("properties", "")
                    manifest[item_id] = {
                        "href": resolve(base, href),
                        "media-type": media_type,
                        "properties": props,
                    }

            # Strategy 1: manifest item with properties="cover-image" (EPUB3)
            for item_id, item in manifest.items():
                if "cover-image" in item["properties"] and is_image(item["href"]):
                    path = item["href"]
                    if path.lower() in names_lower or path in zf.namelist():
                        result.update(found=True, method="EPUB3 properties=cover-image", cover_path=path)
                        return result

            # Strategy 2: <meta name="cover" content="item-id"> (EPUB2/Calibre)
            metadata_el = root.find("opf:metadata", NS) or root.find("metadata")
            if metadata_el is not None:
                for el in list(metadata_el):
                    if el.attrib.get("name") == "cover":
                        cover_id = el.attrib.get("content", "")
                        if cover_id in manifest:
                            path = manifest[cover_id]["href"]
                            if path.lower() in names_lower or path in zf.namelist():
                                result.update(found=True, method="OPF meta name=cover", cover_path=path)
                                return result

            # Strategy 3: manifest item whose id contains "cover" and is an image
            for item_id, item in manifest.items():
                if "cover" in item_id.lower() and is_image(item["href"]):
                    path = item["href"]
                    if path.lower() in names_lower or path in zf.namelist():
                        result.update(found=True, method="manifest id contains 'cover'", cover_path=path)
                        return result

            # Strategy 4: file named cover.* anywhere in zip
            for lower, real in names_lower.items():
                stem = Path(lower).stem
                if stem == "cover" and is_image(lower):
                    result.update(found=True, method="file named cover.*", cover_path=real)
                    return result

            result["method"] = "not found"

    except Exception as e:
        result["error"] = str(e)

    return result

epubs = sorted(Path(EPUB_DIR).glob("*.epub"))
results = [find_cover(str(p)) for p in epubs]

found = sum(1 for r in results if r["found"])
print(f"\nCover image search results ({found}/{len(results)} found)\n")
print(f"{'File':<55} {'Found':<6} {'Method'}")
print("-" * 110)
for r in results:
    status = "YES" if r["found"] else "NO"
    method = r.get("method") or r.get("error") or ""
    cover  = f"  -> {r['cover_path']}" if r.get("cover_path") else ""
    print(f"{r['file']:<55} {status:<6} {method}{cover}")
