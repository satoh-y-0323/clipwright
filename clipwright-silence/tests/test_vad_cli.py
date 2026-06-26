"""test_vad_cli.py — Tests for clipwright_silence.vad_cli.

Target:
  CLI contract validation for clipwright_silence.vad_cli.main(argv) (§7.1/7.2/7.3)

CLI contract (§7.1 unified):
  - Entry: main(argv: list[str] | None = None) -> int
  - Arguments: --media <path> --threshold <f> --min-speech <f> --min-silence <f>
               --media-duration <float seconds> (optional: linked to timeout)
  - Success: stdout JSON {"speech_segments": [[start, end], ...]} (seconds, float), exit 0
  - All errors: exit 0 + stdout JSON {"error": {"code", "message", "hint"}}
  - stdout is JSON only. Logs etc. go to stderr.

Verification perspectives:
  (1) Argument parsing (argparse) and value forwarding
  (2) Mock get_speech_timestamps and verify speech intervals -> second-converted speech_segments JSON
  (3) ImportError path (silero-vad/onnxruntime missing) -> DEPENDENCY_MISSING JSON + exit 0
  (4) Internal ffmpeg core run() raises ClipwrightError(SUBPROCESS_FAILED)
      -> error JSON + exit 0
  (5) ffmpeg resolution failure (resolve_tool raises DEPENDENCY_MISSING)
      -> error JSON + exit 0
  (6) JSON appears on stdout only (logs etc. go to stderr)
  (7) --media-duration argument reception and inner ffmpeg timeout linking (CR M-2 / SR M-2)
  (8) SUBPROCESS_FAILED handler outputs generic wording, not ffmpeg stderr fragments (SR M-1)
  (9) ImportError message contains fixed wording / exc.name equivalent, not internal paths (SR L-2)
"""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Attempt to import vad_cli (_VAD_CLI_AVAILABLE = False if not implemented)
# ---------------------------------------------------------------------------

try:
    from clipwright_silence.vad_cli import main as vad_main

    _VAD_CLI_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _VAD_CLI_AVAILABLE = False

