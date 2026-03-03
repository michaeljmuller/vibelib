---
id: TASK-22
title: EPUB ingestion pipeline
status: To Do
assignee:
  - '@claude'
created_date: '2026-03-03 21:45'
updated_date: '2026-03-03 22:10'
labels:
  - bootstrap
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
- [ ] #1 Processing an EPUB creates correct books, authors, book_authors, book_tags, ebook_files, and bootstrap_progress rows
- [ ] #2 Re-processing the same EPUB (idempotency) creates no new rows
- [ ] #3 A corrupt or unreadable EPUB records an extract_error in bootstrap_issues and is otherwise skipped
- [ ] #4 All DB writes for one file succeed or fail together (transaction rollback tested by simulating a mid-write failure)
- [ ] #5 ebook_files.asin is populated from the EPUB's embedded ASIN identifier; books.asin is set to the same value if books.asin is currently null
- [ ] #6 An EPUB with no title uses the filename (minus extension) as the title, records a no_metadata issue, and the stub books record is still created correctly
<!-- AC:END -->
