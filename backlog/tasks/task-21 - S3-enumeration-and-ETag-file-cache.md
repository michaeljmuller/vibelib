---
id: TASK-21
title: S3 enumeration and ETag file cache
status: Testing
assignee:
  - '@claude'
created_date: '2026-03-03 21:45'
updated_date: '2026-03-05 01:35'
labels:
  - bootstrap
milestone: m-1
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
- [x] #1 list_s3_objects() correctly paginates a bucket with more than 1000 objects
- [x] #2 Non-.epub and non-.m4b keys are silently skipped and never returned
- [x] #3 .epub keys are all returned before .m4b keys
- [x] #4 get_cached_file() returns a cached copy on second call without making a new S3 request
- [x] #5 get_cached_file() re-downloads when the ETag differs from the cached value
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Implement bootstrap/s3_cache.py with list_s3_objects() (paginated, two-pass EPUB-first ordering) and get_cached_file() (ETag-keyed local cache under CACHE_DIR)
2. Write unit tests covering pagination, extension filtering, two-pass ordering, cache hit, and cache miss on ETag change
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Implemented bootstrap/s3_cache.py with list_s3_objects() (paginated via list_objects_v2, filters to .epub/.m4b only, collects all EPUBs then all M4Bs for two-pass ordering, supports LIMIT_KEYS and MAX_FILES) and get_cached_file() (ETag-keyed cache under CACHE_DIR, downloads only on cache miss or ETag change). Unit tests in bootstrap/tests/test_s3_cache.py cover extension filtering, two-pass ordering, multi-page pagination, LIMIT_KEYS, MAX_FILES, cache hit, cache miss, and ETag-triggered re-download (10 tests, all pass). Added bootstrap/conftest.py to prevent bootstrap.py from interfering with pytest collection.
<!-- SECTION:FINAL_SUMMARY:END -->
