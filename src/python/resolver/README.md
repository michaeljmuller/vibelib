# vibelib resolver

Resolves raw assets (`epubs`, `m4bs` rows) to the abstract catalog (`books`,
`people`, `series`) — linking to existing entities or creating new ones. A
free deterministic pass handles exact matches; Claude adjudicates the rest
with a confidence score. Confident results commit automatically; uncertain
ones (and anything proposing a pseudonym link) wait in a review queue.

All commands run through the compose service (nothing installs on the host):

```sh
cd src/docker
docker compose run --rm resolver <command> ...
```

---

## `resolve` — process unresolved assets

```sh
docker compose run --rm resolver resolve [--limit N] [--dry-run] [--asset TYPE:ID]
```

Selects raw assets that have **no** `book_epubs`/`book_m4bs` link and **no**
`resolutions` row, and processes each one (epubs first, then m4bs). Safe to
re-run anytime; already-handled assets are skipped, so the same command serves
the initial backlog and every incremental run after the loader adds assets.

| Flag | Meaning |
|---|---|
| `--limit N` | Process at most N assets, then stop. Useful for chunking the backlog. |
| `--dry-run` | Print what would happen (including the full proposal JSON) but write nothing — no catalog rows, no resolutions rows. Still makes LLM calls for non-exact assets. |
| `--asset TYPE:ID` | Resolve a single asset, e.g. `--asset epub:123` or `--asset m4b:45`. |

Each processed asset ends in one of these states (the `resolutions.status` /
`method` columns):

| Outcome | Meaning |
|---|---|
| `auto` / `exact` | Matched an existing book deterministically (same normalized title + same authors). Free — no LLM call. Linked immediately. |
| `auto` / `llm` | Claude resolved it with confidence ≥ 0.9 and no pseudonym proposals. Applied immediately. |
| `auto` / `llm_cover` | Same, but the cover image was needed (text metadata was junk). |
| `pending` / `llm`* | Confidence < 0.9, **or** the proposal includes pseudonym links (those are never auto-committed). Nothing applied — waits in the review queue. |
| `skipped` | Already linked or already has a resolutions row. |
| `error` | Asset missing or the model returned something unusable; logged, nothing written. Re-runnable. |

A summary of counts and token usage prints at the end.

---

## `backfill` — fill missing facts on existing books

```sh
docker compose run --rm resolver backfill [--limit N] [--dry-run]
```

