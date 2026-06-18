"""test_analyze.py — Tests for clipwright_color.analyze (measure_brightness + _parse_signalstats).

Mock policy:
  - Patch clipwright_color.analyze.resolve_tool to control the ffmpeg binary path.
  - Patch clipwright_color.analyze.run to control ffmpeg stderr output.
  - No real ffmpeg binary is invoked.

Important (signalstats output format, verified with ffmpeg 8.1.1 on Windows):
  metadata=print emits per sampled frame on stderr:
    [Parsed_metadata_2 @ 000002139b615380] lavfi.signalstats.YMIN=9
    [Parsed_metadata_2 @ 000002139b615380] lavfi.signalstats.YAVG=125.951
    [Parsed_metadata_2 @ 000002139b615380] lavfi.signalstats.YMAX=242
  YAVG is a float, YMIN/YMAX are integers (but the regex handles both).
  The test mock stderr fixtures use this [Parsed_metadata_2 @ ...] prefix format.

Verification points:
  (a) multi-frame stderr -> correct YAVG mean + sampled_frames count
  (b) YMIN/YMAX aggregation (min/max across frames)
  (c) no YAVG lines -> measured=None + warning (U-1)
  (d) run raises DEPENDENCY_MISSING -> re-raised with fixed message (no abs path)
  (e) SUBPROCESS_FAILED
  (f) SUBPROCESS_TIMEOUT
  (g) argv array, shell-free, timeout passed, -vf string equals expected value
  (h) inf/nan token in YAVG -> measured=None

Requirements: FR-2 (processing), NFR-2 (subprocess discipline), architecture-report §4.
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import pytest
from clipwright.errors import ClipwrightError, ErrorCode

# ===========================================================================
# Actual ffmpeg signalstats output format (verified: ffmpeg 8.1.1 Windows)
# ===========================================================================

# Normal 3-frame signalstats stderr output — [Parsed_metadata_2 @ addr] prefix format
_SIGNALSTATS_STDERR_3FRAMES = """\
[Parsed_metadata_2 @ 000002139b615380] lavfi.signalstats.YMIN=9
[Parsed_metadata_2 @ 000002139b615380] lavfi.signalstats.YAVG=80.000
[Parsed_metadata_2 @ 000002139b615380] lavfi.signalstats.YMAX=200
[Parsed_metadata_2 @ 000002139b615381] lavfi.signalstats.YMIN=20
[Parsed_metadata_2 @ 000002139b615381] lavfi.signalstats.YAVG=100.000
[Parsed_metadata_2 @ 000002139b615381] lavfi.signalstats.YMAX=230
[Parsed_metadata_2 @ 000002139b615382] lavfi.signalstats.YMIN=30
[Parsed_metadata_2 @ 000002139b615382] lavfi.signalstats.YAVG=120.000
[Parsed_metadata_2 @ 000002139b615382] lavfi.signalstats.YMAX=240
"""
# Expected YAVG mean: (80 + 100 + 120) / 3 = 100.0
# Expected YMIN min: 9, YMAX max: 240, sampled_frames: 3

# Single-frame stderr
_SIGNALSTATS_STDERR_1FRAME = """\
[Parsed_metadata_2 @ 000002139b615380] lavfi.signalstats.YMIN=9
[Parsed_metadata_2 @ 000002139b615380] lavfi.signalstats.YAVG=125.951
[Parsed_metadata_2 @ 000002139b615380] lavfi.signalstats.YMAX=242
"""

# No YAVG lines — measurement not possible
_SIGNALSTATS_STDERR_NO_YAVG = """\
ffmpeg version 8.1.1 ...
[Parsed_metadata_2 @ 000002139b615380] lavfi.signalstats.YMIN=9
[Parsed_metadata_2 @ 000002139b615380] lavfi.signalstats.YMAX=242
size=N/A time=00:00:05.00 ...
"""

# YAVG line containing "inf" — should degrade to None
_SIGNALSTATS_STDERR_INF_YAVG = """\
[Parsed_metadata_2 @ 000002139b615380] lavfi.signalstats.YMIN=9
[Parsed_metadata_2 @ 000002139b615380] lavfi.signalstats.YAVG=inf
[Parsed_metadata_2 @ 000002139b615380] lavfi.signalstats.YMAX=242
"""

_FAKE_FFMPEG = "/usr/local/bin/ffmpeg"


def _fake_resolve(name: str, env_var: str | None = None) -> str:
    """Success mock for resolve_tool."""
    return _FAKE_FFMPEG


def _make_run_ok(stderr: str) -> Any:
    """Return a closure that mocks a successful run call (returncode=0, given stderr)."""

    def _impl(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        return CompletedProcess(args=cmd, returncode=0, stdout="", stderr=stderr)

    return _impl


# ===========================================================================
# (a) multi-frame stderr -> correct YAVG mean + sampled_frames count
# ===========================================================================


class TestSignalstatsMultiFrame:
    """Verify YAVG averaging and sampled_frames count for multi-frame output."""

    def test_measured_not_none(self, tmp_path: Path) -> None:
        """measured must not be None when YAVG lines are present."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()

        with (
            pytest.MonkeyPatch().context() as mp,
        ):
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr(
                "clipwright_color.analyze.run",
                _make_run_ok(_SIGNALSTATS_STDERR_3FRAMES),
            )
            result = measure_brightness(media, opts)

        assert result["measured"] is not None

    def test_yavg_mean_of_3_frames(self, tmp_path: Path) -> None:
        """yavg must be the mean of 3 YAVG values (80+100+120)/3=100.0."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr(
                "clipwright_color.analyze.run",
                _make_run_ok(_SIGNALSTATS_STDERR_3FRAMES),
            )
            result = measure_brightness(media, opts)

        measured = result["measured"]
        assert measured is not None
        assert measured["yavg"] == pytest.approx(100.0, abs=0.01)

    def test_sampled_frames_count(self, tmp_path: Path) -> None:
        """sampled_frames must be 3 (one per YAVG line)."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr(
                "clipwright_color.analyze.run",
                _make_run_ok(_SIGNALSTATS_STDERR_3FRAMES),
            )
            result = measure_brightness(media, opts)

        measured = result["measured"]
        assert measured is not None
        assert measured["sampled_frames"] == 3

    def test_no_warning_on_success(self, tmp_path: Path) -> None:
        """warnings must be empty on successful measurement."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr(
                "clipwright_color.analyze.run",
                _make_run_ok(_SIGNALSTATS_STDERR_3FRAMES),
            )
            result = measure_brightness(media, opts)

        assert result["warnings"] == []


# ===========================================================================
# (b) YMIN/YMAX aggregation (min/max)
# ===========================================================================


class TestSignalstatsMinMaxAggregation:
    """YMIN must be the min across frames; YMAX must be the max across frames."""

    def test_ymin_is_minimum_across_frames(self, tmp_path: Path) -> None:
        """ymin must be min(9, 20, 30) = 9."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr(
                "clipwright_color.analyze.run",
                _make_run_ok(_SIGNALSTATS_STDERR_3FRAMES),
            )
            result = measure_brightness(media, opts)

        measured = result["measured"]
        assert measured is not None
        assert measured["ymin"] == pytest.approx(9.0, abs=0.01)

    def test_ymax_is_maximum_across_frames(self, tmp_path: Path) -> None:
        """ymax must be max(200, 230, 240) = 240."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr(
                "clipwright_color.analyze.run",
                _make_run_ok(_SIGNALSTATS_STDERR_3FRAMES),
            )
            result = measure_brightness(media, opts)

        measured = result["measured"]
        assert measured is not None
        assert measured["ymax"] == pytest.approx(240.0, abs=0.01)


# ===========================================================================
# (c) no YAVG lines -> measured=None + warning (U-1)
# ===========================================================================


class TestNoYavgLines:
    """When no YAVG lines are present, measured must be None with a warning (U-1)."""

    def test_no_yavg_gives_measured_none(self, tmp_path: Path) -> None:
        """measured must be None when stderr has no YAVG lines (U-1)."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr(
                "clipwright_color.analyze.run",
                _make_run_ok(_SIGNALSTATS_STDERR_NO_YAVG),
            )
            result = measure_brightness(media, opts)

        assert result["measured"] is None, (
            "U-1: measured must be None when YAVG lines are absent."
        )

    def test_no_yavg_gives_warning(self, tmp_path: Path) -> None:
        """A warning must be emitted when no YAVG lines are present (U-1)."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr(
                "clipwright_color.analyze.run",
                _make_run_ok(_SIGNALSTATS_STDERR_NO_YAVG),
            )
            result = measure_brightness(media, opts)

        assert len(result["warnings"]) > 0, (
            "U-1: a warning is required when YAVG measurement is not possible."
        )


# ===========================================================================
# (d) DEPENDENCY_MISSING -> re-raised with fixed message (no abs path)
# ===========================================================================


class TestDependencyMissing:
    """DEPENDENCY_MISSING is propagated (with fixed message, no abs path)."""

    def test_dependency_missing_propagates(self, tmp_path: Path) -> None:
        """DEPENDENCY_MISSING must be re-raised when resolve_tool fails."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()

        def _fail_resolve(name: str, env_var: str | None = None) -> str:
            raise ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message=f"{name} not found on PATH.",
                hint=f"Install {name} and add it to PATH.",
            )

        with (
            pytest.MonkeyPatch().context() as mp,
            pytest.raises(ClipwrightError) as exc_info,
        ):
            mp.setattr("clipwright_color.analyze.resolve_tool", _fail_resolve)
            measure_brightness(media, opts)

        assert exc_info.value.code == ErrorCode.DEPENDENCY_MISSING

    def test_dependency_missing_message_no_absolute_path(self, tmp_path: Path) -> None:
        """Error message must not expose absolute directory path (CWE-209)."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()

        def _fail_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message=f"ffmpeg failed for {media}",
                hint="Check.",
            )

        with (
            pytest.MonkeyPatch().context() as mp,
            pytest.raises(ClipwrightError) as exc_info,
        ):
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_color.analyze.run", _fail_run)
            measure_brightness(media, opts)

        assert str(tmp_path) not in exc_info.value.message, (
            "CWE-209: absolute path must not appear in the re-raised error message."
        )


# ===========================================================================
# (e) SUBPROCESS_FAILED
# ===========================================================================


class TestSubprocessFailed:
    """SUBPROCESS_FAILED is re-raised with fixed message (no raw stderr)."""

    def test_subprocess_failed_propagates(self, tmp_path: Path) -> None:
        """SUBPROCESS_FAILED must be re-raised when ffmpeg exits with non-zero code."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()

        def _fail_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="ffmpeg command exited with code 1.",
                hint="Check the ffmpeg version and arguments.",
            )

        with (
            pytest.MonkeyPatch().context() as mp,
            pytest.raises(ClipwrightError) as exc_info,
        ):
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_color.analyze.run", _fail_run)
            measure_brightness(media, opts)

        assert exc_info.value.code == ErrorCode.SUBPROCESS_FAILED

    def test_subprocess_failed_message_no_absolute_path(self, tmp_path: Path) -> None:
        """SUBPROCESS_FAILED message must not contain the absolute tmp path (CWE-209)."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()

        def _fail_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=f"ffmpeg failed for {media}",
                hint="Check.",
            )

        with (
            pytest.MonkeyPatch().context() as mp,
            pytest.raises(ClipwrightError) as exc_info,
        ):
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_color.analyze.run", _fail_run)
            measure_brightness(media, opts)

        assert str(tmp_path) not in exc_info.value.message


# ===========================================================================
# (f) SUBPROCESS_TIMEOUT
# ===========================================================================


class TestSubprocessTimeout:
    """SUBPROCESS_TIMEOUT is re-raised when ffmpeg execution times out."""

    def test_subprocess_timeout_propagates(self, tmp_path: Path) -> None:
        """SUBPROCESS_TIMEOUT must be re-raised when run times out."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()

        def _timeout_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_TIMEOUT,
                message="ffmpeg command timed out.",
                hint="Increase the timeout or try with shorter media.",
            )

        with (
            pytest.MonkeyPatch().context() as mp,
            pytest.raises(ClipwrightError) as exc_info,
        ):
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_color.analyze.run", _timeout_run)
            measure_brightness(media, opts)

        assert exc_info.value.code == ErrorCode.SUBPROCESS_TIMEOUT


