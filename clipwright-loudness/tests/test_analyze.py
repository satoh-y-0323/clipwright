"""test_analyze.py — Tests for analyze.py (loudnorm/volumedetect execution, loudness measurement).

Mock policy:
  - Patch clipwright_loudness.analyze.resolve_tool to control the ffmpeg binary path.
  - Patch clipwright_loudness.analyze.run to control ffmpeg stderr.
  - No real ffmpeg binary is invoked.

Important (DC-AS-004 lesson: avoid repeating the regex field-name mismatch bug from noise):
  Actual ffmpeg 8.1.1 loudnorm print_format=json output format (verified on real hardware):
    [Parsed_loudnorm_0 @ 0x...] <- empty line
    {
    \t"input_i" : "-21.75",
    \t"input_tp" : "-18.06",
    \t"input_lra" : "0.00",
    \t"input_thresh" : "-31.75",
    \t"output_i" : "-14.03",
    \t"output_tp" : "-10.27",
    \t"output_lra" : "0.00",
    \t"output_thresh" : "-24.03",
    \t"normalization_type" : "dynamic",
    \t"target_offset" : "0.03"
    }
    Note: values are output as quoted strings. "-inf" may appear (silent input).

  Actual ffmpeg 8.1.1 volumedetect output format (verified on real hardware):
    [Parsed_volumedetect_0 @ 0x...] n_samples: 132300
    [Parsed_volumedetect_0 @ 0x...] mean_volume: -21.1 dB
    [Parsed_volumedetect_0 @ 0x...] max_volume: -18.1 dB
    [Parsed_volumedetect_0 @ 0x...] histogram_18db: 38400
    Note: "max_volume: <VALUE> dB" format. VALUE is a negative float.

Verification points:
  (a) loudnorm success: extract input_i/input_tp/input_lra/input_thresh/target_offset from stderr JSON
  (b) peak success: extract max_volume from volumedetect
  (c) measurement failure (missing JSON/fields) -> measured=None + warning (U-1 confirmed)
  (d) ffmpeg missing -> DEPENDENCY_MISSING
  (e) execution failure -> SUBPROCESS_FAILED (no raw stderr or absolute path in message)
  (f) timeout -> SUBPROCESS_TIMEOUT
  (g) assert subprocess argument list, shell=False, timeout, exit-code check
  (h) verify track-wide measurement command construction
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import pytest
from clipwright.errors import ClipwrightError, ErrorCode

# ===========================================================================
# Actual ffmpeg output formats (verified: ffmpeg 8.1.1 Windows)
# ===========================================================================

# Normal loudnorm print_format=json output (format verified on real hardware)
_LOUDNORM_STDERR_NORMAL = """\
ffmpeg version 8.1.1 ...
Input #0, ...
[Parsed_loudnorm_0 @ 0000019762f437c0]
{
\t"input_i" : "-21.75",
\t"input_tp" : "-18.06",
\t"input_lra" : "0.00",
\t"input_thresh" : "-31.75",
\t"output_i" : "-14.03",
\t"output_tp" : "-10.27",
\t"output_lra" : "0.00",
\t"output_thresh" : "-24.03",
\t"normalization_type" : "dynamic",
\t"target_offset" : "0.03"
}
size=N/A time=00:00:05.00 ...
"""

# loudnorm: case where -inf appears (e.g. silent input — measurement not possible)
_LOUDNORM_STDERR_INF_VALUES = """\
[Parsed_loudnorm_0 @ 0000019762f437c0]
{
\t"input_i" : "-inf",
\t"input_tp" : "-inf",
\t"input_lra" : "0.00",
\t"input_thresh" : "-70.00",
\t"output_i" : "-inf",
\t"output_tp" : "-inf",
\t"output_lra" : "0.00",
\t"output_thresh" : "-70.00",
\t"normalization_type" : "dynamic",
\t"target_offset" : "inf"
}
"""

# loudnorm: stderr with no JSON block at all (measurement not possible)
_LOUDNORM_STDERR_NO_JSON = """\
ffmpeg version 8.1.1 ...
Input #0, ...
Stream #0:0: Audio: aac, 44100 Hz, stereo, fltp, 192 kb/s
size=N/A time=00:00:05.00 ...
"""

# Normal volumedetect output (format verified on real hardware)
_VOLUMEDETECT_STDERR_NORMAL = """\
ffmpeg version 8.1.1 ...
[Parsed_volumedetect_0 @ 000001fbb2026580] n_samples: 0
[Parsed_volumedetect_0 @ 000001fbb2024c00] n_samples: 132300
[Parsed_volumedetect_0 @ 000001fbb2024c00] mean_volume: -21.1 dB
[Parsed_volumedetect_0 @ 000001fbb2024c00] max_volume: -18.1 dB
[Parsed_volumedetect_0 @ 000001fbb2024c00] histogram_18db: 38400
size=N/A time=00:00:03.00 ...
"""

# volumedetect: stderr without a max_volume field (measurement not possible)
_VOLUMEDETECT_STDERR_NO_MAX_VOLUME = """\
ffmpeg version 8.1.1 ...
[Parsed_volumedetect_0 @ 000001fbb2024c00] n_samples: 0
size=N/A time=00:00:00.00 ...
"""

_FAKE_FFMPEG = "/usr/local/bin/ffmpeg"


def _fake_resolve(name: str, env_var: str | None = None) -> str:
    """Success mock for resolve_tool: returns ffmpeg path."""
    return _FAKE_FFMPEG


def _make_run_ok(stderr: str) -> Any:
    """Return a closure that mocks a successful run call (returncode=0, given stderr)."""

    def _impl(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        return CompletedProcess(args=cmd, returncode=0, stdout="", stderr=stderr)

    return _impl


# ===========================================================================
# (a) loudnorm success: extract measured values from stderr JSON (DC-AS-004 lesson)
# ===========================================================================


class TestLoudnormNormal:
    """Verify that measured values are correctly extracted from real ffmpeg loudnorm JSON.

    If the impl's JSON parsing or field names do not match the real format, this fails
    and catches a DC-AS-004 equivalent bug in Red.
    """

    def test_loudnorm_measured_not_none(self, tmp_path: Path) -> None:
        """measured must not be None when extracted from a normal loudnorm stderr (DC-AS-004 lesson)."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert result["measured"] is not None, (
            "DC-AS-004 lesson: failed to extract measured from real ffmpeg loudnorm JSON."
            " Check impl JSON parsing / field names."
        )

    def test_loudnorm_input_i_extracted(self, tmp_path: Path) -> None:
        """input_i must be extracted as -21.75."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        measured = result["measured"]
        assert measured is not None
        assert measured["input_i"] == pytest.approx(-21.75, abs=0.01)

    def test_loudnorm_input_tp_extracted(self, tmp_path: Path) -> None:
        """input_tp must be extracted as -18.06."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        measured = result["measured"]
        assert measured is not None
        assert measured["input_tp"] == pytest.approx(-18.06, abs=0.01)

    def test_loudnorm_input_lra_extracted(self, tmp_path: Path) -> None:
        """input_lra must be extracted as 0.0."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        measured = result["measured"]
        assert measured is not None
        assert measured["input_lra"] == pytest.approx(0.0, abs=0.01)

    def test_loudnorm_input_thresh_extracted(self, tmp_path: Path) -> None:
        """input_thresh must be extracted as -31.75."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        measured = result["measured"]
        assert measured is not None
        assert measured["input_thresh"] == pytest.approx(-31.75, abs=0.01)

    def test_loudnorm_target_offset_extracted(self, tmp_path: Path) -> None:
        """target_offset must be extracted as 0.03."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        measured = result["measured"]
        assert measured is not None
        assert measured["target_offset"] == pytest.approx(0.03, abs=0.01)

    def test_loudnorm_no_warning_on_success(self, tmp_path: Path) -> None:
        """warnings must be empty on successful measurement."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert result["warnings"] == []


