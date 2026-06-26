"""test_detect.py — Red tests for detect.py orchestration.

Target API:
  clipwright_silence.detect.detect_silence(
      media: str,
      output: str,
      options: DetectSilenceOptions,
  ) -> dict

Mocking policy:
  - Patch clipwright_silence.detect.inspect_media to supply MediaInfo.
  - Patch clipwright_silence.detect.run to control silencedetect stderr.
  - No real ffmpeg/ffprobe binaries are called.

Verification aspects (DC-AS-001-005 / DC-AM-002/003):
  (1) silencedetect stderr parsing (regex, line-start match, '.' fixed decimal, DC-AM-003)
  (2) Trailing silence with missing silence_end -> completed with total_duration (DC-AM-002)
  (3) KEEP clip list (V1, source_range rate, target_url, metadata) (DC-AS-001/003)
  (4) Input validation error group (DC-AS-001/002/004)
  (5) Envelope format (ok/summary/data/artifacts)
  (6) Edge cases: all silence, zero silence
  (7) Non-destructive, basename only (no full path exposure)
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_silence.schemas import DetectSilenceOptions

# ===========================================================================
# Helpers
# ===========================================================================

FPS = 30.0


def _make_media_info(
    path: str = "/fake/video.mp4",
    *,
    duration_sec: float | None = 10.0,
    rate: float = FPS,
    has_video: bool = True,
    audio_streams: int = 1,
) -> MediaInfo:
    """Helper to construct a MediaInfo for tests.

    Used as the mock return value for inspect_media.
    duration=None is for PROBE_FAILED scenario (DC-AS-004).
    """
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(StreamInfo(index=0, codec_type="video", codec_name="h264"))
    for _i in range(audio_streams):
        streams.append(
            StreamInfo(index=len(streams), codec_type="audio", codec_name="aac")
        )
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


def _make_stderr(
    intervals: list[tuple[float, float]],
    *,
    omit_last_end: bool = False,
) -> str:
    """Helper to generate a fake stderr containing silence_start / silence_end lines.

    When omit_last_end=True, the last silence_end is omitted to simulate trailing silence
    (DC-AM-002 trailing silence_end missing scenario).
    """
    lines: list[str] = []
    for i, (start, end) in enumerate(intervals):
        lines.append(f"[silencedetect @ 0xabcdef] silence_start: {start:.6f}")
        if not (omit_last_end and i == len(intervals) - 1):
            lines.append(
                f"[silencedetect @ 0xabcdef] silence_end: {end:.6f} | "
                f"silence_duration: {end - start:.6f}"
            )
    return "\n".join(lines)


def _fake_run_ok(stderr: str) -> Any:
    """Return a closure that acts as a successful mock for run."""

    def _impl(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        return CompletedProcess(args=cmd, returncode=0, stdout="", stderr=stderr)

    return _impl


def _fake_vad_run_ok(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
    """Successful mock for VAD CLI run: returns empty speech_segments JSON."""
    payload = json.dumps({"speech_segments": []})
    return CompletedProcess(args=cmd, returncode=0, stdout=payload, stderr="")


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


# ===========================================================================
# (1) silencedetect stderr parsing (DC-AM-003)
# ===========================================================================


class TestStderrParsing:
    """Parsing aspects for silencedetect stderr (DC-AM-003).

    Covers regex, line-start match, fixed decimal / fractional, multi-digit, and multiple intervals.
    """

    def test_parse_single_interval(self, tmp_path: Path) -> None:
        """Parsing one silence_start/end interval produces the expected KEEP."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(2.0, 5.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

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

        assert result["ok"] is True
        assert result["data"]["silence_count"] == 1

    def test_parse_fractional_seconds(self, tmp_path: Path) -> None:
        """Fractional seconds (e.g., 2.123456) are correctly parsed (DC-AM-003)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(2.123456, 5.654321)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

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

        assert result["ok"] is True
        assert result["data"]["silence_count"] == 1

    def test_parse_multiple_intervals(self, tmp_path: Path) -> None:
        """Multiple intervals are correctly parsed (DC-AM-003)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # 3 intervals
        intervals = [(1.0, 2.0), (4.5, 5.5), (8.0, 9.0)]
        stderr = _make_stderr(intervals)
        media_info = _make_media_info(path=media, duration_sec=12.0)

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

        assert result["ok"] is True
        assert result["data"]["silence_count"] == 3

    def test_parse_large_value_seconds(self, tmp_path: Path) -> None:
        """Multi-digit seconds (e.g., 120.5) are correctly parsed (DC-AM-003)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(120.5, 135.25)])
        media_info = _make_media_info(path=media, duration_sec=300.0)

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

        assert result["ok"] is True
        assert result["data"]["silence_count"] == 1

    def test_non_silence_lines_ignored(self, tmp_path: Path) -> None:
        """Non-silence_start/end lines do not affect parse results (DC-AM-003)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # Inject unrelated lines
        stderr = (
            "[ffmpeg version 6.0] noise=-30dB\n"
            "[silencedetect @ 0xabcdef] silence_start: 3.000000\n"
            "frame=100 fps=25 q=0.0 size=N/A time=00:00:10.00 bitrate=N/A\n"
            "[silencedetect @ 0xabcdef] silence_end: 7.000000 | "
            "silence_duration: 4.000000\n"
        )
        media_info = _make_media_info(path=media, duration_sec=10.0)

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

        assert result["ok"] is True
        assert result["data"]["silence_count"] == 1


# ===========================================================================
# (2) Trailing silence_end missing -> completed with total_duration (DC-AM-002)
# ===========================================================================