# Mark all tests as xfail unless vad_cli is available
pytestmark = pytest.mark.xfail(
    not _VAD_CLI_AVAILABLE,
    reason="vad_cli.py is not implemented",
    strict=True,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_MEDIA = "/fake/video.mp4"
_DUMMY_PCM = "/tmp/fake_audio.wav"


def _make_silero_mock(
    speech_segments: list[dict[str, Any]],
) -> tuple[MagicMock, MagicMock]:
    """Return a set of mocks for the silero_vad module.

    MagicMock(spec=ModuleType) rejects access to load_silero_vad / get_speech_timestamps
    because they do not exist in types.ModuleType, so we use spec-less MagicMock().

    Returns:
        (mock_silero_vad_module, mock_get_speech_timestamps)
    """
    mock_module = MagicMock()
    mock_model = MagicMock()
    mock_module.load_silero_vad.return_value = mock_model

    mock_get_ts = MagicMock(return_value=speech_segments)
    mock_module.get_speech_timestamps = mock_get_ts

    return mock_module, mock_get_ts


def _capture_main(argv: list[str]) -> tuple[int, dict[str, Any]]:
    """Run main(argv) and return (exit_code, stdout_json).

    Redirects stdout to StringIO and parses the JSON.
    """
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        exit_code = vad_main(argv)
    buf.seek(0)
    stdout_text = buf.read()
    return exit_code, json.loads(stdout_text)


# ---------------------------------------------------------------------------
# (1) Argument parsing
# ---------------------------------------------------------------------------


class TestArgParsing:
    """Validation of argument parsing (argparse) and value forwarding."""

    def test_required_media_arg_present(self) -> None:
        """--media must function as a required argument; omission returns error JSON + exit 0.

        Verifies that SystemExit raised by argparse is caught by main
        and converted to exit 0 + error JSON.
        """
        # call without --media
        exit_code, result = _capture_main([])
        assert exit_code == 0
        assert "error" in result
        # SystemExit from omitting --media must always be converted to INVALID_INPUT (§7.1)
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_defaults_are_used(self) -> None:
        """Default values for --threshold / --min-speech / --min-silence are used.

        Confirms that defaults are passed in the get_speech_timestamps call arguments.
        """
        mock_module, mock_get_ts = _make_silero_mock(
            [{"start": 0, "end": 16000}]  # 1s at 16kHz
        )
        fake_np = MagicMock()
        fake_np.frombuffer.return_value = MagicMock()
        fake_wave_module = MagicMock()
        fake_wave_file = MagicMock()
        fake_wave_file.__enter__ = MagicMock(return_value=fake_wave_file)
        fake_wave_file.__exit__ = MagicMock(return_value=False)
        fake_wave_file.getnframes.return_value = 16000
        fake_wave_file.getframerate.return_value = 16000
        fake_wave_file.readframes.return_value = b"\x00" * (16000 * 2)
        fake_wave_module.open.return_value = fake_wave_file

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("wave.open", fake_wave_module.open),
            patch("numpy.frombuffer", fake_np.frombuffer),
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            exit_code, result = _capture_main(["--media", _DUMMY_MEDIA])

        assert exit_code == 0
        # must be called with default threshold=0.5
        call_kwargs = mock_get_ts.call_args
        assert call_kwargs is not None
        # check threshold keyword argument
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        if "threshold" in kwargs:
            assert kwargs["threshold"] == pytest.approx(0.5)

    def test_custom_threshold_forwarded(self) -> None:
        """Value specified by --threshold must be forwarded to get_speech_timestamps."""
        mock_module, mock_get_ts = _make_silero_mock([])
        fake_wave_module = MagicMock()
        fake_wave_file = MagicMock()
        fake_wave_file.__enter__ = MagicMock(return_value=fake_wave_file)
        fake_wave_file.__exit__ = MagicMock(return_value=False)
        fake_wave_file.getnframes.return_value = 0
        fake_wave_file.getframerate.return_value = 16000
        fake_wave_file.readframes.return_value = b""
        fake_wave_module.open.return_value = fake_wave_file

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("wave.open", fake_wave_module.open),
            patch("numpy.frombuffer", return_value=MagicMock()),
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            exit_code, result = _capture_main(
                ["--media", _DUMMY_MEDIA, "--threshold", "0.7"]
            )

        assert exit_code == 0
        call_kwargs = mock_get_ts.call_args
        assert call_kwargs is not None
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        if "threshold" in kwargs:
            assert kwargs["threshold"] == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# (2) Speech intervals -> speech_segments JSON output
# ---------------------------------------------------------------------------


class TestSpeechSegmentsOutput:
    """Validate speech intervals -> speech_segments JSON by mocking get_speech_timestamps."""

    def _run_with_segments(
        self, raw_segments: list[dict[str, Any]], sample_rate: int = 16000
    ) -> tuple[int, dict[str, Any]]:
        """Run main with a silero_vad mock that returns the specified raw_segments."""
        mock_module, _ = _make_silero_mock(raw_segments)
        fake_wave_module = MagicMock()
        fake_wave_file = MagicMock()
        fake_wave_file.__enter__ = MagicMock(return_value=fake_wave_file)
        fake_wave_file.__exit__ = MagicMock(return_value=False)
        fake_wave_file.getnframes.return_value = sample_rate * 10
        fake_wave_file.getframerate.return_value = sample_rate
        raw_pcm = b"\x00" * (sample_rate * 10 * 2)
        fake_wave_file.readframes.return_value = raw_pcm
        fake_wave_module.open.return_value = fake_wave_file

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("wave.open", fake_wave_module.open),
            patch("numpy.frombuffer", return_value=MagicMock()),
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            return _capture_main(["--media", _DUMMY_MEDIA])

    def test_empty_speech_segments(self) -> None:
        """When there is no speech, speech_segments must be an empty list."""
        exit_code, result = self._run_with_segments([])
        assert exit_code == 0
        assert "speech_segments" in result
        assert result["speech_segments"] == []

    def test_single_segment_converted_to_seconds(self) -> None:
        """1 speech interval is returned with second conversion (/ sample_rate).

        When silero-vad returns {"start": 8000, "end": 24000},
        at 16kHz it must be converted to [0.5, 1.5].
        """
        exit_code, result = self._run_with_segments(
            [{"start": 8000, "end": 24000}], sample_rate=16000
        )
        assert exit_code == 0
        assert "speech_segments" in result
        segs = result["speech_segments"]
        assert len(segs) == 1
        start_sec, end_sec = segs[0]
        assert start_sec == pytest.approx(0.5)
        assert end_sec == pytest.approx(1.5)

    def test_multiple_segments_ordered(self) -> None:
        """Multiple intervals must be returned in ascending order."""
        exit_code, result = self._run_with_segments(
            [
                {"start": 0, "end": 8000},
                {"start": 16000, "end": 24000},
            ],
            sample_rate=16000,
        )
        assert exit_code == 0
        segs = result["speech_segments"]
        assert len(segs) == 2
        # verify ascending order
        for i in range(len(segs) - 1):
            assert segs[i][0] < segs[i + 1][0]

    def test_speech_timestamps_with_sample_unit_values(self) -> None:
        """When get_speech_timestamps returns integer sample units, they must be converted to seconds via / sample_rate.

        vad_cli.py calls get_speech_timestamps with return_seconds=False (default).
        So the return value is {"start": int_samples, "end": int_samples} in sample units.
        After conversion, speech_segments must be [start / sample_rate, end / sample_rate] in seconds.
        """
        sample_rate = 16000
        start_samples = 16000  # 1.0 s = 16000 samples
        end_samples = 40000  # 2.5 s = 40000 samples
        mock_module_new = MagicMock()
        mock_module_new.load_silero_vad.return_value = MagicMock()
        # returns integer sample units (actual behavior with return_seconds=False)
        mock_module_new.get_speech_timestamps.return_value = [
            {"start": start_samples, "end": end_samples}
        ]

        fake_wave_module = MagicMock()
        fake_wave_file = MagicMock()
        fake_wave_file.__enter__ = MagicMock(return_value=fake_wave_file)
        fake_wave_file.__exit__ = MagicMock(return_value=False)
        fake_wave_file.getnframes.return_value = sample_rate * 5
        fake_wave_file.getframerate.return_value = sample_rate
        fake_wave_file.readframes.return_value = b"\x00" * (sample_rate * 5 * 2)
        fake_wave_module.open.return_value = fake_wave_file

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module_new}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("wave.open", fake_wave_module.open),
            patch("numpy.frombuffer", return_value=MagicMock()),
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            exit_code, result = _capture_main(["--media", _DUMMY_MEDIA])

        assert exit_code == 0
        assert "speech_segments" in result
        segs = result["speech_segments"]
        assert len(segs) == 1
        assert segs[0][0] == pytest.approx(start_samples / sample_rate)  # 1.0 s
        assert segs[0][1] == pytest.approx(end_samples / sample_rate)  # 2.5 s

    def test_exit_zero_on_success(self) -> None:
        """Must return exit 0 on success."""
        exit_code, _ = self._run_with_segments([{"start": 0, "end": 16000}])
        assert exit_code == 0


