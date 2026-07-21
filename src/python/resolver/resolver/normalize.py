"""Deterministic name/title normalization.

These functions decide tier-1 (LLM-free) matching, so they must be conservative:
two strings normalizing to the same key means "definitely the same"; anything
softer than that is left to the LLM tier.
"""

import re
import unicodedata

_ARTICLES = ("the", "a", "an")
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v", "phd", "md"}

# --- volume numbers ----------------------------------------------------------

# The discriminator the other normalizers destroy: loose_title() strips colon
# subtitles and parentheticals, which is exactly where the volume number lives
# ("Azarinth Healer: Book Four" -> "azarinth healer"). So it must be read off
# the RAW title, before any normalization. Both the exact matcher and the audit
# depend on this to tell one volume of a series from another.

_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}

_KEYWORD = re.compile(
    r"\b(?:book|volume|vol|arc|part|episode)s?\.?\s*#?\s*(\d{1,3}|[a-z]+)\b",
    re.IGNORECASE,
)
_HASH = re.compile(r"#\s*(\d{1,3})\b")
# A bare trailing number, but only after real words ("Defiance of the Fall 10"),
# never a title that *is* a number ("1632", "11/22/63", "2 Lies, 2 Thrones").
_TRAILING = re.compile(r"^(?=.*[A-Za-z])\D+?\s(\d{1,3})\s*$")


def volume_number(title: str | None) -> int | None:
    """Volume/sequence number carried in a title: 'Book 3', 'Volume 2', '#4',
    'ARC 3', 'Book Four', or a bare trailing number ('Defiance of the Fall 10').
    None when the title carries no number."""
    if not title:
        return None

    for m in _KEYWORD.finditer(title):
        raw = m.group(1)
        if raw.isdigit():
            return int(raw)
        if raw.casefold() in _WORD_NUMBERS:
            return _WORD_NUMBERS[raw.casefold()]

    m = _HASH.search(title)
    if m:
        return int(m.group(1))

    # Only the first colon-segment: "Jake's Magical Market 3: Home Sweet Home".
    m = _TRAILING.match(title.split(":")[0].strip())
    return int(m.group(1)) if m else None


def _strip_diacritics(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def _base(s: str) -> str:
    s = _strip_diacritics(s).casefold()
    s = s.replace("'", "").replace("’", "")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _uninvert(name: str) -> str:
    """'King, Stephen' -> 'Stephen King'. A comma followed by a suffix
    ('King, Jr.') is not an inversion; extra commas are left to _base()."""
    head, sep, tail = name.partition(",")
    tail = tail.strip()
    if not sep or not tail:
        return name
    if tail.rstrip(".").casefold() in _NAME_SUFFIXES:
        return f"{head.strip()} {tail}"
    return f"{tail} {head.strip()}"


def norm_person(name: str) -> str:
    """Key for person identity: 'J. R. R. Tolkien', 'J.R.R. Tolkien' and
    'JRR Tolkien' all map to 'jrr tolkien', and 'Tolkien, J.R.R.' with them
    (raw epub credits are often 'Last, First'). Middle names/initials are
    kept (Ian Banks != Ian M. Banks here — that's an LLM decision)."""
    tokens = _base(_uninvert(name)).split()
    merged: list[str] = []
    run: list[str] = []
    for tok in tokens:
        if len(tok) == 1:
            run.append(tok)
        else:
            if run:
                merged.append("".join(run))
                run = []
            merged.append(tok)
    if run:
        merged.append("".join(run))
    return " ".join(merged)


def norm_title(title: str) -> str:
    """Strict title key: casefolded, punctuation-free, leading article dropped."""
    s = _base(title)
    for art in _ARTICLES:
        if s.startswith(art + " "):
            s = s[len(art) + 1 :]
            break
    return s


def loose_title(title: str) -> str:
    """Loose title key: also drops parentheticals and colon subtitles, so
    'We Are Legion (We Are Bob)' and 'We Are Legion' collide."""
    s = re.sub(r"\([^)]*\)", " ", title)
    s = s.split(":")[0]
    return norm_title(s)


def sort_name(name: str) -> str:
    """'Stephen King' -> 'King, Stephen'; suffix-aware ('Martin Luther King Jr'
    -> 'King, Martin Luther Jr'); single tokens unchanged."""
    tokens = name.strip().split()
    if len(tokens) < 2:
        return name.strip()
    suffix = None
    if tokens[-1].rstrip(".").casefold() in _NAME_SUFFIXES and len(tokens) > 2:
        suffix = tokens.pop()
    last = tokens.pop()
    result = f"{last}, {' '.join(tokens)}"
    if suffix:
        result += f" {suffix}"
    return result


def sort_title(title: str) -> str:
    """'The Martian' -> 'Martian, The'; other titles unchanged."""
    t = title.strip()
    for art in _ARTICLES:
        if t.casefold().startswith(art + " "):
            return f"{t[len(art) + 1:]}, {t[:len(art)]}"
    return t


def split_authors(raw: str | None) -> list[str]:
    """Split a raw multi-author credit ('Daniel Abraham & Ty Franck') into
    individual names. Commas are NOT split on — 'King, Stephen' is one name."""
    if not raw:
        return []
    parts = re.split(r"\s*(?:;|/|&|\band\b)\s*", raw)
    return [p.strip() for p in parts if p.strip()]


def parse_pub_date(raw: str | None) -> str | None:
    """Validate/coerce a publication date to ISO 'YYYY-MM-DD'.

    Accepts 'YYYY-MM-DD', 'YYYY-MM' (-> first of month), and 'YYYY'
    (-> Jan 1, the year-only convention). Anything else -> None."""
    if not raw:
        return None
    s = raw.strip()
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?", s)
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1)
    import datetime

    try:
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        return None


def norm_language(raw: str | None) -> str | None:
    """Normalize a language tag to BCP-47 casing ('EN' -> 'en',
    'pt_pt' -> 'pt-PT'). Anything that doesn't look like a tag -> None."""
    if not raw:
        return None
    s = raw.strip().replace("_", "-")
    m = re.fullmatch(r"([A-Za-z]{2,3})((?:-[A-Za-z0-9]{2,8})*)", s)
    if not m:
        return None
    primary = m.group(1).lower()
    rest = "".join(
        "-" + (p.upper() if len(p) == 2 and p.isalpha() else p)
        for p in m.group(2).split("-")
        if p
    )
    return primary + rest


def title_is_junk(title: str | None) -> bool:
    """Heuristic for 'this title is a filename, not a title' — the trigger for
    the cover-image fallback."""
    if not title or not title.strip():
        return True
    t = title.strip()
    if re.search(r"\.(epub|m4b|mobi|azw3?|pdf)$", t, re.IGNORECASE):
        return True
    if " " not in t and ("_" in t or re.fullmatch(r"[\w\-]{12,}", t)):
        return True
    return False