# ===========================================================================
# (g) argv array, shell-free, timeout, -vf string validation
# ===========================================================================


class TestSubprocessContract:
    """Verify subprocess calling discipline: argv array, timeout, -vf string (NFR-2/CWE-78)."""

    def test_cmd_is_list_of_strings(self, tmp_path: Path) -> None:
        """Command passed to run must be list[str] (shell=False equivalent)."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()
        captured_cmds: list[Any] = []

        def _capture(cmd: Any, **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_color.analyze.run", _capture)
            measure_brightness(media, opts)

        assert len(captured_cmds) >= 1
        cmd = captured_cmds[0]
        assert isinstance(cmd, list), f"cmd must be list, got {type(cmd)}"
        for arg in cmd:
            assert isinstance(arg, str), f"each cmd arg must be str, got {arg!r}"

    def test_cmd_starts_with_ffmpeg_binary(self, tmp_path: Path) -> None:
        """First argument must be the ffmpeg binary from resolve_tool."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_color.analyze.run", _capture)
            measure_brightness(media, opts)

        assert captured_cmds[0][0] == _FAKE_FFMPEG

    def test_run_receives_timeout(self, tmp_path: Path) -> None:
        """run must receive a positive timeout keyword argument."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()
        captured_kwargs: list[dict[str, Any]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_kwargs.append(kwargs)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_color.analyze.run", _capture)
            measure_brightness(media, opts)

        assert len(captured_kwargs) >= 1
        kw = captured_kwargs[0]
        assert "timeout" in kw, "timeout must be passed to run"
        assert isinstance(kw["timeout"], (int, float))
        assert kw["timeout"] > 0

    def test_vf_is_single_argv_element(self, tmp_path: Path) -> None:
        """-vf value must be a single argv element (not split, CWE-78)."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions(sample_interval_sec=1.0)
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_color.analyze.run", _capture)
            measure_brightness(media, opts)

        cmd = captured_cmds[0]
        assert "-vf" in cmd, "-vf must be in the command"
        vf_index = cmd.index("-vf")
        vf_value = cmd[vf_index + 1]
        # The entire filter chain must be a single argument (not split on commas/spaces)
        expected_vf = "fps=1/1,signalstats=stat=brng,metadata=print"
        assert vf_value == expected_vf, (
            f"-vf value must be single element '{expected_vf}', got '{vf_value}'"
        )

    def test_vf_contains_signalstats(self, tmp_path: Path) -> None:
        """-vf value must contain 'signalstats' filter."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_color.analyze.run", _capture)
            measure_brightness(media, opts)

        cmd = captured_cmds[0]
        cmd_str = " ".join(cmd)
        assert "signalstats" in cmd_str, f"'signalstats' not in command: {cmd_str}"

    def test_vf_contains_fps_filter_with_interval(self, tmp_path: Path) -> None:
        """-vf must include fps=1/<sample_interval_sec> for the given interval."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions(sample_interval_sec=2.0)
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_color.analyze.run", _capture)
            measure_brightness(media, opts)

        cmd = captured_cmds[0]
        vf_index = cmd.index("-vf")
        vf_value = cmd[vf_index + 1]
        assert "fps=1/2" in vf_value, (
            f"fps=1/2 not found in vf value for interval=2.0: {vf_value}"
        )

    def test_cmd_includes_null_output(self, tmp_path: Path) -> None:
        """Command must include -f null - (no video output file needed)."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_color.analyze.run", _capture)
            measure_brightness(media, opts)

        cmd = captured_cmds[0]
        assert "null" in cmd, f"'null' not found in command: {cmd}"


# ===========================================================================
# (h) inf/nan token in YAVG -> measured=None
# ===========================================================================


class TestInfNanYavg:
    """When YAVG contains inf/nan, BrightnessMeasured validation fails -> measured=None."""

    def test_inf_yavg_token_gives_measured_none(self, tmp_path: Path) -> None:
        """YAVG=inf in stderr must result in measured=None (ValidationError degrade)."""
        from clipwright_color.analyze import (
            measure_brightness,  # type: ignore[import-not-found]
        )
        from clipwright_color.schemas import (
            DetectColorOptions,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_color.analyze.resolve_tool", _fake_resolve)
            mp.setattr(
                "clipwright_color.analyze.run",
                _make_run_ok(_SIGNALSTATS_STDERR_INF_YAVG),
            )
            result = measure_brightness(media, opts)

        assert result["measured"] is None, (
            "YAVG=inf must degrade to measured=None via BrightnessMeasured validation."
        )
