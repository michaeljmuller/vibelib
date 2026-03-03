---
id: TASK-25
title: Progress logging and post-run report
status: To Do
assignee:
  - '@claude'
created_date: '2026-03-03 21:46'
labels:
  - bootstrap
dependencies:
  - TASK-22
  - TASK-23
  - TASK-24
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Implement the operator-facing output: structured progress log lines and a final summary report.\n\nProgress lines are emitted to stdout after each file is processed. Format:\n[TIMESTAMP] Progress: N/TOTAL (PCT%) | elapsed: H:MM | eta: H:MM | OUTCOME "TITLE" by AUTHOR\n\nAmazon events are logged separately:\n[TIMESTAMP] Amazon N/TOTAL_WITH_ASIN: scraping ASIN\n[TIMESTAMP] Amazon OK ASIN -- rating=X, pages=N, series="NAME"\n[TIMESTAMP] Amazon FAILED ASIN -- REASON\n\nETA is calculated from the current processing rate (objects per second) since startup.\n\nPost-run report is printed to stdout after the run completes. It includes:\n- S3 objects: discovered / skipped (wrong type) / processed\n- Books: created / matched\n- Authors: created / matched\n- Series created\n- Amazon: succeeded / skipped (no ASIN) / failed\n- Issue counts by category\n- Full list of unresolved issues with S3 key, category, and detail
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A progress line is emitted for every processed file in the specified format
- [ ] #2 Amazon scrape start and result are each logged on a separate line
- [ ] #3 ETA updates correctly as the run progresses
- [ ] #4 Post-run report counts match the actual rows in the relevant DB tables
- [ ] #5 All output goes to stdout (not stderr)
<!-- AC:END -->