# ===========================================================================
# (b) peak success: extract max_volume from volumedetect
# ===========================================================================


class TestPeakNormal:
    """Verify that max_volume is correctly extracted from real ffmpeg volumedetect stderr."""

    def test_peak_measured_not_none(self, tmp_path: Path) -> None:
        """measured must not be None when extracted from a normal volumedetect stderr."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_VOLUMEDETECT_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(media, mode="peak", target_peak_db=-1.0)

        assert result["measured"] is not None, (
            "Failed to extract measured from normal volumedetect stderr."
            " Check the regex for 'max_volume: <VALUE> dB' format."
        )

    def test_peak_max_volume_db_extracted(self, tmp_path: Path) -> None:
        """max_volume_db must be extracted as -18.1."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_VOLUMEDETECT_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(media, mode="peak", target_peak_db=-1.0)

        measured = result["measured"]
        assert measured is not None
        assert measured["max_volume_db"] == pytest.approx(-18.1, abs=0.1)

    def test_peak_no_warning_on_success(self, tmp_path: Path) -> None:
        """warnings must be empty on successful measurement."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_VOLUMEDETECT_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(media, mode="peak", target_peak_db=-1.0)

        assert result["warnings"] == []


# ===========================================================================
# (c) measurement failure (missing JSON / -inf values) -> measured=None + warning (U-1 confirmed)
# ===========================================================================


class TestLoudnormMeasurementFailure:
    """When loudnorm measurement is not possible, measured=None + warning must be returned (U-1)."""

    def test_loudnorm_no_json_gives_measured_none(self, tmp_path: Path) -> None:
        """No JSON block in stderr -> measured=None (U-1)."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_NO_JSON),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert result["measured"] is None, (
            "U-1: measured must be None when loudnorm JSON is absent."
        )

    def test_loudnorm_no_json_gives_warning(self, tmp_path: Path) -> None:
        """No JSON block in stderr -> warnings must contain a warning (U-1)."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_NO_JSON),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert len(result["warnings"]) > 0, (
            "U-1: a warning is required when measurement is not possible."
        )

    def test_loudnorm_inf_values_gives_measured_none(self, tmp_path: Path) -> None:
        """JSON with -inf values -> measured=None (silent input — not measurable, U-1).

        When loudnorm returns "-inf" (silent input), LoudnormMeasured raises
        ValidationError due to allow_inf_nan=False, so measured must be None.
        """
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_INF_VALUES),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert result["measured"] is None, (
            "U-1: measured must be None when loudnorm returns -inf values."
        )


class TestPeakMeasurementFailure:
    """When volumedetect measurement is not possible, measured=None + warning must be returned (U-1)."""

    def test_peak_no_max_volume_gives_measured_none(self, tmp_path: Path) -> None:
        """No max_volume field in stderr -> measured=None."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_VOLUMEDETECT_STDERR_NO_MAX_VOLUME),
            ),
        ):
            result = measure_loudness(media, mode="peak", target_peak_db=-1.0)

        assert result["measured"] is None

    def test_peak_no_max_volume_gives_warning(self, tmp_path: Path) -> None:
        """No max_volume field in stderr -> warnings must contain a warning."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_VOLUMEDETECT_STDERR_NO_MAX_VOLUME),
            ),
        ):
            result = measure_loudness(media, mode="peak", target_peak_db=-1.0)

        assert len(result["warnings"]) > 0


# ===========================================================================
# (d) ffmpeg missing -> DEPENDENCY_MISSING
# ===========================================================================


class TestFfmpegNotFound:
    """DEPENDENCY_MISSING is raised when ffmpeg cannot be resolved."""

    def test_dependency_missing_when_ffmpeg_not_found_loudnorm(
        self, tmp_path: Path
    ) -> None:
        """DEPENDENCY_MISSING must propagate when resolve_tool raises it in loudnorm mode."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        def _fail_resolve(name: str, env_var: str | None = None) -> str:
            raise ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message=f"{name} not found on PATH.",
                hint=f"Install {name} and add it to PATH.",
            )

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fail_resolve
            ),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert exc_info.value.code == ErrorCode.DEPENDENCY_MISSING

    def test_dependency_missing_when_ffmpeg_not_found_peak(
        self, tmp_path: Path
    ) -> None:
        """DEPENDENCY_MISSING must also propagate in peak mode."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        def _fail_resolve(name: str, env_var: str | None = None) -> str:
            raise ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message=f"{name} not found.",
                hint="Install it.",
            )

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fail_resolve
            ),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            measure_loudness(media, mode="peak", target_peak_db=-1.0)

        assert exc_info.value.code == ErrorCode.DEPENDENCY_MISSING


# ===========================================================================
# (e) execution failure -> SUBPROCESS_FAILED (no raw stderr or absolute path in message)
# ===========================================================================


class TestSubprocessFailed:
    """SUBPROCESS_FAILED is raised on ffmpeg failure, with no secrets in the message."""

    def test_subprocess_failed_loudnorm(self, tmp_path: Path) -> None:
        """SUBPROCESS_FAILED must propagate when run raises it in loudnorm mode."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        def _fail_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="ffmpeg command exited with code 1.",
                hint="Check the ffmpeg version and arguments.",
            )

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_fail_run),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert exc_info.value.code == ErrorCode.SUBPROCESS_FAILED

    def test_subprocess_failed_message_no_absolute_path(self, tmp_path: Path) -> None:
        """SUBPROCESS_FAILED message must not contain an absolute directory path."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        def _fail_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            # Raise with the absolute media path in the message — if impl re-raises
            # as-is, the absolute path leaks. measure_loudness must not expose it.
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=f"ffmpeg failed for {media}",
                hint="Check.",
            )

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_fail_run),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert str(tmp_path) not in exc_info.value.message

    def test_subprocess_failed_peak(self, tmp_path: Path) -> None:
        """SUBPROCESS_FAILED must also propagate in peak mode."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        def _fail_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="ffmpeg command failed.",
                hint="Check.",
            )

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_fail_run),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            measure_loudness(media, mode="peak", target_peak_db=-1.0)

        assert exc_info.value.code == ErrorCode.SUBPROCESS_FAILED


