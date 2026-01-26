---
id: TASK-9
title: Add Apple OAuth provider
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
Add Apple as an OAuth provider option for user authentication.

Requires: Apple Developer account ($99/year), real domain (no localhost)

## Setup Instructions (Apple Developer)

1. Go to https://developer.apple.com/account/

2. Create an App ID
   - Certificates, Identifiers & Profiles → Identifiers
   - "+" → "App IDs" → "App"
   - Description: "Library"
   - Bundle ID: com.yourname.library
   - Enable "Sign in with Apple"
   - Save

3. Create a Services ID (for web auth)
   - Identifiers → "+" → "Services IDs"
   - Description: "Library Web"
   - Identifier: com.yourname.library.web
   - Enable "Sign in with Apple"
   - Configure:
     - Domains: your production domain
     - Return URLs: https://yourdomain.com/callback/apple
   - Save

4. Create a Private Key
   - Keys → "+"
   - Name it, enable "Sign in with Apple"
   - Download .p8 file (only available once)
   - Note the Key ID

5. Add to docker/.env:
   APPLE_CLIENT_ID=com.yourname.library.web
   APPLE_TEAM_ID=your_team_id
   APPLE_KEY_ID=your_key_id
   APPLE_PRIVATE_KEY=contents_of_p8_file
<!-- SECTION:DESCRIPTION:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Timeframes:
- Setup: ~30 minutes
- Turnaround: immediate once configured
- Requires real domain (no localhost) and DNS access
<!-- SECTION:NOTES:END -->
