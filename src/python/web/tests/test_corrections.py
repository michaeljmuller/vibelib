"""Correcting a book already in the catalog.

The thing worth testing here is not that a correction works but that it stops:
a correction overwrites rows the whole library reads, so a request about the
publication date must not be able to reach the title. That guarantee is carried
by one rule -- a field the model left null produces no key, and a key that is
absent is never touched -- so most of what follows checks that absence.
"""

import datetime

import pytest

from web.ingest.apply import apply_correction
from web.ingest.corrections import _seed_names
from web.ingest.llm import BookCorrection, PersonRef, SeriesRef, to_correction
from web.ingest.summary import correction_rows


def _correction(**kwargs) -> BookCorrection:
    fields = {
        "title": None, "series": None, "series_position": None,
        "publication_date": None, "language": None, "authors": None,
        "confidence": 0.9, "notes": "",
    }
    return BookCorrection(**{**fields, **kwargs})


class TestToCorrection:
    def test_untouched_fields_produce_no_keys(self):
        out = to_correction(_correction(publication_date="2022-09-06"))
        assert out == {"publication_date": "2022-09-06"}

    def test_a_correction_that_changes_nothing_is_empty(self):
        assert to_correction(_correction()) == {}

    def test_title_carries_its_sort_form(self):
        out = to_correction(_correction(title="The Martian"))
        assert out == {"title": "The Martian", "sort_title": "Martian, The"}

    def test_a_junk_date_is_dropped_rather_than_written(self):
        assert to_correction(_correction(publication_date="sometime in 2022")) == {}

    def test_year_only_dates_become_the_january_convention(self):
        assert to_correction(_correction(publication_date="2022"))[
            "publication_date"
        ] == "2022-01-01"

    def test_language_is_normalised(self):
        assert to_correction(_correction(language="EN"))["language"] == "en"

    def test_authors_become_an_ordered_action_list(self):
        out = to_correction(
            _correction(
                authors=[
                    PersonRef(raw_name="Ursula K. Le Guin", link_person_id=7, create=None),
                    PersonRef(raw_name="Someone New", link_person_id=None, create=None),
                ]
            )
        )
        assert out["authors"][0] == {"link": 7, "raw_name": "Ursula K. Le Guin"}
        assert out["authors"][1]["create"]["name"] == "Someone New"
        assert out["authors"][1]["create"]["sort_name"] == "New, Someone"

    def test_an_empty_author_list_is_a_change_not_a_silence(self):
        # None means "leave the credits alone"; [] means "credit nobody", and
        # the two must not collapse into each other.
        assert to_correction(_correction(authors=[])) == {"authors": []}

    def test_series_links_or_creates(self):
        linked = to_correction(
            _correction(series=SeriesRef(link_series_id=3, create_name=None))
        )
        assert linked["series"] == {"link": 3}
        created = to_correction(
            _correction(series=SeriesRef(link_series_id=None, create_name="Earthsea"))
        )
        assert created["series"]["create"]["name"] == "Earthsea"


class TestSeedNames:
    def test_pulls_a_name_out_of_a_sentence(self):
        seeds = _seed_names("you got the author wrong, it's Ursula K. Le Guin", {})
        assert any("Le Guin" in s for s in seeds)

    def test_skips_the_word_that_opens_a_sentence(self):
        assert _seed_names("The publication date is wrong", {}) == []

    def test_includes_what_the_book_already_credits(self):
        seeds = _seed_names(
            "the date is wrong", {"authors": ["Anne McCaffrey"], "series_name": "Pern"}
        )
        assert seeds == ["Anne McCaffrey", "Pern"]

    def test_does_not_repeat_a_name_the_book_already_has(self):
        seeds = _seed_names("it is Anne McCaffrey", {"authors": ["Anne McCaffrey"]})
        assert seeds == ["Anne McCaffrey"]


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class FakeConn:
    """Records every statement it is asked to run.

    Answers only the lookups these paths make on the way to a write: the book
    exists, a person referenced by id exists, and nothing matches by name (so a
    `create` stays a create). Everything else comes back empty.
    """

    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.statements.append((flat, params or ()))
        upper = flat.upper()
        if upper.startswith("SELECT ID FROM BOOKS"):
            return _Result([{"id": 1}])
        if upper.startswith("SELECT ID FROM PEOPLE WHERE ID"):
            return _Result([{"id": params[0]}])
        if upper.startswith("SELECT ID FROM SERIES WHERE ID"):
            return _Result([{"id": params[0]}])
        return _Result()

    def transaction(self):
        conn = self

        class _Txn:
            def __enter__(self):
                return conn

            def __exit__(self, *exc):
                return False

        return _Txn()

    def touched(self, column: str) -> bool:
        return any(f"SET {column} =" in sql for sql, _ in self.statements)


