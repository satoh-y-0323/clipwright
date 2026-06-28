"""test_analyze.py — Tests for clipwright_stabilize.analyze.

Mock policy (F-1):
  - Patch clipwright_stabilize.analyze.resolve_tool to control the ffmpeg binary path.
  - Patch clipwright_stabilize.analyze.run to control ffmpeg stdout/stderr/returncode.
  - No real ffmpeg binary or real libvidstab is invoked.
  - Severity unit tests use real .trf fixture files (offline, no ffmpeg).

Verification points:
  (1) argv contains "vidstabdetect=result=<media_stem>.stabilize.trf:shakiness=<s>:accuracy=<a>"
      with relative result basename (cwd+relative approach, P-2/P-3).
  (2) run is called with cwd=<trf output directory> (kwarg assert).
  (3) timeout is passed as a positive value to run.
  (4) UNSUPPORTED branch: run raises ClipwrightError(code=SUBPROCESS_FAILED,
      message containing "No such filter" or "Unknown filter")
      -> run_vidstabdetect re-raises with ErrorCode.UNSUPPORTED_OPERATION,
         fixed message (no abs path / raw stderr), from None (CWE-209).
  (5) Other run failure (e.g. timeout) -> fixed message, no path exposure.
  (6) rc=0 but .trf not generated -> SUBPROCESS_FAILED (same defense as frames).
  (7) _estimate_severity: real TRF1 fixtures -> non-None float in [0.0, 1.0] (AC-1).
      Ordering: calm severity < shaky severity (AC-2).
      Overflow regression: large doubles must not cause inf -> None (old bug fix).
      Graceful (NF-2): broken/unknown magic / empty body / oversized -> None, no raise.
      severity=None -> warnings has 1 entry in run_vidstabdetect return.
  -vf is a single argv element (CWE-78).
  -i uses absolute input path (cwd-independent).
  cmd ends with -f null -.

Requirements: FR-2-1 (ffmpeg invocation), FR-2-3 (UNSUPPORTED detection),
FR-2-4 (severity best-effort), architecture-report §4-A/§4-B/§4-C, F-1.
"""

from __future__ import annotations

import struct
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import pytest
from clipwright.errors import ClipwrightError, ErrorCode

_FAKE_FFMPEG = "/usr/local/bin/ffmpeg"

# Real TRF1 fixture files produced by ffmpeg vidstabdetect (offline, no ffmpeg needed).
# shaky.stabilize.trf — high-shake source, 14 KB.
# calm.stabilize.trf  — low-motion source,  28 KB.
_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_SHAKY_TRF = _FIXTURES_DIR / "shaky.stabilize.trf"
_CALM_TRF = _FIXTURES_DIR / "calm.stabilize.trf"


def _fake_resolve(name: str, env_var: str | None = None) -> str:
    """Success mock for resolve_tool."""
    return _FAKE_FFMPEG


