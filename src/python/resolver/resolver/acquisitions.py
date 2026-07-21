"""Acquisition dates from an Amazon "Your Orders" data export.

The dates already in epub_acquisitions/m4b_acquisitions came from the old
library's assets.acq_date, which recorded when a file was *added to the old
library* -- a proxy for the purchase, and one that stops in 2024. The Amazon
export has the real thing.

This module matches the export against the library and emits a SQL transcript
(src/sql/fixes/acquisitions.sql) to be replayed on production, in the same
spirit as src/sql/fixes/series.sql. It never writes to the database itself.

Two keys, doing different jobs, which is worth keeping straight:

  * ASIN matches Amazon to an asset. It is exact, and it is what epubs use.
  * s3_key addresses an asset in a database. It is what the emitted SQL is
    keyed on, because ASIN cannot do that job: a third of the epubs have no
    ASIN and none of the m4bs do. s3_key is NOT NULL UNIQUE on both tables and
    identical on both machines, since both ingest from the same bucket.

Audiobooks are the hard side. The m4b files carry no iTunes ASIN atom, so the
only join to an Audible order is the product name, and the match has to be
unique to be trusted. Where there is no Audible match, the date is inherited
from the epub of the same book: ebook and audiobook are usually bought
together. Usually -- not always, which is why inheriting is the fallback and
not the rule. Books read years before the audiobook was bought are exactly the
ones a sort-by-acquired view puts on the first page.
"""

import csv
import datetime
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import psycopg

from .normalize import loose_title, volume_number

log = logging.getLogger(__name__)

# Where the export lives inside the resolver container (docker-compose mounts
# data/amazon-order-info read-only). Amazon's own directory names, spaces and
# all, are kept as-is so a fresh download can be dropped in unmodified.
DEFAULT_EXPORT = Path("/amazon")
_ORDERS = Path("Your Orders/Your Amazon Orders/Digital Content Orders.csv")
_BORROWED = Path("Your Orders/Your Amazon Orders/Digital Borrowed Items.csv")

DEFAULT_OUT = Path("/sql/fixes/acquisitions.sql")

# Order Date is UTC ("2023-04-27T03:23:00Z"), and a late-evening purchase in
# Boulder lands on the next UTC day. Converting first agrees with the old
# library's dates substantially more often than not converting, which is the
# evidence that the old library recorded local dates.
LOCAL = ZoneInfo("America/Denver")

# Amazon's seller-of-record for audiobooks. Everything else in this file --
# "Amazon.com Services, Inc", the agency-model publishers -- is Kindle.
AUDIBLE = "Audible"


@dataclass(frozen=True)
class Match:
    """One asset's acquisition date, and where it came from."""

    s3_key: str
    acquired_on: datetime.date
    method: str  # asin | kindle-unlimited | title | audible-title | via-epub
    detail: str  # ASIN, or the s3_key inherited from
    old: datetime.date | None = None  # what the database has now, if anything

    @property
    def change(self) -> str:
        if self.old is None:
            return "new"
        if self.old == self.acquired_on:
            return "same"
        return "changed"


# --- the export --------------------------------------------------------------


@dataclass(frozen=True)
class Order:
    asin: str
    when: datetime.date
    product_name: str
    audible: bool