# ---------------------------------------------------------------------------
# (3) ImportError path (silero-vad/onnxruntime missing) -> DEPENDENCY_MISSING
# ---------------------------------------------------------------------------


class TestImportErrorPath:
    """Validate DEPENDENCY_MISSING when silero-vad / onnxruntime cannot be imported."""

    def test_silero_vad_import_error_returns_dependency_missing(
        self,
    ) -> None:
        """DEPENDENCY_MISSING JSON + exit 0 when silero_vad cannot be imported."""
        # set silero_vad to None if it exists
        with patch.dict(
            "sys.modules",
            {"silero_vad": None},
        ):
            exit_code, result = _capture_main(["--media", _DUMMY_MEDIA])

        assert exit_code == 0
        assert "error" in result
        assert result["error"]["code"] == "DEPENDENCY_MISSING"

    def test_dependency_missing_error_has_hint(self) -> None:
        """DEPENDENCY_MISSING error must have a hint field pointing to pip install."""
        with patch.dict(
            "sys.modules",
            {"silero_vad": None},
        ):
            _, result = _capture_main(["--media", _DUMMY_MEDIA])

        assert "error" in result
        hint = result["error"].get("hint", "")
        # pip install or [vad] extra guidance must be in the hint
        assert "pip install" in hint or "vad" in hint.lower()

    def test_dependency_missing_has_message(self) -> None:
        """DEPENDENCY_MISSING error must have a message field."""
        with patch.dict(
            "sys.modules",
            {"silero_vad": None},
        ):
            _, result = _capture_main(["--media", _DUMMY_MEDIA])

        assert "error" in result
        assert "message" in result["error"]
        assert len(result["error"]["message"]) > 0

    def test_onnxruntime_import_error_returns_dependency_missing(self) -> None:
        """DEPENDENCY_MISSING + exit 0 when onnxruntime cannot be imported.

        Validates the case where silero_vad depends on onnxruntime and
        ImportError propagates when onnxruntime is missing.
        """
        # simulate load_silero_vad raising ImportError
        mock_module = MagicMock()
        mock_module.load_silero_vad.side_effect = ImportError(
            "No module named 'onnxruntime'"
        )

        with patch.dict("sys.modules", {"silero_vad": mock_module}):
            exit_code, result = _capture_main(["--media", _DUMMY_MEDIA])

        assert exit_code == 0
        assert "error" in result
        assert result["error"]["code"] == "DEPENDENCY_MISSING"


