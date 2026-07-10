"""test_ffmetadata_mux.py — AC-8 real-ffmpeg execution proof for ffmetadata
chapter export.

confirm-export §6 identified this as the one missing acceptance-criterion
test in clipwright-export: `test_chapters.py` (see its module docstring)
deliberately only exercises `serialize_ffmetadata`'s pure text output — it
never hands that text to a real ffmpeg process. That leaves the "is this
FFMETADATA1 text actually accepted by ffmpeg and interpreted as the intended
chapters" question unverified, which is exactly the class of bug this
project has been burned by before (filter_complex string-assertion-only
tests missing real graph-wiring/media-type-mismatch failures — see
test_pip_ffmpeg_execution.py's module docstring).

This module closes that gap end-to-end:
  1. Generate a short synthetic video with real ffmpeg (`color=` lavfi
     source).
  2. Build an OTIO timeline with 2-3 `scene_boundary` markers and export it
     via the real `export_chapters(format="ffmetadata")` pipeline (not just
     `serialize_ffmetadata` directly) — this also exercises
     `_collect_chapters`/`_export_chapters_inner` end-to-end.
  3. Mux the generated ffmetadata sidecar into the video with real ffmpeg
     (`-map_metadata 1 -codec copy`).
  4. Read the chapters back out with real ffprobe (`-show_chapters -of
     json`) and assert start/end/title match what was exported.

One dedicated test additionally proves the `_escape_ffmeta` escaping is
functionally necessary and correct: an unescaped `;` or `=` in a FFMETADATA1
value is a syntax-significant character (comment / key-value separator), so
if escaping were broken the mux would silently produce a different chapter
title (or ffmpeg would refuse the file) rather than crash — a bug class that
only real ffmpeg parsing can catch.

Skipped entirely when ffmpeg/ffprobe cannot be resolved (CLIPWRIGHT_FFMPEG /
CLIPWRIGHT_FFPROBE env vars, PATH fallback) — see `pyproject.toml`'s
`integration` marker. Env/skip/subprocess conventions mirror
`clipwright-render/tests/test_pip_ffmpeg_execution.py`.

How to run:
  uv run pytest -k ffmetadata_mux
  (or: uv run pytest -m integration)

IMPORTANT: run with `uv run pytest` (not bare `pytest`) — a bare interpreter
without the workspace venv is a known environment pitfall in this repo.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from clipwright.otio_utils import add_clip, add_marker, new_timeline, save_timeline
from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

from clipwright_export.chapters import export_chapters
from clipwright_export.schemas import ExportChaptersOptions

# ===========================================================================
# ffmpeg/ffprobe binary resolution (mirrors test_pip_ffmpeg_execution.py)
# ===========================================================================


def _find_binary(name: str, env_var: str) -> str | None:
    """Search for a binary in PATH first, then fall back to env_var."""
    found = shutil.which(name)
    if found:
        return found
    env_val = os.environ.get(env_var)
    if env_val and Path(env_val).is_file():
        return env_val
    return None


_FFMPEG = _find_binary("ffmpeg", "CLIPWRIGHT_FFMPEG")
_FFPROBE = _find_binary("ffprobe", "CLIPWRIGHT_FFPROBE")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        _FFMPEG is None or _FFPROBE is None,
        reason=(
            "ffmpeg/ffprobe not found. Add both to PATH or set the "
            "CLIPWRIGHT_FFMPEG / CLIPWRIGHT_FFPROBE environment variables."
        ),
    ),
]

_E2E_TIMEOUT: int = int(os.environ.get("E2E_TIMEOUT_SEC", "120"))

_RATE = 25.0  # fps
_WIDTH = 64
_HEIGHT = 64
_VIDEO_DURATION_SEC = 6.0

# ===========================================================================
# Helpers: fixture generation
# ===========================================================================


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with the project's standard security discipline:
    argv (no shell=True), explicit timeout, utf-8 decode with replacement
    (CWE-78 / cp932 discipline)."""
    return subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )


def _make_synthetic_video(
    ffmpeg: str,
    output: Path,
    *,
    color: str = "blue",
    duration: float = _VIDEO_DURATION_SEC,
    rate: float = _RATE,
    width: int = _WIDTH,
    height: int = _HEIGHT,
) -> None:
    """Generate a short solid-color video (no audio needed for chapter mux)."""
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color={color}:size={width}x{height}:rate={int(rate)}:duration={duration}",
        "-t",
        str(duration),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    result = _run(cmd)
    assert result.returncode == 0, (
        f"Fixture video generation failed ({output.name}): {result.stderr[:400]}"
    )


def _build_scene_timeline(
    tmp_path: Path,
    *,
    name: str,
    markers: list[tuple[float, str]],
    clip_duration_sec: float = _VIDEO_DURATION_SEC,
    rate: float = _RATE,
) -> Path:
    """Build a V1-only OTIO timeline with scene_boundary markers and save it.

    The clip's media_reference points at a non-existent path: chapters.py
    only validates the *timeline* file's existence
    (validate_source_or_basename), never the referenced media, so a real
    media file is unnecessary here (matches test_chapters.py's
    `_build_timeline` convention).
    """
    tl = new_timeline(name=name)
    v1 = tl.tracks[0]
    add_clip(
        v1,
        media=MediaRef(target_url="/fake/video.mp4"),
        source_range=TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=rate),
            duration=RationalTimeModel(value=clip_duration_sec * rate, rate=rate),
        ),
    )
    for start_sec, title in markers:
        add_marker(
            v1,
            marked_range=TimeRangeModel(
                start_time=RationalTimeModel(value=start_sec * rate, rate=rate),
                duration=RationalTimeModel(value=0.0, rate=rate),
            ),
            name=title,
            metadata={"kind": "scene_boundary"},
        )

    otio_path = tmp_path / f"{name}.otio"
    save_timeline(tl, str(otio_path))
    return otio_path


