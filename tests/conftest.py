"""Shared pytest fixtures.

ffmpeg/ffprobe lookup order:
  1. PATH (shutil.which)
  2. Environment variables CLIPWRIGHT_FFPROBE / CLIPWRIGHT_FFMPEG

Integration tests are skipped only when neither source is found. [DC-AM-006]
CLIPWRIGHT_FFMPEG is for tests only; runtime (media.py) uses only ffprobe. [DC-AM-008]
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _find_binary(name: str, env_var: str) -> str | None:
    """Locate a binary via PATH then env_var. Returns None if neither is found."""
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
    """Path to the ffmpeg executable (for test media generation only).

    Returns None if not found.
    """
    return _find_binary("ffmpeg", "CLIPWRIGHT_FFMPEG")


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Return a temporary directory. Auto-cleaned up by pytest's tmp_path."""
    return tmp_path


@pytest.fixture(scope="session")
def sample_media(
    ffmpeg_path: str | None,
    tmp_path_factory: pytest.TempPathFactory,
) -> str:
    """Generate a 3-second test mp4 via ffmpeg lavfi (testsrc + sine).

    Returns the path to the generated file.
    Skips via pytest.skip only when ffmpeg is completely unavailable. [DC-AM-006]
    """
    if ffmpeg_path is None:
        pytest.skip(
            "ffmpeg not found. "
            "Install via winget install Gyan.FFmpeg or "
            "set CLIPWRIGHT_FFMPEG to the full path."
        )

    out_dir = tmp_path_factory.mktemp("media")
    out_path = str(out_dir / "test_3sec.mp4")

    cmd = [
        ffmpeg_path,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=3:size=320x240:rate=30",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=3",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-shortest",
        out_path,
    ]
    result = subprocess.run(
        cmd,
        shell=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.fail(
            f"Failed to generate test media.\ncmd: {cmd}\nstderr: {result.stderr}"
        )
    return out_path
