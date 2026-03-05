---
id: TASK-23
title: M4B ingestion pipeline
status: Testing
assignee:
  - '@claude'
created_date: '2026-03-03 21:45'
updated_date: '2026-03-05 15:47'
labels:
  - bootstrap
milestone: m-1
dependencies:
  - TASK-18
  - TASK-19
  - TASK-20
  - TASK-21
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Implement the ingestion pipeline for M4B audiobook files, following the same pattern as EPUB ingestion.\n\nFor a given M4B S3 key:\n1. Download the file via the ETag cache.\n2. Extract metadata using mutagen: title (\xa9nam), artist (\xa9ART), year (\xa9day), and duration_seconds from the audio stream info.\n3. Attempt to match against existing books records (title + author) using fuzzy matching. Because the EPUB pass runs first, most M4Bs should match an existing record.\n4. In a single DB transaction: create or reuse the books record; create or match authors; insert audiobook_files (including duration_seconds and file_size_bytes from S3 metadata); insert bootstrap_progress.\n\nNote: M4B files may carry little or no tag metadata. If no title is available, fall back to the filename (minus extension) as the title.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Processing an M4B that matches an existing EPUB book results in one books record and two file records (ebook_files + audiobook_files)
- [x] #2 Processing an M4B with no existing match creates a new books record
- [x] #3 duration_seconds is populated from mutagen stream info when available
- [x] #4 file_size_bytes is populated from S3 object metadata (not the downloaded file)
- [x] #5 Re-processing the same M4B creates no new rows
- [x] #6 An M4B with no author metadata records a no_metadata issue; an M4B with no title falls back to the filename (minus extension) and does not record an issue
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Implemented the M4B audiobook ingestion pipeline in bootstrap/ingest_m4b.py, following the same pattern as EPUB ingestion.

Changes:
- bootstrap/ingest_m4b.py: process_m4b() — idempotency check, ETag-cached download, mutagen MP4 tag extraction (©nam title, ©ART artist, ©day year, stream duration), fuzzy title matching against existing books, single-transaction writes (books, authors, book_authors, audiobook_files, bootstrap_progress). M4B-specific: missing title → silent filename fallback (no issue); missing author → no_metadata issue.
- bootstrap/tests/test_ingest_m4b.py: 9 unit tests covering all 6 ACs (all pass).

All 26 bootstrap tests pass.
<!-- SECTION:FINAL_SUMMARY:END -->
