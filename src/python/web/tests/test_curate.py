"""The edit form's server side.

A form submits the whole record every time, which is what lets it clear a field
-- and also what makes it capable of blanking something by accident. So the
tests here are mostly about the difference from a correction: every column the
form owns is written on every save, including to NULL, and the credit list is
replaced rather than merged.
"""

import datetime

import pytest

from web.curate import BookEdit, PersonInput, SeriesInput, update_book


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class FakeConn:
    """Records statements; says the book and any id-referenced row exist, and
    that nothing matches by name (so a create stays a create)."""

    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.statements.append((flat, params or ()))
        upper = flat.upper()
        if upper.startswith("SELECT 1 FROM BOOKS"):
            return _Result([{"?column?": 1}])
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

    def update_params(self) -> tuple:
        return next(p for s, p in self.statements if s.startswith("UPDATE books SET title"))


def _edit(**kwargs) -> BookEdit:
    return BookEdit(**{"title": "The Dispossessed", **kwargs})


class TestUpdateBook:
    def test_writes_every_column_the_form_owns(self):
        conn = FakeConn()
        update_book(conn, 1, _edit(
            series=SeriesInput(id=3), series_position=4,
            publication_date=datetime.date(1974, 5, 1), language="en",
        ))
        title, sort, series_id, position, published, language, book_id = conn.update_params()
        assert (title, sort) == ("The Dispossessed", "Dispossessed, The")
        assert (series_id, position) == (3, 4)
        assert published == datetime.date(1974, 5, 1)
        assert (language, book_id) == ("en", 1)

    def test_a_blank_field_clears_the_column(self):
        # The whole point of the form over a correction: absence is a value.
        conn = FakeConn()
        update_book(conn, 1, _edit(series=None, series_position=None,
                                   publication_date=None, language=None))
        _, _, series_id, position, published, language, _ = conn.update_params()
        assert (series_id, position, published, language) == (None, None, None, None)

    def test_an_empty_series_name_is_no_series_rather_than_a_new_one(self):
        conn = FakeConn()
        update_book(conn, 1, _edit(series=SeriesInput(name="   ")))
        assert conn.update_params()[2] is None
        assert not any(s.startswith("INSERT INTO series") for s, _ in conn.statements)

    def test_language_is_normalised_on_the_way_in(self):
        conn = FakeConn()
        update_book(conn, 1, _edit(language="PT_pt"))
        assert conn.update_params()[5] == "pt-PT"

    def test_credits_are_replaced_in_the_order_given(self):
        conn = FakeConn()
        update_book(conn, 1, _edit(authors=[PersonInput(id=7), PersonInput(id=9)]))
        inserts = [p for s, p in conn.statements if s.startswith("INSERT INTO book_authors")]
        assert [(p[1], p[2]) for p in inserts] == [(7, 1), (9, 2)]
        assert sum(s.startswith("DELETE FROM book_authors") for s, _ in conn.statements) == 1

    def test_saving_with_no_authors_leaves_the_book_uncredited(self):
        conn = FakeConn()
        update_book(conn, 1, _edit(authors=[]))
        assert any(s.startswith("DELETE FROM book_authors") for s, _ in conn.statements)
        assert not any(s.startswith("INSERT INTO book_authors") for s, _ in conn.statements)

    def test_an_untitled_book_is_refused(self):
        with pytest.raises(ValueError):
            update_book(FakeConn(), 1, _edit(title="   "))

    def test_a_missing_book_is_refused(self):
        class Gone(FakeConn):
            def execute(self, sql, params=None):
                super().execute(sql, params)
                return _Result()

        with pytest.raises(ValueError):
            update_book(Gone(), 99, _edit())

    def test_nothing_is_written_before_the_book_is_known_to_exist(self):
        class Gone(FakeConn):
            def execute(self, sql, params=None):
                super().execute(sql, params)
                return _Result()

        conn = Gone()
        with pytest.raises(ValueError):
            update_book(conn, 99, _edit(authors=[PersonInput(id=7)]))
        assert not any(
            s.startswith(("UPDATE", "DELETE", "INSERT")) for s, _ in conn.statements
        )


class TestInputs:
    def test_an_id_beats_a_name(self):
        assert PersonInput(id=7, name="whoever").to_action() == {"link": 7}

    def test_a_bare_name_becomes_a_create(self):
        assert PersonInput(name="Ursula K. Le Guin").to_action() == {
            "create": {"name": "Ursula K. Le Guin"}
        }

    def test_an_author_needs_something_to_go_on(self):
        with pytest.raises(ValueError):
            PersonInput(name="  ").to_action()

    def test_a_new_series_carries_its_sort_name(self):
        assert SeriesInput(name="The Dispossessed").to_action() == {
            "create": {"name": "The Dispossessed", "sort_name": "Dispossessed, The"}
        }
