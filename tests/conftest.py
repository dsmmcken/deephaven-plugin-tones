"""
Pytest configuration for deephaven-plugin-tones tests.

Adds ``src/`` to ``sys.path`` so that the package is importable without a
full ``pip install`` (which would require building the JS bundle).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Insert src/ at the front of the path so the local package shadows any
# installed version.
_src = str(Path(__file__).parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)