def _make_run_ok(trf_path: Path) -> Any:
    """Return a closure that writes an empty .trf file and returns rc=0."""

    def _impl(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        # Actually create the .trf file so rc=0 + exists() check passes.
        trf_path.write_bytes(b"TRF1")
        return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    return _impl


def _make_run_ok_no_file() -> Any:
    """Return a closure that does NOT write the .trf file (rc=0, file absent)."""

    def _impl(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    return _impl


def _make_run_fail(code: ErrorCode, message: str) -> Any:
    """Return a closure that raises ClipwrightError with given code/message."""

    def _impl(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        raise ClipwrightError(
            code=code,
            message=message,
            hint="Check ffmpeg.",
        )

    return _impl


# ===========================================================================
# (1) argv contains correct vidstabdetect filtergraph string
# ===========================================================================


class TestArgvFiltergraph:
    """Verify argv contains the correct vidstabdetect filtergraph (FR-2-1, P-2/P-3)."""

    def test_vf_contains_vidstabdetect_with_relative_basename(
        self, tmp_path: Path
    ) -> None:
        """vidstabdetect=result= must use relative trf basename (no path sep)."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            run_vidstabdetect,
        )
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )

        media = tmp_path / "myvideo.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "myvideo.otio"
        opts = DetectShakeOptions(shakiness=7, accuracy=10, smoothing=30)
        captured_cmds: list[list[str]] = []
        expected_trf = tmp_path / "myvideo.stabilize.trf"

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            expected_trf.write_bytes(b"TRF1")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_stabilize.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_stabilize.analyze.run", _capture)
            run_vidstabdetect(media, output, opts)

        assert len(captured_cmds) == 1
        cmd = captured_cmds[0]
        # -vf must be present
        assert "-vf" in cmd, "-vf must be in the command"
        vf_idx = cmd.index("-vf")
        vf_val = cmd[vf_idx + 1]
        # relative trf basename (no '/' or '\\' separator inside result=...)
        assert "vidstabdetect=result=myvideo.stabilize.trf" in vf_val, (
            f"expected 'vidstabdetect=result=myvideo.stabilize.trf' in vf, got: {vf_val}"
        )

    def test_vf_contains_shakiness(self, tmp_path: Path) -> None:
        """vidstabdetect must include shakiness=<s> matching the option."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            run_vidstabdetect,
        )
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )

        media = tmp_path / "v.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "v.otio"
        opts = DetectShakeOptions(shakiness=3, accuracy=10, smoothing=30)
        captured_cmds: list[list[str]] = []
        trf = tmp_path / "v.stabilize.trf"

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            trf.write_bytes(b"TRF1")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_stabilize.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_stabilize.analyze.run", _capture)
            run_vidstabdetect(media, output, opts)

        vf_idx = captured_cmds[0].index("-vf")
        vf_val = captured_cmds[0][vf_idx + 1]
        assert ":shakiness=3" in vf_val, f"shakiness=3 not in vf: {vf_val}"

    def test_vf_contains_accuracy(self, tmp_path: Path) -> None:
        """vidstabdetect must include accuracy=<a> matching the option."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            run_vidstabdetect,
        )
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )

        media = tmp_path / "v.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "v.otio"
        opts = DetectShakeOptions(shakiness=5, accuracy=12, smoothing=30)
        captured_cmds: list[list[str]] = []
        trf = tmp_path / "v.stabilize.trf"

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            trf.write_bytes(b"TRF1")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_stabilize.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_stabilize.analyze.run", _capture)
            run_vidstabdetect(media, output, opts)

        vf_idx = captured_cmds[0].index("-vf")
        vf_val = captured_cmds[0][vf_idx + 1]
        assert ":accuracy=12" in vf_val, f"accuracy=12 not in vf: {vf_val}"

    def test_input_is_absolute_path(self, tmp_path: Path) -> None:
        """-i must use absolute media path (cwd-independent)."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            run_vidstabdetect,
        )
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )

        media = tmp_path / "v.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "v.otio"
        opts = DetectShakeOptions()
        captured_cmds: list[list[str]] = []
        trf = tmp_path / "v.stabilize.trf"

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            trf.write_bytes(b"TRF1")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_stabilize.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_stabilize.analyze.run", _capture)
            run_vidstabdetect(media, output, opts)

        cmd = captured_cmds[0]
        assert "-i" in cmd
        i_idx = cmd.index("-i")
        input_arg = cmd[i_idx + 1]
        assert Path(input_arg).is_absolute(), (
            f"-i must be absolute path, got: {input_arg}"
        )

    def test_cmd_ends_with_f_null(self, tmp_path: Path) -> None:
        """Command must end with -f null - (discard video output)."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            run_vidstabdetect,
        )
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )

        media = tmp_path / "v.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "v.otio"
        opts = DetectShakeOptions()
        captured_cmds: list[list[str]] = []
        trf = tmp_path / "v.stabilize.trf"

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            trf.write_bytes(b"TRF1")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_stabilize.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_stabilize.analyze.run", _capture)
            run_vidstabdetect(media, output, opts)

        cmd = captured_cmds[0]
        assert cmd[-3:] == ["-f", "null", "-"], (
            f"command must end with -f null -, got tail: {cmd[-3:]}"
        )

    def test_vf_result_sanitized_for_special_chars(self, tmp_path: Path) -> None:
        """filtergraph special chars in media stem must be replaced in result= (SR-INJ-002)."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            run_vidstabdetect,
        )
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )

        # Stem contains ':' and ';' — filtergraph-unsafe on Linux.
        # Windows does not allow ':' in filenames, so we substitute a safe-looking
        # special char that is allowed on both platforms but unsafe in filtergraphs.
        media = tmp_path / "my video[clip].mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "my video[clip].otio"
        opts = DetectShakeOptions()
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            # Write the sanitized trf (brackets replaced with underscores).
            trf = tmp_path / "my_video_clip_.stabilize.trf"
            trf.write_bytes(b"TRF1")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_stabilize.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_stabilize.analyze.run", _capture)
            run_vidstabdetect(media, output, opts)

        cmd = captured_cmds[0]
        vf_idx = cmd.index("-vf")
        vf_val = cmd[vf_idx + 1]
        # Brackets and spaces must NOT appear in result=
        assert "[" not in vf_val, f"'[' must not appear in vf: {vf_val}"
        assert "]" not in vf_val, f"']' must not appear in vf: {vf_val}"
        assert " " not in vf_val, f"space must not appear in vf: {vf_val}"
        # Sanitized stem must appear (spaces and brackets replaced with '_')
        assert "my_video_clip_" in vf_val, (
            f"sanitized stem 'my_video_clip_' not found in vf: {vf_val}"
        )


