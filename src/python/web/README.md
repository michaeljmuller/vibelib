# vibelib web

The library's browse interface: a FastAPI app serving a JSON API over the
curated catalog (`books`, `people`, `series`) plus a static frontend that
consumes it. The same API is what a mobile client would talk to.

Book files stay in S3 and are handed out as short-lived presigned URLs, so the
container never proxies file bytes. Cover images come off the extracted-cover
tree the resolver also reads (`data/covers/{epub,m4b}/{asset_id}.jpg`), mounted
read-only — no S3 round trip to draw the grid.

There is no authentication; the service assumes it sits on a private host.

```sh
util/web.sh          # build and start on http://localhost:8000
util/web.sh logs
util/web.sh stop
```

## API

| Route | |
| --- | --- |
| `GET /api/books` | paginated list: `q`, `author_id`, `series_id`, `language`, `format=epub\|m4b`, `sort=title\|date\|series`, `limit`, `offset` |
| `GET /api/books/{id}` | detail: editions (with size, duration, narrators), description, series siblings |
| `GET /api/authors` | id, name, book count |
| `GET /api/series` | id, name, book count, `highest_position`, `is_complete` |
| `GET /api/languages` | language code, book count |
| `GET /covers/{epub\|m4b}/{asset_id}` | cover image; 404 when none was extracted |
| `GET /download/{epub\|m4b}/{asset_id}` | 302 to a 5-minute presigned S3 URL |

`q` matches title, author name, and series name, served by the trigram indexes
from `src/sql/migrations/002_resolutions.sql`.

## Frontend

`web/static/` — plain HTML/CSS/JS, no build step, no external assets. Cover grid
with infinite scroll, search and filter bar, and a detail dialog with download
buttons and the series strip. Books whose asset carried no cover render a
title/author placeholder tile.

Publisher-supplied descriptions are HTML; the detail view runs them through a
tag whitelist (`sanitize()` in `app.js`) before rendering.
