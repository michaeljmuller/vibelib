---
id: TASK-13
title: scrape book metadata from amazon
status: Testing
assignee:
  - '@claude'
created_date: '2026-02-26 23:54'
updated_date: '2026-02-27 00:21'
labels: []
dependencies: []
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
when viewing a book's metadata, try to scrape the metadata from amazon.com.  use the ASIN if you have it.  make sure you're getting the information for the kindle book.

some information to collect: 

 - # of pages
 - publication date
 - what series it's part of
 - # of reviews
 - avg rating
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 When viewing an ebook with an ASIN in its metadata, Amazon metadata is fetched and displayed on the details page
- [x] #2 Displayed Amazon data includes: avg rating, number of ratings, page count, publication date, and series (when available)
- [x] #3 If Amazon scraping fails or no ASIN is available, the page still loads with EPUB metadata intact
- [x] #4 Scraped data targets the Kindle edition (via the ASIN from the EPUB)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add playwright to service/requirements.txt
2. Update docker/Dockerfile.service to run "playwright install chromium --with-deps" after pip install (installs Chromium + OS deps)
3. Add scrape_amazon_metadata(asin) to service/app.py using Playwright sync API: launch headless Chromium with realistic UA/viewport, navigate to https://www.amazon.com/dp/{asin}, wait for network idle, parse DOM for rating, num_ratings, pages, publication_date, series; detect and bail on CAPTCHA pages; cache results by ASIN (~1h TTL)
4. Update get_ebook_metadata() to call scraper with ASIN from EPUB identifiers and merge result into response under "amazon" key; failure is logged and silently swallowed
5. Update ebook_details.html to show Amazon section with scraped fields
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
- ASIN stored as mobi-asin in EPUB identifiers (not asin); fixed to check both
- Added ASIN format validation (B + 9 alphanumeric) to filter out UUIDs stored in mobi-asin field
- Tested B004Q7CIFI (11/22/63): rating=4.6, 14275 ratings, 1121 pages, pub date Nov 8 2011
- Dead ASINs return all-null gracefully (no exception)
- Used Playwright base image mcr.microsoft.com/playwright/python:v1.50.0-jammy to avoid Debian Trixie font package conflicts with --with-deps
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added Amazon metadata scraping to the existing GET /api/ebooks/<s3_key>/metadata endpoint.

Changes:
- service/requirements.txt: added playwright==1.50.0
- docker/Dockerfile.service: switched base image to mcr.microsoft.com/playwright/python:v1.50.0-jammy (includes Chromium; avoids font package conflicts on Debian Trixie)
- service/app.py: added scrape_amazon_metadata(asin) using Playwright sync API with headless Chromium; in-memory cache by ASIN (1h TTL); CAPTCHA detection; parses rating, num_ratings, pages, publication_date, series from product page DOM
- service/app.py: updated get_ebook_metadata() to extract ASIN from mobi-asin or asin identifier fields, validate it looks like a real ASIN (B + 9 alphanumeric), scrape Amazon, and merge result under "amazon" key; failure is logged and silently swallowed
- ui/templates/ebook_details.html: added "From Amazon" section displaying all five fields plus a link to the Amazon product page

Tested with 11/22/63 (B004Q7CIFI): rating=4.6, 14,275 ratings, 1,121 pages, Nov 8 2011.
<!-- SECTION:FINAL_SUMMARY:END -->
