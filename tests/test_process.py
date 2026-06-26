"""test_process.py — Unit tests for the subprocess runner in process.py.

Covers:
- resolve_tool: PATH (shutil.which) → env fallback → ClipwrightError(DEPENDENCY_MISSING)
- run(cmd, timeout): success / non-zero exit → SUBPROCESS_FAILED /
  TimeoutExpired → SUBPROCESS_TIMEOUT
- Verify subprocess is called with shell=False and an argument list
"""

from __future__ import annotations

import os
import subprocess
from subprocess import CompletedProcess
from unittest.mock import MagicMock

import pytest

# --- Import ---
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.process import resolve_tool, run

# ===========================================================================
# resolve_tool — PATH first → env fallback → DEPENDENCY_MISSING
# ===========================================================================


class TestResolveToolPathPriority:
    """If shutil.which finds the tool, env_var is ignored and PATH result returned."""

    def test_returns_which_path_when_found(self, mocker: MagicMock) -> None:
        """Returns the path from shutil.which as-is (PATH takes highest priority)."""
        mocker.patch("shutil.which", return_value="/usr/bin/ffprobe")
        mocker.patch.dict("os.environ", {"CLIPWRIGHT_FFPROBE": "/env/ffprobe"})

        result = resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")

        assert result == "/usr/bin/ffprobe"

    def test_which_is_called_with_tool_name(self, mocker: MagicMock) -> None:
        """shutil.which is called with the tool name."""
        mock_which = mocker.patch("shutil.which", return_value="/usr/bin/ffprobe")

        resolve_tool("ffprobe")

        mock_which.assert_called_once_with("ffprobe")

    def test_env_var_not_used_when_which_finds_tool(self, mocker: MagicMock) -> None:
        """When PATH finds the tool, os.path.isfile is not called for the env var."""
        mocker.patch("shutil.which", return_value="/usr/bin/ffprobe")
        # Verify os.path.isfile is not called
        mock_isfile = mocker.patch("os.path.isfile")

        resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")

        mock_isfile.assert_not_called()


class TestResolveToolEnvFallback:
    """When shutil.which returns None, use the path from env_var."""

    def test_returns_env_var_path_when_which_returns_none(
        self, mocker: MagicMock
    ) -> None:
        """Returns the env var path when it points to a valid executable."""
        mocker.patch("shutil.which", return_value=None)
        mocker.patch.dict("os.environ", {"CLIPWRIGHT_FFPROBE": "/env/ffprobe.exe"})
        mocker.patch("os.path.isfile", return_value=True)
        mocker.patch("os.access", return_value=True)

        result = resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")

        assert result == "/env/ffprobe.exe"

    def test_env_var_path_must_be_file(self, mocker: MagicMock) -> None:
        """Raises DEPENDENCY_MISSING when the env var path does not exist."""
        mocker.patch("shutil.which", return_value=None)
        mocker.patch.dict("os.environ", {"CLIPWRIGHT_FFPROBE": "/nonexistent/ffprobe"})
        mocker.patch("os.path.isfile", return_value=False)

        with pytest.raises(ClipwrightError) as exc_info:
            resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")

        assert exc_info.value.code == ErrorCode.DEPENDENCY_MISSING

    def test_env_var_not_set_falls_through_to_missing(self, mocker: MagicMock) -> None:
        """Raises DEPENDENCY_MISSING when env var is not set and which returns None."""
        mocker.patch("shutil.which", return_value=None)
        mocker.patch.dict("os.environ", {}, clear=True)

        with pytest.raises(ClipwrightError) as exc_info:
            resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")

        assert exc_info.value.code == ErrorCode.DEPENDENCY_MISSING

    def test_without_env_var_argument_uses_only_which(self, mocker: MagicMock) -> None:
        """Raises DEPENDENCY_MISSING when env_var is omitted and which returns None."""
        mocker.patch("shutil.which", return_value=None)

        with pytest.raises(ClipwrightError) as exc_info:
            resolve_tool("ffprobe")

        assert exc_info.value.code == ErrorCode.DEPENDENCY_MISSING


