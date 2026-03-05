---
id: TASK-22
title: EPUB ingestion pipeline
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
Implement the full ingestion pipeline for a single EPUB file. This is the core of the bootstrap tool.\n\nFor a given S3 key:\n1. Download the file via the ETag cache.\n2. Extract metadata using common/ functions (title, authors, language, pub date, ISBN, ASIN, subjects).\n3. Attempt to match against existing books records using the fuzzy matching functions.\n4. In a single DB transaction: create or reuse the books record; create or match authors records; insert book_authors and book_tags; insert the ebook_files record; insert the bootstrap_progress row with outcome 'created' or 'matched'.\n\nIf metadata extraction fails, record an extract_error issue and skip.\nIf matching is ambiguous (multiple candidates above threshold), record a match_conflict issue and create a new record.\nIf no title or author is found, record a no_metadata issue and create a stub books record using the S3 key as a fallback identifier.\n\nAcquisition date is set from the S3 object's last_modified date.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Processing an EPUB creates correct books, authors, book_authors, book_tags, ebook_files, and bootstrap_progress rows
- [x] #2 Re-processing the same EPUB (idempotency) creates no new rows
- [x] #3 A corrupt or unreadable EPUB records an extract_error in bootstrap_issues and is otherwise skipped
- [x] #4 All DB writes for one file succeed or fail together (transaction rollback tested by simulating a mid-write failure)
- [x] #5 ebook_files.asin is populated from the EPUB's embedded ASIN identifier; books.asin is set to the same value if books.asin is currently null
- [x] #6 An EPUB with no title uses the filename (minus extension) as the title, records a no_metadata issue, and the stub books record is still created correctly
<!-- AC:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Implemented the full EPUB ingestion pipeline in bootstrap/ingest_epub.py, with supporting modules bootstrap/run_context.py and bootstrap/db_helpers.py.

Changes:
- bootstrap/run_context.py: RunContext dataclass holding in-memory book/author cache + counters; load_context() pre-populates from DB at startup.
- bootstrap/db_helpers.py: All DB helper functions (check_already_processed, find_matching_book_candidates, find_or_create_author, create_book, update_book_asin, insert_book_author, insert_book_tag, insert_ebook_file, record_progress, record_issue).
- bootstrap/ingest_epub.py: process_epub() — idempotency check, ETag-cached download, ebooklib metadata extraction, fuzzy title matching, single-transaction writes (books, authors, book_authors, book_tags, ebook_files, bootstrap_progress). Records extract_error, match_conflict, and no_metadata issues as appropriate.
- bootstrap/bootstrap.py: Implemented run() using load_context + list_s3_objects loop calling process_epub/process_m4b.
- bootstrap/tests/test_ingest_epub.py: 8 unit tests covering all 6 ACs (all pass).
- bootstrap/conftest.py: Added project root to sys.path so common/ is importable in tests.
<!-- SECTION:FINAL_SUMMARY:END -->
