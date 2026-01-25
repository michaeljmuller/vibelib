#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKLOG_PORT=6555

# kill any old backlog jobs
lsof -tiTCP:${BACKLOG_PORT} -sTCP:LISTEN | xargs kill

# run backlog
cd ${SCRIPT_DIR}/tasks/backlog && backlog browser --port ${BACKLOG_PORT} &

