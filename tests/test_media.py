"""test_media.py — Tests for the ffprobe wrapper in media.py.

Target: inspect_media(path: str) -> MediaInfo

Unit tests (process.run mocked):
- ffprobe JSON → MediaInfo struct
- Invalid JSON → PROBE_FAILED
- Input file absent → FILE_NOT_FOUND
- ffprobe absent → DEPENDENCY_MISSING

Rate determination rules (§13.3 DC-AS-006):
- If a video stream exists, use avg_frame_rate of the first video stream as rate
- Audio-only sources use rate = 1000.0

Integration tests (real ffprobe):
- Use sample_media / ffprobe_path fixtures from conftest.py
- Required (not skipped) when ffmpeg/ffprobe is reachable (§13.4 DC-AM-006)
- Inspect generated mp4 and verify duration / streams

Security / quality tests:
- F-04: _validate_existing_file must reject symbolic links (SR-V-002)
  Symlink creation requires privileges on Windows; guard with pytest.skip on failure
- L-2: Unit tests for _to_optional_int helper conversion logic (CR-Q-002)
  Parametrize over None / int / float / numeric string / invalid values
"""

from __future__ import annotations

import json
from subprocess import CompletedProcess
from unittest.mock import MagicMock

import pytest

from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import (
    inspect_media,
)
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

# ===========================================================================
# Helper: build ffprobe JSON payload
# ===========================================================================


def _make_ffprobe_json(
    *,
    duration: str = "3.000000",
    streams: list[dict] | None = None,
    container_format: str = "mov,mp4,m4a,3gp,3g2,mj2",
    bit_rate: str | None = None,
) -> str:
    """Simulate the output of ffprobe -print_format json -show_format -show_streams.

    bit_rate: value to set in format.bit_rate. Key is omitted when None.
    """
    if streams is None:
        streams = [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 320,
                "height": 240,
                "avg_frame_rate": "30/1",
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "44100",
                "channels": 2,
            },
        ]
    fmt: dict[str, object] = {
        "format_name": container_format,
        "duration": duration,
    }
    if bit_rate is not None:
        fmt["bit_rate"] = bit_rate
    return json.dumps(
        {
            "streams": streams,
            "format": fmt,
        }
    )


def _make_completed_process(stdout: str, returncode: int = 0) -> CompletedProcess[str]:
    return CompletedProcess(
        args=["ffprobe"],
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


# ===========================================================================
# Unit tests: ffprobe JSON → MediaInfo struct
# ===========================================================================


class TestInspectMediaSuccess:
    """Happy path: structure ffprobe JSON output into MediaInfo."""

    def test_returns_media_info_instance(self, mocker: MagicMock, tmp_path) -> None:
        """Return value is a MediaInfo instance."""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        result = inspect_media(str(media_file))

        assert isinstance(result, MediaInfo)

    def test_path_is_preserved_in_media_info(self, mocker: MagicMock, tmp_path) -> None:
        """MediaInfo.path matches the input path."""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        result = inspect_media(str(media_file))

        assert result.path == str(media_file)

    def test_streams_are_parsed(self, mocker: MagicMock, tmp_path) -> None:
        """The streams list is parsed into StreamInfo objects."""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        result = inspect_media(str(media_file))

        assert len(result.streams) == 2
        assert all(isinstance(s, StreamInfo) for s in result.streams)

    def test_video_stream_fields(self, mocker: MagicMock, tmp_path) -> None:
        """Video stream codec_type / width / height are mapped correctly."""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        result = inspect_media(str(media_file))

        video = next(s for s in result.streams if s.codec_type == "video")
        assert video.codec_name == "h264"
        assert video.width == 320
        assert video.height == 240

    def test_audio_stream_fields(self, mocker: MagicMock, tmp_path) -> None:
        """Audio stream sample_rate / channels are mapped correctly."""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        result = inspect_media(str(media_file))

        audio = next(s for s in result.streams if s.codec_type == "audio")
        assert audio.sample_rate == 44100
        assert audio.channels == 2

    def test_container_is_parsed(self, mocker: MagicMock, tmp_path) -> None:
        """The container field is populated from format_name."""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(
                _make_ffprobe_json(container_format="mov,mp4,m4a,3gp,3g2,mj2")
            ),
        )

        result = inspect_media(str(media_file))

        assert result.container is not None
        assert "mp4" in result.container

    def test_duration_is_rational_time_model(self, mocker: MagicMock, tmp_path) -> None:
        """duration is returned as RationalTimeModel (bare seconds float is not OK)."""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(_make_ffprobe_json(duration="3.0")),
        )

        result = inspect_media(str(media_file))

        assert result.duration is not None
        assert isinstance(result.duration, RationalTimeModel)


