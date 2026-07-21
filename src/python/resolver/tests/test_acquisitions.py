import datetime
from pathlib import Path

import pytest

from resolver.acquisitions import (
    Match,
    Order,
    _index,
    _local_date,
    _lookup,
    _sql_string,
    _upsert,
    read_borrowed,
    read_orders,
    render_sql,
)

ORDERS_HEADER = (
    "ASIN,Order Date,Order Status,Price,Product Name,Seller of Record\n"
)
BORROWED_HEADER = "ASIN,Loan Creation Date,Product Name\n"


def _export(tmp_path: Path, orders: str = "", borrowed: str = "") -> Path:
    d = tmp_path / "Your Orders" / "Your Amazon Orders"
    d.mkdir(parents=True)
    # utf-8-sig, because that is what Amazon writes and what the reader must survive
    (d / "Digital Content Orders.csv").write_text(
        ORDERS_HEADER + orders, encoding="utf-8-sig"
    )
    (d / "Digital Borrowed Items.csv").write_text(
        BORROWED_HEADER + borrowed, encoding="utf-8-sig"
    )
    return tmp_path


class TestLocalDate:
    def test_utc_evening_is_the_previous_local_day(self):
        # 03:23Z is 21:23 the day before in Boulder; the library got it on the 26th
        assert _local_date("2023-04-27T03:23:00Z") == datetime.date(2023, 4, 26)

    def test_utc_midday_is_the_same_local_day(self):
        assert _local_date("2026-06-16T11:51:00Z") == datetime.date(2026, 6, 16)


class TestReadOrders:
    def test_earliest_order_wins(self, tmp_path):
        # A re-purchase must not move the book to the top of a by-acquired sort
        export = _export(
            tmp_path,
            orders=(
                "B1,2020-05-05T18:00:00Z,SUCCESS,9.99,A Book,Audible\n"
                "B1,2015-03-03T18:00:00Z,SUCCESS,9.99,A Book,Audible\n"
            ),
        )
        assert read_orders(export)["B1"].when == datetime.date(2015, 3, 3)

    def test_duplicate_component_rows_collapse(self, tmp_path):
        # every order appears twice in the export: a Price Amount row and a Tax row
        export = _export(
            tmp_path,
            orders=(
                "B1,2020-05-05T18:00:00Z,SUCCESS,0.69,A Book,Audible\n"
                "B1,2020-05-05T18:00:00Z,SUCCESS,9.99,A Book,Audible\n"
            ),
        )
        assert len(read_orders(export)) == 1

    def test_unsuccessful_orders_are_skipped(self, tmp_path):
        export = _export(
            tmp_path,
            orders=(
                "B1,2020-05-05T18:00:00Z,CANCELLED_BY_AMAZON,9.99,A Book,Audible\n"
                "B2,2020-05-05T18:00:00Z,PENDING,9.99,B Book,Audible\n"
            ),
        )
        assert read_orders(export) == {}

    def test_seller_of_record_marks_audiobooks(self, tmp_path):
        export = _export(
            tmp_path,
            orders=(
                "B1,2020-05-05T18:00:00Z,SUCCESS,9.99,A Book,Audible\n"
                "B2,2020-05-05T18:00:00Z,SUCCESS,9.99,B Book,Macmillan\n"
            ),
        )
        orders = read_orders(export)
        assert orders["B1"].audible and not orders["B2"].audible


class TestReadBorrowed:
    def test_earliest_loan_wins(self, tmp_path):
        export = _export(
            tmp_path,
            borrowed=(
                "B1,2022-01-01T18:00:00Z,A Book\n"
                "B1,2021-10-06T18:00:00Z,A Book\n"
            ),
        )
        assert read_borrowed(export)["B1"] == datetime.date(2021, 10, 6)


def _order(asin: str, name: str, audible: bool = True) -> Order:
    return Order(asin, datetime.date(2020, 1, 1), name, audible)


