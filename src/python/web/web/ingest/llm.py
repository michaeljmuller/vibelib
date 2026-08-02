"""Tier-2 adjudication: one structured-output Claude call per ambiguous asset.

The model receives the asset's raw metadata plus short candidate lists (never
the whole catalog) and returns a typed decision. A cover image is attached only
on the explicit fallback path.
"""

import base64
import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any

import anthropic
from pydantic import BaseModel

from .normalize import norm_language, parse_pub_date, sort_name, sort_title

log = logging.getLogger("uvicorn.error")

DEFAULT_MODEL = "claude-sonnet-5"


def model_id() -> str:
    return os.environ.get("RESOLVER_MODEL", DEFAULT_MODEL)


def _parsed(response, what: str) -> tuple[Any, dict[str, int]]:
    """The decision and its token usage. The CLI printed a usage total at the end
    of a run; a web request has no such moment, so each call says what it cost in
    the log instead -- the only place anyone can go looking afterwards."""
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_read_input_tokens": response.usage.cache_read_input_tokens or 0,
        "cache_creation_input_tokens": response.usage.cache_creation_input_tokens or 0,
    }
    log.info(
        "llm %s (%s): in=%d out=%d cache_read=%d cache_write=%d",
        what, model_id(), usage["input_tokens"], usage["output_tokens"],
        usage["cache_read_input_tokens"], usage["cache_creation_input_tokens"],
    )
    if response.parsed_output is None:
        raise RuntimeError(
            f"model returned no parseable output (stop_reason={response.stop_reason})"
        )
    return response.parsed_output, usage


# --- output schema -----------------------------------------------------------


class NewPerson(BaseModel):
    name: str
    disambiguator: str | None


class PersonRef(BaseModel):
    """Resolution of one raw credited name: link an existing person OR create."""

    raw_name: str
    link_person_id: int | None
    create: NewPerson | None


class SeriesRef(BaseModel):
    link_series_id: int | None
    create_name: str | None


class NewBook(BaseModel):
    title: str
    series: SeriesRef | None
    series_position: int | None  # whole numbers only; null for interstitials
    publication_date: str | None  # ISO date of FIRST publication; YYYY-01-01 if year-only
    language: str | None  # BCP-47, e.g. 'en', 'pt'


class BookUpdate(BaseModel):
    """Corrections to the EXISTING book being linked (only when its current
    record is wrong). None = leave that field unchanged."""

    title: str | None
    series: SeriesRef | None
    series_position: int | None


class BookDecision(BaseModel):
    link_book_id: int | None
    update: BookUpdate | None  # only meaningful together with link_book_id
    create: NewBook | None


class PseudonymProposal(BaseModel):
    pseudonym_name: str
    real_person_names: list[str]
    note: str | None


class Adjudication(BaseModel):
    book: BookDecision
    authors: list[PersonRef]
    narrators: list[PersonRef]
    pseudonym_proposals: list[PseudonymProposal]
    acquired_on: str | None  # only when a reviewer correction states one
    metadata_insufficient: bool
    confidence: float
    notes: str


# --- prompt ------------------------------------------------------------------

