"""Crediting a pen name once instead of a person twice.

Publisher metadata routinely lists both halves as separate dc:creator entries,
and taken at face value that credited every Defiance of the Fall volume, five
He Who Fights with Monsters and two Mother of Learning to one person under two
names. The prompt now says not to; this is the half that does not depend on the
model having listened.

The interesting tests are the ones where it must NOT fire: a real co-writing
pair looks identical from here except for the thing being checked.
"""

import pytest

from web.ingest.apply import _credit_pen_name_only


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    """Answers the author_pseudonyms lookup from a set of (pen, real) pairs."""

    def __init__(self, links=()):
        self.links = list(links)
        self.queried = False

    def execute(self, sql, params=None):
        self.queried = True
        ids = set(params["ids"])
        return _Result(
            [{"author_id": real} for pen, real in self.links if pen in ids and real in ids]
        )


SHIRTALOON, DEVERELL, SOMEONE_ELSE = 1, 2, 3


class TestCreditPenNameOnly:
    def test_drops_the_person_behind_a_credited_pen_name(self):
        conn = FakeConn([(SHIRTALOON, DEVERELL)])
        assert _credit_pen_name_only(conn, [SHIRTALOON, DEVERELL]) == [SHIRTALOON]

    def test_keeps_a_genuine_co_writing_pair(self):
        # Niven and Pournelle: two people, no link between them.
        conn = FakeConn([])
        assert _credit_pen_name_only(conn, [1, 2]) == [1, 2]

    def test_keeps_the_real_person_when_the_pen_name_is_not_credited(self):
        # A book published under the legal name is by that person, and the fact
        # that they also write as someone else is irrelevant to this book.
        conn = FakeConn([(SHIRTALOON, DEVERELL)])
        assert _credit_pen_name_only(conn, [DEVERELL, SOMEONE_ELSE]) == [DEVERELL, SOMEONE_ELSE]

    def test_keeps_a_co_author_who_is_nobody_s_pen_name(self):
        conn = FakeConn([(SHIRTALOON, DEVERELL)])
        assert _credit_pen_name_only(
            conn, [SHIRTALOON, DEVERELL, SOMEONE_ELSE]
        ) == [SHIRTALOON, SOMEONE_ELSE]

    def test_a_reciprocal_link_does_not_uncredit_the_book(self):
        # nobody103 <-> Domagoj Kurmaić are stored both ways round in the live
        # data, so each looks like the other's real name. Dropping both would
        # leave the book with no author at all, silently.
        conn = FakeConn([(1, 2), (2, 1)])
        assert _credit_pen_name_only(conn, [1, 2]) == [1, 2]

    def test_order_is_preserved(self):
        conn = FakeConn([(SHIRTALOON, DEVERELL)])
        assert _credit_pen_name_only(conn, [5, DEVERELL, SHIRTALOON, 6]) == [5, SHIRTALOON, 6]

    @pytest.mark.parametrize("ids", [[], [7]])
    def test_a_single_credit_needs_no_lookup(self, ids):
        conn = FakeConn([(SHIRTALOON, DEVERELL)])
        assert _credit_pen_name_only(conn, ids) == ids
        assert not conn.queried