class TestApplyCorrection:
    def test_only_the_named_column_is_written(self):
        conn = FakeConn()
        apply_correction(conn, 1, {"publication_date": "2022-09-06"})
        assert conn.touched("publication_date")
        for untouched in ("title", "language", "series_id", "series_position"):
            assert not conn.touched(untouched)

    def test_an_empty_correction_is_refused(self):
        with pytest.raises(ValueError):
            apply_correction(FakeConn(), 1, {})

    def test_a_missing_book_is_refused(self):
        class Gone(FakeConn):
            def execute(self, sql, params=None):
                super().execute(sql, params)
                return _Result()  # nothing exists, including the book

        with pytest.raises(ValueError):
            apply_correction(Gone(), 99, {"language": "en"})

    def test_correcting_authors_replaces_the_whole_credit_list(self):
        conn = FakeConn()
        apply_correction(
            conn, 1, {"authors": [{"link": 7, "raw_name": "Ursula K. Le Guin"}]}
        )
        deletes = [s for s, _ in conn.statements if s.startswith("DELETE FROM book_authors")]
        inserts = [s for s, _ in conn.statements if s.startswith("INSERT INTO book_authors")]
        assert len(deletes) == 1 and len(inserts) == 1

    def test_a_correction_about_a_date_never_deletes_a_credit(self):
        conn = FakeConn()
        apply_correction(conn, 1, {"publication_date": "2022-09-06"})
        assert not any(s.startswith("DELETE") for s, _ in conn.statements)


class TestCorrectionRows:
    """The card must show what is being replaced, not just what replaces it."""

    BOOK = {
        "id": 1, "title": "The Dispossessed", "series_id": None, "series_name": None,
        "series_position": None, "publication_date": datetime.date(2019, 1, 1),
        "language": "en", "authors": ["Ursula Le Guin"],
    }

    def test_shows_before_and_after(self):
        rows = correction_rows(FakeConn(), self.BOOK, {"publication_date": "1974-05-01"})
        assert len(rows) == 1
        assert rows[0]["label"] == "Publication date"
        assert "2019-01-01" in rows[0]["text"] and "1974-05-01" in rows[0]["text"]

    def test_an_absent_value_reads_as_none_rather_than_blank(self):
        book = {**self.BOOK, "publication_date": None}
        rows = correction_rows(FakeConn(), book, {"publication_date": "1974-05-01"})
        assert "(none)" in rows[0]["text"]

    def test_unchanged_fields_get_no_row(self):
        rows = correction_rows(FakeConn(), self.BOOK, {"language": "pt"})
        assert [r["label"] for r in rows] == ["Language"]

    def test_replacing_credits_says_that_it_replaces_them(self):
        rows = correction_rows(
            FakeConn(), self.BOOK,
            {"authors": [{"create": {"name": "Ursula K. Le Guin"}, "raw_name": "x"}]},
        )
        assert "Ursula Le Guin" in rows[0]["text"]
        assert "replaces the whole credit list" in rows[0]["warning"]
