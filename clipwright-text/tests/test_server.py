"""test_server.py — Tests for clipwright-text server.py MCP boundary.

Target:
  - clipwright_add_text tool registered in MCP
  - MCP annotations:
    readOnlyHint=False / destructiveHint=False / idempotentHint=True /
    openWorldHint=False
  - options=None -> error_result with INVALID_INPUT
  - Success envelope shape: ok/summary/data/artifacts/warnings
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from clipwright.schemas import ToolError, ToolResult

# ---------------------------------------------------------------------------
# Attempt to import server.py
# ---------------------------------------------------------------------------

try:
    from clipwright_text.server import main, mcp

    _SERVER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _SERVER_AVAILABLE = False

# Mark all tests as xfail unless server.py is importable.
pytestmark = pytest.mark.xfail(
    not _SERVER_AVAILABLE,
    reason="server.py is not implemented",
    strict=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_tool_result(**kwargs: Any) -> ToolResult:
    """Return a success ToolResult template for clipwright_add_text."""
    defaults: dict[str, Any] = {
        "ok": True,
        "summary": 'Added text overlay "Hello World" at 1.0s for 3.0s. Timeline now has 1 text overlay(s). Output: out.otio.',
        "data": {
            "applied": 1,
            "overlay_count": 1,
            "start_sec": 1.0,
            "duration_sec": 3.0,
        },
        "artifacts": [
            {
                "role": "timeline",
                "path": "/tmp/out.otio",
                "format": "otio",
            }
        ],
        "warnings": [],
    }
    defaults.update(kwargs)
    return ToolResult.model_validate(defaults)


def _error_tool_result(code: str) -> ToolResult:
    """Return an error ToolResult template."""
    return ToolResult(
        ok=False,
        error=ToolError(code=code, message="error", hint="hint"),
    )


# ---------------------------------------------------------------------------
# Tool registration tests
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Validate that clipwright_add_text is registered in the MCP server."""

    def test_tool_is_registered(self) -> None:
        """clipwright_add_text must be registered in mcp."""
        tool = mcp._tool_manager.get_tool("clipwright_add_text")  # noqa: SLF001
        assert tool is not None

    def test_registered_tool_name(self) -> None:
        """The registered tool must be named 'clipwright_add_text'."""
        tools = asyncio.run(mcp.list_tools())
        names = [t.name for t in tools]
        assert "clipwright_add_text" in names


# ---------------------------------------------------------------------------
# MCP annotations tests
# ---------------------------------------------------------------------------


