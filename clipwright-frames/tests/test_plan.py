"""test_plan.py — Tests for plan.py pure functions.

Target functions (all pure, no IO, no mocks needed):
  - compute_interval_timestamps(duration_sec, interval_sec) -> list[float]
  - compute_timestamps_mode(timestamps, duration_sec) -> (kept: list[float], skipped: list[float])
  - scene_marker_seconds(markers) -> list[float]
  - build_fps_command(ffmpeg, media, out_pattern, options) -> list[str]
  - build_single_frame_command(ffmpeg, media, ts, out_path, options) -> list[str]
  - frame_filename(index, fmt) -> str

Verification aspects:
  (A) compute_interval_timestamps
      (A-1) Normal interval: timestamps at 0, interval, 2*interval... up to < duration
      (A-2) Single frame: interval_sec > duration -> empty list
      (A-3) Exact boundary: duration is exact multiple of interval (last ts = N-1 * interval)
      (A-4) Small interval: many timestamps generated correctly
  (B) compute_timestamps_mode
      (B-1) All timestamps in range [0, duration) -> all kept, none skipped
      (B-2) Timestamp exactly at duration -> skipped
      (B-3) Negative timestamp -> skipped
      (B-4) Mix of valid and out-of-range -> correct partition
      (B-5) Deduplication: duplicate timestamps collapsed to one
      (B-6) Output is sorted (kept and skipped both sorted)
      (B-7) Empty input -> both lists empty
  (C) scene_marker_seconds
      (C-1) Single marker -> list with one float
      (C-2) Multiple markers -> sorted list of floats
      (C-3) Empty marker list -> empty list
      (C-4) Seconds = marked_range.start_time.value / rate
  (D) build_fps_command — interval mode, ffmpeg argument locked
      (D-1) -vf contains fps=1/{interval_sec} with no metacharacters
      (D-2) format=jpeg: -q:v {quality} is present in command
      (D-3) format=png: -q:v is NOT present in command
      (D-4) max_width=None: no scale in -vf
      (D-5) max_width set: -vf is "fps=1/{interval},scale='min({W},iw)':-2" (single -vf)
      (D-6) -i <media> present; out_pattern in command
      (D-7) ffmpeg path is first element
      (D-8) Numeric values (interval/quality/max_width) contain no shell metacharacters
  (E) build_single_frame_command — scene/timestamps mode, ffmpeg argument locked
      (E-1) -ss {ts} appears BEFORE -i in the command (input seeking)
      (E-2) -frames:v 1 is present
      (E-3) format=jpeg: -q:v {quality} is present
      (E-4) format=png: -q:v is NOT present
      (E-5) max_width=None: no -vf in command
      (E-6) max_width set: -vf "scale='min({W},iw)':-2" (scale only, no fps filter)
      (E-7) out_path is present in command
      (E-8) ffmpeg path is first element
  (F) frame_filename
      (F-1) format=jpeg -> "frame_00001.jpg"
      (F-2) format=png  -> "frame_00001.png"
      (F-3) Zero-padded to 5 digits: index=0 -> "frame_00000.jpg"
      (F-4) Large index: index=99999 -> "frame_99999.jpg"
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import opentimelineio as otio
import pytest

if TYPE_CHECKING:
    from clipwright_frames.schemas import ExtractFramesOptions

from clipwright_frames.plan import (
    build_fps_command,
    build_single_frame_command,
    compute_interval_timestamps,
    compute_timestamps_mode,
    frame_filename,
    scene_marker_seconds,
)

# ===========================================================================
# Helpers
# ===========================================================================

FFMPEG = "/usr/bin/ffmpeg"
MEDIA = "/fake/video.mp4"
OUT_PATTERN = "/tmp/frames/frame_%05d.jpg"
OUT_PATH = "/tmp/frames/frame_00001.jpg"
FPS = 30.0


def _opts(
    mode: str = "interval",
    interval_sec: float = 10.0,
    format: str = "jpeg",
    quality: int = 2,
    max_width: int | None = None,
    timestamps: list[float] | None = None,
    scene_timeline: str | None = None,
) -> ExtractFramesOptions:
    """Create an ExtractFramesOptions instance using the real schema."""
    from clipwright_frames.schemas import ExtractFramesOptions

    kwargs: dict[str, object] = {
        "mode": mode,
        "interval_sec": interval_sec,
        "format": format,
        "quality": quality,
        "max_width": max_width,
        "timestamps": timestamps if timestamps is not None else [],
        "scene_timeline": scene_timeline,
    }
    return ExtractFramesOptions(**kwargs)


def _make_marker(start_sec: float, rate: float = FPS) -> otio.schema.Marker:
    """Create an OTIO Marker at start_sec."""
    start = otio.opentime.RationalTime(start_sec * rate, rate)
    duration = otio.opentime.RationalTime(0.0, rate)
    marked_range = otio.opentime.TimeRange(start_time=start, duration=duration)
    marker = otio.schema.Marker(name="scene_boundary", marked_range=marked_range)
    marker.metadata["clipwright"] = {"kind": "scene_boundary"}
    return marker


# ===========================================================================
# (A) compute_interval_timestamps
# ===========================================================================


class TestComputeIntervalTimestamps:
    """Tests for compute_interval_timestamps(duration_sec, interval_sec) -> list[float]."""

    def test_a1_normal_interval(self) -> None:
        """i*interval_sec < duration for i=0,1,2 with duration=30, interval=10."""
        result = compute_interval_timestamps(duration_sec=30.0, interval_sec=10.0)
        assert result == [0.0, 10.0, 20.0]

    def test_a2_interval_exceeds_duration_returns_empty(self) -> None:
        """interval_sec > duration -> empty list (architecture §6-5: no frames + warning)."""
        result = compute_interval_timestamps(duration_sec=5.0, interval_sec=10.0)
        assert result == []

    def test_a3_exact_boundary_excludes_duration(self) -> None:
        """duration exactly divisible by interval: last ts is (N-1)*interval, not N*interval."""
        result = compute_interval_timestamps(duration_sec=20.0, interval_sec=10.0)
        # i=0 -> 0.0 < 20 OK; i=1 -> 10.0 < 20 OK; i=2 -> 20.0 < 20 False -> stop
        assert result == [0.0, 10.0]

    def test_a4_small_interval_generates_many_timestamps(self) -> None:
        """Small interval (1.0) with duration=5.0 yields 5 timestamps."""
        result = compute_interval_timestamps(duration_sec=5.0, interval_sec=1.0)
        assert result == [0.0, 1.0, 2.0, 3.0, 4.0]

    def test_a5_fractional_interval(self) -> None:
        """Fractional interval: duration=1.0, interval=0.4 -> [0.0, 0.4, 0.8]."""
        result = compute_interval_timestamps(duration_sec=1.0, interval_sec=0.4)
        assert len(result) == 3
        assert result[0] == pytest.approx(0.0)
        assert result[1] == pytest.approx(0.4)
        assert result[2] == pytest.approx(0.8)

    def test_a6_equal_duration_and_interval(self) -> None:
        """When interval == duration, only i=0 satisfies 0*interval < duration."""
        result = compute_interval_timestamps(duration_sec=10.0, interval_sec=10.0)
        assert result == [0.0]


# ===========================================================================
# (B) compute_timestamps_mode
# ===========================================================================


class TestComputeTimestampsMode:
    """Tests for compute_timestamps_mode(timestamps, duration_sec) -> (kept, skipped)."""

    def test_b1_all_in_range(self) -> None:
        """All timestamps in [0, duration) -> all kept, none skipped."""
        kept, skipped = compute_timestamps_mode([1.0, 3.0, 5.0], duration_sec=10.0)
        assert kept == [1.0, 3.0, 5.0]
        assert skipped == []

    def test_b2_timestamp_at_duration_is_skipped(self) -> None:
        """Timestamp == duration is out of range [0, duration) -> skipped."""
        kept, skipped = compute_timestamps_mode([0.0, 5.0, 10.0], duration_sec=10.0)
        assert 10.0 not in kept
        assert 10.0 in skipped

    def test_b3_negative_timestamp_is_skipped(self) -> None:
        """Negative timestamp is out of range -> skipped."""
        kept, skipped = compute_timestamps_mode([-1.0, 5.0], duration_sec=10.0)
        assert -1.0 not in kept
        assert -1.0 in skipped
        assert 5.0 in kept

    def test_b4_mixed_in_and_out_of_range(self) -> None:
        """Partition correctly: [0.0, 5.0] kept, [-1.0, 10.0, 15.0] skipped."""
        kept, skipped = compute_timestamps_mode(
            [-1.0, 0.0, 5.0, 10.0, 15.0], duration_sec=10.0
        )
        assert kept == [0.0, 5.0]
        assert skipped == [-1.0, 10.0, 15.0]

    def test_b5_deduplication(self) -> None:
        """Duplicate timestamps are collapsed to one entry in kept."""
        kept, skipped = compute_timestamps_mode([3.0, 3.0, 3.0], duration_sec=10.0)
        assert kept == [3.0]
        assert skipped == []

    def test_b6_output_is_sorted(self) -> None:
        """Both kept and skipped lists are sorted in ascending order."""
        kept, skipped = compute_timestamps_mode(
            [9.0, 1.0, 12.0, 5.0, -2.0], duration_sec=10.0
        )
        assert kept == sorted(kept)
        assert skipped == sorted(skipped)

    def test_b7_empty_input(self) -> None:
        """Empty timestamps -> both lists empty."""
        kept, skipped = compute_timestamps_mode([], duration_sec=10.0)
        assert kept == []
        assert skipped == []

    def test_b8_timestamp_zero_is_kept(self) -> None:
        """ts=0.0 is within [0, duration) -> kept."""
        kept, skipped = compute_timestamps_mode([0.0], duration_sec=5.0)
        assert kept == [0.0]
        assert skipped == []


# ===========================================================================
# (C) scene_marker_seconds
# ===========================================================================


class TestSceneMarkerSeconds:
    """Tests for scene_marker_seconds(markers) -> list[float]."""

    def test_c1_single_marker(self) -> None:
        """Single marker at 5.0s -> [5.0]."""
        marker = _make_marker(5.0)
        result = scene_marker_seconds([marker])
        assert result == pytest.approx([5.0])

    def test_c2_multiple_markers_sorted(self) -> None:
        """Multiple markers in arbitrary order -> sorted list of seconds."""
        markers = [_make_marker(8.0), _make_marker(2.0), _make_marker(5.0)]
        result = scene_marker_seconds(markers)
        assert result == pytest.approx([2.0, 5.0, 8.0])

    def test_c3_empty_list(self) -> None:
        """Empty marker list -> empty list."""
        result = scene_marker_seconds([])
        assert result == []

    def test_c4_seconds_calculated_from_value_and_rate(self) -> None:
        """Seconds = start_time.value / rate (e.g. value=150 rate=30 -> 5.0s)."""
        rate = 30.0
        start = otio.opentime.RationalTime(150.0, rate)
        duration = otio.opentime.RationalTime(0.0, rate)
        marked_range = otio.opentime.TimeRange(start_time=start, duration=duration)
        marker = otio.schema.Marker(name="m", marked_range=marked_range)
        result = scene_marker_seconds([marker])
        assert result == pytest.approx([5.0])

    def test_c5_different_rates(self) -> None:
        """Markers with different rates should each be converted correctly."""
        # marker1: value=24, rate=24 -> 1.0s
        # marker2: value=50, rate=25 -> 2.0s
        start1 = otio.opentime.RationalTime(24.0, 24.0)
        start2 = otio.opentime.RationalTime(50.0, 25.0)
        dur1 = otio.opentime.RationalTime(0.0, 24.0)
        dur2 = otio.opentime.RationalTime(0.0, 25.0)
        m1 = otio.schema.Marker(
            name="m1",
            marked_range=otio.opentime.TimeRange(start_time=start1, duration=dur1),
        )
        m2 = otio.schema.Marker(
            name="m2",
            marked_range=otio.opentime.TimeRange(start_time=start2, duration=dur2),
        )
        result = scene_marker_seconds([m2, m1])  # reversed order
        assert result == pytest.approx([1.0, 2.0])


# ===========================================================================
# (D) build_fps_command — interval mode
# ===========================================================================


class TestBuildFpsCommand:
    """Tests for build_fps_command(ffmpeg, media, out_pattern, options) -> list[str]."""

    def _cmd(
        self,
        interval_sec: float = 10.0,
        format: str = "jpeg",
        quality: int = 2,
        max_width: int | None = None,
    ) -> list[str]:
        opts = _opts(
            mode="interval",
            interval_sec=interval_sec,
            format=format,
            quality=quality,
            max_width=max_width,
        )
        return build_fps_command(FFMPEG, MEDIA, OUT_PATTERN, opts)

    def test_d1_vf_contains_fps_filter(self) -> None:
        """-vf value must contain fps=1/{interval_sec} as a numeric expression."""
        cmd = self._cmd(interval_sec=10.0)
        vf_idx = cmd.index("-vf")
        vf_val = cmd[vf_idx + 1]
        # Must start with fps=1/ followed by a decimal number
        assert re.search(r"fps=1/\d+(?:\.\d+)?", vf_val), (
            f"-vf value {vf_val!r} does not contain fps filter"
        )

    def test_d2_jpeg_has_quality_flag(self) -> None:
        """-q:v {quality} must be in the command when format=jpeg."""
        cmd = self._cmd(format="jpeg", quality=3)
        assert "-q:v" in cmd
        qv_idx = cmd.index("-q:v")
        assert cmd[qv_idx + 1] == "3"

    def test_d3_png_has_no_quality_flag(self) -> None:
        """-q:v must NOT be in the command when format=png."""
        cmd = self._cmd(format="png")
        assert "-q:v" not in cmd

    def test_d4_no_max_width_no_scale(self) -> None:
        """Without max_width, -vf value must not contain 'scale'."""
        cmd = self._cmd(max_width=None)
        vf_idx = cmd.index("-vf")
        vf_val = cmd[vf_idx + 1]
        assert "scale" not in vf_val

    def test_d5_max_width_combined_in_single_vf(self) -> None:
        """With max_width, -vf = "fps=1/{interval},scale='min({W},iw)':-2" (single -vf)."""
        cmd = self._cmd(interval_sec=10.0, max_width=640)
        # Only one -vf flag
        assert cmd.count("-vf") == 1
        vf_idx = cmd.index("-vf")
        vf_val = cmd[vf_idx + 1]
        assert "fps=1/" in vf_val
        assert "scale=" in vf_val
        assert "640" in vf_val
        assert ":-2" in vf_val

    def test_d6_media_and_out_pattern_present(self) -> None:
        """-i <media> and out_pattern must both appear in the command."""
        cmd = self._cmd()
        assert MEDIA in cmd
        assert OUT_PATTERN in cmd
        i_idx = cmd.index("-i")
        assert cmd[i_idx + 1] == MEDIA

    def test_d7_ffmpeg_is_first_element(self) -> None:
        """The first element of the command must be the ffmpeg path."""
        cmd = self._cmd()
        assert cmd[0] == FFMPEG

    def test_d8_no_metacharacters_in_numeric_values(self) -> None:
        """Numeric values embedded in -vf must not contain shell metacharacters."""
        cmd = self._cmd(interval_sec=10.0, quality=5, max_width=640)
        vf_idx = cmd.index("-vf")
        vf_val = cmd[vf_idx + 1]
        # Shell metacharacters that would enable injection
        metacharacters = set(";|&$`!><\\\"'")
        embedded_metachar = set(vf_val) & metacharacters
        # Single quotes are valid in scale='min(...)' — allow only those in the
        # expected pattern; full injection chars like ; | & $ ` etc. must be absent
        forbidden = embedded_metachar - {"'"}
        assert not forbidden, (
            f"Shell metacharacters found in -vf value: {forbidden!r} in {vf_val!r}"
        )

    def test_d9_vf_locked_by_regex_no_max_width(self) -> None:
        """-vf value must fully match expected pattern when max_width is None."""
        cmd = self._cmd(interval_sec=5.0, max_width=None)
        vf_idx = cmd.index("-vf")
        vf_val = cmd[vf_idx + 1]
        pattern = re.compile(r"^fps=1/\d+(?:\.\d+)?$")
        assert pattern.fullmatch(vf_val), (
            f"-vf value {vf_val!r} does not match expected 'fps=1/{{N}}' pattern"
        )

    def test_d10_vf_locked_by_regex_with_max_width(self) -> None:
        """-vf value must fully match expected pattern when max_width is set."""
        cmd = self._cmd(interval_sec=10.0, max_width=320)
        vf_idx = cmd.index("-vf")
        vf_val = cmd[vf_idx + 1]
        pattern = re.compile(r"^fps=1/\d+(?:\.\d+)?,scale='min\(\d+,iw\)':-2$")
        assert pattern.fullmatch(vf_val), (
            f"-vf value {vf_val!r} does not match expected combined fps+scale pattern"
        )


# ===========================================================================
# (E) build_single_frame_command — scene/timestamps mode
# ===========================================================================


class TestBuildSingleFrameCommand:
    """Tests for build_single_frame_command(ffmpeg, media, ts, out_path, options) -> list[str]."""

    def _cmd(
        self,
        ts: float = 5.0,
        format: str = "jpeg",
        quality: int = 2,
        max_width: int | None = None,
    ) -> list[str]:
        opts = _opts(
            mode="scene",
            format=format,
            quality=quality,
            max_width=max_width,
        )
        return build_single_frame_command(FFMPEG, MEDIA, ts, OUT_PATH, opts)

    def test_e1_ss_appears_before_i(self) -> None:
        """-ss {ts} must appear before -i in the command (input seeking)."""
        cmd = self._cmd(ts=5.0)
        assert "-ss" in cmd
        assert "-i" in cmd
        ss_idx = cmd.index("-ss")
        i_idx = cmd.index("-i")
        assert ss_idx < i_idx, "-ss must come before -i for input seeking"
        # SR L-3: -ss value must be a str, not a float
        assert isinstance(cmd[ss_idx + 1], str), (
            f"-ss value must be str, got {type(cmd[ss_idx + 1])!r}"
        )

    def test_e2_frames_v_1_present(self) -> None:
        """-frames:v 1 must be in the command to extract a single frame."""
        cmd = self._cmd()
        assert "-frames:v" in cmd
        fv_idx = cmd.index("-frames:v")
        assert cmd[fv_idx + 1] == "1"

    def test_e3_jpeg_has_quality_flag(self) -> None:
        """-q:v {quality} must be present when format=jpeg."""
        cmd = self._cmd(format="jpeg", quality=4)
        assert "-q:v" in cmd
        qv_idx = cmd.index("-q:v")
        assert cmd[qv_idx + 1] == "4"

    def test_e4_png_has_no_quality_flag(self) -> None:
        """-q:v must NOT be present when format=png."""
        cmd = self._cmd(format="png")
        assert "-q:v" not in cmd

    def test_e5_no_max_width_no_vf(self) -> None:
        """Without max_width, -vf must not be in the command at all."""
        cmd = self._cmd(max_width=None)
        assert "-vf" not in cmd

    def test_e6_max_width_adds_scale_only_vf(self) -> None:
        """With max_width, -vf must contain scale filter only (no fps filter)."""
        cmd = self._cmd(max_width=480)
        assert "-vf" in cmd
        vf_idx = cmd.index("-vf")
        vf_val = cmd[vf_idx + 1]
        assert "scale=" in vf_val
        assert "fps" not in vf_val
        assert "480" in vf_val
        assert ":-2" in vf_val

    def test_e7_out_path_in_command(self) -> None:
        """The output path must appear as the last positional argument."""
        cmd = self._cmd()
        assert OUT_PATH in cmd

    def test_e8_ffmpeg_is_first_element(self) -> None:
        """The first element of the command must be the ffmpeg path."""
        cmd = self._cmd()
        assert cmd[0] == FFMPEG

    def test_e9_ss_value_matches_ts(self) -> None:
        """-ss value must correspond to the provided timestamp as a str (SR L-3)."""
        ts = 7.5
        cmd = self._cmd(ts=ts)
        ss_idx = cmd.index("-ss")
        # SR L-3: ts is stored as str(ts) — element must be a str equal to str(ts)
        assert cmd[ss_idx + 1] == str(ts), (
            f"-ss value expected {str(ts)!r}, got {cmd[ss_idx + 1]!r}"
        )

    def test_e10_vf_scale_locked_by_regex(self) -> None:
        """-vf value with max_width must match 'scale=...' pattern precisely."""
        cmd = self._cmd(max_width=320)
        vf_idx = cmd.index("-vf")
        vf_val = cmd[vf_idx + 1]
        pattern = re.compile(r"^scale='min\(\d+,iw\)':-2$")
        assert pattern.fullmatch(vf_val), (
            f"-vf value {vf_val!r} does not match expected scale pattern"
        )

    def test_e11_all_elements_are_str(self) -> None:
        """SR L-3: every element of build_single_frame_command result must be str."""
        cmd = self._cmd(ts=1.5, max_width=480)
        non_str = [(i, type(v)) for i, v in enumerate(cmd) if not isinstance(v, str)]
        assert not non_str, (
            f"Non-str elements found (index, type): {non_str}"
        )


# ===========================================================================
# (F) frame_filename
# ===========================================================================


class TestFrameFilename:
    """Tests for frame_filename(index, fmt) -> str."""

    def test_f1_jpeg_extension_is_jpg(self) -> None:
        """format='jpeg' -> extension is .jpg (not .jpeg)."""
        result = frame_filename(1, "jpeg")
        assert result == "frame_00001.jpg"

    def test_f2_png_extension_is_png(self) -> None:
        """format='png' -> extension is .png."""
        result = frame_filename(1, "png")
        assert result == "frame_00001.png"

    def test_f3_zero_padded_to_5_digits(self) -> None:
        """index=0 -> 'frame_00000.jpg' (zero-padded to 5 digits)."""
        result = frame_filename(0, "jpeg")
        assert result == "frame_00000.jpg"

    def test_f4_large_index(self) -> None:
        """index=99999 -> 'frame_99999.jpg'."""
        result = frame_filename(99999, "jpeg")
        assert result == "frame_99999.jpg"

    def test_f5_prefix_is_frame_with_underscore(self) -> None:
        """All filenames start with 'frame_'."""
        result = frame_filename(42, "jpeg")
        assert result.startswith("frame_")

    def test_f6_format_string_is_exactly_05d(self) -> None:
        """The index portion uses exactly 5-digit zero padding."""
        result = frame_filename(7, "jpeg")
        # Extract the numeric part between 'frame_' and '.'
        match = re.match(r"frame_(\d+)\.", result)
        assert match is not None
        digits = match.group(1)
        assert len(digits) == 5, f"Expected 5 digits, got {len(digits)} in {result!r}"
        assert digits == "00007"