def _mux_ffmetadata(
    ffmpeg: str, video: Path, ffmeta: Path, output: Path
) -> subprocess.CompletedProcess[str]:
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video),
        "-i",
        str(ffmeta),
        "-map_metadata",
        "1",
        "-codec",
        "copy",
        str(output),
    ]
    return _run(cmd)


def _ffprobe_chapters(ffprobe: str, video: Path) -> list[dict[str, Any]]:
    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_chapters",
        str(video),
    ]
    result = _run(cmd)
    assert result.returncode == 0, (
        f"ffprobe -show_chapters failed: {result.stderr[:400]}"
    )
    payload = json.loads(result.stdout)
    chapters = payload.get("chapters", [])
    assert isinstance(chapters, list)
    return chapters


# ===========================================================================
# Tests
# ===========================================================================

_TOLERANCE_SEC = 0.05


class TestFfmetadataMuxRealFfmpeg:
    def test_mux_and_ffprobe_measure_expected_chapters(self, tmp_path: Path) -> None:
        """AC-8: export_chapters(format="ffmetadata") produces a sidecar that
        real ffmpeg accepts via -map_metadata, and real ffprobe reports the
        same chapter count/START/END/title back."""
        assert _FFMPEG is not None
        assert _FFPROBE is not None

        markers = [
            (0.0, "Intro"),
            (2.0, "Middle Chapter"),
            (4.0, "End"),
        ]
        otio_path = _build_scene_timeline(tmp_path, name="mux_basic", markers=markers)

        ffmeta_path = tmp_path / "chapters.ffmeta"
        result = export_chapters(
            timeline=str(otio_path),
            output=str(ffmeta_path),
            options=ExportChaptersOptions(format="ffmetadata"),
        )
        assert result.ok is True, result.error
        assert result.data["chapter_count"] == 3
        assert ffmeta_path.exists()

        video_path = tmp_path / "src.mp4"
        _make_synthetic_video(_FFMPEG, video_path)

        muxed_path = tmp_path / "muxed.mp4"
        mux_result = _mux_ffmetadata(_FFMPEG, video_path, ffmeta_path, muxed_path)
        assert mux_result.returncode == 0, (
            f"ffmpeg mux failed: {mux_result.stderr[:400]}"
        )
        assert muxed_path.exists()

        probed = _ffprobe_chapters(_FFPROBE, muxed_path)
        assert len(probed) == 3

        expected_starts_sec = [0.0, 2.0, 4.0]
        expected_ends_sec = [2.0, 4.0, 6.0]
        expected_titles = ["Intro", "Middle Chapter", "End"]

        for chapter, exp_start, exp_end, exp_title in zip(
            probed, expected_starts_sec, expected_ends_sec, expected_titles, strict=True
        ):
            assert abs(float(chapter["start_time"]) - exp_start) < _TOLERANCE_SEC
            assert abs(float(chapter["end_time"]) - exp_end) < _TOLERANCE_SEC
            assert chapter["tags"]["title"] == exp_title

    def test_escaped_title_roundtrips_through_mux(self, tmp_path: Path) -> None:
        """AC-8 escaping proof: a title containing '=' and ';' — both
        syntax-significant in FFMETADATA1 (key-value separator / comment
        marker) — must be escaped by _escape_ffmeta so that real ffmpeg
        parses the whole value as one title, and ffprobe reads back the
        exact original (unescaped) string. If escaping were broken, ffmpeg
        would either truncate the value at the ';'/'=' or fail to parse the
        file at all."""
        assert _FFMPEG is not None
        assert _FFPROBE is not None

        raw_title = "Q=A;Cost#1"
        markers = [
            (0.0, "Intro"),
            (2.0, raw_title),
            (4.0, "Outro"),
        ]
        otio_path = _build_scene_timeline(tmp_path, name="mux_escaped", markers=markers)

        ffmeta_path = tmp_path / "chapters_escaped.ffmeta"
        result = export_chapters(
            timeline=str(otio_path),
            output=str(ffmeta_path),
            options=ExportChaptersOptions(format="ffmetadata"),
        )
        assert result.ok is True, result.error

        # Sanity: the escaping actually happened in the written sidecar text.
        ffmeta_text = ffmeta_path.read_text(encoding="utf-8")
        assert "title=Q\\=A\\;Cost\\#1" in ffmeta_text

        video_path = tmp_path / "src_escaped.mp4"
        _make_synthetic_video(_FFMPEG, video_path)

        muxed_path = tmp_path / "muxed_escaped.mp4"
        mux_result = _mux_ffmetadata(_FFMPEG, video_path, ffmeta_path, muxed_path)
        assert mux_result.returncode == 0, (
            f"ffmpeg mux failed: {mux_result.stderr[:400]}"
        )

        probed = _ffprobe_chapters(_FFPROBE, muxed_path)
        assert len(probed) == 3
        assert probed[1]["tags"]["title"] == raw_title
