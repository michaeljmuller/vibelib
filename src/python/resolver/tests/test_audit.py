import pytest

from resolver.audit import (
    base_title,
    multi_asset_books,
    volume_number,
)


@pytest.mark.parametrize(
    "title,expected",
    [
        # the forms the library actually uses
        ("A Soldier's Life: Book 1", 1),
        ("A Soldier's Life: Book 2: Sobral City", 2),
        ("The Wandering Inn: Volume 2", 2),
        ("Azarinth Healer Book Four: A LitRPG Adventure", 4),
        ("Mother of Learning: ARC 3", 3),
        ("Defiance of the Fall 10: A LitRPG Adventure", 10),
        ("The Path of Ascension 3", 3),
        ("Back to One (The Bad Guys Book 7)", 7),
        ("Some Series #4", 4),
        # titles whose numbers are NOT volume numbers
        ("1632", None),
        ("11/22/63", None),
        ("2 Lies, 2 Thrones", None),
        ("10th Anniversary", None),
        ("The 5th Wave", None),
        ("1st to Die", None),
        ("The Eye of the World", None),
        (None, None),
    ],
)
def test_volume_number(title, expected):
    assert volume_number(title) == expected


def test_base_title_strips_the_volume_phrase():
    # The m4b and epub of one book carry the volume differently; the base title
    # must agree so they can be matched, with volume_number() carrying the number.
    assert base_title("Mother of Learning Arc 1") == base_title("Mother of Learning: ARC 1")
    assert base_title("The Path of Ascension 3") == base_title(
        "The Path of Ascension 3: A LitRPG Adventure"
    )
    assert base_title("Milk Run (Smuggler's Tales From The Golden Age)") == base_title(
        "Milk Run"
    )


def test_base_title_keeps_different_books_apart():
    assert base_title("The Silent Patient") != base_title("The Silent Sea")


def _asset(id, title, s3_key="k", book_id=1):
    return {"id": id, "title": title, "s3_key": s3_key, "book_id": book_id, "authors": []}


BOOKS = {1: {"id": 1, "title": "A Book", "authors": []}}


def test_multi_asset_flags_different_volumes_as_collapsed():
    epubs = [
        _asset(1, "A Soldier's Life: Book 1"),
        _asset(2, "A Soldier's Life: Book 2: Sobral City"),
    ]
    (f,) = multi_asset_books(BOOKS, epubs, [])
    assert f.verdict == "collapsed"


def test_multi_asset_flags_same_book_twice_as_duplicate_file():
    epubs = [
        _asset(1, "The Eye of the World", "a.epub"),
        _asset(2, "The Eye of the World: Book One of The Wheel of Time", "b.epub"),
    ]
    (f,) = multi_asset_books(BOOKS, epubs, [])
    assert f.verdict == "duplicate-file"


def test_multi_asset_covers_m4bs_too():
    m4bs = [
        _asset(295, "The Path of Ascension"),
        _asset(299, "The Path of Ascension 4"),
    ]
    (f,) = multi_asset_books(BOOKS, [], m4bs)
    assert f.verdict == "collapsed"


def test_single_asset_book_is_not_flagged():
    assert multi_asset_books(BOOKS, [_asset(1, "A Book")], []) == []
