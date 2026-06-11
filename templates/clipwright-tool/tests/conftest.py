"""clipwright-__TOOL__ shared test fixtures.

Template provides only minimal fixture generating temporary input file.
Real tools place small real materials in fixtures/ and load them here.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def sample_input(tmp_path: Path) -> Path:
    """Generate and return dummy input file for testing."""
    path = tmp_path / "input.txt"
    path.write_text("sample", encoding="utf-8")
    return path