# ===========================================================================
# (2) run is called with cwd=<trf output directory>
# ===========================================================================


class TestRunCwd:
    """Verify run is called with cwd=<trf output directory> (P-3, FR-2-1)."""

    def test_run_called_with_correct_cwd(self, tmp_path: Path) -> None:
        """run must be called with cwd=str(output.parent) (the trf directory)."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            run_vidstabdetect,
        )
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )

        media = tmp_path / "v.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "v.otio"
        opts = DetectShakeOptions()
        captured_kwargs: list[dict[str, Any]] = []
        trf = tmp_path / "v.stabilize.trf"

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_kwargs.append(kwargs)
            trf.write_bytes(b"TRF1")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_stabilize.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_stabilize.analyze.run", _capture)
            run_vidstabdetect(media, output, opts)

        assert len(captured_kwargs) == 1
        kw = captured_kwargs[0]
        assert "cwd" in kw, "run must receive 'cwd' keyword argument"
        assert kw["cwd"] == str(tmp_path), (
            f"cwd must be str(output.parent)={str(tmp_path)}, got: {kw['cwd']}"
        )


# ===========================================================================
# (3) timeout is passed as a positive value
# ===========================================================================


class TestRunTimeout:
    """Verify run receives a positive timeout value (F-5, §4-A)."""

    def test_run_receives_positive_timeout(self, tmp_path: Path) -> None:
        """run must receive a positive timeout kwarg."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            run_vidstabdetect,
        )
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )

        media = tmp_path / "v.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "v.otio"
        opts = DetectShakeOptions()
        captured_kwargs: list[dict[str, Any]] = []
        trf = tmp_path / "v.stabilize.trf"

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_kwargs.append(kwargs)
            trf.write_bytes(b"TRF1")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_stabilize.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_stabilize.analyze.run", _capture)
            run_vidstabdetect(media, output, opts)

        kw = captured_kwargs[0]
        assert "timeout" in kw, "run must receive 'timeout' keyword argument"
        assert isinstance(kw["timeout"], (int, float))
        assert kw["timeout"] > 0


# ===========================================================================
# (4) UNSUPPORTED branch: Unknown filter / No such filter -> UNSUPPORTED_OPERATION
# ===========================================================================


