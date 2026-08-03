"""_adopt_epub_publication_date: let a newly linked epub fix a *guessed* date.

The two cases below look identical from inside apply() and want opposite
outcomes, which is what all the guards are for:

  "Upping the Ante"  book created from the audiobook, guessed 2019-01-01;
                     its epub says 2022-09-06.  -> adopt (the guess was wrong)
  "The Pothunters"   first published 1902, correctly recorded; its epub is a
                     2011 reissue.              -> refuse (we'd corrupt it)
"""

import datetime

import pytest

from web.ingest.apply import _adopt_epub_publication_date


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConn:
    """Answers the one SELECT, and records any UPDATE it's asked to run."""

    def __init__(self, current, raw, n_epubs=1):
        self.row = {"current": current, "raw": raw, "n_epubs": n_epubs}
        self.updated_to = None

    def execute(self, sql, params=None):
        if sql.strip().upper().startswith("UPDATE"):
            self.updated_to = params[0]
            return _Result(None)
        return _Result(self.row)


def date(s):
    return datetime.date.fromisoformat(s)


def test_guessed_placeholder_is_replaced_by_the_epubs_real_date():
    conn = FakeConn(date("2019-01-01"), "2022-09-06T06:00:00+00:00")
    _adopt_epub_publication_date(conn, book_id=1336, epub_id=1329)
    assert conn.updated_to == "2022-09-06"


def test_null_date_is_filled():
    conn = FakeConn(None, "2024-04-18T06:00:00+00:00")
    _adopt_epub_publication_date(conn, 1, 1)
    assert conn.updated_to == "2024-04-18"


def test_reissue_of_a_classic_is_refused():
    # The Pothunters: 1902 is right; the epub is a 2011 edition.
    conn = FakeConn(date("1902-01-01"), "2011-09-05T00:00:00+00:00")
    _adopt_epub_publication_date(conn, 284, 307)
    assert conn.updated_to is None


def test_a_precise_existing_date_is_never_overwritten():
    conn = FakeConn(date("2023-06-20"), "2024-01-01T00:00:00+00:00")
    _adopt_epub_publication_date(conn, 1, 1)
    assert conn.updated_to is None


def test_not_the_books_first_epub_is_left_alone():
    # A book that already had an epub took its date from that epub.
    conn = FakeConn(date("2019-01-01"), "2022-09-06", n_epubs=2)
    _adopt_epub_publication_date(conn, 1, 1)
    assert conn.updated_to is None


@pytest.mark.parametrize("raw", [
    "",
    None,
    "2022",                      # year-only: no better than the placeholder
    "2022-01-01T00:00:00+00:00",  # a placeholder itself
    "0101-01-01T00:00:00+00:00",  # the garbage sentinel 31 epubs carry
    "not a date",
])
def test_useless_epub_dates_are_ignored(raw):
    conn = FakeConn(date("2019-01-01"), raw)
    _adopt_epub_publication_date(conn, 1, 1)
    assert conn.updated_to is None


def test_drift_is_allowed_up_to_the_limit():
    conn = FakeConn(date("2019-01-01"), "2024-05-02")   # 5 years: still a guess
    _adopt_epub_publication_date(conn, 1, 1)
    assert conn.updated_to == "2024-05-02"

    conn = FakeConn(date("2019-01-01"), "2025-05-02")   # 6 years: too far
    _adopt_epub_publication_date(conn, 1, 1)
    assert conn.updated_to is None


# --- the m4b side ------------------------------------------------------------
#
# "Snake-Eater" arrived stating 2025-12-01 in its own ©day atom and was recorded
# as 2025-01-01, because the model knew the year and the prompt tells it to
# distrust raw date fields. Within a year already agreed on, the file is the
# better source; across years it is talking about the recording, not the work.

import datetime as _dt

from web.ingest.apply import _sharpen_publication_date_from_m4b


class _M4bConn(FakeConn):
    def __init__(self, current, raw, n_m4bs=1):
        super().__init__(current, raw, n_epubs=n_m4bs)
        self.row = {"current": current, "raw": raw, "n_m4bs": n_m4bs}


class TestSharpenFromM4b:
    def test_adds_the_day_inside_an_agreed_year(self):
        conn = _M4bConn(_dt.date(2025, 1, 1), "2025-12-01")
        _sharpen_publication_date_from_m4b(conn, 1, 1)
        assert conn.updated_to == "2025-12-01"

    def test_refuses_to_move_the_year(self):
        # A 2005 novel whose audiobook is dated 2007: the recording's date is
        # not the work's, and the year we have is the better answer.
        conn = _M4bConn(_dt.date(2005, 1, 1), "2007-10-02")
        _sharpen_publication_date_from_m4b(conn, 1, 1)
        assert conn.updated_to is None

    def test_leaves_a_real_date_alone(self):
        conn = _M4bConn(_dt.date(2025, 6, 14), "2025-12-01")
        _sharpen_publication_date_from_m4b(conn, 1, 1)
        assert conn.updated_to is None

    def test_no_date_at_all_leaves_no_year_to_agree_with(self):
        conn = _M4bConn(None, "2025-12-01")
        _sharpen_publication_date_from_m4b(conn, 1, 1)
        assert conn.updated_to is None

    def test_a_year_only_file_date_adds_nothing(self):
        conn = _M4bConn(_dt.date(2025, 1, 1), "2025")
        _sharpen_publication_date_from_m4b(conn, 1, 1)
        assert conn.updated_to is None

    def test_only_the_book_s_first_audiobook_speaks(self):
        conn = _M4bConn(_dt.date(2025, 1, 1), "2025-12-01", n_m4bs=2)
        _sharpen_publication_date_from_m4b(conn, 1, 1)
        assert conn.updated_to is None
