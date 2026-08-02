"""Thumbnailing: the grid draws a 140px tile, the originals are 1200x1920.

Real files on a tmp_path rather than fakes -- the whole point of the code under
test is what ends up on disk and how big it is, and Pillow is the only thing
that can answer that.
"""

import io

import pytest
from PIL import Image

from web import covers


@pytest.fixture(autouse=True)
def covers_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_COVERS_DIR", str(tmp_path))
    return tmp_path


def jpeg(width=1200, height=1920, mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (width, height), "navy").save(buf, "JPEG")
    return buf.getvalue()


def test_save_writes_the_original_and_a_thumbnail(covers_dir):
    assert covers.save("epub", 7, jpeg()) is True

    original = covers_dir / "epub" / "7.jpg"
    thumb = covers_dir / "epub" / "thumb" / "7.jpg"
    assert original.is_file() and thumb.is_file()

    with Image.open(original) as img:
        assert img.size == (1200, 1920)  # untouched
    with Image.open(thumb) as img:
        assert max(img.size) == covers.THUMB_EDGE
        assert img.size == (250, 400)  # aspect ratio preserved
    assert thumb.stat().st_size < original.stat().st_size


def test_m4b_gets_one_too(covers_dir):
    # Both asset types run through the same save(); this is the guard against a
    # future change that special-cases epub and quietly leaves audiobooks out.
    assert covers.save("m4b", 3, jpeg()) is True
    assert (covers_dir / "m4b" / "thumb" / "3.jpg").is_file()
    assert covers.find_thumb("m4b", 3) is not None


def test_a_palette_png_is_converted_rather_than_rejected(covers_dir):
    buf = io.BytesIO()
    Image.new("P", (600, 900)).save(buf, "PNG")
    assert covers.write_thumb("epub", 11, buf.getvalue()) is True
    with Image.open(covers_dir / "epub" / "thumb" / "11.jpg") as img:
        assert img.mode == "RGB"


def test_a_cover_smaller_than_the_thumbnail_is_not_upscaled(covers_dir):
    covers.save("epub", 12, jpeg(200, 300))
    with Image.open(covers_dir / "epub" / "thumb" / "12.jpg") as img:
        assert img.size == (200, 300)


def test_an_undecodable_cover_still_saves_the_original(covers_dir):
    # A cover Pillow chokes on is still a fine cover for the detail card, and
    # the grid route falls back to the original -- so this must not fail the
    # ingest that is calling it.
    assert covers.save("epub", 9, b"this is not an image") is True
    assert (covers_dir / "epub" / "9.jpg").is_file()
    assert covers.find_thumb("epub", 9) is None


def test_discard_takes_the_thumbnail_with_it(covers_dir):
    # A leftover thumbnail would be worn by whatever asset next reuses the id.
    covers.save("epub", 4, jpeg())
    covers.discard("epub", 4)
    assert covers.find_cover("epub", 4) is None
    assert covers.find_thumb("epub", 4) is None
    assert not (covers_dir / "epub" / "thumb" / "4.jpg").exists()


def test_a_cover_with_no_thumbnail_is_still_found(covers_dir):
    # What the /covers/{type}/thumb/{id} fallback stands on: the two lookups are
    # independent, so a cover whose thumbnail was never written still resolves.
    (covers_dir / "epub").mkdir(parents=True)
    (covers_dir / "epub" / "5.jpg").write_bytes(jpeg())
    assert covers.find_thumb("epub", 5) is None
    assert covers.find_cover("epub", 5) is not None