class TestUnsupportedOperation:
    """libvidstab not supported -> UNSUPPORTED_OPERATION (FR-2-3, §4-B, CWE-209)."""

    @pytest.mark.parametrize(
        "stderr_msg",
        ["Unknown filter 'vidstabdetect'", "No such filter: 'vidstabdetect'"],
    )
    def test_unsupported_filter_raises_unsupported_operation(
        self, tmp_path: Path, stderr_msg: str
    ) -> None:
        """stderr with 'Unknown filter' or 'No such filter' must raise UNSUPPORTED_OPERATION."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            run_vidstabdetect,
        )
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )

        media = tmp_path / "v.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "v.otio"
        opts = DetectShakeOptions()

        def _fail(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=stderr_msg,
                hint="ffmpeg failed.",
            )

        with (
            pytest.MonkeyPatch().context() as mp,
            pytest.raises(ClipwrightError) as exc_info,
        ):
            mp.setattr("clipwright_stabilize.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_stabilize.analyze.run", _fail)
            run_vidstabdetect(media, output, opts)

        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION

    def test_unsupported_message_no_absolute_path(self, tmp_path: Path) -> None:
        """UNSUPPORTED_OPERATION message must not expose absolute path (CWE-209)."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            run_vidstabdetect,
        )
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )

        media = tmp_path / "v.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "v.otio"
        opts = DetectShakeOptions()

        def _fail(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=f"Unknown filter 'vidstabdetect' at {tmp_path}",
                hint="ffmpeg failed.",
            )

        with (
            pytest.MonkeyPatch().context() as mp,
            pytest.raises(ClipwrightError) as exc_info,
        ):
            mp.setattr("clipwright_stabilize.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_stabilize.analyze.run", _fail)
            run_vidstabdetect(media, output, opts)

        err = exc_info.value
        assert str(tmp_path) not in err.message, (
            "CWE-209: absolute path must not appear in UNSUPPORTED_OPERATION message"
        )
        assert str(tmp_path) not in err.hint, (
            "CWE-209: absolute path must not appear in UNSUPPORTED_OPERATION hint"
        )
        assert exc_info.value.__cause__ is None, (
            "CWE-209: re-raised error must use 'from None' to suppress __cause__ "
            "(prevents raw exc chain from leaking abs paths / stderr to callers)"
        )

    def test_unsupported_hint_contains_libvidstab_guidance(
        self, tmp_path: Path
    ) -> None:
        """UNSUPPORTED_OPERATION hint must contain libvidstab installation guidance."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            run_vidstabdetect,
        )
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )

        media = tmp_path / "v.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "v.otio"
        opts = DetectShakeOptions()

        def _fail(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="Unknown filter 'vidstabdetect'",
                hint=".",
            )

        with (
            pytest.MonkeyPatch().context() as mp,
            pytest.raises(ClipwrightError) as exc_info,
        ):
            mp.setattr("clipwright_stabilize.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_stabilize.analyze.run", _fail)
            run_vidstabdetect(media, output, opts)

        hint = exc_info.value.hint.lower()
        assert "libvidstab" in hint, (
            f"hint must contain 'libvidstab' installation guidance, got: {exc_info.value.hint}"
        )


# ===========================================================================
# (5) Other run failure -> fixed message, no path exposure
# ===========================================================================


class TestOtherRunFailure:
    """Non-UNSUPPORTED run failures must re-raise with fixed message (no path)."""

    def test_subprocess_timeout_reraises_with_fixed_message(
        self, tmp_path: Path
    ) -> None:
        """SUBPROCESS_TIMEOUT must be re-raised without exposing abs path (CWE-209)."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            run_vidstabdetect,
        )
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )

        media = tmp_path / "v.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "v.otio"
        opts = DetectShakeOptions()

        def _timeout(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_TIMEOUT,
                message=f"timed out processing {tmp_path}",
                hint=".",
            )

        with (
            pytest.MonkeyPatch().context() as mp,
            pytest.raises(ClipwrightError) as exc_info,
        ):
            mp.setattr("clipwright_stabilize.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_stabilize.analyze.run", _timeout)
            run_vidstabdetect(media, output, opts)

        err = exc_info.value
        assert str(tmp_path) not in err.message, (
            "CWE-209: absolute path must not appear in re-raised error message"
        )
        assert exc_info.value.__cause__ is None, (
            "CWE-209: re-raised error must use 'from None' to suppress __cause__ "
            "(prevents raw exc chain from leaking abs paths / stderr to callers)"
        )

    def test_subprocess_failed_no_unsupported_reraises(self, tmp_path: Path) -> None:
        """SUBPROCESS_FAILED without filter keyword re-raises with sanitised message."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            run_vidstabdetect,
        )
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )

        media = tmp_path / "v.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "v.otio"
        opts = DetectShakeOptions()

        def _fail(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=f"ffmpeg exited 1 in {tmp_path}",
                hint=".",
            )

        with (
            pytest.MonkeyPatch().context() as mp,
            pytest.raises(ClipwrightError) as exc_info,
        ):
            mp.setattr("clipwright_stabilize.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_stabilize.analyze.run", _fail)
            run_vidstabdetect(media, output, opts)

        err = exc_info.value
        assert str(tmp_path) not in err.message
        assert exc_info.value.__cause__ is None, (
            "CWE-209: re-raised error must use 'from None' to suppress __cause__ "
            "(prevents raw exc chain from leaking abs paths / stderr to callers)"
        )


# ===========================================================================
# (6) rc=0 but .trf not generated -> SUBPROCESS_FAILED
# ===========================================================================


class TestTrfNotGenerated:
    """rc=0 but .trf absent must raise SUBPROCESS_FAILED (§4-D defense)."""

    def test_rc0_without_trf_raises_subprocess_failed(self, tmp_path: Path) -> None:
        """SUBPROCESS_FAILED must be raised when rc=0 but .trf file does not exist."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            run_vidstabdetect,
        )
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )

        media = tmp_path / "v.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "v.otio"
        opts = DetectShakeOptions()

        # run succeeds but does NOT write the .trf file
        def _ok_no_file(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            pytest.MonkeyPatch().context() as mp,
            pytest.raises(ClipwrightError) as exc_info,
        ):
            mp.setattr("clipwright_stabilize.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_stabilize.analyze.run", _ok_no_file)
            run_vidstabdetect(media, output, opts)

        assert exc_info.value.code == ErrorCode.SUBPROCESS_FAILED