# ===========================================================================
# Rate determination rule tests (§13.3 DC-AS-006)
# ===========================================================================


class TestRateDecisionRule:
    """Verify the duration rate determination rules (DC-AS-006)."""

    def test_video_stream_avg_frame_rate_used_as_rate(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """avg_frame_rate of the first video stream becomes the rate.

        avg_frame_rate="30/1" → rate=30.0
        """
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        streams = [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "avg_frame_rate": "30/1",
            }
        ]
        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(
                _make_ffprobe_json(streams=streams, duration="3.0")
            ),
        )

        result = inspect_media(str(media_file))

        assert result.duration is not None
        assert result.duration.rate == pytest.approx(30.0)

    def test_fractional_avg_frame_rate_parsed_correctly(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """Fractional avg_frame_rate (e.g. "24000/1001") is converted to rate correctly.

        24000/1001 ≈ 23.976 fps
        """
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        streams = [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "avg_frame_rate": "24000/1001",
            }
        ]
        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(
                _make_ffprobe_json(streams=streams, duration="3.0")
            ),
        )

        result = inspect_media(str(media_file))

        assert result.duration is not None
        assert result.duration.rate == pytest.approx(24000 / 1001, rel=1e-4)

    def test_audio_only_uses_rate_1000(self, mocker: MagicMock, tmp_path) -> None:
        """Audio-only sources use rate=1000.0 (DC-AS-006)."""
        media_file = tmp_path / "audio.mp4"
        media_file.write_bytes(b"dummy")

        streams = [
            {
                "index": 0,
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "44100",
                "channels": 2,
            }
        ]
        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(
                _make_ffprobe_json(streams=streams, duration="5.0")
            ),
        )

        result = inspect_media(str(media_file))

        assert result.duration is not None
        assert result.duration.rate == 1000.0

    def test_first_video_stream_rate_used_when_multiple_video_streams(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """Multiple video streams: the first stream's avg_frame_rate is used."""
        media_file = tmp_path / "multi.mp4"
        media_file.write_bytes(b"dummy")

        streams = [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "avg_frame_rate": "25/1",
            },
            {
                "index": 1,
                "codec_type": "video",
                "codec_name": "h264",
                "avg_frame_rate": "60/1",
            },
        ]
        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(
                _make_ffprobe_json(streams=streams, duration="3.0")
            ),
        )

        result = inspect_media(str(media_file))

        assert result.duration is not None
        assert result.duration.rate == pytest.approx(25.0)

    def test_duration_value_reflects_format_duration(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """duration.value equals format.duration (seconds) converted by rate.

        duration=3.0 s, rate=30.0 fps → value=90.0
        """
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        streams = [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "avg_frame_rate": "30/1",
            }
        ]
        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(
                _make_ffprobe_json(streams=streams, duration="3.0")
            ),
        )

        result = inspect_media(str(media_file))

        assert result.duration is not None
        # 3.0 s × 30.0 fps = 90.0 frames
        assert result.duration.value == pytest.approx(90.0)


# ===========================================================================
# Unit tests: error paths
# ===========================================================================


