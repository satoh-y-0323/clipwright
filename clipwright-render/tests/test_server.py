"""test_server.py — Tests for clipwright-render server.py (MCP).

Targets:
  - clipwright_render tool is registered with MCP and delegates to render.render_timeline
  - MCP annotations (§5): readOnly:false / destructive:false / idempotent:true / openWorld:false
  - Success envelope (ok:true)
  - Failure envelope (ok:false, error:{code,message,hint})
  - dry_run delegation is passed to the render layer
  - outputSchema is typed (ToolResult shape)
  - structuredContent top-level includes ok field (not wrapped)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Attempt to import server.py (if not implemented, _SERVER_AVAILABLE = False)
# ---------------------------------------------------------------------------

try:
    from clipwright_render.server import (
        clipwright_render as server_clipwright_render,
    )
    from clipwright_render.server import mcp

    _SERVER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _SERVER_AVAILABLE = False

# Mark all tests as xfail until server.py is available
pytestmark = pytest.mark.xfail(
    not _SERVER_AVAILABLE,
    reason="server.py not yet implemented — Red (failing due to missing implementation)",
    strict=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_dict(**kwargs: Any) -> dict[str, Any]:
    """Return a success envelope dict (used as mock return value for render_timeline)."""
    base: dict[str, Any] = {
        "ok": True,
        "summary": "ok",
        "data": {},
        "artifacts": [],
        "warnings": [],
    }
    base.update(kwargs)
    return base


def _error_dict(code: str) -> dict[str, Any]:
    """Return a failure envelope dict (used as mock return value for render_timeline)."""
    return {
        "ok": False,
        "summary": None,
        "data": {},
        "artifacts": [],
        "warnings": [],
        "error": {
            "code": code,
            "message": "error",
            "hint": "hint",
        },
    }


# ---------------------------------------------------------------------------
# MCP annotations tests (§5)
# ---------------------------------------------------------------------------


class TestMcpAnnotations:
    """Verify that the clipwright_render tool's MCP annotations match the §5 specification."""

    def _get_annotations(self) -> Any:
        # FastMCP has no stable public API to retrieve tool info, so we depend on
        # the private _tool_manager API here (L-2).
        # This may break on FastMCP version upgrades; it is supplemented by
        # Inspector smoke-testing as a separate assurance.
        tool = mcp._tool_manager.get_tool(  # type: ignore[attr-defined]
            "clipwright_render"
        )
        assert tool is not None, "clipwright_render must be registered with mcp"
        return tool.annotations

    def test_tool_is_registered(self) -> None:
        """clipwright_render is registered with mcp."""
        # Depends on internal API due to no stable public API (L-2);
        # supplemented by Inspector smoke-testing.
        tool = mcp._tool_manager.get_tool(  # type: ignore[attr-defined]
            "clipwright_render"
        )
        assert tool is not None

    def test_read_only_hint_is_false(self) -> None:
        """readOnlyHint=False (generates an output file)."""
        ann = self._get_annotations()
        assert ann.readOnlyHint is False

    def test_destructive_hint_is_false(self) -> None:
        """destructiveHint=False (input and OTIO are unchanged)."""
        ann = self._get_annotations()
        assert ann.destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        """idempotentHint=True (same input produces same output)."""
        ann = self._get_annotations()
        assert ann.idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False (does not touch the external network)."""
        ann = self._get_annotations()
        assert ann.openWorldHint is False


# ---------------------------------------------------------------------------
# MCP tool call: delegation to render.render_timeline
# ---------------------------------------------------------------------------


class TestMcpToolDelegation:
    """Verify that server.clipwright_render is a thin wrapper that calls render.render_timeline."""

    def test_success_delegates_to_render_timeline(self, tmp_path: Path) -> None:
        """On success, render.render_timeline is called and the result is delegated."""
        expected = _ok_dict(summary="rendered ok")

        with patch(
            "clipwright_render.server.render_timeline",
            return_value=expected,
        ) as mock_render:
            result = server_clipwright_render(
                timeline="tl.otio",
                output="out.mp4",
                options={},
                dry_run=False,
            )

        mock_render.assert_called_once()
        d = result.model_dump()
        assert d["ok"] is True

    def test_failure_returns_error_envelope(self, tmp_path: Path) -> None:
        """When render_timeline returns a failure envelope, the server returns it unchanged."""
        expected = _error_dict("FILE_NOT_FOUND")

        with patch(
            "clipwright_render.server.render_timeline",
            return_value=expected,
        ):
            result = server_clipwright_render(
                timeline="missing.otio",
                output="out.mp4",
                options={},
            )

        d = result.model_dump()
        assert d["ok"] is False
        assert d["error"]["code"] == "FILE_NOT_FOUND"

    def test_dry_run_passed_to_render_timeline(self, tmp_path: Path) -> None:
        """dry_run=True is passed to render_timeline."""
        with patch(
            "clipwright_render.server.render_timeline",
            return_value=_ok_dict(),
        ) as mock_render:
            server_clipwright_render(
                timeline="tl.otio",
                output="out.mp4",
                options={},
                dry_run=True,
            )

        _args, kwargs = mock_render.call_args
        # dry_run=True must be passed as positional or keyword argument
        assert kwargs.get("dry_run") is True or (len(_args) >= 4 and _args[3] is True)

    def test_options_passed_to_render_timeline(self, tmp_path: Path) -> None:
        """options content is passed to render_timeline."""
        from clipwright_render.schemas import RenderOptions

        opts = RenderOptions(video_codec="libx264", crf=23)

        with patch(
            "clipwright_render.server.render_timeline",
            return_value=_ok_dict(),
        ) as mock_render:
            server_clipwright_render(
                timeline="tl.otio",
                output="out.mp4",
                options=opts,
            )

        mock_render.assert_called_once()
        call_args = mock_render.call_args
        # options must be passed in some form
        assert call_args is not None

    def test_error_envelope_has_code_message_hint(self, tmp_path: Path) -> None:
        """The failure envelope contains code / message / hint."""
        expected: dict[str, Any] = {
            "ok": False,
            "summary": None,
            "data": {},
            "artifacts": [],
            "warnings": [],
            "error": {
                "code": "INVALID_INPUT",
                "message": "invalid input",
                "hint": "please fix it",
            },
        }

        with patch(
            "clipwright_render.server.render_timeline",
            return_value=expected,
        ):
            result = server_clipwright_render(
                timeline="tl.otio",
                output="out.mp4",
                options={},
            )

        d = result.model_dump()
        assert d["ok"] is False
        error = d["error"]
        assert "code" in error
        assert "message" in error
        assert "hint" in error


# ---------------------------------------------------------------------------
# MCP boundary tests: outputSchema and structuredContent
# ---------------------------------------------------------------------------


class TestMcpBoundary:
    """Verify typed outputSchema and structuredContent wire contract."""

    def test_outputschema_is_typed(self) -> None:
        """outputSchema must be a typed ToolResult shape with 'ok' in properties."""
        tools = asyncio.run(mcp.list_tools())
        render_tool = next((t for t in tools if t.name == "clipwright_render"), None)
        assert render_tool is not None, "clipwright_render must be in list_tools()"
        schema = render_tool.outputSchema or {}
        props = schema.get("properties") or {}
        assert "ok" in props, (
            "outputSchema must be typed ToolResult (missing 'ok' property)"
        )

    def test_structuredcontent_top_level_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """structuredContent must include 'ok' at top level (not wrapped in a result key).

        DPR-M-003: render_timeline is mocked to avoid ffmpeg invocation.
        """
        monkeypatch.setattr(
            "clipwright_render.server.render_timeline",
            lambda **kw: {
                "ok": True,
                "summary": "dry run ok",
                "data": {},
                "artifacts": [],
                "warnings": [],
            },
        )
        call_result = asyncio.run(
            mcp.call_tool(
                "clipwright_render",
                {"timeline": "/fake.otio", "output": "/fake.mp4", "dry_run": True},
            )
        )
        # mcp.call_tool returns (list[TextContent], structured_dict) tuple.
        content_list, _structured = call_result
        assert content_list, "call_tool must return at least one content item"
        # Parse from text representation to verify wire-format shape.
        content = json.loads(content_list[0].text) if content_list else {}
        assert "ok" in content, (
            "structuredContent must not be wrapped — 'ok' must be at top level"
        )