class TestResolveToolDependencyMissingError:
    """Verify the error content when DEPENDENCY_MISSING is raised."""

    def test_error_has_message_and_hint(self, mocker: MagicMock) -> None:
        """ClipwrightError includes message and hint (§6.4 contract)."""
        mocker.patch("shutil.which", return_value=None)
        mocker.patch.dict("os.environ", {}, clear=True)

        with pytest.raises(ClipwrightError) as exc_info:
            resolve_tool("ffprobe")

        err = exc_info.value
        assert len(err.message) > 0
        assert len(err.hint) > 0

    def test_error_message_contains_tool_name(self, mocker: MagicMock) -> None:
        """The message includes the tool name (for debuggability)."""
        mocker.patch("shutil.which", return_value=None)
        mocker.patch.dict("os.environ", {}, clear=True)

        with pytest.raises(ClipwrightError) as exc_info:
            resolve_tool("ffprobe")

        assert "ffprobe" in exc_info.value.message


# ===========================================================================
# run — success / non-zero exit / timeout
# ===========================================================================


class TestRunSuccess:
    """Happy path when subprocess.run exits with returncode=0."""

    def test_returns_completed_process_on_success(self, mocker: MagicMock) -> None:
        """Returns CompletedProcess on success."""
        mock_cp = CompletedProcess(
            args=["ffprobe", "-version"],
            returncode=0,
            stdout="ffprobe version 6.0",
            stderr="",
        )
        mocker.patch("subprocess.run", return_value=mock_cp)

        result = run(["ffprobe", "-version"])

        assert result.returncode == 0
        assert result.stdout == "ffprobe version 6.0"

    def test_subprocess_called_with_shell_false(self, mocker: MagicMock) -> None:
        """subprocess.run is called with shell=False (§6.5 contract)."""
        mock_cp = CompletedProcess(
            args=["ffprobe", "-version"], returncode=0, stdout="", stderr=""
        )
        mock_run = mocker.patch("subprocess.run", return_value=mock_cp)

        run(["ffprobe", "-version"])

        _call_kwargs = mock_run.call_args.kwargs
        assert _call_kwargs.get("shell") is False

    def test_subprocess_called_with_list_cmd(self, mocker: MagicMock) -> None:
        """The first argument to subprocess.run is a list (argument array)."""
        mock_cp = CompletedProcess(
            args=["ffprobe", "-version"], returncode=0, stdout="", stderr=""
        )
        mock_run = mocker.patch("subprocess.run", return_value=mock_cp)
        cmd = ["ffprobe", "-i", "video.mp4"]

        run(cmd)

        positional_arg = mock_run.call_args.args[0]
        assert isinstance(positional_arg, list)
        assert positional_arg == cmd

    def test_subprocess_called_with_capture_output(self, mocker: MagicMock) -> None:
        """Called with capture_output=True (captures stdout/stderr)."""
        mock_cp = CompletedProcess(args=["ffprobe"], returncode=0, stdout="", stderr="")
        mock_run = mocker.patch("subprocess.run", return_value=mock_cp)

        run(["ffprobe"])

        _call_kwargs = mock_run.call_args.kwargs
        assert _call_kwargs.get("capture_output") is True

    def test_subprocess_called_with_text_true(self, mocker: MagicMock) -> None:
        """Called with text=True (stdout/stderr returned as str)."""
        mock_cp = CompletedProcess(args=["ffprobe"], returncode=0, stdout="", stderr="")
        mock_run = mocker.patch("subprocess.run", return_value=mock_cp)

        run(["ffprobe"])

        _call_kwargs = mock_run.call_args.kwargs
        assert _call_kwargs.get("text") is True

    def test_timeout_is_passed_to_subprocess(self, mocker: MagicMock) -> None:
        """The timeout argument is forwarded to subprocess.run."""
        mock_cp = CompletedProcess(args=["ffprobe"], returncode=0, stdout="", stderr="")
        mock_run = mocker.patch("subprocess.run", return_value=mock_cp)

        run(["ffprobe"], timeout=30.0)

        _call_kwargs = mock_run.call_args.kwargs
        assert _call_kwargs.get("timeout") == 30.0

    def test_default_timeout_is_set(self, mocker: MagicMock) -> None:
        """A default timeout is set even when the timeout argument is omitted
        (unlimited timeout is prohibited)."""
        mock_cp = CompletedProcess(args=["ffprobe"], returncode=0, stdout="", stderr="")
        mock_run = mocker.patch("subprocess.run", return_value=mock_cp)

        run(["ffprobe"])

        _call_kwargs = mock_run.call_args.kwargs
        assert "timeout" in _call_kwargs
        assert _call_kwargs["timeout"] is not None