# ===========================================================================
# (7) _estimate_severity — real fixture-driven tests (AC-1 / AC-2 / regression)
# ===========================================================================


class TestEstimateSeverityFixtures:
    """_estimate_severity with real TRF1 fixture files (offline unit test, no ffmpeg).

    Fixture files are committed to tests/fixtures/:
      shaky.stabilize.trf — high-motion source, 14 KB, real TRF1 output.
      calm.stabilize.trf  — low-motion source,  28 KB, real TRF1 output.

    These tests exercise the overflow-safe computation path.  The old
    implementation summed raw IEEE-754 doubles naively; when doubles encoded
    mis-read int32 values they were astronomically large (≈ 1.7e308) and
    sum() overflowed to inf → severity = inf/30.0 = inf → not isfinite()
    guard → None.  The tests below nail that regression.
    """

    def test_shaky_trf_returns_nonnone_float_in_range(self) -> None:
        """AC-1: shaky.stabilize.trf must return a non-None float in [0.0, 1.0].

        Fails with the old implementation (sum overflow → inf → None).
        """
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        result = _estimate_severity(_SHAKY_TRF)
        assert result is not None, (
            "shaky.stabilize.trf returned None — overflow-to-inf regression detected "
            "(large doubles: sum() overflows to inf → isfinite guard → None)"
        )
        assert 0.0 <= result <= 1.0, f"severity out of range: {result}"

    def test_calm_trf_returns_nonnone_float_in_range(self) -> None:
        """AC-1: calm.stabilize.trf must return a non-None float in [0.0, 1.0].

        Fails with the old implementation (sum overflow → inf → None).
        """
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        result = _estimate_severity(_CALM_TRF)
        assert result is not None, (
            "calm.stabilize.trf returned None — overflow-to-inf regression detected "
            "(large doubles: sum() overflows to inf → isfinite guard → None)"
        )
        assert 0.0 <= result <= 1.0, f"severity out of range: {result}"

    def test_calm_severity_less_than_shaky(self) -> None:
        """AC-2: calm fixture must score strictly lower severity than shaky fixture.

        Ordering property: low-motion source < high-motion source.
        Fails with the old implementation because both return None.
        """
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        calm = _estimate_severity(_CALM_TRF)
        shaky = _estimate_severity(_SHAKY_TRF)
        assert calm is not None and shaky is not None, (
            f"prerequisite failed: calm={calm}, shaky={shaky} — both must be non-None"
        )
        assert calm < shaky, (
            f"expected calm ({calm:.4f}) < shaky ({shaky:.4f}) — ordering violated"
        )

    def test_real_trf_not_none_overflow_regression(self) -> None:
        """Regression guard: both real .trf files must return a value, not None.

        Old bug: large int32-reinterpreted doubles (≈ 1.7e308 each) caused
        sum(finite_abs) to overflow to inf.  severity = inf / 30.0 = inf.
        Then `if not math.isfinite(severity): return None` returned None even
        though the file was structurally valid.  This test pins that regression
        so it is detected immediately if the overflow-safe fix is reverted.
        """
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        for trf_path in (_SHAKY_TRF, _CALM_TRF):
            result = _estimate_severity(trf_path)
            assert result is not None, (
                f"{trf_path.name} returned None — "
                "large-double sum-overflow regression re-introduced"
            )


# ===========================================================================
# (7b) _estimate_severity — graceful handling of bad / adversarial inputs (NF-2)
# ===========================================================================


