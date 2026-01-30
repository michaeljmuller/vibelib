---
id: TASK-11
title: ISBN extraction from EPUB
status: Done
assignee:
  - '@claude'
created_date: '2026-01-27 00:24'
updated_date: '2026-01-30 03:23'
labels: []
dependencies: []
ordinal: 8000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
when extracting metadata, parse the expanded EPUB to see if you can find the copyright-page section(s).  from there, see if you can parse out the ISBN.  add this to the info displayed on the metadata page.

if there are multiple ISBNs, use the one for the ebook.
<!-- SECTION:DESCRIPTION:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Added extract_isbn_from_content() function to parse copyright pages
2. Extract ISBNs using regex, prefer ebook-labeled ISBN
3. Fall back to DC metadata ISBN if no content ISBN found
4. Display ISBN prominently in ebook_details.html template
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added ISBN extraction from EPUB copyright page content.

Service layer (service/app.py):
- Added extract_isbn_from_content() that finds copyright pages and extracts ISBNs
- Uses regex to match ISBN-10 and ISBN-13 formats
- When multiple ISBNs found, prefers one labeled "ebook", "epub", "digital", or "electronic"
- Falls back to ISBN from DC metadata if no content ISBN found
- ISBN returned as single "isbn" field in metadata response

UI layer (ui/templates/ebook_details.html):
- Added ISBN display below authors, styled with monospace font

Tests:
- Multiple ISBNs with ebook label: correctly selects ebook ISBN
- Multiple ISBNs without label: returns first ISBN
- No copyright page: falls back to DC metadata ISBN
<!-- SECTION:FINAL_SUMMARY:END -->