class TestTrailingSilenceCompletion:
    """When silence_end is missing for trailing silence, it is completed to total_duration
    (DC-AM-002).
    """

    def test_missing_trailing_silence_end_is_completed(self, tmp_path: Path) -> None:
        """Trailing silence_end missing -> completed to total_duration=10.0 and excluded from KEEP.

        Only silence_start=7.0 with no silence_end.
        total_duration=10.0 -> silence interval completed as (7.0, 10.0).
        KEEP should be a single interval of (0.0, 7.0).
        """
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # no silence_end
        stderr = "[silencedetect @ 0xabcdef] silence_start: 7.000000\n"
        media_info = _make_media_info(path=media, duration_sec=10.0)

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

        assert result["ok"] is True
        # Completion causes it to be recognized as 1 silence interval
        assert result["data"]["silence_count"] == 1
        # KEEP is only the portion before the trailing silence (1 clip)
        assert result["data"]["keep_count"] == 1

    def test_only_silence_start_no_end_keeps_before_start(self, tmp_path: Path) -> None:
        """Only silence_start=3.0 (no silence_end) -> KEEP is only (0, 3.0).

        Completed silence: (3.0, total_duration=10.0)
        KEEP: single interval (0.0, 3.0).
        """
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = "[silencedetect @ 0xabcdef] silence_start: 3.000000\n"
        media_info = _make_media_info(path=media, duration_sec=10.0)

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

        assert result["ok"] is True
        assert result["data"]["keep_count"] == 1

    def test_mixed_complete_and_incomplete_silence(self, tmp_path: Path) -> None:
        """Complete and trailing-incomplete intervals coexist and are counted correctly (DC-AM-002)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # Interval 1 is complete; interval 2 has missing silence_end
        stderr = (
            "[silencedetect @ 0xabcdef] silence_start: 2.000000\n"
            "[silencedetect @ 0xabcdef] silence_end: 4.000000 | "
            "silence_duration: 2.000000\n"
            "[silencedetect @ 0xabcdef] silence_start: 8.000000\n"
        )
        media_info = _make_media_info(path=media, duration_sec=12.0)

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

        assert result["ok"] is True
        # 2 intervals (including completed one)
        assert result["data"]["silence_count"] == 2


# ===========================================================================
# (3) OTIO verification of KEEP clip list (DC-AS-001/003/AD-4)
# ===========================================================================


class TestKeepClipOtio:
    """Verify that keep-clips are correctly placed in the V1 track of the generated timeline.otio.

    source_range.rate = inspect_media MediaInfo.duration.rate (DC-AS-003).
    target_url = absolute path to media (DC-AS-001).
    metadata["clipwright"] = {tool, version, kind:"keep"}.
    """

    def test_v1_track_has_keep_clips(self, tmp_path: Path) -> None:
        """V1 track must have at least one clip (AD-4)."""
        from clipwright.otio_utils import load_timeline

        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(3.0, 7.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0, rate=FPS)

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

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        assert v1.kind == otio.schema.TrackKind.Video
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) > 0

    def test_source_range_rate_matches_media_info_duration_rate(
        self, tmp_path: Path
    ) -> None:
        """source_range.rate must match MediaInfo.duration.rate (DC-AS-003)."""
        from clipwright.otio_utils import load_timeline

        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # Built with rate=25.0 (value different from FPS to test)
        custom_rate = 25.0
        stderr = _make_stderr([(2.0, 6.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0, rate=custom_rate)

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

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        for clip in clips:
            assert clip.source_range is not None
            assert clip.source_range.start_time.rate == pytest.approx(custom_rate)
            assert clip.source_range.duration.rate == pytest.approx(custom_rate)

    def test_source_range_value_encodes_seconds_times_rate(
        self, tmp_path: Path
    ) -> None:
        """source_range.start_time.value = start_sec * rate (DC-AS-003).

        For KEEP (0.0, 3.0) with rate=30: start_time.value=0, duration.value=90.
        """
        from clipwright.otio_utils import load_timeline

        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # Silence (3,10) -> KEEP (0, 3)
        stderr = _make_stderr([(3.0, 10.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0, rate=30.0)

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

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) == 1
        clip = clips[0]
        # KEEP (0.0, 3.0), rate=30 -> value=0.0, duration.value=90.0
        assert clip.source_range.start_time.value == pytest.approx(0.0)
        assert clip.source_range.duration.value == pytest.approx(90.0)

    def test_target_url_resolves_to_media_path(self, tmp_path: Path) -> None:
        """clip target_url must resolve to the media file (DC-AS-001).

        When media is inside the OTIO output directory, media_ref_for_otio returns a
        relative POSIX path.  Resolve relative to the OTIO directory to compare.
        """
        from clipwright.otio_utils import load_timeline

        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(3.0, 7.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

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

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        abs_media = str(Path(media).resolve())
        out_dir = Path(output).parent
        for clip in clips:
            assert isinstance(clip.media_reference, otio.schema.ExternalReference)
            ref_url = clip.media_reference.target_url
            ref_path = Path(ref_url)
            # Resolve relative to OTIO dir (media_ref_for_otio may return relative path)
            if ref_path.is_absolute():
                resolved = str(ref_path.resolve())
            else:
                resolved = str((out_dir / ref_path).resolve())
            assert resolved == abs_media, (
                f"target_url {ref_url!r} resolved to {resolved!r}, expected {abs_media!r}"
            )

    def test_clip_metadata_has_clipwright_key(self, tmp_path: Path) -> None:
        """clip.metadata["clipwright"] must contain tool/version/kind="keep"."""
        from clipwright.otio_utils import load_timeline

        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(3.0, 7.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

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

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        for clip in clips:
            cw = clip.metadata.get("clipwright")
            assert cw is not None, "metadata['clipwright'] is not set"
            assert cw.get("tool") == "clipwright-silence"
            assert "version" in cw
            assert cw.get("kind") == "keep"

    def test_clip_count_matches_keep_count(self, tmp_path: Path) -> None:
        """The clip count in V1 must match data["keep_count"]."""
        from clipwright.otio_utils import load_timeline

        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # 2 silence intervals -> 3 KEEP intervals
        stderr = _make_stderr([(2.0, 3.0), (6.0, 7.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

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

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clip_count = sum(1 for it in v1 if isinstance(it, otio.schema.Clip))
        assert clip_count == result["data"]["keep_count"]


# ===========================================================================
# (4) Input validation error group (DC-AS-001/002/004)
# ===========================================================================


class TestInputValidation:
    """Verifies input validation errors (DC-AS-001/002/004)."""

    def test_audio_stream_missing_returns_unsupported_operation(
        self, tmp_path: Path
    ) -> None:
        """No audio stream -> UNSUPPORTED_OPERATION (DC-AS-002)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(
            path=media, duration_sec=10.0, has_video=True, audio_streams=0
        )

        with patch(
            "clipwright_silence.detect.inspect_media",
            return_value=media_info,
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.UNSUPPORTED_OPERATION

    def test_video_stream_missing_returns_unsupported_operation(
        self, tmp_path: Path
    ) -> None:
        """No video stream -> UNSUPPORTED_OPERATION (DC-AS-002)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(
            path=media, duration_sec=10.0, has_video=False, audio_streams=1
        )

        with patch(
            "clipwright_silence.detect.inspect_media",
            return_value=media_info,
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.UNSUPPORTED_OPERATION

    def test_duration_none_returns_probe_failed(self, tmp_path: Path) -> None:
        """MediaInfo.duration is None -> PROBE_FAILED (DC-AS-004)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=None)

        with patch(
            "clipwright_silence.detect.inspect_media",
            return_value=media_info,
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PROBE_FAILED

    def test_ffmpeg_not_found_returns_dependency_missing(self, tmp_path: Path) -> None:
        """ffmpeg not found -> DEPENDENCY_MISSING (AD-1, DC-GP-004)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        def _fake_resolve(name: str, env_var: str | None = None) -> str:
            raise ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="ffmpeg not found",
                hint="Add ffmpeg to PATH.",
            )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=_fake_resolve,
            ),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.DEPENDENCY_MISSING

    def test_inspect_media_file_not_found_propagates(self, tmp_path: Path) -> None:
        """inspect_media raises FILE_NOT_FOUND -> propagates to the envelope."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "nonexistent.mp4")
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_silence.detect.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"File not found: {Path(media).name}",
                hint="Specify a valid media file path.",
            ),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND

    def test_symlink_media_propagates_path_not_allowed(self, tmp_path: Path) -> None:
        """Symlink media -> PATH_NOT_ALLOWED from inspect_media propagates."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "link.mp4")
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_silence.detect.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.PATH_NOT_ALLOWED,
                message=f"Symbolic links are not accepted: {Path(media).name}",
                hint="Specify a real file.",
            ),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED

    def test_output_in_different_dir_allowed(self, tmp_path: Path) -> None:
        """output in a different directory than media is now allowed (new policy: DC-AS-001 removed)."""
        from clipwright_silence.detect import detect_silence

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "other"
        out_dir.mkdir()
        media = str(media_dir / "video.mp4")
        Path(media).touch()
        output = str(out_dir / "out.otio")

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=_make_media_info(path=media, duration_sec=10.0),
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_run_ok(""),
            ),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True

    def test_output_invalid_extension_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """output extension other than .otio -> INVALID_INPUT (AD-5)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.mp4")  # not .otio

        with patch(
            "clipwright_silence.detect.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_output_parent_dir_not_found_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """output parent directory missing -> INVALID_INPUT or FILE_NOT_FOUND (AD-5)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "nonexistent_dir" / "out.otio")

        with patch(
            "clipwright_silence.detect.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] in (
            ErrorCode.INVALID_INPUT,
            ErrorCode.FILE_NOT_FOUND,
        )


