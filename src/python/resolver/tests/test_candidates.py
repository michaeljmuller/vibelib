from resolver.candidates import author_sets_match, exact_match


class TestAuthorSetsMatch:
    def test_same_authors_different_spelling(self):
        assert author_sets_match(["J.R.R. Tolkien"], ["J. R. R. Tolkien"])

    def test_order_insensitive(self):
        assert author_sets_match(
            ["Neil Gaiman", "Terry Pratchett"], ["Terry Pratchett", "Neil Gaiman"]
        )

    def test_different_author(self):
        assert not author_sets_match(["Iain Banks"], ["Iain M. Banks"])

    def test_subset_is_not_a_match(self):
        assert not author_sets_match(
            ["Daniel Abraham"], ["Daniel Abraham", "Ty Franck"]
        )

    def test_empty_never_matches(self):
        assert not author_sets_match([], [])
        assert not author_sets_match(["Someone"], [])


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeConn:
    """Stands in for the trigram candidate query; exact_match does the deciding."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, *args, **kwargs):
        return _Result(self._rows)


def book(book_id, title, authors):
    return {
        "id": book_id, "title": title, "authors": authors,
        "series_position": None, "series": None, "sim": 0.9,
    }


def epub(title, authors):
    return {"asset_type": "epub", "title": title, "authors": authors}


class TestExactMatchVolumeGuard:
    """Tier 1 links at confidence 1.0 with no LLM and no review, so a false
    positive here is committed silently. loose_title() strips colon subtitles —
    exactly where the volume number lives — so without a volume guard every
    volume of a series collapses onto the same book. This actually happened:
    A Soldier's Life books 4 and 5 were auto-linked onto book 1.
    """

    SOLDIERS_LIFE_1 = book(
        16, "A Soldier's Life: Book 1", ["Erick Thiemke", "Always RollsAOne"]
    )

    def test_later_volume_does_not_link_to_book_one(self):
        conn = FakeConn([self.SOLDIERS_LIFE_1])
        asset = epub(
            "A Soldier's Life: Book 4: The Hounds",
            ["Thiemke, Erick", "RollsAOne, Always"],
        )
        assert exact_match(conn, asset) is None

    def test_the_same_volume_still_links(self):
        conn = FakeConn([self.SOLDIERS_LIFE_1])
        asset = epub(
            "A Soldier's Life: Book 1", ["Thiemke, Erick", "RollsAOne, Always"]
        )
        assert exact_match(conn, asset) == {"book": {"link": 16}, "authors": []}

    def test_numbered_asset_does_not_link_to_an_unnumbered_book(self):
        # "The Wandering Inn" (the book-1 entity) vs "…: Volume 2": loose_title
        # maps both to "wandering inn". They are different books.
        conn = FakeConn([book(957, "The Wandering Inn", ["pirateaba"])])
        asset = epub("The Wandering Inn: Volume 2", ["pirateaba"])
        assert exact_match(conn, asset) is None

    def test_unnumbered_titles_still_match_each_other(self):
        # The guard must not break the ordinary case it was never about.
        conn = FakeConn([book(425, "Home Sweet Home", ["J.R. Mathews"])])
        asset = epub("Home Sweet Home", ["Mathews, J.R."])
        assert exact_match(conn, asset) == {"book": {"link": 425}, "authors": []}

    def test_same_volume_links_even_when_loose_titles_collide(self):
        # The guard's job is to block *differing* volumes, not to break the
        # subtitle-stripping that loose_title exists for. Both sides here reduce
        # to "azarinth healer" and both are volume 4, so this must still link.
        conn = FakeConn([book(700, "Azarinth Healer: Book Four", ["Rhaegar"])])
        asset = epub("Azarinth Healer: Book Four: A LitRPG Adventure", ["Rhaegar"])
        assert exact_match(conn, asset) == {"book": {"link": 700}, "authors": []}
