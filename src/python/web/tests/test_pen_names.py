"""Crediting a pen name once instead of a person twice.

Publisher metadata routinely lists both halves as separate dc:creator entries,
and taken at face value that credited every Defiance of the Fall volume, five
He Who Fights with Monsters, five A Soldier's Life and two Mother of Learning
to one person under two names.

dropped_author_indexes answers the question once, for both the review card and
apply, so the card cannot promise a credit that accepting will not make. The
interesting tests are the ones where it must NOT fire: a genuine co-writing pair
looks identical from here except for the thing being checked.
"""

import pytest

from web.ingest.apply import dropped_author_indexes
from web.ingest.summary import proposal_rows

SHIRTALOON, DEVERELL, THIRD = 1, 2, 3


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConn:
    """Answers the author_pseudonyms lookup from (pen_id, real_id) pairs, and
    the people-by-name lookup that a `create` credit goes through."""

    def __init__(self, links=(), people=None):
        self.links = list(links)
        self.people = people or {}  # name -> id, for find_person_exact
        self.queried = False

    def execute(self, sql, params=None):
        if "author_pseudonyms" in sql:
            self.queried = True
            if "ids" in params:  # dropped_author_indexes: links among the credited
                ids = set(params["ids"])
                return _Result(
                    [{"author_id": r} for p, r in self.links if p in ids and r in ids]
                )
            # _pseudonym_rows: links between this pen name and these real people,
            # in either direction.
            pen, reals = params["pen"], set(params["reals"])
            return _Result([
                {"pseudonym_id": p, "author_id": r}
                for p, r in self.links
                if (p == pen and r in reals) or (r == pen and p in reals)
            ])
        if "FROM people" in sql:
            name = params["n"]
            hit = self.people.get(name)
            return _Result([{"id": hit, "name": name}] if hit else [])
        return _Result()


def _proposal(authors, pseudonyms=()):
    return {"book": {"link": 1}, "authors": list(authors), "pseudonyms": list(pseudonyms)}


link = lambda pid, raw="": {"link": pid, "raw_name": raw}
create = lambda name: {"create": {"name": name}, "raw_name": name}


class TestDroppedAuthorIndexes:
    def test_drops_the_person_behind_a_credited_pen_name(self):
        conn = FakeConn([(SHIRTALOON, DEVERELL)])
        assert dropped_author_indexes(
            conn, _proposal([link(SHIRTALOON), link(DEVERELL)])
        ) == {1}

    def test_keeps_a_genuine_co_writing_pair(self):
        conn = FakeConn([])
        assert dropped_author_indexes(conn, _proposal([link(1), link(2)])) == set()

    def test_keeps_the_real_person_when_the_pen_name_is_not_credited(self):
        conn = FakeConn([(SHIRTALOON, DEVERELL)])
        assert dropped_author_indexes(
            conn, _proposal([link(DEVERELL), link(THIRD)])
        ) == set()

    def test_keeps_a_co_author_who_is_nobody_s_pen_name(self):
        conn = FakeConn([(SHIRTALOON, DEVERELL)])
        assert dropped_author_indexes(
            conn, _proposal([link(SHIRTALOON), link(DEVERELL), link(THIRD)])
        ) == {1}

    def test_a_reciprocal_link_does_not_uncredit_the_book(self):
        # nobody103 <-> Domagoj Kurmaić are stored both ways round in the live
        # data, so each looks like the other's real name. Dropping both would
        # leave the book with no author at all, silently.
        conn = FakeConn([(1, 2), (2, 1)])
        assert dropped_author_indexes(conn, _proposal([link(1), link(2)])) == set()

    def test_a_single_credit_needs_no_lookup(self):
        conn = FakeConn([(SHIRTALOON, DEVERELL)])
        assert dropped_author_indexes(conn, _proposal([link(SHIRTALOON)])) == set()
        assert not conn.queried

    def test_the_proposal_s_own_pseudonym_link_counts(self):
        # The first book that tells us about a pair should benefit from it,
        # before the link is in the table at all.
        conn = FakeConn([])
        proposal = _proposal(
            [create("TheFirstDefier"), create("JF Brink")],
            [{"pseudonym_name": "TheFirstDefier", "real_person_names": ["JF Brink"]}],
        )
        assert dropped_author_indexes(conn, proposal) == {1}

    def test_a_pseudonym_link_for_someone_not_credited_is_ignored(self):
        conn = FakeConn([])
        proposal = _proposal(
            [create("Someone"), create("Another")],
            [{"pseudonym_name": "Richard Bachman", "real_person_names": ["Another"]}],
        )
        assert dropped_author_indexes(conn, proposal) == set()

    def test_name_matching_ignores_punctuation_and_inversion(self):
        conn = FakeConn([])
        proposal = _proposal(
            [create("TheFirstDefier"), create("Brink, JF")],
            [{"pseudonym_name": "TheFirstDefier", "real_person_names": ["J.F. Brink"]}],
        )
        assert dropped_author_indexes(conn, proposal) == {1}


