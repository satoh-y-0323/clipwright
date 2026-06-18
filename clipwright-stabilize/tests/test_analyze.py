"""test_analyze.py — Tests for clipwright_stabilize.analyze.

Mock policy (F-1):
  - Patch clipwright_stabilize.analyze.resolve_tool to control the ffmpeg binary path.
  - Patch clipwright_stabilize.analyze.run to control ffmpeg stdout/stderr/returncode.
  - No real ffmpeg binary or real libvidstab is invoked.
  - Severity integration tests (real .trf + real ffmpeg) are marked @pytest.mark.integration
    and are NOT included here.

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
  (7) _estimate_severity unit: TRF1 magic + IEEE-754 little-endian doubles -> 0.0-1.0.
      Magic mismatch / 0 doubles / all nan/inf / broken bytes -> None.
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


def _build_trf_binary(doubles: list[float]) -> bytes:
    """Build a minimal valid TRF1 binary payload."""
    header = b"TRF1"
    body = struct.pack(f"<{len(doubles)}d", *doubles)
    return header + body


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
        assert cmd[-2:] == ["-f", "null"] or cmd[-3:] == ["-f", "null", "-"], (
            f"command must end with -f null -, got tail: {cmd[-3:]}"
        )
        assert "-" in cmd, "'-' output sink must be present"


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
# (7) _estimate_severity unit tests
# ===========================================================================


class TestEstimateSeverity:
    """_estimate_severity: TRF1 binary -> 0.0-1.0, or None on parse failure (§4-C)."""

    def test_valid_trf_returns_float_in_range(self, tmp_path: Path) -> None:
        """Valid TRF1 + finite doubles must return a float in [0.0, 1.0]."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        trf = tmp_path / "test.stabilize.trf"
        # Use doubles around 15.0 (below _NORM_PX=30.0) -> severity ~ 0.5
        trf.write_bytes(_build_trf_binary([10.0, 15.0, 20.0, 10.0]))
        result = _estimate_severity(trf)
        assert result is not None
        assert 0.0 <= result <= 1.0

    def test_magic_mismatch_returns_none(self, tmp_path: Path) -> None:
        """Wrong magic bytes must return None."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        trf = tmp_path / "bad.stabilize.trf"
        trf.write_bytes(b"BAAD" + struct.pack("<4d", 1.0, 2.0, 3.0, 4.0))
        assert _estimate_severity(trf) is None

    def test_zero_doubles_returns_none(self, tmp_path: Path) -> None:
        """TRF1 magic with no doubles (empty body) must return None."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        trf = tmp_path / "empty.stabilize.trf"
        trf.write_bytes(b"TRF1")
        assert _estimate_severity(trf) is None

    def test_all_nan_returns_none(self, tmp_path: Path) -> None:
        """TRF1 with all NaN doubles must return None (no finite values)."""
        import math

        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        trf = tmp_path / "nan.stabilize.trf"
        trf.write_bytes(_build_trf_binary([math.nan, math.nan, math.nan]))
        assert _estimate_severity(trf) is None

    def test_all_inf_returns_none(self, tmp_path: Path) -> None:
        """TRF1 with all inf doubles must return None (no finite values)."""
        import math

        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        trf = tmp_path / "inf.stabilize.trf"
        trf.write_bytes(_build_trf_binary([math.inf, -math.inf]))
        assert _estimate_severity(trf) is None

    def test_broken_bytes_returns_none(self, tmp_path: Path) -> None:
        """TRF1 with non-alignable body bytes must return None (struct error)."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        trf = tmp_path / "broken.stabilize.trf"
        # 3 bytes body is not aligned to 8 bytes -> zero doubles -> None
        trf.write_bytes(b"TRF1" + b"\x01\x02\x03")
        assert _estimate_severity(trf) is None

    def test_large_doubles_clamp_to_one(self, tmp_path: Path) -> None:
        """Very large doubles must clamp severity to 1.0 (not exceed 1.0)."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        trf = tmp_path / "large.stabilize.trf"
        # Very large values -> mean_abs >> _NORM_PX -> severity should clamp to 1.0
        trf.write_bytes(_build_trf_binary([1e9, 2e9, 3e9]))
        result = _estimate_severity(trf)
        assert result is not None
        assert result == pytest.approx(1.0)

    def test_zero_values_give_near_zero_severity(self, tmp_path: Path) -> None:
        """All-zero doubles must give severity near 0.0."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
        )

        trf = tmp_path / "zero.stabilize.trf"
        trf.write_bytes(_build_trf_binary([0.0, 0.0, 0.0]))
        result = _estimate_severity(trf)
        assert result is not None
        assert result == pytest.approx(0.0)


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
