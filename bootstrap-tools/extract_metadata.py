import os
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

EPUB_DIR = os.environ.get("EPUB_DIR", "/epubs")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "/output/ebooks-metadata.md")

# OPF XML namespaces
NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc":  "http://purl.org/dc/elements/1.1/",
}

def find_opf(zf):
    """Return the path to the OPF file inside the epub zip."""
    # Check META-INF/container.xml first
    try:
        container = zf.read("META-INF/container.xml").decode("utf-8", errors="replace")
        root = ET.fromstring(container)
        for rf in root.iter():
            if rf.tag.endswith("rootfile"):
                return rf.attrib.get("full-path")
    except Exception:
        pass
    # Fallback: look for any .opf file
    for name in zf.namelist():
        if name.endswith(".opf"):
            return name
    return None

def dc(tag):
    return f"{{{NS['dc']}}}{tag}"

def opf(tag):
    return f"{{{NS['opf']}}}{tag}"

def text(el):
    return el.text.strip() if el is not None and el.text else None

def extract_metadata(epub_path):
    meta = {"file": Path(epub_path).name}
    try:
        with zipfile.ZipFile(epub_path, "r") as zf:
            opf_path = find_opf(zf)
            if not opf_path:
                meta["error"] = "No OPF found"
                return meta
            opf_xml = zf.read(opf_path).decode("utf-8", errors="replace")
            root = ET.fromstring(opf_xml)

            # Locate <metadata> element (handle namespaced and plain tags)
            metadata_el = root.find("opf:metadata", NS) or root.find("metadata")

            def find_dc(tag):
                if metadata_el is None:
                    return None
                el = metadata_el.find(f"dc:{tag}", NS)
                if el is None:
                    el = metadata_el.find(dc(tag))
                return text(el)

            meta["title"]    = find_dc("title")
            meta["author"]   = find_dc("creator")
            meta["publisher"]= find_dc("publisher")
            meta["date"]     = find_dc("date")
            meta["language"] = find_dc("language")
            meta["identifier"]= find_dc("identifier")
            meta["description"] = find_dc("description")
            meta["subject"]  = find_dc("subject")

            # Check all identifiers for ASIN / ISBN
            if metadata_el is not None:
                for el in metadata_el.findall(f"dc:identifier", NS) + metadata_el.findall(dc("identifier")):
                    scheme = el.attrib.get("scheme") or el.attrib.get(f"{{{NS['opf']}}}scheme", "")
                    val = text(el) or ""
                    if "asin" in scheme.lower() or val.upper().startswith("B0") and len(val) == 10:
                        meta["asin"] = val
                    elif "isbn" in scheme.lower():
                        meta["isbn"] = val

            # Series info from OPF meta tags (Calibre / EPUB3 conventions)
            if metadata_el is not None:
                for el in metadata_el.findall("opf:meta", NS) + metadata_el.findall("meta"):
                    name = el.attrib.get("name", "")
                    content = el.attrib.get("content", "")
                    prop = el.attrib.get("property", "")
                    if name == "calibre:series":
                        meta["series"] = content
                    elif name == "calibre:series_index":
                        meta["series_index"] = content
                    elif "belongs-to-collection" in prop:
                        meta["series"] = text(el) or content
                    elif "group-position" in prop:
                        meta["series_index"] = text(el) or content

    except Exception as e:
        meta["error"] = str(e)
    return meta

def format_entry(m):
    lines = [f"### {m.get('title') or m['file']}\n"]
    fields = [
        ("File",        "file"),
        ("Author",      "author"),
        ("Series",      "series"),
        ("Series #",    "series_index"),
        ("Publisher",   "publisher"),
        ("Date",        "date"),
        ("Language",    "language"),
        ("ASIN",        "asin"),
        ("ISBN",        "isbn"),
        ("Identifier",  "identifier"),
        ("Subject",     "subject"),
        ("Description", "description"),
        ("Error",       "error"),
    ]
    for label, key in fields:
        val = m.get(key)
        if val:
            lines.append(f"- **{label}:** {val}")
    return "\n".join(lines)

epubs = sorted(Path(EPUB_DIR).glob("*.epub"))
records = [extract_metadata(str(p)) for p in epubs]

os.makedirs(Path(OUTPUT_FILE).parent, exist_ok=True)
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write("# E-book Metadata\n\n")
    f.write(f"Extracted from {len(records)} EPUB file(s).\n\n")
    for m in records:
        f.write(format_entry(m))
        f.write("\n\n---\n\n")

print(f"Wrote metadata for {len(records)} books to {OUTPUT_FILE}")