class TestRunSubprocessFailed:
    """Error path when subprocess.run exits with returncode != 0."""

    def test_raises_clipwright_error_on_nonzero_returncode(
        self, mocker: MagicMock
    ) -> None:
        """Raises ClipwrightError(SUBPROCESS_FAILED) on non-zero exit."""
        mock_cp = CompletedProcess(
            args=["ffprobe", "-i", "missing.mp4"],
            returncode=1,
            stdout="",
            stderr="No such file or directory",
        )
        mocker.patch("subprocess.run", return_value=mock_cp)

        with pytest.raises(ClipwrightError) as exc_info:
            run(["ffprobe", "-i", "missing.mp4"])

        assert exc_info.value.code == ErrorCode.SUBPROCESS_FAILED

    def test_error_message_contains_stderr(self, mocker: MagicMock) -> None:
        """The SUBPROCESS_FAILED message includes stderr content (for debuggability)."""
        stderr_text = "ffprobe: error reading header"
        mock_cp = CompletedProcess(
            args=["ffprobe"],
            returncode=2,
            stdout="",
            stderr=stderr_text,
        )
        mocker.patch("subprocess.run", return_value=mock_cp)

        with pytest.raises(ClipwrightError) as exc_info:
            run(["ffprobe"])

        assert stderr_text in exc_info.value.message

    def test_error_has_non_empty_hint(self, mocker: MagicMock) -> None:
        """SUBPROCESS_FAILED carries a non-empty hint (§6.4 contract)."""
        mock_cp = CompletedProcess(
            args=["ffprobe"], returncode=1, stdout="", stderr="error"
        )
        mocker.patch("subprocess.run", return_value=mock_cp)

        with pytest.raises(ClipwrightError) as exc_info:
            run(["ffprobe"])

        assert len(exc_info.value.hint) > 0

    @pytest.mark.parametrize("returncode", [1, 2, 127, 255, -1])
    def test_various_nonzero_returncodes_raise_subprocess_failed(
        self, mocker: MagicMock, returncode: int
    ) -> None:
        """Any returncode other than 0 raises SUBPROCESS_FAILED."""
        mock_cp = CompletedProcess(
            args=["ffprobe"], returncode=returncode, stdout="", stderr="error"
        )
        mocker.patch("subprocess.run", return_value=mock_cp)

        with pytest.raises(ClipwrightError) as exc_info:
            run(["ffprobe"])

        assert exc_info.value.code == ErrorCode.SUBPROCESS_FAILED


class TestRunSubprocessTimeout:
    """Error path when subprocess.run raises TimeoutExpired."""

    def test_raises_clipwright_error_on_timeout(self, mocker: MagicMock) -> None:
        """Raises ClipwrightError(SUBPROCESS_TIMEOUT) on TimeoutExpired."""
        mocker.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=10.0),
        )

        with pytest.raises(ClipwrightError) as exc_info:
            run(["ffprobe"], timeout=10.0)

        assert exc_info.value.code == ErrorCode.SUBPROCESS_TIMEOUT

    def test_timeout_error_has_message_and_hint(self, mocker: MagicMock) -> None:
        """SUBPROCESS_TIMEOUT carries message and hint (§6.4 contract)."""
        mocker.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=5.0),
        )

        with pytest.raises(ClipwrightError) as exc_info:
            run(["ffprobe"], timeout=5.0)

        err = exc_info.value
        assert len(err.message) > 0
        assert len(err.hint) > 0

    def test_timeout_not_wrapped_as_subprocess_failed(self, mocker: MagicMock) -> None:
        """TimeoutExpired is raised as SUBPROCESS_TIMEOUT, not SUBPROCESS_FAILED."""
        mocker.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=10.0),
        )

        with pytest.raises(ClipwrightError) as exc_info:
            run(["ffprobe"])

        # Explicitly confirm it is not SUBPROCESS_FAILED
        assert exc_info.value.code != ErrorCode.SUBPROCESS_FAILED
        assert exc_info.value.code == ErrorCode.SUBPROCESS_TIMEOUT


