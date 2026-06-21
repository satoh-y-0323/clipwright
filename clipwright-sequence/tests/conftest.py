"""Shared fixtures for clipwright-sequence tests.

ffprobe search order:
  1. PATH (shutil.which)
  2. Environment variable CLIPWRIGHT_FFPROBE

ffmpeg search order (used only for sample media generation in integration tests):
  1. PATH (shutil.which)
  2. Environment variable CLIPWRIGHT_FFMPEG

Integration tests are skipped only when the required binary is not found.
Follows the same resolution policy as clipwright-trim/tests/conftest.py.

e2e tests call real binaries via subprocess directly (an intentional exception
to the process.run convention used in production code).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _find_binary(name: str, env_var: str) -> str | None:
    """Search for a binary in PATH then env_var. Returns None if neither is found."""
    found = shutil.which(name)
    if found:
        return found
    env_val = os.environ.get(env_var)
    if env_val and Path(env_val).is_file():
        return env_val
    return None


@pytest.fixture(scope="session")
def ffprobe_path() -> str | None:
    """Path to the ffprobe executable. None if not found."""
    return _find_binary("ffprobe", "CLIPWRIGHT_FFPROBE")


@pytest.fixture(scope="session")
def ffmpeg_path() -> str | None:
    """Path to the ffmpeg executable. None if not found."""
    return _find_binary("ffmpeg", "CLIPWRIGHT_FFMPEG")


@pytest.fixture
def require_ffprobe(ffprobe_path: str | None) -> str:
    """For integration tests: skip if ffprobe is not found. Returns the path."""
    if ffprobe_path is None:
        pytest.skip(
            "ffprobe not found on PATH. "
            "Add ffprobe to PATH or set the CLIPWRIGHT_FFPROBE environment variable to the full path."
        )
    return ffprobe_path


@pytest.fixture
def require_ffmpeg(ffmpeg_path: str | None) -> str:
    """For integration tests: skip if ffmpeg is not found. Returns the path."""
    if ffmpeg_path is None:
        pytest.skip(
            "ffmpeg not found on PATH. "
            "Add ffmpeg to PATH or set the CLIPWRIGHT_FFMPEG environment variable to the full path."
        )
    return ffmpeg_path


@pytest.fixture
def sample_media(tmp_path: Path, require_ffmpeg: str) -> Path:
    """Generate a short silent audio file for integration tests.

    Creates a 10-second silent WAV file using ffmpeg. Requires the
    require_ffmpeg fixture (integration tests only).
    """
    media_path = tmp_path / "sample.wav"
    subprocess.run(
        [
            require_ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            "10",
            str(media_path),
        ],
        check=True,
        capture_output=True,
    )
    return media_path