class TestEstimateSeverityGraceful:
    """_estimate_severity must return None (never raise) on corrupt / unknown inputs.

    These tests use synthetic byte sequences — real fixtures are not required
    because the inputs represent conditions that real vidstabdetect would never
    produce (wrong magic, truncated body, oversized file).
    """

    def test_magic_mismatch_returns_none(self, tmp_path: Path) -> None:
        """Wrong magic bytes must return None without raising (unknown format)."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        trf = tmp_path / "bad.trf"
        trf.write_bytes(b"BAAD" + b"\x00" * 8)
        assert _estimate_severity(trf) is None

    def test_future_magic_returns_none(self, tmp_path: Path) -> None:
        """Future / unknown magic (e.g. TRF2) must return None (forward-compat, NF-2)."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        trf = tmp_path / "future.trf"
        trf.write_bytes(b"TRF2" + b"\x00" * 8)
        assert _estimate_severity(trf) is None

    def test_truncated_body_returns_none(self, tmp_path: Path) -> None:
        """TRF1 with body shorter than one double (< 8 bytes) must return None."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        trf = tmp_path / "truncated.trf"
        trf.write_bytes(b"TRF1" + b"\xde\xad\xbe")  # 3-byte body: n = 3 // 8 = 0
        assert _estimate_severity(trf) is None

    def test_empty_body_returns_none(self, tmp_path: Path) -> None:
        """TRF1 magic with no body (zero doubles) must return None."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        trf = tmp_path / "empty.trf"
        trf.write_bytes(b"TRF1")
        assert _estimate_severity(trf) is None

    def test_oversized_file_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """File exceeding _TRF_MAX_BYTES must return None, not raise (OOM guard, SR-MEM-001).

        _TRF_MAX_BYTES is patched to 4 so a 12-byte file triggers the guard
        without allocating 100 MB in the test suite.
        """
        import clipwright_stabilize.analyze as _analyze_mod
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        monkeypatch.setattr(_analyze_mod, "_TRF_MAX_BYTES", 4)
        trf = tmp_path / "oversized.trf"
        trf.write_bytes(b"TRF1" + b"\x00" * 8)  # 12 bytes > patched limit of 4
        assert _estimate_severity(trf) is None

    def test_excessive_lm_count_per_frame_returns_none(self, tmp_path: Path) -> None:
        """Frame with count > _MAX_LM_PER_FRAME must return None (OOM guard, SR-MEM-001).

        Synthetic TRF1 with count = 2**31-1 (max signed int32) — far beyond any
        realistic vidstabdetect output — must trigger the per-frame guard and return
        None without attempting to allocate memory for range(count) LM entries.
        """
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        # Build a minimal TRF1 binary with an absurd LM count in the first frame.
        # Layout: magic(4) + 3×int32(12) + double(8) = 24 B header,
        #         then frame_num(int32=0) + count(int32=2**31-1) = 8 B.
        header = b"TRF1" + struct.pack("<3i", 0, 0, 0) + struct.pack("<d", 0.0)
        frame_prefix = struct.pack("<2i", 0, 2**31 - 1)
        trf = tmp_path / "huge_count.trf"
        trf.write_bytes(header + frame_prefix)

        assert _estimate_severity(trf) is None, (
            "count=2**31-1 per frame must return None (OOM guard), not raise"
        )


