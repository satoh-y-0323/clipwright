"""test_server.py — Tests for clipwright-transcribe server.py (MCP + CLI).

Target:
  - clipwright_transcribe tool is registered in MCP and delegates to
    transcribe.transcribe_media
  - MCP annotations (§6.2, detect-class, TR-AD-11):
    readOnlyHint:true / destructiveHint:false / idempotentHint:true /
    openWorldHint:false
  - Success and error envelope pass-through
  - options=None uses TranscribeOptions() defaults
  - main() calls mcp.run(transport="stdio")
  - outputSchema is typed ToolResult (MCP boundary)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

from clipwright.schemas import ToolError, ToolResult

from clipwright_transcribe.schemas import TranscribeOptions
from clipwright_transcribe.server import (
    clipwright_transcribe as server_transcribe,
)
from clipwright_transcribe.server import main, mcp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_tool_result(**kwargs: Any) -> ToolResult:
    base = ToolResult(
        ok=True,
        summary="ok",
        data={},
        artifacts=[],
        warnings=[],
    )
    return base.model_copy(update=kwargs)


def _error_tool_result(code: str) -> ToolResult:
    return ToolResult(
        ok=False,
        error=ToolError(code=code, message="error", hint="hint"),
    )


# ---------------------------------------------------------------------------
# MCP annotations (§6.2, detect-class, TR-AD-11)
# ---------------------------------------------------------------------------


class TestMcpAnnotations:
    """Verify MCP annotations for the clipwright_transcribe tool."""

    def _get_annotations(self) -> object:
        # CR L-1: No public FastMCP API exists to retrieve tool info, so the private
        # _tool_manager API is used (same approach as the silence package).
        tool = mcp._tool_manager.get_tool("clipwright_transcribe")  # noqa: SLF001
        assert tool is not None, "clipwright_transcribe must be registered in mcp"
        return tool.annotations

    def test_tool_is_registered(self) -> None:
        tool = mcp._tool_manager.get_tool("clipwright_transcribe")  # noqa: SLF001
        assert tool is not None

    def test_read_only_hint_is_true(self) -> None:
        assert self._get_annotations().readOnlyHint is True  # type: ignore[union-attr]

    def test_destructive_hint_is_false(self) -> None:
        assert self._get_annotations().destructiveHint is False  # type: ignore[union-attr]

    def test_idempotent_hint_is_true(self) -> None:
        assert self._get_annotations().idempotentHint is True  # type: ignore[union-attr]

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False (fully offline, no network dependency; TR-AD-11)."""
        assert self._get_annotations().openWorldHint is False  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Delegation and envelope pass-through
# ---------------------------------------------------------------------------


class TestDelegation:
    def test_success_delegates(self) -> None:
        expected = _ok_tool_result(summary="transcribed")
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=expected,
        ) as mock_t:
            result = server_transcribe(media="v.mp4", output="o.otio", options=None)
        mock_t.assert_called_once()
        assert result.ok is True

    def test_failure_passthrough(self) -> None:
        expected = _error_tool_result("FILE_NOT_FOUND")
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=expected,
        ):
            result = server_transcribe(
                media="missing.mp4", output="o.otio", options=None
            )
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "FILE_NOT_FOUND"

    def test_error_envelope_has_code_message_hint(self) -> None:
        expected = _error_tool_result("DEPENDENCY_MISSING")
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=expected,
        ):
            result = server_transcribe(media="v.mp4", output="o.otio", options=None)
        assert result.error is not None
        assert result.error.code
        assert result.error.message
        assert result.error.hint

    def test_options_none_uses_default(self) -> None:
        """When options=None, TranscribeOptions() defaults are passed to the
        delegate."""
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=_ok_tool_result(),
        ) as mock_t:
            server_transcribe(media="v.mp4", output="o.otio", options=None)
        _args, kwargs = mock_t.call_args
        passed = kwargs.get("options")
        assert isinstance(passed, TranscribeOptions)
        assert passed.language is None

    def test_options_passed_through(self) -> None:
        """Explicitly provided options are forwarded unchanged to the delegate."""
        opts = TranscribeOptions(language="ja", initial_prompt="clipwright")
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=_ok_tool_result(),
        ) as mock_t:
            server_transcribe(media="v.mp4", output="o.otio", options=opts)
        _args, kwargs = mock_t.call_args
        assert kwargs.get("options") is opts


# ---------------------------------------------------------------------------
# MCP boundary: outputSchema and structuredContent
# ---------------------------------------------------------------------------


class TestMcpBoundary:
    def test_outputschema_is_typed(self) -> None:
        """outputSchema must expose typed ToolResult fields (ok, summary, etc.)."""
        tools = asyncio.run(mcp.list_tools())
        target = next((t for t in tools if t.name == "clipwright_transcribe"), None)
        assert target is not None, "clipwright_transcribe tool must be listed"
        schema = target.outputSchema or {}
        props = schema.get("properties") or {}
        assert "ok" in props, (
            f"outputSchema must be typed ToolResult with 'ok' property; got: {props}"
        )

    def test_structuredcontent_top_level_ok(self) -> None:
        """call_tool response must expose 'ok' at top level (no wrapping).

        FastMCP call_tool returns a tuple (content_list, structured_dict).
        The text content must contain 'ok' at the top level (not wrapped).
        """
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=ToolResult(
                ok=True,
                summary="ok",
                data={},
                artifacts=[],
                warnings=[],
            ),
        ):
            result = asyncio.run(
                mcp.call_tool(
                    "clipwright_transcribe",
                    {"media": "/fake.mp4", "output": "/fake.otio"},
                )
            )
        assert result, "call_tool must return non-empty result"
        # result is a tuple: (list[TextContent], structured_dict)
        content_list = result[0]
        assert content_list, "content list must be non-empty"
        content = json.loads(content_list[0].text)
        assert "ok" in content, (
            f"structuredContent must not be wrapped; top-level keys: {list(content.keys())}"
        )


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------


class TestCliMain:
    def test_main_is_callable(self) -> None:
        assert callable(main)

    def test_main_runs_mcp_stdio(self) -> None:
        """main() calls mcp.run(transport="stdio")."""
        with patch.object(mcp, "run") as mock_run:
            main()
        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        assert kwargs.get("transport") == "stdio" or (
            len(_args) >= 1 and _args[0] == "stdio"
        )