def _local_date(timestamp: str) -> datetime.date:
    return (
        datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        .astimezone(LOCAL)
        .date()
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    # utf-8-sig: Amazon writes a BOM, which would otherwise become part of the
    # first column's name and make 'ASIN' a KeyError.
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_orders(export: Path) -> dict[str, Order]:
    """Earliest successful order per ASIN.

    Earliest, because a re-purchase or a format upgrade should not move a book
    to the top of a by-acquisition sort -- the library got it the first time.
    Each order appears twice in the file (a Price Amount row and a Tax row);
    keying by ASIN collapses that.
    """
    orders: dict[str, Order] = {}
    for row in _read_csv(export / _ORDERS):
        if row["Order Status"] != "SUCCESS":
            continue
        asin = row["ASIN"]
        when = _local_date(row["Order Date"])
        if asin in orders and orders[asin].when <= when:
            continue
        orders[asin] = Order(
            asin=asin,
            when=when,
            product_name=row["Product Name"],
            audible=row["Seller of Record"] == AUDIBLE,
        )
    return orders


def read_borrowed(export: Path) -> dict[str, datetime.date]:
    """Earliest loan date per ASIN, from Kindle Unlimited and library loans.

    A borrowed book was never bought, but it was acquired: the loan is when it
    entered the library, which is the question acquired_on answers.
    """
    loans: dict[str, datetime.date] = {}
    for row in _read_csv(export / _BORROWED):
        asin = row["ASIN"]
        when = _local_date(row["Loan Creation Date"])
        if asin not in loans or when < loans[asin]:
            loans[asin] = when
    return loans


# --- title matching ----------------------------------------------------------


Index = dict[str, dict[str, int | None]]

# loose_title() splits a title from its subtitle on a colon, which is how Amazon
# writes it ("Adventures in the Argo: Beneath the Dragoneye Moons, Book 2"). The
# m4b tags on disk often use a spaced dash for the same seam, and put the parts in
# either order -- "Adventures in the Argo - Beneath the Dragoneye Moons, Book 2",
# but also "Cory Doctorow - Red Team Blues". So both sides are tried. Widening
# loose_title() itself was the alternative and the wrong one: the resolver's
# book-linking and audit depend on it staying strict.
_DASH = re.compile(r"\s+[-–—]\s+")


def _variants(text: str) -> list[str]:
    return [text, *(p for p in _DASH.split(text, maxsplit=1) if p.strip())]


def _index(orders: Iterable[Order]) -> Index:
    """Orders grouped by loose title, each remembering its volume number.

    loose_title() drops subtitles and parentheticals, which is what lets
    "A Psalm for the Wild-Built: Monk & Robot, Book 1" meet the way the same
    book is named elsewhere -- but it also drops the volume number, and without
    that every volume of a series would collide. So the volume rides alongside
    rather than in the key: it is a tie-breaker, not a precondition. Requiring
    it to match outright would lose every book one side numbers and the other
    does not, which is most of them.
    """
    index: Index = {}
    for order in orders:
        index.setdefault(loose_title(order.product_name), {})[order.asin] = (
            volume_number(order.product_name)
        )
    return index


def _lookup(index: Index, *candidates: str | None) -> str | None:
    """The one ASIN all the candidate strings point at, or None.

    An m4b offers several names for the same book -- the ©nam title, the ©alb
    album (which often carries the series), and the curated book title -- and
    any of them may be the one phrased like the order. Their hits are unioned,
    then narrowed by volume number if the candidates name one. Anything still
    ambiguous (the "A Thousand Li" volumes, whose subtitles all vanish) is
    dropped: a wrong date is worse than a missing one.
    """
    hits: dict[str, int | None] = {}
    volumes: set[int] = set()
    for candidate in candidates:
        if not candidate:
            continue
        for variant in _variants(candidate):
            hits.update(index.get(loose_title(variant), {}))
        # From the whole string, not the variants: the volume is a property of
        # the book, and a variant that drops it must not be read as "no volume".
        if (v := volume_number(candidate)) is not None:
            volumes.add(v)

    if len(volumes) > 1:
        return None  # the candidates disagree about which volume this even is
    if volumes:
        volume = volumes.pop()
        # An order that names our volume beats one that names none, and one that
        # names a different volume is not this book at all -- which is what stops
        # "All the Skills 2" from matching an order for the first book.
        exact = {a: v for a, v in hits.items() if v == volume}
        hits = exact or {a: v for a, v in hits.items() if v is None}

    return next(iter(hits)) if len(hits) == 1 else None


# --- matching against the library --------------------------------------------


def match_epubs(
    conn: psycopg.Connection,
    orders: dict[str, Order],
    loans: dict[str, datetime.date],
) -> tuple[list[Match], list[dict]]:
    """Returns (matches, unmatched epub rows)."""
    rows = conn.execute(
        """SELECT e.id, e.s3_key, e.asin, e.title, a.acquired_on AS old
           FROM epubs e
           LEFT JOIN epub_acquisitions a ON a.epub_id = e.id
           ORDER BY e.id"""
    ).fetchall()
    kindle = _index(o for o in orders.values() if not o.audible)

    matches: list[Match] = []
    unmatched: list[dict] = []
    for row in rows:
        asin, old = row["asin"], row["old"]
        if asin and asin in orders:
            matches.append(
                Match(row["s3_key"], orders[asin].when, "asin", asin, old)
            )
        elif asin and asin in loans:
            matches.append(
                Match(row["s3_key"], loans[asin], "kindle-unlimited", asin, old)
            )
        elif (hit := _lookup(kindle, row["title"])) is not None:
            matches.append(
                Match(row["s3_key"], orders[hit].when, "title", hit, old)
            )
        else:
            unmatched.append(row)
    return matches, unmatched


def match_m4bs(
    conn: psycopg.Connection,
    orders: dict[str, Order],
    epub_dates: dict[int, datetime.date],
) -> tuple[list[Match], list[dict], list[dict]]:
    """Returns (matches, unmatched m4b rows, disagreements).

    `epub_dates` maps epub id to the date matched for it, so an m4b with no
    Audible order can inherit from the ebook of the same book. Disagreements --
    m4bs where both sources exist but differ by more than a month -- are
    reported rather than silently resolved: they are the cases that justify
    preferring the Audible order, and worth an eye before the SQL is committed.
    """
    rows = conn.execute(
        """SELECT m.id, m.s3_key, m.title, m.album, b.title AS book_title,
                  a.acquired_on AS old,
                  array_remove(array_agg(be.epub_id), NULL) AS epub_ids
           FROM m4bs m
           LEFT JOIN m4b_acquisitions a ON a.m4b_id = m.id
           LEFT JOIN book_m4bs bm ON bm.m4b_id = m.id
           LEFT JOIN books b ON b.id = bm.book_id
           LEFT JOIN book_epubs be ON be.book_id = bm.book_id
           GROUP BY m.id, m.s3_key, m.title, m.album, b.title, a.acquired_on
           ORDER BY m.id"""
    ).fetchall()
    audible = _index(o for o in orders.values() if o.audible)

    matches: list[Match] = []
    unmatched: list[dict] = []
    disagreements: list[dict] = []
    for row in rows:
        old = row["old"]
        hit = _lookup(audible, row["title"], row["album"], row["book_title"])
        # Earliest, because a book with two epubs was acquired when the first
        # one arrived.
        paired = [epub_dates[e] for e in row["epub_ids"] if e in epub_dates]
        inherited = min(paired) if paired else None

        if hit is not None:
            when = orders[hit].when
            matches.append(Match(row["s3_key"], when, "audible-title", hit, old))
            if inherited is not None and abs((inherited - when).days) > 31:
                disagreements.append(
                    {"title": row["title"], "audible": when, "epub": inherited}
                )
        elif inherited is not None:
            matches.append(
                Match(row["s3_key"], inherited, "via-epub", "paired epub", old)
            )
        else:
            unmatched.append(row)
    return matches, unmatched, disagreements


# --- the emitted transcript --------------------------------------------------

_HEADER = """\
-- Acquisition dates from an Amazon "Your Orders" export, generated by
--
--     util/resolver.sh acquisitions
--
-- This is a transcript, not a migration: it is regenerated whole every time a
-- fresh export is downloaded, so it must not be given a migration number (the
-- runner would apply one filename once and ignore every later regeneration).
-- Replaying it is a no-op -- the upserts are ON CONFLICT DO UPDATE.
--
-- It DOES NOTHING BY DEFAULT. It reports what it would change and rolls back:
--
--     util/psql.sh            < src/sql/fixes/acquisitions.sql   -- report only
--     util/psql.sh -v apply=1 < src/sql/fixes/acquisitions.sql   -- and keep it
--
-- Keyed on s3_key, never on ids, so it is correct on production even though its
-- id values need not match local's. The comment on each row is how the date was
-- found: `asin` and `kindle-unlimited` are exact ASIN matches, `title` and
-- `audible-title` are unique normalized-title matches, and `via-epub` is
-- inherited from the ebook of the same book (audiobooks carry no ASIN).
--
-- Generated {generated} from {export}.
"""

_TABLES = {
    "epub": ("epub_acquisitions", "epubs", "epub_id"),
    "m4b": ("m4b_acquisitions", "m4bs", "m4b_id"),
}


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _values(matches: list[Match]) -> str:
    """The VALUES rows, one per line, each annotated with how it was matched,
    terminated with a semicolon.

    Punctuation goes before the comment and never after. A trailing `--` runs to
    end of line, so a comma or a semicolon placed after one is commented out, and
    the file silently stops being SQL.
    """
    ordered = sorted(matches, key=lambda m: m.s3_key)
    width = max(len(_sql_string(m.s3_key)) for m in ordered)
    return "\n".join(
        f"  ({_sql_string(m.s3_key):<{width}}, DATE '{m.acquired_on}')"
        f"{',' if i < len(ordered) - 1 else ';'}  -- {m.method} {m.detail}"
        for i, m in enumerate(ordered)
    )


def _upsert(kind: str, matches: list[Match]) -> str:
    acq, assets, fk = _TABLES[kind]
    if not matches:
        return f"-- no {kind} matches\n"

    return f"""\
CREATE TEMP TABLE src_{kind} (s3_key TEXT PRIMARY KEY, acquired_on DATE NOT NULL);
INSERT INTO src_{kind} VALUES
{_values(matches)}

CREATE TEMP TABLE before_{kind} AS SELECT {fk}, acquired_on FROM {acq};

INSERT INTO {acq} ({fk}, acquired_on)
SELECT a.id, s.acquired_on FROM src_{kind} s JOIN {assets} a USING (s3_key)
ON CONFLICT ({fk}) DO UPDATE SET acquired_on = EXCLUDED.acquired_on;

-- An s3_key here that is not in {assets} is a stale row: the file was renamed or
-- removed since this was generated. Named out loud, because the alternative is
-- silently doing less than the file says.
SELECT '{kind}: no such s3_key' AS problem, s.s3_key
FROM src_{kind} s
WHERE NOT EXISTS (SELECT 1 FROM {assets} a WHERE a.s3_key = s.s3_key);

-- Counted through src_{kind}, not over the whole table: the rows this file did
-- not touch are not part of the answer to "what would this do?".
SELECT '{kind}' AS asset,
       count(*) FILTER (WHERE b.{fk} IS NULL)                 AS inserted,
       count(*) FILTER (WHERE b.acquired_on <> n.acquired_on) AS changed,
       count(*) FILTER (WHERE b.acquired_on =  n.acquired_on) AS unchanged
FROM src_{kind} s
JOIN {assets} a ON a.s3_key = s.s3_key
JOIN {acq} n ON n.{fk} = a.id
LEFT JOIN before_{kind} b ON b.{fk} = a.id;

SELECT a.s3_key, b.acquired_on AS was, n.acquired_on AS now
FROM {acq} n
JOIN before_{kind} b USING ({fk})
JOIN {assets} a ON a.id = n.{fk}
WHERE b.acquired_on <> n.acquired_on
ORDER BY a.s3_key;
"""


def render_sql(
    epubs: list[Match], m4bs: list[Match], export: Path
) -> str:
    body = "\n".join(
        [
            _HEADER.format(
                # Local, like every other date in this file. The container runs
                # on UTC, so today() would put tomorrow's date on an evening run.
                generated=datetime.datetime.now(LOCAL).date().isoformat(),
                export=export,
            ),
            r"\set ON_ERROR_STOP on",
            "BEGIN;",
            "",
            _upsert("epub", epubs),
            _upsert("m4b", m4bs),
            r"\if :{?apply}",
            "COMMIT;",
            r"\echo '*** applied ***'",
            r"\else",
            "ROLLBACK;",
            r"\echo '*** DRY RUN -- rolled back. Re-run with -v apply=1 to keep it. ***'",
            r"\endif",
            "",
        ]
    )
    return body


# --- entry point -------------------------------------------------------------


def run(
    conn: psycopg.Connection,
    export: Path = DEFAULT_EXPORT,
    out: Path = DEFAULT_OUT,
    dry_run: bool = False,
) -> dict:
    orders = read_orders(export)
    loans = read_borrowed(export)
    log.info(
        "export: %d ordered ASINs (%d audible), %d borrowed",
        len(orders),
        sum(1 for o in orders.values() if o.audible),
        len(loans),
    )

    epub_matches, epub_missed = match_epubs(conn, orders, loans)
    # Ids, not s3_keys, because that is what book_epubs joins on.
    ids = {
        r["s3_key"]: r["id"]
        for r in conn.execute("SELECT id, s3_key FROM epubs").fetchall()
    }
    epub_dates = {ids[m.s3_key]: m.acquired_on for m in epub_matches}
    m4b_matches, m4b_missed, disagreements = match_m4bs(conn, orders, epub_dates)

    report = _render_report(
        epub_matches, epub_missed, m4b_matches, m4b_missed, disagreements
    )

    if not dry_run:
        out.write_text(render_sql(epub_matches, m4b_matches, export))
        log.info("wrote %s", out)

    return {
        "report": report,
        "epubs": epub_matches,
        "m4bs": m4b_matches,
        "disagreements": disagreements,
    }


def _counts(matches: list[Match], key) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in matches:
        counts[key(m)] = counts.get(key(m), 0) + 1
    return counts


def _render_report(
    epub_matches: list[Match],
    epub_missed: list[dict],
    m4b_matches: list[Match],
    m4b_missed: list[dict],
    disagreements: list[dict],
) -> str:
    lines: list[str] = []

    for label, matches, missed in (
        ("epubs", epub_matches, epub_missed),
        ("m4bs", m4b_matches, m4b_missed),
    ):
        lines.append(f"== {label}: {len(matches)} matched, {len(missed)} not ==")
        for method, n in sorted(_counts(matches, lambda m: m.method).items()):
            lines.append(f"  {method:<16} {n}")
        for change, n in sorted(_counts(matches, lambda m: m.change).items()):
            lines.append(f"  -> {change:<13} {n}")
        lines.append("")

    changed = [m for m in epub_matches + m4b_matches if m.change == "changed"]
    lines.append(f"== {len(changed)} dates change ==")
    for m in sorted(changed, key=lambda m: m.s3_key):
        lines.append(f"  {m.old} -> {m.acquired_on}  {m.s3_key}  ({m.method})")
    lines.append("")

    if disagreements:
        lines.append(
            f"== {len(disagreements)} audiobooks bought well after the ebook "
            f"(audible order wins) =="
        )
        for d in disagreements:
            lines.append(f"  epub {d['epub']}  audible {d['audible']}  {d['title']}")
        lines.append("")

    if epub_missed or m4b_missed:
        lines.append(
            f"== no match: {len(epub_missed)} epubs, {len(m4b_missed)} m4bs "
            f"(dates they already have are left alone) =="
        )
        for row in epub_missed + m4b_missed:
            lines.append(f"  {'(dated)' if row['old'] else '(no date)':<9} {row['s3_key']}")

    return "\n".join(lines)
