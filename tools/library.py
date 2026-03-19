"""
Core ingestion logic for vibelib.

Entry points:
  ingest_epub(conn, epub, state)
  ingest_m4b(conn, m4b, state)

Each entry point calls the appropriate add_* helpers. Helpers for books,
series, and narrators are stubs pending their own implementation.

State is an IngestionState instance that holds the author cache, seen-token
cache, and accumulated mapping — shared across all records in a run.
"""

import json
import re

import anthropic
import psycopg2
from rapidfuzz import fuzz

FUZZY_THRESHOLD = 92

# epub compound separators: semicolon, ampersand, " and " — NOT comma ("Last, First")
_EPUB_SPLIT_RE = re.compile(r'\s*(?:;|&|\band\b)\s*', re.IGNORECASE)

# m4b compound separator: comma (fields are in "First Last" form)
_M4B_SPLIT_RE = re.compile(r'\s*,\s*')


# ---------------------------------------------------------------------------
# Shared run state
# ---------------------------------------------------------------------------

class IngestionState:
    """Holds caches and accumulated output for one ingest run."""

    def __init__(self, conn, dry_run=False):
        self.dry_run   = dry_run
        self.mapping   = []
        self._seen     = {}          # raw token -> cached entry dict
        self._authors  = _load_authors(conn)  # [(id, name)]

    def write_mapping(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.mapping, f, indent=2, ensure_ascii=False)
        new_count = sum(1 for e in self.mapping if e["tier"] == "new")
        llm_count = sum(1 for e in self.mapping if e["tier"] == "llm")
        print(f"\nWrote {len(self.mapping)} mappings to {path}")
        print(f"  {new_count} new authors, {llm_count} LLM matches")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def process_epub(conn, epub, state):
    add_authors(conn, "epub", epub["epub_id"], epub["authors"], _EPUB_SPLIT_RE, state)
    # add_book(conn, epub, state)    # TODO
    # add_series(conn, epub, state)  # TODO


def process_m4b(conn, m4b, state):
    if m4b["artist"]:
        add_authors(conn, "m4b", m4b["m4b_id"], [m4b["artist"]], _M4B_SPLIT_RE, state)
    # add_book(conn, m4b, state)      # TODO
    # add_series(conn, m4b, state)    # TODO
    # add_narrators(conn, m4b, state) # TODO


# ---------------------------------------------------------------------------
# add_* helpers
# ---------------------------------------------------------------------------

def add_authors(conn, source, source_id, raw_strings, split_re, state):
    """Resolve each author token against the authors table and record the mapping."""
    for raw in raw_strings:
        for token in split_re.split(raw):
            token = token.strip()
            if not token:
                continue

            if token in state._seen:
                entry = state._seen[token]
            else:
                result = _match_tiers_1_to_3(token, state._authors)
                if result is None:
                    result = _match_llm(token, state._authors)

                if result:
                    author_id, canonical, tier = result
                else:
                    canonical = _canonicalize(token)
                    tier = "new"
                    if state.dry_run:
                        author_id = None
                    else:
                        author_id = _db_insert_author(canonical, conn)
                    state._authors.append((author_id, canonical))
                    print(f"  [new] {canonical!r}  (from {token!r})")

                entry = {"canonical": canonical, "author_id": author_id, "tier": tier}
                state._seen[token] = entry

            state.mapping.append({
                "source":    source,
                "source_id": source_id,
                "raw":       token,
                **entry,
            })


# ---------------------------------------------------------------------------
# Name utilities
# ---------------------------------------------------------------------------

def _normalize(name):
    name = name.lower()
    name = re.sub(r"[.,\-]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _squish(norm_key):
    return norm_key.replace(" ", "")


def _clean(name):
    name = re.sub(r'\([^)]*\)', '', name)
    name = re.sub(r'\b\d{4}(?:-\d{4})?\b', '', name)
    return _normalize(name)


def _uninvert(name):
    if name.count(",") == 1:
        last, first = name.split(",", 1)
        return f"{first.strip()} {last.strip()}"
    return name


def _canonicalize(raw):
    name = re.sub(r'\([^)]*\)', '', raw)
    name = re.sub(r'\b\d{4}(?:-\d{4})?\b', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    if name and name == name.upper() and name != name.lower():
        name = name.title()
    name = _uninvert(name)
    return name or raw.strip()


def _make_sort_name(name):
    if "," in name:
        return name
    parts = name.rsplit(" ", 1)
    if len(parts) == 2:
        first, last = parts
        return f"{last}, {first}"
    return name


# ---------------------------------------------------------------------------
# Matching tiers
# ---------------------------------------------------------------------------

def _match_tiers_1_to_3(token, cache):
    if not cache:
        return None
    tok_norm  = _normalize(token)
    tok_sq    = _squish(tok_norm)
    tok_clean = _clean(token)
    best_score, best_entry = 0, None
    for aid, name in cache:
        if _normalize(name) == tok_norm:
            return (aid, name, "normalized")
        if _squish(_normalize(name)) == tok_sq:
            return (aid, name, "squish")
        score = fuzz.token_sort_ratio(tok_clean, _clean(name))
        if score > best_score:
            best_score, best_entry = score, (aid, name)
    if best_score >= FUZZY_THRESHOLD:
        return (best_entry[0], best_entry[1], f"fuzzy:{best_score}")
    return None


def _match_llm(token, cache):
    if not cache:
        return None
    tok_clean = _clean(token)
    ranked = sorted(cache, key=lambda r: fuzz.token_sort_ratio(tok_clean, _clean(r[1])), reverse=True)[:20]
    candidates = [name for _, name in ranked]
    client = anthropic.Anthropic()
    prompt = f"""I have a new author name from e-book metadata and a list of author names already in my library. Does the new name refer to the same individual person as any name in the list? Only match if you are certain it is the same person — different formatting, punctuation, or initials spacing of the same name. Do not match co-authors or different people.

New name: {json.dumps(token)}

Library candidates:
{json.dumps(candidates, indent=2)}

Return a JSON object with:
  "match": the matching library name, or null if none
  "reason": a one-line explanation"""
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            result = json.loads(m.group(0))
            matched_name = result.get("match")
            if matched_name:
                matched_id = next((aid for aid, name in cache if name == matched_name), None)
                if matched_id is not None:
                    return (matched_id, matched_name, "llm")
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _load_authors(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id, name FROM authors ORDER BY id")
        return cur.fetchall()


def _db_insert_author(canonical, conn):
    sort_name = _make_sort_name(canonical)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO authors (name, sort_name) VALUES (%s, %s) RETURNING id",
            (canonical, sort_name),
        )
        author_id = cur.fetchone()[0]
    conn.commit()
    return author_id
