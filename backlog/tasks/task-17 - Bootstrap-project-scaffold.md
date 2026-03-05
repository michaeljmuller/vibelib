---
id: TASK-17
title: Bootstrap project scaffold
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
Create the bootstrap/ directory with all project scaffolding needed before any logic is implemented. Includes requirements.txt (boto3, ebooklib, mutagen, psycopg2-binary, rapidfuzz, playwright), a Dockerfile based on the same Playwright base image used by the service, and a bootstrap.py entry point that reads all required env vars and fails fast with a clear error if any are missing, then verifies connectivity to both Postgres and S3 before proceeding.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 bootstrap/ directory exists with requirements.txt, Dockerfile, and bootstrap.py
- [x] #2 Dockerfile builds successfully
- [x] #3 bootstrap.py exits with a clear error message if any required env var is absent
- [x] #4 bootstrap.py verifies Postgres and S3 connectivity on startup and logs the result of each check
- [x] #5 LIMIT_KEYS and MAX_FILES env vars are recognised and correctly restrict which S3 objects are processed
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Create bootstrap/requirements.txt with the six required packages
2. Create bootstrap/Dockerfile based on the same mcr.microsoft.com/playwright/python:v1.50.0-jammy image
3. Create bootstrap/bootstrap.py: load and validate required env vars (fail fast), parse optional vars, check Postgres connectivity, check S3 connectivity, parse LIMIT_KEYS and MAX_FILES, log startup summary
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Created bootstrap/ scaffold: requirements.txt (boto3, ebooklib, mutagen, psycopg2-binary, rapidfuzz, playwright all pinned to match service versions), Dockerfile based on mcr.microsoft.com/playwright/python:v1.50.0-jammy (same image as service), and bootstrap.py entry point. bootstrap.py validates all required env vars at startup and exits with a per-var error message for each missing one. Checks Postgres and S3 connectivity and logs OK/FAILED for each. Parses LIMIT_KEYS (comma-separated set) and MAX_FILES (validated positive integer) and logs them at startup. Dockerfile build verified successfully.
<!-- SECTION:FINAL_SUMMARY:END -->
