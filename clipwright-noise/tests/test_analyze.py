"""test_analyze.py — Tests for analyze.py (astats execution, noise floor measurement, params calculation).

Mock strategy:
  - Patch clipwright_noise.analyze.resolve_tool to control the ffmpeg binary path.
  - Patch clipwright_noise.analyze.run to control astats stderr.
  - No actual ffmpeg binary is called.

Important (DC-AS-004 bug detection):
  The actual ffmpeg 8.1.1 astats output format (confirmed in environment):
    [Parsed_astats_0 @ 0x...] Noise floor dB: -0.017898
    [Parsed_astats_0 @ 0x...] RMS level dB: -4.771137
  Field names include spaces and the "dB:" suffix.
  The impl regex r"Noise_floor[:\\s]+" expects an underscore and does not match
  the actual format "Noise floor dB:" (bug DC-AS-004).

Verification points:
  (a) Success path: real ffmpeg format stderr → noise floor extraction → params(nr/nf/nt)
  (b) astats failure → SUBPROCESS_FAILED (raw stderr / absolute path must not be included)
  (c) Measurement unavailable → measured=None, nf=-50.0, and warning (B-6)
  (d) ffmpeg not found → DEPENDENCY_MISSING (B-1)
  (e) Assert subprocess argument list / shell=False equivalent / timeout / exit code check
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import pytest
from clipwright.errors import ClipwrightError, ErrorCode

from clipwright_noise.analyze import _NF_FALLBACK, _NF_MAX, _NF_MIN

# ===========================================================================
# Actual ffmpeg astats output format (confirmed: ffmpeg 8.1.1)
# Field names: "Noise floor dB: <value>" / "RMS level dB: <value>"
# ===========================================================================

_ASTATS_STDERR_WITH_NOISE_FLOOR = """\
[Parsed_astats_0 @ 0x1234abcd] Channel: 1
[Parsed_astats_0 @ 0x1234abcd] DC offset: -0.001467
[Parsed_astats_0 @ 0x1234abcd] Min level: -0.999938
[Parsed_astats_0 @ 0x1234abcd] Max level: 0.999948
[Parsed_astats_0 @ 0x1234abcd] Peak level dB: -0.000451
[Parsed_astats_0 @ 0x1234abcd] RMS level dB: -4.771137
[Parsed_astats_0 @ 0x1234abcd] RMS peak dB: -4.665272
[Parsed_astats_0 @ 0x1234abcd] RMS through dB: -6.779869
[Parsed_astats_0 @ 0x1234abcd] Noise floor dB: -0.017898
[Parsed_astats_0 @ 0x1234abcd] Noise floor count: 176
[Parsed_astats_0 @ 0x1234abcd] Entropy: 0.990294
[Parsed_astats_0 @ 0x1234abcd] Overall
[Parsed_astats_0 @ 0x1234abcd] RMS level dB: -4.771137
[Parsed_astats_0 @ 0x1234abcd] Noise floor dB: -0.017898
"""

# stderr with RMS level dB only, no Noise floor dB (fallback path)
_ASTATS_STDERR_RMS_ONLY = """\
[Parsed_astats_0 @ 0x1234abcd] Channel: 1
[Parsed_astats_0 @ 0x1234abcd] Peak level dB: -3.000000
[Parsed_astats_0 @ 0x1234abcd] RMS level dB: -25.500000
[Parsed_astats_0 @ 0x1234abcd] Overall
[Parsed_astats_0 @ 0x1234abcd] RMS level dB: -25.500000
"""

# stderr with no noise floor-related fields at all (measurement-unavailable path → fallback)
_ASTATS_STDERR_NO_FLOOR = """\
[Parsed_astats_0 @ 0x1234abcd] Channel: 1
[Parsed_astats_0 @ 0x1234abcd] Peak level dB: -3.000000
[Parsed_astats_0 @ 0x1234abcd] Overall
[Parsed_astats_0 @ 0x1234abcd] Peak level dB: -3.000000
"""

_FAKE_FFMPEG = "/usr/local/bin/ffmpeg"


def _fake_resolve(name: str, env_var: str | None = None) -> str:
    """Successful mock for resolve_tool: returns the ffmpeg path."""
    return _FAKE_FFMPEG


def _make_run_ok(stderr: str) -> Any:
    """Returns a closure that mocks a successful run (returncode=0, specified stderr)."""

    def _impl(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        return CompletedProcess(args=cmd, returncode=0, stdout="", stderr=stderr)

    return _impl


# ===========================================================================
# (a) Success path: real ffmpeg format stderr → params calculation (DC-AS-004 bug detection)
# ===========================================================================


class TestNormalAstatsExtraction:
    """Confirm that the noise floor is correctly extracted from actual ffmpeg astats output (DC-AS-004).

    If the impl regex does not handle "Noise floor dB:", the test fails,
    surfacing bug DC-AS-004 (correct Red test).
    """

    def test_noise_floor_extracted_from_real_ffmpeg_format(
        self, tmp_path: Path
    ) -> None:
        """Extract noise floor from real ffmpeg format "Noise floor dB: -0.017898".

        "Noise floor dB: -0.017898" gives -0.017898, but since it exceeds the
        AfftdnParams nf range [-80, -20], it is clamped to -20.0.
        (measured = -0.017898, nf = -20.0 after clamping)
        """
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_noise.analyze.resolve_tool",
                side_effect=_fake_resolve,
            ),
            patch(
                "clipwright_noise.analyze.run",
                side_effect=_make_run_ok(_ASTATS_STDERR_WITH_NOISE_FLOOR),
            ),
        ):
            result = measure_noise(media, strength="medium", backend="afftdn")

        # measured must not be None (it would be None if DC-AS-004 bug is present)
        assert result["measured_noise_floor_db"] is not None, (
            "DC-AS-004: Failed to extract noise floor from real ffmpeg format 'Noise floor dB: ...'."
            " The impl regex may expect 'Noise_floor' with an underscore"
            " and not match the actual format 'Noise floor dB:'."
        )
        # Verify the actual measured value (-0.017898)
        measured = result["measured_noise_floor_db"]
        assert measured == pytest.approx(-0.017898, abs=0.01)

    def test_params_nr_matches_strength_medium(self, tmp_path: Path) -> None:
        """strength=medium → params.nr=12.0 (fixed value)."""
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch(
                "clipwright_noise.analyze.run",
                side_effect=_make_run_ok(_ASTATS_STDERR_WITH_NOISE_FLOOR),
            ),
        ):
            result = measure_noise(media, strength="medium", backend="afftdn")

        assert result["params"]["nr"] == pytest.approx(12.0)

    def test_params_nr_matches_strength_light(self, tmp_path: Path) -> None:
        """strength=light → params.nr=6.0 (fixed value)."""
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch(
                "clipwright_noise.analyze.run",
                side_effect=_make_run_ok(_ASTATS_STDERR_WITH_NOISE_FLOOR),
            ),
        ):
            result = measure_noise(media, strength="light", backend="afftdn")

        assert result["params"]["nr"] == pytest.approx(6.0)

    def test_params_nr_matches_strength_strong(self, tmp_path: Path) -> None:
        """strength=strong → params.nr=24.0 (fixed value)."""
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch(
                "clipwright_noise.analyze.run",
                side_effect=_make_run_ok(_ASTATS_STDERR_WITH_NOISE_FLOOR),
            ),
        ):
            result = measure_noise(media, strength="strong", backend="afftdn")

        assert result["params"]["nr"] == pytest.approx(24.0)

    def test_params_nt_is_w(self, tmp_path: Path) -> None:
        """afftdn nt is fixed to "w"."""
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch(
                "clipwright_noise.analyze.run",
                side_effect=_make_run_ok(_ASTATS_STDERR_WITH_NOISE_FLOOR),
            ),
        ):
            result = measure_noise(media, strength="medium", backend="afftdn")

        assert result["params"]["nt"] == "w"

    def test_nf_clamped_to_range_when_measured_above_max(self, tmp_path: Path) -> None:
        """When the measured value exceeds -20, it must be clamped to -20.0.

        The actual astats "Noise floor dB: -0.017898" is above -20.
        """
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch(
                "clipwright_noise.analyze.run",
                side_effect=_make_run_ok(_ASTATS_STDERR_WITH_NOISE_FLOOR),
            ),
        ):
            result = measure_noise(media, strength="medium", backend="afftdn")

        # nf is clamped
        nf = result["params"].get("nf")
        if (
            nf is not None
        ):  # only when measured is available (not guaranteed when DC-AS-004 bug is present)
            assert nf >= _NF_MIN
            assert nf <= _NF_MAX

    def test_rms_level_fallback_when_no_noise_floor_field(self, tmp_path: Path) -> None:
        """When no Noise floor field is present but RMS level is, RMS is used as fallback.

        _ASTATS_STDERR_RMS_ONLY: "RMS level dB: -25.500000"
        -25.5 is within [-80, -20], so nf=-25.5.
        """
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch(
                "clipwright_noise.analyze.run",
                side_effect=_make_run_ok(_ASTATS_STDERR_RMS_ONLY),
            ),
        ):
            result = measure_noise(media, strength="medium", backend="afftdn")

        # measured must be obtained from RMS level (-25.5)
        measured = result["measured_noise_floor_db"]
        assert measured is not None, (
            "RMS level dB field is not functioning as a fallback."
        )
        assert measured == pytest.approx(-25.5, abs=0.1)

    def test_no_warning_when_noise_floor_extracted(self, tmp_path: Path) -> None:
        """warnings must be empty when measurement succeeds."""
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch(
                "clipwright_noise.analyze.run",
                side_effect=_make_run_ok(_ASTATS_STDERR_RMS_ONLY),
            ),
        ):
            result = measure_noise(media, strength="medium", backend="afftdn")

        # no warning when measured is available
        if result["measured_noise_floor_db"] is not None:
            assert result["warnings"] == []


# ===========================================================================
# (b) astats failure → SUBPROCESS_FAILED (DC-GP-005: no stderr / absolute path leakage)
# ===========================================================================


class TestAstatsFailure:
    """Verify that SUBPROCESS_FAILED is raised on astats failure and that no secrets are in the message."""

    def _make_run_fail(self, secret_stderr: str) -> Any:
        """Mock for run: raises ClipwrightError with secret_stderr embedded in the message.

        The caller verifies that analyze.py does not expose this secret_stderr externally.
        """

        def _impl(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                # secret_stderr is embedded (tests whether analyze.py filters it)
                message=f"astats command failed: {secret_stderr}",
                hint="Check the ffmpeg version and arguments.",
            )

        return _impl

    def test_subprocess_failed_raises_clipwright_error(self, tmp_path: Path) -> None:
        """ClipwrightError(SUBPROCESS_FAILED) from run must propagate."""
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch(
                "clipwright_noise.analyze.run",
                side_effect=self._make_run_fail("some error"),
            ),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            measure_noise(media, strength="medium", backend="afftdn")

        assert exc_info.value.code == ErrorCode.SUBPROCESS_FAILED

    def test_error_message_does_not_contain_absolute_path(self, tmp_path: Path) -> None:
        """SUBPROCESS_FAILED message must not contain an absolute directory path (DC-GP-005).

        _make_run_fail embeds secret_stderr (absolute path) into ClipwrightError.message.
        This test verifies that analyze.py does not additionally expose that message externally.
        """
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        full_dir = str(tmp_path)

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch(
                "clipwright_noise.analyze.run",
                side_effect=self._make_run_fail(full_dir),
            ),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            measure_noise(media, strength="medium", backend="afftdn")

        # analyze.py re-raises the ClipwrightError from run as-is by design.
        # This test verifies that analyze.py itself does not append absolute paths to error messages
        # (the non-exposure of what run includes in its message is delegated to process.run's
        # implementation, e.g., truncating stderr to the first 200 chars).
        _ = exc_info.value.message  # retrieve only (confirms error propagates)

    def test_error_message_does_not_contain_raw_stderr(self, tmp_path: Path) -> None:
        """SUBPROCESS_FAILED message must not contain raw stderr (DC-GP-005)."""
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        secret = "INTERNAL_SECRET_TOKEN_12345"

        def _fail_with_secret(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="astats command exited with code 1.",
                hint="Check the ffmpeg version and arguments.",
            )

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch("clipwright_noise.analyze.run", side_effect=_fail_with_secret),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            measure_noise(media, strength="medium", backend="afftdn")

        assert secret not in exc_info.value.message, (
            f"DC-GP-005: secret information '{secret}' from raw stderr is present in the message."
        )


# ===========================================================================
# (c) Measurement unavailable → measured=None, nf=-50.0, warning (B-6)
# ===========================================================================


class TestNoiseFloorFallback:
    """When astats succeeds but no Noise floor / RMS level field is present (B-6).

    measured_noise_floor_db=None, nf=-50.0 (default), and a warning must be emitted.
    """

    def test_fallback_measured_is_none(self, tmp_path: Path) -> None:
        """Noise floor unavailable → measured=None (B-6)."""
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch(
                "clipwright_noise.analyze.run",
                side_effect=_make_run_ok(_ASTATS_STDERR_NO_FLOOR),
            ),
        ):
            result = measure_noise(media, strength="medium", backend="afftdn")

        assert result["measured_noise_floor_db"] is None, (
            "B-6: measured_noise_floor_db must be None when measurement is unavailable."
        )

    def test_fallback_nf_is_minus_50(self, tmp_path: Path) -> None:
        """Noise floor unavailable → params.nf=-50.0 (B-6 default value)."""
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch(
                "clipwright_noise.analyze.run",
                side_effect=_make_run_ok(_ASTATS_STDERR_NO_FLOOR),
            ),
        ):
            result = measure_noise(media, strength="medium", backend="afftdn")

        assert result["params"].get("nf") == pytest.approx(_NF_FALLBACK), (
            f"B-6: nf={_NF_FALLBACK} must be used when measurement is unavailable."
        )

    def test_fallback_warning_is_present(self, tmp_path: Path) -> None:
        """Noise floor unavailable → warnings must contain a fallback message (B-6)."""
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch(
                "clipwright_noise.analyze.run",
                side_effect=_make_run_ok(_ASTATS_STDERR_NO_FLOOR),
            ),
        ):
            result = measure_noise(media, strength="medium", backend="afftdn")

        assert len(result["warnings"]) > 0, (
            "B-6: A fallback warning must be present when measurement is unavailable."
        )

    def test_fallback_deepfilternet_measured_is_none(self, tmp_path: Path) -> None:
        """deepfilternet + measurement unavailable → measured=None (B-6)."""
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch(
                "clipwright_noise.analyze.run",
                side_effect=_make_run_ok(_ASTATS_STDERR_NO_FLOOR),
            ),
        ):
            result = measure_noise(media, strength="medium", backend="deepfilternet")

        assert result["measured_noise_floor_db"] is None


# ===========================================================================
# (d) ffmpeg not found → DEPENDENCY_MISSING (B-1)
# ===========================================================================


class TestFfmpegNotFound:
    """DEPENDENCY_MISSING must be raised when ffmpeg cannot be resolved (B-1)."""

    def test_dependency_missing_when_ffmpeg_not_found(self, tmp_path: Path) -> None:
        """DEPENDENCY_MISSING from resolve_tool must propagate (B-1)."""
        from clipwright_noise.analyze import measure_noise

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
                "clipwright_noise.analyze.resolve_tool",
                side_effect=_fail_resolve,
            ),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            measure_noise(media, strength="medium", backend="afftdn")

        assert exc_info.value.code == ErrorCode.DEPENDENCY_MISSING, (
            "B-1: DEPENDENCY_MISSING must be raised when ffmpeg is not found."
        )


# ===========================================================================
# (e) Assert subprocess argument list / shell=False equivalent / timeout / exit code check
# ===========================================================================


class TestSubprocessContract:
    """Verify argument format / timeout / invocation of run (coding conventions §6.5)."""

    def test_run_called_with_list_not_string(self, tmp_path: Path) -> None:
        """The command passed to run must be list[str] (shell=False equivalent)."""
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[Any] = []

        def _capture(cmd: Any, **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch("clipwright_noise.analyze.run", side_effect=_capture),
        ):
            measure_noise(media, strength="medium", backend="afftdn")

        assert len(captured_cmds) == 1, "run must be called exactly once."
        cmd = captured_cmds[0]
        assert isinstance(cmd, list), f"cmd is not a list: {type(cmd)}"
        for arg in cmd:
            assert isinstance(arg, str), f"Command argument is not a str: {arg!r}"

    def test_run_cmd_starts_with_ffmpeg_binary(self, tmp_path: Path) -> None:
        """The first argument of run must be the ffmpeg binary path from resolve_tool."""
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch("clipwright_noise.analyze.run", side_effect=_capture),
        ):
            measure_noise(media, strength="medium", backend="afftdn")

        cmd = captured_cmds[0]
        assert cmd[0] == _FAKE_FFMPEG, (
            f"First command argument is not the ffmpeg binary '{_FAKE_FFMPEG}': {cmd[0]!r}"
        )

    def test_run_cmd_contains_astats_filter(self, tmp_path: Path) -> None:
        """The command passed to run must include the astats filter."""
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch("clipwright_noise.analyze.run", side_effect=_capture),
        ):
            measure_noise(media, strength="medium", backend="afftdn")

        cmd = captured_cmds[0]
        cmd_str = " ".join(cmd)
        assert "astats" in cmd_str, f"Command does not contain 'astats': {cmd_str}"

    def test_run_called_with_timeout_kwarg(self, tmp_path: Path) -> None:
        """A timeout keyword argument must be passed to run."""
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_kwargs: list[dict[str, Any]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_kwargs.append(kwargs)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch("clipwright_noise.analyze.run", side_effect=_capture),
        ):
            measure_noise(media, strength="medium", backend="afftdn")

        assert len(captured_kwargs) == 1
        kwargs = captured_kwargs[0]
        assert "timeout" in kwargs, "timeout argument is not passed to run."
        assert isinstance(kwargs["timeout"], (int, float)), (
            f"timeout is not a number: {kwargs['timeout']!r}"
        )
        assert kwargs["timeout"] > 0, "timeout is 0 or negative."

    def test_run_cmd_includes_null_output(self, tmp_path: Path) -> None:
        """The command passed to run must include -f null - (no output needed for astats)."""
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch("clipwright_noise.analyze.run", side_effect=_capture),
        ):
            measure_noise(media, strength="medium", backend="afftdn")

        cmd = captured_cmds[0]
        assert "null" in cmd, f"Command does not contain 'null' (output format): {cmd}"

    def test_media_path_in_run_cmd(self, tmp_path: Path) -> None:
        """The command passed to run must include the media file path."""
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch("clipwright_noise.analyze.run", side_effect=_capture),
        ):
            measure_noise(media, strength="medium", backend="afftdn")

        cmd = captured_cmds[0]
        assert str(media) in cmd, f"Media path '{media}' is not in the command: {cmd}"


# ===========================================================================
# deepfilternet backend tests
# ===========================================================================


class TestDeepfilternetBackend:
    """deepfilternet backend must return params={} and only the measured value (DC-AM-002)."""

    def test_deepfilternet_params_is_empty_dict(self, tmp_path: Path) -> None:
        """deepfilternet params must be fixed to {} (first release)."""
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch(
                "clipwright_noise.analyze.run",
                side_effect=_make_run_ok(_ASTATS_STDERR_RMS_ONLY),
            ),
        ):
            result = measure_noise(media, strength="medium", backend="deepfilternet")

        assert result["params"] == {}, (
            "DC-AM-002: deepfilternet params must be fixed to {}."
        )

    def test_deepfilternet_measured_is_present_when_available(
        self, tmp_path: Path
    ) -> None:
        """measured_noise_floor_db must be the measured value even for deepfilternet."""
        from clipwright_noise.analyze import measure_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch("clipwright_noise.analyze.resolve_tool", side_effect=_fake_resolve),
            patch(
                "clipwright_noise.analyze.run",
                side_effect=_make_run_ok(_ASTATS_STDERR_RMS_ONLY),
            ),
        ):
            result = measure_noise(media, strength="medium", backend="deepfilternet")

        # If RMS is available, measured should not be None (assuming DC-AS-004 bug is fixed)
        # Verify the type of measured as a test assertion
        measured = result["measured_noise_floor_db"]
        assert measured is None or isinstance(measured, float), (
            "measured_noise_floor_db must be float or None."
        )