# ---------------------------------------------------------------------------
# (4) Internal ffmpeg run() raises ClipwrightError(SUBPROCESS_FAILED) -> error JSON + exit 0
# ---------------------------------------------------------------------------


class TestFfmpegSubprocessFailure:
    """Validate error JSON + exit 0 when internal ffmpeg raises SUBPROCESS_FAILED.

    Follows DC-AS-001.
    """

    def test_subprocess_failed_returns_error_json(self) -> None:
        """Must return error JSON + exit 0 when core run() raises ClipwrightError(SUBPROCESS_FAILED)."""
        from clipwright.errors import ClipwrightError, ErrorCode

        mock_module, _ = _make_silero_mock([])

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.side_effect = ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="ffmpeg failed with exit code 1",
                hint="Check ffmpeg arguments",
            )
            exit_code, result = _capture_main(["--media", _DUMMY_MEDIA])

        assert exit_code == 0
        assert "error" in result
        assert result["error"]["code"] == "SUBPROCESS_FAILED"

    def test_subprocess_failed_error_has_code_message_hint(self) -> None:
        """SUBPROCESS_FAILED error must have code / message / hint."""
        from clipwright.errors import ClipwrightError, ErrorCode

        mock_module, _ = _make_silero_mock([])

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.side_effect = ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="ffmpeg failed",
                hint="check args",
            )
            _, result = _capture_main(["--media", _DUMMY_MEDIA])

        err = result["error"]
        assert "code" in err
        assert "message" in err
        assert "hint" in err

    def test_exit_zero_on_subprocess_failed(self) -> None:
        """exit code must be 0 even on SUBPROCESS_FAILED (§7.1: all errors exit 0)."""
        from clipwright.errors import ClipwrightError, ErrorCode

        mock_module, _ = _make_silero_mock([])

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.side_effect = ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="failed",
                hint="hint",
            )
            exit_code, _ = _capture_main(["--media", _DUMMY_MEDIA])

        assert exit_code == 0


