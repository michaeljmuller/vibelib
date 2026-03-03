---
id: TASK-18
title: Add bootstrap tracking tables to schema
status: To Do
assignee:
  - '@claude'
created_date: '2026-03-03 21:44'
updated_date: '2026-03-03 22:07'
labels:
  - bootstrap
dependencies: []
documentation:
  - documentation/BOOTSTRAP-TOOL-SPEC.md
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add the two bootstrap-specific tables to sql/schema.sql. Neither table is part of the main library data model; they exist only to support the bootstrap tool's idempotency and issue tracking.

bootstrap_progress tracks every S3 object that has been fully processed (committed). Its primary key on s3_object_key enforces idempotency.

bootstrap_issues records problems encountered during the run so the operator can review and resolve them afterward. Columns: issue_id (bigint PK), s3_object_key (varchar), book_id (bigint FK nullable), category (varchar), detail (text), created_at (timestamptz), resolved (boolean default false), resolved_at (timestamptz nullable), resolution_note (text nullable).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 bootstrap_progress table added to sql/schema.sql with columns: s3_object_key (varchar PK), processed_at (timestamptz), outcome (varchar), book_id (bigint FK nullable)
- [ ] #2 bootstrap_issues table added to sql/schema.sql with all specified columns
- [ ] #3 Schema applies cleanly against a fresh Postgres instance with no errors
<!-- AC:END -->
