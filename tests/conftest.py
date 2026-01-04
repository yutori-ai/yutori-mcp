"""Pytest configuration for yutori-mcp tests.

This file exists to prevent pytest from loading the root-level conftest.py
which contains fixtures for the full Yutori project (e.g., PostgresContainer).
"""

import sys
from pathlib import Path

# Add src to path for imports
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))
