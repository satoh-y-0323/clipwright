"""test_process.py — process.py のサブプロセスランナー単体テスト（Red フェーズ）。

対象:
- resolve_tool: PATH 優先（shutil.which）→ env フォールバック → 見つからなければ
  ClipwrightError(DEPENDENCY_MISSING)
- run(cmd, timeout): 成功 / 非ゼロ終了 → SUBPROCESS_FAILED /
  TimeoutExpired → SUBPROCESS_TIMEOUT
- shell=False・引数配列で subprocess が呼ばれることを検証

[RED] process.py は未実装のため ImportError で失敗する。
"""

from __future__ import annotations

import os
import subprocess
from subprocess import CompletedProcess
from unittest.mock import MagicMock

import pytest

# --- Import（process.py 未実装のため ImportError が発生する → Red） ---
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.process import resolve_tool, run

# ===========================================================================
# resolve_tool — PATH 優先 → env フォールバック → DEPENDENCY_MISSING
# ===========================================================================


class TestResolveToolPathPriority:
    """shutil.which で見つかれば env_var を無視して PATH のパスを返す。"""

    def test_returns_which_path_when_found(self, mocker: MagicMock) -> None:
        """shutil.which が返すパスをそのまま返す（PATH 最優先）。"""
        mocker.patch("shutil.which", return_value="/usr/bin/ffprobe")
        mocker.patch.dict("os.environ", {"CLIPWRIGHT_FFPROBE": "/env/ffprobe"})

        result = resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")

        assert result == "/usr/bin/ffprobe"

    def test_which_is_called_with_tool_name(self, mocker: MagicMock) -> None:
        """shutil.which がツール名で呼ばれることを確認する。"""
        mock_which = mocker.patch("shutil.which", return_value="/usr/bin/ffprobe")

        resolve_tool("ffprobe")

        mock_which.assert_called_once_with("ffprobe")

    def test_env_var_not_used_when_which_finds_tool(self, mocker: MagicMock) -> None:
        """PATH で見つかった場合、env 変数のファイル存在確認は不要（呼ばれない）。"""
        mocker.patch("shutil.which", return_value="/usr/bin/ffprobe")
        # os.path.isfile が呼ばれていないことを確認する
        mock_isfile = mocker.patch("os.path.isfile")

        resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")

        mock_isfile.assert_not_called()


class TestResolveToolEnvFallback:
    """shutil.which が None のとき、env_var のパスを使う。"""

    def test_returns_env_var_path_when_which_returns_none(
        self, mocker: MagicMock
    ) -> None:
        """env 変数に有効なパスがあれば、それを返す。"""
        mocker.patch("shutil.which", return_value=None)
        mocker.patch.dict("os.environ", {"CLIPWRIGHT_FFPROBE": "/env/ffprobe.exe"})
        mocker.patch("os.path.isfile", return_value=True)

        result = resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")

        assert result == "/env/ffprobe.exe"

    def test_env_var_path_must_be_file(self, mocker: MagicMock) -> None:
        """env 変数のパスが存在しないファイルの場合は DEPENDENCY_MISSING。"""
        mocker.patch("shutil.which", return_value=None)
        mocker.patch.dict("os.environ", {"CLIPWRIGHT_FFPROBE": "/nonexistent/ffprobe"})
        mocker.patch("os.path.isfile", return_value=False)

        with pytest.raises(ClipwrightError) as exc_info:
            resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")

        assert exc_info.value.code == ErrorCode.DEPENDENCY_MISSING

    def test_env_var_not_set_falls_through_to_missing(self, mocker: MagicMock) -> None:
        """env 変数が未設定かつ which が None → DEPENDENCY_MISSING。"""
        mocker.patch("shutil.which", return_value=None)
        mocker.patch.dict("os.environ", {}, clear=True)

        with pytest.raises(ClipwrightError) as exc_info:
            resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")

        assert exc_info.value.code == ErrorCode.DEPENDENCY_MISSING

    def test_without_env_var_argument_uses_only_which(self, mocker: MagicMock) -> None:
        """env_var 引数を省略した場合、which で見つからなければ DEPENDENCY_MISSING。"""
        mocker.patch("shutil.which", return_value=None)

        with pytest.raises(ClipwrightError) as exc_info:
            resolve_tool("ffprobe")

        assert exc_info.value.code == ErrorCode.DEPENDENCY_MISSING


class TestResolveToolDependencyMissingError:
    """DEPENDENCY_MISSING 時のエラー内容を検証する。"""

    def test_error_has_message_and_hint(self, mocker: MagicMock) -> None:
        """ClipwrightError に message と hint が含まれる（§6.4 規約）。"""
        mocker.patch("shutil.which", return_value=None)
        mocker.patch.dict("os.environ", {}, clear=True)

        with pytest.raises(ClipwrightError) as exc_info:
            resolve_tool("ffprobe")

        err = exc_info.value
        assert len(err.message) > 0
        assert len(err.hint) > 0

    def test_error_message_contains_tool_name(self, mocker: MagicMock) -> None:
        """message にツール名が含まれる（デバッグ容易性）。"""
        mocker.patch("shutil.which", return_value=None)
        mocker.patch.dict("os.environ", {}, clear=True)

        with pytest.raises(ClipwrightError) as exc_info:
            resolve_tool("ffprobe")

        assert "ffprobe" in exc_info.value.message


