"""test_sequence.py — Tests for clipwright_sequence.sequence.build_sequence.

Verifies the build_sequence orchestration layer: happy-path OTIO assembly,
error envelopes, path security, dedup, tolerance, and co-location enforcement.

Architecture references:
  - architecture-report-20260621-205501.md §4 ADR-SEQ-4
  - §5 ADR-SEQ-5 (OTIO build)
  - §6 ADR-SEQ-6 (co-location)
  - §V2.1 (DC-AS-001 single-resolve, target_url)
  - §V2.2 (DC-AS-002 probe->duration->rate->has_video->colocation->output!=source)
  - §V2.3 (DC-AS-003 tolerance 1 frame)
  - §V2.4 (DC-AS-004 rate>=1000 INVALID_INPUT)
  - §V2.6 (DC-AM-002 resolved-key dedup)
  - §V2.8 (DC-AM-001 output==source PATH_NOT_ALLOWED)
  - §V2.9 (DC-AM-003 approx total)
  - §V2.10 (DC-AM-004/005 transparent core ErrorCodes)
  - §V2.11 (DC-GP-002 OSError fallback / CWE-209)
  - §V2.12 (DC-GP-003 clips>1000 INVALID_INPUT)

Mocking policy:
  - Patch clipwright_sequence.sequence.inspect_media with synthetic MediaInfo.
  - No real ffprobe binary is called.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.otio_utils import load_timeline
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_sequence.schemas import SequenceClip
from clipwright_sequence.sequence import build_sequence

# ===========================================================================
# Constants
# ===========================================================================

FPS = 30.0
DURATION_SEC = 10.0
VERSION = "0.1.0"

# ===========================================================================
# Helpers
# ===========================================================================


def _make_media_info(
    path: str = "/fake/video.mp4",
    *,
    duration_sec: float | None = DURATION_SEC,
    rate: float = FPS,
    has_video: bool = True,
) -> MediaInfo:
    """Construct synthetic MediaInfo for mocking inspect_media.

    When has_video=False the only stream is audio (codec_type='audio').
    When rate >= 1000.0 we still include a video stream but the duration
    carries the sentinel rate — matching the media.py audio-only fallback path.
    """
    if has_video:
        streams: list[StreamInfo] = [
            StreamInfo(index=0, codec_type="video", codec_name="h264"),
            StreamInfo(index=1, codec_type="audio", codec_name="aac"),
        ]
    else:
        streams = [
            StreamInfo(index=0, codec_type="audio", codec_name="aac"),
        ]
    duration = (
        RationalTimeModel(value=duration_sec * rate, rate=rate)
        if duration_sec is not None
        else None
    )
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
    """Build a SequenceClip for tests."""
    return SequenceClip(media=media, start_sec=start_sec, end_sec=end_sec)


def _make_clips(tmp_path: Path, n: int) -> list[SequenceClip]:
    """Create n SequenceClip objects that all reference the same media file."""
    media = str(tmp_path / "source.mp4")
    return [_clip(media) for _ in range(n)]


def _media_path(tmp_path: Path, name: str = "video.mp4") -> str:
    return str(tmp_path / name)


def _output_path(tmp_path: Path, name: str = "out.otio") -> str:
    return str(tmp_path / name)


# ===========================================================================
# Happy path: 2-3 clips across >=1 source -> ok_result
# ===========================================================================


class TestHappyPath:
    """build_sequence succeeds with multiple clips and returns a valid OTIO timeline."""

    def test_two_clips_single_source_ok(self, tmp_path: Path) -> None:
        """2 clips from the same source -> ok=True."""
        media = _media_path(tmp_path)
        Path(media).touch()
        output = _output_path(tmp_path)
        clips = [
            _clip(media, start_sec=0.0, end_sec=5.0),
            _clip(media, start_sec=5.0, end_sec=10.0),
        ]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is True

    def test_three_clips_two_sources_ok(self, tmp_path: Path) -> None:
        """3 clips across 2 sources -> ok=True."""
        media_a = _media_path(tmp_path, "a.mp4")
        media_b = _media_path(tmp_path, "b.mp4")
        Path(media_a).touch()
        Path(media_b).touch()
        output = _output_path(tmp_path)
        clips = [
            _clip(media_a, start_sec=0.0, end_sec=5.0),
            _clip(media_b, start_sec=0.0, end_sec=5.0),
            _clip(media_a, start_sec=5.0, end_sec=10.0),
        ]

        def _fake_inspect(path: str) -> MediaInfo:
            return _make_media_info(path=path)

        with patch(
            "clipwright_sequence.sequence.inspect_media",
            side_effect=_fake_inspect,
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is True

    def test_v1_track_is_video(self, tmp_path: Path) -> None:
        """OTIO tracks[0] is a Video track (V1)."""
        media = _media_path(tmp_path)
        Path(media).touch()
        output = _output_path(tmp_path)
        clips = [_clip(media, 0.0, 5.0), _clip(media, 5.0, 10.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            build_sequence(clips=clips, output=output)

        tl = load_timeline(output)
        assert tl.tracks[0].kind == otio.schema.TrackKind.Video

    def test_v1_clip_count_matches_input(self, tmp_path: Path) -> None:
        """N input clips -> N ExternalReference clips in V1 (enumeration order)."""
        media = _media_path(tmp_path)
        Path(media).touch()
        output = _output_path(tmp_path)
        clips = [
            _clip(media, 0.0, 3.0),
            _clip(media, 3.0, 7.0),
            _clip(media, 7.0, 10.0),
        ]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            build_sequence(clips=clips, output=output)

        tl = load_timeline(output)
        v1_clips = [it for it in tl.tracks[0] if isinstance(it, otio.schema.Clip)]
        assert len(v1_clips) == 3

    def test_a1_track_is_empty(self, tmp_path: Path) -> None:
        """A1 track (tracks[1]) must be empty (DC-AS-006)."""
        media = _media_path(tmp_path)
        Path(media).touch()
        output = _output_path(tmp_path)
        clips = [_clip(media, 0.0, 5.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            build_sequence(clips=clips, output=output)

        tl = load_timeline(output)
        a1_clips = [it for it in tl.tracks[1] if isinstance(it, otio.schema.Clip)]
        assert len(a1_clips) == 0

    def test_clips_are_external_references(self, tmp_path: Path) -> None:
        """Each clip in V1 must be an ExternalReference (render requirement)."""
        media = _media_path(tmp_path)
        Path(media).touch()
        output = _output_path(tmp_path)
        clips = [_clip(media, 0.0, 5.0), _clip(media, 5.0, 10.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            build_sequence(clips=clips, output=output)

        tl = load_timeline(output)
        v1_clips = [it for it in tl.tracks[0] if isinstance(it, otio.schema.Clip)]
        for clip in v1_clips:
            assert isinstance(clip.media_reference, otio.schema.ExternalReference)

    def test_source_range_value_encodes_sec_times_rate(self, tmp_path: Path) -> None:
        """source_range start/duration values encode sec*rate (ADR-SEQ-5)."""
        rate = 30.0
        media = _media_path(tmp_path)
        Path(media).touch()
        output = _output_path(tmp_path)
        clips = [_clip(media, 2.0, 5.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media, rate=rate),
        ):
            build_sequence(clips=clips, output=output)

        tl = load_timeline(output)
        v1_clips = [it for it in tl.tracks[0] if isinstance(it, otio.schema.Clip)]
        assert len(v1_clips) == 1
        sr = v1_clips[0].source_range
        assert sr is not None
        expected_start = otio.opentime.RationalTime(value=2.0 * rate, rate=rate)
        expected_dur = otio.opentime.RationalTime(value=3.0 * rate, rate=rate)
        assert sr.start_time == expected_start
        assert sr.duration == expected_dur

    def test_clip_metadata_clipwright_keys(self, tmp_path: Path) -> None:
        """Each clip's metadata['clipwright'] contains tool/version/kind/index."""
        media = _media_path(tmp_path)
        Path(media).touch()
        output = _output_path(tmp_path)
        clips = [_clip(media, 0.0, 5.0), _clip(media, 5.0, 10.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            build_sequence(clips=clips, output=output)

        tl = load_timeline(output)
        v1_clips = [it for it in tl.tracks[0] if isinstance(it, otio.schema.Clip)]
        for i, clip in enumerate(v1_clips):
            cw = clip.metadata.get("clipwright")
            assert cw is not None, f"metadata['clipwright'] missing on clip {i}"
            assert cw["tool"] == "clipwright_build_sequence"
            assert cw["version"] == VERSION
            assert cw["kind"] == "sequence_clip"
            assert cw["index"] == i

    def test_clips_in_enumeration_order(self, tmp_path: Path) -> None:
        """Clips in V1 appear in the same order as the input clips list."""
        media_a = _media_path(tmp_path, "a.mp4")
        media_b = _media_path(tmp_path, "b.mp4")
        Path(media_a).touch()
        Path(media_b).touch()
        output = _output_path(tmp_path)
        clips = [
            _clip(media_a, 0.0, 5.0),
            _clip(media_b, 0.0, 3.0),
            _clip(media_a, 5.0, 10.0),
        ]

        resolved_a = str(Path(media_a).resolve())
        resolved_b = str(Path(media_b).resolve())

        def _fake_inspect(path: str) -> MediaInfo:
            return _make_media_info(path=path)

        with patch(
            "clipwright_sequence.sequence.inspect_media",
            side_effect=_fake_inspect,
        ):
            build_sequence(clips=clips, output=output)

        tl = load_timeline(output)
        v1_clips = [it for it in tl.tracks[0] if isinstance(it, otio.schema.Clip)]
        assert len(v1_clips) == 3
        assert v1_clips[0].media_reference.target_url == resolved_a
        assert v1_clips[1].media_reference.target_url == resolved_b
        assert v1_clips[2].media_reference.target_url == resolved_a


# ===========================================================================
# data / summary envelope (DC-AM-003)
# ===========================================================================


class TestEnvelope:
    """Verify ok_result envelope: data fields, summary, artifacts, warnings."""

    def test_data_contains_clip_count(self, tmp_path: Path) -> None:
        """data must contain clip_count matching the number of input clips."""
        media = _media_path(tmp_path)
        Path(media).touch()
        output = _output_path(tmp_path)
        clips = [_clip(media, 0.0, 5.0), _clip(media, 5.0, 10.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is True
        assert result["data"]["clip_count"] == 2

    def test_data_contains_total_duration_sec(self, tmp_path: Path) -> None:
        """data must contain total_duration_sec as sum of clip ranges."""
        media = _media_path(tmp_path)
        Path(media).touch()
        output = _output_path(tmp_path)
        clips = [
            _clip(media, 0.0, 4.0),  # 4s
            _clip(media, 5.0, 8.0),  # 3s
        ]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is True
        assert result["data"]["total_duration_sec"] == pytest.approx(7.0, abs=1e-6)

    def test_data_contains_unique_source_count(self, tmp_path: Path) -> None:
        """data must contain unique_source_count based on resolved-path dedup."""
        media_a = _media_path(tmp_path, "a.mp4")
        media_b = _media_path(tmp_path, "b.mp4")
        Path(media_a).touch()
        Path(media_b).touch()
        output = _output_path(tmp_path)
        # 3 clips but only 2 unique sources
        clips = [
            _clip(media_a, 0.0, 5.0),
            _clip(media_b, 0.0, 5.0),
            _clip(media_a, 5.0, 10.0),
        ]

        def _fake_inspect(path: str) -> MediaInfo:
            return _make_media_info(path=path)

        with patch(
            "clipwright_sequence.sequence.inspect_media",
            side_effect=_fake_inspect,
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is True
        assert result["data"]["unique_source_count"] == 2

    def test_summary_contains_approx_total(self, tmp_path: Path) -> None:
        """summary must contain 'approx total' (DC-AM-003)."""
        media = _media_path(tmp_path)
        Path(media).touch()
        output = _output_path(tmp_path)
        clips = [_clip(media, 0.0, 5.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is True
        assert "approx total" in result["summary"]

    def test_artifacts_contain_timeline_otio(self, tmp_path: Path) -> None:
        """artifacts must contain one entry with role='timeline' and format='otio'."""
        media = _media_path(tmp_path)
        Path(media).touch()
        output = _output_path(tmp_path)
        clips = [_clip(media, 0.0, 5.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is True
        artifacts = result["artifacts"]
        tl_arts = [
            a
            for a in artifacts
            if (
                (isinstance(a, dict) and a.get("role") == "timeline")
                or (hasattr(a, "role") and a.role == "timeline")
            )
        ]
        assert len(tl_arts) == 1
        art = tl_arts[0]
        fmt = art.get("format") if isinstance(art, dict) else art.format
        assert fmt == "otio"


# ===========================================================================
# DC-AS-001: co-location (single resolve; target_url = Path(media).resolve())
# ===========================================================================


class TestColocation:
    """DC-AS-001: co-location against output.parent; target_url = resolved path."""

    def test_source_in_output_dir_allowed(self, tmp_path: Path) -> None:
        """Source in the same directory as output -> ok=True."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        clips = [_clip(media, 0.0, 5.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is True

    def test_source_in_subdir_of_output_dir_allowed(self, tmp_path: Path) -> None:
        """Source in a recursive subdirectory of output.parent -> ok=True (ADR-SEQ-6)."""
        subdir = tmp_path / "sub" / "deep"
        subdir.mkdir(parents=True)
        media = str(subdir / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        clips = [_clip(media, 0.0, 5.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is True

    def test_source_outside_output_dir_path_not_allowed(self, tmp_path: Path) -> None:
        """Source outside output.parent tree -> PATH_NOT_ALLOWED."""
        outside = tmp_path / "outside"
        outside.mkdir()
        media = str(outside / "video.mp4")
        Path(media).touch()
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        output = str(project_dir / "out.otio")
        clips = [_clip(media, 0.0, 5.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED

    def test_target_url_equals_resolved_path(self, tmp_path: Path) -> None:
        """target_url in OTIO equals Path(media).resolve() (DC-AS-001 single-resolve)."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        clips = [_clip(media, 0.0, 5.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            build_sequence(clips=clips, output=output)

        tl = load_timeline(output)
        v1_clips = [it for it in tl.tracks[0] if isinstance(it, otio.schema.Clip)]
        expected_url = str(Path(media).resolve())
        for clip in v1_clips:
            assert clip.media_reference.target_url == expected_url


# ===========================================================================
# DC-AS-002: precedence — nonexistent + outside-tree -> FILE_NOT_FOUND first
# ===========================================================================


class TestPrecedence:
    """DC-AS-002: probe runs before co-location; nonexistent path -> FILE_NOT_FOUND."""

    def test_nonexistent_outside_tree_returns_file_not_found(
        self, tmp_path: Path
    ) -> None:
        """Nonexistent path that is also outside the project tree -> FILE_NOT_FOUND.

        probe runs first (DC-AS-002), so FILE_NOT_FOUND takes precedence over
        PATH_NOT_ALLOWED.
        """
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        # Path does not exist and is outside project_dir
        missing = str(tmp_path / "missing_outside.mp4")
        output = str(project_dir / "out.otio")
        clips = [_clip(missing, 0.0, 5.0)]

        with patch(
            "clipwright_sequence.sequence.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"File not found: {Path(missing).name}",
                hint="Check that the path is correct and the file exists.",
            ),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is False
        # Must be FILE_NOT_FOUND, NOT PATH_NOT_ALLOWED
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND


# ===========================================================================
# CWE-209 regression: FILE_NOT_FOUND message must not expose full path
# ===========================================================================


class TestFileNotFoundPathNonExposure:
    """SR H-1 / SR L-3 regression: FILE_NOT_FOUND message must not contain the
    absolute path from the core _validate_existing_file (CWE-209)."""

    def test_file_not_found_message_does_not_expose_full_path(
        self, tmp_path: Path
    ) -> None:
        """FILE_NOT_FOUND error message must not contain the absolute path (CWE-209).

        Patches inspect_media to raise the real core format — which includes the full
        path in the message — and asserts that build_sequence sanitizes it so that the
        absolute path is not present in the returned error message.
        """
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        media = str(tmp_path / "missing.mp4")
        output = str(project_dir / "out.otio")
        clips = [_clip(media, 0.0, 5.0)]

        with patch(
            "clipwright_sequence.sequence.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"Symbolic links are not accepted: {media}",  # real core format
                hint="Specify the path to a real file, not a symbolic link.",
            ),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND
        error_msg = result["error"]["message"]
        assert str(tmp_path) not in error_msg, (
            "FILE_NOT_FOUND message must not expose the absolute path (CWE-209)"
        )


# ===========================================================================
# DC-AS-004: rate sentinel (rate >= 1000.0 with has_video=True) -> INVALID_INPUT
# ===========================================================================


class TestRateSentinel:
    """DC-AS-004: video source with probe rate >= 1000.0 -> INVALID_INPUT."""

    def test_rate_sentinel_invalid_input(self, tmp_path: Path) -> None:
        """Source with has_video=True and rate=1000.0 (sentinel) -> INVALID_INPUT."""
        media = _media_path(tmp_path)
        Path(media).touch()
        output = _output_path(tmp_path)
        clips = [_clip(media, 0.0, 5.0)]
        # rate=1000.0 is the sentinel produced by media.py when avg_frame_rate is 0/0
        sentinel_info = _make_media_info(
            path=media, duration_sec=DURATION_SEC, rate=1000.0, has_video=True
        )
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=sentinel_info,
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_rate_sentinel_message_mentions_frame_rate(self, tmp_path: Path) -> None:
        """INVALID_INPUT message for rate sentinel mentions frame rate."""
        media = _media_path(tmp_path)
        Path(media).touch()
        output = _output_path(tmp_path)
        clips = [_clip(media, 0.0, 5.0)]
        sentinel_info = _make_media_info(
            path=media, duration_sec=DURATION_SEC, rate=1000.0, has_video=True
        )
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=sentinel_info,
        ):
            result = build_sequence(clips=clips, output=output)

        msg = result["error"]["message"].lower()
        assert "frame rate" in msg or "rate" in msg


# ===========================================================================
# has_video=False (audio-only) -> INVALID_INPUT
# ===========================================================================


class TestAudioOnly:
    """Audio-only source (has_video=False) -> INVALID_INPUT."""

    def test_audio_only_returns_invalid_input(self, tmp_path: Path) -> None:
        """Source with no video stream -> INVALID_INPUT."""
        media = _media_path(tmp_path)
        Path(media).touch()
        output = _output_path(tmp_path)
        clips = [_clip(media, 0.0, 5.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media, has_video=False),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT


# ===========================================================================
# duration=None -> PROBE_FAILED (transparent)
# ===========================================================================


class TestDurationNone:
    """duration=None from probe -> PROBE_FAILED (DC-AM-005 transparent)."""

    def test_duration_none_returns_probe_failed(self, tmp_path: Path) -> None:
        """MediaInfo.duration is None -> PROBE_FAILED."""
        media = _media_path(tmp_path)
        Path(media).touch()
        output = _output_path(tmp_path)
        clips = [_clip(media, 0.0, 5.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=None),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PROBE_FAILED


# ===========================================================================
# DC-AM-001: output == source (resolved equal) -> PATH_NOT_ALLOWED
# ===========================================================================


class TestOutputEqualsSource:
    """DC-AM-001: output == source path (resolved equal) -> PATH_NOT_ALLOWED."""

    def test_output_same_as_source_path_not_allowed(self, tmp_path: Path) -> None:
        """output resolved path equal to source resolved path -> PATH_NOT_ALLOWED."""
        # Use .otio extension so the source passes extension check
        media = str(tmp_path / "video.otio")
        Path(media).touch()
        output = media  # same path
        clips = [_clip(media, 0.0, 5.0)]

        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED

    def test_output_same_as_source_message_and_hint_present(
        self, tmp_path: Path
    ) -> None:
        """output==source error has non-empty message and hint."""
        media = str(tmp_path / "video.otio")
        Path(media).touch()
        output = media
        clips = [_clip(media, 0.0, 5.0)]

        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is False
        assert (
            isinstance(result["error"]["message"], str) and result["error"]["message"]
        )
        assert isinstance(result["error"]["hint"], str) and result["error"]["hint"]


# ===========================================================================
# DC-AM-002: same file via different spelling -> probe called ONCE
# ===========================================================================


class TestResolvedKeyDedup:
    """DC-AM-002: different spellings of the same file -> probe once, unique_source=1."""

    def test_same_file_different_spelling_probe_once(self, tmp_path: Path) -> None:
        """./a.mp4 and a.mp4 resolve to the same path -> inspect_media called once."""
        media_abs = str(tmp_path / "a.mp4")
        Path(media_abs).touch()

        # Use CWD-relative spellings that resolve to the same file
        original_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            media_rel1 = "./a.mp4"
            media_rel2 = "a.mp4"
            output = str(tmp_path / "out.otio")
            clips = [_clip(media_rel1, 0.0, 5.0), _clip(media_rel2, 5.0, 10.0)]

            probe_calls: list[str] = []

            def _tracking_inspect(path: str) -> MediaInfo:
                probe_calls.append(path)
                return _make_media_info(path=media_abs)

            with patch(
                "clipwright_sequence.sequence.inspect_media",
                side_effect=_tracking_inspect,
            ):
                result = build_sequence(clips=clips, output=output)
        finally:
            os.chdir(original_cwd)

        assert result["ok"] is True
        # inspect_media must have been called exactly once (resolved-key dedup)
        assert len(probe_calls) == 1

    def test_same_file_different_spelling_unique_source_count_one(
        self, tmp_path: Path
    ) -> None:
        """Same file via different spellings -> unique_source_count=1 in data."""
        media_abs = str(tmp_path / "a.mp4")
        Path(media_abs).touch()

        original_cwd = os.getcwd()
        try:
            os.chdir(str(tmp_path))
            media_rel1 = "./a.mp4"
            media_rel2 = "a.mp4"
            output = str(tmp_path / "out.otio")
            clips = [_clip(media_rel1, 0.0, 5.0), _clip(media_rel2, 5.0, 10.0)]

            with patch(
                "clipwright_sequence.sequence.inspect_media",
                return_value=_make_media_info(path=media_abs),
            ):
                result = build_sequence(clips=clips, output=output)
        finally:
            os.chdir(original_cwd)

        assert result["ok"] is True
        assert result["data"]["unique_source_count"] == 1


# ===========================================================================
# DC-GP-003: clips length 1001 -> INVALID_INPUT
# ===========================================================================


class TestClipsLengthLimit:
    """DC-GP-003: clips > 1000 -> INVALID_INPUT with hint about 1000 limit."""

    def test_1001_clips_returns_invalid_input(self, tmp_path: Path) -> None:
        """1001 clips -> INVALID_INPUT."""
        media = _media_path(tmp_path)
        Path(media).touch()
        output = _output_path(tmp_path)
        # Build 1001 clips using sub-ranges so each clip is valid on its own
        # (we don't need inspect_media to be called for this fast-fail)
        clips = [
            SequenceClip(media=media, start_sec=float(i), end_sec=float(i + 1))
            for i in range(1001)
        ]

        result = build_sequence(clips=clips, output=output)

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_1001_clips_hint_mentions_1000(self, tmp_path: Path) -> None:
        """INVALID_INPUT hint for clips>1000 mentions 'at most 1000'."""
        media = _media_path(tmp_path)
        Path(media).touch()
        output = _output_path(tmp_path)
        clips = [
            SequenceClip(media=media, start_sec=float(i), end_sec=float(i + 1))
            for i in range(1001)
        ]

        result = build_sequence(clips=clips, output=output)

        assert result["ok"] is False
        hint = result["error"]["hint"].lower()
        assert "1000" in hint


# ===========================================================================
# Empty clips, wrong extension, missing parent dir -> INVALID_INPUT
# ===========================================================================


class TestFastFailValidation:
    """Fast-fail checks before inspect_media: empty clips, extension, parent dir."""

    def test_empty_clips_returns_invalid_input(self, tmp_path: Path) -> None:
        """Empty clips list -> INVALID_INPUT (before inspect_media)."""
        output = _output_path(tmp_path)
        inspect_called: list[str] = []

        with patch(
            "clipwright_sequence.sequence.inspect_media",
            side_effect=lambda p: inspect_called.append(p),
        ):
            result = build_sequence(clips=[], output=output)

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT
        assert len(inspect_called) == 0

    def test_non_otio_extension_returns_invalid_input(self, tmp_path: Path) -> None:
        """output extension != .otio -> INVALID_INPUT (before inspect_media)."""
        media = _media_path(tmp_path)
        Path(media).touch()
        output = str(tmp_path / "out.mp4")
        clips = [_clip(media, 0.0, 5.0)]
        inspect_called: list[str] = []

        with patch(
            "clipwright_sequence.sequence.inspect_media",
            side_effect=lambda p: inspect_called.append(p),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT
        assert len(inspect_called) == 0

    def test_missing_output_parent_dir_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """output parent directory missing -> INVALID_INPUT (before inspect_media)."""
        media = _media_path(tmp_path)
        Path(media).touch()
        output = str(tmp_path / "nonexistent_dir" / "out.otio")
        clips = [_clip(media, 0.0, 5.0)]
        inspect_called: list[str] = []

        with patch(
            "clipwright_sequence.sequence.inspect_media",
            side_effect=lambda p: inspect_called.append(p),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT
        assert len(inspect_called) == 0


# ===========================================================================
# DC-GP-002: OSError fallback in co-location / PATH_NOT_ALLOWED message (CWE-209)
# ===========================================================================


class TestOsErrorFallback:
    """DC-GP-002: resolve() OSError -> absolute() comparison; message hides full path."""

    def test_oserror_fallback_outside_tree_still_rejected(self, tmp_path: Path) -> None:
        """When Path.resolve() raises OSError, absolute() comparison rejects outside source."""
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        media = str(outside_dir / "video.mp4")
        Path(media).touch()
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        output = str(project_dir / "out.otio")
        clips = [_clip(media, 0.0, 5.0)]

        original_resolve = Path.resolve

        def _patched_resolve(self: Path, strict: bool = False) -> Path:
            # Raise OSError for source paths to trigger fallback
            if str(self).endswith(".mp4"):
                raise OSError("simulated network path failure")
            return original_resolve(self, strict=strict)

        with (
            patch(
                "clipwright_sequence.sequence.inspect_media",
                return_value=_make_media_info(path=media),
            ),
            patch.object(Path, "resolve", _patched_resolve),
        ):
            result = build_sequence(clips=clips, output=output)

        # After OSError fallback, outside source must still be rejected
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED

    def test_path_not_allowed_message_hides_full_path(self, tmp_path: Path) -> None:
        """PATH_NOT_ALLOWED message must NOT contain full path (CWE-209 / DC-GP-002)."""
        outside = tmp_path / "outside"
        outside.mkdir()
        media = str(outside / "video.mp4")
        Path(media).touch()
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        output = str(project_dir / "out.otio")
        clips = [_clip(media, 0.0, 5.0)]

        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED
        error_msg = result["error"]["message"]
        # Must NOT contain full path components (CWE-209)
        assert str(tmp_path) not in error_msg
        assert str(outside) not in error_msg
        # Must contain fixed "boundary" wording
        assert "boundary" in error_msg.lower() or "outside" in error_msg.lower()

    def test_oserror_fallback_subdir_still_allowed(self, tmp_path: Path) -> None:
        """When Path.resolve() raises OSError, absolute() comparison allows subdirectory."""
        subdir = tmp_path / "sub"
        subdir.mkdir()
        media = str(subdir / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        clips = [_clip(media, 0.0, 5.0)]

        original_resolve = Path.resolve

        def _patched_resolve(self: Path, strict: bool = False) -> Path:
            if str(self).endswith(".mp4"):
                raise OSError("simulated failure")
            return original_resolve(self, strict=strict)

        with (
            patch(
                "clipwright_sequence.sequence.inspect_media",
                return_value=_make_media_info(path=media),
            ),
            patch.object(Path, "resolve", _patched_resolve),
        ):
            result = build_sequence(clips=clips, output=output)

        # Subdirectory source must be allowed even under OSError fallback
        assert result["ok"] is True


# ===========================================================================
# DC-AM-004/005: inspect_media raising core ErrorCodes -> transparent
# ===========================================================================


class TestTransparentCoreErrors:
    """DC-AM-004/005: DEPENDENCY_MISSING / SUBPROCESS_FAILED / SUBPROCESS_TIMEOUT
    from inspect_media are returned transparently (not swallowed)."""

    @pytest.mark.parametrize(
        "code",
        [
            ErrorCode.DEPENDENCY_MISSING,
            ErrorCode.SUBPROCESS_FAILED,
            ErrorCode.SUBPROCESS_TIMEOUT,
        ],
    )
    def test_inspect_media_error_transparent(
        self, code: ErrorCode, tmp_path: Path
    ) -> None:
        """inspect_media raising <code> -> same code returned in error envelope."""
        media = _media_path(tmp_path)
        Path(media).touch()
        output = _output_path(tmp_path)
        clips = [_clip(media, 0.0, 5.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            side_effect=ClipwrightError(
                code=code,
                message=f"Simulated {code}",
                hint="Hint for the error.",
            ),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is False
        assert result["error"]["code"] == code


# ===========================================================================
# DC-AS-003: tolerance — end within 1 frame over duration -> ok, clipped
# ===========================================================================


class TestToleranceIntegration:
    """DC-AS-003: end within 1 frame beyond duration is accepted (integration case)."""

    def test_end_within_one_frame_over_duration_ok(self, tmp_path: Path) -> None:
        """end_sec = duration + (0.5/rate) -> ok=True (probe-error absorption)."""
        rate = 30.0
        duration = 10.0
        media = _media_path(tmp_path)
        Path(media).touch()
        output = _output_path(tmp_path)
        half_frame = 0.5 / rate
        clips = [_clip(media, 0.0, duration + half_frame)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=duration, rate=rate),
        ):
            result = build_sequence(clips=clips, output=output)

        assert result["ok"] is True