# ===========================================================================
# (5) Envelope format
# ===========================================================================


class TestEnvelope:
    """Verify the success envelope format (§6.3 / architecture §return value envelope)."""

    def test_success_envelope_has_required_keys(self, tmp_path: Path) -> None:
        """On success, ok/summary/data/artifacts/warnings are all present."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(2.0, 5.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

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

        assert result["ok"] is True
        assert "summary" in result
        assert "data" in result
        assert "artifacts" in result
        assert "warnings" in result

    def test_data_has_required_fields(self, tmp_path: Path) -> None:
        """data must contain silence_count / total_silence_seconds / keep_count /
        total_keep_seconds.
        """
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(2.0, 5.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

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

        data = result["data"]
        assert "silence_count" in data
        assert "total_silence_seconds" in data
        assert "keep_count" in data
        assert "total_keep_seconds" in data

    def test_artifacts_contains_timeline_otio(self, tmp_path: Path) -> None:
        """artifacts must contain one artifact with role="timeline" / format="otio"."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(2.0, 5.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

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

        artifacts = result["artifacts"]
        assert len(artifacts) >= 1
        # artifacts may be a dict or an Artifact model
        timeline_artifacts = [
            a
            for a in artifacts
            if (
                (isinstance(a, dict) and a.get("role") == "timeline")
                or (hasattr(a, "role") and a.role == "timeline")
            )
        ]
        assert len(timeline_artifacts) == 1

    def test_data_counts_match_silence_intervals(self, tmp_path: Path) -> None:
        """data's silence_count must match the actual number of silence intervals."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        intervals = [(1.0, 2.0), (4.0, 5.0), (7.0, 8.0)]
        stderr = _make_stderr(intervals)
        media_info = _make_media_info(path=media, duration_sec=10.0)

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

        assert result["ok"] is True
        assert result["data"]["silence_count"] == 3
        assert result["data"]["keep_count"] == 4

    def test_total_silence_seconds_approx(self, tmp_path: Path) -> None:
        """total_silence_seconds holds a value close to the sum of silence interval durations."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # Silence: 2-5 (3s), 7-8 (1s) = total 4s
        stderr = _make_stderr([(2.0, 5.0), (7.0, 8.0)])
        media_info = _make_media_info(path=media, duration_sec=12.0)

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

        assert result["ok"] is True
        total_silence = result["data"]["total_silence_seconds"]
        assert total_silence == pytest.approx(4.0, abs=0.01)

    def test_summary_is_non_empty_string(self, tmp_path: Path) -> None:
        """summary must be a non-empty string (§6.3 convention)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(2.0, 5.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

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

        assert isinstance(result["summary"], str)
        assert len(result["summary"]) > 0


# ===========================================================================
# (6) Edge cases: all silence / zero silence
# ===========================================================================


class TestEdgeCases:
    """Edge cases for all silence and zero silence."""

    def test_all_silence_returns_ok_with_empty_v1_and_warning(
        self, tmp_path: Path
    ) -> None:
        """All silence -> ok=True + warning + empty V1 (AD-3 §2 / design policy: not an error)."""
        from clipwright.otio_utils import load_timeline

        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # Entire duration is silence
        stderr = _make_stderr([(0.0, 10.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

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

        assert result["ok"] is True
        # warning should indicate that there are no intervals to keep
        assert len(result["warnings"]) > 0
        # V1 has 0 clips
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) == 0
        assert result["data"]["keep_count"] == 0

    def test_no_silence_returns_single_full_clip(self, tmp_path: Path) -> None:
        """Zero silence -> single full-duration clip (AD-3 §2)."""
        from clipwright.otio_utils import load_timeline

        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # No silence lines in stderr
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_run_ok(""),
            ),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        assert result["data"]["silence_count"] == 0
        assert result["data"]["keep_count"] == 1
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) == 1

    def test_no_silence_clip_covers_full_duration(self, tmp_path: Path) -> None:
        """With zero silence, the single clip covers the full duration. rate=30.0, total=10.0s."""
        from clipwright.otio_utils import load_timeline

        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0, rate=30.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_run_ok(""),
            ),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) == 1
        clip = clips[0]
        # Full duration 10.0s at rate=30.0 -> duration.value = 300.0
        assert clip.source_range.duration.value == pytest.approx(300.0)


# ===========================================================================
# (7) Non-destructive and path safety (Sec M-1 / AD-4)
# ===========================================================================


class TestNonDestructiveAndPathSafety:
    """Non-destructive and no full path exposure (basename only, no raw ffmpeg stderr)."""

    def test_media_file_unchanged_after_detect(self, tmp_path: Path) -> None:
        """Media file content must not change after detect (non-destructive)."""
        from clipwright_silence.detect import detect_silence

        media_path = tmp_path / "video.mp4"
        media_path.write_bytes(b"dummy content")
        original = media_path.read_bytes()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(2.0, 5.0)])
        media_info = _make_media_info(path=str(media_path), duration_sec=10.0)

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
            detect_silence(str(media_path), output, _opts())

        assert media_path.read_bytes() == original

    def test_error_message_does_not_expose_directory_path(self, tmp_path: Path) -> None:
        """Error message must not contain a directory path (basename only, Sec M-1)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        output = str(tmp_path / "out.otio")
        full_dir = str(tmp_path)

        with patch(
            "clipwright_silence.detect.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"File not found: {Path(media).name}",
                hint="Specify a valid media file path.",
            ),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        error_msg = result["error"]["message"]
        assert full_dir not in error_msg

    def test_error_message_does_not_expose_raw_stderr(self, tmp_path: Path) -> None:
        """Error message must not contain raw ffmpeg stderr (Sec M-1)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        raw_secret = "INTERNAL_SECRET_PATH /home/user/private"
        media_info = _make_media_info(path=media, duration_sec=10.0)

        def _fake_run_fail(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="Command failed with exit code 1",
                hint="Check the command.",
            )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_run_fail,
            ),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        error_msg = result["error"]["message"]
        assert raw_secret not in error_msg

    def test_ffmpeg_called_with_list_not_shell_string(self, tmp_path: Path) -> None:
        """The command passed to run must be list[str] (shell=False equivalent, §6.5)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        captured_cmds: list[list[str]] = []

        def _capture_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_capture_run),
        ):
            detect_silence(media, output, _opts())

        assert len(captured_cmds) >= 1
        for cmd in captured_cmds:
            assert isinstance(cmd, list), "A non-list command was passed to run"
            for arg in cmd:
                assert isinstance(arg, str), "Command argument contains a non-str"

    def test_ffmpeg_timeout_uses_max_60_or_duration_times_2(
        self, tmp_path: Path
    ) -> None:
        """run is called with timeout = max(60, ceil(total_duration * 2)) (AD-3 design).

        total_duration=10.0s -> max(60, ceil(20)) = 60.
        """
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        captured_timeouts: list[float] = []

        def _capture_run(
            cmd: list[str], *, timeout: float = 60.0, **kwargs: Any
        ) -> CompletedProcess[str]:
            captured_timeouts.append(timeout)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_capture_run),
        ):
            detect_silence(media, output, _opts())

        assert len(captured_timeouts) >= 1
        # total=10s -> max(60, ceil(10*2))=max(60,20)=60
        assert captured_timeouts[0] == pytest.approx(
            max(60, math.ceil(10.0 * 2)), abs=1
        )


