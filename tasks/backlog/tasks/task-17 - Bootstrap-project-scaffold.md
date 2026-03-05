---
id: TASK-17
title: Bootstrap project scaffold
status: To Do
assignee:
  - '@claude'
created_date: '2026-03-03 21:44'
updated_date: '2026-03-03 22:10'
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
- [ ] #1 bootstrap/ directory exists with requirements.txt, Dockerfile, and bootstrap.py
- [ ] #2 Dockerfile builds successfully
- [ ] #3 bootstrap.py exits with a clear error message if any required env var is absent
- [ ] #4 bootstrap.py verifies Postgres and S3 connectivity on startup and logs the result of each check
- [ ] #5 LIMIT_KEYS and MAX_FILES env vars are recognised and correctly restrict which S3 objects are processed
<!-- AC:END -->