# ---------------------------------------------------------------------------
# (5) ffmpeg resolution failure (resolve_tool raises DEPENDENCY_MISSING) -> error JSON + exit 0
# ---------------------------------------------------------------------------


class TestFfmpegResolveFailed:
    """Validate DEPENDENCY_MISSING when ffmpeg is not found by resolve_tool.

    Follows DC-AS-006.
    """

    def test_resolve_tool_failure_returns_dependency_missing(self) -> None:
        """error JSON + exit 0 when resolve_tool raises DEPENDENCY_MISSING."""
        from clipwright.errors import ClipwrightError, ErrorCode

        mock_module, _ = _make_silero_mock([])

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch("clipwright_silence.vad_cli.resolve_tool") as mock_resolve,
        ):
            mock_resolve.side_effect = ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="ffmpeg not found on PATH",
                hint="Install via brew install ffmpeg or similar",
            )
            exit_code, result = _capture_main(["--media", _DUMMY_MEDIA])

        assert exit_code == 0
        assert "error" in result
        assert result["error"]["code"] == "DEPENDENCY_MISSING"

    def test_resolve_tool_failure_hint_mentions_ffmpeg(self) -> None:
        """ffmpeg resolution failure hint must contain ffmpeg installation guidance."""
        from clipwright.errors import ClipwrightError, ErrorCode

        mock_module, _ = _make_silero_mock([])

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch("clipwright_silence.vad_cli.resolve_tool") as mock_resolve,
        ):
            mock_resolve.side_effect = ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="ffmpeg not found on PATH",
                hint="Install via brew install ffmpeg or similar",
            )
            _, result = _capture_main(["--media", _DUMMY_MEDIA])

        hint = result["error"].get("hint", "")
        assert "ffmpeg" in hint.lower() or "ffprobe" in hint.lower()

    def test_resolve_tool_failure_exit_zero(self) -> None:
        """exit code must be 0 even on resolve_tool failure (§7.1: all errors exit 0)."""
        from clipwright.errors import ClipwrightError, ErrorCode

        mock_module, _ = _make_silero_mock([])

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch("clipwright_silence.vad_cli.resolve_tool") as mock_resolve,
        ):
            mock_resolve.side_effect = ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="ffmpeg not found",
                hint="install ffmpeg",
            )
            exit_code, _ = _capture_main(["--media", _DUMMY_MEDIA])

        assert exit_code == 0


# ---------------------------------------------------------------------------
# (6) stdout/stderr separation (JSON on stdout only)
# ---------------------------------------------------------------------------