# ===========================================================================
# (8) SR L-2: FILE_NOT_FOUND / symlink rejection message excludes directory part
# ===========================================================================


class TestFileNotFoundMessageSafety:
    """FILE_NOT_FOUND message must contain only the basename (SR L-2).

    The detect layer catches ClipwrightError(FILE_NOT_FOUND) and replaces the message,
    so the caller's error message must not contain the directory part.
    """

    def test_file_not_found_message_contains_only_basename(
        self, tmp_path: Path
    ) -> None:
        """FILE_NOT_FOUND message must not contain a directory path (SR L-2)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "missing_video.mp4")
        output = str(tmp_path / "out.otio")
        full_dir = str(tmp_path)

        with patch(
            "clipwright_silence.detect.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"File not found: {media}",  # full path included
                hint="Specify a valid media file path.",
            ),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND
        error_msg = result["error"]["message"]
        # Directory part (full path) must not be present
        assert full_dir not in error_msg
        # basename must be present
        assert "missing_video.mp4" in error_msg
        # hint must be inherited from inspect_media (not lost after replacement, N-2)
        exp_hint = "Specify a valid media file path."
        assert result["error"]["hint"] == exp_hint

    def test_symlink_path_not_allowed_message_contains_only_basename(
        self, tmp_path: Path
    ) -> None:
        """Symlink rejection message must not contain a directory path (SR L-2).

        pathpolicy.validate_source_file emits basename-only messages for PATH_NOT_ALLOWED.
        detect_silence propagates the message without further sanitization for this code,
        so the mock reflects the real source (basename only, not full path).
        """
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "link.mp4")
        output = str(tmp_path / "out.otio")
        full_dir = str(tmp_path)

        with patch(
            "clipwright_silence.detect.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.PATH_NOT_ALLOWED,
                message="Symbolic links are not accepted: link.mp4",  # basename only (real impl)
                hint="Specify a real file.",
            ),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED
        error_msg = result["error"]["message"]
        # Directory part must not be present
        assert full_dir not in error_msg
        # basename must be present
        assert "link.mp4" in error_msg
        # hint must be inherited from inspect_media (N-2)
        assert result["error"]["hint"] == "Specify a real file."


# ===========================================================================
# (9) SR L-3: intervals where silence_end < silence_start are skipped
# ===========================================================================


class TestAbnormalIntervalGuard:
    """Inverted intervals (end < start) must be ignored (SR L-3).

    As a defensive measure for future backend replacements, _parse_silence_intervals
    skips intervals where end < start.
    """

    def test_inverted_interval_is_ignored(self, tmp_path: Path) -> None:
        """An inverted interval (end < start) must not be counted in silence_count (SR L-3)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # inverted interval: start=5.0 > end=3.0 -> skipped
        # valid interval: start=1.0, end=2.0 -> counted
        stderr = (
            "[silencedetect @ 0xabcdef] silence_start: 1.000000\n"
            "[silencedetect @ 0xabcdef] silence_end: 2.000000 | "
            "silence_duration: 1.000000\n"
            "[silencedetect @ 0xabcdef] silence_start: 5.000000\n"
            "[silencedetect @ 0xabcdef] silence_end: 3.000000 | "
            "silence_duration: -2.000000\n"
        )
        media_info = _make_media_info(path=media, duration_sec=10.0)

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

        assert result["ok"] is True
        # Inverted interval is skipped; only the valid interval is counted
        assert result["data"]["silence_count"] == 1

    def test_only_inverted_interval_results_in_no_silence(self, tmp_path: Path) -> None:
        """When all intervals are inverted, silence_count=0 and the full duration becomes KEEP (SR L-3)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # all inverted intervals (end < start) -> all skipped
        stderr = (
            "[silencedetect @ 0xabcdef] silence_start: 7.000000\n"
            "[silencedetect @ 0xabcdef] silence_end: 2.000000 | "
            "silence_duration: -5.000000\n"
        )
        media_info = _make_media_info(path=media, duration_sec=10.0)

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

        assert result["ok"] is True
        assert result["data"]["silence_count"] == 0
        # no silence -> full duration becomes 1 KEEP
        assert result["data"]["keep_count"] == 1


# ===========================================================================
# VAD backend tests (VAD-AD-01~08 / §7.1/7.4/7.5/7.7/7.9)
# ===========================================================================


def _make_vad_speech_json(speech_segments: list[tuple[float, float]]) -> str:
    """Generate a success stdout JSON for the VAD CLI.

    Each element is a (start_sec, end_sec) speech interval tuple.
    Matches the actual vad_cli.py output format (list [[start, end], ...]) (CR M-4 / CR-T-001).
    """
    return json.dumps({"speech_segments": [[s, e] for s, e in speech_segments]})


def _make_vad_error_json(code: str, message: str, hint: str) -> str:
    """Generate an error stdout JSON for the VAD CLI."""
    return json.dumps({"error": {"code": code, "message": message, "hint": hint}})


def _fake_vad_run(stdout_json: str) -> Any:
    """Return a closure that mocks a successful VAD CLI run.

    Returns a CompletedProcess with the given JSON in stdout.
    """

    def _impl(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        return CompletedProcess(args=cmd, returncode=0, stdout=stdout_json, stderr="")

    return _impl


def _vad_opts(
    vad_threshold: float = 0.5,
    vad_min_speech_duration: float = 0.25,
    vad_min_silence_duration: float = 0.1,
    padding: float = 0.0,
    min_keep_duration: float = 0.0,
) -> DetectSilenceOptions:
    """Build a DetectSilenceOptions with backend="vad"."""
    return DetectSilenceOptions(
        backend="vad",
        vad_threshold=vad_threshold,
        vad_min_speech_duration=vad_min_speech_duration,
        vad_min_silence_duration=vad_min_silence_duration,
        padding=padding,
        min_keep_duration=min_keep_duration,
    )


# ---------------------------------------------------------------------------
# (1) VAD CLI invocation argument array validation (VAD-AD-02, DC-AS-001)
# ---------------------------------------------------------------------------


class TestVadCliInvocation:
    """VAD CLI must be launched with the correct argument array when backend="vad" (VAD-AD-02).

    - sys.executable -m clipwright_silence.vad_cli
    - --media / --threshold / --min-speech / --min-silence are passed from options
    """

    def test_vad_cli_called_with_sys_executable_module_flag(
        self, tmp_path: Path
    ) -> None:
        """VAD CLI is launched via sys.executable -m clipwright_silence.vad_cli."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)
        speech_json = _make_vad_speech_json([(1.0, 9.0)])

        captured_cmds: list[list[str]] = []

        def _capture_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(
                args=cmd, returncode=0, stdout=speech_json, stderr=""
            )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch("clipwright_silence.detect.run", side_effect=_capture_run),
        ):
            detect_silence(media, output, _vad_opts())

        assert len(captured_cmds) >= 1
        cmd = captured_cmds[0]
        assert isinstance(cmd, list)
        # starts in order: sys.executable -m clipwright_silence.vad_cli
        assert cmd[0] == sys.executable
        assert cmd[1] == "-m"
        assert cmd[2] == "clipwright_silence.vad_cli"

    def test_vad_cli_receives_media_option(self, tmp_path: Path) -> None:
        """VAD CLI receives the media path via the --media option."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)
        speech_json = _make_vad_speech_json([(1.0, 9.0)])

        captured_cmds: list[list[str]] = []

        def _capture_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(
                args=cmd, returncode=0, stdout=speech_json, stderr=""
            )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch("clipwright_silence.detect.run", side_effect=_capture_run),
        ):
            detect_silence(media, output, _vad_opts())

        cmd = captured_cmds[0]
        assert "--media" in cmd
        media_idx = cmd.index("--media")
        # element after --media must be the absolute path of the media file
        assert cmd[media_idx + 1] == str(Path(media).resolve())

    def test_vad_cli_receives_threshold_option(self, tmp_path: Path) -> None:
        """VAD CLI receives the --threshold option from options.vad_threshold."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)
        speech_json = _make_vad_speech_json([(1.0, 9.0)])
        opts = _vad_opts(vad_threshold=0.7)

        captured_cmds: list[list[str]] = []

        def _capture_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(
                args=cmd, returncode=0, stdout=speech_json, stderr=""
            )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch("clipwright_silence.detect.run", side_effect=_capture_run),
        ):
            detect_silence(media, output, opts)

        cmd = captured_cmds[0]
        assert "--threshold" in cmd
        thresh_idx = cmd.index("--threshold")
        assert cmd[thresh_idx + 1] == "0.7"

    def test_vad_cli_receives_min_speech_option(self, tmp_path: Path) -> None:
        """VAD CLI receives the --min-speech option from options.vad_min_speech_duration."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)
        speech_json = _make_vad_speech_json([(1.0, 9.0)])
        opts = _vad_opts(vad_min_speech_duration=0.3)

        captured_cmds: list[list[str]] = []

        def _capture_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(
                args=cmd, returncode=0, stdout=speech_json, stderr=""
            )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch("clipwright_silence.detect.run", side_effect=_capture_run),
        ):
            detect_silence(media, output, opts)

        cmd = captured_cmds[0]
        assert "--min-speech" in cmd
        idx = cmd.index("--min-speech")
        assert cmd[idx + 1] == "0.3"

    def test_vad_cli_receives_min_silence_option(self, tmp_path: Path) -> None:
        """VAD CLI receives the --min-silence option from options.vad_min_silence_duration."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)
        speech_json = _make_vad_speech_json([(1.0, 9.0)])
        opts = _vad_opts(vad_min_silence_duration=0.2)

        captured_cmds: list[list[str]] = []

        def _capture_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(
                args=cmd, returncode=0, stdout=speech_json, stderr=""
            )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch("clipwright_silence.detect.run", side_effect=_capture_run),
        ):
            detect_silence(media, output, opts)

        cmd = captured_cmds[0]
        assert "--min-silence" in cmd
        idx = cmd.index("--min-silence")
        assert cmd[idx + 1] == "0.2"

    def test_vad_backend_does_not_use_silencedetect_path(self, tmp_path: Path) -> None:
        """When backend="vad", the silencedetect path (resolve_tool ffmpeg) is not called.

        The VAD path launches via sys.executable -m directly and does not use resolve_tool (VAD-AD-02).
        """
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)
        speech_json = _make_vad_speech_json([(1.0, 9.0)])

        resolve_called_names: list[str] = []

        def _capture_resolve(name: str, env_var: str | None = None) -> str:
            resolve_called_names.append(name)
            return f"/usr/bin/{name}"

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=_capture_resolve,
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_vad_run(speech_json),
            ),
        ):
            detect_silence(media, output, _vad_opts())

        # resolve_tool("ffmpeg") from the silencedetect path must not be called
        assert "ffmpeg" not in resolve_called_names

    def test_silencedetect_backend_uses_resolve_tool_not_vad_cli(
        self, tmp_path: Path
    ) -> None:
        """When backend="silencedetect" (default), VAD CLI is not called and the silencedetect path is used.

        Non-regression for the backend branch (VAD-AD-01).
        """
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(2.0, 5.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        captured_cmds: list[list[str]] = []

        def _capture_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr=stderr)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_capture_run),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        # silencedetect path: cmd[0] is the ffmpeg path (not sys.executable)
        assert len(captured_cmds) >= 1
        cmd = captured_cmds[0]
        assert cmd[0] == "/usr/bin/ffmpeg"
        assert "-m" not in cmd