class TestTitleLookup:
    def test_subtitle_and_series_phrasing_still_match(self):
        index = _index([_order("B1", "A Psalm for the Wild-Built: Monk & Robot, Book 1")])
        assert _lookup(index, "A Psalm for the Wild-Built") == "B1"

    def test_volume_number_keeps_series_entries_apart(self):
        index = _index(
            [
                _order("B1", "All the Skills: A Deck-Building LitRPG"),
                _order("B2", "All the Skills 2: A Deck-Building LitRPG"),
            ]
        )
        assert _lookup(index, "All the Skills 2") == "B2"

    def test_candidates_are_unioned(self):
        # the ©nam title misses, the ©alb album hits
        index = _index([_order("B1", "A Memory Called Empire: Teixcalaan, Book 1")])
        assert _lookup(index, "Untitled", "A Memory Called Empire") == "B1"

    def test_volume_breaks_a_tie_between_entries_of_one_series(self):
        # the subtitle is where the volume lives, and loose_title throws it away,
        # so both of these are "chrysalis" until the number is consulted
        index = _index(
            [_order("B1", "Chrysalis: Book One"), _order("B2", "Chrysalis: Book Two")]
        )
        assert _lookup(index, "Chrysalis: Book 2") == "B2"

    def test_unnumbered_candidate_will_not_pick_a_volume(self):
        index = _index(
            [_order("B1", "Chrysalis: Book One"), _order("B2", "Chrysalis: Book Two")]
        )
        assert _lookup(index, "Chrysalis") is None

    def test_stated_volume_must_agree_even_when_it_is_the_only_hit(self):
        # owning book 2 but having only ordered book 1 must not match
        index = _index([_order("B1", "Chrysalis: Book One")])
        assert _lookup(index, "Chrysalis: Book Two") is None

    def test_candidates_disagreeing_about_volume_match_nothing(self):
        index = _index([_order("B1", "Chrysalis: Book One")])
        assert _lookup(index, "Chrysalis: Book One", "Chrysalis: Book 2") is None

    def test_ambiguity_is_dropped_not_guessed(self):
        # the "A Thousand Li" case: distinct books, same loose title, no volume
        index = _index(
            [
                _order("B1", "A Thousand Li: The First Step"),
                _order("B2", "A Thousand Li: The First War"),
            ]
        )
        assert _lookup(index, "A Thousand Li") is None

    def test_same_asin_from_several_candidates_is_not_ambiguous(self):
        index = _index([_order("B1", "A Gentleman in Moscow")])
        assert _lookup(index, "A Gentleman in Moscow", "A Gentleman in Moscow") == "B1"

    def test_no_match(self):
        assert _lookup(_index([_order("B1", "A Book")]), "Another Book") is None

    def test_empty_candidates_are_ignored(self):
        assert _lookup(_index([_order("B1", "A Book")]), None, "") is None


class TestSqlEscaping:
    def test_apostrophes_are_doubled(self):
        assert _sql_string("Traveler's Gate.epub") == "'Traveler''s Gate.epub'"

    def test_generated_values_quote_titles_safely(self):
        sql = _upsert(
            "epub",
            [Match("O'Brian; DROP TABLE epubs.epub", datetime.date(2020, 1, 1), "asin", "B1")],
        )
        assert "'O''Brian; DROP TABLE epubs.epub'" in sql


class TestRenderSql:
    def _sql(self, **kw):
        return render_sql(
            kw.get("epubs", [Match("a.epub", datetime.date(2020, 1, 2), "asin", "B1")]),
            kw.get("m4bs", [Match("a.m4b", datetime.date(2020, 1, 3), "via-epub", "paired epub")]),
            Path("/amazon"),
        )

    def test_does_not_commit_without_apply(self):
        sql = self._sql()
        assert "\\if :{?apply}" in sql
        assert "ROLLBACK;" in sql
        assert sql.index("ROLLBACK;") > sql.index("\\if :{?apply}")

    def test_upserts_are_idempotent(self):
        sql = self._sql()
        assert sql.count("ON CONFLICT (epub_id) DO UPDATE") == 1
        assert sql.count("ON CONFLICT (m4b_id) DO UPDATE") == 1
        assert "DO UPDATE SET acquired_on = EXCLUDED.acquired_on" in sql

    def test_keyed_on_s3_key_not_ids(self):
        sql = self._sql()
        assert "JOIN epubs a USING (s3_key)" in sql
        assert "JOIN m4bs a USING (s3_key)" in sql

    def test_punctuation_precedes_the_comment(self):
        # a comma or semicolon after a trailing `--` is inside the comment, and
        # the file stops being SQL -- the mistake generated SQL is most prone to
        sql = _upsert(
            "epub",
            [
                Match("a.epub", datetime.date(2020, 1, 1), "asin", "B1"),
                Match("b.epub", datetime.date(2020, 1, 2), "asin", "B2"),
            ],
        )
        rows = [ln for ln in sql.splitlines() if ln.startswith("  ('")]
        assert rows[0] == "  ('a.epub', DATE '2020-01-01'),  -- asin B1"
        assert rows[1] == "  ('b.epub', DATE '2020-01-02');  -- asin B2"

    def test_values_are_emitted_once(self):
        sql = self._sql()
        assert sql.count("'a.epub'") == 1

    def test_match_method_is_recorded_per_row(self):
        sql = self._sql()
        assert "-- asin B1" in sql
        assert "-- via-epub paired epub" in sql

    def test_rows_are_sorted_for_a_readable_diff(self):
        sql = render_sql(
            [
                Match("z.epub", datetime.date(2020, 1, 1), "asin", "B2"),
                Match("a.epub", datetime.date(2020, 1, 1), "asin", "B1"),
            ],
            [],
            Path("/amazon"),
        )
        assert sql.index("'a.epub'") < sql.index("'z.epub'")

    def test_empty_side_emits_no_upsert(self):
        sql = render_sql([], [], Path("/amazon"))
        assert "ON CONFLICT (epub_id)" not in sql
        assert "ON CONFLICT (m4b_id)" not in sql
        assert "-- no epub matches" in sql
        assert "-- no m4b matches" in sql


class TestMatchChange:
    def test_no_existing_date_is_new(self):
        assert Match("a", datetime.date(2020, 1, 1), "asin", "B1").change == "new"

    def test_agreeing_date_is_same(self):
        d = datetime.date(2020, 1, 1)
        assert Match("a", d, "asin", "B1", old=d).change == "same"

    def test_differing_date_is_changed(self):
        m = Match("a", datetime.date(2020, 1, 1), "asin", "B1", old=datetime.date(2019, 1, 1))
        assert m.change == "changed"
