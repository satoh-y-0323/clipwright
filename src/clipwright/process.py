"""process.py — サブプロセスランナー。

外部ツールの探索（resolve_tool）と実行（run）を担う。
shell=False・引数配列でのみ実行し、コマンドインジェクションを防ぐ（CWE-78 / 規約6.5）。
subprocess を直接呼び出す唯一のモジュールとすることで、規律の一元管理を実現する。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from subprocess import CompletedProcess

from clipwright.errors import ClipwrightError, ErrorCode

_INSTALL_HINT = "Windows では `winget install Gyan.FFmpeg` 等で導入してください。"


def resolve_tool(name: str, env_var: str | None = None) -> str:
    """外部ツールの実行ファイルパスを解決して返す。

    解決順: PATH（shutil.which）→ env_var 環境変数のパス → DEPENDENCY_MISSING。
    env_var で指定したパスはファイルとして存在し、かつ実行可能である必要がある。
    実行可能性は os.access(path, os.X_OK) で確認する。これにより、
    ファイルが存在しても実行権限がない場合に subprocess が Permission Denied で
    失敗するリスクを事前に防ぐ（[SR-V-001] F-05 対応）。
    実行不可の場合は env を採用せず DEPENDENCY_MISSING へフォールバックする。

    Args:
        name: ツール名（例: "ffprobe"）。
        env_var: フォールバックとして参照する環境変数名（例: "CLIPWRIGHT_FFPROBE"）。

    Returns:
        解決されたツールの実行ファイルパス。

    Raises:
        ClipwrightError: ツールが見つからない場合（DEPENDENCY_MISSING）。
    """
    # 1. PATH から探す（最優先）
    which_path = shutil.which(name)
    if which_path is not None:
        return which_path

    # 2. env_var で指定された環境変数のパスを使う
    if env_var is not None:
        env_path = os.environ.get(env_var)
        if env_path is not None:
            if os.path.isfile(env_path) and os.access(env_path, os.X_OK):
                return env_path
            # 環境変数は設定されているがファイルが存在しないか実行不可
            raise ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message=(
                    f"{name} が見つかりません"
                    f"（{env_var}={env_path} は存在しないか実行不可なファイルです）"
                ),
                hint=(
                    f"{env_var} に有効な実行可能ファイルのフルパスを設定するか、"
                    f"{name} を PATH の通ったディレクトリに配置してください。"
                    + _INSTALL_HINT
                ),
            )

    # 3. どちらでも見つからない
    raise ClipwrightError(
        code=ErrorCode.DEPENDENCY_MISSING,
        message=f"{name} が PATH 上に見つかりません",
        hint=(
            f"{name} を PATH の通ったディレクトリに配置するか、"
            "環境変数に実行ファイルのフルパスを設定してください。"
            + _INSTALL_HINT
        ),
    )


def run(
    cmd: list[str],
    *,
    timeout: float = 60.0,
    cwd: str | None = None,
) -> CompletedProcess[str]:
    """外部コマンドを安全に実行して CompletedProcess を返す。

    shell=False・引数配列で実行する（コマンドインジェクション対策）。
    timeout / stderr 収集 / 終了コード検査を必ず行う（規約6.5）。

    Args:
        cmd: 実行コマンドと引数のリスト。文字列結合ではなく配列で渡す。
        timeout: タイムアウト秒数（デフォルト60秒）。
        cwd: 作業ディレクトリ。None の場合は現在のディレクトリ。

    Returns:
        subprocess.CompletedProcess（returncode=0 の場合のみ返す）。

    Raises:
        ClipwrightError: 非ゼロ終了時（SUBPROCESS_FAILED）またはタイムアウト時
            （SUBPROCESS_TIMEOUT）。
    """
    try:
        result = subprocess.run(
            cmd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired as exc:
        tool = cmd[0] if cmd else ""
        raise ClipwrightError(
            code=ErrorCode.SUBPROCESS_TIMEOUT,
            message=f"コマンドが {exc.timeout} 秒でタイムアウトしました: {tool}",
            hint="timeout 値を大きくするか、入力ファイルのサイズを確認してください。",
        ) from exc

    if result.returncode != 0:
        # stderr は先頭 200 文字・改行除去の要約に絞る（詳細パス情報の漏洩防止）
        stderr_summary = result.stderr[:200].replace("\n", " ").strip()
        raise ClipwrightError(
            code=ErrorCode.SUBPROCESS_FAILED,
            message=(
                f"コマンドが終了コード {result.returncode} で失敗しました:"
                f" {stderr_summary}"
            ),
            hint="コマンドの引数・入力ファイルパス・ツールのバージョンを確認してください。",
        )

    return result
