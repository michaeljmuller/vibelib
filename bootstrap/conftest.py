import sys
from pathlib import Path
from unittest.mock import MagicMock

_HERE = Path(__file__).parent

# Ensure bootstrap/ submodules are importable as top-level modules in tests
sys.path.insert(0, str(_HERE))

# Ensure the project root is on the path so `common` is importable
sys.path.insert(0, str(_HERE.parent))

# Stub out common.amazon before any test imports it.  common/amazon.py imports
# playwright at module level; playwright is not installed in the test environment.
# enrich_amazon.py lazily imports scrape_amazon_metadata from common.amazon, so
# tests patch common.amazon.scrape_amazon_metadata instead of the enrich_amazon module.
sys.modules.setdefault('common.amazon', MagicMock())

# Prevent pytest from collecting bootstrap.py as a test module (it's the entrypoint,
# not a test, and its top-level imports would fail without a real Postgres/S3)
collect_ignore = ['bootstrap.py']
