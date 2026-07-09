"""test_available_range.py — ADR-3 available_range wiring contract.

Target: clipwright_silence.detect.detect_silence generates keep-clips whose
media_reference.available_range spans the *full media duration*, not the
clip's own source_range (sub-range tool, ADR-3).

ADR-3 contract (architecture-report-20260709-191456.md):
  - silence is a sub-range tool: source_range is a partial keep interval,
    available_range must always be the full media span
    TimeRange(start=0, duration=media_info.duration).
  - available_range is computed exactly once (from media_info.duration) and
    reused, unmodified, for every keep clip -- it must NOT be derived from
    each clip's own source_range.
  - Containment invariant: for every clip,
    source_range.start_time + source_range.duration <= available_range.duration
    (i.e. source_range is a subset of available_range).

Mocking policy mirrors test_detect.py:
  - Patch clipwright_silence.detect.inspect_media to supply MediaInfo.
  - Patch clipwright_silence.detect.resolve_tool / run to avoid real ffmpeg.

Implementation note: detect.py builds
  MediaRef(target_url=media_ref_for_otio(...), available_range=...)
with a fixed full-duration TimeRange, so ExternalReference.available_range is
wired by add_clip (core add_clip wires it when MediaRef.available_range is
not None, see otio_utils.py ADR-4).
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.otio_utils import load_timeline
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_silence.detect import detect_silence
from clipwright_silence.schemas import DetectSilenceOptions

# ===========================================================================
# Helpers (mirrors test_detect.py)
# ===========================================================================


def _make_media_info(
    path: str = "/fake/video.mp4",
    *,
    duration_sec: float = 10.0,
    rate: float = 30.0,
) -> MediaInfo:
    streams = [
        StreamInfo(index=0, codec_type="video", codec_name="h264"),
        StreamInfo(index=1, codec_type="audio", codec_name="aac"),
    ]
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=duration_sec * rate, rate=rate),
        streams=streams,
        bit_rate=8_000_000,
    )


def _make_stderr(intervals: list[tuple[float, float]]) -> str:
    lines: list[str] = []
    for start, end in intervals:
        lines.append(f"[silencedetect @ 0xabcdef] silence_start: {start:.6f}")
        lines.append(
            f"[silencedetect @ 0xabcdef] silence_end: {end:.6f} | "
            f"silence_duration: {end - start:.6f}"
        )
    return "\n".join(lines)


def _fake_run_ok(stderr: str) -> Any:
    def _impl(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        return CompletedProcess(args=cmd, returncode=0, stdout="", stderr=stderr)

    return _impl


def _opts(
    silence_threshold_db: float = -30.0,
    min_silence_duration: float = 0.5,
    padding: float = 0.0,
    min_keep_duration: float = 0.0,
) -> DetectSilenceOptions:
    return DetectSilenceOptions(
        silence_threshold_db=silence_threshold_db,
        min_silence_duration=min_silence_duration,
        padding=padding,
        min_keep_duration=min_keep_duration,
    )


def _run_detect(
    tmp_path: Path,
    intervals: list[tuple[float, float]],
    *,
    duration_sec: float = 10.0,
    rate: float = 30.0,
) -> tuple[dict[str, Any], str]:
    """Run detect_silence with mocked ffmpeg/inspect_media; return (result, output_path)."""
    media = str(tmp_path / "video.mp4")
    Path(media).touch()
    output = str(tmp_path / "out.otio")
    stderr = _make_stderr(intervals)
    media_info = _make_media_info(path=media, duration_sec=duration_sec, rate=rate)

    with (
        patch(
            "clipwright_silence.detect.inspect_media",
            return_value=media_info,
        ),
        patch(
            "clipwright_silence.detect.resolve_tool",
            side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
        ),
        patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
    ):
        result = detect_silence(media, output, _opts())

    return result, output


def _clips(output: str) -> list[otio.schema.Clip]:
    tl = load_timeline(output)
    v1 = tl.tracks[0]
    return [it for it in v1 if isinstance(it, otio.schema.Clip)]


# ===========================================================================
# ADR-3: available_range == full media duration (not source_range)
# ===========================================================================


class TestAvailableRangeIsFullMediaDuration:
    """available_range must always be TimeRange(0, media_info.duration),
    regardless of each clip's own (partial) source_range (ADR-3)."""

    def test_available_range_is_set_on_every_clip(self, tmp_path: Path) -> None:
        """media_reference.available_range must not be None (ADR-3/ADR-4 wiring)."""
        # Two silence intervals -> multiple keep clips
        result, output = _run_detect(tmp_path, [(2.0, 3.0), (6.0, 7.0)])
        assert result["ok"] is True

        clips = _clips(output)
        assert len(clips) >= 2
        for clip in clips:
            assert isinstance(clip.media_reference, otio.schema.ExternalReference)
            assert clip.media_reference.available_range is not None, (
                "available_range must be wired for keep clips (ADR-3)"
            )

    def test_available_range_matches_full_media_duration(self, tmp_path: Path) -> None:
        """available_range.start_time=0 and duration = media_info.duration
        (rate=30.0, total=10.0s -> duration.value=300.0), not the clip's
        own (shorter) source_range."""
        # Silence (3,10) -> single KEEP (0,3): source_range much shorter than
        # the full 10s media duration.
        result, output = _run_detect(
            tmp_path, [(3.0, 10.0)], duration_sec=10.0, rate=30.0
        )
        assert result["ok"] is True

        clips = _clips(output)
        assert len(clips) == 1
        clip = clips[0]
        available_range = clip.media_reference.available_range
        assert available_range is not None

        assert available_range.start_time == otio.opentime.RationalTime(
            value=0.0, rate=30.0
        )
        assert available_range.duration == otio.opentime.RationalTime(
            value=300.0, rate=30.0
        )

        # Must differ from the clip's own (partial) source_range -- proves
        # available_range is NOT derived by reusing source_range (ADR-3).
        assert available_range.duration != clip.source_range.duration

    def test_available_range_is_identical_fixed_value_across_all_clips(
        self, tmp_path: Path
    ) -> None:
        """available_range is computed once from media_info.duration and reused
        unchanged for every keep clip, even though each clip has a distinct
        (different-length) source_range."""
        # 3 silence intervals -> 4 keep clips of differing lengths
        result, output = _run_detect(
            tmp_path,
            [(1.0, 2.0), (4.0, 4.5), (7.0, 7.2)],
            duration_sec=12.0,
            rate=25.0,
        )
        assert result["ok"] is True

        clips = _clips(output)
        assert len(clips) == 4

        # Sanity: clip source_range durations differ from each other
        source_durations = {clip.source_range.duration.value for clip in clips}
        assert len(source_durations) > 1, (
            "test fixture should produce clips with differing source_range "
            "durations to prove available_range does not track source_range"
        )

        available_ranges = []
        for clip in clips:
            ar = clip.media_reference.available_range
            assert ar is not None
            available_ranges.append(ar)

        first = available_ranges[0]
        for ar in available_ranges[1:]:
            assert ar.start_time == first.start_time
            assert ar.duration == first.duration

        # And that fixed value is the full media duration (12.0s @ 25.0 -> 300.0)
        assert first.start_time == otio.opentime.RationalTime(value=0.0, rate=25.0)
        assert first.duration == otio.opentime.RationalTime(value=300.0, rate=25.0)

    def test_available_range_no_silence_single_full_clip(self, tmp_path: Path) -> None:
        """Zero-silence case: the single full-duration clip's source_range
        happens to equal the media duration, but available_range must still
        be an explicit, independently-constructed TimeRange (not merely
        coincidentally equal via source_range reuse)."""
        result, output = _run_detect(tmp_path, [], duration_sec=10.0, rate=30.0)
        assert result["ok"] is True

        clips = _clips(output)
        assert len(clips) == 1
        clip = clips[0]
        available_range = clip.media_reference.available_range
        assert available_range is not None
        assert available_range.start_time == otio.opentime.RationalTime(
            value=0.0, rate=30.0
        )
        assert available_range.duration == otio.opentime.RationalTime(
            value=300.0, rate=30.0
        )


