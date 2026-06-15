"""Shared fixtures for clipwright-scene tests.

ffmpeg/ffprobe search order:
  1. PATH (shutil.which)
  2. Environment variables CLIPWRIGHT_FFMPEG / CLIPWRIGHT_FFPROBE

scenedetect search order:
  1. PATH (shutil.which)
  2. No env-var override (scenedetect is an optional extra with no dedicated env var)

Integration tests are skipped only when neither is found.
Follows the same resolution policy as clipwright-silence/tests/conftest.py.

e2e tests call real binaries, so direct subprocess use is permitted
(an intentional exception to the process.run convention in production code).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from clipwright.errors import ClipwrightError
from clipwright.process import resolve_tool


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


@pytest.fixture(scope="session")
def scenedetect_path() -> str | None:
    """Path to the scenedetect CLI executable. None if not found."""
    try:
        return resolve_tool("scenedetect", None)
    except ClipwrightError:
        return None


@pytest.fixture
def require_scenedetect(scenedetect_path: str | None) -> str:
    """For integration tests: skip if scenedetect is not found. Returns the path."""
    if scenedetect_path is None:
        pytest.skip(
            "scenedetect not found on PATH. "
            "Install via `pip install scenedetect` or the [pyscenedetect] optional extra."
        )
    return scenedetect_path
