---
id: TASK-19
title: Extract shared library from service
status: To Do
assignee:
  - '@claude'
created_date: '2026-03-03 21:44'
labels:
  - bootstrap
milestone: m-1
dependencies:
  - TASK-17
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The service/app.py currently contains all S3, EPUB extraction, ASIN extraction, and Amazon scraping logic in one file. The bootstrap tool needs the same logic. Rather than duplicating it, extract these functions into a new common/ Python package at the project root that both service and bootstrap import from.\n\nFunctions to move: get_s3_client(), get_cached_epub(), extract_epub_metadata(), extract_isbn_from_content(), get_epub_asin(), format_isbn(), extract_epub_cover(), scrape_amazon_metadata().\n\nThe service/app.py should be updated to import from common/ and all existing behavior must remain unchanged.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 common/ package exists and is importable
- [ ] #2 All listed functions are in common/ and removed from service/app.py (service imports them from common/)
- [ ] #3 The service layer still functions correctly after the refactor (existing endpoints return the same responses)
- [ ] #4 bootstrap.py can import from common/ without errors
<!-- AC:END -->
