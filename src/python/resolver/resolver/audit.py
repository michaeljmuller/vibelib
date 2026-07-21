"""Read-only audit of epub/m4b -> abstract book associations.

Finds places where the resolver mis-associated an asset. Reports only; nothing
here writes to the catalog. The false positives are the dangerous kind — "The
Path of Ascension 2" and "…Book 3" are *different books* — so every finding is
for a human to judge.

The checks, worst first:

  audio_without_ebook  a book with an m4b and no epub. Audiobooks are almost
                       always bought alongside the ebook, so an audio-only book
                       usually means its epub is sitting on a *different*
                       abstract book — one real book split across two entities.
  multi_asset_books    a book with >1 epub or >1 m4b. An abstract book should
                       have at most one of each. Either distinct books were
                       collapsed into one entity, or the same book was uploaded
                       twice; the remedies differ, so the report says which.
  omnibus              one audio file spanning several epub volumes. The schema
                       cannot express this; surfaced, never "fixed".
  orphans              assets with no book link, plus the review backlog.
"""

import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Literal

import psycopg

from .normalize import loose_title, norm_person, split_authors, volume_number


_VOLUME_PHRASE = re.compile(
    r"\b(?:book|volume|vol|arc|part|episode)s?\.?\s*#?\s*"
    r"(?:\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b"
    r"|#\s*\d{1,3}\b|\s\d{1,3}\s*$",
    re.IGNORECASE,
)


def base_title(title: str | None) -> str:
    """Title key with the volume phrase removed, so 'Mother of Learning Arc 1'
    and 'Mother of Learning: ARC 1' agree. Pair it with volume_number(), which
    carries the number the key drops."""
    if not title:
        return ""
    stripped = _VOLUME_PHRASE.sub(" ", title.split(":")[0])
    return loose_title(stripped) or loose_title(title)


def _author_tokens(names: list[str]) -> set[str]:
    """Name tokens for a tolerant author comparison. Deliberately loose: an m4b's
    artist field mixes inverted names and comma-joined multi-author credits
    ('Shirtaloon, Travis Deverell'), which split_authors() won't separate, so an
    exact person match would miss real pairs."""
    tokens: set[str] = set()
    for name in names:
        for part in name.replace(",", " ").split():
            tok = norm_person(part)
            if len(tok) > 2:
                tokens.add(tok)
    return tokens


def _authors_agree(a: list[str], b: list[str]) -> bool:
    ta, tb = _author_tokens(a), _author_tokens(b)
    return bool(ta & tb) if ta and tb else False


# --- findings ----------------------------------------------------------------

Verdict = Literal["confident", "ambiguous", "collapsed", "duplicate-file", "info"]


@dataclass
class Finding:
    check: str
    verdict: Verdict
    summary: str
    evidence: list[str] = field(default_factory=list)
    book_id: int | None = None
    depends_on: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "verdict": self.verdict,
            "summary": self.summary,
            "evidence": self.evidence,
            "book_id": self.book_id,
            "depends_on": self.depends_on,
        }


OMNIBUS = re.compile(
    r"omnibus|box\s?set|publisher.?s\s+pack|collection"
    r"|books?\s*\d+\s*[-–]\s*\d+|vol(?:ume)?s?\.?\s*\d+\s*[-–]\s*\d+",
    re.IGNORECASE,
)


# --- data loading ------------------------------------------------------------


