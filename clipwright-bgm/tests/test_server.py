"""test_server.py — Contract tests for server.py (MCP + CLI) (Red phase).

Test scope:
  13. MCP annotations values for clipwright_add_bgm:
      readOnlyHint=True / destructiveHint=False / idempotentHint=True / openWorldHint=False
  14. Connectivity check that the tool calls add_bgm and returns a ToolResult.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from clipwright_bgm.server import clipwright_add_bgm as server_action
from clipwright_bgm.server import main, mcp

# ===========================================================================
# Helpers
# ===========================================================================


def _ok_envelope(**kwargs: Any) -> dict[str, Any]:
    """Helper to generate a test ok envelope."""
    base: dict[str, Any] = {
        "ok": True,
        "summary": "BGM added.",
        "data": {},
        "artifacts": [{"role": "timeline", "path": "output.otio", "format": "otio"}],
        "warnings": [],
    }
    base.update(kwargs)
    return base


def _get_tool_annotations() -> Any:
    """Helper to return the annotations of the registered clipwright_add_bgm tool.

    FastMCP has no public API to retrieve registered tools, so the private
    attribute _tool_manager is accessed for testing purposes.
    """
    tool = mcp._tool_manager.get_tool("clipwright_add_bgm")  # noqa: SLF001
    assert tool is not None, "clipwright_add_bgm must be registered in mcp"
    return tool.annotations


# ===========================================================================
# Test scope 13: MCP registration and annotations check
# ===========================================================================


class TestMcpRegistration:
    """clipwright_add_bgm must be correctly registered in MCP."""

    def test_tool_is_registered(self) -> None:
        """clipwright_add_bgm must be present in the MCP tool list."""
        tool = mcp._tool_manager.get_tool("clipwright_add_bgm")  # noqa: SLF001
        assert tool is not None, "clipwright_add_bgm is not registered in MCP."


class TestMcpAnnotations:
    """MCP annotations check for clipwright_add_bgm (design CR M-4, project-conventions.md).

    Not read-only because a new output OTIO file is created.
    Input timeline and media are unchanged (non-destructive).
    readOnlyHint=False / destructiveHint=False / idempotentHint=True / openWorldHint=False.
    """

    def test_read_only_hint_is_false(self) -> None:
        """readOnlyHint=False: not read-only because a new output OTIO file is created (CR M-4)."""
        annotations = _get_tool_annotations()
        assert annotations.readOnlyHint is False

    def test_destructive_hint_is_false(self) -> None:
        """destructiveHint=False: not a destructive operation."""
        annotations = _get_tool_annotations()
        assert annotations.destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        """idempotentHint=True: same input produces same output."""
        annotations = _get_tool_annotations()
        assert annotations.idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False: no network access (OTIO operations only)."""
        annotations = _get_tool_annotations()
        assert annotations.openWorldHint is False


# ===========================================================================
# Test scope 14: Delegation connectivity to add_bgm
# ===========================================================================


class TestDelegation:
    """clipwright_add_bgm must correctly delegate to bgm.add_bgm."""

    def test_success_delegates_to_add_bgm(self) -> None:
        """On success, add_bgm must be called and its result returned."""
        with patch(
            "clipwright_bgm.server.add_bgm",
            return_value=_ok_envelope(summary="BGM added successfully"),
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
        """When add_bgm returns an error envelope, it must be propagated as-is."""
        error_envelope: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": "INVALID_INPUT",
                "message": "A BGM clip already exists.",
                "hint": "Check the existing BGM clip.",
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
        """timeline / bgm / output arguments must be correctly forwarded to add_bgm."""
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
        """options=None must be forwarded to add_bgm (default when omitted)."""
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
        # options may be None or a BgmOptions instance depending on implementation
        assert "options" in kwargs

    def test_options_explicit_forwarded(self) -> None:
        """When options is explicitly specified, it must be passed through as-is."""
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
# main() — stdio startup
# ===========================================================================


class TestCliMain:
    """main() must start the MCP server over stdio."""

    def test_main_runs_mcp_with_stdio_transport(self) -> None:
        """main() must call mcp.run(transport="stdio")."""
        with patch.object(mcp, "run") as mock_run:
            main()

        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        assert kwargs.get("transport") == "stdio" or (
            len(_args) >= 1 and _args[0] == "stdio"
        ), f"transport='stdio' was not passed. args={_args}, kwargs={kwargs}"