# ===========================================================================
# F-05: resolve_tool — executability check for env_var path ([SR-V-001])
# ===========================================================================


class TestResolveToolEnvVarExecutability:
    """F-05 fix: a file pointed to by env_var that is not executable is rejected.

    [SR-V-001] os.path.isfile() alone cannot check execute permission.
    A non-executable file is not used as the env fallback; DEPENDENCY_MISSING is raised.

    On Windows, X_OK has limited meaning, so the test uses monkeypatch to return False
    for a cross-platform-safe verification.
    """

    def test_non_executable_env_path_raises_dependency_missing(
        self, mocker: MagicMock
    ) -> None:
        """env_var path exists but is not executable → DEPENDENCY_MISSING.

        Arrange: which=None, isfile=True, os.access(X_OK)=False
        Act: resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")
        Assert: ClipwrightError(DEPENDENCY_MISSING) is raised
        """
        # Arrange
        mocker.patch("shutil.which", return_value=None)
        mocker.patch.dict("os.environ", {"CLIPWRIGHT_FFPROBE": "/env/ffprobe"})
        mocker.patch("os.path.isfile", return_value=True)
        mocker.patch("os.access", return_value=False)

        # Act & Assert
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")

        assert exc_info.value.code == ErrorCode.DEPENDENCY_MISSING

    def test_non_executable_env_path_is_not_returned(self, mocker: MagicMock) -> None:
        """A non-executable path is not returned (an exception is raised instead).

        Arrange: which=None, isfile=True, os.access(X_OK)=False
        Act: resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")
        Assert: ClipwrightError is raised and "/env/ffprobe" is not returned
        """
        # Arrange
        mocker.patch("shutil.which", return_value=None)
        mocker.patch.dict("os.environ", {"CLIPWRIGHT_FFPROBE": "/env/ffprobe"})
        mocker.patch("os.path.isfile", return_value=True)
        mocker.patch("os.access", return_value=False)

        # Act & Assert: env_path is not returned; an exception is raised instead
        with pytest.raises(ClipwrightError):
            resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")

    def test_executable_env_path_is_returned(self, mocker: MagicMock) -> None:
        """[Green keep] An executable env path is returned (regression guard).

        Arrange: which=None, isfile=True, os.access(X_OK)=True
        Act: resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")
        Assert: env path is returned
        """
        # Arrange
        mocker.patch("shutil.which", return_value=None)
        mocker.patch.dict("os.environ", {"CLIPWRIGHT_FFPROBE": "/env/ffprobe"})
        mocker.patch("os.path.isfile", return_value=True)
        mocker.patch("os.access", return_value=True)

        # Act
        result = resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")

        # Assert
        assert result == "/env/ffprobe"

    @pytest.mark.parametrize(
        "env_path",
        [
            "/env/ffprobe",
            "/opt/local/bin/ffprobe",
            "C:\\tools\\ffprobe.exe",
        ],
    )
    def test_non_executable_check_applies_to_various_paths(
        self, mocker: MagicMock, env_path: str
    ) -> None:
        """DEPENDENCY_MISSING is raised for any non-executable path (parametrize).

        Arrange: which=None, isfile=True, os.access(X_OK)=False (each path)
        Act: resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")
        Assert: ClipwrightError(DEPENDENCY_MISSING) is raised
        """
        # Arrange
        mocker.patch("shutil.which", return_value=None)
        mocker.patch.dict("os.environ", {"CLIPWRIGHT_FFPROBE": env_path})
        mocker.patch("os.path.isfile", return_value=True)
        mocker.patch("os.access", return_value=False)

        # Act & Assert
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")

        assert exc_info.value.code == ErrorCode.DEPENDENCY_MISSING

    def test_os_access_called_with_x_ok_flag(self, mocker: MagicMock) -> None:
        """Executability is checked with os.access(path, os.X_OK).

        Arrange: which=None, isfile=True, os.access=False
        Act: resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")
        Assert: os.access is called with the X_OK flag
        """
        # Arrange
        mocker.patch("shutil.which", return_value=None)
        mocker.patch.dict("os.environ", {"CLIPWRIGHT_FFPROBE": "/env/ffprobe"})
        mocker.patch("os.path.isfile", return_value=True)
        mock_access = mocker.patch("os.access", return_value=False)

        # Act
        with pytest.raises(ClipwrightError):
            resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")

        # Assert: os.access is called with the os.X_OK flag
        mock_access.assert_called_once_with("/env/ffprobe", os.X_OK)

    def test_which_found_skips_env_executability_check(self, mocker: MagicMock) -> None:
        """[Green keep] PATH takes priority; env executability is not checked.

        Arrange: which="/usr/bin/ffprobe"
        Act: resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")
        Assert: which path is returned and os.access is not called
        """
        # Arrange
        mocker.patch("shutil.which", return_value="/usr/bin/ffprobe")
        mocker.patch.dict("os.environ", {"CLIPWRIGHT_FFPROBE": "/env/ffprobe"})
        mock_access = mocker.patch("os.access", return_value=False)

        # Act
        result = resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")

        # Assert
        assert result == "/usr/bin/ffprobe"
        mock_access.assert_not_called()