def _load_epubs(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return conn.execute(
        """SELECT e.id, e.title, e.s3_key, be.book_id,
                  COALESCE((SELECT array_agg(ea.author ORDER BY ea.position)
                            FROM epub_authors ea WHERE ea.epub_id = e.id),
                           ARRAY[]::text[]) AS authors
           FROM epubs e LEFT JOIN book_epubs be ON be.epub_id = e.id
           ORDER BY e.id"""
    ).fetchall()


def _load_m4bs(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return conn.execute(
        """SELECT m.id, m.title, m.album, m.artist, m.s3_key, m.duration_s, bm.book_id
           FROM m4bs m LEFT JOIN book_m4bs bm ON bm.m4b_id = m.id
           ORDER BY m.id"""
    ).fetchall()


def _load_books(conn: psycopg.Connection) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        """SELECT b.id, b.title, b.series_id, b.series_position, s.name AS series_name,
                  COALESCE((SELECT array_agg(p.name ORDER BY ba.position)
                            FROM book_authors ba JOIN people p ON p.id = ba.author_id
                            WHERE ba.book_id = b.id), ARRAY[]::text[]) AS authors
           FROM books b LEFT JOIN series s ON s.id = b.series_id"""
    ).fetchall()
    return {r["id"]: r for r in rows}


# --- checks ------------------------------------------------------------------


def audio_without_ebook(
    books: dict[int, dict], epubs: list[dict], m4bs: list[dict]
) -> tuple[list[Finding], list[dict]]:
    """Books with an m4b and no epub. Hunt each one's epub across the whole
    library — linked or not. Returns (findings, ambiguous_shortlist)."""
    with_epub = {e["book_id"] for e in epubs if e["book_id"]}
    audio_only = sorted(
        {m["book_id"] for m in m4bs if m["book_id"] and m["book_id"] not in with_epub}
    )

    findings: list[Finding] = []
    shortlist: list[dict] = []

    for book_id in audio_only:
        book = books[book_id]
        m4b = next(m for m in m4bs if m["book_id"] == book_id)

        if OMNIBUS.search(f"{book['title']} {m4b['title'] or ''} {m4b['album'] or ''}"):
            continue  # handled by omnibus(); force-linking these would be wrong

        key, vol = base_title(m4b["title"]), volume_number(m4b["title"])
        m4b_authors = split_authors(m4b["artist"])

        candidates = [
            e
            for e in epubs
            if base_title(e["title"]) == key
            and (vol is None or volume_number(e["title"]) in (None, vol))
            and _authors_agree(m4b_authors, list(e["authors"]))
        ]
        # An exact volume match beats a volume-unknown one: "He Who Fights with
        # Monsters" (book 1) and "…10" share a base title.
        exact = [e for e in candidates if volume_number(e["title"]) == vol]
        if exact:
            candidates = exact

        if not candidates:
            findings.append(
                Finding(
                    "audio_without_ebook",
                    "ambiguous",
                    f"book {book_id} \"{book['title']}\" is audio-only — no epub found",
                    [f"m4b {m4b['id']} {m4b['title']!r} by {m4b['artist']!r}",
                     "confirm you really own no ebook for this one"],
                    book_id,
                )
            )
            continue

        if len(candidates) == 1:
            e = candidates[0]
            where = (
                f"currently on book {e['book_id']}"
                if e["book_id"]
                else "currently UNLINKED"
            )
            depends = None
            if e["book_id"] and _is_multi_asset(e["book_id"], epubs, m4bs):
                depends = (
                    f"book {e['book_id']} is itself a multi-asset book — split it "
                    f"before moving this epub"
                )
            findings.append(
                Finding(
                    "audio_without_ebook",
                    "confident",
                    f"book {book_id} \"{book['title']}\" holds the audiobook; its epub "
                    f"is on a different book — one book split in two",
                    [
                        f"m4b {m4b['id']} {m4b['title']!r}",
                        f"epub {e['id']} {e['title']!r} — {where}",
                    ],
                    book_id,
                    depends,
                )
            )
            continue

        shortlist.append({"book": book, "m4b": m4b, "candidates": candidates})

    return findings, shortlist


def _is_multi_asset(book_id: int, epubs: list[dict], m4bs: list[dict]) -> bool:
    return (
        sum(1 for e in epubs if e["book_id"] == book_id) > 1
        or sum(1 for m in m4bs if m["book_id"] == book_id) > 1
    )


def multi_asset_books(
    books: dict[int, dict], epubs: list[dict], m4bs: list[dict]
) -> list[Finding]:
    """Every book with >1 epub or >1 m4b, classified per asset pair."""
    findings: list[Finding] = []

    for kind, assets in (("epub", epubs), ("m4b", m4bs)):
        by_book: dict[int, list[dict]] = {}
        for a in assets:
            if a["book_id"]:
                by_book.setdefault(a["book_id"], []).append(a)

        for book_id, group in sorted(by_book.items()):
            if len(group) < 2:
                continue
            book = books[book_id]
            for i, a in enumerate(group):
                for b in group[i + 1 :]:
                    va, vb = volume_number(a["title"]), volume_number(b["title"])
                    # loose_title here, NOT base_title: this check has to *tell
                    # volumes apart* ("The Path of Ascension" vs "… 4"), so the key
                    # must keep the number. base_title strips it, which is what the
                    # cross-format match in audio_without_ebook needs instead.
                    ka, kb = loose_title(a["title"]), loose_title(b["title"])

                    if (va is not None and vb is not None and va != vb) or ka != kb:
                        findings.append(
                            Finding(
                                "multi_asset_books",
                                "collapsed",
                                f"book {book_id} \"{book['title']}\" has two different "
                                f"{kind}s merged into one entity",
                                [
                                    f"{kind} {a['id']} {a['title']!r} (vol {va})",
                                    f"{kind} {b['id']} {b['title']!r} (vol {vb})",
                                ],
                                book_id,
                            )
                        )
                    else:
                        # Same title, two files. Usually a duplicate upload — but an
                        # epub's embedded title can simply be wrong (epub 380 is
                        # "Frozen Sky 2: Betrayed" carrying "The Frozen Sky" in its
                        # OPF), and only the s3_key gives that away. Hence the keys
                        # as evidence, and the hedge in the summary.
                        findings.append(
                            Finding(
                                "multi_asset_books",
                                "duplicate-file",
                                f"book {book_id} \"{book['title']}\" has two {kind}s with "
                                f"the same title — a duplicate upload, unless one file's "
                                f"metadata is wrong (compare the keys)",
                                [
                                    f"{kind} {a['id']} {a['s3_key']!r}",
                                    f"{kind} {b['id']} {b['s3_key']!r}",
                                ],
                                book_id,
                            )
                        )
    return findings


def omnibus(books: dict[int, dict], m4bs: list[dict]) -> list[Finding]:
    """One audio file spanning several epub volumes. The schema has no way to say
    'this m4b covers books 1-3', so these are surfaced, not fixed."""
    findings = []
    for m in m4bs:
        book = books.get(m["book_id"]) if m["book_id"] else None
        haystack = f"{m['title'] or ''} {m['album'] or ''} {book['title'] if book else ''}"
        if OMNIBUS.search(haystack):
            hours = round((m["duration_s"] or 0) / 3600, 1)
            findings.append(
                Finding(
                    "omnibus",
                    "info",
                    f"m4b {m['id']} {m['title']!r} ({hours}h) spans several volumes — "
                    f"the schema cannot model this",
                    [f"on book {m['book_id']} \"{book['title']}\"" if book else "unlinked",
                     "not a split: do not force-link it to a single book"],
                    m["book_id"],
                )
            )
    return findings


def orphans(conn: psycopg.Connection, epubs: list[dict], m4bs: list[dict]) -> list[Finding]:
    """Assets with no book link, plus the review backlog. Omissions, not errors —
    but the biggest reason a book is missing its other format."""
    findings = []
    unlinked_e = [e for e in epubs if not e["book_id"]]
    unlinked_m = [m for m in m4bs if not m["book_id"]]

    for kind, rows in (("epub", unlinked_e), ("m4b", unlinked_m)):
        if rows:
            findings.append(
                Finding(
                    "orphans",
                    "info",
                    f"{len(rows)} {kind}s are not linked to any book",
                    [f"{kind} {r['id']} {r['title']!r}" for r in rows[:10]]
                    + ([f"... and {len(rows) - 10} more"] if len(rows) > 10 else []),
                )
            )

    queue = conn.execute(
        "SELECT status, count(*) AS n FROM resolutions "
        "WHERE status IN ('pending', 'rejected') GROUP BY status"
    ).fetchall()
    for row in queue:
        findings.append(
            Finding(
                "orphans",
                "info",
                f"{row['n']} resolutions are {row['status']}",
                ["work them with: util/resolver.sh review"],
            )
        )
    return findings


def invariants(conn: psycopg.Connection) -> list[Finding]:
    """No asset may belong to more than one book. Clean today; keep it that way."""
    findings = []
    for kind, table, col in (
        ("epub", "book_epubs", "epub_id"),
        ("m4b", "book_m4bs", "m4b_id"),
    ):
        rows = conn.execute(
            f"SELECT {col} AS id, count(*) AS n FROM {table} "
            f"GROUP BY {col} HAVING count(*) > 1"
        ).fetchall()
        for r in rows:
            findings.append(
                Finding(
                    "invariants",
                    "collapsed",
                    f"{kind} {r['id']} is linked to {r['n']} different books",
                    ["an asset belongs to exactly one abstract book"],
                )
            )
    return findings


# --- LLM tier (ambiguous shortlist only) -------------------------------------

# claude-sonnet-5 list price, $/million tokens. Only used for the up-front
# estimate; a wrong number here costs nothing but a misleading printout.
PRICES = {"claude-sonnet-5": (3.00, 15.00), "claude-opus-4-8": (5.00, 25.00)}
EST_INPUT_TOKENS = 900
EST_OUTPUT_TOKENS = 200


def estimate_cost(n_calls: int, model: str) -> str:
    price_in, price_out = PRICES.get(model, (3.00, 15.00))
    dollars = (
        n_calls * EST_INPUT_TOKENS * price_in
        + n_calls * EST_OUTPUT_TOKENS * price_out
    ) / 1_000_000
    return f"{n_calls} call(s) to {model}, roughly ${dollars:.2f}"


def adjudicate_shortlist(shortlist: list[dict]) -> list[Finding]:
    """One structured call per ambiguous case: which candidate epub, if any, is
    the same book as this m4b?"""
    import anthropic
    from pydantic import BaseModel

    from .llm import model_id

    class SameBook(BaseModel):
        epub_id: int | None  # the epub that is the SAME BOOK, or null if none is
        confidence: float
        reasoning: str

    prompt = (
        "You match an audiobook to its ebook in a personal library. Given one "
        "audiobook's metadata and several candidate ebooks, return the epub_id of "
        "the ebook that is THE SAME BOOK. Series volumes are different books: "
        "'The Path of Ascension 2' and 'The Path of Ascension: Book 3' are NOT the "
        "same book. An omnibus or box set spanning several volumes is NOT the same "
        "book as any single volume. If no candidate is the same book, return null. "
        "Never guess."
    )

    client = anthropic.Anthropic()
    findings = []
    for case in shortlist:
        m4b, book = case["m4b"], case["book"]
        payload = {
            "audiobook": {
                "title": m4b["title"],
                "album": m4b["album"],
                "artist": m4b["artist"],
                "on_book": book["title"],
            },
            "candidate_ebooks": [
                {"epub_id": e["id"], "title": e["title"], "authors": list(e["authors"])}
                for e in case["candidates"]
            ],
        }
        response = client.messages.parse(
            model=model_id(),
            max_tokens=512,
            system=[
                {
                    "type": "text",
                    "text": prompt,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ],
            messages=[{"role": "user", "content": str(payload)}],
            output_format=SameBook,
        )
        adj = response.parsed_output
        if adj is None:
            continue

        match = next(
            (e for e in case["candidates"] if e["id"] == adj.epub_id), None
        )
        if match is None:
            findings.append(
                Finding(
                    "audio_without_ebook",
                    "ambiguous",
                    f"book {book['id']} \"{book['title']}\" — no candidate epub is the "
                    f"same book (LLM)",
                    [adj.reasoning],
                    book["id"],
                )
            )
        else:
            where = (
                f"currently on book {match['book_id']}"
                if match["book_id"]
                else "currently UNLINKED"
            )
            findings.append(
                Finding(
                    "audio_without_ebook",
                    "confident" if adj.confidence >= 0.8 else "ambiguous",
                    f"book {book['id']} \"{book['title']}\" holds the audiobook; its "
                    f"epub is elsewhere (LLM, conf={adj.confidence:.2f})",
                    [
                        f"m4b {m4b['id']} {m4b['title']!r}",
                        f"epub {match['id']} {match['title']!r} — {where}",
                        adj.reasoning,
                    ],
                    book["id"],
                )
            )
    return findings


# --- driver ------------------------------------------------------------------

CHECKS = ("audio", "multi", "omnibus", "orphans")


def run(
    conn: psycopg.Connection,
    checks: tuple[str, ...] = CHECKS,
    use_llm: bool = True,
) -> list[Finding]:
    books = _load_books(conn)
    epubs = _load_epubs(conn)
    m4bs = _load_m4bs(conn)

    findings: list[Finding] = []

    if "audio" in checks:
        found, shortlist = audio_without_ebook(books, epubs, m4bs)
        findings += found
        if shortlist:
            if use_llm and os.environ.get("ANTHROPIC_API_KEY"):
                from .llm import model_id

                # stderr, so it never lands in the middle of --json output.
                print(
                    f"  {len(shortlist)} ambiguous cases -> "
                    f"{estimate_cost(len(shortlist), model_id())}",
                    file=sys.stderr,
                )
                findings += adjudicate_shortlist(shortlist)
            else:
                for case in shortlist:
                    findings.append(
                        Finding(
                            "audio_without_ebook",
                            "ambiguous",
                            f"book {case['book']['id']} \"{case['book']['title']}\" — "
                            f"{len(case['candidates'])} candidate epubs, needs judgment",
                            [f"m4b {case['m4b']['id']} {case['m4b']['title']!r}"]
                            + [
                                f"epub {e['id']} {e['title']!r} "
                                f"(book {e['book_id'] or 'UNLINKED'})"
                                for e in case["candidates"]
                            ],
                            case["book"]["id"],
                        )
                    )

    if "multi" in checks:
        findings += multi_asset_books(books, epubs, m4bs)
    if "omnibus" in checks:
        findings += omnibus(books, m4bs)
    if "orphans" in checks:
        findings += orphans(conn, epubs, m4bs)

    findings += invariants(conn)
    return findings


ORDER = {"confident": 0, "collapsed": 1, "ambiguous": 2, "duplicate-file": 3, "info": 4}
HEADINGS = {
    "confident": "SPLIT BOOKS — one book, two entities (fix these first)",
    "collapsed": "COLLAPSED — different books merged into one entity",
    "ambiguous": "NEEDS JUDGMENT",
    "duplicate-file": "DUPLICATE FILES — same book uploaded twice",
    "info": "FOR INFORMATION",
}


def render(findings: list[Finding]) -> str:
    out: list[str] = []
    for verdict in sorted({f.verdict for f in findings}, key=lambda v: ORDER[v]):
        group = [f for f in findings if f.verdict == verdict]
        out.append(f"\n=== {HEADINGS[verdict]} ({len(group)}) ===\n")
        for f in group:
            out.append(f"  {f.summary}")
            for e in f.evidence:
                out.append(f"      {e}")
            if f.depends_on:
                out.append(f"      DEPENDS ON: {f.depends_on}")
            out.append("")
    if not findings:
        out.append("\nno findings\n")
    return "\n".join(out)
