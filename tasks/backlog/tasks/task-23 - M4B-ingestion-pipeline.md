---
id: TASK-23
title: M4B ingestion pipeline
status: To Do
assignee:
  - '@claude'
created_date: '2026-03-03 21:45'
updated_date: '2026-03-03 22:07'
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
- [ ] #1 Processing an M4B that matches an existing EPUB book results in one books record and two file records (ebook_files + audiobook_files)
- [ ] #2 Processing an M4B with no existing match creates a new books record
- [ ] #3 duration_seconds is populated from mutagen stream info when available
- [ ] #4 file_size_bytes is populated from S3 object metadata (not the downloaded file)
- [ ] #5 Re-processing the same M4B creates no new rows
- [ ] #6 An M4B with no author metadata records a no_metadata issue; an M4B with no title falls back to the filename (minus extension) and does not record an issue
<!-- AC:END -->
