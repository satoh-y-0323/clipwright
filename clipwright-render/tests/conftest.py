"""conftest.py — Shared fixtures for clipwright-render tests.

Binary resolution order for ffmpeg/ffprobe:
  1. PATH (shutil.which)
  2. Environment variables CLIPWRIGHT_FFMPEG / CLIPWRIGHT_FFPROBE

Integration tests are skipped only when neither source finds the binary.
Follows the same resolution strategy as core's conftest.py (DC-GP-002).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _find_binary(name: str, env_var: str) -> str | None:
    """Search for a binary via PATH then env_var. Returns None if not found."""
    found = shutil.which(name)
    if found:
        return found
    env_val = os.environ.get(env_var)
    if env_val and Path(env_val).is_file():
        return env_val
    return None


@pytest.fixture(scope="session")
def ffprobe_path() -> str | None:
    """Path to the ffprobe executable, or None if not found."""
    return _find_binary("ffprobe", "CLIPWRIGHT_FFPROBE")


@pytest.fixture(scope="session")
def ffmpeg_path() -> str | None:
    """Path to the ffmpeg executable, or None if not found."""
    return _find_binary("ffmpeg", "CLIPWRIGHT_FFMPEG")


@pytest.fixture
def require_ffprobe(ffprobe_path: str | None) -> str:
    """For integration tests: skip if ffprobe is unavailable. Returns the path."""
    if ffprobe_path is None:
        pytest.skip(
            "ffprobe not found. "
            "Add ffprobe to PATH or set the CLIPWRIGHT_FFPROBE environment variable."
        )
    return ffprobe_path


@pytest.fixture
def require_ffmpeg(ffmpeg_path: str | None) -> str:
    """For integration tests: skip if ffmpeg is unavailable. Returns the path."""
    if ffmpeg_path is None:
        pytest.skip(
            "ffmpeg not found. "
            "Add ffmpeg to PATH or set the CLIPWRIGHT_FFMPEG environment variable."
        )
    return ffmpeg_path


@pytest.fixture(scope="session")
def subtitles_filter_available(ffmpeg_path: str | None) -> bool:
    """Return True when ffmpeg's subtitles filter (libass) is compiled in."""
    if ffmpeg_path is None:
        return False
    try:
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return any("subtitles" in line for line in result.stdout.splitlines())
    except Exception:
        return False


@pytest.fixture
def require_subtitles_filter(subtitles_filter_available: bool) -> None:
    """Skip tests when ffmpeg subtitles filter (libass) is unavailable."""
    if not subtitles_filter_available:
        pytest.skip(
            "ffmpeg subtitles filter not available (libass not compiled in). "
            "Install an ffmpeg build with libass support."
        )
