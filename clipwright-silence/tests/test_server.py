"""test_server.py — clipwright-silence server.py（MCP + CLI）の Red テスト。

対象:
  - clipwright_detect_silence ツールが MCP に登録され、
    detect.detect_silence へ委譲すること
  - MCP annotations（§6.2・detect 系）:
    readOnlyHint:true / destructiveHint:false / idempotentHint:true
  - 成功時エンベロープ（ok:true）
  - 失敗時エンベロープ（ok:false, error:{code,message,hint}）
  - detect_silence が error_result を返したらそのまま返ること
  - main() 関数の存在と呼び出せること（DC-GP-002）

server.py は未実装のため全テストが「機能未実装による失敗」で Red になる。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# server.py の import 試行（未実装なら _SERVER_AVAILABLE = False）
# ---------------------------------------------------------------------------

try:
    from clipwright_silence.server import (
        clipwright_detect_silence as server_detect_silence,
    )
    from clipwright_silence.server import main, mcp

    _SERVER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _SERVER_AVAILABLE = False

# server.py が存在しない限り全テストを xfail にする
pytestmark = pytest.mark.xfail(
    not _SERVER_AVAILABLE,
    reason="server.py が未実装のため Red（機能未実装による失敗）",
    strict=True,
)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _ok_envelope(**kwargs: Any) -> dict[str, Any]:
    """成功エンベロープのひな型を返す。"""
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
    """失敗エンベロープのひな型を返す。"""
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": "error",
            "hint": "hint",
        },
    }


# ---------------------------------------------------------------------------
# MCP annotations テスト（§6.2・detect 系）
# ---------------------------------------------------------------------------


class TestMcpAnnotations:
    """clipwright_detect_silence ツールの MCP annotations を検証する。

    detect 系: readOnlyHint:true / destructiveHint:false / idempotentHint:true
    （render と異なり readOnlyHint=true である点に注意）
    """

    def _get_annotations(self) -> Any:
        # CR L-1: FastMCP の公開 API でツール情報を取得する手段がないため、
        # プライベート API (_tool_manager) に依存している。
        # FastMCP のバージョンアップで _tool_manager が変更・削除された場合に
        # このテストが壊れるリスクがある。公開 API が整備され次第移行すること。
        tool = mcp._tool_manager.get_tool(  # type: ignore[attr-defined]
            "clipwright_detect_silence"
        )
        assert tool is not None, "clipwright_detect_silence が mcp に登録されていること"
        return tool.annotations

    def test_tool_is_registered(self) -> None:
        """clipwright_detect_silence が mcp に登録されていること。"""
        # CR L-1: _tool_manager はプライベート API。FastMCP 更新で壊れるリスクあり
        tool = mcp._tool_manager.get_tool(  # type: ignore[attr-defined]
            "clipwright_detect_silence"
        )
        assert tool is not None

    def test_read_only_hint_is_true(self) -> None:
        """readOnlyHint=True（メディアファイルを書き換えない・detect 系規約）。"""
        ann = self._get_annotations()
        assert ann.readOnlyHint is True

    def test_destructive_hint_is_false(self) -> None:
        """destructiveHint=False（入力メディア・OTIO は不変）。"""
        ann = self._get_annotations()
        assert ann.destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        """idempotentHint=True（同一入力・同一パラメータ→同一 timeline）。"""
        ann = self._get_annotations()
        assert ann.idempotentHint is True


# ---------------------------------------------------------------------------
# MCP ツール呼び出し: detect.detect_silence への委譲テスト
# ---------------------------------------------------------------------------


class TestMcpToolDelegation:
    """detect.detect_silence への委譲と error エンベロープのパススルーを検証する。

    detect_silence を patch して呼び出しの委譲を確認する。
    """

    def test_success_delegates_to_detect_silence(self) -> None:
        """成功時に detect.detect_silence を呼び委譲すること。"""
        expected = _ok_envelope(summary="detected ok")

        with patch(
            "clipwright_silence.server.detect_silence",
            return_value=expected,
        ) as mock_detect:
            result = server_detect_silence(
                media="video.mp4",
                output="out.otio",
                options=None,
            )

        mock_detect.assert_called_once()
        assert result["ok"] is True

    def test_failure_returns_error_envelope(self) -> None:
        """detect_silence が失敗エンベロープを返すと server もそのまま返す。"""
        expected = _error_envelope("FILE_NOT_FOUND")

        with patch(
            "clipwright_silence.server.detect_silence",
            return_value=expected,
        ):
            result = server_detect_silence(
                media="missing.mp4",
                output="out.otio",
                options=None,
            )

        assert result["ok"] is False
        assert result["error"]["code"] == "FILE_NOT_FOUND"

    def test_error_result_passthrough(self) -> None:
        """detect が error_result を返したらそのまま返ること（二重変換なし）。"""
        expected = _error_envelope("DEPENDENCY_MISSING")

        with patch(
            "clipwright_silence.server.detect_silence",
            return_value=expected,
        ):
            result = server_detect_silence(
                media="video.mp4",
                output="out.otio",
                options=None,
            )

        assert result["ok"] is False
        error = result["error"]
        assert error["code"] == "DEPENDENCY_MISSING"
        assert "message" in error
        assert "hint" in error

    def test_error_envelope_has_code_message_hint(self) -> None:
        """失敗エンベロープに code / message / hint が含まれること。"""
        expected: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": "UNSUPPORTED_OPERATION",
                "message": "音声ストリームが無いため無音検出できません",
                "hint": "音声ストリームを含む素材を指定してください",
            },
        }

        with patch(
            "clipwright_silence.server.detect_silence",
            return_value=expected,
        ):
            result = server_detect_silence(
                media="no_audio.mp4",
                output="out.otio",
                options=None,
            )

        assert result["ok"] is False
        error = result["error"]
        assert "code" in error
        assert "message" in error
        assert "hint" in error

    def test_options_passed_to_detect_silence(self) -> None:
        """options の内容が detect_silence に渡されること。"""
        from clipwright_silence.schemas import DetectSilenceOptions

        opts = DetectSilenceOptions(
            silence_threshold_db=-40.0, min_silence_duration=1.0
        )

        with patch(
            "clipwright_silence.server.detect_silence",
            return_value=_ok_envelope(),
        ) as mock_detect:
            server_detect_silence(
                media="video.mp4",
                output="out.otio",
                options=opts,
            )

        mock_detect.assert_called_once()
        call_args = mock_detect.call_args
        assert call_args is not None

    def test_ok_envelope_structure(self) -> None:
        """成功エンベロープに ok/summary/data/artifacts/warnings が含まれること。"""
        expected = _ok_envelope(
            summary=(
                "総尺 60 秒の素材から無音 3 区間を検出。"
                "残す 4 区間の timeline.otio を生成しました。"
            ),
            data={
                "silence_count": 3,
                "total_silence_seconds": 10.0,
                "keep_count": 4,
                "total_keep_seconds": 50.0,
            },
            artifacts=[{"role": "timeline", "path": "out.otio", "format": "otio"}],
        )

        with patch(
            "clipwright_silence.server.detect_silence",
            return_value=expected,
        ):
            result = server_detect_silence(
                media="video.mp4",
                output="out.otio",
                options=None,
            )

        assert result["ok"] is True
        assert "summary" in result
        assert "data" in result
        assert "artifacts" in result
        assert "warnings" in result


# ---------------------------------------------------------------------------
# main() 存在確認テスト（DC-GP-002 / §6.3）
# ---------------------------------------------------------------------------


class TestCliMain:
    """main() 関数の存在と基本呼び出しを検証する（DC-GP-002: CLI = MCP stdio 起動）。"""

    def test_main_is_callable(self) -> None:
        """main() 関数が存在し callable であること。"""
        assert callable(main)

    def test_main_exists_in_module(self) -> None:
        """clipwright_silence.server モジュールに main が定義されていること。"""
        import clipwright_silence.server as server_module

        assert hasattr(server_module, "main")
        assert callable(server_module.main)

    def test_main_runs_mcp_server(self) -> None:
        """main() が mcp.run を呼び出すこと（stdio 起動・DC-GP-002）。

        実際の stdio 起動は行わず、mcp.run のモックで確認する。
        """
        with patch.object(mcp, "run") as mock_run:
            main()

        mock_run.assert_called_once()
        # transport="stdio" で呼ばれることを確認
        _args, kwargs = mock_run.call_args
        assert kwargs.get("transport") == "stdio" or (
            len(_args) >= 1 and _args[0] == "stdio"
        )