class TestMcpAnnotations:
    """Validate MCP annotations for the clipwright_add_text tool.

    readOnlyHint=False  (writes a new OTIO file; input is never modified)
    destructiveHint=False (input timeline is non-destructive)
    idempotentHint=True  (same input + same options -> same output timeline)
    openWorldHint=False  (local filesystem only, no network access)
    """

    def _get_annotations(self) -> Any:
        tool = mcp._tool_manager.get_tool("clipwright_add_text")  # noqa: SLF001
        assert tool is not None, "clipwright_add_text must be registered in mcp"
        return tool.annotations

    def test_read_only_hint_is_false(self) -> None:
        """readOnlyHint=False (writes a new OTIO file to output)."""
        ann = self._get_annotations()
        assert ann.readOnlyHint is False

    def test_destructive_hint_is_false(self) -> None:
        """destructiveHint=False (input timeline remains unchanged)."""
        ann = self._get_annotations()
        assert ann.destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        """idempotentHint=True (same input + options -> same output)."""
        ann = self._get_annotations()
        assert ann.idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False (local filesystem only)."""
        ann = self._get_annotations()
        assert ann.openWorldHint is False


# ---------------------------------------------------------------------------
# options=None behavior
# ---------------------------------------------------------------------------


class TestOptionsNone:
    """When options=None, server must return INVALID_INPUT."""

    def test_options_none_returns_invalid_input(self) -> None:
        """options=None must return ok=False with INVALID_INPUT."""
        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_add_text",
                {"timeline": "test.otio", "output": "out.otio"},
            )
        )
        assert structured["ok"] is False
        error = structured.get("error") or {}
        assert error.get("code") == "INVALID_INPUT"
        assert error.get("hint"), "hint must be non-empty"

    def test_options_none_not_a_success(self) -> None:
        """options=None must never succeed."""
        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_add_text",
                {"timeline": "test.otio", "output": "out.otio"},
            )
        )
        assert structured["ok"] is not True


# ---------------------------------------------------------------------------
# Success envelope shape
# ---------------------------------------------------------------------------


class TestSuccessEnvelopeShape:
    """Validate the success envelope shape returned via MCP."""

    def test_success_envelope_has_ok_summary_data_artifacts_warnings(self) -> None:
        """Success envelope must contain ok/summary/data/artifacts/warnings."""
        expected = _ok_tool_result()

        with patch(
            "clipwright_text.server.add_text",
            return_value=expected,
        ):
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_add_text",
                    {
                        "timeline": "test.otio",
                        "output": "out.otio",
                        "options": {
                            "text": "Hello World",
                            "start_sec": 1.0,
                            "duration_sec": 3.0,
                        },
                    },
                )
            )

        assert structured["ok"] is True
        assert structured.get("summary"), "summary must be non-empty"
        assert "data" in structured, "envelope must have data"
        assert "artifacts" in structured, "envelope must have artifacts"
        assert "warnings" in structured, "envelope must have warnings"

    def test_success_data_applied(self) -> None:
        """data.applied must be present in the success envelope."""
        expected = _ok_tool_result()
        with patch("clipwright_text.server.add_text", return_value=expected):
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_add_text",
                    {
                        "timeline": "test.otio",
                        "output": "out.otio",
                        "options": {
                            "text": "Hello World",
                            "start_sec": 1.0,
                            "duration_sec": 3.0,
                        },
                    },
                )
            )
        assert structured["data"]["applied"] == 1

    def test_success_artifacts_timeline_entry(self) -> None:
        """artifacts must contain entry with role='timeline' and format='otio'."""
        expected = _ok_tool_result()
        with patch("clipwright_text.server.add_text", return_value=expected):
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_add_text",
                    {
                        "timeline": "test.otio",
                        "output": "out.otio",
                        "options": {
                            "text": "Hello World",
                            "start_sec": 1.0,
                            "duration_sec": 3.0,
                        },
                    },
                )
            )
        artifacts = structured.get("artifacts", [])
        tl_artifact = next((a for a in artifacts if a.get("role") == "timeline"), None)
        assert tl_artifact is not None, "artifacts must contain role='timeline'"
        assert tl_artifact.get("format") == "otio"

    def test_success_warnings_is_list(self) -> None:
        """warnings must be a list (empty list on success)."""
        expected = _ok_tool_result()
        with patch("clipwright_text.server.add_text", return_value=expected):
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_add_text",
                    {
                        "timeline": "test.otio",
                        "output": "out.otio",
                        "options": {
                            "text": "Hello World",
                            "start_sec": 1.0,
                            "duration_sec": 3.0,
                        },
                    },
                )
            )
        assert isinstance(structured.get("warnings"), list)


# ---------------------------------------------------------------------------
# MCP tool delegation
# ---------------------------------------------------------------------------


class TestMcpToolDelegation:
    """Validate delegation to text.add_text and envelope passthrough."""

    def test_success_delegates_to_add_text(self) -> None:
        """On success, must call and delegate to add_text."""
        expected = _ok_tool_result()

        with patch(
            "clipwright_text.server.add_text",
            return_value=expected,
        ) as mock_add_text:
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_add_text",
                    {
                        "timeline": "test.otio",
                        "output": "out.otio",
                        "options": {
                            "text": "Hello World",
                            "start_sec": 1.0,
                            "duration_sec": 3.0,
                        },
                    },
                )
            )

        mock_add_text.assert_called_once()
        assert structured["ok"] is True

    def test_failure_envelope_returned_as_is(self) -> None:
        """When add_text returns an error envelope, server returns it unchanged."""
        expected = _error_tool_result("FILE_NOT_FOUND")

        with patch(
            "clipwright_text.server.add_text",
            return_value=expected,
        ):
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_add_text",
                    {
                        "timeline": "missing.otio",
                        "output": "out.otio",
                        "options": {
                            "text": "Hello World",
                            "start_sec": 1.0,
                            "duration_sec": 3.0,
                        },
                    },
                )
            )

        assert structured["ok"] is False
        assert structured.get("error") is not None
        assert structured["error"]["code"] == "FILE_NOT_FOUND"

    def test_add_text_called_with_correct_args(self) -> None:
        """add_text must be called with timeline, output, and options arguments."""
        captured_calls: list[dict[str, Any]] = []

        def _capture(**kwargs: Any) -> ToolResult:
            captured_calls.append(kwargs)
            return _ok_tool_result()

        with patch("clipwright_text.server.add_text", side_effect=_capture):
            asyncio.run(
                mcp.call_tool(
                    "clipwright_add_text",
                    {
                        "timeline": "my_timeline.otio",
                        "output": "my_output.otio",
                        "options": {
                            "text": "Hello World",
                            "start_sec": 1.0,
                            "duration_sec": 3.0,
                        },
                    },
                )
            )

        assert len(captured_calls) == 1
        assert captured_calls[0].get("timeline") == "my_timeline.otio"
        assert captured_calls[0].get("output") == "my_output.otio"


# ---------------------------------------------------------------------------
# main() existence test
# ---------------------------------------------------------------------------


class TestCliMain:
    """Validate existence and callability of main()."""

    def test_main_is_callable(self) -> None:
        """main() function must exist and be callable."""
        assert callable(main)

    def test_main_exists_in_module(self) -> None:
        """main must be defined in the clipwright_text.server module."""
        import clipwright_text.server as server_module

        assert hasattr(server_module, "main")
        assert callable(server_module.main)

    def test_main_runs_mcp_server(self) -> None:
        """main() must call mcp.run (stdio launch)."""
        with patch.object(mcp, "run") as mock_run:
            main()

        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        assert kwargs.get("transport") == "stdio" or (
            len(_args) >= 1 and _args[0] == "stdio"
        )


# ---------------------------------------------------------------------------
# MCP wire contract
# ---------------------------------------------------------------------------


class TestMcpBoundary:
    """Validate MCP wire contract: outputSchema and structuredContent."""

    def test_outputschema_exposes_ok_property(self) -> None:
        """outputSchema must expose 'ok' property (typed ToolResult via FastMCP)."""
        tools = asyncio.run(mcp.list_tools())
        tool = next(t for t in tools if t.name == "clipwright_add_text")
        schema = tool.outputSchema or {}
        assert "ok" in schema.get("properties", {}), (
            "outputSchema must expose 'ok' property"
        )

    def test_structuredcontent_top_level_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_tool must return structuredContent with top-level 'ok' key."""
        monkeypatch.setattr(
            "clipwright_text.server.add_text",
            lambda **kw: _ok_tool_result(),
        )
        result = asyncio.run(
            mcp.call_tool(
                "clipwright_add_text",
                {
                    "timeline": "t.otio",
                    "output": "out.otio",
                    "options": {
                        "text": "Hello World",
                        "start_sec": 1.0,
                        "duration_sec": 3.0,
                    },
                },
            )
        )
        content, structured = result
        assert structured is not None
        assert "ok" in structured
        assert structured["ok"] is True
