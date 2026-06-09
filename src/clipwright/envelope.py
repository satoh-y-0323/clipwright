"""envelope.py — 返り値エンベロープ生成ヘルパー。

全ツールの返り値を統一形式（§6.3 / §6.4）に整える薄いヘルパー。
ToolResult / ToolErrorResult の dict 表現を返すため、
FastMCP の JSON シリアライズと直接互換する。
"""

from __future__ import annotations

from typing import Any


def ok_result(
    summary: str,
    *,
    data: dict[str, Any] | None = None,
    artifacts: list[Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """成功エンベロープ dict を構築する（§6.3 ToolResult 形）。

    summary には AI が次の一手を判断できる要点（件数・尺・最大値等）を含める。
    "最小限"にしない。

    Args:
        summary: 処理結果の要点（必須）。
        data: 付帯情報（任意）。
        artifacts: 出力ファイルへの参照リスト（任意）。
        warnings: 警告メッセージリスト（任意）。

    Returns:
        { ok: True, summary, data, artifacts, warnings } の dict。
    """
    return {
        "ok": True,
        "summary": summary,
        "data": data if data is not None else {},
        "artifacts": artifacts if artifacts is not None else [],
        "warnings": warnings if warnings is not None else [],
    }


def error_result(code: str, message: str, hint: str) -> dict[str, Any]:
    """失敗エンベロープ dict を構築する（§6.4 ToolErrorResult 形）。

    message には何が起きたかを、hint には次の一手（具体的な解決策）を記す。
    hint が空では規約違反（§6 エラー規約）。

    Args:
        code: ErrorCode 値の文字列表現。
        message: 何が起きたか。
        hint: 次の一手（具体的・アクション可能な内容）。

    Returns:
        { ok: False, error: { code, message, hint } } の dict。
    """
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "hint": hint,
        },
    }
