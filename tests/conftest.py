"""Make the repo root importable so `import app` / `import parsers` work
regardless of how pytest is invoked (bare `pytest`, `python -m pytest`, or
from a different cwd) — bare `pytest` does not add the repo root to
sys.path on its own, only the containing test directory.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