# ===========================================================================
# run — 成功 / 非ゼロ終了 / タイムアウト
# ===========================================================================


class TestRunSuccess:
    """subprocess.run が returncode=0 で終了した場合の正常系。"""

    def test_returns_completed_process_on_success(self, mocker: MagicMock) -> None:
        """成功時に CompletedProcess を返す。"""
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
        """subprocess.run が shell=False で呼ばれることを検証する（規約6.5）。"""
        mock_cp = CompletedProcess(
            args=["ffprobe", "-version"], returncode=0, stdout="", stderr=""
        )
        mock_run = mocker.patch("subprocess.run", return_value=mock_cp)

        run(["ffprobe", "-version"])

        _call_kwargs = mock_run.call_args.kwargs
        assert _call_kwargs.get("shell") is False

    def test_subprocess_called_with_list_cmd(self, mocker: MagicMock) -> None:
        """subprocess.run の第1引数が list（引数配列）で渡されることを検証する。"""
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
        """capture_output=True で呼ばれる（stdout/stderr を捕捉）。"""
        mock_cp = CompletedProcess(args=["ffprobe"], returncode=0, stdout="", stderr="")
        mock_run = mocker.patch("subprocess.run", return_value=mock_cp)

        run(["ffprobe"])

        _call_kwargs = mock_run.call_args.kwargs
        assert _call_kwargs.get("capture_output") is True

    def test_subprocess_called_with_text_true(self, mocker: MagicMock) -> None:
        """text=True で呼ばれる（stdout/stderr が str として返る）。"""
        mock_cp = CompletedProcess(args=["ffprobe"], returncode=0, stdout="", stderr="")
        mock_run = mocker.patch("subprocess.run", return_value=mock_cp)

        run(["ffprobe"])

        _call_kwargs = mock_run.call_args.kwargs
        assert _call_kwargs.get("text") is True

    def test_timeout_is_passed_to_subprocess(self, mocker: MagicMock) -> None:
        """timeout 引数が subprocess.run に渡される。"""
        mock_cp = CompletedProcess(args=["ffprobe"], returncode=0, stdout="", stderr="")
        mock_run = mocker.patch("subprocess.run", return_value=mock_cp)

        run(["ffprobe"], timeout=30.0)

        _call_kwargs = mock_run.call_args.kwargs
        assert _call_kwargs.get("timeout") == 30.0

    def test_default_timeout_is_set(self, mocker: MagicMock) -> None:
        """timeout 引数を省略しても何らかのデフォルト timeout が
        設定される（無制限禁止）。"""
        mock_cp = CompletedProcess(args=["ffprobe"], returncode=0, stdout="", stderr="")
        mock_run = mocker.patch("subprocess.run", return_value=mock_cp)

        run(["ffprobe"])

        _call_kwargs = mock_run.call_args.kwargs
        assert "timeout" in _call_kwargs
        assert _call_kwargs["timeout"] is not None


class TestRunSubprocessFailed:
    """subprocess.run が returncode != 0 で終了した場合の異常系。"""

    def test_raises_clipwright_error_on_nonzero_returncode(
        self, mocker: MagicMock
    ) -> None:
        """非ゼロ終了時に ClipwrightError(SUBPROCESS_FAILED) を送出する。"""
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
        """SUBPROCESS_FAILED の message に stderr 内容が含まれる（デバッグ容易性）。"""
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
        """SUBPROCESS_FAILED のエラーは空でない hint を持つ（§6.4 規約）。"""
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
        """returncode=0 以外はすべて SUBPROCESS_FAILED。"""
        mock_cp = CompletedProcess(
            args=["ffprobe"], returncode=returncode, stdout="", stderr="error"
        )
        mocker.patch("subprocess.run", return_value=mock_cp)

        with pytest.raises(ClipwrightError) as exc_info:
            run(["ffprobe"])

        assert exc_info.value.code == ErrorCode.SUBPROCESS_FAILED


class TestRunSubprocessTimeout:
    """subprocess.run が TimeoutExpired を送出した場合の異常系。"""

    def test_raises_clipwright_error_on_timeout(self, mocker: MagicMock) -> None:
        """TimeoutExpired 時に ClipwrightError(SUBPROCESS_TIMEOUT) を送出する。"""
        mocker.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=10.0),
        )

        with pytest.raises(ClipwrightError) as exc_info:
            run(["ffprobe"], timeout=10.0)

        assert exc_info.value.code == ErrorCode.SUBPROCESS_TIMEOUT

    def test_timeout_error_has_message_and_hint(self, mocker: MagicMock) -> None:
        """SUBPROCESS_TIMEOUT のエラーは message と hint を持つ（§6.4 規約）。"""
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
        """TimeoutExpired は SUBPROCESS_FAILED ではなく
        SUBPROCESS_TIMEOUT として送出される。"""
        mocker.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=10.0),
        )

        with pytest.raises(ClipwrightError) as exc_info:
            run(["ffprobe"])

        # SUBPROCESS_FAILED ではないことを明示的に確認
        assert exc_info.value.code != ErrorCode.SUBPROCESS_FAILED
        assert exc_info.value.code == ErrorCode.SUBPROCESS_TIMEOUT


