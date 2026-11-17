"""Correct acquisition dates from saved Amazon digital orders.

Run it through util/amazon-dates.sh, which runs it in a container. See
docs/amazon-dates.txt for the whole process.

  match FILE...   Read order files saved by util/amazon-digital-orders.js,
                  match each purchase to an epub or m4b through the web API,
                  and write plan.json and report.txt. Read-only.
  apply PLAN      Send the "earlier" rows of a plan to the web API, then read
                  each book back and make sure that the dates changed.

Standard library only, so the container needs no packages.
"""

import datetime
import difflib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("VIBELIB_URL", "https://vibelib.themullers.org").rstrip("/")
TOKEN = os.environ.get("ADMIN_API_TOKEN", "")

# Lines on an order card that are buttons or notices, not item data.
ACTIONS = {
    "Return for refund", "Write a product review", "Manage content and devices",
    "Ask Alexa about this order", "Buy it again", "View your item",
    "Get product support", "Manage your subscription",
}
ACTION_PREFIXES = ("View order", "Return window")

NUMBER_WORDS = ("one two three four five six seven eight nine ten eleven twelve "
                "thirteen fourteen fifteen sixteen seventeen eighteen nineteen "
                "twenty").split()

# Subtitle parts that every book in a genre shares, so they find nothing useful.
GENRE_PARTS = ("litrpg", "progression fantasy", "a novel", "cultivation novel")

MIN_SCORE = 0.6


# --- the API -------------------------------------------------------------------


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + TOKEN)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path}: HTTP {e.code} {e.read()[:200]!r}")


_searches = {}


def search(q):
    # The epub and the m4b of one book send the same queries; send each once.
    if q not in _searches:
        _searches[q] = call("GET", "/api/books?limit=20&q=" + urllib.parse.quote(q))["items"]
    return _searches[q]


# --- reading the order files -----------------------------------------------------


def parse_file(path):
    """One record per purchased item: order, date, title, kind, authors."""
    items = []
    text = open(path, encoding="utf-8").read()
    for card in text.split("\n=====\n"):
        lines = [l.strip() for l in card.strip().splitlines() if l.strip()]
        if "ORDER PLACED" not in lines:
            continue  # a subscription charge, not a purchase
        date = datetime.datetime.strptime(
            lines[lines.index("ORDER PLACED") + 1], "%B %d, %Y").date().isoformat()
        order_line = next(l for l in lines if l.startswith("ORDER #"))
        order = order_line.split("# ", 1)[1]
        body = lines[lines.index(order_line) + 1:]

        # Each item is: title, format, then authors (absent for most audiobooks),
        # followed by buttons. A line after the authors starts the next item.
        cur = None
        found = []
        for l in body:
            if l in ACTIONS or l.startswith(ACTION_PREFIXES):
                cur = None
                continue
            if cur is None or cur["authors"] is not None:
                cur = {"title": l, "format": None, "authors": None}
                found.append(cur)
            elif cur["format"] is None and ("Edition" in l or "Audiobook" in l):
                cur["format"] = l
            else:
                cur["authors"] = l
        for it in found:
            if not it["format"]:
                continue  # video and other non-book items
            kind = "epub" if it["format"].startswith("Kindle") else "m4b"
            items.append({"order": order, "date": date, "kind": kind,
                          "title": it["title"], "authors": it["authors"]})
    return items


# --- matching ---------------------------------------------------------------------


def norm(s):
    s = s.lower().replace("&", "and").replace("’", "'")
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s).split())


def similarity(a, b):
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def book_number(title):
    """The number in "Book 7", "Book Seven" or "Monsters 13:", if any."""
    m = (re.search(r"\b(?:book|volume)\s+(\d+|" + "|".join(NUMBER_WORDS) + r")\b", title, re.I)
         or re.search(r"\b(\d{1,2})\b(?=\s*:)", title))
    if not m:
        return None
    v = m.group(1).lower()
    return int(v) if v.isdigit() else NUMBER_WORDS.index(v) + 1


def queries(title):
    """Search strings, most specific first.

    The search matches text in titles, so punctuation that the library title
    does not share ("A(n)t", "Healer, Book Six") makes a query miss. Each
    subtitle part, and each part cut at its first bracket or comma, is tried.
    """
    t = re.sub(r"\s*\(.*?\)\s*", " ", title).strip()
    main = t.split(":")[0].strip()
    qs = [t, main, re.split(r"\s+(?:book|volume)\s+\w+$", main, flags=re.I)[0]]
    # Parts of the raw title too: dropping "(...)" above turns "A(n)t" into "A t".
    for part in t.split(":") + title.split(":"):
        part = part.strip()
        if len(part) > 3 and not any(g in part.lower() for g in GENRE_PARTS):
            qs += [part, re.split(r"[(),]", part)[0].strip()]
    out = []
    for q in qs:
        if len(q) > 3 and q not in out:
            out.append(q)
    return out


