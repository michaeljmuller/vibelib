"""The review card: what an admin needs to see before approving a proposal.

Ported from the CLI's interactive review, which rendered the same facts as
fixed-width terminal lines. Here each line is a row -- {label, verb, text,
warning} -- and the browser does the layout. The judgment stays on this side:
which rows exist, what a LINK actually points at, and the warning that a
proposed series CREATE would really split an existing series in two.
"""

from typing import Any

import psycopg

from . import apply, candidates
from .resolve import CLOSE_LOOK_CONFIDENCE


def _row(label: str, text: str, verb: str = "", warning: str = "") -> dict[str, str]:
    return {"label": label, "verb": verb, "text": text, "warning": warning}


def _person_row(
    label: str, action: dict[str, Any], names: dict[str, Any]
) -> dict[str, str]:
    if "link" in action:
        name = names.get("people", {}).get(action["link"])
        who = f"#{action['link']} {name}" if name else f"#{action['link']}"
        return _row(label, f"{who}  (raw: {action.get('raw_name', '?')})", verb="link")
    spec = action["create"]
    text = spec["name"]
    if spec.get("disambiguator"):
        text += f"  ({spec['disambiguator']})"
    return _row(label, text, verb="create")


def _series_rows(
    series: dict[str, Any], names: dict[str, Any], label: str = "Series"
) -> list[dict[str, str]]:
    """Render a series ref the way `apply` will actually execute it.

    A `create` whose name already exists is silently linked by
    `apply._resolve_series`, so showing it as CREATE misleads the reviewer.
    A create with no exact match but a close one gets a warning, because that
    is precisely how a series gets split in two (Longmire / Walt Longmire).
    """
    if "link" in series:
        name = names.get("series", {}).get(series["link"])
        who = f"#{series['link']} “{name}”" if name else f"#{series['link']}"
        return [_row(label, who, verb="link")]

    name = series["create"]["name"]
    match = names.get("series_match", {}).get(name)
    if match:
        return [
            _row(label, f"#{match['id']} “{match['name']}”  (matched by name)", verb="link")
        ]

    near = names.get("series_near", {}).get(name) or []
    warning = ""
    if near:
        hits = ", ".join(f"#{n['id']} “{n['name']}”" for n in near)
        warning = f"near-match: {hits} — edit to link to one of these instead?"
    return [_row(label, f"“{name}”", verb="create", warning=warning)]