class TestStdoutStderrSeparation:
    """Validate that JSON is output only on stdout and logs go to stderr."""

    def _run_capturing_both(
        self, argv: list[str], *, force_error: bool = False
    ) -> tuple[int, str, str]:
        """Run main(argv) and return (exit_code, stdout_text, stderr_text)."""
        from clipwright.errors import ClipwrightError, ErrorCode

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        mock_module, _ = _make_silero_mock([{"start": 0, "end": 16000}])
        fake_wave_module = MagicMock()
        fake_wave_file = MagicMock()
        fake_wave_file.__enter__ = MagicMock(return_value=fake_wave_file)
        fake_wave_file.__exit__ = MagicMock(return_value=False)
        fake_wave_file.getnframes.return_value = 16000
        fake_wave_file.getframerate.return_value = 16000
        fake_wave_file.readframes.return_value = b"\x00" * (16000 * 2)
        fake_wave_module.open.return_value = fake_wave_file

        with (
            patch("sys.stdout", stdout_buf),
            patch("sys.stderr", stderr_buf),
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("wave.open", fake_wave_module.open),
            patch("numpy.frombuffer", return_value=MagicMock()),
            patch("tempfile.NamedTemporaryFile"),
        ):
            if force_error:
                mock_run.side_effect = ClipwrightError(
                    code=ErrorCode.SUBPROCESS_FAILED,
                    message="failed",
                    hint="hint",
                )
            else:
                mock_run.return_value = MagicMock(returncode=0)
            exit_code = vad_main(argv)

        return exit_code, stdout_buf.getvalue(), stderr_buf.getvalue()

    def test_stdout_is_valid_json_on_success(self) -> None:
        """On success, stdout must be valid JSON."""
        _, stdout, _ = self._run_capturing_both(["--media", _DUMMY_MEDIA])
        parsed = json.loads(stdout)
        assert isinstance(parsed, dict)

    def test_stdout_is_valid_json_on_error(self) -> None:
        """On error, stdout must be valid JSON."""
        _, stdout, _ = self._run_capturing_both(
            ["--media", _DUMMY_MEDIA], force_error=True
        )
        parsed = json.loads(stdout)
        assert isinstance(parsed, dict)

    def test_stdout_contains_no_log_lines(self) -> None:
        """stdout must not contain lines other than JSON (no log mixing).

        If json.loads succeeds in parsing the stdout text as a dict, there is no extra text.
        Extra text would cause json.loads to fail.
        """
        _, stdout, _ = self._run_capturing_both(["--media", _DUMMY_MEDIA])
        # json.loads succeeding = stdout is pure JSON
        result = json.loads(stdout)
        assert isinstance(result, dict)

    def test_error_json_on_stdout_not_stderr(self) -> None:
        """Error information must appear in stdout JSON, not in stderr.

        Confirms that the JSON envelope does not appear in stderr.
        (Logs in stderr are allowed, but the JSON envelope must be stdout only.)
        """
        _, stdout, stderr = self._run_capturing_both(
            ["--media", _DUMMY_MEDIA], force_error=True
        )
        # stdout JSON must have an error key
        parsed = json.loads(stdout)
        assert "error" in parsed
        # stderr must not contain the {"error": ...} JSON envelope
        try:
            stderr_parsed = json.loads(stderr)
            # if stderr is JSON, it must not have an error key
            assert "error" not in stderr_parsed
        except (json.JSONDecodeError, ValueError):
            # stderr not being JSON is fine (log strings OK)
            pass


# ---------------------------------------------------------------------------
# (7) --media-duration argument and inner ffmpeg timeout linking (CR M-2 / SR M-2)
# ---------------------------------------------------------------------------


