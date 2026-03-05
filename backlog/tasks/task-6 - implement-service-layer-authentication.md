---
id: TASK-6
title: implement service layer authentication
status: Done
assignee:
  - claude
created_date: '2026-01-26 03:17'
updated_date: '2026-01-26 04:16'
labels: []
dependencies:
  - TASK-7
ordinal: 6000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
refer to documentation for auth notes
<!-- SECTION:DESCRIPTION:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add requests to service/requirements.txt (for GitHub API calls)
2. Add auth decorator that validates Bearer token via GitHub API
3. Add simple token cache to avoid hitting GitHub on every request
4. Apply auth to /api/objects endpoint (not /api/health)
5. Rebuild and test
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Blocked: implement UI auth first, service layer will validate JWTs from UI/iOS clients.

Implemented GitHub token validation with 5-minute cache.
Added documentation/SERVICE-ENDPOINT-TESTING.md with Device Flow instructions.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Implemented service layer authentication via GitHub token validation.

Changes:
- service/app.py: Added auth_required decorator, validates tokens via GitHub API with 5-min cache
- service/requirements.txt: Added requests
- documentation/SERVICE-ENDPOINT-TESTING.md: Device Flow testing instructions

Behavior:
- /api/objects requires valid GitHub token in Authorization header
- /api/health remains unauthenticated
- Invalid/missing tokens return 401
<!-- SECTION:FINAL_SUMMARY:END -->
