"""test_server.py — server.py（MCP + CLI）の契約面テスト（Red フェーズ）。

検証観点:
  13. clipwright_add_bgm の MCP annotations 値の確認:
      readOnlyHint=True / destructiveHint=False / idempotentHint=True / openWorldHint=False
  14. ツールが add_bgm を呼び ToolResult を返す疎通確認。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from clipwright_bgm.server import clipwright_add_bgm as server_action
from clipwright_bgm.server import main, mcp

# ===========================================================================
# ヘルパー
# ===========================================================================


def _ok_envelope(**kwargs: Any) -> dict[str, Any]:
    """テスト用 ok エンベロープを生成するヘルパー。"""
    base: dict[str, Any] = {
        "ok": True,
        "summary": "BGM を追加しました。",
        "data": {},
        "artifacts": [{"role": "timeline", "path": "output.otio", "format": "otio"}],
        "warnings": [],
    }
    base.update(kwargs)
    return base


def _get_tool_annotations() -> Any:
    """MCP に登録された clipwright_add_bgm の annotations を返すヘルパー。

    FastMCP は登録済みツールを取得する公開 API を持たないため、
    テスト目的でプライベート属性 _tool_manager を参照する。
    """
    tool = mcp._tool_manager.get_tool("clipwright_add_bgm")  # noqa: SLF001
    assert tool is not None, "clipwright_add_bgm が mcp に登録されていること"
    return tool.annotations


# ===========================================================================
# テスト観点 13: MCP 登録・annotations 確認
# ===========================================================================


class TestMcpRegistration:
    """clipwright_add_bgm が MCP に正しく登録されていること。"""

    def test_tool_is_registered(self) -> None:
        """clipwright_add_bgm が MCP ツールリストに存在すること。"""
        tool = mcp._tool_manager.get_tool("clipwright_add_bgm")  # noqa: SLF001
        assert tool is not None, "clipwright_add_bgm が MCP に登録されていない。"


class TestMcpAnnotations:
    """clipwright_add_bgm の MCP annotations 確認（設計 CR M-4・project-conventions.md）。

    出力 OTIO ファイルを新規生成するため readOnlyHint=False。
    入力 timeline・メディアは不変（非破壊）。
    readOnlyHint=False / destructiveHint=False / idempotentHint=True / openWorldHint=False。
    """

    def test_read_only_hint_is_false(self) -> None:
        """readOnlyHint=False: 出力 OTIO ファイルを新規生成するため read-only ではない（CR M-4）。"""
        annotations = _get_tool_annotations()
        assert annotations.readOnlyHint is False

    def test_destructive_hint_is_false(self) -> None:
        """destructiveHint=False: 破壊的操作でない。"""
        annotations = _get_tool_annotations()
        assert annotations.destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        """idempotentHint=True: 同じ入力で同じ出力。"""
        annotations = _get_tool_annotations()
        assert annotations.idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False: ネットワークアクセスなし（OTIO 操作のみ）。"""
        annotations = _get_tool_annotations()
        assert annotations.openWorldHint is False


# ===========================================================================
# テスト観点 14: add_bgm への委譲疎通
# ===========================================================================


class TestDelegation:
    """clipwright_add_bgm が bgm.add_bgm へ正しく委譲すること。"""

    def test_success_delegates_to_add_bgm(self) -> None:
        """成功時に add_bgm が呼ばれ結果が返ること。"""
        with patch(
            "clipwright_bgm.server.add_bgm",
            return_value=_ok_envelope(summary="BGM 追加完了"),
        ) as mock_fn:
            result = server_action(
                timeline="timeline.otio",
                bgm="bgm.mp3",
                output="output.otio",
                options=None,
            )

        mock_fn.assert_called_once()
        assert result["ok"] is True
        assert "BGM" in result["summary"]

    def test_error_result_propagates(self) -> None:
        """add_bgm がエラーエンベロープを返した場合にそのまま伝播すること。"""
        error_envelope: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": "INVALID_INPUT",
                "message": "BGM クリップが既に存在します。",
                "hint": "既存の BGM クリップを確認してください。",
            },
        }
        with patch(
            "clipwright_bgm.server.add_bgm",
            return_value=error_envelope,
        ):
            result = server_action(
                timeline="timeline.otio",
                bgm="bgm.mp3",
                output="output.otio",
                options=None,
            )

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_timeline_and_bgm_and_output_forwarded(self) -> None:
        """timeline / bgm / output 引数が add_bgm に正しく渡ること。"""
        with patch(
            "clipwright_bgm.server.add_bgm",
            return_value=_ok_envelope(),
        ) as mock_fn:
            server_action(
                timeline="/path/to/timeline.otio",
                bgm="/path/to/bgm.mp3",
                output="/path/to/output.otio",
                options=None,
            )

        _args, kwargs = mock_fn.call_args
        assert kwargs.get("timeline") == "/path/to/timeline.otio"
        assert kwargs.get("bgm") == "/path/to/bgm.mp3"
        assert kwargs.get("output") == "/path/to/output.otio"

    def test_options_none_forwarded(self) -> None:
        """options=None が add_bgm に渡ること（省略時の既定）。"""
        with patch(
            "clipwright_bgm.server.add_bgm",
            return_value=_ok_envelope(),
        ) as mock_fn:
            server_action(
                timeline="timeline.otio",
                bgm="bgm.mp3",
                output="output.otio",
                options=None,
            )

        _args, kwargs = mock_fn.call_args
        # options は None または BgmOptions インスタンスのどちらでも可（実装依存）
        assert "options" in kwargs

    def test_options_explicit_forwarded(self) -> None:
        """options を明示指定した場合はそのまま渡ること。"""
        from clipwright_bgm.schemas import BgmOptions

        custom_opts = BgmOptions(volume_db=-12.0, fade_in_sec=1.0)
        with patch(
            "clipwright_bgm.server.add_bgm",
            return_value=_ok_envelope(),
        ) as mock_fn:
            server_action(
                timeline="timeline.otio",
                bgm="bgm.mp3",
                output="output.otio",
                options=custom_opts,
            )

        _args, kwargs = mock_fn.call_args
        passed = kwargs.get("options")
        assert passed is custom_opts or (
            isinstance(passed, BgmOptions) and passed.volume_db == pytest.approx(-12.0)
        )


# ===========================================================================
# main() — stdio 起動
# ===========================================================================


class TestCliMain:
    """main() が MCP サーバーを stdio で起動すること。"""

    def test_main_runs_mcp_with_stdio_transport(self) -> None:
        """main() が mcp.run(transport="stdio") を呼ぶこと。"""
        with patch.object(mcp, "run") as mock_run:
            main()

        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        assert kwargs.get("transport") == "stdio" or (
            len(_args) >= 1 and _args[0] == "stdio"
        ), f"transport='stdio' が渡されていない。args={_args}, kwargs={kwargs}"
