---
id: TASK-12
title: add a gluetun container to the back end
status: Testing
assignee:
  - '@claude'
created_date: '2026-02-26 23:32'
updated_date: '2026-02-26 23:48'
labels: []
dependencies: []
ordinal: 1000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
add a gluetun container so that outbound connections made by the container go through a VPN
<!-- SECTION:DESCRIPTION:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Read existing docker-compose.yml and .env.example
2. Add gluetun service to docker-compose.yml
3. Modify service container: remove ports, add network_mode and depends_on
4. Update ui SERVICE_URL from service:5000 to gluetun:5000
5. Document VPN variables in .env.example
6. Move task to Testing
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implementation complete:
- Added gluetun service with WireGuard/IVPN config
- service now uses network_mode: service:gluetun
- Port 5001:5000 moved from service to gluetun
- ui SERVICE_URL updated to http://gluetun:5000
- .env.example updated with VPN variable documentation

Testing passed:
- gluetun reached Healthy status; WireGuard connected to 91.132.137.170:58237
- Public IP from service container: 146.70.154.42 (IVPN exit node, New York)
- All four containers (gluetun, postgres, service, ui) reached Healthy
- UI returned HTTP 200 on localhost:8080
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Added gluetun VPN container to route service outbound traffic through IVPN WireGuard.

Changes:
- docker/docker-compose.yml: Added gluetun service with NET_ADMIN capability, /dev/net/tun device, and WireGuard environment variable mapping from custom VPN_* names to gluetun expected names. Moved port mapping 5001:5000 from service to gluetun. Added network_mode: "service:gluetun" and depends_on: [gluetun, postgres] to service container.
- docker/.env.example: Documented five new VPN_* variables (VPN_PROVIDER, VPN_TYPE, VPN_WIREGUARD_PRIVATE_KEY, VPN_WIREGUARD_ADDRESS, VPN_SERVER_COUNTRY) with placeholder values.
- ui SERVICE_URL updated from http://service:5000 to http://gluetun:5000 since service shares gluetun network namespace and is no longer reachable by its own name.

Verification steps:
1. docker compose up gluetun — confirm WireGuard tunnel establishes
2. docker compose up — all services start, UI on :8080
3. curl ifconfig.me from service container — should show IVPN exit IP
<!-- SECTION:FINAL_SUMMARY:END -->
