#!/bin/bash
# Run the web app's unit tests inside the web image -- nothing installs on the host.
# Extra arguments go straight to pytest:
#   util/test.sh
#   util/test.sh -k epub -v
set -e
cd "$(dirname "$0")/../src/docker"

# --no-deps: the tests are pure functions over the ingest modules; they need no
# database and no object store, so there is nothing to wait for.
exec docker compose run --rm --no-deps --build \
  --volume "$(cd ../python/web && pwd)/tests:/app/tests:ro" \
  --entrypoint sh web -c "pip install --quiet --no-cache-dir pytest && cd /app && python -m pytest ${*:-tests}"
