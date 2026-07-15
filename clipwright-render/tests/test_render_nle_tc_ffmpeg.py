"""test_render_nle_tc_ffmpeg.py — Real ffmpeg integration tests for NLE
timecode-origin coordinate relativization (task_id: test-ffmpeg-integration).

Scope (requirements-report-20260715-190935.md AC-6,
architecture-report-20260715-191151.md §6-3/§9 DC-AS-003,
spike-report-nle-probe.md fixture generation commands):

  These tests exercise the full real-ffmpeg round trip that
  test_plan_nle_relativize.py (pure-OTIO, no ffmpeg dependency) cannot cover:
  building a timecode-origin timeline with ``clipwright.nle_interop
  .conform_timeline_for_nle`` directly (create-tool wiring — trim/silence/
  sequence/... — is out of this task's scope per the task brief; the core
  helper is exercised directly so this test is independent of any
  in-progress satellite wiring), then rendering it with real ffmpeg and
  ffprobe-verifying the output.

  1. AC-6: a timecode-origin timeline (built from a ``-timecode 01:00:00:00``
     source) renders to the *same* cut duration as an otherwise-identical
     timecode-free source pushed through the identical KEEP-range /
     conform / render pipeline. This is the practical proof that
     resolve_kept_ranges's relativization (ADR-NI-1) is wired end-to-end: if
     the TC-origin absolute source_range (~3600.5s) ever reached ffmpeg's
     trim filter unrelativized, trimming a source_range far beyond the
     3-second source file's actual duration would produce a near-zero or
     failing output, not the correct ~1.5s result.
  2. DC-AS-003: an 8-stream x 1-channel source (asplit=8 + -map x8, as in
     spike-report-nle-probe.md) conforms successfully (audio streams mirror
     onto Resolve_OTIO audio tracks A1..A8) and renders without error; the
     rendered output carries exactly one audio stream, and that stream's
     sample_rate matches the *first* (index 0) audio stream of the source —
     confirming render's [0:a] selection is unaffected by the additional
     mirrored audio tracks conform adds to the timeline.

How to run (skipped when ffmpeg/ffprobe are absent):
  uv run pytest -k nle_tc_ffmpeg

Set ffmpeg/ffprobe on PATH or via CLIPWRIGHT_FFMPEG / CLIPWRIGHT_FFPROBE env
vars (same resolution order as conftest.py require_ffmpeg/require_ffprobe).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest
from clipwright.media import inspect_media
from clipwright.nle_interop import conform_timeline_for_nle
from clipwright.otio_utils import add_clip, new_timeline, save_timeline
from clipwright.schemas import (
    MediaInfo,
    MediaRef,
    RationalTimeModel,
    TimeRangeModel,
    full_media_range,
)

from clipwright_render.render import render_timeline
from clipwright_render.schemas import RenderOptions

# ===========================================================================
# ffmpeg / ffprobe binary resolution (mirrors test_e2e_merge.py / conftest.py)
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

pytestmark = pytest.mark.e2e

requires_ffmpeg = pytest.mark.skipif(
    _FFMPEG is None,
    reason=(
        "ffmpeg not found. "
        "Add ffmpeg to PATH or "
        "set the CLIPWRIGHT_FFMPEG environment variable to its full path."
    ),
)

requires_ffprobe = pytest.mark.skipif(
    _FFPROBE is None,
    reason=(
        "ffprobe not found. "
        "Add ffprobe to PATH or "
        "set the CLIPWRIGHT_FFPROBE environment variable to its full path."
    ),
)

# Subprocess timeout in seconds for all e2e tests (overridable via CI env var).
_E2E_TIMEOUT: int = int(os.environ.get("E2E_TIMEOUT_SEC", "120"))

# Small/short fixtures to keep this suite fast (§spike-report-nle-probe.md
# used 2s testsrc2 fixtures with the same rationale).
_RATE = 25.0
_WIDTH = 320
_HEIGHT = 180
_SOURCE_DURATION = 3.0
_TC_START = "01:00:00:00"

# Two KEEP ranges with a gap between them, mirroring the shape produced by
# trim/silence (multiple KEEP Clips on V1, not one full-length clip).
_KEEP_RANGES: list[tuple[float, float]] = [(0.5, 1.2), (1.8, 2.6)]
_EXPECTED_OUTPUT_DURATION = sum(end - start for start, end in _KEEP_RANGES)  # 1.5s

# Duration tolerance: ±2 frames at 25fps.
_FRAME_TOLERANCE = 2 / _RATE

# 8 distinct standard sample rates (all valid AAC LC sampling rates) so that
# ffprobe alone can distinguish which source audio stream survived into the
# rendered output (DC-AS-003 "[0:a] 由来" check).
_EIGHT_CHANNEL_SAMPLE_RATES = [8000, 11025, 16000, 22050, 32000, 44100, 48000, 96000]


# ===========================================================================
# Helpers: fixture generation (real ffmpeg, spike-report-nle-probe.md pattern)
# ===========================================================================


def _make_video(
    ffmpeg: str,
    output: Path,
    *,
    timecode: str | None,
    sample_rate: int = 44100,
) -> None:
    """Generate a small testsrc2+sine video, optionally stamped with a
    ``-timecode`` tag (mp4/mov muxer family; confirmed via real ffprobe to
    land in the video stream's ``tags.timecode``, spike-report-nle-probe.md
    §1)."""
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={_WIDTH}x{_HEIGHT}:rate={_RATE}:duration={_SOURCE_DURATION}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate={sample_rate}:duration={_SOURCE_DURATION}",
    ]
    if timecode is not None:
        cmd.extend(["-timecode", timecode])
    cmd.extend(
        [
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ]
    )
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, f"fixture generation failed: {result.stderr[:400]}"


def _make_eight_channel_video(
    ffmpeg: str,
    output: Path,
    *,
    timecode: str | None,
) -> None:
    """Generate a video with 8 mono audio streams, each a distinct sample
    rate, mapped in order (asplit-equivalent multi-input mapping — see
    spike-report-nle-probe.md §5 for the asplit=8 variant this mirrors)."""
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={_WIDTH}x{_HEIGHT}:rate={_RATE}:duration={_SOURCE_DURATION}",
    ]
    for sample_rate in _EIGHT_CHANNEL_SAMPLE_RATES:
        cmd.extend(
            [
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:sample_rate={sample_rate}:duration={_SOURCE_DURATION}",
            ]
        )
    if timecode is not None:
        cmd.extend(["-timecode", timecode])
    cmd.extend(["-map", "0:v"])
    for i in range(1, len(_EIGHT_CHANNEL_SAMPLE_RATES) + 1):
        cmd.extend(["-map", f"{i}:a"])
    cmd.extend(["-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", str(output)])
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"8x1ch fixture generation failed: {result.stderr[:400]}"
    )


# ===========================================================================
# Helpers: ffprobe inspection of rendered output
# ===========================================================================


def _probe_json(ffprobe: str, media: Path) -> dict[str, Any]:
    """Return ffprobe -show_format -show_streams JSON for *media*."""
    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(media),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, f"ffprobe failed: {result.stderr[:400]}"
    parsed: dict[str, Any] = json.loads(result.stdout)
    return parsed


def _get_duration_seconds(probe: dict[str, Any]) -> float:
    duration_str = probe.get("format", {}).get("duration")
    assert duration_str is not None, "duration could not be retrieved"
    return float(duration_str)


def _get_audio_streams(probe: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]


# ===========================================================================
# Helpers: OTIO timeline construction via conform_timeline_for_nle directly
# ===========================================================================


def _build_conformed_two_keep_timeline(
    media_path: Path, media_info: MediaInfo
) -> tuple[otio.schema.Timeline, list[str]]:
    """Build a hand-assembled 2-KEEP-range timeline (the shape trim/silence
    produce) and run it through ``conform_timeline_for_nle`` directly.

    Per the task brief, satellite create-tool wiring (trim.py etc.) is out of
    scope here; this test exercises the core NLE-interop helper directly so
    it does not depend on any given satellite's wiring state.
    """
    media_ref = MediaRef(
        target_url=str(media_path), available_range=full_media_range(media_info)
    )
    timeline = new_timeline(media_path.name)
    v1 = timeline.tracks[0]
    rate = media_info.duration.rate  # type: ignore[union-attr]
    for start_sec, end_sec in _KEEP_RANGES:
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=start_sec * rate, rate=rate),
            duration=RationalTimeModel(value=(end_sec - start_sec) * rate, rate=rate),
        )
        add_clip(v1, media_ref, source_range, name="keep")

    warnings = conform_timeline_for_nle(timeline, {str(media_path): media_info})
    return timeline, warnings


def _build_conformed_single_clip_timeline(
    media_path: Path, media_info: MediaInfo, *, start_sec: float, duration_sec: float
) -> tuple[otio.schema.Timeline, list[str]]:
    """Build a single-clip timeline and conform it (used for the 8x1ch case,
    where only one clip is needed to exercise audio-track mirroring)."""
    media_ref = MediaRef(
        target_url=str(media_path), available_range=full_media_range(media_info)
    )
    timeline = new_timeline(media_path.name)
    v1 = timeline.tracks[0]
    rate = media_info.duration.rate  # type: ignore[union-attr]
    source_range = TimeRangeModel(
        start_time=RationalTimeModel(value=start_sec * rate, rate=rate),
        duration=RationalTimeModel(value=duration_sec * rate, rate=rate),
    )
    add_clip(v1, media_ref, source_range, name="keep")

    warnings = conform_timeline_for_nle(timeline, {str(media_path): media_info})
    return timeline, warnings


# ===========================================================================
# Tests
# ===========================================================================


@requires_ffmpeg
@requires_ffprobe
class TestTcOriginRenderMatchesNoTcOriginRender:
    """AC-6: TC-origin timeline renders to the same cut duration as an
    otherwise-identical TC-free timeline (ADR-NI-1 relativization proof)."""

    def test_conform_sets_global_start_time_only_for_tc_source(
        self, tmp_path: Path
    ) -> None:
        """Sanity check that the two fixtures actually diverge in TC
        metadata before comparing their rendered outputs (otherwise a
        no-op relativization bug could hide behind a trivial pass)."""
        assert _FFMPEG is not None
        tc_source = tmp_path / "tc_source.mp4"
        notc_source = tmp_path / "notc_source.mp4"
        _make_video(_FFMPEG, tc_source, timecode=_TC_START)
        _make_video(_FFMPEG, notc_source, timecode=None)

        tc_info = inspect_media(str(tc_source))
        notc_info = inspect_media(str(notc_source))
        assert tc_info.start_timecode == _TC_START
        assert notc_info.start_timecode is None

        tc_timeline, _ = _build_conformed_two_keep_timeline(tc_source, tc_info)
        notc_timeline, _ = _build_conformed_two_keep_timeline(notc_source, notc_info)

        assert tc_timeline.global_start_time is not None
        assert notc_timeline.global_start_time is None

    def test_output_duration_matches_between_tc_and_no_tc_sources(
        self, tmp_path: Path
    ) -> None:
        """The rendered output duration must be the same (within ±2 frames)
        whether the source carries a non-zero start timecode or not.

        If resolve_kept_ranges ever stopped relativizing source_range against
        available_range.start (ADR-NI-1), the TC-origin clip's absolute
        source_range (~3600.5s) would be handed to ffmpeg's trim filter
        against a 3-second source file — producing a near-zero-length or
        failing render, not the correct ~1.5s result asserted below.
        """
        assert _FFMPEG is not None
        assert _FFPROBE is not None
        tc_source = tmp_path / "tc_source.mp4"
        notc_source = tmp_path / "notc_source.mp4"
        _make_video(_FFMPEG, tc_source, timecode=_TC_START)
        _make_video(_FFMPEG, notc_source, timecode=None)

        tc_info = inspect_media(str(tc_source))
        notc_info = inspect_media(str(notc_source))

        tc_timeline, tc_warnings = _build_conformed_two_keep_timeline(
            tc_source, tc_info
        )
        notc_timeline, notc_warnings = _build_conformed_two_keep_timeline(
            notc_source, notc_info
        )
        assert tc_warnings == []
        assert notc_warnings == []

        tc_timeline_path = tmp_path / "tc_timeline.otio"
        notc_timeline_path = tmp_path / "notc_timeline.otio"
        save_timeline(tc_timeline, str(tc_timeline_path))
        save_timeline(notc_timeline, str(notc_timeline_path))

        tc_out = tmp_path / "tc_out.mp4"
        notc_out = tmp_path / "notc_out.mp4"
        tc_result = render_timeline(
            str(tc_timeline_path), str(tc_out), RenderOptions(), dry_run=False
        )
        notc_result = render_timeline(
            str(notc_timeline_path), str(notc_out), RenderOptions(), dry_run=False
        )

        assert tc_result["ok"] is True, f"TC-origin render failed: {tc_result}"
        assert notc_result["ok"] is True, f"TC-free render failed: {notc_result}"

        tc_probe = _probe_json(_FFPROBE, tc_out)
        notc_probe = _probe_json(_FFPROBE, notc_out)
        tc_duration = _get_duration_seconds(tc_probe)
        notc_duration = _get_duration_seconds(notc_probe)

        assert abs(tc_duration - _EXPECTED_OUTPUT_DURATION) <= _FRAME_TOLERANCE, (
            f"TC-origin render duration {tc_duration}s deviates from the "
            f"expected {_EXPECTED_OUTPUT_DURATION}s cut total by more than "
            f"{_FRAME_TOLERANCE}s — relativization did not produce the "
            "correct cut position/duration"
        )
        assert abs(notc_duration - _EXPECTED_OUTPUT_DURATION) <= _FRAME_TOLERANCE
        assert abs(tc_duration - notc_duration) <= _FRAME_TOLERANCE, (
            f"TC-origin ({tc_duration}s) and TC-free ({notc_duration}s) "
            "renders diverged; they must produce the same cut position and "
            "duration (AC-6)"
        )


@requires_ffmpeg
@requires_ffprobe
class TestEightChannelSourceConformAndRender:
    """DC-AS-003: an 8x1ch source's conformed (multi-audio-track) timeline
    still renders with a single [0:a]-derived output audio stream."""

    def test_conform_mirrors_eight_audio_tracks(self, tmp_path: Path) -> None:
        """Sanity check: conform_timeline_for_nle actually mirrors all 8
        audio streams onto 8 Audio tracks before rendering is exercised."""
        assert _FFMPEG is not None
        source = tmp_path / "eight_channel.mp4"
        _make_eight_channel_video(_FFMPEG, source, timecode=_TC_START)

        media_info = inspect_media(str(source))
        audio_streams = [s for s in media_info.streams if s.codec_type == "audio"]
        assert len(audio_streams) == 8

        timeline, warnings = _build_conformed_single_clip_timeline(
            source, media_info, start_sec=0.5, duration_sec=1.0
        )
        assert warnings == []
        audio_tracks = [
            t for t in timeline.tracks if t.kind == otio.schema.TrackKind.Audio
        ]
        assert len(audio_tracks) == 8

    def test_render_output_has_single_audio_stream_from_first_source_stream(
        self, tmp_path: Path
    ) -> None:
        """render must still select a single audio stream ([0:a]) even
        though the conformed timeline now carries 8 mirrored audio tracks
        (architecture-report §9 DC-AS-003: resolve_kept_ranges/resolve_bgm
        do not walk the mirrored audio tracks; only the [0:a] input stream
        reaches the output).

        The 8 source audio streams are given distinct sample rates so that
        ffprobe alone can attribute the single output audio stream back to
        the *first* source audio stream (index 0 == [0:a]), not any other
        mirrored position.
        """
        assert _FFMPEG is not None
        assert _FFPROBE is not None
        source = tmp_path / "eight_channel.mp4"
        _make_eight_channel_video(_FFMPEG, source, timecode=_TC_START)

        media_info = inspect_media(str(source))
        source_audio_streams = [
            s for s in media_info.streams if s.codec_type == "audio"
        ]
        assert [s.sample_rate for s in source_audio_streams] == (
            _EIGHT_CHANNEL_SAMPLE_RATES
        )

        timeline, warnings = _build_conformed_single_clip_timeline(
            source, media_info, start_sec=0.5, duration_sec=1.0
        )
        assert warnings == []

        timeline_path = tmp_path / "eight_channel_timeline.otio"
        save_timeline(timeline, str(timeline_path))

        out_path = tmp_path / "eight_channel_out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"8x1ch render failed: {result}"

        out_probe = _probe_json(_FFPROBE, out_path)
        out_audio_streams = _get_audio_streams(out_probe)
        assert len(out_audio_streams) == 1, (
            f"expected exactly 1 audio stream in the output, got "
            f"{len(out_audio_streams)}: {out_audio_streams}"
        )
        assert (
            int(out_audio_streams[0]["sample_rate"]) == (_EIGHT_CHANNEL_SAMPLE_RATES[0])
        ), (
            "output audio stream does not match the first ([0:a]) source "
            "audio stream's sample_rate — audio track mirroring leaked into "
            "render's stream selection"
        )
