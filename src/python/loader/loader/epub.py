"""Extract raw metadata from an epub's OPF package document.

Deliberately raw: values land in the `epubs` table exactly as the file states
them (dc:date keeps whatever format it uses, dc:creator keeps its punctuation).
Normalization is the resolver's job, and keeping this layer honest is what lets
a wrong title inside a file be recognized as a file problem rather than a bug.
"""

import posixpath
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

CONTAINER = "META-INF/container.xml"
NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
OPF_ROLE = f"{{{NS['opf']}}}role"
OPF_SCHEME = f"{{{NS['opf']}}}scheme"

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp")


@dataclass
class Epub:
    title: str
    authors: list[tuple[str, str]] = field(default_factory=list)  # (name, role)
    asin: str | None = None
    isbn: str | None = None
    publisher: str | None = None
    published_date: str | None = None
    language: str | None = None
    description: str | None = None
    series: str | None = None
    series_position: float | None = None
    identifier: str | None = None
    subject: str | None = None
    cover_path: str | None = None


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    return t or None


def _opf_path(zf: zipfile.ZipFile) -> str:
    root = ET.fromstring(zf.read(CONTAINER))
    rootfile = root.find("c:rootfiles/c:rootfile", NS)
    if rootfile is None or not rootfile.get("full-path"):
        raise ValueError("container.xml names no rootfile")
    return rootfile.get("full-path")  # type: ignore[return-value]


def _cover_path(opf: ET.Element, opf_dir: str) -> str | None:
    """The cover image's path inside the zip.

    Two conventions, and files in this library use both: EPUB3 marks the
    manifest item `properties="cover-image"`; EPUB2 puts `<meta name="cover"
    content="<item id>">` in the metadata and you chase the id into the manifest.
    """
    manifest = opf.find("opf:manifest", NS)
    if manifest is None:
        return None

    href = None
    for item in manifest.findall("opf:item", NS):
        if "cover-image" in (item.get("properties") or ""):
            href = item.get("href")
            break

    if href is None:
        meta = opf.find("opf:metadata/opf:meta[@name='cover']", NS)
        cover_id = meta.get("content") if meta is not None else None
        if cover_id:
            item = manifest.find(f"opf:item[@id='{cover_id}']", NS)
            if item is not None:
                href = item.get("href")

    if not href or not href.lower().endswith(IMAGE_SUFFIXES):
        return None
    # hrefs are relative to the OPF, which is usually not at the zip root.
    return posixpath.normpath(posixpath.join(opf_dir, href))


def _series(meta: ET.Element) -> tuple[str | None, float | None]:
    """Calibre writes series as <meta name="calibre:series">; EPUB3 uses a
    belongs-to-collection element. Try both."""
    name = pos = None

    el = meta.find("opf:meta[@name='calibre:series']", NS)
    if el is not None:
        name = (el.get("content") or "").strip() or None
    el = meta.find("opf:meta[@name='calibre:series_index']", NS)
    if el is not None:
        try:
            pos = float(el.get("content") or "")
        except ValueError:
            pos = None

    if name is None:
        for el in meta.findall("opf:meta[@property='belongs-to-collection']", NS):
            name = _text(el)
            break
        for el in meta.findall("opf:meta[@property='group-position']", NS):
            try:
                pos = float((el.text or "").strip())
            except ValueError:
                pos = None
            break

    return name, pos


def parse(path: str) -> Epub:
    with zipfile.ZipFile(path) as zf:
        opf_name = _opf_path(zf)
        opf = ET.fromstring(zf.read(opf_name))
        opf_dir = posixpath.dirname(opf_name)

        meta = opf.find("opf:metadata", NS)
        if meta is None:
            raise ValueError("OPF has no <metadata>")

        title = _text(meta.find("dc:title", NS))
        if not title:
            raise ValueError("OPF has no dc:title")

        authors: list[tuple[str, str]] = []
        for el in meta.findall("dc:creator", NS):
            name = _text(el)
            if name:
                authors.append((name, el.get(OPF_ROLE) or "author"))

        asin = isbn = None
        identifier = None
        for el in meta.findall("dc:identifier", NS):
            value = _text(el)
            if not value:
                continue
            scheme = (el.get(OPF_SCHEME) or "").upper()
            low = value.lower()
            # `urn:asin:B0CV8MMBFT` is how most of this library states it; the
            # opf:scheme attribute is the older EPUB2 spelling.
            if (
                scheme in ("AMAZON", "MOBI-ASIN")
                or low.startswith(("urn:asin:", "amzn"))
            ):
                asin = asin or value.split(":")[-1]
            elif scheme == "ISBN" or low.startswith("urn:isbn:"):
                isbn = isbn or value.split(":")[-1]
            elif identifier is None:
                identifier = value

        series, series_position = _series(meta)

        return Epub(
            title=title,
            authors=authors,
            asin=asin,
            isbn=isbn,
            publisher=_text(meta.find("dc:publisher", NS)),
            published_date=_text(meta.find("dc:date", NS)),
            language=_text(meta.find("dc:language", NS)),
            description=_text(meta.find("dc:description", NS)),
            series=series,
            series_position=series_position,
            identifier=identifier,
            subject=_text(meta.find("dc:subject", NS)),
            cover_path=_cover_path(opf, opf_dir),
        )


def cover_bytes(path: str, cover_path: str) -> bytes | None:
    """The cover image itself, for extraction to the covers directory."""
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.read(cover_path)
    except KeyError:
        return None