# ---------------------------------------------------------------------------
# (2) Double-inversion edge case validation (§7.4, DC-AS-003)
# ---------------------------------------------------------------------------


class TestVadDoubleInversionEdgeCases:
    """Verify that VAD speech intervals -> invert -> KEEP equals the input speech intervals (§7.4).

    Double inversion: detect (speech->silence) -> plan (silence->KEEP).
    KEEP = speech intervals is verified with fixed expected values (padding=0.0).
    """

    def test_empty_speech_segments_gives_empty_keep(self, tmp_path: Path) -> None:
        """(1) Zero speech (empty speech_segments) -> silence [(0, total)] -> empty KEEP (all removed)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)
        speech_json = _make_vad_speech_json([])  # zero speech

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_vad_run(speech_json),
            ),
        ):
            result = detect_silence(media, output, _vad_opts(padding=0.0))

        assert result["ok"] is True
        assert result["data"]["keep_count"] == 0

    def test_full_speech_gives_full_keep(self, tmp_path: Path) -> None:
        """(2) Full speech [(0, total)] -> empty silence -> KEEP [(0, total)] (full KEEP)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        total = 10.0
        media_info = _make_media_info(path=media, duration_sec=total)
        speech_json = _make_vad_speech_json([(0.0, total)])

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_vad_run(speech_json),
            ),
        ):
            result = detect_silence(media, output, _vad_opts(padding=0.0))

        assert result["ok"] is True
        assert result["data"]["keep_count"] == 1
        assert result["data"]["total_keep_seconds"] == pytest.approx(total)

    def test_head_speech_gives_keep_starting_from_zero(self, tmp_path: Path) -> None:
        """(3) Head speech [(0, 4)] -> KEEP starts at (0, 4) (no leading silence)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        total = 10.0
        media_info = _make_media_info(path=media, duration_sec=total, rate=30.0)
        # speech: (0, 4) -> silence: (4, 10) -> KEEP: (0, 4)
        speech_json = _make_vad_speech_json([(0.0, 4.0)])

        from clipwright.otio_utils import load_timeline

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_vad_run(speech_json),
            ),
        ):
            result = detect_silence(media, output, _vad_opts(padding=0.0))

        assert result["ok"] is True
        assert result["data"]["keep_count"] == 1
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) == 1
        # start_time.value = 0.0 * 30 = 0, duration.value = 4.0 * 30 = 120
        assert clips[0].source_range.start_time.value == pytest.approx(0.0)
        assert clips[0].source_range.duration.value == pytest.approx(120.0)

    def test_tail_speech_gives_keep_ending_at_total(self, tmp_path: Path) -> None:
        """(4) Tail speech [(6, 10)] -> KEEP ends at (6, total=10) (no trailing silence)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        total = 10.0
        media_info = _make_media_info(path=media, duration_sec=total, rate=30.0)
        # speech: (6, 10) -> silence: (0, 6) -> KEEP: (6, 10)
        speech_json = _make_vad_speech_json([(6.0, total)])

        from clipwright.otio_utils import load_timeline

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_vad_run(speech_json),
            ),
        ):
            result = detect_silence(media, output, _vad_opts(padding=0.0))

        assert result["ok"] is True
        assert result["data"]["keep_count"] == 1
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) == 1
        # start_time.value = 6.0 * 30 = 180, duration.value = 4.0 * 30 = 120
        assert clips[0].source_range.start_time.value == pytest.approx(180.0)
        assert clips[0].source_range.duration.value == pytest.approx(120.0)

    def test_speech_end_beyond_total_is_clipped(self, tmp_path: Path) -> None:
        """When speech_segments end > total, it is clipped to total (§7.4 pre-processing)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        total = 10.0
        media_info = _make_media_info(path=media, duration_sec=total)
        # end=15.0 > total=10.0 -> clipped to end=10.0 equivalent
        speech_json = _make_vad_speech_json([(2.0, 15.0)])

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_vad_run(speech_json),
            ),
        ):
            result = detect_silence(media, output, _vad_opts(padding=0.0))

        assert result["ok"] is True
        # after clipping: speech (2, 10) -> silence (0, 2) -> KEEP (2, 10) = 8.0s (speech=KEEP)
        assert result["data"]["keep_count"] == 1
        assert result["data"]["total_keep_seconds"] == pytest.approx(8.0)

    def test_speech_start_below_zero_is_clipped(self, tmp_path: Path) -> None:
        """When speech_segments start < 0, it is clipped to 0 (§7.4 pre-processing)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        total = 10.0
        media_info = _make_media_info(path=media, duration_sec=total)
        # start=-3.0 < 0 -> clipped to start=0.0 equivalent
        speech_json = _make_vad_speech_json([(-3.0, 8.0)])

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_vad_run(speech_json),
            ),
        ):
            result = detect_silence(media, output, _vad_opts(padding=0.0))

        assert result["ok"] is True
        # after clipping: speech (0, 8) -> silence (8, 10) -> KEEP (0, 8)
        assert result["data"]["keep_count"] == 1
        assert result["data"]["total_keep_seconds"] == pytest.approx(8.0)

    def test_degenerate_segment_start_ge_end_is_removed(self, tmp_path: Path) -> None:
        """Degenerate intervals (start >= end) are removed (§7.4 pre-processing)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        total = 10.0
        media_info = _make_media_info(path=media, duration_sec=total)
        # (5.0, 5.0) is a degenerate interval (start==end) -> removed
        # (3.0, 7.0) is a valid speech interval
        speech_json = _make_vad_speech_json([(5.0, 5.0), (3.0, 7.0)])

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_vad_run(speech_json),
            ),
        ):
            result = detect_silence(media, output, _vad_opts(padding=0.0))

        assert result["ok"] is True
        # after removing degenerate: speech [(3, 7)] -> silence [(0,3),(7,10)] -> KEEP [(3, 7)] = 1 interval
        assert result["data"]["keep_count"] == 1


# ---------------------------------------------------------------------------
# (3) metadata["clipwright"] backend key validation (VAD-AD-07, DC-GP-001)
# ---------------------------------------------------------------------------


class TestVadMetadataBackend:
    """metadata["clipwright"] must contain backend="vad" when backend="vad" (VAD-AD-07).

    The silencedetect path contains backend="silencedetect".
    The existing test test_clip_metadata_has_clipwright_key only asserts tool/version/kind,
    so adding the backend key does not break it (DC-GP-001).
    """

    def test_vad_backend_metadata_contains_backend_key(self, tmp_path: Path) -> None:
        """clip.metadata["clipwright"]["backend"] == "vad" when backend="vad"."""
        from clipwright.otio_utils import load_timeline

        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)
        speech_json = _make_vad_speech_json([(1.0, 9.0)])

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_vad_run(speech_json),
            ),
        ):
            result = detect_silence(media, output, _vad_opts())

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) > 0
        for clip in clips:
            cw = clip.metadata.get("clipwright")
            assert cw is not None
            assert cw.get("tool") == "clipwright-silence"
            assert "version" in cw
            assert cw.get("kind") == "keep"
            assert cw.get("backend") == "vad"

    def test_silencedetect_backend_metadata_contains_backend_key(
        self, tmp_path: Path
    ) -> None:
        """clip.metadata["clipwright"]["backend"] == "silencedetect" when backend="silencedetect"."""
        from clipwright.otio_utils import load_timeline

        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(3.0, 7.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

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

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        for clip in clips:
            cw = clip.metadata.get("clipwright")
            assert cw is not None
            assert cw.get("backend") == "silencedetect"


# ---------------------------------------------------------------------------
# (4) VAD error mapping validation (VAD-AD-06, §7.1)
# ---------------------------------------------------------------------------


class TestVadErrorMapping:
    """VAD CLI error JSON must be mapped to the corresponding ErrorCode (VAD-AD-06, §7.1)."""

    def test_dependency_missing_error_maps_to_dependency_missing(
        self, tmp_path: Path
    ) -> None:
        """VAD CLI returns DEPENDENCY_MISSING error JSON -> DEPENDENCY_MISSING envelope.

        The hint must contain pip install clipwright-silence[vad].
        """
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)
        error_json = _make_vad_error_json(
            code="DEPENDENCY_MISSING",
            message="silero-vad is not installed",
            hint="Install VAD dependencies with pip install clipwright-silence[vad]",
        )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_vad_run(error_json),
            ),
        ):
            result = detect_silence(media, output, _vad_opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.DEPENDENCY_MISSING
        # hint must contain pip install guidance
        hint = result["error"]["hint"]
        assert "pip install" in hint or "clipwright-silence[vad]" in hint

    def test_subprocess_failed_error_maps_to_subprocess_failed(
        self, tmp_path: Path
    ) -> None:
        """VAD CLI returns SUBPROCESS_FAILED error JSON -> SUBPROCESS_FAILED envelope."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)
        error_json = _make_vad_error_json(
            code="SUBPROCESS_FAILED",
            message="ffmpeg execution failed",
            hint="Check that ffmpeg exists on PATH",
        )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_vad_run(error_json),
            ),
        ):
            result = detect_silence(media, output, _vad_opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.SUBPROCESS_FAILED

    def test_probe_failed_error_maps_to_probe_failed(self, tmp_path: Path) -> None:
        """VAD CLI returns PROBE_FAILED error JSON -> PROBE_FAILED envelope."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)
        error_json = _make_vad_error_json(
            code="PROBE_FAILED",
            message="Audio probe failed",
            hint="Check that the media file is valid",
        )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_vad_run(error_json),
            ),
        ):
            result = detect_silence(media, output, _vad_opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PROBE_FAILED


# ---------------------------------------------------------------------------
# (5) VAD summary wording (§7.5, DC-AM-003)
# ---------------------------------------------------------------------------


class TestVadSummary:
    """speech_count must be reflected in the summary when backend="vad" (§7.5).

    A summary in "N speech interval(s)" form is generated,
    distinguishable from the silencedetect path summary.
    """

    def test_vad_summary_contains_speech_count(self, tmp_path: Path) -> None:
        """VAD summary contains the speech count (§7.5, DC-AM-003)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=20.0)
        # 3 speech intervals
        speech_json = _make_vad_speech_json([(1.0, 4.0), (8.0, 12.0), (15.0, 18.0)])

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_vad_run(speech_json),
            ),
        ):
            result = detect_silence(media, output, _vad_opts(padding=0.0))

        assert result["ok"] is True
        summary = result["summary"]
        assert isinstance(summary, str)
        assert len(summary) > 0
        # speech_count=3 should be reflected in summary
        assert "3" in summary
        # Summary should mention speech
        assert "speech" in summary.lower()

    def test_vad_summary_contains_non_speech_count(self, tmp_path: Path) -> None:
        """VAD summary must contain the count of non-speech (removed) intervals (§7.5)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        total = 10.0
        media_info = _make_media_info(path=media, duration_sec=total)
        # 1 speech interval -> 2 non-speech (silence) intervals
        speech_json = _make_vad_speech_json([(3.0, 7.0)])

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_vad_run(speech_json),
            ),
        ):
            result = detect_silence(media, output, _vad_opts(padding=0.0))

        assert result["ok"] is True
        summary = result["summary"]
        # count 2 (non-speech or removed intervals) must appear in summary
        assert "2" in summary

    def test_silencedetect_summary_unchanged(self, tmp_path: Path) -> None:
        """silencedetect path summary wording must not change after adding VAD (non-regression)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(2.0, 5.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

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

        assert result["ok"] is True
        summary = result["summary"]
        # Existing summary contains the "silence" keyword
        assert "silence" in summary.lower()


# ---------------------------------------------------------------------------
# (6) Common input validation also applied on VAD path (VAD-AD-04, §7.9)
# ---------------------------------------------------------------------------


class TestVadCommonInputValidation:
    """Common input validation (output .otio, same directory, video+audio, rate) must work on VAD path.

    Both paths share the same common validation code, so the same errors occur on the VAD path.
    """

    def test_vad_audio_stream_missing_returns_unsupported_operation(
        self, tmp_path: Path
    ) -> None:
        """No audio stream on VAD path -> UNSUPPORTED_OPERATION."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(
            path=media, duration_sec=10.0, has_video=True, audio_streams=0
        )

        with patch(
            "clipwright_silence.detect.inspect_media",
            return_value=media_info,
        ):
            result = detect_silence(media, output, _vad_opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.UNSUPPORTED_OPERATION

    def test_vad_video_stream_missing_returns_unsupported_operation(
        self, tmp_path: Path
    ) -> None:
        """No video stream on VAD path -> UNSUPPORTED_OPERATION."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(
            path=media, duration_sec=10.0, has_video=False, audio_streams=1
        )

        with patch(
            "clipwright_silence.detect.inspect_media",
            return_value=media_info,
        ):
            result = detect_silence(media, output, _vad_opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.UNSUPPORTED_OPERATION

    def test_vad_duration_none_returns_probe_failed(self, tmp_path: Path) -> None:
        """duration=None on VAD path -> PROBE_FAILED."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=None)

        with patch(
            "clipwright_silence.detect.inspect_media",
            return_value=media_info,
        ):
            result = detect_silence(media, output, _vad_opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PROBE_FAILED

    def test_vad_output_invalid_extension_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """output extension other than .otio on VAD path -> INVALID_INPUT."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.mp4")

        with patch(
            "clipwright_silence.detect.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0),
        ):
            result = detect_silence(media, output, _vad_opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_vad_output_in_different_dir_is_allowed(self, tmp_path: Path) -> None:
        """VAD backend with output in a different directory than media -> ok=True.

        The co-location constraint (DC-AS-001-VAD) was removed by impl-detectcut.
        VAD CLI reads media directly and does not depend on the output OTIO path.
        """
        from clipwright_silence.detect import detect_silence

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "other"
        out_dir.mkdir()
        media = str(media_dir / "video.mp4")
        Path(media).touch()
        output = str(out_dir / "out.otio")

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=_make_media_info(path=media, duration_sec=10.0),
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_vad_run_ok),
        ):
            result = detect_silence(media, output, _vad_opts())

        assert result["ok"] is True, (
            f"Expected ok=True after removing VAD co-location constraint; "
            f"got ok=False: {result.get('error')}"
        )

    def test_vad_source_range_rate_matches_media_info_duration_rate(
        self, tmp_path: Path
    ) -> None:
        """source_range.rate must match MediaInfo.duration.rate on VAD path (DC-AS-003)."""
        from clipwright.otio_utils import load_timeline

        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        custom_rate = 25.0
        media_info = _make_media_info(path=media, duration_sec=10.0, rate=custom_rate)
        speech_json = _make_vad_speech_json([(2.0, 8.0)])

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_vad_run(speech_json),
            ),
        ):
            result = detect_silence(media, output, _vad_opts(padding=0.0))

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        for clip in clips:
            assert clip.source_range is not None
            assert clip.source_range.start_time.rate == pytest.approx(custom_rate)
            assert clip.source_range.duration.rate == pytest.approx(custom_rate)


