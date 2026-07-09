"""test_available_range.py — Red tests for available_range wiring in build_sequence.

ADR-3 contract: sequence is create + sub-range + multi-source.  Each clip's
ExternalReference.available_range must describe the FULL media duration of
"that clip's own source" (TimeRange(0, probes[rc.source].duration)), never the
partial source_range that was trimmed in.  With multiple distinct sources,
each clip must look up its OWN source's available_range (no cross-source
mix-up), and every clip's source_range must be contained within its
available_range (start_time >= 0, end <= available_range.duration).

sequence.py (as of this test's authoring) constructs MediaRef(target_url=...)
without available_range, so ExternalReference.available_range stays None
after add_clip (core otio_utils.add_clip already supports available_range
via ADR-4; the wiring on the sequence.py caller side is what's missing).
This is the expected Red: available_range is None instead of the full-source
TimeRange, i.e. the *feature* (available_range population) is not implemented
yet — not a broken test (no typo/import errors).

Mocking policy: patch clipwright_sequence.sequence.inspect_media; no real
ffprobe binary is called (mirrors test_sequence.py).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_sequence.schemas import SequenceClip
from clipwright_sequence.sequence import build_sequence

# ===========================================================================
# Helpers (mirrors test_sequence.py)
# ===========================================================================


def _make_media_info(
    path: str,
    *,
    duration_sec: float,
    rate: float,
) -> MediaInfo:
    """Construct synthetic MediaInfo with a video+audio stream."""
    streams: list[StreamInfo] = [
        StreamInfo(index=0, codec_type="video", codec_name="h264"),
        StreamInfo(index=1, codec_type="audio", codec_name="aac"),
    ]
    duration = RationalTimeModel(value=duration_sec * rate, rate=rate)
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=duration,
        streams=streams,
        bit_rate=8_000_000,
    )


def _clip(
    media: str,
    start_sec: float | None = None,
    end_sec: float | None = None,
) -> SequenceClip:
    return SequenceClip(media=media, start_sec=start_sec, end_sec=end_sec)


def _v1_clips(output: str) -> list[otio.schema.Clip]:
    from clipwright.otio_utils import load_timeline

    tl = load_timeline(output)
    return [it for it in tl.tracks[0] if isinstance(it, otio.schema.Clip)]


# ===========================================================================
# Single source: available_range = full source duration, not source_range
# ===========================================================================


class TestSingleSourceAvailableRange:
    """A single-source sub-range clip must get the FULL source duration as
    available_range, independent of the trimmed-in source_range."""

    def test_available_range_is_full_source_duration(self, tmp_path: Path) -> None:
        """available_range == TimeRange(0, probe.duration) at probe.rate."""
        rate = 30.0
        duration = 10.0
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # Sub-range trim: only [2.0, 5.0) is kept in.
        clips = [_clip(media, 2.0, 5.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(media, duration_sec=duration, rate=rate),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is True
        v1_clips = _v1_clips(output)
        assert len(v1_clips) == 1
        ref = v1_clips[0].media_reference
        available_range = ref.available_range
        assert available_range is not None, (
            "available_range must be set from the source's full media duration "
            "(ADR-3); found None — the MediaRef built in sequence.py does not "
            "pass available_range to add_clip yet."
        )
        assert available_range.start_time == otio.opentime.RationalTime(
            value=0.0, rate=rate
        )
        assert available_range.duration == otio.opentime.RationalTime(
            value=duration * rate, rate=rate
        )

    def test_available_range_differs_from_source_range(self, tmp_path: Path) -> None:
        """available_range (full source) must NOT equal the trimmed-in
        source_range — proves the value is not a copy-paste of source_range
        (ADR-3: sub-range flows to source_range only)."""
        rate = 25.0
        duration = 20.0
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        clips = [_clip(media, 3.0, 8.0)]  # 5s sub-range out of 20s source
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(media, duration_sec=duration, rate=rate),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is True
        clip = _v1_clips(output)[0]
        available_range = clip.media_reference.available_range
        assert available_range is not None
        assert available_range.duration != clip.source_range.duration
        assert available_range.start_time != clip.source_range.start_time


# ===========================================================================
# Multiple sources: per-source lookup must not mix sources up
# ===========================================================================


class TestMultiSourceAvailableRange:
    """Each clip's available_range must come from ITS OWN source's probe,
    never another source's — the core risk of a per-source lookup."""

    def test_two_sources_each_clip_gets_own_source_duration(
        self, tmp_path: Path
    ) -> None:
        """media_a (10s@30fps) and media_b (20s@24fps) interleaved: clip 0/2
        (media_a) must carry media_a's available_range; clip 1 (media_b) must
        carry media_b's — never swapped."""
        media_a = str(tmp_path / "a.mp4")
        media_b = str(tmp_path / "b.mp4")
        Path(media_a).touch()
        Path(media_b).touch()
        output = str(tmp_path / "out.otio")

        rate_a, duration_a = 30.0, 10.0
        rate_b, duration_b = 24.0, 20.0

        clips = [
            _clip(media_a, 0.0, 5.0),
            _clip(media_b, 0.0, 8.0),
            _clip(media_a, 5.0, 10.0),
        ]

        def _fake_inspect(path: str) -> MediaInfo:
            if path == media_a:
                return _make_media_info(path, duration_sec=duration_a, rate=rate_a)
            if path == media_b:
                return _make_media_info(path, duration_sec=duration_b, rate=rate_b)
            raise AssertionError(f"unexpected probe target: {path}")

        with patch(
            "clipwright_sequence.sequence.inspect_media",
            side_effect=_fake_inspect,
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is True
        v1_clips = _v1_clips(output)
        assert len(v1_clips) == 3

        expected_a = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(value=0.0, rate=rate_a),
            duration=otio.opentime.RationalTime(value=duration_a * rate_a, rate=rate_a),
        )
        expected_b = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(value=0.0, rate=rate_b),
            duration=otio.opentime.RationalTime(value=duration_b * rate_b, rate=rate_b),
        )

        ar0 = v1_clips[0].media_reference.available_range
        ar1 = v1_clips[1].media_reference.available_range
        ar2 = v1_clips[2].media_reference.available_range

        assert ar0 is not None and ar0 == expected_a, (
            "clip 0 (media_a) must carry media_a's available_range"
        )
        assert ar1 is not None and ar1 == expected_b, (
            "clip 1 (media_b) must carry media_b's available_range, "
            "not media_a's (source mix-up guard)"
        )
        assert ar2 is not None and ar2 == expected_a, (
            "clip 2 (media_a again) must carry media_a's available_range, "
            "same as clip 0"
        )
        # Explicit cross-source non-mix-up guard.
        assert ar1 != ar0
        assert ar1 != ar2

    def test_repeated_source_shares_identical_available_range(
        self, tmp_path: Path
    ) -> None:
        """Two clips on the same source with DIFFERENT sub-ranges must still
        report the IDENTICAL available_range (it describes the source, not
        the per-clip trim window)."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        rate = 30.0
        duration = 12.0
        clips = [
            _clip(media, 0.0, 4.0),
            _clip(media, 4.0, 12.0),
        ]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(media, duration_sec=duration, rate=rate),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is True
        v1_clips = _v1_clips(output)
        assert len(v1_clips) == 2
        ar0 = v1_clips[0].media_reference.available_range
        ar1 = v1_clips[1].media_reference.available_range
        assert ar0 is not None
        assert ar1 is not None
        assert ar0 == ar1
        # And their source_range windows must still differ (sanity: the
        # clips really are different sub-ranges of the same source).
        assert v1_clips[0].source_range != v1_clips[1].source_range


# ===========================================================================
# Containment: source_range must be a sub-range of available_range
# ===========================================================================


class TestSourceRangeContainedInAvailableRange:
    """For every clip, source_range must be contained within available_range
    (ADR-3: sub-range semantics require the trimmed window to fit inside the
    full-source availability window)."""

    def test_source_range_within_available_range_bounds(self, tmp_path: Path) -> None:
        """start_time >= available.start_time and
        (start_time + duration) <= (available.start_time + available.duration),
        for a single mid-source sub-range clip."""
        rate = 25.0
        duration = 15.0
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        clips = [_clip(media, 4.0, 9.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(media, duration_sec=duration, rate=rate),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is True
        clip = _v1_clips(output)[0]
        available_range = clip.media_reference.available_range
        assert available_range is not None
        sr = clip.source_range
        assert sr is not None

        avail_start = available_range.start_time
        avail_end = available_range.start_time + available_range.duration
        sr_start = sr.start_time
        sr_end = sr.start_time + sr.duration

        assert sr_start >= avail_start
        assert sr_end <= avail_end

    def test_full_length_clip_source_range_exactly_fills_available_range(
        self, tmp_path: Path
    ) -> None:
        """When end_sec is omitted (full source length), source_range must
        span exactly the same window as available_range (start=0,
        duration=full source duration)."""
        rate = 30.0
        duration = 8.0
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        clips = [_clip(media, None, None)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(media, duration_sec=duration, rate=rate),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is True
        clip = _v1_clips(output)[0]
        available_range = clip.media_reference.available_range
        assert available_range is not None
        sr = clip.source_range
        assert sr is not None
        assert sr.start_time == available_range.start_time
        assert sr.duration == available_range.duration
