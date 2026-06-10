"""test_server.py — clipwright-wrap server.py（MCP + CLI）のテスト。

対象:
  - clipwright_wrap_captions ツールが MCP に登録され wrap.wrap_captions へ委譲
  - MCP annotations（WR-AD-10）:
    readOnlyHint:true / destructiveHint:false / idempotentHint:true / openWorldHint:false
  - 成功・失敗エンベロープのパススルー
  - options None 時に WrapCaptionsOptions() 既定（language="ja"/max_chars=16/max_lines=2）
  - main() が mcp.run(transport="stdio") を呼ぶ

DC-GP-001 language 責務の検証方針（重要）:
  server.py は薄いラッパーで MCP 境界のエラー変換責務を持たない（transcribe server 同型）。
  language 対応外は WrapCaptionsOptions 構築時の Pydantic ValidationError が源流であり、
  server.py 自身が language を if 検査して INVALID_INPUT 化する分岐は作らない。
  本テストでは wrap.wrap_captions をモックして「委譲のみ」を検証する。
  language 検証は schema の責務（test_schemas.py で別途検証）。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from clipwright_wrap.server import (
    clipwright_wrap_captions as server_wrap_captions,
)
from clipwright_wrap.server import main, mcp

from clipwright_wrap.schemas import WrapCaptionsOptions

# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _ok_envelope(**kwargs: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ok": True,
        "summary": "ok",
        "data": {},
        "artifacts": [],
        "warnings": [],
    }
    base.update(kwargs)
    return base


def _error_envelope(code: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"code": code, "message": "error", "hint": "hint"},
    }


# ---------------------------------------------------------------------------
# MCP annotations（WR-AD-10）
# ---------------------------------------------------------------------------


class TestMcpAnnotations:
    """clipwright_wrap_captions ツールの MCP annotations を検証する。"""

    def _get_annotations(self) -> Any:
        # FastMCP の公開 API でツール情報を取得する手段がないため
        # プライベート API (_tool_manager) に依存している（transcribe/silence と同方針）。
        tool = mcp._tool_manager.get_tool("clipwright_wrap_captions")  # noqa: SLF001
        assert tool is not None, "clipwright_wrap_captions が mcp に登録されていること"
        return tool.annotations

    def test_tool_is_registered(self) -> None:
        tool = mcp._tool_manager.get_tool("clipwright_wrap_captions")  # noqa: SLF001
        assert tool is not None

    def test_read_only_hint_is_true(self) -> None:
        assert self._get_annotations().readOnlyHint is True

    def test_destructive_hint_is_false(self) -> None:
        assert self._get_annotations().destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        assert self._get_annotations().idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False（完全オフライン・ネット非依存・WR-AD-10）。"""
        assert self._get_annotations().openWorldHint is False


# ---------------------------------------------------------------------------
# 委譲とエンベロープのパススルー
# ---------------------------------------------------------------------------


class TestDelegation:
    def test_success_delegates(self) -> None:
        expected = _ok_envelope(summary="wrapped")
        with patch(
            "clipwright_wrap.server.wrap_captions",
            return_value=expected,
        ) as mock_w:
            result = server_wrap_captions(
                input="in.srt", output="out.srt", options=None
            )
        mock_w.assert_called_once()
        assert result["ok"] is True

    def test_failure_passthrough(self) -> None:
        expected = _error_envelope("FILE_NOT_FOUND")
        with patch(
            "clipwright_wrap.server.wrap_captions",
            return_value=expected,
        ):
            result = server_wrap_captions(
                input="missing.srt", output="out.srt", options=None
            )
        assert result["ok"] is False
        assert result["error"]["code"] == "FILE_NOT_FOUND"

    def test_error_envelope_has_code_message_hint(self) -> None:
        expected = _error_envelope("DEPENDENCY_MISSING")
        with patch(
            "clipwright_wrap.server.wrap_captions",
            return_value=expected,
        ):
            result = server_wrap_captions(
                input="in.srt", output="out.srt", options=None
            )
        error = result["error"]
        assert "code" in error
        assert "message" in error
        assert "hint" in error

    def test_options_none_uses_default(self) -> None:
        """options=None のとき WrapCaptionsOptions() 既定が委譲先へ渡ること。

        既定値: language="ja" / max_chars=16 / max_lines=2（WR-AD-05）。
        """
        with patch(
            "clipwright_wrap.server.wrap_captions",
            return_value=_ok_envelope(),
        ) as mock_w:
            server_wrap_captions(input="in.srt", output="out.srt", options=None)
        _args, kwargs = mock_w.call_args
        passed = kwargs.get("options")
        assert isinstance(passed, WrapCaptionsOptions)
        assert passed.language == "ja"
        assert passed.max_chars == 16
        assert passed.max_lines == 2

    def test_options_passed_through(self) -> None:
        """指定した options がそのまま委譲先へ渡ること。"""
        opts = WrapCaptionsOptions(language="zh-hans", max_chars=20, max_lines=3)
        with patch(
            "clipwright_wrap.server.wrap_captions",
            return_value=_ok_envelope(),
        ) as mock_w:
            server_wrap_captions(input="in.srt", output="out.srt", options=opts)
        _args, kwargs = mock_w.call_args
        assert kwargs.get("options") is opts

    def test_server_does_not_validate_language_itself(self) -> None:
        """server.py は language を if 検査して INVALID_INPUT 化する分岐を持たない。

        DC-GP-001: language 検証は WrapCaptionsOptions（schema）の責務。
        server は wrap.wrap_captions へ委譲するだけであり、
        モックが ok:True を返せばそのまま通過すること（二重変換なし）。
        """
        # wrap_captions をモックして ok:True を返させ、server が何も変換しないことを確認する
        expected = _ok_envelope(summary="no double conversion")
        opts = WrapCaptionsOptions(language="ja")  # 有効な language
        with patch(
            "clipwright_wrap.server.wrap_captions",
            return_value=expected,
        ) as mock_w:
            result = server_wrap_captions(
                input="in.srt", output="out.srt", options=opts
            )
        # server は結果をそのまま返す（変換なし）
        assert result["ok"] is True
        assert result["summary"] == "no double conversion"
        mock_w.assert_called_once()


# ---------------------------------------------------------------------------
# main() エントリポイント
# ---------------------------------------------------------------------------


class TestCliMain:
    def test_main_is_callable(self) -> None:
        assert callable(main)

    def test_main_runs_mcp_stdio(self) -> None:
        """main() が mcp.run(transport="stdio") を呼ぶこと。"""
        with patch.object(mcp, "run") as mock_run:
            main()
        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        assert kwargs.get("transport") == "stdio" or (
            len(_args) >= 1 and _args[0] == "stdio"
        )