class TestSeverityNoneWarning:
    """When severity=None, run_vidstabdetect must add a warning entry."""

    def test_severity_none_adds_warning(self, tmp_path: Path) -> None:
        """When .trf cannot be parsed for severity, warnings must have >= 1 entry."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            run_vidstabdetect,
        )
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )

        media = tmp_path / "v.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "v.otio"
        opts = DetectShakeOptions()
        trf = tmp_path / "v.stabilize.trf"

        def _ok_bad_trf(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            # Write a trf with wrong magic -> _estimate_severity returns None
            trf.write_bytes(b"BAAD" + struct.pack("<4d", 1.0, 2.0, 3.0, 4.0))
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_stabilize.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_stabilize.analyze.run", _ok_bad_trf)
            result = run_vidstabdetect(media, output, opts)

        assert result["severity"] is None
        assert len(result["warnings"]) >= 1, (
            "warnings must have >= 1 entry when severity cannot be estimated"
        )


# ===========================================================================
# argv discipline: single -vf element, list[str], starts with ffmpeg binary
# ===========================================================================


class TestArgvDiscipline:
    """Verify subprocess calling discipline (CWE-78, NFR-3)."""

    def test_vf_is_single_argv_element(self, tmp_path: Path) -> None:
        """-vf value must be a single argv element (not split on commas/spaces)."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            run_vidstabdetect,
        )
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )

        media = tmp_path / "v.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "v.otio"
        opts = DetectShakeOptions()
        captured_cmds: list[list[str]] = []
        trf = tmp_path / "v.stabilize.trf"

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            trf.write_bytes(b"TRF1")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_stabilize.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_stabilize.analyze.run", _capture)
            run_vidstabdetect(media, output, opts)

        cmd = captured_cmds[0]
        assert "-vf" in cmd
        vf_idx = cmd.index("-vf")
        # Confirm vf_idx+1 is the next element (i.e., -vf is not the last item)
        assert vf_idx + 1 < len(cmd), "-vf must be followed by its value"
        vf_val = cmd[vf_idx + 1]
        # -vf value must be a single string (not two separate elements)
        assert isinstance(vf_val, str), f"-vf value must be str, got: {type(vf_val)}"
        # The value itself must contain the filter name
        assert "vidstabdetect" in vf_val

    def test_cmd_is_list_of_strings(self, tmp_path: Path) -> None:
        """Command passed to run must be list[str] (shell=False equivalent, CWE-78)."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            run_vidstabdetect,
        )
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )

        media = tmp_path / "v.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "v.otio"
        opts = DetectShakeOptions()
        captured_cmds: list[Any] = []
        trf = tmp_path / "v.stabilize.trf"

        def _capture(cmd: Any, **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            trf.write_bytes(b"TRF1")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_stabilize.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_stabilize.analyze.run", _capture)
            run_vidstabdetect(media, output, opts)

        cmd = captured_cmds[0]
        assert isinstance(cmd, list), f"cmd must be list, got {type(cmd)}"
        for arg in cmd:
            assert isinstance(arg, str), f"each arg must be str, got {arg!r}"

    def test_cmd_starts_with_ffmpeg_binary(self, tmp_path: Path) -> None:
        """First argument must be the ffmpeg binary from resolve_tool."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            run_vidstabdetect,
        )
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )

        media = tmp_path / "v.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "v.otio"
        opts = DetectShakeOptions()
        captured_cmds: list[list[str]] = []
        trf = tmp_path / "v.stabilize.trf"

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            trf.write_bytes(b"TRF1")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("clipwright_stabilize.analyze.resolve_tool", _fake_resolve)
            mp.setattr("clipwright_stabilize.analyze.run", _capture)
            run_vidstabdetect(media, output, opts)

        assert captured_cmds[0][0] == _FAKE_FFMPEG


# ===========================================================================
# Text TRF (VID.STAB) format — Linux/apt/brew libvidstab cross-platform tests
# ===========================================================================

# Real VID.STAB text fixture files produced by Docker ubuntu24.04/libvidstab1.1.
_TXT_SHAKY_TRF = _FIXTURES_DIR / "linux_text_shaky.stabilize.trf"
_TXT_CALM_TRF = _FIXTURES_DIR / "linux_text_calm.stabilize.trf"


