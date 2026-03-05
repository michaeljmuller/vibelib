---
id: TASK-24
title: 'Amazon scraping with rate limiting, series handling, and catch-up queue'
status: To Do
assignee:
  - '@claude'
created_date: '2026-03-03 21:45'
updated_date: '2026-03-03 22:10'
labels:
  - bootstrap
milestone: m-1
dependencies:
  - TASK-18
  - TASK-19
  - TASK-20
  - TASK-22
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Implement Amazon enrichment, which runs inline after a book record is created.\n\nFor each book with a valid ASIN:\n1. Check amazon_metadata: if a row already exists for this ASIN, skip.\n2. Sleep a random AMAZON_DELAY_MIN to AMAZON_DELAY_MAX seconds.\n3. Call scrape_amazon_metadata() from common/.\n4. On success: insert amazon_metadata row (asin, sample_time, rating, num_ratings, publication_date, page_count).\n5. If Amazon returns series info: normalize the series name, fuzzy-match against existing series records, create a new series record if needed, insert book_series.\n6. On CAPTCHA/bot-detection: record an amazon_captcha issue, skip.\n7. On any other error: record an amazon_error issue, skip.\n\nCatch-up queue: on startup, before processing S3 objects, query for books where books.asin IS NOT NULL and no amazon_metadata row exists. These are added to a queue processed after the main S3 pass.\n\nAll Amazon requests exit through the gluetun VPN network.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A book with a valid ASIN gets an amazon_metadata row after processing
- [ ] #2 A book whose ASIN is already in amazon_metadata is not re-scraped
- [ ] #3 A randomized delay is inserted between every Amazon request
- [ ] #4 A CAPTCHA response records an amazon_captcha issue and does not abort the run
- [ ] #5 Series data from Amazon creates a series record and a book_series record with correct sort_order
- [ ] #6 Fuzzy series matching reuses an existing series record rather than creating a duplicate
- [ ] #7 On startup, books with an ASIN but no amazon_metadata row are added to the catch-up queue and scraped
- [ ] #8 The Amazon series widget text is inspected on real responses to determine whether the book number is embedded in it; the scraper is updated to extract the numeric book number (for sort_order) and display number (for display_number) if present, and to store just the series name if no number can be parsed
<!-- AC:END -->
