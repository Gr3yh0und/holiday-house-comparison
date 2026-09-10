"""Make the repo root importable so `import app` / `import parsers` work
regardless of how pytest is invoked (bare `pytest`, `python -m pytest`, or
from a different cwd) — bare `pytest` does not add the repo root to
sys.path on its own, only the containing test directory.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# deploy.sh/deploy-test.sh check /etc/homelab/holiday-house-comparison.env
# before the repo-local deploy.config used by the deploy-script tests. Force
# that check to miss so the tests are isolated from whatever real config
# happens to exist on the machine running them (HOMELAB_ENV overrides the
# hardcoded path -- see deploy.sh/deploy-test.sh).
os.environ["HOMELAB_ENV"] = "/nonexistent-for-tests/holiday-house-comparison.env"
