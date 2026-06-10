"""wrap_cli.py — BudouX 文節分割の別プロセス小 CLI。

MCP サーバープロセスから import されない（§2.4 subprocess 疎結合）。
wrap.py が sys.executable -m clipwright_wrap.wrap_cli として別プロセス起動する。

CLI 契約（WR-AD-02）:
  - stdin: JSON {"language": "ja", "texts": ["cue1", ...]}
  - stdout: JSON {"segments": [["文節1", "文節2", ...], ...]}
  - エラー時 stdout: {"error": {"code": str, "message": str, "hint": str}}
  - main() は全例外をトップレベルで捕捉し、必ず stdout JSON を出して return 0。
  - stdout は JSON のみ。ログ・進捗は stderr へ。
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from clipwright.errors import ErrorCode

# pip install ヒント文字列
_WRAP_INSTALL_HINT = (
    "`pip install clipwright-wrap` で clipwright-wrap を導入してください。"
)

# language → parser ロード関数のマッピング（DC-AS-002: テスト monkeypatch のターゲット）
# budoux はモジュールトップレベルで import する。本 CLI は別プロセスで起動されるため
# サーバープロセスへの漏洩リスクはなく、_PARSER_LOADERS をモジュール定数として
# expose する必要がある（テストが直接参照する）。
# budoux が未インストールの場合は空辞書のまま（main() で DEPENDENCY_MISSING を返す）。
try:
    import budoux as _budoux

    _PARSER_LOADERS: dict[str, Any] = {
        "ja": _budoux.load_default_japanese_parser,
        "zh-hans": _budoux.load_default_simplified_chinese_parser,
        "zh-hant": _budoux.load_default_traditional_chinese_parser,
        "th": _budoux.load_default_thai_parser,
    }
except ImportError:
    _PARSER_LOADERS = {}


def _error_output(code: str, message: str, hint: str) -> None:
    """エラー JSON を stdout に出力する。

    呼び出し元でパス情報をサニタイズしてから渡すこと。
    """
    result: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "hint": hint,
        }
    }
    print(json.dumps(result, ensure_ascii=False), file=sys.stdout)


def main(argv: list[str] | None = None) -> int:  # noqa: ARG001
    """wrap_cli エントリポイント。

    全例外をトップレベルで捕捉し、stdout に JSON を出力して return 0（WR-AD-02）。

    Args:
        argv: コマンドライン引数リスト（現バージョンでは未使用）。

    Returns:
        終了コード（常に 0）。
    """
    try:
        # --- stdin から JSON を読み込む ---
        try:
            raw = sys.stdin.read()
            payload: dict[str, Any] = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            _error_output(
                code=str(ErrorCode.INVALID_INPUT),
                message="stdin の JSON パースに失敗しました",
                hint="stdin に有効な JSON オブジェクトを渡してください。",
            )
            return 0

        # --- 入力バリデーション ---
        if "language" not in payload:
            _error_output(
                code=str(ErrorCode.INVALID_INPUT),
                message="'language' キーがありません",
                hint="stdin JSON に 'language' キーを含めてください。",
            )
            return 0

        if "texts" not in payload:
            _error_output(
                code=str(ErrorCode.INVALID_INPUT),
                message="'texts' キーがありません",
                hint="stdin JSON に 'texts' キーを含めてください。",
            )
            return 0

        language: str = payload["language"]
        texts = payload["texts"]

        if not isinstance(texts, list):
            _error_output(
                code=str(ErrorCode.INVALID_INPUT),
                message="'texts' はリストである必要があります",
                hint="stdin JSON の 'texts' を文字列のリストにしてください。",
            )
            return 0

        # --- parser ロード関数を取得（DC-AS-002: texts ループの外で 1 回のみ）---
        if language not in _PARSER_LOADERS:
            _error_output(
                code=str(ErrorCode.INVALID_INPUT),
                message=f"対応していない language: {language!r}",
                hint=(
                    f"language は {list(_PARSER_LOADERS.keys())}"
                    " のいずれかを指定してください。"
                ),
            )
            return 0

        # parser は texts ループの外で 1 回だけロードする（DC-AS-002）
        # ローダー呼び出し時の ImportError は DEPENDENCY_MISSING として返す
        try:
            parser = _PARSER_LOADERS[language]()
        except ImportError:
            # SR L-2: str(exc) には内部パスが含まれうるため固定文言を使用する
            _error_output(
                code=str(ErrorCode.DEPENDENCY_MISSING),
                message="budoux のインポートに失敗しました",
                hint=_WRAP_INSTALL_HINT,
            )
            return 0

        # --- 各 cue テキストを文節分割する ---
        segments: list[list[str]] = []
        for text in texts:
            seg: list[str] = parser.parse(text)
            segments.append(seg)

        result: dict[str, Any] = {"segments": segments}
        print(json.dumps(result, ensure_ascii=False), file=sys.stdout)
        return 0

    except Exception:
        # 想定外の例外もすべて捕捉して error JSON を返す（WR-AD-02）
        # SR NF-L-1: str(exc) に内部パスが含まれうるため固定文言を使用する。
        # デバッグ詳細は stderr 限定・stdout JSON には漏洩させない。
        traceback.print_exc(file=sys.stderr)
        _error_output(
            code=str(ErrorCode.INTERNAL),
            message="wrap_cli で予期しないエラーが発生しました",
            hint="再現条件を添えて報告してください。",
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