def best_book(item):
    qs = queries(item["title"])
    cands = {}
    for q in qs:
        for b in search(q):
            cands[b["id"]] = b
    n = book_number(item["title"])
    names = {norm(a) for a in (item["authors"] or "").split(",") if a.strip()}
    best = None
    for b in cands.values():
        score = max(similarity(q, b["title"]) for q in [item["title"]] + qs)
        if names & {norm(a) for a in b["authors"]}:
            score += 0.3
        if n and b.get("series_position") and b["series_position"] != n:
            score -= 1  # right series, wrong book
        if best is None or score > best[0]:
            best = (score, b)
    return best if best and best[0] >= MIN_SCORE else None


def match(paths):
    items = [it for p in paths for it in parse_file(p)]
    details = {}
    rows = []
    for it in items:
        row = dict(it)
        found = best_book(it)
        if found:
            score, b = found
            if b["id"] not in details:
                details[b["id"]] = call("GET", f"/api/books/{b['id']}")
            assets = details[b["id"]][it["kind"] + "s"]
            row.update(book_id=b["id"], book_title=b["title"], score=round(score, 2))
            if assets:
                a = max(assets, key=lambda a: similarity(it["title"], a["title"] or ""))
                row.update(asset_id=a["id"], current=a["acquired_on"])
        rows.append(row)

    # One asset can be bought more than once (several years' files, or a
    # re-purchase). The earliest purchase is the acquisition.
    earliest = {}
    for r in rows:
        if "asset_id" in r:
            key = (r["kind"], r["asset_id"])
            if key not in earliest or r["date"] < earliest[key]["date"]:
                earliest[key] = r

    for r in rows:
        if "book_id" not in r:
            r["status"] = "missing"
        elif "asset_id" not in r:
            r["status"] = "missing-" + r["kind"]
        elif earliest[(r["kind"], r["asset_id"])] is not r:
            r["status"] = "duplicate"
        elif r["current"] == r["date"]:
            r["status"] = "same"
        elif r["current"] is None or r["date"] < r["current"]:
            r["status"] = "earlier"
        else:
            r["status"] = "keep"  # the library has an earlier date
    return rows


def report(rows):
    out = []
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    out.append("counts: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    sections = [
        ("earlier", "Will move earlier (apply sends these)"),
        ("keep", "Library date is earlier than the order; left alone"),
        ("missing-epub", "Book is in the library, but not its epub"),
        ("missing-m4b", "Book is in the library, but not its m4b"),
        ("missing", "Not in the library (check by hand before trusting)"),
    ]
    for status, heading in sections:
        sel = [r for r in rows if r["status"] == status]
        if not sel:
            continue
        out.append(f"\n{heading}:")
        for r in sel:
            line = f"  {r['date']}  {r['kind']:4}  {r['title'][:60]}"
            if "book_title" in r:
                line += f"\n        -> book {r['book_id']} {r['book_title']!r} (score {r['score']})"
            if r.get("current"):
                line += f", now {r['current']}"
            out.append(line)
    return "\n".join(out) + "\n"


# --- applying ---------------------------------------------------------------------


def apply(plan_path):
    rows = [r for r in json.load(open(plan_path)) if r["status"] == "earlier"]
    for r in rows:
        call("PUT", f"/api/admin/assets/{r['kind']}/{r['asset_id']}/acquired-on",
             {"acquired_on": r["date"]})
        print(f"set {r['kind']} {r['asset_id']} to {r['date']}  {r['book_title']}")
    bad = 0
    for book_id in sorted({r["book_id"] for r in rows}):
        d = call("GET", f"/api/books/{book_id}")
        have = {(k, a["id"]): a["acquired_on"] for k in ("epub", "m4b") for a in d[k + "s"]}
        for r in rows:
            if r["book_id"] == book_id and have.get((r["kind"], r["asset_id"])) != r["date"]:
                bad += 1
                print(f"MISMATCH {r['kind']} {r['asset_id']}: {have.get((r['kind'], r['asset_id']))}")
    print(f"{len(rows)} sent, {bad} mismatches on read-back")
    return 1 if bad else 0


def main(argv):
    if not TOKEN:
        raise SystemExit("ADMIN_API_TOKEN is not set")
    if len(argv) >= 2 and argv[0] == "match":
        rows = match(argv[1:])
        out_dir = os.path.dirname(os.path.abspath(argv[1]))
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        plan = os.path.join(out_dir, f"plan-{stamp}.json")
        text = report(rows)
        json.dump(rows, open(plan, "w"), indent=1)
        open(plan.replace(".json", ".txt"), "w").write(text)
        print(text)
        print("plan:", os.path.basename(plan))
        return 0
    if len(argv) == 2 and argv[0] == "apply":
        return apply(argv[1])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
