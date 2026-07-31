"""Removing the record of a file that should not have been read.

No database here, so what is under test is the SQL that gets built: which table
it deletes from, that the guard against deleting a linked asset is in the
statement itself, and that the audit-log row goes with it. The cascade to
authors/chapters/acquisitions is the schema's job and is not restated here.
"""

import pytest

from web.ingest import store


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConn:
    """Records every statement; hands back whatever the DELETE was told to."""

    def __init__(self, deleted_row):
        self._deleted_row = deleted_row
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))
        return _Result(self._deleted_row if "DELETE FROM epubs" in sql
                       or "DELETE FROM m4bs" in sql else None)


def test_deletes_the_row_and_hands_back_the_key():
    # The key, not just a yes: the object outlives the row, and the caller has
    # to tell the worker this file is unread again.
    conn = FakeConn({"s3_key": "Snake-Eater.m4b"})
    assert store.delete_asset(conn, "m4b", 7) == "Snake-Eater.m4b"
    assert "DELETE FROM m4bs" in conn.statements[0][0]


def test_a_missing_row_is_reported_not_raised():
    conn = FakeConn(None)
    assert store.delete_asset(conn, "epub", 99) is None


def test_the_link_guard_is_in_the_statement():
    # Not a check above the DELETE: the cascade would take book_m4bs with it and
    # quietly strip the audiobook off a book in the library.
    conn = FakeConn({"s3_key": "x.epub"})
    store.delete_asset(conn, "m4b", 7)
    sql = conn.statements[0][0]
    assert "NOT EXISTS" in sql and "book_m4bs" in sql and "m4b_id" in sql


def test_the_resolutions_row_goes_too():
    conn = FakeConn({"s3_key": "x.epub"})
    store.delete_asset(conn, "epub", 7)
    deletes = [s for s, _ in conn.statements if "DELETE FROM resolutions" in s]
    assert len(deletes) == 1
    assert conn.statements[1][1] == ("epub", 7)


def test_nothing_else_is_touched_when_there_was_no_row():
    conn = FakeConn(None)
    store.delete_asset(conn, "epub", 99)
    assert len(conn.statements) == 1


@pytest.mark.parametrize(
    "asset_type,table,join",
    [("epub", "epubs", "book_epubs"), ("m4b", "m4bs", "book_m4bs")],
)
def test_each_asset_type_hits_its_own_tables(asset_type, table, join):
    conn = FakeConn({"s3_key": "x.epub"})
    store.delete_asset(conn, asset_type, 1)
    sql = conn.statements[0][0]
    assert f"DELETE FROM {table}" in sql and join in sql
