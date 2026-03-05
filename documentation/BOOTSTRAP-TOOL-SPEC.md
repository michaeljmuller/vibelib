# Bootstrap Tool Specification

## Purpose

The S3 bucket contains ~2800 objects (EPUBs and M4Bs) that pre-exist the database.  The bootstrap
tool does a one-time population of the database from this existing data so the library is usable
without manually entering every book.

The tool is **idempotent** — running it more than once produces no duplicate records — and
**resumable** — it can be interrupted at any point and restarted safely.

The tool lives in `bootstrap/` at the project root.  In the future the same ingestion logic will
be incorporated into the web application for drag-and-drop file ingestion.

---

## S3 Object Inventory

All files are in a flat namespace with no directory structure.  The tool processes `.epub` and
`.m4b` files and silently skips everything else.  Not every file is guaranteed to carry useful
embedded metadata; sparse or missing metadata is handled gracefully.

---

## Processing Pipeline

On startup the tool:

1. Queries `bootstrap_progress` to build the set of already-processed S3 keys.
2. Queries for books where `books.asin IS NOT NULL` and no `amazon_metadata` row exists (Amazon catch-up queue;
   see [Amazon Scraping](#amazon-scraping)).
3. Paginates through all S3 objects, skipping keys already in `bootstrap_progress`.

Files are processed in two passes: all EPUBs first, then M4Bs.  This ensures that the richer EPUB
metadata creates the canonical `books` record before the M4B tries to match against it.

For each file the tool:

1. Skips the file immediately if it is not `.epub` or `.m4b`.
2. Downloads the file, using a local ETag-keyed cache to avoid redundant S3 downloads.
3. Extracts metadata from the file (see [Metadata Extraction](#metadata-extraction)).
4. Attempts to match against an existing `books` record (see [Matching](#matching)).
5. In a single DB transaction: create or reuse the `books` record; create or match `authors`;
   populate `book_authors`, `book_tags`, and `ebook_files`/`audiobook_files`; write the
   `bootstrap_progress` row.  The transaction commits here.
6. If the book has a valid ASIN, scrapes Amazon for supplemental metadata and series info
   **outside the transaction** (see [Amazon Scraping](#amazon-scraping)).
7. Emits a progress log line (see [Progress Monitoring](#progress-monitoring)).

After all S3 objects are processed, the tool works through the Amazon catch-up queue, then prints
the post-run report.

---

## Metadata Extraction

### EPUB

Uses `ebooklib` (always with `options={"ignore_ncx": True}`).  The existing
`extract_epub_metadata()` and `extract_isbn_from_content()` functions from `service/app.py` are
reused.

| Field | Destination |
|---|---|
| Title | `books.title` |
| Authors | `authors`, `book_authors` |
| Language | `books.language_code` |
| Publication date | `books.publication_date`, `books.publication_year` |
| ISBN | `books.isbn` |
| ASIN (`mobi-asin` or `asin` identifier) | `ebook_files.asin`; also copied to `books.asin` if `books.asin` is currently null |
| Subjects | `book_tags` |
| File size (from S3 object metadata) | `ebook_files.file_size_bytes` |

`ebook_files.file_format` is set from the file extension (e.g., `"epub"`).

EPUBs do not reliably carry series information.  Series data comes exclusively from Amazon.

### M4B

Uses `mutagen` to read MP4/AAC tags.

| Field | Destination |
|---|---|
| Title (`\xa9nam`) | `books.title` |
| Artist (`\xa9ART`) | `authors`, `book_authors` |
| Year (`\xa9day`) | `books.publication_year` |
| Duration | `audiobook_files.duration_seconds` |
| File size (from S3 object metadata) | `audiobook_files.file_size_bytes` |

`audiobook_files.file_format` is set to `"m4b"`.

M4B files do not reliably carry narrator information.  The `narrators` and `audiobook_narrators`
tables are not populated by the bootstrap tool.

### Title Fallback

If a file yields no title, the filename minus its extension is used as the title.  This applies
to both EPUBs and M4Bs and ensures `books.title` (which is NOT NULL) always has a meaningful
value.  A `no_metadata` issue is recorded whenever a fallback title is used.

### Acquisition Date

`books.acquisition_date` is set to the S3 object's `LastModified` date, which is the best
available proxy for when the file was acquired.  This is available from the S3 object listing
without downloading the file.

---

## Matching

The same book often exists as both an EPUB and an M4B.  All formats for the same book must map to
a single `books` record.  Author names must be reconciled across files to avoid duplicates in the
`authors` table.

### Title Normalization

Before any comparison, titles are normalized:
- Lowercase
- Strip punctuation
- Strip leading articles ("the", "a", "an")
- Collapse whitespace

Exact normalized matches are accepted without a confidence check.  Near-matches are scored with
`rapidfuzz`; matches at or above `FUZZY_MATCH_THRESHOLD` are accepted automatically, below it are
flagged as `match_conflict` issues.

### Author Normalization

Author names from different files for the same book may vary in punctuation, use of initials, or
word order.  Normalization steps:
- Normalize "Last, First" to "First Last"
- Strip punctuation from initials ("P.G." → "PG")
- Lowercase for comparison only; original casing is preserved for storage

The same `rapidfuzz` threshold governs author fuzzy matching.  When a new name matches an existing
`authors` record, it is mapped to that record rather than creating a duplicate.  The longer of the
two name forms is kept as `primary_name` (e.g., "Stephen King" is preferred over "King, S.").

The `authors.pseudonym_for` relationship is not populated by the bootstrap tool.

### Book Identity

A file is matched to an existing `books` record when:
1. Normalized title matches (exact or above threshold), **and**
2. At least one author matches (normalized).

If a title matches but no author metadata is available, the tool logs a `match_conflict` issue and
creates a new record rather than merging speculatively.

---

## Amazon Scraping

Books with a valid ASIN are enriched by scraping Amazon immediately after the book record is
created.  The existing `scrape_amazon_metadata()` function from `service/app.py` (Playwright /
headless Chromium) is reused.  Amazon is the only source of series information during bootstrap.

### VPN Requirement

All Amazon requests **must** exit through the gluetun VPN container.  The bootstrap container
runs with `network_mode: "container:gluetun"`, sharing the network namespace of the running
gluetun container (started by the main `docker/docker-compose.yml`).  Running the tool directly
on the host is not supported.

### Series

When Amazon returns a series name, the tool:
1. Normalizes the name (same rules as title normalization) and fuzzy-matches against existing
   `series` records.
2. Creates a new `series` record if no match is found.
3. Creates a `book_series` record with the appropriate `sort_order` and `display_number`.

### Books Without an ASIN

Public domain titles and other files without a valid ASIN are silently skipped for Amazon
enrichment.  They will have no `amazon_metadata` record and no series association from bootstrap.

### Rate Limiting

- A randomized delay of `AMAZON_DELAY_MIN`–`AMAZON_DELAY_MAX` seconds is inserted between
  requests.
- Each ASIN is scraped at most once per run.  Before scraping, the tool checks `amazon_metadata`;
  if a row already exists the scrape is skipped.
- CAPTCHA or bot-detection responses are logged as `amazon_captcha` issues and skipped without
  retry.

### Resumability

Amazon scraping happens after the per-file DB transaction commits, so an interruption during
scraping leaves a book record with no `amazon_metadata` row.  On startup the tool queries for
`books.asin IS NOT NULL` with no corresponding `amazon_metadata` row and adds those books to a
catch-up queue that is processed after the main S3 pass.

---

## Resume Behavior

The tool can be interrupted at any point — SIGINT, SIGTERM, crash, lost connection — and restarted
cleanly.

### Per-File Transaction

All DB writes for a single file are wrapped in one transaction:
- `books`, `authors`, `book_authors`, `book_tags`
- `ebook_files` or `audiobook_files`
- The `bootstrap_progress` row for this S3 key

The `bootstrap_progress` row is inside the transaction, so a file is marked done only if every
write committed.  A mid-transaction kill leaves no partial data; the file is simply reprocessed on
restart.

### Skipping Completed Files

On startup the tool loads the set of S3 keys already in `bootstrap_progress`.  Any key in that set
is skipped immediately without downloading or processing.

Wrong-type files (non-EPUB, non-M4B) are skipped before any work is done and are **not** recorded
in `bootstrap_progress`.

### File Cache

Files are cached locally by S3 ETag.  On a resumed run, already-cached files are not re-downloaded,
which speeds up reprocessing of any file whose transaction did not commit before the interruption.

---

## Issue Tracking

Problems are recorded in `bootstrap_issues` immediately when encountered so a partial run still
leaves useful diagnostic information.

### Categories

| Category | Description |
|---|---|
| `no_metadata` | File yielded no usable title or author; a minimal stub record was created |
| `match_conflict` | Could not confidently match to an existing book; a new record was created |
| `amazon_captcha` | Amazon returned a CAPTCHA or bot-detection page |
| `amazon_error` | Amazon scrape failed (timeout, parse error, etc.) |
| `extract_error` | File could not be downloaded or its metadata could not be parsed |

### Schema

```
bootstrap_issues
  issue_id        bigint PK
  s3_object_key   varchar       -- file that triggered the issue
  book_id         bigint FK     -- associated book record, if one exists (nullable)
  category        varchar       -- one of the categories above
  detail          text          -- human-readable description
  created_at      timestamptz
  resolved        boolean
  resolved_at     timestamptz   -- nullable
  resolution_note text          -- nullable; operator fills in after fixing
```

The `resolved` flag is set manually (via psql or a future UI) after the operator addresses the
issue.

---

## Progress Monitoring

Because the run processes ~2800 files including Amazon scraping with mandatory delays, it will run
for a long time.  All output goes to stdout so it is readable via `docker logs -f`.

A structured line is emitted after each file is processed:

```
[2026-03-03 10:23:45] Progress: 1312/2788 (47%) | elapsed: 4:22 | eta: 5:01 | created "11/22/63" by Stephen King
```

Amazon scraping events are logged separately:

```
[2026-03-03 10:24:03] Amazon 1045/1312: scraping B004Q7CIFI
[2026-03-03 10:24:19] Amazon OK B004Q7CIFI — rating=4.6, pages=1121, series="The Dark Tower"
```

---

## Post-Run Report

After the run completes, a summary is printed to stdout:

- S3 objects: discovered / skipped (wrong type) / processed
- Books: created / matched to existing
- Authors: created / matched to existing
- Series created
- Amazon: succeeded / skipped (no ASIN) / failed
- Issues by category
- Full list of unresolved issues with S3 key, category, and detail

---

## Incremental Test Plan

The full bucket contains ~2800 files and will take hours to process.  The tool should be validated
incrementally, starting from the smallest possible scope and expanding only after each stage is
confirmed correct.  Each stage uses `DRY_RUN=1` first, then a live run against a clean database.

### Stage 1 — Single EPUB, no ASIN

Pick one EPUB from the bucket that is known to have no ASIN (e.g., a Wodehouse public domain
file).  Confirm:
- Correct `books`, `authors`, `book_authors`, `book_tags`, `ebook_files` rows created
- `bootstrap_progress` row written with outcome `created`
- No Amazon scraping attempted
- Progress log line emitted in the correct format
- Re-run produces no new rows

### Stage 2 — Single EPUB with ASIN

Pick one EPUB known to have an embedded ASIN (e.g., a Stephen King novel).  Confirm everything
from Stage 1, plus:
- `books.asin` and `ebook_files.asin` populated from file metadata
- `amazon_metadata` row created with correct fields
- Series record and `book_series` row created if Amazon returns series info
- Amazon rate-limiting delay observed between the file and Amazon requests

### Stage 3 — Matching EPUB + M4B pair

Pick one book that exists as both an EPUB and an M4B in the bucket.  Process the EPUB first, then
the M4B.  Confirm:
- Single `books` record shared by both files
- One `ebook_files` row and one `audiobook_files` row, both referencing the same `book_id`
- `duration_seconds` populated on the audiobook row
- No duplicate `authors` records

### Stage 4 — Small mixed batch (~20 files)

Run against a manually curated list of ~20 S3 keys covering:
- EPUBs with and without ASINs
- At least one EPUB/M4B matched pair
- At least one file with sparse metadata (expected `no_metadata` issue)
- At least two books by the same author with name variants

Confirm:
- Author deduplication works across multiple files
- Fuzzy title matching correctly links the EPUB/M4B pair
- Issues are recorded in `bootstrap_issues` for expected problem files
- Post-run report counts match actual DB rows

### Stage 5 — Interrupt and resume

Using the Stage 4 batch, simulate an interruption at various points:
- Kill after ~5 files processed; verify restart skips them and completes cleanly
- Kill during an Amazon scrape; verify catch-up queue picks up the affected book on restart

### Stage 6 — Full bucket, dry run

Run `DRY_RUN=1` against the full bucket.  Review the log output for:
- Unexpectedly high `match_conflict` or `no_metadata` counts (suggests tuning is needed)
- Author name variants that were not deduplicated (adjust `FUZZY_MATCH_THRESHOLD` if needed)
- Any unexpected errors

Adjust configuration as needed before proceeding to Stage 7.

### Stage 7 — Full bucket, live run

Run against the full bucket with a clean database.  Monitor via `docker logs -f`.  After
completion:
- Verify post-run report totals look plausible (book count, author count, series count)
- Review all `bootstrap_issues` entries and resolve or note each one
- Spot-check a sample of books in the database against known correct metadata

---

## Configuration

| Variable | Description | Default |
|---|---|---|
| `S3_BUCKET` | Bucket name | required |
| `S3_ENDPOINT` | S3-compatible endpoint URL | required |
| `S3_REGION` | Region | `us-east-1` |
| `AWS_ACCESS_KEY_ID` | S3 access key | required |
| `AWS_SECRET_ACCESS_KEY` | S3 secret key | required |
| `DATABASE_URL` | Postgres connection string | required |
| `CACHE_DIR` | Local cache directory for downloaded files | `/tmp/bootstrap_cache` |
| `AMAZON_DELAY_MIN` | Min seconds between Amazon requests | `5` |
| `AMAZON_DELAY_MAX` | Max seconds between Amazon requests | `15` |
| `FUZZY_MATCH_THRESHOLD` | `rapidfuzz` score (0–100) for auto-accepting matches | `85` |
| `DRY_RUN` | Log planned actions without writing to the database | unset |
| `LIMIT_KEYS` | Comma-separated list of S3 keys to process; all others are skipped. Used for incremental testing (Stages 1–4). | unset |
| `MAX_FILES` | Stop after processing this many files. Used for smoke-testing the full pipeline on a small slice. | unset |

In dry-run mode no DB writes are made, including to `bootstrap_progress` and `bootstrap_issues`.
Progress and match decisions are still logged so the output can be reviewed before a live run.

---

## Implementation Notes

- Written in Python; lives in `bootstrap/` with its own `requirements.txt` and `Dockerfile`.
- Shared logic (S3 client, EPUB extraction, Amazon scraping) is extracted from `service/app.py`
  into a common library rather than duplicated.
- Run as a one-shot Docker container via a dedicated `docker/docker-compose.bootstrap.yml`.
- Uses `network_mode: "container:gluetun"` to route traffic through the already-running gluetun
  container from the main `docker/docker-compose.yml`.  Gluetun must be running before invoking
  the bootstrap compose.

### `bootstrap_progress` Schema

```
bootstrap_progress
  s3_object_key   varchar PK    -- natural key; uniqueness enforces idempotency
  processed_at    timestamptz
  outcome         varchar       -- 'created', 'matched', or 'error'
  book_id         bigint FK     -- nullable
```
