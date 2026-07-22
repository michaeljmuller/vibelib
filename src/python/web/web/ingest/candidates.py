"""Tier-1 exact matching and tier-2 candidate retrieval.

Tier 1 links an asset with zero LLM cost when an existing book has the same
normalized title AND the same normalized author set. Everything softer goes to
tier 2, where this module supplies compact candidate lists (via pg_trgm) for
the LLM to adjudicate against.
"""

from typing import Any

import psycopg

from .normalize import norm_person, norm_title, loose_title, volume_number

BOOK_SIM_THRESHOLD = 0.3
NAME_SIM_THRESHOLD = 0.3


def author_sets_match(raw_authors: list[str], book_author_names: list[str]) -> bool:
    """True iff the two credit lists are the same people under norm_person."""
    if not raw_authors or not book_author_names:
        return False
    return {norm_person(a) for a in raw_authors} == {
        norm_person(n) for n in book_author_names
    }


def _book_candidates(conn: psycopg.Connection, title: str, limit: int) -> list[dict]:
    # Trigram similarity, OR the stored title appearing inside the asset's
    # title — catalog titles are cruft-free ("Sobral City") while raw titles
    # often aren't ("A Soldier's Life: Book 2: Sobral City"), which kills
    # trigram scores.
    return conn.execute(
        """SELECT b.id, b.title, b.series_position, s.name AS series,
                  similarity(lower(b.title), lower(%(t)s)) AS sim,
                  ARRAY(SELECT p.name FROM book_authors ba
                        JOIN people p ON p.id = ba.author_id
                        WHERE ba.book_id = b.id ORDER BY ba.position) AS authors
           FROM books b
           LEFT JOIN series s ON s.id = b.series_id
           WHERE similarity(lower(b.title), lower(%(t)s)) > %(th)s
              OR position(lower(b.title) IN lower(%(t)s)) > 0
           ORDER BY sim DESC
           LIMIT %(lim)s""",
        {"t": title, "th": BOOK_SIM_THRESHOLD, "lim": limit},
    ).fetchall()


def exact_match(conn: psycopg.Connection, meta: dict[str, Any]) -> dict | None:
    """Return a proposal linking to an existing book, or None.

    Match rule: exactly one candidate book whose normalized title equals the
    asset's (strict key first, then loose key) and whose author set matches.
    """
    title = meta.get("title")
    raw_authors = meta.get("authors") or []
    if not title or not raw_authors:
        return None

    # Volume 4 of a series is not volume 1 of it, however alike the titles read.
    # loose_title() strips colon subtitles, which is exactly where the volume
    # number lives, so on its own it maps every volume of a series to one key:
    # "A Soldier's Life: Book 4: The Hounds" and "A Soldier's Life: Book 1" both
    # become "soldiers life", the author sets match, and tier 1 links them at
    # confidence 1.0 with no LLM and no review. The volume number is read off the
    # RAW title precisely because the normalizers destroy it.
    asset_volume = volume_number(title)

    candidates = _book_candidates(conn, title, limit=20)
    for key_fn in (norm_title, loose_title):
        asset_key = key_fn(title)
        if not asset_key:
            continue
        hits = [
            c
            for c in candidates
            if key_fn(c["title"]) == asset_key
            and author_sets_match(raw_authors, c["authors"])
            and volume_number(c["title"]) == asset_volume
        ]
        if len(hits) == 1:
            proposal: dict[str, Any] = {
                "book": {"link": hits[0]["id"]},
                "authors": [],  # already attached to the linked book
            }
            if meta["asset_type"] == "m4b":
                proposal["narrators"] = [
                    _person_ref_for(conn, n) for n in meta.get("narrators") or []
                ]
            return proposal
        if len(hits) > 1:
            return None  # ambiguous (same-title-same-author duplicates) -> LLM
    return None


def _person_ref_for(conn: psycopg.Connection, name: str) -> dict:
    """Exact-tier person resolution: link by norm_person if a unique existing
    person matches, else create. Used for narrators only — low-risk because a
    narrator row carries no cross-book identity claims."""
    person = find_person_exact(conn, name)
    if person is not None:
        return {"link": person["id"], "raw_name": name}
    return {"create": {"name": name}, "raw_name": name}


def find_person_exact(conn: psycopg.Connection, name: str) -> dict | None:
    rows = conn.execute(
        """SELECT id, name FROM people
           WHERE similarity(lower(name), lower(%(n)s)) > %(th)s
           ORDER BY similarity(lower(name), lower(%(n)s)) DESC
           LIMIT 10""",
        {"n": name, "th": NAME_SIM_THRESHOLD},
    ).fetchall()
    key = norm_person(name)
    hits = [r for r in rows if norm_person(r["name"]) == key]
    return hits[0] if len(hits) == 1 else None


def find_series_exact(conn: psycopg.Connection, name: str) -> dict | None:
    rows = conn.execute(
        """SELECT id, name FROM series
           WHERE similarity(lower(name), lower(%(n)s)) > %(th)s
           ORDER BY similarity(lower(name), lower(%(n)s)) DESC
           LIMIT 10""",
        {"n": name, "th": NAME_SIM_THRESHOLD},
    ).fetchall()
    key = norm_title(name)
    hits = [r for r in rows if norm_title(r["name"]) == key]
    return hits[0] if len(hits) == 1 else None


def get_candidates(conn: psycopg.Connection, meta: dict[str, Any]) -> dict[str, Any]:
    """Compact candidate lists for the LLM prompt."""
    out: dict[str, Any] = {"books": [], "people": {}, "series": []}

    if meta.get("title"):
        out["books"] = [
            {
                "id": c["id"],
                "title": c["title"],
                "authors": c["authors"],
                "series": c["series"],
                "series_position": c["series_position"],
            }
            for c in _book_candidates(conn, meta["title"], limit=5)
        ]

    for name in (meta.get("authors") or []) + (meta.get("narrators") or []):
        if name in out["people"]:
            continue
        rows = conn.execute(
            """SELECT id, name, disambiguator FROM people
               WHERE similarity(lower(name), lower(%(n)s)) > %(th)s
               ORDER BY similarity(lower(name), lower(%(n)s)) DESC
               LIMIT 3""",
            {"n": name, "th": NAME_SIM_THRESHOLD},
        ).fetchall()
        out["people"][name] = [dict(r) for r in rows]

    # Raw series signal: epubs have a series column; m4bs encode it in album.
    series_signal = meta.get("series") or meta.get("album")
    if series_signal:
        rows = conn.execute(
            """SELECT id, name FROM series
               WHERE similarity(lower(name), lower(%(n)s)) > %(th)s
               ORDER BY similarity(lower(name), lower(%(n)s)) DESC
               LIMIT 3""",
            {"n": series_signal, "th": NAME_SIM_THRESHOLD},
        ).fetchall()
        out["series"] = [dict(r) for r in rows]

    return out