SYSTEM_PROMPT = """You are the entity resolver for a personal ebook/audiobook \
library. Given one asset's raw metadata and short lists of candidate entities \
already in the catalog, decide how the asset maps onto the abstract catalog of \
books, people (authors/narrators), and series.

Rules:
- A book is format-independent: an epub and an m4b of the same work are ONE \
book, even if their titles differ cosmetically ("We Are Legion" vs "We Are \
Legion (We Are Bob)"). Prefer linking a candidate over creating a new book; \
create only when no candidate is plausibly the same work.
- When linking to an existing book whose record is WRONG (bad title, wrong or \
missing series/position) — especially when a reviewer correction says so — \
put the corrections in book.update alongside link_book_id. Updates modify the \
shared catalog record, so they are never auto-committed; a human approves \
them.
- Two books can share a title but be different works (different authors) — \
never link on title alone.
- Author name variants (initials, punctuation, middle names: "J.R.R. Tolkien" \
/ "JRR Tolkien", "Iain Banks" / "Iain M. Banks") are usually the same person — \
link to the candidate. Two DIFFERENT people may also share a name; if you \
believe a credited name is a different person than the same-named candidate, \
create a new person with a short `disambiguator` (e.g. "the historian, not \
the novelist").
- Credit books to the name on the cover, even when it is a pseudonym: the \
pseudonym is itself a person row. If you know the credited name is a pen name \
of real people (e.g. Richard Bachman -> Stephen King; James S. A. Corey -> \
Daniel Abraham + Ty Franck), additionally emit a pseudonym_proposal naming the \
real people. Only propose pseudonym links you actually know; never guess.
- Publisher metadata often credits a pen name AND the person behind it as two \
separate creators ("TheFirstDefier" and "Brink, JF"; "Shirtaloon" and "Travis \
Deverell"). That is ONE author, listed twice. Credit only the cover name and \
put the other in a pseudonym_proposal — never emit both as authors, which \
would credit one person twice under two names. Two genuinely different people \
who wrote a book together (Niven and Pournelle, Preston and Child) are of \
course both authors; the test is whether one name is the other's pen name, not \
whether two names appear.
- Series: infer membership from the series/album/title fields (audiobook \
"album" often looks like "The Expanse, Book 3"). Positions are whole numbers \
only. Interstitial works (novellas like "Edgedancer", positions like 1.5) get \
series membership with a null position — mention it in notes. You may also use \
world knowledge: if you recognize the work and know its series and position \
(e.g. "A Feast for Crows" -> A Song of Ice and Fire #4), include them even \
when the metadata omits them. Never invent a series for a work you don't \
actually recognize.
- For new books/people/series, output clean human-facing names and titles \
(fix ALL-CAPS, strip filename artifacts), keeping original language and \
spelling.
- Raw titles often carry search-engine cruft: embedded series names, "Book N" \
markers, and genre taglines. The abstract title is the work's core title \
ONLY — series and position are captured in their own fields. Example: raw \
"The Warships Universe: Combat, a military sci-fi adventure Book 12" -> \
title "Combat", series "The Warships Universe", position 12. Strip taglines \
like "A Novel" / "A LitRPG Adventure". Exception: when a work has no distinct \
title of its own (e.g. "Azarinth Healer: Book Three"), keep the \
"Series: Book N" form as the title.
- publication_date: the work's FIRST publication date in ISO format \
(YYYY-MM-DD). The asset's raw date fields are often edition or reprint dates — \
prefer your knowledge of the work's original publication. If you only know \
the year, use YYYY-01-01; if you don't know, use null. Never guess a made-up \
date.
- language: the work's language as a BCP-47 code ('en', 'pt', 'pt-PT'). Use \
the asset's language field when present; otherwise the language the metadata \
is evidently in; null if unclear.
- acquired_on: when this FILE was obtained — a fact about the copy, and a \
different fact from publication_date, which is the work's first publication. \
Nothing in the metadata can tell you it, so leave it null. The one exception \
is a reviewer correction that states one ("acquired July 27", "I bought this \
last year"): then give it as ISO YYYY-MM-DD, resolving a partial date against \
the `today` you were given. A correction about acquisition must never change \
publication_date, and vice versa.
- Narrators are people too, and may be the same person as an author \
(self-narrated) — if so, link the same person for both roles.
- confidence: the probability (0.0-1.0) that a human reviewer would accept \
this mapping as-is. A person reviews every proposal regardless, so this is not \
a gate and there is nothing to be won by clearing a threshold — it tells the \
reviewer where to spend their attention, and an honest low score is far more \
useful to them than a confident wrong one. Creating new book/person/series \
rows from clean, unambiguous metadata when there are no plausible candidates \
IS a high-confidence action (>= 0.9) — a sparse catalog is normal, not a \
reason to hedge. Reserve lower confidence for genuine ambiguity: a candidate \
that might or might not be the same work or person, conflicting metadata, or \
uncertain series placement that would change the mapping.
- If the text metadata is too poor to identify the work (missing or \
filename-like title, no author), set metadata_insufficient to true and keep \
confidence low.
- notes: one or two short sentences of rationale a human reviewer will read.
"""


def revise(
    client: anthropic.Anthropic,
    meta: dict[str, Any],
    candidates: dict[str, Any],
    prior_proposal: dict[str, Any],
    instruction: str,
    acquired_on: datetime.date,
) -> tuple["Adjudication", dict[str, int]]:
    """Revise a prior proposal per a human reviewer's plain-language correction.
    Same schema and system prompt as adjudicate(); the correction is
    authoritative over the model's own judgment.

    `today` is here because a correction is written the way a person speaks --
    "acquired July 27" carries no year -- and the model has no clock."""
    payload = {
        "asset": {k: v for k, v in meta.items() if v not in (None, [], "")},
        "candidates": candidates,
        "your_previous_proposal": prior_proposal,
        "current_acquired_on": acquired_on.isoformat(),
        "today": datetime.date.today().isoformat(),
        "reviewer_correction": instruction,
    }
    response = client.messages.parse(
        model=model_id(),
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT
                + "\nA human reviewer has corrected your previous proposal for "
                "this asset. Produce the full revised mapping. The reviewer's "
                "correction is authoritative — apply it exactly, keep everything "
                "they didn't mention, and set confidence to reflect the revised "
                "mapping.",
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, default=str),
            }
        ],
        output_format=Adjudication,
    )
    return _parsed(response, "revise")


# --- backfill: fill missing facts on already-created books -------------------


class BookFacts(BaseModel):
    publication_date: str | None
    language: str | None


