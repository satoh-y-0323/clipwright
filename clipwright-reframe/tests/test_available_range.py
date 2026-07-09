"""test_available_range.py — TDD Red for clipwright_reframe._add_full_clip
media_reference.available_range wiring (core otio_utils ADR-4 parity).

Context:
  clipwright.otio_utils.add_clip already wires MediaRef.available_range into
  ExternalReference.available_range when present (ADR-4, see
  tests/test_otio_utils.py::TestAvailableRangeWiring in the core package).
  clipwright_reframe.reframe._add_full_clip constructs an
  otio.schema.ExternalReference directly (it predates add_clip's
  available_range support) and never sets .available_range, so it stays None
  on every clip written by clipwright-reframe.

  Since _add_full_clip always represents the *entire* source media (a
  full-length passthrough clip, not a trimmed excerpt), the correct wiring is
  available_range == source_range (same start_time/duration): the clip's
  source_range already spans [0, duration_sec), so available_range should be
  set to that same TimeRange.

Verification points:
  A. Video clip (V1) available_range wiring
     A-1: media_reference.available_range is not None (currently unset -> Red)
     A-2: available_range.start_time == RationalTime(0, rate)
     A-3: available_range.duration == source_range.duration (full media length)
     A-4: source_range is a subset of available_range (⊆): start_time and
          end_time_exclusive of source_range fall within available_range.

  B. Audio clip (A1) available_range wiring
     B-1/B-2: shared reference quirk (documented, not a new regression) —
       _add_full_clip reuses a single ExternalReference instance across V1 and
       A1 (existing behaviour predates this test; see source_range sharing).
       As a direct consequence, once available_range is wired, A1's
       available_range will also reflect the *video-based* duration_sec, even
       though A1's own source_range is independently constructed with the
       same values. This test explicitly allows/documents that value (ADR-4
       reaffirmed) rather than asserting an audio-specific available_range.
     B-3: A1 available_range still satisfies source_range ⊆ available_range
          for the audio clip's own source_range.

  C. Non-audio media (has_audio=False)
     C-1: V1 available_range wiring still holds when no A1 clip exists.

Red rationale: _add_full_clip never sets ExternalReference.available_range,
so clip.media_reference.available_range is None for every assertion below
that expects a non-None TimeRange. This is a feature-not-implemented failure,
not a broken test (no typos/import errors expected).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_reframe.reframe import reframe
from clipwright_reframe.schemas import ReframeOptions

# ===========================================================================
# Helpers (duplicated from test_reframe.py per file-local convention)
# ===========================================================================

_FPS = 30.0
_DURATION_SEC = 10.0


def _make_media_info(
    path: str,
    *,
    has_video: bool = True,
    has_audio: bool = True,
) -> MediaInfo:
    """Build a minimal MediaInfo for monkeypatching inspect_media."""
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(StreamInfo(index=0, codec_type="video", codec_name="h264"))
    if has_audio:
        streams.append(StreamInfo(index=1, codec_type="audio", codec_name="aac"))
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(
            value=_DURATION_SEC * _FPS,
            rate=_FPS,
        ),
        streams=streams,
        bit_rate=8_000_000,
    )


def _run_reframe_and_load(
    tmp_path: Path,
    *,
    media_name: str = "video.mp4",
    output_name: str = "out.otio",
    has_audio: bool = True,
) -> tuple[dict[str, Any], otio.schema.Timeline]:
    """Run reframe() with timeline=None (create-new path -> _add_full_clip),
    then load the resulting .otio file. Returns (result, timeline)."""
    media_path = tmp_path / media_name
    media_path.write_bytes(b"dummy media")
    output_path = tmp_path / output_name
    opts = ReframeOptions(target_w=1080, target_h=1920, mode="pad")

    with patch(
        "clipwright_reframe.reframe.inspect_media",
        side_effect=lambda p: _make_media_info(str(p), has_audio=has_audio),
    ):
        result = reframe(
            media=str(media_path),
            output=str(output_path),
            options=opts,
            timeline=None,
        )

    tl = otio.adapters.read_from_file(str(output_path))
    return result, tl  # type: ignore[return-value]


def _video_clip(tl: otio.schema.Timeline) -> otio.schema.Clip:
    clips = [
        clip
        for track in tl.video_tracks()
        for clip in track
        if isinstance(clip, otio.schema.Clip)
    ]
    assert len(clips) == 1, f"Expected exactly 1 video clip, got {len(clips)}"
    return clips[0]


def _audio_clip(tl: otio.schema.Timeline) -> otio.schema.Clip:
    clips = [
        clip
        for track in tl.audio_tracks()
        for clip in track
        if isinstance(clip, otio.schema.Clip)
    ]
    assert len(clips) == 1, f"Expected exactly 1 audio clip, got {len(clips)}"
    return clips[0]


def _assert_subset(
    inner: otio.opentime.TimeRange, outer: otio.opentime.TimeRange
) -> None:
    """Assert inner (source_range) is a subset of outer (available_range): ⊆."""
    assert outer.start_time <= inner.start_time, (
        f"available_range.start_time={outer.start_time} must be <="
        f" source_range.start_time={inner.start_time}"
    )
    inner_end = inner.end_time_exclusive()
    outer_end = outer.end_time_exclusive()
    assert inner_end <= outer_end, (
        f"source_range end_time_exclusive={inner_end} must be <="
        f" available_range end_time_exclusive={outer_end}"
    )


# ===========================================================================
# A. Video clip (V1) available_range wiring
# ===========================================================================


class TestVideoAvailableRange:
    """_add_full_clip must wire media_reference.available_range for V1 (ADR-4)."""

    def test_available_range_is_not_none(self, tmp_path: Path) -> None:
        """A-1: video clip's media_reference.available_range must be set."""
        result, tl = _run_reframe_and_load(tmp_path)
        assert result["ok"] is True, f"Expected ok=True, got: {result}"
        clip = _video_clip(tl)
        assert clip.media_reference.available_range is not None, (
            "_add_full_clip must wire media_reference.available_range"
            " (currently unset — feature not implemented)."
        )

    def test_available_range_start_time_is_zero(self, tmp_path: Path) -> None:
        """A-2: available_range.start_time must be RationalTime(0, rate)."""
        _result, tl = _run_reframe_and_load(tmp_path)
        clip = _video_clip(tl)
        available_range = clip.media_reference.available_range
        assert available_range is not None
        assert available_range.start_time == otio.opentime.RationalTime(
            0.0, _FPS
        ), (
            f"available_range.start_time must be 0 at rate={_FPS},"
            f" got {available_range.start_time}"
        )

    def test_available_range_duration_matches_full_source_duration(
        self, tmp_path: Path
    ) -> None:
        """A-3: available_range.duration must equal the full source_range
        duration (full-length clip, not a trimmed excerpt)."""
        _result, tl = _run_reframe_and_load(tmp_path)
        clip = _video_clip(tl)
        available_range = clip.media_reference.available_range
        assert available_range is not None
        assert available_range.duration == clip.source_range.duration, (
            f"available_range.duration={available_range.duration} must equal"
            f" source_range.duration={clip.source_range.duration}"
            " for a full-length clip."
        )
        assert available_range.duration == otio.opentime.RationalTime(
            _DURATION_SEC * _FPS, _FPS
        )

    def test_source_range_is_subset_of_available_range(self, tmp_path: Path) -> None:
        """A-4: source_range must be a subset (⊆) of available_range."""
        _result, tl = _run_reframe_and_load(tmp_path)
        clip = _video_clip(tl)
        available_range = clip.media_reference.available_range
        assert available_range is not None
        _assert_subset(clip.source_range, available_range)