# ===========================================================================
# (f) timeout -> SUBPROCESS_TIMEOUT
# ===========================================================================


class TestSubprocessTimeout:
    """SUBPROCESS_TIMEOUT is raised when the ffmpeg execution times out."""

    def test_subprocess_timeout_loudnorm(self, tmp_path: Path) -> None:
        """SUBPROCESS_TIMEOUT must propagate when run raises it in loudnorm mode."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        def _timeout_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_TIMEOUT,
                message="ffmpeg command timed out.",
                hint="Increase the timeout or try with shorter media.",
            )

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_timeout_run),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert exc_info.value.code == ErrorCode.SUBPROCESS_TIMEOUT

    def test_subprocess_timeout_peak(self, tmp_path: Path) -> None:
        """SUBPROCESS_TIMEOUT must also propagate in peak mode."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        def _timeout_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_TIMEOUT,
                message="Timeout.",
                hint="hint",
            )

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_timeout_run),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            measure_loudness(media, mode="peak", target_peak_db=-1.0)

        assert exc_info.value.code == ErrorCode.SUBPROCESS_TIMEOUT


# ===========================================================================
# (g) assert subprocess argument list, shell=False, timeout, exit-code check
# ===========================================================================


class TestSubprocessContract:
    """Verify argument format, timeout, and call details of run (coding conventions §6.5)."""

    def test_run_called_with_list_not_string_loudnorm(self, tmp_path: Path) -> None:
        """loudnorm: command passed to run must be list[str] (shell=False equivalent)."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[Any] = []

        def _capture(cmd: Any, **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert len(captured_cmds) >= 1
        cmd = captured_cmds[0]
        assert isinstance(cmd, list), f"cmd is not a list: {type(cmd)}"
        for arg in cmd:
            assert isinstance(arg, str), f"command argument is not str: {arg!r}"

    def test_run_called_with_list_not_string_peak(self, tmp_path: Path) -> None:
        """peak: command passed to run must be list[str] (shell=False equivalent)."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[Any] = []

        def _capture(cmd: Any, **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(media, mode="peak", target_peak_db=-1.0)

        cmd = captured_cmds[0]
        assert isinstance(cmd, list)

    def test_run_cmd_starts_with_ffmpeg_binary_loudnorm(self, tmp_path: Path) -> None:
        """loudnorm: first argument to run must be the ffmpeg binary path from resolve_tool."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        cmd = captured_cmds[0]
        assert cmd[0] == _FAKE_FFMPEG

    def test_run_called_with_timeout_loudnorm(self, tmp_path: Path) -> None:
        """loudnorm: run must receive the timeout keyword argument."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_kwargs: list[dict[str, Any]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_kwargs.append(kwargs)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert len(captured_kwargs) >= 1
        kwargs = captured_kwargs[0]
        assert "timeout" in kwargs, "timeout argument was not passed to run."
        assert isinstance(kwargs["timeout"], (int, float))
        assert kwargs["timeout"] > 0

    def test_run_called_with_timeout_peak(self, tmp_path: Path) -> None:
        """peak: run must receive the timeout keyword argument."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_kwargs: list[dict[str, Any]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_kwargs.append(kwargs)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(media, mode="peak", target_peak_db=-1.0)

        kwargs = captured_kwargs[0]
        assert "timeout" in kwargs
        assert kwargs["timeout"] > 0

    def test_run_cmd_includes_null_output_loudnorm(self, tmp_path: Path) -> None:
        """loudnorm: run command must include -f null - (no output needed)."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        cmd = captured_cmds[0]
        assert "null" in cmd, f"'null' not found in command: {cmd}"

    def test_run_cmd_includes_null_output_peak(self, tmp_path: Path) -> None:
        """peak: run command must include -f null - (no output needed)."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(media, mode="peak", target_peak_db=-1.0)

        cmd = captured_cmds[0]
        assert "null" in cmd


# ===========================================================================
# (h) track-wide measurement command construction
# ===========================================================================


class TestTrackMeasurementCommand:
    """Verify command construction for track-wide measurement (entire first audio stream) (ADR-L7)."""

    def test_loudnorm_command_contains_loudnorm_filter(self, tmp_path: Path) -> None:
        """loudnorm command must contain the 'loudnorm' filter (ADR-L1)."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        cmd = captured_cmds[0]
        cmd_str = " ".join(cmd)
        assert "loudnorm" in cmd_str, (
            f"'loudnorm' filter not found in command: {cmd_str}"
        )

    def test_loudnorm_command_contains_print_format_json(self, tmp_path: Path) -> None:
        """loudnorm command must contain 'print_format=json' (ADR-L1)."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        cmd = captured_cmds[0]
        cmd_str = " ".join(cmd)
        assert "print_format=json" in cmd_str, (
            f"'print_format=json' not found in command: {cmd_str}"
        )

    def test_loudnorm_command_contains_target_i(self, tmp_path: Path) -> None:
        """loudnorm command must contain the target I value (I=-14)."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        cmd = captured_cmds[0]
        cmd_str = " ".join(cmd)
        assert "I=-14" in cmd_str, (
            f"I=-14 (target LUFS) not found in command: {cmd_str}"
        )

    def test_loudnorm_command_contains_media_path(self, tmp_path: Path) -> None:
        """loudnorm command must contain the media path."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        cmd = captured_cmds[0]
        assert str(media) in cmd, f"media path not found in command: {cmd}"

    def test_peak_command_contains_volumedetect_filter(self, tmp_path: Path) -> None:
        """peak command must contain the 'volumedetect' filter (ADR-L2)."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(media, mode="peak", target_peak_db=-1.0)

        cmd = captured_cmds[0]
        cmd_str = " ".join(cmd)
        assert "volumedetect" in cmd_str, (
            f"'volumedetect' filter not found in command: {cmd_str}"
        )


# ===========================================================================
# H-1 regression: must find the loudnorm JSON even when leading {} blocks are present
# ===========================================================================


# stderr with a leading {} block before the loudnorm JSON (the case that caused H-1)
_LOUDNORM_STDERR_WITH_LEADING_BRACE = """\
ffmpeg version 8.1.1 ...
Input #0, {} format ...
{}
[Parsed_loudnorm_0 @ 0000019762f437c0]
{
\t"input_i" : "-21.75",
\t"input_tp" : "-18.06",
\t"input_lra" : "0.00",
\t"input_thresh" : "-31.75",
\t"output_i" : "-14.03",
\t"output_tp" : "-10.27",
\t"output_lra" : "0.00",
\t"output_thresh" : "-24.03",
\t"normalization_type" : "dynamic",
\t"target_offset" : "0.03"
}
size=N/A time=00:00:05.00 ...
"""


class TestLoudnormLeadingBraceRegression:
    """H-1 regression: trailing loudnorm JSON must be found even when leading {} blocks exist.

    Regression test for the change from re.search (first match) to
    re.findall (all candidates, search in reverse).
    """

    def test_loudnorm_with_leading_brace_measured_not_none(
        self, tmp_path: Path
    ) -> None:
        """measured must not be None when leading {} blocks are present in stderr (H-1)."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_WITH_LEADING_BRACE),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert result["measured"] is not None, (
            "H-1 regression: leading {} block caused trailing loudnorm JSON to be missed."
            " Check re.findall + reversed tail search."
        )

    def test_loudnorm_with_leading_brace_input_i_extracted(
        self, tmp_path: Path
    ) -> None:
        """input_i must be extracted as -21.75 even with leading {} blocks (H-1)."""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_WITH_LEADING_BRACE),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        measured = result["measured"]
        assert measured is not None
        assert measured["input_i"] == pytest.approx(-21.75, abs=0.01)
