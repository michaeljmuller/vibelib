# vibelib web

The library's browse interface: a FastAPI app serving a JSON API over the
curated catalog (`books`, `people`, `series`) plus a static frontend that
consumes it. The same API is what a mobile client would talk to.

Book files stay in the object store and are streamed out through the app, which
is the only thing that ever talks to the bucket (see the note at the top of
`web/s3.py` for why, rather than presigned URLs). Cover images come off
`data/covers/{epub,m4b}/{asset_id}.jpg`, mounted from the host — no S3 round
trip to draw the grid.

Everything is behind Google sign-in and an allowlist; see `web/auth.py`.

```sh
util/web.sh          # build and start on http://localhost:8000
util/web.sh logs
util/web.sh stop
util/test.sh         # unit tests, inside the image
```

## API

| Route | |
| --- | --- |
| `GET /api/books` | paginated list: `q`, `author_id`, `series_id`, `language`, `format=epub\|m4b`, `sort=title\|date\|series\|acquired`, `limit`, `offset` |
| `GET /api/books/{id}` | detail: editions (with size, duration, narrators), description, series siblings |
| `GET /api/authors` | id, name, book count |
| `GET /api/series` | id, name, book count, `highest_position`, `is_complete` |
| `GET /api/languages` | language code, book count |
| `GET /api/me` | the signed-in user |
| `GET /covers/{epub\|m4b}/{asset_id}` | cover image; 404 when none was extracted |
| `GET /download/{epub\|m4b}/{asset_id}` | the file itself, streamed; honors Range so players can seek |

Admins only:

| Route | |
| --- | --- |
| `GET/POST /api/users`, `DELETE /api/users/{id}` | the sign-in allowlist |
| `GET /api/admin/ingest/*` | adding books — see below |

`q` matches title, author name, and series name, served by the trigram indexes
from `src/sql/migrations/002_resolutions.sql`.

## Adding books (`/ingest`, admins only)

Two lists, which are the two states a file can be in. Both are queries, not
tables — which is why the queue needs no persistence and there is no schema for
any of this.

**List A — files with no row yet.** A work queue. Items arrive by being dropped
on the page, or by being found in the bucket with nothing in `epubs`/`m4bs`
pointing at them (`POST /scan`, run on page load). `web/ingest/worker.py` works
through them one at a time on its own thread: fetch or store, read the metadata,
write the raw rows, extract the cover, stamp today as the acquisition date.

Because that thread belongs to the app and not to a request, closing the page
interrupts nothing — reopen it and the same job is still going, at whatever
percentage it has reached. The one exception is the browser's own upload, whose
bytes live in the page; the UI says so while one is running.

Phases are named for the bytes actually moving, and only those carry a
percentage — reading an epub is milliseconds and `ffprobe` on a local m4b is a
few seconds with nothing to report:

    queued · uploading · downloading · storing · reading · done · failed

**List B — rows with no book.** Click one and `web/ingest/resolve.py` proposes
where it belongs: free and deterministic when the normalized title and author
set match an existing book (`candidates.py`), otherwise one structured Claude
call (`llm.py`). The card shows the file's own metadata beside the proposal, and
you Accept, Request changes, or Cancel.

**Remove** is the other way out of list B, for a file that should never have
been read — the wrong upload, a stray object. It deletes the raw row (children
by cascade, plus the extracted cover and any `resolutions` row) and nothing
else: the bucket object stays, because the row is something this app made and
can make again while the object is the file itself. Deleting an object is a
back-end job, on purpose. The consequence is stated on the confirmation — a
removed row whose object is still in the bucket comes back on the next scan —
and a linked asset is refused in the `DELETE`'s own `WHERE` clause, so the
cascade can never strip a file off a book in the library.

**Nothing is written until Accept.** `resolve` and `revise` compute a proposal
and hand it back; the browser holds it. Cancel is the browser dropping it, which
is why there is no route for it and nothing to clean up afterwards — the asset
simply stays in list B, untouched. Accept is the only route that writes:
`apply.py` links or creates the book, people and series in one transaction,
`backfill.py` fills a publication date or language the proposal could not
supply, and one `resolutions` row records what was applied.

| Route | |
| --- | --- |
| `GET /state` | both lists and whether the worker is busy; the page polls this while anything moves |
| `POST /scan` | queue every bucket object with no row |
| `POST /upload` | take the bytes into staging and hand them to the worker |
| `POST /clear-finished` | drop finished jobs from the display; also releases a failed fetch for another try |
| `POST /resolve` | propose a mapping. Writes nothing |
| `POST /revise` | a plain-language correction, re-proposed. Writes nothing |
| `POST /accept` | apply it. The only route that writes to the catalog |
| `POST /discard` | delete the raw row for an unlinked asset. Never touches the bucket |

Configuration: `ANTHROPIC_API_KEY` (without it, only this page stops working),
`RESOLVER_MODEL` (default `claude-sonnet-5`), and `INGEST_STAGING_DIR` — a
bind-mounted directory, because a file being read can be a gigabyte and the
container's writable layer is the wrong place for one. Everything in it is
disposable; the worker sweeps it at startup. The image needs `ffmpeg`, since
`ffprobe` is the only reader that handles both m4b chapter conventions this
library contains.

Cost is one Claude call per proposal, one per correction, and at most one on
accept.

## Frontend

`web/static/` — plain HTML/CSS/JS, no build step, no external assets. Cover grid
with infinite scroll, search and filter bar, and a detail dialog with download
buttons and the series strip. Books whose asset carried no cover render a
title/author placeholder tile. `ingest.js` is mostly a view of state it polls
rather than state it holds; uploads go through `XMLHttpRequest` rather than
`fetch`, because only XHR reports upload progress and an audiobook is big enough
that a silent minute looks like a hang.

Publisher-supplied descriptions are HTML; the detail view runs them through a
tag whitelist (`sanitize()` in `app.js`) before rendering.