# ===========================================================================
# B. Audio clip (A1) available_range wiring — shared-reference quirk
# ===========================================================================


class TestAudioAvailableRange:
    """A1 shares the same ExternalReference instance as V1 in _add_full_clip.

    This means available_range on the audio clip will reflect the
    video-based duration_sec once wired, exactly as source_range already
    does today. This is documented and accepted (ADR-4 reaffirmed), not a
    newly introduced regression.
    """

    def test_audio_available_range_is_not_none(self, tmp_path: Path) -> None:
        """B-1: audio clip's media_reference.available_range must be set."""
        result, tl = _run_reframe_and_load(tmp_path, has_audio=True)
        assert result["ok"] is True, f"Expected ok=True, got: {result}"
        clip = _audio_clip(tl)
        assert clip.media_reference.available_range is not None, (
            "_add_full_clip must wire media_reference.available_range"
            " for the audio clip too (currently unset)."
        )

    def test_audio_available_range_reflects_video_based_duration(
        self, tmp_path: Path
    ) -> None:
        """B-2: A1 available_range duration equals the same video-based
        duration_sec as V1 (allowed shared-reference value, ADR-4 reaffirmed)."""
        _result, tl = _run_reframe_and_load(tmp_path, has_audio=True)
        video_clip = _video_clip(tl)
        audio_clip = _audio_clip(tl)
        video_available_range = video_clip.media_reference.available_range
        audio_available_range = audio_clip.media_reference.available_range
        assert video_available_range is not None
        assert audio_available_range is not None
        assert audio_available_range.duration == video_available_range.duration, (
            "A1 available_range.duration is expected to mirror V1's"
            " video-based duration_sec because _add_full_clip shares a"
            " single ExternalReference instance across V1/A1"
            " (documented, not a new regression)."
        )
        assert audio_available_range.duration == otio.opentime.RationalTime(
            _DURATION_SEC * _FPS, _FPS
        )

    def test_audio_source_range_is_subset_of_available_range(
        self, tmp_path: Path
    ) -> None:
        """B-3: audio clip's own source_range must still be a subset (⊆) of
        its available_range."""
        _result, tl = _run_reframe_and_load(tmp_path, has_audio=True)
        clip = _audio_clip(tl)
        available_range = clip.media_reference.available_range
        assert available_range is not None
        _assert_subset(clip.source_range, available_range)


# ===========================================================================
# C. Non-audio media (has_audio=False)
# ===========================================================================


class TestNoAudioAvailableRange:
    """V1 available_range wiring must hold independently of audio presence."""

    def test_video_available_range_wired_without_audio_track(
        self, tmp_path: Path
    ) -> None:
        """C-1: has_audio=False must not affect V1 available_range wiring."""
        result, tl = _run_reframe_and_load(tmp_path, has_audio=False)
        assert result["ok"] is True, f"Expected ok=True, got: {result}"

        audio_clips = [
            clip
            for track in tl.audio_tracks()
            for clip in track
            if isinstance(clip, otio.schema.Clip)
        ]
        assert len(audio_clips) == 0, (
            "No audio clip expected when has_audio=False (existing L-1 guard)."
        )

        clip = _video_clip(tl)
        available_range = clip.media_reference.available_range
        assert available_range is not None, (
            "_add_full_clip must wire media_reference.available_range"
            " even when the source media has no audio stream."
        )
        _assert_subset(clip.source_range, available_range)
