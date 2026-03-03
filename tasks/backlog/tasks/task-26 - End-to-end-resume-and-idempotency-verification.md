---
id: TASK-26
title: End-to-end resume and idempotency verification
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
  - TASK-25
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Verify that the tool handles interruption and restart correctly across the two main failure scenarios.\n\nScenario 1 - interrupted during file ingestion:\nProcess a batch of files. Simulate an abrupt interruption (raise an exception mid-run). Restart the tool. Verify that already-processed files are skipped, no duplicate records exist in any table, and the final DB state is identical to a clean run.\n\nScenario 2 - interrupted during Amazon scraping:\nProcess a batch of files through to completion including bootstrap_progress writes. Delete a subset of amazon_metadata rows to simulate an interruption after book creation but before Amazon scraping completed. Restart the tool. Verify that the catch-up queue picks up the affected books, scrapes Amazon for them, and produces the correct amazon_metadata rows.\n\nScenario 3 - full re-run on a complete database:\nRun the tool to completion. Run it again. Verify zero new rows are created in any table and no errors are logged.\n\nThese tests require a running Postgres instance and either real or mocked S3 and Amazon endpoints.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Scenario 1: restart after mid-run interruption produces the same final DB state as a clean run, with no duplicate records
- [ ] #2 Scenario 2: restart after Amazon-scraping interruption correctly scrapes all books that were missing amazon_metadata
- [ ] #3 Scenario 3: a second full run on a complete database creates no new rows in any table
<!-- AC:END -->