Fills `publication_date` (the work's *first* publication, per world knowledge —
not the edition's date; `YYYY-01-01` when only the year is known) and
`language` (BCP-47) on books where they are NULL. Language comes from a linked
epub's language field when possible; otherwise one small LLM call per book.
Only NULL columns are written — existing values are never overwritten — so
re-running is always safe. Books the model doesn't recognize stay NULL rather
than getting a guessed date.

---

## `review` — work the queue of pending proposals

A `pending` proposal has written **nothing** to the catalog. Approving it
applies it; rejecting it discards it. You never need to edit the database for
items in this queue.

### Interactive mode (the primary workflow)

```sh
docker compose run --rm resolver review            # or: util/resolver.sh review
docker compose run --rm resolver review --clear    # each card starts at the top of the screen
```

Walks the pending queue one item at a time. Each card shows the asset's raw
metadata, the proposed entities, why the item needs review (pseudonym proposal
or sub-threshold confidence), and the model's rationale, then prompts:

```
[a]ccept  [e]dit  [s]kip  [r]eject  [q]uit →
```

- **accept** — apply the proposal and mark it approved.
- **edit** — type a plain-language correction ("the series is Dragonriders of
  Pern, position 11", "title is just 'Sobral City'"); one LLM call revises the
  proposal and the updated card is shown for accept / further edits. When an
  edited proposal is accepted, the revision and your instruction are saved to
  the resolution's audit row.
- **skip** — leave it pending and move on (un-accepted edits are discarded).
- **reject** — discard the proposal; the asset stays unlinked.
- **quit** — end the session; everything not yet handled stays pending.

### Scripting subcommands

```sh
docker compose run --rm resolver review list        # pending queue
docker compose run --rm resolver review rejected    # everything rejected, with s3 keys
docker compose run --rm resolver review show 42
docker compose run --rm resolver review approve 42
docker compose run --rm resolver review reject 42
```

`review rejected` lists every rejected resolution with the raw asset's title
and `s3_key` — the handle you need to find (and possibly remove) the
underlying file in the object store. Rejected assets are never reprocessed;
if you delete an asset from the library, delete its `resolutions` row too
(nothing references it), or delete the row alone to make the asset eligible
for re-resolution.

| Command | Effect |
|---|---|
| `list` | One line per pending resolution: id, asset, confidence, what it proposes (`link book N` / `create "Title"`), a `[pseudonym]` tag when relevant, and the model's rationale. |
| `show ID` | Full detail for one resolution: the asset's raw metadata side-by-side with the complete proposal JSON. Use this before deciding. |
| `approve ID` | Applies the proposal in one transaction (creates/links people, series, book; links the asset; writes any pseudonym rows) and marks it `approved`. |
| `reject ID` | Marks it `rejected`. Nothing is written; the asset stays unlinked. A rejected row also blocks reprocessing — to make the asset eligible for `resolve` again, delete its row from `resolutions`. |

Note on auto-committed resolutions: they are already applied and there is
currently no CLI to undo one — correcting a bad auto commit means removing the
`book_epubs`/`book_m4bs` row (and any entities only it created) by hand. Every
resolution row keeps the proposal it applied, which tells you exactly what to
remove.

---

## Configuration (environment variables)

Set in `src/docker/docker-compose.yml` / `src/docker/.env`; all are already
wired up for the compose service.

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for the LLM tier. |
| `RESOLVER_MODEL` | `claude-sonnet-5` | Model for adjudication. Switch to `claude-opus-4-8` if review-queue quality disappoints. |
| `RESOLVER_COVERS_DIR` | `/covers` (mounted from `data/covers`) | Where `{epub,m4b}/<id>.jpg` cover images live, for the fallback when text metadata is junk. |
| `PGHOST` / `PGDATABASE` / `PGUSER` / `PGPASSWORD` | `db` / `vibelib` / `vibelib` / from `.env` | Standard libpq connection variables. |

## `audit` — find mis-associated assets

```sh
util/audit.sh [--no-llm] [--check audio|multi|omnibus|orphans] [--json]
```

**Read-only.** Reports suspect epub/m4b → book links; fixes nothing. The false
positives here are the dangerous kind ("The Path of Ascension 2" and "…Book 3"
are *different books*), so every finding is for a human to judge.

| Check | Finds |
|---|---|
| `audio` | A book with an m4b and no epub. Audiobooks are almost always bought with the ebook, so audio-only usually means the epub sits on a *different* book — one book split in two. Hunts the missing epub across the whole library. |
| `multi` | A book with >1 epub or >1 m4b. Either distinct books were **collapsed** into one entity (re-link) or the same book was uploaded twice (**duplicate-file** — drop one). Reported per asset pair, since one book can be both. |
| `omnibus` | One audio file spanning several epub volumes. The schema cannot express this — surfaced, never force-linked. |
| `orphans` | Unlinked assets and the review backlog. |

Deterministic rules sort split candidates into confident / rejected / ambiguous;
only the ambiguous shortlist goes to Claude (`RESOLVER_MODEL`), and the command
prints a cost estimate before spending anything. `--no-llm` skips that tier.

The key discriminator is `audit.volume_number()`, read off the **raw** title —
`loose_title()` strips colon subtitles and parentheticals, which is exactly where
the volume number lives ("Azarinth Healer: Book Four" → "azarinth healer").

## Development

```sh
cd src/python/resolver
pip install -e '.[dev]'
pytest
```

The core is a library: `resolver.pipeline.resolve_asset(conn, "epub", 123)`
resolves one asset end-to-end and is the intended entry point for a future
web-upload flow; the CLI is a thin wrapper around it.