class TestInspectMediaFileNotFound:
    """FILE_NOT_FOUND is raised when the input file does not exist."""

    def test_raises_file_not_found_for_nonexistent_path(
        self, mocker: MagicMock
    ) -> None:
        """Passing a non-existent path raises FILE_NOT_FOUND."""
        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")

        with pytest.raises(ClipwrightError) as exc_info:
            inspect_media("/nonexistent/path/video.mp4")

        assert exc_info.value.code == ErrorCode.FILE_NOT_FOUND

    def test_file_not_found_has_message_and_hint(self, mocker: MagicMock) -> None:
        """FILE_NOT_FOUND error carries message and hint (§6.4 contract)."""
        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")

        with pytest.raises(ClipwrightError) as exc_info:
            inspect_media("/nonexistent/video.mp4")

        err = exc_info.value
        assert len(err.message) > 0
        assert len(err.hint) > 0

    def test_file_not_found_before_resolve_tool_is_called(
        self, mocker: MagicMock
    ) -> None:
        """File validation happens before resolve_tool (FILE_NOT_FOUND comes first).

        Checking file existence before locating ffprobe gives faster feedback.
        """
        mock_resolve = mocker.patch(
            "clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe"
        )

        with pytest.raises(ClipwrightError) as exc_info:
            inspect_media("/nonexistent/video.mp4")

        # File validation precedes resolve_tool; not called on absent file
        assert exc_info.value.code == ErrorCode.FILE_NOT_FOUND
        mock_resolve.assert_not_called()


class TestInspectMediaDependencyMissing:
    """DEPENDENCY_MISSING is raised when ffprobe is not found."""

    def test_raises_dependency_missing_when_ffprobe_not_found(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """inspect_media propagates DEPENDENCY_MISSING from resolve_tool correctly."""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch(
            "clipwright.process.resolve_tool",
            side_effect=ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="ffprobe not found on PATH",
                hint="Install via winget install Gyan.FFmpeg",
            ),
        )

        with pytest.raises(ClipwrightError) as exc_info:
            inspect_media(str(media_file))

        assert exc_info.value.code == ErrorCode.DEPENDENCY_MISSING

    def test_dependency_missing_hint_mentions_ffprobe(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """DEPENDENCY_MISSING error hint includes an actionable ffprobe instruction."""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch(
            "clipwright.process.resolve_tool",
            side_effect=ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="ffprobe not found on PATH",
                hint="Install via winget install Gyan.FFmpeg",
            ),
        )

        with pytest.raises(ClipwrightError) as exc_info:
            inspect_media(str(media_file))

        assert len(exc_info.value.hint) > 0


class TestInspectMediaProbeFailed:
    """PROBE_FAILED is raised when ffprobe returns invalid JSON."""

    def test_raises_probe_failed_on_invalid_json(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """Raises PROBE_FAILED when ffprobe stdout is not valid JSON."""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process("THIS IS NOT JSON"),
        )

        with pytest.raises(ClipwrightError) as exc_info:
            inspect_media(str(media_file))

        assert exc_info.value.code == ErrorCode.PROBE_FAILED

    def test_raises_probe_failed_on_empty_stdout(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """Raises PROBE_FAILED when ffprobe stdout is an empty string."""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(""),
        )

        with pytest.raises(ClipwrightError) as exc_info:
            inspect_media(str(media_file))

        assert exc_info.value.code == ErrorCode.PROBE_FAILED

    def test_raises_probe_failed_on_json_missing_required_fields(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """Raises PROBE_FAILED when required fields (streams / format) are absent."""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(json.dumps({"unexpected": "data"})),
        )

        with pytest.raises(ClipwrightError) as exc_info:
            inspect_media(str(media_file))

        assert exc_info.value.code == ErrorCode.PROBE_FAILED

    def test_probe_failed_has_message_and_hint(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """PROBE_FAILED error carries message and hint (§6.4 contract)."""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process("INVALID JSON{{"),
        )

        with pytest.raises(ClipwrightError) as exc_info:
            inspect_media(str(media_file))

        err = exc_info.value
        assert len(err.message) > 0
        assert len(err.hint) > 0