def proposal_rows(
    proposal: dict[str, Any], names: dict[str, Any]
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    book = proposal.get("book", {})
    if "link" in book:
        title = names.get("books", {}).get(book["link"])
        who = f"#{book['link']} “{title}”" if title else f"#{book['link']}"
        rows.append(_row("Book", who, verb="link"))
        upd = book.get("update") or {}
        if "title" in upd:
            rows.append(_row("Retitle", f"“{upd['title']}”", verb="update"))
        if "series" in upd:
            rows += _series_rows(upd["series"], names, label="Reassign series")
        if "series_position" in upd:
            rows.append(_row("Reposition", str(upd["series_position"]), verb="update"))
    else:
        spec = book.get("create", {})
        rows.append(_row("Book", f"“{spec.get('title', '?')}”", verb="create"))
        series = spec.get("series")
        if series is not None:
            rows += _series_rows(series, names)
            pos = spec.get("series_position")
            rows.append(
                _row("Position", str(pos) if pos is not None else "none (interstitial)")
            )
        rows.append(_row("Published", spec.get("publication_date") or "?"))
        rows.append(_row("Language", spec.get("language") or "?"))
    # A credit apply will drop is shown, not hidden: the model did propose it,
    # and "why is this book not by both of them?" is a question best answered
    # before accepting rather than afterwards. Same reason _series_rows renders
    # a create that will really be a link.
    dropped = set(names.get("dropped_authors") or [])
    for i, a in enumerate(proposal.get("authors", [])):
        row = _person_row("Author", a, names)
        if i in dropped:
            row = _row(
                "Author",
                row["text"],
                verb="skip",
                warning="a pen name of theirs is credited above — recorded as a "
                        "pseudonym rather than crediting one person twice",
            )
        rows.append(row)
    for n in proposal.get("narrators", []):
        rows.append(_person_row("Narrator", n, names))
    # Resolved in link_names, because this is the one row whose meaning depends
    # on what is already in the database: the same two names are a new link, a
    # link that already exists, or two people about to be invented.
    for pseu in names.get("pseudonyms") or []:
        rows.append(_row("Pseudonym", pseu["text"], verb=pseu["verb"],
                         warning=pseu["warning"]))
    return rows


def raw_rows(meta: dict[str, Any]) -> list[dict[str, str]]:
    """What the file itself says, so the reviewer can judge the proposal against
    its source rather than against the model's summary of it."""
    rows = [
        _row("Title", meta.get("title") or "(none)"),
        _row("Authors", "; ".join(meta.get("authors") or []) or "(none)"),
    ]
    if meta.get("asset_type") == "epub":
        series = meta.get("series") or "(none)"
        pos = meta.get("series_position")
        rows.append(_row("Series", f"{series} #{pos}" if pos is not None else series))
        if meta.get("published_date"):
            rows.append(_row("Publication date", f"{meta['published_date']} (edition)"))
        if meta.get("language"):
            rows.append(_row("Language", meta["language"]))
    else:
        if meta.get("narrators"):
            rows.append(_row("Narrators", "; ".join(meta["narrators"])))
        if meta.get("album"):
            rows.append(_row("Album", meta["album"]))
        if meta.get("date"):
            # The ©day atom: when this *recording* was released. Easy to mistake
            # for when it was bought -- they coincide for a new release picked up
            # on release day, and are years apart for anything off the backlist.
            rows.append(_row("Publication date", f"{meta['date']} (edition)"))
    rows.append(_row("File", meta.get("s3_key") or "?"))
    return rows


def review_reason(proposal: dict[str, Any], confidence: float | None) -> str:
    """What is worth a second look on this card.

    Every proposal is reviewed, so this is not a reason it ended up here -- it
    is where to look first. The two named cases reach past the file being added
    and change records other books share, which is the kind of mistake that is
    tedious to find later and worse to undo.
    """
    if proposal.get("pseudonyms"):
        return "proposes a pseudonym link — check it before it ties two people together"
    if proposal.get("book", {}).get("update"):
        return "changes the existing book's own record, not just this file"
    if confidence is not None and confidence < CLOSE_LOOK_CONFIDENCE:
        return f"the model was unsure ({confidence:.2f}) — worth reading closely"
    return "nothing unusual; check it and accept"


def _person_near(conn: psycopg.Connection, name: str) -> list[dict[str, Any]]:
    """People close enough to `name` that creating it may be a split rather than
    a new person. Same idea as _series_near, and the same advisory status."""
    rows = conn.execute(
        """SELECT id, name FROM people
            WHERE similarity(lower(name), lower(%(n)s)) > %(th)s
            ORDER BY similarity(lower(name), lower(%(n)s)) DESC LIMIT 3""",
        {"n": name, "th": candidates.NAME_SIM_THRESHOLD},
    ).fetchall()
    return [dict(r) for r in rows]


def _resolve_person_for_display(
    conn: psycopg.Connection, name: str
) -> dict[str, Any]:
    """How apply._resolve_person will really treat this name: link an existing
    person, or create one. The pseudonym path never carries an id -- it resolves
    by name alone -- so this is the only way the card can say which."""
    existing = candidates.find_person_exact(conn, name)
    if existing is not None:
        return {"id": existing["id"], "text": f"#{existing['id']} {existing['name']}",
                "new": False, "near": []}
    return {"id": None, "text": f"“{name}” (new person)", "new": True,
            "near": _person_near(conn, name)}


def _pseudonym_rows(
    conn: psycopg.Connection, proposal: dict[str, Any]
) -> list[dict[str, str]]:
    """One rendered row per proposed pen-name link, saying what accepting does.

    Three things worth seeing before accepting, none of which the bare names
    showed: whether either side is about to become a new person row (the
    pseudonym path resolves by name only, so a spelling variant silently makes
    a second Travis Deverell); whether the link is already recorded; and whether
    it is already recorded the *other way round*, where adding this one leaves
    the pair pointing at each other -- which is the state nobody103 and Domagoj
    Kurmaić are in, and the reason apply refuses to uncredit a book.
    """
    out: list[dict[str, str]] = []
    for pseu in proposal.get("pseudonyms", []):
        pen = _resolve_person_for_display(conn, pseu.get("pseudonym_name") or "?")
        reals = [
            _resolve_person_for_display(conn, n)
            for n in pseu.get("real_person_names", [])
        ]
        text = f"{pen['text']} → {' + '.join(r['text'] for r in reals)}"

        warnings: list[str] = []
        for side in (pen, *reals):
            if side["near"]:
                hits = ", ".join(f"#{n['id']} “{n['name']}”" for n in side["near"])
                warnings.append(f"near-match: {hits} — a second row for the same person?")

        existing_ids = [r["id"] for r in reals if r["id"] is not None]
        forward = reverse = False
        if pen["id"] is not None and existing_ids:
            rows = conn.execute(
                """SELECT pseudonym_id, author_id FROM author_pseudonyms
                    WHERE (pseudonym_id = %(pen)s AND author_id = ANY(%(reals)s))
                       OR (author_id = %(pen)s AND pseudonym_id = ANY(%(reals)s))""",
                {"pen": pen["id"], "reals": existing_ids},
            ).fetchall()
            forward = any(r["pseudonym_id"] == pen["id"] for r in rows)
            reverse = any(r["author_id"] == pen["id"] for r in rows)
        # Nothing is written when the link is already there, whichever else is
        # true -- the insert is ON CONFLICT DO NOTHING.
        if forward:
            verb = "skip"
            warnings.insert(
                0,
                "already recorded in both directions — the pair point at each other"
                if reverse
                else "already recorded; nothing to add",
            )
        else:
            verb = "create" if any(s["new"] for s in (pen, *reals)) else "link"
            if reverse:
                warnings.append(
                    "already recorded the other way round — accepting adds the "
                    "mirror row, leaving the two pointing at each other"
                )
        out.append({"text": text, "verb": verb, "warning": "  ".join(warnings)})
    return out


def _series_near(conn: psycopg.Connection, name: str) -> list[dict[str, Any]]:
    """Existing series close enough to `name` that creating it would probably
    be a split rather than a genuinely new series. Advisory only."""
    rows = conn.execute(
        """SELECT id, name FROM series
           WHERE similarity(lower(name), lower(%(n)s)) > %(th)s
              OR position(lower(name) IN lower(%(n)s)) > 0
              OR position(lower(%(n)s) IN lower(name)) > 0
           ORDER BY similarity(lower(name), lower(%(n)s)) DESC
           LIMIT 3""",
        {"n": name, "th": candidates.NAME_SIM_THRESHOLD},
    ).fetchall()
    return [dict(r) for r in rows]


def link_names(conn: psycopg.Connection, proposal: dict[str, Any]) -> dict[str, Any]:
    """Look up display names for every LINK action in a proposal, so the card
    can show what an id points at — plus, for every proposed series CREATE,
    what `apply` will really do with it (link to an existing row, or split)."""
    book = proposal.get("book", {})
    book_ids = [book["link"]] if "link" in book else []
    series_ids = []
    series_creates: list[str] = []
    for src in (
        (book.get("create") or {}).get("series"),
        (book.get("update") or {}).get("series"),
    ):
        if not src:
            continue
        if "link" in src:
            series_ids.append(src["link"])
        else:
            series_creates.append(src["create"]["name"])
    people_ids = [
        a["link"]
        for a in proposal.get("authors", []) + proposal.get("narrators", [])
        if "link" in a
    ]

    names: dict[str, Any] = {
        "books": {}, "series": {}, "people": {},
        "series_match": {}, "series_near": {},
        # Asked of apply itself, so the card cannot disagree with what
        # accepting does -- see apply.dropped_author_indexes.
        "dropped_authors": sorted(apply.dropped_author_indexes(conn, proposal)),
        "pseudonyms": _pseudonym_rows(conn, proposal),
    }
    for name in series_creates:
        # Same call apply._resolve_series makes, so the card cannot disagree
        # with what approving it actually does.
        existing = candidates.find_series_exact(conn, name)
        if existing is not None:
            names["series_match"][name] = dict(existing)
        else:
            near = _series_near(conn, name)
            if near:
                names["series_near"][name] = near
    if book_ids:
        for r in conn.execute(
            "SELECT id, title FROM books WHERE id = ANY(%s)", (book_ids,)
        ).fetchall():
            names["books"][r["id"]] = r["title"]
    if series_ids:
        for r in conn.execute(
            "SELECT id, name FROM series WHERE id = ANY(%s)", (series_ids,)
        ).fetchall():
            names["series"][r["id"]] = r["name"]
    if people_ids:
        for r in conn.execute(
            "SELECT id, name FROM people WHERE id = ANY(%s)", (people_ids,)
        ).fetchall():
            names["people"][r["id"]] = r["name"]
    return names