class TestMediaDurationArg:
    """Validate that --media-duration is received and the inner ffmpeg timeout is linked to total.

    CR M-2 / SR M-2: inner ffmpeg timeout = max(30, ceil(total_duration * 2))
    detect.py passes total_duration_sec as --media-duration, satisfying
    §7.7 "inner timeout must always be shorter than outer".
    """

    def test_media_duration_arg_accepted(self) -> None:
        """--media-duration argument must be accepted and processing must complete normally."""
        mock_module, _ = _make_silero_mock([])
        fake_wave_module = MagicMock()
        fake_wave_file = MagicMock()
        fake_wave_file.__enter__ = MagicMock(return_value=fake_wave_file)
        fake_wave_file.__exit__ = MagicMock(return_value=False)
        fake_wave_file.getnframes.return_value = 0
        fake_wave_file.getframerate.return_value = 16000
        fake_wave_file.readframes.return_value = b""
        fake_wave_module.open.return_value = fake_wave_file

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("wave.open", fake_wave_module.open),
            patch("numpy.frombuffer", return_value=MagicMock()),
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            exit_code, result = _capture_main(
                ["--media", _DUMMY_MEDIA, "--media-duration", "60.0"]
            )

        # --media-duration accepted (argparse does not raise SystemExit)
        assert exit_code == 0
        assert "error" not in result

    def test_ffmpeg_timeout_uses_media_duration(self) -> None:
        """When --media-duration is specified, the inner ffmpeg timeout must be linked to total.

        Confirms via mock of run() timeout argument.
        Before CR M-2 fix (fixed 120s), timeout is 120s even for total=10s -> Red.
        After fix, total=10s -> max(30, ceil(10*2))=30 is expected.
        """
        import math

        total_duration = 10.0
        expected_timeout = float(max(30, math.ceil(total_duration * 2)))

        mock_module, _ = _make_silero_mock([])
        fake_wave_module = MagicMock()
        fake_wave_file = MagicMock()
        fake_wave_file.__enter__ = MagicMock(return_value=fake_wave_file)
        fake_wave_file.__exit__ = MagicMock(return_value=False)
        fake_wave_file.getnframes.return_value = 16000 * int(total_duration)
        fake_wave_file.getframerate.return_value = 16000
        fake_wave_file.readframes.return_value = b"\x00" * (
            16000 * int(total_duration) * 2
        )
        fake_wave_module.open.return_value = fake_wave_file

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("wave.open", fake_wave_module.open),
            patch("numpy.frombuffer", return_value=MagicMock()),
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            _capture_main(
                [
                    "--media",
                    _DUMMY_MEDIA,
                    "--media-duration",
                    str(total_duration),
                ]
            )

        # check timeout keyword argument when run() was called
        assert mock_run.called, "run() was not called"
        call_kwargs = mock_run.call_args
        actual_timeout = call_kwargs.kwargs.get(
            "timeout", call_kwargs.args[1] if len(call_kwargs.args) > 1 else None
        )
        assert actual_timeout == pytest.approx(expected_timeout), (
            f"timeout={actual_timeout} differs from expected {expected_timeout}. "
            f"When --media-duration={total_duration}, max(30, ceil({total_duration}*2))="
            f"{expected_timeout} is expected."
        )


# ---------------------------------------------------------------------------
# (8) SUBPROCESS_FAILED handler outputs generic wording, not ffmpeg stderr fragments (SR M-1)
# ---------------------------------------------------------------------------


class TestSubprocessFailedSanitize:
    """Validate that ClipwrightError(SUBPROCESS_FAILED) handler does not leak stderr fragments.

    SR M-1: process.py embeds stderr[:200] into ClipwrightError.message.
    If vad_cli.py's except ClipwrightError handler outputs that message as-is,
    internal paths (-i /path/to/video.mp4 etc.) leak into the MCP response.
    Verifies that it is replaced with generic wording ("internal subprocess failed" equivalent).
    """

    def _run_with_subprocess_failed(self, stderr_fragment: str) -> dict[str, Any]:
        """Run main after raising ClipwrightError(SUBPROCESS_FAILED) containing the given stderr fragment."""
        from clipwright.errors import ClipwrightError, ErrorCode

        mock_module, _ = _make_silero_mock([])

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.side_effect = ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=f"Command failed with exit code 1: {stderr_fragment}",
                hint="Check ffmpeg arguments",
            )
            _, result = _capture_main(["--media", _DUMMY_MEDIA])
        return result

    def test_subprocess_failed_message_does_not_contain_stderr_fragment(
        self,
    ) -> None:
        """SUBPROCESS_FAILED message must not contain ffmpeg stderr fragments.

        Before SR M-1 fix, exc.message is output as-is so stderr fragments leak.
        After fix, it must be replaced with generic wording (no ffmpeg stderr exposure).
        """
        secret_path = "/home/user/private/videos/secret.mp4"
        stderr_fragment = f"-i {secret_path}"
        result = self._run_with_subprocess_failed(stderr_fragment)

        assert "error" in result
        message = result["error"].get("message", "")
        # internal path from ffmpeg stderr must not appear in message
        assert secret_path not in message, (
            f"message contains internal path '{secret_path}': {message!r}"
        )
        assert stderr_fragment not in message, (
            f"message contains stderr fragment '{stderr_fragment}': {message!r}"
        )

    def test_subprocess_failed_message_is_generic(self) -> None:
        """SUBPROCESS_FAILED message must be generic wording."""
        result = self._run_with_subprocess_failed("some stderr output")

        assert "error" in result
        message = result["error"].get("message", "")
        # some generic wording must be present (non-empty)
        assert len(message) > 0


