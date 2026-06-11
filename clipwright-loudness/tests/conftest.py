"""Shared fixtures for clipwright-loudness tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_media(tmp_path: Path) -> Path:
    """Create and return a dummy media file for testing (mp4 extension stub)."""
    path = tmp_path / "video.mp4"
    path.write_bytes(b"dummy media")
    return path