# ===========================================================================
# F-05: resolve_tool — env_var パスの実行可能性検証（[SR-V-001]）
# ===========================================================================


class TestResolveToolEnvVarExecutability:
    """F-05 fix: env_var が指すファイルが存在しても実行不可なら採用しない。

    [SR-V-001] os.path.isfile() のみでは実行権限を確認できない。
    実行不可ファイルは env フォールバックとして使用せず DEPENDENCY_MISSING へ。

    Windows では X_OK の意味が薄いため、os.access を monkeypatch して
    False を返すクロスプラットフォーム安全なテストで検証する。
    """

    def test_non_executable_env_path_raises_dependency_missing(
        self, mocker: MagicMock
    ) -> None:
        """[Red] env_var パスが存在するが実行不可 → DEPENDENCY_MISSING。

        Arrange: which が None, isfile が True, os.access(X_OK) が False
        Act: resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")
        Assert: ClipwrightError(DEPENDENCY_MISSING) が送出される
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
        """[Red] 実行不可パスは resolve_tool が返さない（例外が上がる）。

        Arrange: which が None, isfile が True, os.access(X_OK) が False
        Act: resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")
        Assert: ClipwrightError が上がり "/env/ffprobe" は返されない
        """
        # Arrange
        mocker.patch("shutil.which", return_value=None)
        mocker.patch.dict("os.environ", {"CLIPWRIGHT_FFPROBE": "/env/ffprobe"})
        mocker.patch("os.path.isfile", return_value=True)
        mocker.patch("os.access", return_value=False)

        # Act & Assert: 例外が上がることで戻り値が env_path でないことを確認
        with pytest.raises(ClipwrightError):
            resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")

    def test_executable_env_path_is_returned(self, mocker: MagicMock) -> None:
        """[Green 維持] 実行可能なら env パスを返す（既存挙動のリグレッション防止）。

        Arrange: which が None, isfile が True, os.access(X_OK) が True
        Act: resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")
        Assert: env パスが返される
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
        """[Red] パスに依存せず実行不可なら DEPENDENCY_MISSING（parametrize）。

        Arrange: which が None, isfile が True, os.access(X_OK) が False（各パス）
        Act: resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")
        Assert: ClipwrightError(DEPENDENCY_MISSING) が送出される
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
        """[Red] 実行可能性確認に os.access(path, os.X_OK) が使われること。

        Arrange: which が None, isfile が True, os.access が False
        Act: resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")
        Assert: os.access が X_OK フラグで呼ばれている
        """
        # Arrange
        mocker.patch("shutil.which", return_value=None)
        mocker.patch.dict("os.environ", {"CLIPWRIGHT_FFPROBE": "/env/ffprobe"})
        mocker.patch("os.path.isfile", return_value=True)
        mock_access = mocker.patch("os.access", return_value=False)

        # Act
        with pytest.raises(ClipwrightError):
            resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")

        # Assert: os.access が os.X_OK フラグで呼ばれていること
        mock_access.assert_called_once_with("/env/ffprobe", os.X_OK)

    def test_which_found_skips_env_executability_check(self, mocker: MagicMock) -> None:
        """[Green 維持] PATH で見つかれば env 実行可能性を確認しない（PATH 優先）。

        Arrange: which が "/usr/bin/ffprobe" を返す
        Act: resolve_tool("ffprobe", env_var="CLIPWRIGHT_FFPROBE")
        Assert: which のパスが返り、os.access は呼ばれない
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
    """shell=False・引数配列実行の不変条件を追加シナリオで確認する。

    コマンドインジェクション対策（CWE-78 / 規約6.5）の中核検証。
    """

    def test_shell_is_false_regardless_of_cmd_content(self, mocker: MagicMock) -> None:
        """スペース含みの引数でも shell=False が維持される。"""
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
        """subprocess.run に渡るのは join された文字列ではなくリスト。"""
        mock_cp = CompletedProcess(
            args=["ffprobe", "-v", "quiet"],
            returncode=0,
            stdout="",
            stderr="",
        )
        mock_run = mocker.patch("subprocess.run", return_value=mock_cp)

        run(["ffprobe", "-v", "quiet"])

        first_arg = mock_run.call_args.args[0]
        # 文字列に結合されていない（リストのまま）
        assert not isinstance(first_arg, str)
        assert isinstance(first_arg, list)
