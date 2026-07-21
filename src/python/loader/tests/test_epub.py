"""Epub OPF parsing, against synthetic files.

The library's real epubs use both cover conventions and both series conventions,
and several carry an OPF that is not at the zip root — the cases below are the
ones that actually bite.
"""

import zipfile

import pytest

from loader import epub

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="{opf}" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{version}">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"
            xmlns:opf="http://www.idpf.org/2007/opf">
    {metadata}
  </metadata>
  <manifest>
    {manifest}
  </manifest>
</package>"""


def build(tmp_path, metadata, manifest="", opf="OEBPS/content.opf", version="3.0",
          images=()):
    path = tmp_path / "book.epub"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/container.xml", CONTAINER.format(opf=opf))
        z.writestr(opf, OPF.format(metadata=metadata, manifest=manifest,
                                   version=version))
        for name in images:
            z.writestr(name, b"\xff\xd8\xff\xe0 fake jpeg")
    return str(path)


def test_basic_metadata(tmp_path):
    e = epub.parse(build(tmp_path, """
        <dc:title>Service Model</dc:title>
        <dc:creator opf:role="aut">Adrian Tchaikovsky</dc:creator>
        <dc:publisher>Tor</dc:publisher>
        <dc:date>2024-06-04</dc:date>
        <dc:language>en</dc:language>
        <dc:description>A robot detective.</dc:description>
        <dc:subject>science fiction</dc:subject>
    """))
    assert e.title == "Service Model"
    assert e.authors == [("Adrian Tchaikovsky", "aut")]
    assert e.publisher == "Tor"
    assert e.published_date == "2024-06-04"      # raw, not parsed
    assert e.language == "en"
    assert e.description == "A robot detective."
    assert e.subject == "science fiction"


def test_multiple_authors_keep_order_and_default_role(tmp_path):
    e = epub.parse(build(tmp_path, """
        <dc:title>Good Omens</dc:title>
        <dc:creator>Terry Pratchett</dc:creator>
        <dc:creator opf:role="edt">Neil Gaiman</dc:creator>
    """))
    assert e.authors == [("Terry Pratchett", "author"), ("Neil Gaiman", "edt")]


@pytest.mark.parametrize("scheme,value,field,expected", [
    ("AMAZON", "B0CV8MMBFT", "asin", "B0CV8MMBFT"),
    ("MOBI-ASIN", "B08BR48FK9", "asin", "B08BR48FK9"),
    ("ISBN", "9780316414296", "isbn", "9780316414296"),
])
def test_identifier_schemes(tmp_path, scheme, value, field, expected):
    e = epub.parse(build(tmp_path, f"""
        <dc:title>T</dc:title>
        <dc:identifier opf:scheme="{scheme}">{value}</dc:identifier>
    """))
    assert getattr(e, field) == expected


def test_urn_isbn_without_a_scheme_attribute(tmp_path):
    e = epub.parse(build(tmp_path, """
        <dc:title>T</dc:title>
        <dc:identifier>urn:isbn:9780316414296</dc:identifier>
    """))
    assert e.isbn == "9780316414296"


def test_urn_asin_without_a_scheme_attribute(tmp_path):
    # The dominant form in this library: 914 of the 1293 existing epubs use it,
    # and missing it silently drops the ASIN.
    e = epub.parse(build(tmp_path, """
        <dc:title>T</dc:title>
        <dc:identifier id="bookid">urn:asin:B0DMFG62M9</dc:identifier>
    """))
    assert e.asin == "B0DMFG62M9"
    assert e.identifier is None


def test_unrecognized_identifier_is_kept_raw(tmp_path):
    e = epub.parse(build(tmp_path, """
        <dc:title>T</dc:title>
        <dc:identifier>9d5bf317-1029-4618-8480-4c5b7b589a0d</dc:identifier>
    """))
    assert e.identifier == "9d5bf317-1029-4618-8480-4c5b7b589a0d"
    assert e.isbn is None and e.asin is None


def test_calibre_series(tmp_path):
    e = epub.parse(build(tmp_path, """
        <dc:title>Hobnobbing</dc:title>
        <meta name="calibre:series" content="New Era Online"/>
        <meta name="calibre:series_index" content="3"/>
    """))
    assert e.series == "New Era Online"
    assert e.series_position == 3.0


def test_epub3_collection_series(tmp_path):
    e = epub.parse(build(tmp_path, """
        <dc:title>T</dc:title>
        <meta property="belongs-to-collection">The Wandering Inn</meta>
        <meta property="group-position">2</meta>
    """))
    assert e.series == "The Wandering Inn"
    assert e.series_position == 2.0


def test_half_step_series_position(tmp_path):
    # series_position is NUMERIC(6,2) precisely so 1.5 survives.
    e = epub.parse(build(tmp_path, """
        <dc:title>T</dc:title>
        <meta name="calibre:series" content="S"/>
        <meta name="calibre:series_index" content="1.5"/>
    """))
    assert e.series_position == 1.5


def test_cover_epub3_properties(tmp_path):
    path = build(
        tmp_path,
        "<dc:title>T</dc:title>",
        manifest='<item id="c" href="images/cover.jpg" properties="cover-image"'
                 ' media-type="image/jpeg"/>',
        images=("OEBPS/images/cover.jpg",),
    )
    e = epub.parse(path)
    assert e.cover_path == "OEBPS/images/cover.jpg"      # resolved against the OPF dir
    assert epub.cover_bytes(path, e.cover_path).startswith(b"\xff\xd8")


def test_cover_epub2_meta_pointer(tmp_path):
    path = build(
        tmp_path,
        '<dc:title>T</dc:title><meta name="cover" content="cover-img"/>',
        manifest='<item id="cover-img" href="image_rsrc4F8.jpg" media-type="image/jpeg"/>',
        version="2.0",
        images=("OEBPS/image_rsrc4F8.jpg",),
    )
    assert epub.parse(path).cover_path == "OEBPS/image_rsrc4F8.jpg"


def test_cover_pointing_at_a_non_image_is_ignored(tmp_path):
    # Some files point the cover meta at the cover *page* (xhtml), not the image.
    e = epub.parse(build(
        tmp_path,
        '<dc:title>T</dc:title><meta name="cover" content="c"/>',
        manifest='<item id="c" href="titlepage.xhtml" media-type="application/xhtml+xml"/>',
    ))
    assert e.cover_path is None


def test_opf_at_zip_root(tmp_path):
    path = build(
        tmp_path,
        "<dc:title>T</dc:title>",
        manifest='<item id="c" href="cover.jpg" properties="cover-image" media-type="image/jpeg"/>',
        opf="content.opf",
        images=("cover.jpg",),
    )
    assert epub.parse(path).cover_path == "cover.jpg"


def test_missing_title_is_an_error(tmp_path):
    # title is NOT NULL in the schema; better to fail this one file loudly.
    with pytest.raises(ValueError, match="dc:title"):
        epub.parse(build(tmp_path, "<dc:creator>Nobody</dc:creator>"))


def test_missing_cover_bytes_returns_none(tmp_path):
    path = build(tmp_path, "<dc:title>T</dc:title>")
    assert epub.cover_bytes(path, "OEBPS/not-there.jpg") is None
