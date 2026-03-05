import sys
from pathlib import Path

# Ensure bootstrap/ submodules are importable as top-level modules in tests
sys.path.insert(0, str(Path(__file__).parent))

# Prevent pytest from collecting bootstrap.py as a test module (it's the entrypoint,
# not a test, and its top-level imports would fail without a real Postgres/S3)
collect_ignore = ['bootstrap.py']