class TestTheCardSaysSo:
    """The card must show the dropped credit as dropped -- the whole reason the
    answer is computed once and shared."""

    def test_a_dropped_credit_is_marked_and_explained(self):
        proposal = _proposal([link(SHIRTALOON, "Shirtaloon"), link(DEVERELL, "Travis Deverell")])
        names = {"people": {SHIRTALOON: "Shirtaloon", DEVERELL: "Travis Deverell"},
                 "dropped_authors": [1]}
        rows = [r for r in proposal_rows(proposal, names) if r["label"] == "Author"]
        assert rows[0]["verb"] == "link" and not rows[0]["warning"]
        assert rows[1]["verb"] == "skip"
        assert "pseudonym" in rows[1]["warning"]
        assert "Travis Deverell" in rows[1]["text"]

    def test_nothing_is_marked_when_nothing_is_dropped(self):
        proposal = _proposal([link(1, "A"), link(2, "B")])
        rows = [r for r in proposal_rows(proposal, {"dropped_authors": []})
                if r["label"] == "Author"]
        assert all(r["verb"] == "link" for r in rows)


class TestPseudonymRowsResolve:
    """The card must say whether accepting links two people or invents them.

    The pseudonym path in apply carries no ids -- it resolves by name alone --
    so a spelling variant silently makes a second person. That is precisely
    what the row now has to show.
    """

    def _rows(self, conn, pen, reals):
        from web.ingest.summary import _pseudonym_rows
        return _pseudonym_rows(
            conn, {"pseudonyms": [{"pseudonym_name": pen, "real_person_names": reals}]}
        )

    def test_both_sides_known_reads_as_a_link(self):
        conn = FakeConn(people={"Shirtaloon": SHIRTALOON, "Travis Deverell": DEVERELL})
        row = self._rows(conn, "Shirtaloon", ["Travis Deverell"])[0]
        assert row["verb"] == "link"
        assert row["text"] == "#1 Shirtaloon → #2 Travis Deverell"

    def test_an_unknown_name_is_flagged_as_a_new_person(self):
        conn = FakeConn(people={"Shirtaloon": SHIRTALOON})
        row = self._rows(conn, "Shirtaloon", ["Travis Deverel"])[0]
        assert row["verb"] == "create"
        assert "new person" in row["text"]

    def test_an_existing_link_is_marked_as_nothing_to_add(self):
        conn = FakeConn(
            links=[(SHIRTALOON, DEVERELL)],
            people={"Shirtaloon": SHIRTALOON, "Travis Deverell": DEVERELL},
        )
        row = self._rows(conn, "Shirtaloon", ["Travis Deverell"])[0]
        assert row["verb"] == "skip"
        assert "already recorded" in row["warning"]

    def test_a_link_recorded_both_ways_writes_nothing(self):
        # The live nobody103 / Domagoj Kurmaić state: reporting this as a link
        # about to be made would be wrong twice over, since ON CONFLICT DO
        # NOTHING means accepting writes no row at all.
        conn = FakeConn(
            links=[(SHIRTALOON, DEVERELL), (DEVERELL, SHIRTALOON)],
            people={"Shirtaloon": SHIRTALOON, "Travis Deverell": DEVERELL},
        )
        row = self._rows(conn, "Shirtaloon", ["Travis Deverell"])[0]
        assert row["verb"] == "skip"
        assert "both directions" in row["warning"]

    def test_a_reverse_link_is_called_out(self):
        # Accepting would leave the pair pointing at each other, which is the
        # state nobody103 and Domagoj Kurmaić are already in.
        conn = FakeConn(
            links=[(DEVERELL, SHIRTALOON)],
            people={"Shirtaloon": SHIRTALOON, "Travis Deverell": DEVERELL},
        )
        row = self._rows(conn, "Shirtaloon", ["Travis Deverell"])[0]
        assert "other way round" in row["warning"]
        assert row["verb"] != "skip"  # it would still write a row