# ===========================================================================
# ADR-3: containment invariant source_range subset-of available_range
# ===========================================================================


class TestSourceRangeSubsetOfAvailableRange:
    """For every keep clip, source_range must be contained within
    available_range: start_time + duration <= available_range.duration
    (ADR-3 core regression-prevention contract)."""

    def test_containment_holds_for_multiple_keep_clips(self, tmp_path: Path) -> None:
        result, output = _run_detect(
            tmp_path,
            [(1.5, 2.5), (5.0, 6.25), (8.9, 9.1)],
            duration_sec=10.0,
            rate=30.0,
        )
        assert result["ok"] is True

        clips = _clips(output)
        assert len(clips) >= 2
        for clip in clips:
            available_range = clip.media_reference.available_range
            assert available_range is not None
            sr = clip.source_range
            assert sr is not None

            # Same rate assumption holds throughout this tool (single media, single rate)
            assert sr.start_time.rate == pytest.approx(available_range.duration.rate)

            sr_end_value = sr.start_time.value + sr.duration.value
            available_end_value = (
                available_range.start_time.value + available_range.duration.value
            )
            assert sr.start_time.value >= available_range.start_time.value
            assert sr_end_value <= available_end_value, (
                f"clip source_range end {sr_end_value} exceeds "
                f"available_range end {available_end_value} (ADR-3 violation)"
            )

    def test_containment_holds_for_trailing_keep_clip_near_media_end(
        self, tmp_path: Path
    ) -> None:
        """A keep clip that runs up to the very end of the media must still
        satisfy the containment invariant exactly (no off-by-one overrun)."""
        # Silence (0, 4) -> single KEEP (4.0, 10.0), touching media end exactly.
        result, output = _run_detect(
            tmp_path, [(0.0, 4.0)], duration_sec=10.0, rate=30.0
        )
        assert result["ok"] is True

        clips = _clips(output)
        assert len(clips) == 1
        clip = clips[0]
        available_range = clip.media_reference.available_range
        assert available_range is not None
        sr = clip.source_range
        sr_end_value = sr.start_time.value + sr.duration.value
        available_end_value = (
            available_range.start_time.value + available_range.duration.value
        )
        assert sr_end_value == pytest.approx(available_end_value)
