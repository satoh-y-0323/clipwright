"""test_server_mcp_boundary.py — MCP wire boundary tests (Red phase).

Covers:
- outputSchema for all 4 tools must be typed (not generic DictOutput with additionalProperties=true)
- outputSchema properties must include: ok / summary / data / artifacts / warnings / error
- call_tool structuredContent top-level must have 'ok' key (no {"result": ...} wrapping)
- Union regression prevention: single ToolResult model, no wrap_output union issue
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from clipwright.server import mcp

# Tool names defined in server.py
_TOOL_NAMES = [
    "clipwright_init_project",
    "clipwright_inspect_media",
    "clipwright_read_timeline",
    "clipwright_write_timeline",
]

# Required envelope property names in outputSchema
_REQUIRED_PROPS = {"ok", "summary", "data", "artifacts", "warnings", "error"}


# ===========================================================================
# Helpers
# ===========================================================================


def _run(coro: Any) -> Any:
    """Run an async coroutine synchronously (pytest-asyncio not required)."""
    return asyncio.run(coro)


# ===========================================================================
# outputSchema — typed schema (not generic DictOutput)
# ===========================================================================


class TestOutputSchemaTyped:
    """outputSchema for each tool must be a typed ToolResult schema, not DictOutput."""

    def test_four_tools_are_registered(self) -> None:
        """All 4 tools appear in list_tools()."""
        tools = _run(mcp.list_tools())
        names = {t.name for t in tools}
        for expected in _TOOL_NAMES:
            assert expected in names, f"Tool '{expected}' not found in list_tools()"

    @pytest.mark.parametrize("tool_name", _TOOL_NAMES)
    def test_output_schema_not_generic_dict_output(self, tool_name: str) -> None:
        """outputSchema must not be a generic DictOutput (additionalProperties: true is untyped).

        After ToolResult unification, the schema must come from ToolResult Pydantic model,
        not from the fallback dict[str, Any] path that FastMCP uses for plain dicts.
        """
        tools = _run(mcp.list_tools())
        tool = next((t for t in tools if t.name == tool_name), None)
        assert tool is not None, f"Tool '{tool_name}' not found"
        schema = tool.outputSchema or {}
        # Generic DictOutput has additionalProperties=true and no 'properties'
        # After the fix, outputSchema must have explicit 'properties'
        assert "properties" in schema, (
            f"Tool '{tool_name}' outputSchema has no 'properties' key — "
            f"it is still a generic DictOutput. Got: {schema}"
        )

    @pytest.mark.parametrize("tool_name", _TOOL_NAMES)
    def test_output_schema_additional_properties_not_true(self, tool_name: str) -> None:
        """outputSchema must not have additionalProperties=True (untyped fallback).

        The generic DictOutput sets additionalProperties=True because the return
        type is dict[str, Any]. After ToolResult unification, this must be False
        or absent.
        """
        tools = _run(mcp.list_tools())
        tool = next((t for t in tools if t.name == tool_name), None)
        assert tool is not None
        schema = tool.outputSchema or {}
        additional = schema.get("additionalProperties", False)
        assert additional is not True, (
            f"Tool '{tool_name}' outputSchema still has additionalProperties=True — "
            "return type must be ToolResult, not dict[str, Any]"
        )

    @pytest.mark.parametrize("tool_name", _TOOL_NAMES)
    def test_output_schema_has_required_envelope_properties(self, tool_name: str) -> None:
        """outputSchema properties must include all ToolResult envelope fields.

        Required: ok / summary / data / artifacts / warnings / error
        """
        tools = _run(mcp.list_tools())
        tool = next((t for t in tools if t.name == tool_name), None)
        assert tool is not None
        schema = tool.outputSchema or {}
        props = set(schema.get("properties", {}).keys())
        missing = _REQUIRED_PROPS - props
        assert not missing, (
            f"Tool '{tool_name}' outputSchema is missing properties: {missing}. "
            f"Found: {props}"
        )

    @pytest.mark.parametrize("tool_name", _TOOL_NAMES)
    def test_output_schema_ok_property_is_boolean(self, tool_name: str) -> None:
        """outputSchema.properties.ok must be typed as boolean."""
        tools = _run(mcp.list_tools())
        tool = next((t for t in tools if t.name == tool_name), None)
        assert tool is not None
        schema = tool.outputSchema or {}
        props = schema.get("properties", {})
        assert "ok" in props, f"'ok' not in outputSchema.properties for '{tool_name}'"
        ok_type = props["ok"].get("type")
        assert ok_type == "boolean", (
            f"outputSchema.properties.ok.type must be 'boolean', got '{ok_type}'"
        )


# ===========================================================================
# call_tool structuredContent — no {"result": ...} wrapping (union regression)
# ===========================================================================


class TestCallToolStructuredContent:
    """structuredContent from call_tool must have 'ok' at top level (no union wrapping)."""

    def test_read_timeline_error_structured_top_level_ok(self) -> None:
        """clipwright_read_timeline with invalid args returns structuredContent with top-level 'ok'.

        The tool returns error_result when both args are None.
        If ToolResult is a union (old design), FastMCP 1.27.2 wraps structuredContent
        as {"result": ...}, which breaks the wire contract.
        """
        content_blocks, structured = _run(
            mcp.call_tool("clipwright_read_timeline", {})
        )
        assert isinstance(structured, dict), (
            f"structuredContent must be a dict, got {type(structured)}"
        )
        assert "ok" in structured, (
            f"'ok' must be at the top level of structuredContent. "
            f"Got keys: {list(structured.keys())}. "
            "If 'result' is present, ToolResult is still a union type (regression)."
        )

    def test_read_timeline_error_not_wrapped_in_result(self) -> None:
        """structuredContent must NOT be wrapped in a 'result' key (union wrapping regression)."""
        content_blocks, structured = _run(
            mcp.call_tool("clipwright_read_timeline", {})
        )
        assert "result" not in structured, (
            "structuredContent is wrapped in {'result': ...} — "
            "this is the FastMCP union wrapping regression. "
            "ToolResult must be a single model, not a union with ToolErrorResult."
        )

    def test_read_timeline_error_ok_is_false(self) -> None:
        """clipwright_read_timeline with no args returns ok=False in structuredContent."""
        content_blocks, structured = _run(
            mcp.call_tool("clipwright_read_timeline", {})
        )
        assert structured.get("ok") is False, (
            f"Expected ok=False for error envelope, got: {structured.get('ok')}"
        )

    def test_read_timeline_error_has_error_key(self) -> None:
        """Error envelope structuredContent has an 'error' key."""
        content_blocks, structured = _run(
            mcp.call_tool("clipwright_read_timeline", {})
        )
        assert "error" in structured, (
            f"Error envelope must have 'error' key. Got: {list(structured.keys())}"
        )

    def test_init_project_success_structured_top_level_ok(self, tmp_path: Any) -> None:
        """clipwright_init_project success returns structuredContent with top-level 'ok'.

        Uses a real tmp directory to avoid ffprobe/subprocess dependency.
        """
        project_dir = str(tmp_path / "test_proj")
        content_blocks, structured = _run(
            mcp.call_tool(
                "clipwright_init_project",
                {"project_dir": project_dir, "name": "test"},
            )
        )
        assert isinstance(structured, dict)
        assert "ok" in structured, (
            f"'ok' must be at top level of structuredContent. "
            f"Got keys: {list(structured.keys())}"
        )

    def test_init_project_success_ok_is_true(self, tmp_path: Any) -> None:
        """clipwright_init_project success returns ok=True in structuredContent."""
        project_dir = str(tmp_path / "test_proj2")
        content_blocks, structured = _run(
            mcp.call_tool(
                "clipwright_init_project",
                {"project_dir": project_dir, "name": "test"},
            )
        )
        assert structured.get("ok") is True, (
            f"Expected ok=True for success envelope, got: {structured.get('ok')}"
        )

    def test_inspect_media_error_via_mock_structured_top_level_ok(self) -> None:
        """clipwright_inspect_media error path returns structuredContent with top-level 'ok'.

        Mocks _inspect_media to avoid ffprobe subprocess dependency.
        """
        from clipwright.errors import ClipwrightError, ErrorCode

        with patch(
            "clipwright.server._inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="ffprobe not found",
                hint="Install FFmpeg",
            ),
        ):
            content_blocks, structured = _run(
                mcp.call_tool("clipwright_inspect_media", {"path": "/fake/video.mp4"})
            )

        assert isinstance(structured, dict)
        assert "ok" in structured, (
            f"'ok' must be at top level of structuredContent. "
            f"Got keys: {list(structured.keys())}"
        )
        assert structured.get("ok") is False


# ===========================================================================
# ToolResult unification — no ToolErrorResult import after merge
# ===========================================================================


class TestToolResultUnification:
    """After ToolResult unification, ToolErrorResult must be removed from schemas."""

    def test_tool_error_result_removed_from_schemas(self) -> None:
        """ToolErrorResult must not be importable from clipwright.schemas after unification.

        Before: schemas.py exports ToolErrorResult (Literal[False] model).
        After:  ToolErrorResult is deleted; ToolResult is a unified model with ok: bool.
        This test is Red until ToolErrorResult is removed.
        """
        try:
            from clipwright.schemas import ToolErrorResult  # type: ignore[attr-defined]

            pytest.fail(
                "ToolErrorResult is still importable from clipwright.schemas. "
                "It must be removed as part of the ToolResult unification."
            )
        except ImportError:
            pass  # Expected after unification — test passes

    def test_tool_result_ok_accepts_false(self) -> None:
        """Unified ToolResult must accept ok=False without ValidationError."""
        from clipwright.schemas import ToolError, ToolResult

        try:
            result = ToolResult(
                ok=False,
                error=ToolError(code="X", message="m", hint="h"),
            )
            assert result.ok is False
        except Exception as exc:
            pytest.fail(
                f"ToolResult(ok=False) raised {type(exc).__name__}: {exc}. "
                "ToolResult must use ok: bool, not ok: Literal[True]."
            )

    def test_tool_result_summary_optional(self) -> None:
        """Unified ToolResult must allow summary=None (not required)."""
        from clipwright.schemas import ToolError, ToolResult

        try:
            result = ToolResult(
                ok=False,
                error=ToolError(code="X", message="m", hint="h"),
            )
            assert result.summary is None
        except Exception as exc:
            pytest.fail(
                f"ToolResult with summary=None raised {type(exc).__name__}: {exc}. "
                "summary must be str | None = None in unified ToolResult."
            )

    def test_tool_result_has_error_field(self) -> None:
        """Unified ToolResult must have an error field (absent in old model)."""
        from clipwright.schemas import ToolResult

        result = ToolResult(ok=True, summary="ok")
        assert hasattr(result, "error"), (
            "ToolResult must have an 'error' field after unification"
        )
        assert result.error is None
