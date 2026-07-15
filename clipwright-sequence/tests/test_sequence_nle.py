"""test_sequence_nle.py — NLE interop conform wiring for build_sequence.

Verifies that build_sequence relays freshly built multi-source timelines
through clipwright.nle_interop.conform_timeline_for_nle (ADR-NI-8/9/10/11):

  - global_start_time is taken from the first V1 clip's source timecode
    (Resolve matches later clips per-clip by their own source TC, ADR-NI-11).
  - Every V1 clip's source_range is shifted by its own source's start timecode
    (per-clip TC shift; keyed by the exact target_url written onto the clip,
    ADR-NI-9 — a key mismatch would silently no-op, which these assertions
    would catch).
  - Audio tracks are mirrored up to N = max audio-stream count across sources;
    a source with fewer streams than N is Gap-filled at the missing positions.

All probing is mocked (inspect_media side_effect); no ffmpeg/ffprobe is
invoked. OTIO metadata is compared through _normalize_otio_value to defeat
opentimelineio 0.18.x AnyVector/AnyDictionary identity-equality (see
tests/test_nle_interop.py for the same pattern).

Architecture references:
  - architecture-report-20260715-191151.md ADR-NI-8 (SourceProbe extension),
    §5 (#9), §9 (ADR-NI-9 key match / ADR-NI-11 multi-source cross-TC).
  - requirements FR-9.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
from clipwright.otio_utils import load_timeline
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_sequence.schemas import SequenceClip
from clipwright_sequence.sequence import build_sequence

# ===========================================================================
# Constants
# ===========================================================================

FPS = 30.0
# One hour and two hours expressed in frames at 30 fps (SMPTE non-drop).
_TC_1H_FRAMES = 3600.0 * FPS  # 01:00:00:00 -> 108000
_TC_2H_FRAMES = 7200.0 * FPS  # 02:00:00:00 -> 216000


# ===========================================================================
# Helpers
# ===========================================================================


def _media_info(
    path: str,
    *,
    frames: float,
    rate: float = FPS,
    start_timecode: str | None = None,
    audio_channels: list[int] | None = None,
) -> MediaInfo:
    """Hand-build a MediaInfo (one video stream + given audio streams).

    audio_channels is a per-audio-stream channel count list, e.g. [2, 1] is
    one stereo stream followed by one mono stream.
    """
    streams: list[StreamInfo] = [
        StreamInfo(index=0, codec_type="video", codec_name="h264")
    ]
    for i, channels in enumerate(audio_channels or []):
        streams.append(
            StreamInfo(
                index=i + 1,
                codec_type="audio",
                codec_name="aac",
                channels=channels,
            )
        )
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=frames, rate=rate),
        streams=streams,
        bit_rate=8_000_000,
        start_timecode=start_timecode,
    )


def _normalize_otio_value(value: object) -> object:
    """Recursively convert OTIO AnyDictionary/AnyVector metadata to plain
    dict/list so equality checks compare content instead of identity.

    See tests/test_nle_interop.py::_normalize_otio_value for the rationale
    (AnyVector.__eq__ falls back to object identity in otio 0.18.x).
    """
    if isinstance(value, Mapping):
        return {k: _normalize_otio_value(v) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_normalize_otio_value(v) for v in value]
    return value


def _clip(media: str, start: float, end: float) -> SequenceClip:
    return SequenceClip(media=media, start_sec=start, end_sec=end)


def _v1_clips(tl: otio.schema.Timeline) -> list[otio.schema.Clip]:
    return [it for it in tl.tracks[0] if isinstance(it, otio.schema.Clip)]


def _audio_tracks(tl: otio.schema.Timeline) -> list[otio.schema.Track]:
    return [t for t in tl.tracks if t.kind == otio.schema.TrackKind.Audio]


# ===========================================================================
# 1. Multi-source, different start timecode
# ===========================================================================


class TestMultiSourceTimecode:
    """Two sources with distinct start timecodes -> per-clip TC shift and
    global_start_time from the first clip (ADR-NI-8/11)."""

    def _build(self, tmp_path: Path) -> otio.schema.Timeline:
        media_a = str(tmp_path / "a.mp4")
        media_b = str(tmp_path / "b.mp4")
        Path(media_a).touch()
        Path(media_b).touch()
        output = str(tmp_path / "out.otio")

        infos = {
            media_a: _media_info(
                media_a,
                frames=300.0,
                start_timecode="01:00:00:00",
                audio_channels=[2],
            ),
            media_b: _media_info(
                media_b,
                frames=150.0,
                start_timecode="02:00:00:00",
                audio_channels=[2],
            ),
        }

        def _fake_inspect(path: str) -> MediaInfo:
            return infos[path]

        clips = [_clip(media_a, 0.0, 5.0), _clip(media_b, 0.0, 5.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            side_effect=_fake_inspect,
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is True, f"build_sequence failed: {result}"
        # No key-match failure warning: the target_url -> MediaInfo map keys must
        # equal the target_urls written onto the clips (ADR-NI-9).
        assert not any("not found in media_infos" in w for w in result["warnings"])
        return load_timeline(output)

    def test_global_start_time_is_first_clip_timecode(self, tmp_path: Path) -> None:
        tl = self._build(tmp_path)
        gst = tl.global_start_time
        assert gst is not None
        assert gst.value == _TC_1H_FRAMES
        assert gst.rate == FPS

    def test_each_clip_shifted_by_its_own_timecode(self, tmp_path: Path) -> None:
        tl = self._build(tmp_path)
        v1 = _v1_clips(tl)
        assert len(v1) == 2
        # Clip A: start_sec 0 -> 0 + 01:00:00:00 == 108000 frames.
        assert v1[0].source_range.start_time.value == _TC_1H_FRAMES
        # Clip B: start_sec 0 -> 0 + 02:00:00:00 == 216000 frames (its OWN TC,
        # not the first clip's — proves the per-clip target_url lookup matched).
        assert v1[1].source_range.start_time.value == _TC_2H_FRAMES

    def test_available_range_also_shifted(self, tmp_path: Path) -> None:
        tl = self._build(tmp_path)
        v1 = _v1_clips(tl)
        ref_a = v1[0].media_reference
        ref_b = v1[1].media_reference
        assert isinstance(ref_a, otio.schema.ExternalReference)
        assert isinstance(ref_b, otio.schema.ExternalReference)
        assert ref_a.available_range.start_time.value == _TC_1H_FRAMES
        assert ref_b.available_range.start_time.value == _TC_2H_FRAMES


# ===========================================================================
# 2. Multi-source, different audio stream counts
# ===========================================================================


class TestMultiSourceAudioLayout:
    """Two sources with different audio-stream counts -> N = max streams audio
    tracks, with Gap fill for the source that has fewer streams (ADR-NI-10)."""

    def _build(self, tmp_path: Path) -> otio.schema.Timeline:
        # Source A has two audio streams (stereo + mono); source B has one.
        media_a = str(tmp_path / "a.mp4")
        media_b = str(tmp_path / "b.mp4")
        Path(media_a).touch()
        Path(media_b).touch()
        output = str(tmp_path / "out.otio")

        infos = {
            media_a: _media_info(media_a, frames=300.0, audio_channels=[2, 1]),
            media_b: _media_info(media_b, frames=150.0, audio_channels=[2]),
        }

        def _fake_inspect(path: str) -> MediaInfo:
            return infos[path]

        clips = [_clip(media_a, 0.0, 5.0), _clip(media_b, 0.0, 5.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            side_effect=_fake_inspect,
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is True, f"build_sequence failed: {result}"
        return load_timeline(output)

    def test_audio_track_count_is_max_streams(self, tmp_path: Path) -> None:
        """N = max audio streams across sources = 2 (source A has 2)."""
        tl = self._build(tmp_path)
        assert len(_audio_tracks(tl)) == 2

    def test_first_audio_track_mirrors_all_clips(self, tmp_path: Path) -> None:
        """Audio track for stream #0 mirrors both V1 clips (both sources have
        at least one audio stream)."""
        tl = self._build(tmp_path)
        a1 = _audio_tracks(tl)[0]
        a1_clips = [it for it in a1 if isinstance(it, otio.schema.Clip)]
        assert len(a1_clips) == 2

    def test_second_audio_track_gap_fills_missing_stream(self, tmp_path: Path) -> None:
        """Audio track for stream #1: source A contributes a Clip, source B
        (no stream #1) contributes a Gap of the same duration."""
        tl = self._build(tmp_path)
        a2 = _audio_tracks(tl)[1]
        items = list(a2)
        assert len(items) == 2
        assert isinstance(items[0], otio.schema.Clip)  # source A stream #1
        assert isinstance(items[1], otio.schema.Gap)  # source B has no stream #1
        # The Gap mirrors the V1 clip B duration (5 s at 30 fps = 150 frames).
        assert items[1].source_range.duration.value == 150.0

    def test_channels_metadata_reflects_stream_layout(self, tmp_path: Path) -> None:
        """The Resolve_OTIO Channels metadata on the stream #0 mirror of source
        A carries its 2 (stereo) channels on Source Track ID 0."""
        tl = self._build(tmp_path)
        a1 = _audio_tracks(tl)[0]
        first = next(it for it in a1 if isinstance(it, otio.schema.Clip))
        meta = _normalize_otio_value(first.metadata["Resolve_OTIO"])
        assert isinstance(meta, dict)
        assert meta["Channels"] == [
            {"Source Channel ID": 0, "Source Track ID": 0},
            {"Source Channel ID": 1, "Source Track ID": 0},
        ]