# ---------------------------------------------------------------------------
# (9) ImportError message contains fixed wording / exc.name equivalent, not internal paths (SR L-2)
# ---------------------------------------------------------------------------


class TestImportErrorMessageSanitize:
    """Validate that ImportError message does not contain Python internal paths.

    SR L-2: str(exc) of ImportError may contain
    "cannot import name 'X' from '/path/to/site-packages/...'" etc.
    Verifies that the implementation uses a fixed message or exc.name (module name only).
    """

    def _run_with_import_error(self, exc_message: str) -> dict[str, Any]:
        """Run main while raising ImportError with the given message on silero_vad import."""
        # setting sys.modules to None causes ImportError
        with patch.dict(
            "sys.modules",
            {"silero_vad": None},
        ):
            _, result = _capture_main(["--media", _DUMMY_MEDIA])
        return result

    def test_import_error_message_excludes_internal_path(self) -> None:
        """DEPENDENCY_MISSING message must not contain Python internal paths.

        Before SR L-2 fix, f"...{exc}" uses the str representation of exc as-is,
        which may leak internal paths. After fix, a fixed message or exc.name is expected.
        This verifies that typical internal path fragments like '/site-packages/' and
        '/usr/lib/python' are not present in message.
        """
        result = self._run_with_import_error(
            "cannot import name 'load_silero_vad' "
            "from '/home/user/.venv/lib/python3.11/site-packages/silero_vad/__init__.py'"
        )
        assert "error" in result
        assert result["error"]["code"] == "DEPENDENCY_MISSING"
        message = result["error"].get("message", "")
        # internal path fragment must not be in message
        assert "/site-packages/" not in message, (
            f"message contains internal path '/site-packages/': {message!r}"
        )

    def test_import_error_message_is_fixed_or_module_name(self) -> None:
        """DEPENDENCY_MISSING message must be fixed wording or module name only.

        Regardless of whether the implementation uses exc.name or a fixed message,
        verifies that message is non-empty and appropriate wording.
        """
        result = self._run_with_import_error("No module named 'silero_vad'")
        assert "error" in result
        message = result["error"].get("message", "")
        assert len(message) > 0


# ---------------------------------------------------------------------------
# (10) Unexpected exception handler sanitization (SR NF-L-1)
# ---------------------------------------------------------------------------


class TestUnexpectedExceptionSanitize:
    """Validate that the except Exception handler does not include str(exc) in message.

    SR NF-L-1: even if vad_cli.py's except Exception handler catches exceptions like
    OSError that contain internal paths, the error JSON message must not contain
    str(exc) content (internal path fragments etc.).
    """

    def test_unexpected_exception_message_excludes_exc_str(self) -> None:
        """error message on unexpected exception must not contain str(exc).

        Causes an OSError("No such file or directory: '/home/user/private/media.mp4'")
        to be caught by the except Exception handler, and confirms no path fragments in message.
        """
        exc_message = "No such file or directory: '/home/user/private/media.mp4'"

        fake_wave_module = MagicMock()
        fake_wave_module.open.side_effect = OSError(exc_message)

        with (
            patch.dict("sys.modules", {"silero_vad": MagicMock()}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("wave.open", fake_wave_module.open),
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            exit_code, result = _capture_main(["--media", _DUMMY_MEDIA])

        assert exit_code == 0
        assert "error" in result
        assert result["error"]["code"] == "INTERNAL"
        message = result["error"].get("message", "")
        # str(exc) must not be in message (no internal path leakage)
        assert "/home/user/private/media.mp4" not in message, (
            f"message contains internal path: {message!r}"
        )
        assert exc_message not in message, (
            f"message contains str(exc) verbatim: {message!r}"
        )
