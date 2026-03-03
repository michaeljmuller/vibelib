---
id: TASK-21
title: S3 enumeration and ETag file cache
status: To Do
assignee:
  - '@claude'
created_date: '2026-03-03 21:45'
labels:
  - bootstrap
dependencies:
  - TASK-17
  - TASK-19
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Implement the S3 object listing and local caching layer used by all subsequent ingestion tasks.\n\nlist_s3_objects(): paginates through the entire bucket using list_objects_v2, returning an iterator of (key, size, last_modified, etag) tuples. Filters to .epub and .m4b only; silently skips everything else.\n\nget_cached_file(s3_key, etag): returns a local path to the file, downloading from S3 only if the file is not already cached or the ETag has changed. Cache lives under CACHE_DIR, keyed by a hash of the S3 key (reusing the pattern from service/app.py).\n\nTwo-pass ordering: the iterator yields all .epub objects before any .m4b objects so that richer EPUB metadata creates canonical book records first.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 list_s3_objects() correctly paginates a bucket with more than 1000 objects
- [ ] #2 Non-.epub and non-.m4b keys are silently skipped and never returned
- [ ] #3 .epub keys are all returned before .m4b keys
- [ ] #4 get_cached_file() returns a cached copy on second call without making a new S3 request
- [ ] #5 get_cached_file() re-downloads when the ETag differs from the cached value
<!-- AC:END -->
