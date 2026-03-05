---
id: TASK-7
title: implement UI layer authentication
status: Done
assignee:
  - claude
created_date: '2026-01-26 03:18'
updated_date: '2026-01-26 04:09'
labels: []
dependencies: []
ordinal: 5000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
refer to docs for auth notes
<!-- SECTION:DESCRIPTION:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add authlib to ui/requirements.txt for OAuth support
2. Add GitHub OAuth config env vars to docker-compose
3. Implement /login/github route - redirects to GitHub OAuth
4. Implement /callback/github route - exchanges code for token, stores user in session
5. Implement /logout route - clears session
6. Update service calls to include token in Authorization header
7. Add login requirement to /objects route
8. Update landing page with GitHub login button

Note: Architecture supports multiple providers; Google/Apple added via tasks 8/9.
<!-- SECTION:PLAN:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Implemented GitHub OAuth authentication for the UI layer.

Changes:
- ui/app.py: Added authlib OAuth, login/callback/logout routes, login_required decorator
- ui/templates/landing.html: GitHub login button, shows user info when logged in
- ui/templates/objects.html: Shows user avatar and logout link
- ui/requirements.txt: Added authlib
- docker/docker-compose.yml: Added GitHub OAuth env vars
- docker/.env.example: Added GitHub OAuth placeholders

Architecture supports multiple providers; Google/Apple can be added via tasks 8/9.
<!-- SECTION:FINAL_SUMMARY:END -->
