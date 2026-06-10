"""__TOOL___cli.py — 外部 OSS を包む別プロセス小 CLI（M4・参考実装）。

OSS を使わない純 Python ツールではこのファイルは不要なので削除してよい。

このモジュールは MCP サーバープロセスから import されない（ライセンス独立・M4）。
__TOOL__.py が sys.executable -m clipwright___TOOL__.__TOOL___cli で別プロセス起動する。

CLI 契約:
  - stdin: JSON（このツールの入力ペイロード）
  - stdout: JSON（成功結果）
  - エラー時 stdout: {"error": {"code": str, "message": str, "hint": str}}
  - main() は全例外をトップレベルで捕捉し、必ず stdout JSON を出して return 0。
  - stdout は JSON のみ。ログ・進捗・トレースは stderr へ（秘密漏洩防止・CWE-209）。
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from clipwright.errors import ErrorCode

# OSS 導入ヒント。__TOOL__ を実 OSS パッケージ名に合わせて書き換える。
_INSTALL_HINT = "`pip install <your-oss>` で依存をインストールしてください。"

# 外部 OSS はモジュールトップで import する（別プロセスなのでサーバーへ漏洩しない）。
# 未インストールなら _OSS を None にし、main() で DEPENDENCY_MISSING を返す。
try:
    # import your_oss as _oss  # noqa: ERA001  TODO: 実 OSS に置き換える
    _OSS: Any = object()  # 雛形ではダミー。実装時は import 結果を入れる。
except ImportError:  # pragma: no cover
    _OSS = None


def _error_output(code: str, message: str, hint: str) -> None:
    """エラー JSON を stdout に出力する。message/hint はサニタイズ済みで渡すこと。"""
    result: dict[str, Any] = {"error": {"code": code, "message": message, "hint": hint}}
    print(json.dumps(result, ensure_ascii=False), file=sys.stdout)


def main(argv: list[str] | None = None) -> int:  # noqa: ARG001
    """CLI エントリポイント。全例外を捕捉し stdout JSON を出して return 0。"""
    try:
        try:
            payload: dict[str, Any] = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, ValueError):
            _error_output(
                code=str(ErrorCode.INVALID_INPUT),
                message="stdin の JSON パースに失敗しました",
                hint="stdin に有効な JSON オブジェクトを渡してください。",
            )
            return 0

        if _OSS is None:
            _error_output(
                code=str(ErrorCode.DEPENDENCY_MISSING),
                message="必要な OSS がインストールされていません",
                hint=_INSTALL_HINT,
            )
            return 0

        # TODO: payload を使って OSS を呼び、結果を組み立てる。
        result: dict[str, Any] = {"result": payload}
        print(json.dumps(result, ensure_ascii=False), file=sys.stdout)
        return 0

    except Exception:
        # 想定外の例外もすべて捕捉。str(exc) に内部パスが含まれうるため固定文言。
        traceback.print_exc(file=sys.stderr)
        _error_output(
            code=str(ErrorCode.INTERNAL),
            message="CLI シムで予期しないエラーが発生しました",
            hint="再現条件を添えて報告してください。",
        )
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
