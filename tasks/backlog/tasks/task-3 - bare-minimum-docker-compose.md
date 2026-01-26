---
id: TASK-3
title: bare minimum docker compose
status: Done
assignee:
  - '@claude'
created_date: '2026-01-26 00:55'
updated_date: '2026-01-26 03:15'
labels: []
dependencies: []
ordinal: 3000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
create a bare minimum docker compose file.

it should define the UI, service, and database containers.

the ui will just be a landing page for OAuth login and single page that lists object keys.

the service layer will just be a single endpoint that returns a limited number of S3 object keys. 

the database container will be unused initially.
<!-- SECTION:DESCRIPTION:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Create project directory structure (ui/, service/, docker/)
2. Create minimal Python service with Flask - single endpoint /api/objects returning mock S3 keys
3. Create minimal Python UI with Flask - landing page and objects list page (no OAuth yet, just placeholder)
4. Create Dockerfiles in docker/ for ui and service containers
5. Create docker/docker-compose.yml orchestrating ui, service, and postgres containers
6. Test docker compose up works and containers communicate
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Created the following structure:
- service/app.py - Flask app with /api/objects and /api/health endpoints
- service/requirements.txt
- ui/app.py - Flask app with landing page and objects list
- ui/templates/landing.html - OAuth login placeholder
- ui/templates/objects.html - Lists S3 object keys from service
- ui/requirements.txt
- docker/Dockerfile.service
- docker/Dockerfile.ui
- docker/docker-compose.yml - orchestrates ui, service, postgres

Docker daemon not running on dev machine, could not test containers.

Tested successfully:
- Changed service host port to 5001 (5000 used by AirPlay on macOS)
- Service API returns mock S3 keys at localhost:5001/api/objects
- UI landing page works at localhost:8080
- UI objects page fetches from service correctly
- All 3 containers running
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Created bare minimum docker compose setup with three containers:

- **UI**: Flask app with landing page (OAuth placeholder) and objects list page (port 8080)
- **Service**: Flask app with /api/objects endpoint returning mock S3 keys (port 5001)
- **Postgres**: Database container with bind mount at docker/data/postgres (port 5432, unused initially)

Files created:
- docker/docker-compose.yml, Dockerfile.service, Dockerfile.ui
- service/app.py, requirements.txt
- ui/app.py, requirements.txt, templates/landing.html, templates/objects.html

To run: cd docker && docker compose up --build
UI at localhost:8080, API at localhost:5001
<!-- SECTION:FINAL_SUMMARY:END -->
