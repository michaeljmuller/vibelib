"""m4b tag and chapter handling.

Synthesizing a real MP4 needs an encoder, so these cover the two places the
logic actually lives: coercing mutagen's tag values (freeform atoms come back
as bytes, everything else as str) and reading ffprobe's chapter JSON.
"""

import json
import subprocess

import pytest

from web.ingest import m4b


class FakeTags(dict):
    pass


@pytest.mark.parametrize("stored,expected", [
    (["He Who Fights with Monsters"], "He Who Fights with Monsters"),
    ([b"B0CV8MMBFT"], "B0CV8MMBFT"),        # freeform atoms (ASIN) are bytes
    (["  padded  "], "padded"),
    ([""], None),                            # empty tag is absence, not ""
    ([], None),
    (None, None),
])
def test_first_coerces_tag_values(stored, expected):
    tags = FakeTags()
    if stored is not None:
        tags["x"] = stored
    assert m4b._first(tags, "x") == expected


def test_chapters_parsed_from_ffprobe(monkeypatch):
    payload = json.dumps({"chapters": [
        {"start_time": "0.000000", "tags": {"title": "Chapter 1"}},
        {"start_time": "17.879000", "tags": {"title": "Chapter 2"}},
        {"start_time": "1012.483000", "tags": {}},
    ]}).encode()
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=payload),
    )
    chapters = m4b._chapters("x.m4b")
    assert [(c.position, c.title, c.start_ms) for c in chapters] == [
        (1, "Chapter 1", 0),
        (2, "Chapter 2", 17879),      # seconds -> ms, matching the existing rows
        (3, None, 1012483),           # an untitled chapter still gets its offset
    ]


def test_no_chapters_is_normal(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=b'{"chapters": []}'),
    )
    assert m4b._chapters("x.m4b") == []


def test_ffprobe_failure_is_not_fatal(monkeypatch):
    def boom(*a, **k):
        raise subprocess.CalledProcessError(1, "ffprobe")
    monkeypatch.setattr(subprocess, "run", boom)
    # A file whose chapters can't be read is still worth ingesting.
    assert m4b._chapters("x.m4b") == []


def test_chapter_with_unparseable_start_is_dropped(monkeypatch):
    payload = json.dumps({"chapters": [
        {"start_time": "junk", "tags": {"title": "Bad"}},
        {"start_time": "5.0", "tags": {"title": "Good"}},
    ]}).encode()
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=payload),
    )
    chapters = m4b._chapters("x.m4b")
    assert [c.title for c in chapters] == ["Good"]
