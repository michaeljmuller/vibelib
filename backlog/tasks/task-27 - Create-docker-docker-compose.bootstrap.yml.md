---
id: TASK-27
title: Create docker/docker-compose.bootstrap.yml
status: In Progress
assignee:
  - '@claude'
created_date: '2026-03-03 22:08'
updated_date: '2026-03-05 16:05'
labels:
  - bootstrap
milestone: m-1
dependencies:
  - TASK-17
  - TASK-19
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Create a dedicated docker/docker-compose.bootstrap.yml for running the bootstrap tool as a one-shot container.

The bootstrap container must share the gluetun network namespace using network_mode: "container:gluetun" (referencing the already-running gluetun container from the main docker/docker-compose.yml). Gluetun must be running before the bootstrap compose is invoked.

The compose file needs all S3 env vars, DATABASE_URL, and the optional tuning vars (AMAZON_DELAY_MIN, AMAZON_DELAY_MAX, FUZZY_MATCH_THRESHOLD, DRY_RUN, CACHE_DIR). CACHE_DIR should use a bind mount so the file cache persists across container restarts.

The common/ package must be available inside both the service container and the bootstrap container. Update both Dockerfiles to copy it in.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 CACHE_DIR is backed by a bind mount so cached files survive container restarts
- [ ] #2 All required env vars are documented in docker/.env.example
- [ ] #3 common/ package is copied into both the service and bootstrap Docker images and importable in both
- [ ] #4 docker/docker-compose.bootstrap.yml exists and defines the bootstrap one-shot service
- [ ] #5 bootstrap container uses network_mode: "container:gluetun" to route traffic through the running gluetun container
<!-- AC:END -->
