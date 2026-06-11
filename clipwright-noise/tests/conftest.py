"""Shared fixtures for clipwright-noise tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_media(tmp_path: Path) -> Path:
    """Generate and return a dummy media file for testing (mp4 extension stub)."""
    path = tmp_path / "video.mp4"
    path.write_bytes(b"dummy media")
    return path
