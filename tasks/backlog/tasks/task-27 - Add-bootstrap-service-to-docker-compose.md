---
id: TASK-27
title: Add bootstrap service to docker-compose
status: To Do
assignee:
  - '@claude'
created_date: '2026-03-03 22:08'
labels:
  - bootstrap
dependencies:
  - TASK-17
  - TASK-19
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Add the bootstrap tool as a one-shot service in docker/docker-compose.yml.

The bootstrap container must share the gluetun network namespace (network_mode: "service:gluetun") so Amazon requests exit through the VPN. It should be defined under a Docker Compose profile (e.g., --profile bootstrap) so it does not start automatically with the normal stack.

The service needs all S3 env vars, DATABASE_URL, and the optional tuning vars (AMAZON_DELAY_MIN, AMAZON_DELAY_MAX, FUZZY_MATCH_THRESHOLD, DRY_RUN, CACHE_DIR). CACHE_DIR should use a bind mount so the file cache persists across container restarts.

The common/ package must be available inside both the service container and the bootstrap container. Update both Dockerfiles to copy it in.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 bootstrap service defined in docker-compose.yml under a named profile so it does not start with the default stack
- [ ] #2 bootstrap container uses network_mode: "service:gluetun"
- [ ] #3 CACHE_DIR is backed by a bind mount so cached files survive container restarts
- [ ] #4 All required env vars are documented in docker/.env.example
- [ ] #5 common/ package is copied into both the service and bootstrap Docker images and importable in both
<!-- AC:END -->