class TestRunShellFalseInvariant:
    """Verify shell=False and argument array invariant in additional scenarios.

    Core verification of command injection prevention (CWE-78 / §6.5).
    """

    def test_shell_is_false_regardless_of_cmd_content(self, mocker: MagicMock) -> None:
        """shell=False is maintained even with space-containing arguments."""
        mock_cp = CompletedProcess(
            args=["ffprobe", "-i", "path with spaces/video.mp4"],
            returncode=0,
            stdout="",
            stderr="",
        )
        mock_run = mocker.patch("subprocess.run", return_value=mock_cp)

        run(["ffprobe", "-i", "path with spaces/video.mp4"])

        assert mock_run.call_args.kwargs.get("shell") is False

    def test_cmd_elements_are_not_joined_as_string(self, mocker: MagicMock) -> None:
        """subprocess.run receives a list, not a joined string."""
        mock_cp = CompletedProcess(
            args=["ffprobe", "-v", "quiet"],
            returncode=0,
            stdout="",
            stderr="",
        )
        mock_run = mocker.patch("subprocess.run", return_value=mock_cp)

        run(["ffprobe", "-v", "quiet"])

        first_arg = mock_run.call_args.args[0]
        # Must not be a joined string — must still be a list
        assert not isinstance(first_arg, str)
        assert isinstance(first_arg, list)


# ===========================================================================
# A-1: UTF-8 encoding in subprocess.run() kwargs (category A, ADR-1)
# ===========================================================================


class TestRunUTF8EncodingKwargs:
    """Verify that run() passes encoding="utf-8" and errors="replace"
    to subprocess.run().

    Category A: core subprocess output decode (cp932 デコード対策).
    This test ensures that stdout/stderr from external tools (ffmpeg, ffprobe,
    whisper, vad_cli) are decoded as UTF-8, not as the host locale (cp932 on
    JP Windows). The errors="replace" flag makes decoding tolerant of invalid
    bytes (external tool output may be garbled).

    [ADR-1] subprocess.run(..., encoding="utf-8", errors="replace")
    """

    def test_subprocess_run_called_with_encoding_utf8(self, mocker: MagicMock) -> None:
        """subprocess.run is called with encoding='utf-8' keyword argument.

        Arrange: Mock subprocess.run to return success.
        Act: Call run(["echo", "test"]).
        Assert: subprocess.run was called with encoding="utf-8" in kwargs.
        """
        mock_cp = CompletedProcess(
            args=["echo", "test"], returncode=0, stdout="test", stderr=""
        )
        mock_run = mocker.patch("subprocess.run", return_value=mock_cp)

        run(["echo", "test"])

        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs.get("encoding") == "utf-8", (
            "subprocess.run must be called with encoding='utf-8' "
            "(ADR-1: cp932 デコード対策)"
        )

    def test_subprocess_run_called_with_errors_replace(self, mocker: MagicMock) -> None:
        """subprocess.run is called with errors='replace' keyword argument.

        Arrange: Mock subprocess.run to return success.
        Act: Call run(["echo", "test"]).
        Assert: subprocess.run was called with errors="replace" in kwargs.
        """
        mock_cp = CompletedProcess(
            args=["echo", "test"], returncode=0, stdout="test", stderr=""
        )
        mock_run = mocker.patch("subprocess.run", return_value=mock_cp)

        run(["echo", "test"])

        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs.get("errors") == "replace", (
            "subprocess.run must be called with errors='replace' "
            "(ADR-1: invalid bytes are replaced, not raising UnicodeDecodeError)"
        )