# ---------------------------------------------------------------------------
# (7) VAD outer timeout setting (§7.7, DC-AM-004)
# ---------------------------------------------------------------------------


class TestVadTimeout:
    """The outer timeout when launching VAD CLI must be max(60, ceil(total*4)) (§7.7)."""

    def test_vad_timeout_uses_max_60_or_duration_times_4(self, tmp_path: Path) -> None:
        """run is called with VAD timeout = max(60, ceil(total_duration * 4)) (§7.7).

        total_duration=10.0s -> max(60, ceil(40)) = 60.
        """
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)
        speech_json = _make_vad_speech_json([(1.0, 9.0)])

        captured_timeouts: list[float] = []

        def _capture_run(
            cmd: list[str], *, timeout: float = 60.0, **kwargs: Any
        ) -> CompletedProcess[str]:
            captured_timeouts.append(timeout)
            return CompletedProcess(
                args=cmd, returncode=0, stdout=speech_json, stderr=""
            )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch("clipwright_silence.detect.run", side_effect=_capture_run),
        ):
            detect_silence(media, output, _vad_opts())

        assert len(captured_timeouts) >= 1
        # total=10s → max(60, ceil(10*4))=max(60,40)=60
        assert captured_timeouts[0] == pytest.approx(
            max(60, math.ceil(10.0 * 4)), abs=1
        )

    def test_vad_timeout_exceeds_silencedetect_timeout_for_long_video(
        self, tmp_path: Path
    ) -> None:
        """Verify that VAD timeout for long footage (100s) exceeds silencedetect timeout.

        VAD: max(60, ceil(100*4))=400. silencedetect: max(60, ceil(100*2))=200.
        Both are actually launched separately and their timeouts compared (CR L-5 / CR-T-004).
        """
        from clipwright_silence.detect import detect_silence

        media_vad = str(tmp_path / "video_vad.mp4")
        Path(media_vad).touch()
        output_vad = str(tmp_path / "out_vad.otio")
        media_info = _make_media_info(path=media_vad, duration_sec=100.0)
        speech_json = _make_vad_speech_json([(10.0, 90.0)])

        vad_timeouts: list[float] = []

        def _capture_vad_run(
            cmd: list[str], *, timeout: float = 60.0, **kwargs: Any
        ) -> CompletedProcess[str]:
            vad_timeouts.append(timeout)
            return CompletedProcess(
                args=cmd, returncode=0, stdout=speech_json, stderr=""
            )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch("clipwright_silence.detect.run", side_effect=_capture_vad_run),
        ):
            detect_silence(media_vad, output_vad, _vad_opts())

        # get silencedetect path timeout
        media_sd = str(tmp_path / "video_sd.mp4")
        Path(media_sd).touch()
        output_sd = str(tmp_path / "out_sd.otio")
        media_info_sd = _make_media_info(path=media_sd, duration_sec=100.0)
        stderr_sd = _make_stderr([(10.0, 90.0)])

        sd_timeouts: list[float] = []

        def _capture_sd_run(
            cmd: list[str], *, timeout: float = 60.0, **kwargs: Any
        ) -> CompletedProcess[str]:
            sd_timeouts.append(timeout)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr=stderr_sd)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info_sd,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_capture_sd_run),
        ):
            detect_silence(media_sd, output_sd, _opts())

        assert len(vad_timeouts) >= 1
        assert len(sd_timeouts) >= 1
        # total=100s -> VAD: max(60, ceil(100*4))=400, silencedetect: max(60, ceil(100*2))=200
        vad_timeout = vad_timeouts[0]
        sd_timeout = sd_timeouts[0]
        assert vad_timeout == pytest.approx(400, abs=1)
        assert sd_timeout == pytest.approx(200, abs=1)
        # assert VAD timeout > silencedetect timeout (matching test name and content)
        assert vad_timeout > sd_timeout


