"""test_envelope.py — envelope.py の契約面テスト（Red フェーズ）。

対象:
- ok_result: ToolResult 形 dict を返す（§4 返り値エンベロープ）
- error_result: { ok: False, error: { code, message, hint } } 形 dict を返す（§4）

このテストは envelope.py が未実装のため ImportError で失敗する（Red）。
"""

from __future__ import annotations

import pytest

# --- Import（envelope.py 未実装のため ImportError が発生する → Red） ---
from clipwright.envelope import error_result, ok_result


# ===========================================================================
# ok_result
# ===========================================================================


class TestOkResult:
    """ok_result が ToolResult 形 dict を返すことを確認する。"""

    def test_returns_ok_true(self) -> None:
        """ok キーが True。"""
        result = ok_result("処理が完了しました")
        assert result["ok"] is True

    def test_summary_is_set(self) -> None:
        """summary に渡した文字列が格納される。"""
        result = ok_result("3 件のクリップを処理しました")
        assert result["summary"] == "3 件のクリップを処理しました"

    def test_defaults_are_empty(self) -> None:
        """data / artifacts / warnings は未指定時に空のデフォルト値。"""
        result = ok_result("ok")
        assert result["data"] == {} or result.get("data") is not None
        assert result["artifacts"] == [] or result.get("artifacts") is not None
        assert result["warnings"] == [] or result.get("warnings") is not None

    def test_data_is_included(self) -> None:
        """data 引数を渡すと結果に含まれる。"""
        result = ok_result("ok", data={"clip_count": 5, "duration": 30.0})
        assert result["data"]["clip_count"] == 5
        assert result["data"]["duration"] == 30.0

    def test_artifacts_is_included(self) -> None:
        """artifacts 引数を渡すと結果に含まれる。"""
        art = {"role": "timeline", "path": "/out/t.otio", "format": "otio"}
        result = ok_result("ok", artifacts=[art])
        assert len(result["artifacts"]) == 1
        assert result["artifacts"][0]["role"] == "timeline"

    def test_warnings_is_included(self) -> None:
        """warnings 引数を渡すと結果に含まれる。"""
        result = ok_result("ok", warnings=["VFR 映像が含まれています"])
        assert "VFR 映像が含まれています" in result["warnings"]

    def test_all_fields_present(self) -> None:
        """返り値に ok / summary / data / artifacts / warnings キーが全て含まれる。"""
        result = ok_result("完了")
        for key in ("ok", "summary", "data", "artifacts", "warnings"):
            assert key in result, f"キー '{key}' が結果に含まれていません"

    @pytest.mark.parametrize(
        "summary",
        [
            "1 件のメディアをインスペクトしました",
            "プロジェクトを初期化しました",
            "timeline に 3 つのオペレーションを適用しました",
        ],
    )
    def test_summary_passthrough(self, summary: str) -> None:
        """summary の値がそのまま返される。"""
        result = ok_result(summary)
        assert result["summary"] == summary

    def test_ok_field_is_boolean_true(self) -> None:
        """ok の値は Python bool の True（1 ではない）。"""
        result = ok_result("ok")
        assert result["ok"] is True
        assert type(result["ok"]) is bool


# ===========================================================================
# error_result
# ===========================================================================


class TestErrorResult:
    """error_result が { ok: False, error: { code, message, hint } } 形 dict を返すことを確認する。"""

    def test_returns_ok_false(self) -> None:
        """ok キーが False。"""
        result = error_result("FILE_NOT_FOUND", "ファイルが見つかりません", "パスを確認してください")
        assert result["ok"] is False

    def test_error_key_exists(self) -> None:
        """error キーが存在する。"""
        result = error_result("INVALID_INPUT", "不正入力", "修正してください")
        assert "error" in result

    def test_error_has_code(self) -> None:
        """error.code に渡した文字列が格納される。"""
        result = error_result("PROBE_FAILED", "ffprobe 出力のパースに失敗", "ffprobe を確認")
        assert result["error"]["code"] == "PROBE_FAILED"

    def test_error_has_message(self) -> None:
        """error.message に渡した文字列が格納される。"""
        result = error_result("OTIO_ERROR", "OTIO ファイルのパースに失敗しました", "ヒント")
        assert result["error"]["message"] == "OTIO ファイルのパースに失敗しました"

    def test_error_has_hint(self) -> None:
        """error.hint に渡した文字列が格納される。"""
        result = error_result("DEPENDENCY_MISSING", "ffprobe が見つかりません", "winget install Gyan.FFmpeg")
        assert result["error"]["hint"] == "winget install Gyan.FFmpeg"

    def test_error_structure_keys(self) -> None:
        """error オブジェクトに code / message / hint キーが全て含まれる。"""
        result = error_result("INTERNAL", "予期しないエラー", "再現条件を添えて報告してください")
        for key in ("code", "message", "hint"):
            assert key in result["error"], f"error オブジェクトにキー '{key}' がありません"

    def test_top_level_keys(self) -> None:
        """トップレベルに ok / error キーが存在する。"""
        result = error_result("SUBPROCESS_FAILED", "プロセスが終了コード 1 で失敗", "コマンドを確認")
        for key in ("ok", "error"):
            assert key in result, f"トップレベルにキー '{key}' がありません"

    def test_ok_field_is_boolean_false(self) -> None:
        """ok の値は Python bool の False（0 ではない）。"""
        result = error_result("INVALID_INPUT", "x", "y")
        assert result["ok"] is False
        assert type(result["ok"]) is bool

    def test_no_extra_top_level_keys(self) -> None:
        """トップレベルに余分なキーは基本的に含まれない（ok / error のみ）。
        失敗エンベロープは ToolErrorResult 契約に従う。"""
        result = error_result("FILE_NOT_FOUND", "msg", "hint")
        # ok と error 以外のキーは ToolErrorResult 仕様外
        extra_keys = set(result.keys()) - {"ok", "error"}
        assert not extra_keys, f"予期しないキーが含まれています: {extra_keys}"

    @pytest.mark.parametrize(
        "code",
        [
            "DEPENDENCY_MISSING",
            "INVALID_INPUT",
            "FILE_NOT_FOUND",
            "PATH_NOT_ALLOWED",
            "SUBPROCESS_FAILED",
            "SUBPROCESS_TIMEOUT",
            "PROBE_FAILED",
            "OTIO_ERROR",
            "PROJECT_NOT_FOUND",
            "PROJECT_EXISTS",
            "UNSUPPORTED_OPERATION",
            "INTERNAL",
            "TRACK_NOT_FOUND",
        ],
    )
    def test_all_error_codes(self, code: str) -> None:
        """全 ErrorCode 値で error_result を構築できる。"""
        result = error_result(code, "テストメッセージ", "テストヒント")
        assert result["ok"] is False
        assert result["error"]["code"] == code

    def test_hint_is_actionable_pattern(self) -> None:
        """hint が空でないこと（アクション可能 hint は必須・§6 規約）。"""
        result = error_result("SUBPROCESS_TIMEOUT", "タイムアウトしました", "タイムアウト値を増やしてください")
        assert len(result["error"]["hint"]) > 0