BOOK_FACTS_PROMPT = """You fill in missing catalog facts for books in a \
personal library. Given a book's title, authors, series, and hints from its \
raw edition metadata, return:
- publication_date: the work's FIRST publication date, ISO YYYY-MM-DD. Prefer \
your knowledge of the original publication over edition/reprint dates in the \
hints. Year-only knowledge -> YYYY-01-01. If you do not know the work, null. \
Never guess a made-up date.
- language: the work's language as a BCP-47 code ('en', 'pt', 'pt-PT'), from \
the hints or the evident language of the title; null if unclear.
"""


def book_facts(
    client: anthropic.Anthropic, book_info: dict[str, Any]
) -> tuple[BookFacts, dict[str, int]]:
    response = client.messages.parse(
        model=model_id(),
        max_tokens=512,
        system=[
            {
                "type": "text",
                "text": BOOK_FACTS_PROMPT,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": json.dumps(book_info, ensure_ascii=False, default=str),
            }
        ],
        output_format=BookFacts,
    )
    return _parsed(response, "book_facts")


# --- call --------------------------------------------------------------------


def _cover_block(cover_path: Path) -> dict[str, Any]:
    media_type = "image/png" if cover_path.suffix.lower() == ".png" else "image/jpeg"
    data = base64.standard_b64encode(cover_path.read_bytes()).decode()
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def adjudicate(
    client: anthropic.Anthropic,
    meta: dict[str, Any],
    candidates: dict[str, Any],
    cover_path: Path | None = None,
) -> tuple[Adjudication, dict[str, int]]:
    """One structured call; returns the decision and token usage."""
    payload = {
        "asset": {k: v for k, v in meta.items() if v not in (None, [], "")},
        "candidates": candidates,
    }
    content: list[dict[str, Any]] = []
    if cover_path is not None:
        content.append(_cover_block(cover_path))
        content.append(
            {
                "type": "text",
                "text": "The text metadata was insufficient; the asset's cover "
                "image is attached — use it to identify the work.\n\n"
                + json.dumps(payload, ensure_ascii=False, default=str),
            }
        )
    else:
        content.append(
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}
        )

    response = client.messages.parse(
        model=model_id(),
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        ],
        messages=[{"role": "user", "content": content}],
        output_format=Adjudication,
    )
    return _parsed(response, "adjudicate+cover" if cover_path else "adjudicate")


# --- Adjudication -> proposal dict -------------------------------------------


def _person_action(ref: PersonRef) -> dict[str, Any]:
    if ref.link_person_id is not None:
        return {"link": ref.link_person_id, "raw_name": ref.raw_name}
    name = ref.create.name if ref.create else ref.raw_name
    return {
        "create": {
            "name": name,
            "sort_name": sort_name(name),
            "disambiguator": ref.create.disambiguator if ref.create else None,
        },
        "raw_name": ref.raw_name,
    }


def _series_action(ref: SeriesRef | None) -> dict[str, Any] | None:
    if ref is None:
        return None
    if ref.link_series_id is not None:
        return {"link": ref.link_series_id}
    if ref.create_name:
        return {
            "create": {"name": ref.create_name, "sort_name": sort_title(ref.create_name)}
        }
    return None


def to_proposal(adj: Adjudication) -> dict[str, Any]:
    """Map the model's decision to the action dict apply.py executes."""
    if adj.book.link_book_id is not None:
        book: dict[str, Any] = {"link": adj.book.link_book_id}
        upd = adj.book.update
        if upd is not None:
            update: dict[str, Any] = {}
            if upd.title:
                update["title"] = upd.title
                update["sort_title"] = sort_title(upd.title)
            series_action = _series_action(upd.series)
            if series_action is not None:
                update["series"] = series_action
            if upd.series_position is not None:
                update["series_position"] = upd.series_position
            if update:
                book["update"] = update
    else:
        nb = adj.book.create
        if nb is None:
            raise ValueError("adjudication has neither link_book_id nor create")
        series = _series_action(nb.series)
        book = {
            "create": {
                "title": nb.title,
                "sort_title": sort_title(nb.title),
                "series": series,
                "series_position": nb.series_position,
                "publication_date": parse_pub_date(nb.publication_date),
                "language": norm_language(nb.language),
            }
        }
    proposal: dict[str, Any] = {
        "book": book,
        "authors": [_person_action(a) for a in adj.authors],
        "narrators": [_person_action(n) for n in adj.narrators],
        "pseudonyms": [p.model_dump() for p in adj.pseudonym_proposals],
    }
    # Absent unless a correction supplied one, so that a proposal looks the same
    # here as it does coming out of the free tier-1 path, which has no model to
    # ask. (parse_pub_date is just an ISO-date validator; nothing about it is
    # specific to publication.)
    acquired = parse_pub_date(adj.acquired_on)
    if acquired is not None:
        proposal["acquired_on"] = acquired
    return proposal