# ===========================================================================
# SR H-1 [SR-R-001]: VAD CLI stdout empty/invalid JSON -> SUBPROCESS_FAILED
# ===========================================================================


class TestVadJsonDecodeDefense:
    """Must return a SUBPROCESS_FAILED envelope when VAD CLI stdout is empty or invalid JSON.

    _detect_vad_silence_intervals in detect.py must catch JSONDecodeError from json.loads
    and return SUBPROCESS_FAILED instead of propagating an unhandled exception (SR H-1 / SR-R-001).
    """

    def test_empty_stdout_returns_subprocess_failed(self, tmp_path: Path) -> None:
        """VAD CLI stdout is empty string -> must return SUBPROCESS_FAILED envelope (SR H-1)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        def _run_empty_stdout(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch("clipwright_silence.detect.run", side_effect=_run_empty_stdout),
        ):
            result = detect_silence(media, output, _vad_opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.SUBPROCESS_FAILED

    def test_invalid_json_stdout_returns_subprocess_failed(
        self, tmp_path: Path
    ) -> None:
        """VAD CLI stdout is invalid JSON -> must return SUBPROCESS_FAILED envelope (SR H-1)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        def _run_invalid_json(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(
                args=cmd, returncode=0, stdout="not json", stderr=""
            )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch("clipwright_silence.detect.run", side_effect=_run_invalid_json),
        ):
            result = detect_silence(media, output, _vad_opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.SUBPROCESS_FAILED


# ===========================================================================
# SR L-3 [SR-V-001]: malformed elements in speech_segments -> skip and continue
# ===========================================================================


class TestVadSpeechSegmentsMalformedElements:
    """Defense against malformed elements (null/string/empty dict, etc.) in speech_segments.

    The for loop in detect.py must catch TypeError/KeyError/IndexError and skip bad elements
    so that processing continues with valid intervals (SR L-3 / SR-V-001).
    """

    def test_null_element_in_speech_segments_is_skipped(self, tmp_path: Path) -> None:
        """null element in speech_segments must be skipped and processing continues (SR L-3)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        # null element mixed in: [null, [1.0, 5.0]] -> null skipped, [1.0, 5.0] valid
        malformed_json = json.dumps({"speech_segments": [None, [1.0, 5.0]]})

        def _run_malformed(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(
                args=cmd, returncode=0, stdout=malformed_json, stderr=""
            )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch("clipwright_silence.detect.run", side_effect=_run_malformed),
        ):
            result = detect_silence(media, output, _vad_opts(padding=0.0))

        # null skipped; KEEP is computed from [1.0, 5.0] speech interval -> ok=True
        assert result["ok"] is True

    def test_string_element_in_speech_segments_is_skipped(self, tmp_path: Path) -> None:
        """string element in speech_segments must be skipped and processing continues (SR L-3)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        # string element mixed in: ["abc", [2.0, 8.0]] -> "abc" skipped, [2.0, 8.0] valid
        malformed_json = json.dumps({"speech_segments": ["abc", [2.0, 8.0]]})

        def _run_malformed(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(
                args=cmd, returncode=0, stdout=malformed_json, stderr=""
            )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch("clipwright_silence.detect.run", side_effect=_run_malformed),
        ):
            result = detect_silence(media, output, _vad_opts(padding=0.0))

        assert result["ok"] is True

    def test_empty_dict_element_in_speech_segments_is_skipped(
        self, tmp_path: Path
    ) -> None:
        """empty dict element in speech_segments must be skipped and processing continues (SR L-3)."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        # empty dict mixed in: [{}, [3.0, 7.0]] -> {} skipped, [3.0, 7.0] valid
        malformed_json = json.dumps({"speech_segments": [{}, [3.0, 7.0]]})

        def _run_malformed(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(
                args=cmd, returncode=0, stdout=malformed_json, stderr=""
            )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch("clipwright_silence.detect.run", side_effect=_run_malformed),
        ):
            result = detect_silence(media, output, _vad_opts(padding=0.0))

        assert result["ok"] is True


# ===========================================================================
# CR M-2/SR M-2: detect must include --media-duration in the vad_cli launch cmd
# ===========================================================================


class TestVadMediaDurationArg:
    """Verify that the VAD CLI launch command contains --media-duration <total_duration_sec>.

    cmd must contain ["--media-duration", str(total_duration_sec)] (CR M-2/SR M-2).
    """

    def test_vad_cmd_contains_media_duration_arg(self, tmp_path: Path) -> None:
        """VAD CLI launch cmd must contain --media-duration (CR M-2 / SR M-2)."""
        from clipwright_silence.detect import detect_silence

        total_duration = 42.5
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=total_duration)
        speech_json = _make_vad_speech_json([(1.0, 40.0)])

        captured_cmds: list[list[str]] = []

        def _capture_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(
                args=cmd, returncode=0, stdout=speech_json, stderr=""
            )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch("clipwright_silence.detect.run", side_effect=_capture_run),
        ):
            detect_silence(media, output, _vad_opts())

        assert len(captured_cmds) >= 1
        cmd = captured_cmds[0]
        # --media-duration must be present in cmd
        assert "--media-duration" in cmd
        dur_idx = cmd.index("--media-duration")
        # value must be the string representation of total_duration_sec
        assert float(cmd[dur_idx + 1]) == pytest.approx(total_duration)
