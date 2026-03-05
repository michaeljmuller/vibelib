---
id: TASK-18
title: Add bootstrap tracking tables to schema
status: Testing
assignee:
  - '@claude'
created_date: '2026-03-03 21:44'
updated_date: '2026-03-05 01:24'
labels:
  - bootstrap
milestone: m-1
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
- [x] #1 bootstrap_progress table added to sql/schema.sql with columns: s3_object_key (varchar PK), processed_at (timestamptz), outcome (varchar), book_id (bigint FK nullable)
- [x] #2 bootstrap_issues table added to sql/schema.sql with all specified columns
- [x] #3 Schema applies cleanly against a fresh Postgres instance with no errors
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add bootstrap_progress table to sql/schema.sql (after existing tables, before indexes section)
2. Add bootstrap_issues table to sql/schema.sql
3. Add indexes for both tables
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added bootstrap_progress and bootstrap_issues tables to sql/schema.sql with appropriate CHECK constraints (outcome values, category values) and FK references to books with ON DELETE SET NULL. Added 5 indexes covering book_id lookups, s3_object_key, category, and unresolved issues. Schema verified against a fresh Postgres 16 instance — all 16 tables and 54 indexes/triggers created with no errors.
<!-- SECTION:FINAL_SUMMARY:END -->
