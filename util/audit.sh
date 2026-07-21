#!/bin/bash
# Report suspect epub/m4b -> abstract book associations. Read-only; fixes nothing.
#   util/audit.sh                    all checks (LLM adjudicates ambiguous cases)
#   util/audit.sh --no-llm           deterministic rules only
#   util/audit.sh --check audio      one check: audio | multi | omnibus | orphans
#   util/audit.sh --json
set -e
cd "$(dirname "$0")/../src/docker"
exec docker compose run --rm resolver audit "$@"