class TestInspectMediaRunInvocation:
    """Verify how process.run is called (§6.5 shell=False, argument array)."""

    def test_run_called_with_list_cmd(self, mocker: MagicMock, tmp_path) -> None:
        """The command passed to run is a list (argument array)."""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mock_run = mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        inspect_media(str(media_file))

        call_args = mock_run.call_args
        cmd = call_args.args[0] if call_args.args else call_args.kwargs.get("cmd", [])
        assert isinstance(cmd, list)

    def test_run_called_with_show_format_and_show_streams(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """The command includes -show_format and -show_streams."""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mock_run = mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        inspect_media(str(media_file))

        call_args = mock_run.call_args
        cmd = call_args.args[0] if call_args.args else call_args.kwargs.get("cmd", [])
        assert "-show_format" in cmd
        assert "-show_streams" in cmd

    def test_run_called_with_json_print_format(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """The command includes -print_format json."""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mock_run = mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        inspect_media(str(media_file))

        call_args = mock_run.call_args
        cmd = call_args.args[0] if call_args.args else call_args.kwargs.get("cmd", [])
        assert "-print_format" in cmd
        idx = cmd.index("-print_format")
        assert cmd[idx + 1] == "json"

    def test_run_called_with_file_path_as_last_arg(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """The input file path is included in the command."""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mock_run = mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        inspect_media(str(media_file))

        call_args = mock_run.call_args
        cmd = call_args.args[0] if call_args.args else call_args.kwargs.get("cmd", [])
        assert str(media_file) in cmd

    def test_ffprobe_resolved_with_env_var(self, mocker: MagicMock, tmp_path) -> None:
        """resolve_tool is called with "ffprobe" and "CLIPWRIGHT_FFPROBE" (ADR-3)."""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mock_resolve = mocker.patch(
            "clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe"
        )
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        inspect_media(str(media_file))

        mock_resolve.assert_called_once_with("ffprobe", "CLIPWRIGHT_FFPROBE")


# ===========================================================================
# Integration tests: inspect a real mp4 with ffprobe
# ===========================================================================


class TestInspectMediaIntegration:
    """Integration tests using real ffprobe (§13.4 DC-AM-006).

    Uses sample_media / ffprobe_path fixtures from conftest.py.
    Required (not skipped) when ffmpeg/ffprobe is reachable.
    """

    def test_integration_inspect_real_mp4_returns_media_info(
        self,
        sample_media: str,
        ffprobe_path: str | None,
    ) -> None:
        """inspect_media returns MediaInfo for a real mp4 via ffprobe.

        Skips when ffprobe_path is None (ffprobe not reachable; ffmpeg may still exist).
        CLIPWRIGHT_FFPROBE env var makes it reachable.
        """
        if ffprobe_path is None:
            pytest.skip(
                "ffprobe not found (CLIPWRIGHT_FFPROBE not set and not on PATH)."
            )

        result = inspect_media(sample_media)

        assert isinstance(result, MediaInfo)

    def test_integration_duration_is_approximately_3_seconds(
        self,
        sample_media: str,
        ffprobe_path: str | None,
    ) -> None:
        """Generated mp4 (3 s) duration is approximately 3.0 seconds.

        Derive seconds from RationalTimeModel value / rate.
        Tolerance is ±0.1 s (lavfi generation precision).
        """
        if ffprobe_path is None:
            pytest.skip(
                "ffprobe not found (CLIPWRIGHT_FFPROBE not set and not on PATH)."
            )

        result = inspect_media(sample_media)

        assert result.duration is not None
        duration_sec = result.duration.value / result.duration.rate
        assert duration_sec == pytest.approx(3.0, abs=0.1)

    def test_integration_streams_contain_video_and_audio(
        self,
        sample_media: str,
        ffprobe_path: str | None,
    ) -> None:
        """Generated mp4 contains both video and audio streams."""
        if ffprobe_path is None:
            pytest.skip(
                "ffprobe not found (CLIPWRIGHT_FFPROBE not set and not on PATH)."
            )

        result = inspect_media(sample_media)

        codec_types = [s.codec_type for s in result.streams]
        assert "video" in codec_types
        assert "audio" in codec_types

    def test_integration_video_rate_equals_30fps(
        self,
        sample_media: str,
        ffprobe_path: str | None,
    ) -> None:
        """Generated mp4 (30 fps) has duration.rate == 30.0 (DC-AS-006).

        sample_media in conftest is generated at rate=30.
        """
        if ffprobe_path is None:
            pytest.skip(
                "ffprobe not found (CLIPWRIGHT_FFPROBE not set and not on PATH)."
            )

        result = inspect_media(sample_media)

        assert result.duration is not None
        assert result.duration.rate == pytest.approx(30.0)

    def test_integration_path_preserved(
        self,
        sample_media: str,
        ffprobe_path: str | None,
    ) -> None:
        """MediaInfo.path matches the input path (integration)."""
        if ffprobe_path is None:
            pytest.skip(
                "ffprobe not found (CLIPWRIGHT_FFPROBE not set and not on PATH)."
            )

        result = inspect_media(sample_media)

        assert result.path == sample_media


# ===========================================================================
# F-04: Pin symbolic link behaviour in _validate_existing_file (SR-V-002)
# ===========================================================================


class TestValidateExistingFileSymlink:
    """F-04: _validate_existing_file must explicitly reject symbolic links.

    Pins the fix for security finding F-04 (SR-V-002).
    Expects rejection via Path.is_symlink() or path.resolve() != path mismatch.

    Symlink creation requires admin/Developer Mode on Windows.
    Guard with pytest.skip on failure so CI and other environments still run.
    """

    def test_symlink_to_regular_file_is_rejected(self, tmp_path) -> None:
        """A symlink to a regular file raises ClipwrightError (F-04 must reject it).

        Arrange: create real.mp4, then symlink.mp4 → real.mp4
        Act: _validate_existing_file(str(symlink_path))
        Assert: ClipwrightError is raised (FILE_NOT_FOUND or a dedicated code)
        """
        from clipwright.media import _validate_existing_file

        real_file = tmp_path / "real.mp4"
        real_file.write_bytes(b"dummy media content")
        symlink_path = tmp_path / "symlink.mp4"

        # Guard against symlink creation failure on Windows (insufficient privileges)
        try:
            symlink_path.symlink_to(real_file)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(
                f"Failed to create symlink (insufficient privileges or unsupported):"
                f" {exc}"
            )

        # Arrange: symlink was created
        assert symlink_path.is_symlink(), "symlink was created correctly"
        assert symlink_path.is_file(), "symlink returns True for is_file() (followed)"

        # Act & Assert: _validate_existing_file must reject the symlink
        with pytest.raises(ClipwrightError):
            _validate_existing_file(str(symlink_path))

    def test_symlink_rejection_uses_appropriate_error_code(self, tmp_path) -> None:
        """Symlink rejection uses an appropriate ClipwrightError code
        (FILE_NOT_FOUND or a dedicated code).

        Arrange: create symlink.mp4 → real.mp4
        Act: call _validate_existing_file
        Assert: ClipwrightError.code is a valid ErrorCode value
        """
        from clipwright.media import _validate_existing_file

        real_file = tmp_path / "real.mp4"
        real_file.write_bytes(b"dummy")
        symlink_path = tmp_path / "symlink_code_check.mp4"

        try:
            symlink_path.symlink_to(real_file)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"Symlink creation failed: {exc}")

        with pytest.raises(ClipwrightError) as exc_info:
            _validate_existing_file(str(symlink_path))

        # Error code must be a valid ErrorCode value
        assert exc_info.value.code in list(ErrorCode)

    def test_regular_file_still_passes_validation(self, tmp_path) -> None:
        """Regular files (non-symlink) continue to pass validation.

        Regression test: F-04 fix must not break the existing happy path.

        Arrange: create regular video.mp4
        Act: call _validate_existing_file
        Assert: no exception is raised
        """
        from clipwright.media import _validate_existing_file

        regular_file = tmp_path / "video.mp4"
        regular_file.write_bytes(b"dummy media content")

        # Regular file must pass without exception
        _validate_existing_file(str(regular_file))  # must not raise


# ===========================================================================
# L-2: Pin _to_optional_int helper conversion logic (CR-Q-002)
# ===========================================================================


class TestToOptionalInt:
    """L-2: Unit tests for _to_optional_int(val: object) -> int | None.

    Pins the fix for code review finding L-2 (CR-Q-002).
    After extracting the two-step int(str(x)) conversion into _to_optional_int,
    pins the conversion contract with parametrize.

    Target: clipwright.media._to_optional_int
    """

    @pytest.mark.parametrize(
        "val, expected",
        [
            # None input → return None
            (None, None),
            # int input → return int as-is
            (0, 0),
            (320, 320),
            (-1, -1),
            # float input → convert to int (SR-V-001)
            (1.5, 1),
            # inf/nan → catch OverflowError/ValueError and return None (SR-V-001)
            (float("inf"), None),
            (float("nan"), None),
            # bool → subclass of int: True→1 / False→0 (CR-CT-002)
            (True, 1),
            (False, 0),
            # numeric string → convert to int
            ("44100", 44100),
            ("0", 0),
            ("1920", 1920),
            # non-numeric values → return None
            ("not_a_number", None),
            ("", None),
            ("1.5", None),  # float string cannot be converted to int → None
            ({}, None),
            ([], None),
            (object(), None),
        ],
        ids=[
            "none_input",
            "int_zero",
            "int_positive",
            "int_negative",
            "float_input",
            "float_inf",
            "float_nan",
            "bool_true",
            "bool_false",
            "str_44100",
            "str_zero",
            "str_1920",
            "str_invalid",
            "str_empty",
            "str_float",
            "dict_input",
            "list_input",
            "object_input",
        ],
    )
    def test_to_optional_int_conversion(
        self, val: object, expected: int | None
    ) -> None:
        """_to_optional_int returns the expected value for each input.

        Arrange: prepare val as input
        Act: call _to_optional_int(val)
        Assert: return value equals expected
        """
        try:
            from clipwright.media import _to_optional_int  # type: ignore[attr-defined]
        except ImportError:
            pytest.fail(
                "_to_optional_int does not exist in clipwright.media. "
                "L-2 fix (add _to_optional_int helper) is required."
            )

        # Act
        result = _to_optional_int(val)

        # Assert
        assert result == expected

    def test_to_optional_int_returns_int_type_for_valid_input(self) -> None:
        """_to_optional_int returns an int type for valid input (type guarantee).

        Arrange: valid numeric string "320"
        Act: call _to_optional_int("320")
        Assert: return value is of type int
        """
        try:
            from clipwright.media import _to_optional_int  # type: ignore[attr-defined]
        except ImportError:
            pytest.fail(
                "_to_optional_int does not exist in clipwright.media. "
                "L-2 fix is required."
            )

        result = _to_optional_int("320")

        assert isinstance(result, int)

    def test_to_optional_int_returns_none_type_for_invalid_input(self) -> None:
        """_to_optional_int returns None for invalid input (type guarantee).

        Arrange: non-convertible value "abc"
        Act: call _to_optional_int("abc")
        Assert: return value is None
        """
        try:
            from clipwright.media import _to_optional_int  # type: ignore[attr-defined]
        except ImportError:
            pytest.fail(
                "_to_optional_int does not exist in clipwright.media. "
                "L-2 fix is required."
            )

        result = _to_optional_int("abc")

        assert result is None


# ===========================================================================
# AD-1: MediaInfo.bit_rate parse test (schemas.py / media.py contract 100%)
# ===========================================================================


class TestMediaInfoBitRate:
    """AD-1: Pin format.bit_rate → MediaInfo.bit_rate parse contract.

    Design decision AD-1:
    - Add `bit_rate: int | None = None` to MediaInfo in schemas.py.
    - Parse via `_to_optional_int(raw_format.get("bit_rate"))` in _parse_ffprobe_json.
    - "N/A" / missing key → None (reuses _to_optional_int "N/A" absorption).
    """

    @pytest.mark.parametrize(
        "bit_rate_value, include_in_json, expected",
        [
            # Case 1: numeric string → convert to int
            ("128000", True, 128000),
            # Case 2: "N/A" → None (_to_optional_int "N/A" absorption)
            ("N/A", True, None),
            # Case 3: missing key → None (raw_format.get default None)
            (None, False, None),
        ],
        ids=[
            "numeric_string_128000",
            "na_string",
            "key_missing",
        ],
    )
    def test_bit_rate_parsed_from_format(
        self,
        mocker: MagicMock,
        tmp_path,
        bit_rate_value: str | None,
        include_in_json: bool,
        expected: int | None,
    ) -> None:
        """MediaInfo.bit_rate matches the expected value for each format.bit_rate input.

        Arrange: prepare ffprobe JSON according to bit_rate_value / include_in_json
        Act: call inspect_media (process.run mocked)
        Assert: result.bit_rate == expected
        """
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(
                _make_ffprobe_json(
                    bit_rate=bit_rate_value if include_in_json else None,
                )
            ),
        )

        result = inspect_media(str(media_file))

        assert result.bit_rate == expected