# ===========================================================================
# A-2: UTF-8 stdout round-trip with Japanese text (category A, positive guard)
# ===========================================================================


class TestRunUTF8StdoutRoundTrip:
    """Verify that run() successfully decodes UTF-8 stdout with Japanese text.

    Category A: core subprocess output decode.
    This test spawns a real child process that writes UTF-8 Japanese to stdout,
    ensuring run() does not raise UnicodeDecodeError and returns the text intact.

    Success condition: Japanese text in stdout is correctly decoded.
    """

    def test_run_decodes_utf8_japanese_stdout_without_error(self) -> None:
        """run() successfully decodes UTF-8 Japanese characters from stdout.

        Arrange: Prepare a child command that outputs UTF-8 Japanese.
        Act: Call run() with the command.
        Assert: stdout is decoded correctly and contains the Japanese text.
        """
        # Arrange: Use sys.executable to spawn a child that outputs UTF-8 Japanese.
        cmd = [
            os.environ.get("PYTHON", "python"),
            "-c",
            (
                "import sys; sys.stdout.buffer.write('日本語'.encode('utf-8')); "
                "sys.stdout.flush()"
            ),
        ]

        # Act: This should not raise UnicodeDecodeError even on cp932 locale.
        result = run(cmd, timeout=10.0)

        # Assert: The Japanese text is preserved in stdout.
        assert "日本語" in result.stdout, (
            "UTF-8 Japanese text must be correctly decoded from stdout "
            "(ADR-1: encoding='utf-8')"
        )
        assert result.returncode == 0


# ===========================================================================
# A-3: stderr round-trip with Japanese + non-zero exit (category A, DC-GP-003)
# ===========================================================================


class TestRunUTF8StderrRegression:
    """Verify stderr_summary preserves UTF-8 Japanese text in error messages.

    Category A: core subprocess output decode (DC-GP-003: stderr mojibake fix).
    This test reproduces the actual reported bug: a child process writes UTF-8
    Japanese to stderr AND exits non-zero. The run() function collects stderr
    and includes a summary in the error message. Before the fix, this stderr
    summary would suffer from cp932 decode errors or mojibake. After the fix,
    it must preserve Japanese text.

    Success condition: ClipwrightError.message includes the Japanese text from
    stderr without raising UnicodeDecodeError.
    """

    def test_run_preserves_utf8_japanese_in_stderr_on_failure(self) -> None:
        """run() preserves UTF-8 Japanese in error message when child fails.

        Arrange: Child command that writes UTF-8 Japanese to stderr and exits non-zero.
        Act: Call run() (which will raise ClipwrightError(SUBPROCESS_FAILED)).
        Assert: The error message contains the Japanese text from stderr.

        This guards against the reported bug (DC-GP-003) where stderr_summary
        was mojibake or raised UnicodeDecodeError on JP Windows (cp932 locale).
        """
        # Arrange: Child writes UTF-8 Japanese to stderr and exits with code 1.
        cmd = [
            os.environ.get("PYTHON", "python"),
            "-c",
            (
                "import sys; sys.stderr.buffer.write('失敗の理由'.encode('utf-8')); "
                "sys.stderr.flush(); sys.exit(1)"
            ),
        ]

        # Act: This should raise ClipwrightError with the message containing stderr.
        with pytest.raises(ClipwrightError) as exc_info:
            run(cmd, timeout=10.0)

        # Assert: The error code is SUBPROCESS_FAILED.
        assert exc_info.value.code == ErrorCode.SUBPROCESS_FAILED

        # Assert: The error message includes the Japanese text from stderr.
        error_msg = exc_info.value.message
        assert "失敗の理由" in error_msg, (
            "stderr_summary must preserve UTF-8 Japanese text in error message "
            "(ADR-1: encoding='utf-8', DC-GP-003: mojibake fix)"
        )
