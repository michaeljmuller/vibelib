# vibelib loader

Ingests new `.epub` and `.m4b` objects from the object store into the **raw**
asset tables (`epubs`, `m4bs`, `epub_authors`, `m4b_chapters`) and extracts
cover art to `data/covers/<type>/<id>.jpg`.

It stops there. It never creates a book, links an asset, or writes
`m4b_narrators` — those are curation, and they belong to the resolver. The
pipeline is:

```
object store  --loader-->  epubs / m4bs  --resolver-->  books / people / series
```

The bucket is the source of truth for what exists; the `s3_key` columns record
what has been ingested. Anything in the former and not the latter is new work,
which makes the loader re-runnable: it only ever adds what's missing.

All commands run through the compose service (nothing installs on the host):

```sh
util/loader.sh scan                 # what's in the bucket but not the database
util/loader.sh load                 # ingest all of it
util/loader.sh load --limit 5       # ingest a few first
util/loader.sh load --type epub     # epubs only (m4bs are ~300MB each)
util/loader.sh load --dry-run       # list what would be loaded, write nothing
```

`.mobi` objects are ignored by design — the schema has no place for them.

## What it extracts

**epub** — from the OPF package document, raw. `dc:date` keeps whatever format
the file uses; `dc:creator` keeps its punctuation. Normalization is the
resolver's job, and keeping this layer literal is what lets a *wrong title
inside a file* be recognized as a file problem rather than a bug.

Two conventions appear in this library for both cover art and series, and the
loader handles both:

| | EPUB2 | EPUB3 |
|---|---|---|
| cover | `<meta name="cover" content="<item id>">` → manifest | manifest item with `properties="cover-image"` |
| series | `<meta name="calibre:series">` | `belongs-to-collection` |

ASINs are usually stated as `urn:asin:B0CV8MMBFT` (914 of the first 1,293
epubs); the older `opf:scheme="AMAZON"` spelling is also accepted.

**m4b** — tags and cover art via mutagen, chapters via `ffprobe`. Chapters are
the reason for the ffmpeg dependency: an m4b may carry them as a Nero `chpl`
atom or as a QuickTime chapter text track, mutagen exposes neither, and this
library contains both shapes. A file with no chapters is normal and not an error.

## Failure handling

**Each asset commits on its own.** A file that won't parse costs one book, not
the batch, and every failure is listed at the end of the run:

```
2 file(s) failed and were NOT loaded:
  Some Broken Book.epub
      ValueError: OPF has no dc:title
```

This is deliberate. An earlier import silently missed 81 files — they sat in the
bucket, invisible, until an acquisition-date audit turned them up months later.
A loader that reports what it dropped is the whole point of this being a
first-class component rather than a script someone ran once.

`scan` is read-only and safe to run anytime; run it after uploading to see what
the next `load` will pick up.

## Development

```sh
cd src/python/loader
pip install -e '.[dev]'
pytest
```

Tests run against synthetic epubs built in-memory (`tests/test_epub.py`) and
mocked ffprobe output (`tests/test_m4b.py`) — no network, no fixtures on disk.
