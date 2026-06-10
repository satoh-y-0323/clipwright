"""test_server.py — server.py（MCP + CLI）のテスト。

対象:
  - clipwright___ACTION__ が MCP に登録され __TOOL__.__ACTION__ へ委譲する
  - MCP annotations（detect 系の既定値）
  - 成功・失敗エンベロープのパススルー
  - options=None 時に既定 __Action__Options() が委譲先へ渡る
  - main() が mcp.run(transport="stdio") を呼ぶ
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from clipwright___TOOL__.schemas import __Action__Options
from clipwright___TOOL__.server import clipwright___ACTION__ as server_action
from clipwright___TOOL__.server import main, mcp


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


class TestMcpAnnotations:
    def _annotations(self) -> Any:
        tool = mcp._tool_manager.get_tool("clipwright___ACTION__")  # noqa: SLF001
        assert tool is not None, "clipwright___ACTION__ が mcp に登録されていること"
        return tool.annotations

    def test_tool_is_registered(self) -> None:
        assert mcp._tool_manager.get_tool("clipwright___ACTION__") is not None  # noqa: SLF001

    def test_read_only_hint_is_true(self) -> None:
        assert self._annotations().readOnlyHint is True

    def test_destructive_hint_is_false(self) -> None:
        assert self._annotations().destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        assert self._annotations().idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        assert self._annotations().openWorldHint is False


class TestDelegation:
    def test_success_delegates(self) -> None:
        with patch(
            "clipwright___TOOL__.server.__ACTION__",
            return_value=_ok_envelope(summary="done"),
        ) as mock_a:
            result = server_action(input="in.txt", output="out.json", options=None)
        mock_a.assert_called_once()
        assert result["ok"] is True

    def test_options_none_uses_default(self) -> None:
        with patch(
            "clipwright___TOOL__.server.__ACTION__",
            return_value=_ok_envelope(),
        ) as mock_a:
            server_action(input="in.txt", output="out.json", options=None)
        _args, kwargs = mock_a.call_args
        passed = kwargs.get("options")
        assert isinstance(passed, __Action__Options)
        assert passed.example_threshold == 0.5


class TestCliMain:
    def test_main_runs_mcp_stdio(self) -> None:
        with patch.object(mcp, "run") as mock_run:
            main()
        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        assert kwargs.get("transport") == "stdio" or (
            len(_args) >= 1 and _args[0] == "stdio"
        )
