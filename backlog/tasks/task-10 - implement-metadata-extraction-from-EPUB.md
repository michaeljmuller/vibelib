---
id: TASK-10
title: implement metadata extraction from EPUB
status: Done
assignee:
  - '@claude'
created_date: '2026-01-26 23:33'
updated_date: '2026-01-27 00:16'
labels: []
dependencies: []
ordinal: 7000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
in the user interface, provide a hyperlink on every epub

that hyperlink should bring you to a page that displays all the metadata for that epub.
this page should also display the book's cover image.  the cover image can be scaled, but should retain its aspect ratio.

implement this by providing an endpoint in the service that returns the metadata and cover image.  this can be two endpoints, one for the metadata and one for the cover image.

for efficiency's sake, the service should cache the expanded EPUB on the file system so that repeated calls to the endpoint don't force repeated downloading from S3 and unzipping.  

i think there's an "Etag" checksum associated with the s3 objects that we can use to see if we should re-download the object.
<!-- SECTION:DESCRIPTION:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add ebooklib dependency to service/requirements.txt for EPUB parsing

2. Service Layer - Add caching infrastructure:
   - Create cache directory structure
   - Implement get_or_download_epub() with ETag-based cache validation
   - Store ETags alongside cached files to detect changes

3. Service Layer - Add metadata extraction endpoint:
   - GET /api/ebooks/<path:s3_key>/metadata
   - Parse EPUB container.xml to find package.opf
   - Extract title, authors, description, language, publisher, date, ISBN, etc.
   - Return JSON response

4. Service Layer - Add cover image endpoint:
   - GET /api/ebooks/<path:s3_key>/cover
   - Find cover image reference in package.opf
   - Return image with proper content-type header

5. UI Layer - Add ebook details route and template:
   - New route: /ebook/<path:s3_key>
   - New template: templates/ebook_details.html
   - Display metadata in readable format
   - Display cover image (scaled, aspect ratio preserved)

6. UI Layer - Update objects.html template:
   - Add hyperlinks on .epub filenames linking to /ebook/<key>
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- Added ebooklib dependency to service
- Implemented ETag-based caching for EPUB files
- Created metadata and cover extraction endpoints in service
- Added ebook details page with cover display in UI
- Updated objects list to hyperlink EPUB files
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Implemented EPUB metadata extraction and display feature.

Service layer changes (service/app.py):
- Added ebooklib dependency for EPUB parsing
- Implemented ETag-based caching system that stores downloaded EPUBs on filesystem
- GET /api/ebooks/<s3_key>/metadata - returns JSON with title, authors, description, language, publisher, date, identifiers (ISBN etc), subjects, and rights
- GET /api/ebooks/<s3_key>/cover - returns cover image with proper content-type

UI layer changes:
- Added /ebook/<s3_key> route displaying metadata and cover image
- Added /ebook/<s3_key>/cover proxy route for authenticated cover image access
- Created ebook_details.html template with responsive layout showing cover (scaled with aspect ratio preserved) alongside metadata
- Updated objects.html to hyperlink .epub files to their detail pages

Caching mechanism uses S3 HEAD requests to check ETag and only re-downloads when the object has changed.
<!-- SECTION:FINAL_SUMMARY:END -->
