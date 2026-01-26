---
id: TASK-8
title: Add Google OAuth provider
status: To Do
assignee: []
created_date: '2026-01-26 03:52'
updated_date: '2026-01-26 03:54'
labels: []
dependencies:
  - TASK-7
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add Google as an OAuth provider option for user authentication.

## Setup Instructions (Google Cloud Console)

1. Go to https://console.cloud.google.com/

2. Create a project (or select existing)
   - Click project dropdown → "New Project"
   - Name it (e.g., "Library")

3. Enable the Google+ API
   - "APIs & Services" → "Library"
   - Search "Google+ API" → Enable

4. Configure OAuth consent screen
   - "APIs & Services" → "OAuth consent screen"
   - User Type: External
   - App name, support email
   - Save

5. Create OAuth credentials
   - "APIs & Services" → "Credentials"
   - "Create Credentials" → "OAuth client ID"
   - Application type: "Web application"
   - Authorized redirect URIs: http://localhost:8080/callback/google
   - Copy Client ID and Client Secret

6. Add test users (while in Testing mode)
   - OAuth consent screen → Test users
   - Add Gmail addresses of allowed users

7. Add to docker/.env:
   GOOGLE_CLIENT_ID=...
   GOOGLE_CLIENT_SECRET=...
<!-- SECTION:DESCRIPTION:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Timeframes:
- Setup: ~10 minutes
- Testing mode: immediate (add users to test list)
- Published/Verified: days to weeks (Google review process)
<!-- SECTION:NOTES:END -->