class TestEstimateSeverityTextTrf:
    """_estimate_severity with real VID.STAB text fixture files (offline unit tests).

    Fixture files produced by docker ubuntu24.04/libvidstab1.1 vidstabdetect:
      linux_text_shaky.stabilize.trf — high-motion source.
      linux_text_calm.stabilize.trf  — low-motion source.

    These tests verify cross-platform severity estimation; the text format
    is produced by Linux apt and macOS brew libvidstab builds, while the
    binary TRF1 format is produced by Gyan Windows ffmpeg builds.
    """

    def test_text_shaky_trf_returns_nonnone_float_in_range(self) -> None:
        """AC-1 (text): linux_text_shaky.stabilize.trf must return non-None float in [0.0, 1.0]."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        result = _estimate_severity(_TXT_SHAKY_TRF)
        assert result is not None, (
            "linux_text_shaky.stabilize.trf returned None — text TRF parser failed"
        )
        assert 0.0 <= result <= 1.0, f"severity out of range: {result}"

    def test_text_calm_trf_returns_nonnone_float_in_range(self) -> None:
        """AC-1 (text): linux_text_calm.stabilize.trf must return non-None float in [0.0, 1.0]."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        result = _estimate_severity(_TXT_CALM_TRF)
        assert result is not None, (
            "linux_text_calm.stabilize.trf returned None — text TRF parser failed"
        )
        assert 0.0 <= result <= 1.0, f"severity out of range: {result}"

    def test_text_calm_severity_less_than_shaky(self) -> None:
        """AC-2 (text): calm fixture must score strictly lower severity than shaky fixture."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        calm = _estimate_severity(_TXT_CALM_TRF)
        shaky = _estimate_severity(_TXT_SHAKY_TRF)
        assert calm is not None and shaky is not None, (
            f"prerequisite failed: calm={calm}, shaky={shaky} — both must be non-None"
        )
        assert calm < shaky, (
            f"expected text calm ({calm:.4f}) < text shaky ({shaky:.4f}) — ordering violated"
        )

    def test_text_calm_recommends_skip(self) -> None:
        """Integration: text calm fixture must recommend 'skip' via recommend()."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
            recommend,
        )

        severity = _estimate_severity(_TXT_CALM_TRF)
        assert recommend(severity) == "skip", (
            f"expected 'skip' for calm text fixture (severity={severity})"
        )

    def test_text_shaky_recommends_apply(self) -> None:
        """Integration: text shaky fixture must recommend 'apply' via recommend()."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
            recommend,
        )

        severity = _estimate_severity(_TXT_SHAKY_TRF)
        assert recommend(severity) == "apply", (
            f"expected 'apply' for shaky text fixture (severity={severity})"
        )


class TestEstimateSeverityTextTrfGraceful:
    """_estimate_severity graceful handling for text TRF edge cases (NF-2)."""

    def test_vidstab_header_no_frames_returns_none(self, tmp_path: Path) -> None:
        """VID.STAB header with no Frame lines must return None (no displacement data)."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        trf = tmp_path / "no_frames.trf"
        trf.write_bytes(b"VID.STAB 1\n# accuracy = 15\n# shakiness = 5\n")
        assert _estimate_severity(trf) is None

    def test_vidstab_header_only_empty_frames_returns_none(
        self, tmp_path: Path
    ) -> None:
        """VID.STAB with all empty Frame entries (List 0 []) must return None."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        trf = tmp_path / "empty_frames.trf"
        content = b"VID.STAB 1\nFrame 1 (List 0 [])\nFrame 2 (List 0 [])\n"
        trf.write_bytes(content)
        assert _estimate_severity(trf) is None

    def test_vidstab_excessive_lm_per_frame_skips_frame(self, tmp_path: Path) -> None:
        """Frame with LM count > _MAX_LM_PER_FRAME in text format is skipped (not None).

        A frame with fewer LMs in other frames must still yield a severity value.
        """
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _MAX_LM_PER_FRAME,
            _estimate_severity,
        )

        # Build a text TRF where frame 1 has an absurd number of LM entries
        # (by generating a line that tricks _LM_TEXT_RE into matching > limit).
        # We simulate this by patching the constant rather than generating
        # millions of LM entries.
        import clipwright_stabilize.analyze as _mod

        original = _MAX_LM_PER_FRAME
        try:
            # Patch limit to 2 so a frame with 3 LM entries triggers the guard.
            _mod._MAX_LM_PER_FRAME = 2
            # Frame 1: 3 LMs (exceeds patched limit=2) — skipped.
            # Frame 2: 1 LM  (within limit)             — included.
            content = (
                b"VID.STAB 1\n"
                b"Frame 1 (List 3 [(LM 10 10 0 0 32 0.5 1.0),(LM 5 5 0 0 32 0.5 1.0),(LM 3 3 0 0 32 0.5 1.0)])\n"
                b"Frame 2 (List 1 [(LM 2 2 0 0 32 0.5 1.0)])\n"
            )
            trf = tmp_path / "guard.trf"
            trf.write_bytes(content)
            result = _estimate_severity(trf)
            # Frame 2 has 1 LM with hypot(2,2)≈2.83, severity=2.83/30≈0.094 > 0
            assert result is not None, (
                "should return non-None from frame 2 when frame 1 is skipped by OOM guard"
            )
            assert 0.0 <= result <= 1.0
        finally:
            _mod._MAX_LM_PER_FRAME = original
