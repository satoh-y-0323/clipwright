"""test_server.py — clipwright-transcribe server.py（MCP + CLI）のテスト。

対象:
  - clipwright_transcribe ツールが MCP に登録され transcribe.transcribe_media へ委譲
  - MCP annotations（§6.2・detect 系・TR-AD-11）:
    readOnlyHint:true / destructiveHint:false / idempotentHint:true / openWorldHint:false
  - 成功・失敗エンベロープのパススルー
  - options None 時に TranscribeOptions() 既定
  - main() が mcp.run(transport="stdio") を呼ぶ
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from clipwright_transcribe.schemas import TranscribeOptions
from clipwright_transcribe.server import (
    clipwright_transcribe as server_transcribe,
)
from clipwright_transcribe.server import main, mcp

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
# MCP annotations（§6.2・detect 系・TR-AD-11）
# ---------------------------------------------------------------------------


class TestMcpAnnotations:
    """clipwright_transcribe ツールの MCP annotations を検証する。"""

    def _get_annotations(self) -> Any:
        # CR L-1: FastMCP の公開 API でツール情報を取得する手段がないため
        # プライベート API (_tool_manager) に依存している（silence と同方針）。
        tool = mcp._tool_manager.get_tool("clipwright_transcribe")  # noqa: SLF001
        assert tool is not None, "clipwright_transcribe が mcp に登録されていること"
        return tool.annotations

    def test_tool_is_registered(self) -> None:
        tool = mcp._tool_manager.get_tool("clipwright_transcribe")  # noqa: SLF001
        assert tool is not None

    def test_read_only_hint_is_true(self) -> None:
        assert self._get_annotations().readOnlyHint is True

    def test_destructive_hint_is_false(self) -> None:
        assert self._get_annotations().destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        assert self._get_annotations().idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False（完全オフライン・ネット非依存・TR-AD-11）。"""
        assert self._get_annotations().openWorldHint is False


# ---------------------------------------------------------------------------
# 委譲とエンベロープのパススルー
# ---------------------------------------------------------------------------


class TestDelegation:
    def test_success_delegates(self) -> None:
        expected = _ok_envelope(summary="transcribed")
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=expected,
        ) as mock_t:
            result = server_transcribe(media="v.mp4", output="o.otio", options=None)
        mock_t.assert_called_once()
        assert result["ok"] is True

    def test_failure_passthrough(self) -> None:
        expected = _error_envelope("FILE_NOT_FOUND")
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=expected,
        ):
            result = server_transcribe(
                media="missing.mp4", output="o.otio", options=None
            )
        assert result["ok"] is False
        assert result["error"]["code"] == "FILE_NOT_FOUND"

    def test_error_envelope_has_code_message_hint(self) -> None:
        expected = _error_envelope("DEPENDENCY_MISSING")
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=expected,
        ):
            result = server_transcribe(media="v.mp4", output="o.otio", options=None)
        error = result["error"]
        assert "code" in error
        assert "message" in error
        assert "hint" in error

    def test_options_none_uses_default(self) -> None:
        """options=None のとき TranscribeOptions() 既定が委譲先へ渡ること。"""
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=_ok_envelope(),
        ) as mock_t:
            server_transcribe(media="v.mp4", output="o.otio", options=None)
        _args, kwargs = mock_t.call_args
        passed = kwargs.get("options")
        assert isinstance(passed, TranscribeOptions)
        assert passed.language is None

    def test_options_passed_through(self) -> None:
        """指定した options がそのまま委譲先へ渡ること。"""
        opts = TranscribeOptions(language="ja", initial_prompt="clipwright")
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=_ok_envelope(),
        ) as mock_t:
            server_transcribe(media="v.mp4", output="o.otio", options=opts)
        _args, kwargs = mock_t.call_args
        assert kwargs.get("options") is opts


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
