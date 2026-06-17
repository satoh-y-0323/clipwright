"""test_server.py — Tests for clipwright-speed server.py MCP boundary.

Target:
  - clipwright_set_speed tool registered in MCP, delegates to speed.set_speed
  - MCP annotations:
    readOnlyHint=False / destructiveHint=False / idempotentHint=True /
    openWorldHint=False
  - options=None -> error_result with INVALID_INPUT (speed is required, no default)
  - Success envelope shape: ok/summary/data{applied_count,speed,clip_indices}/
    artifacts[{role:"timeline", format:"otio"}]
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
    from clipwright_speed.server import main, mcp

    _SERVER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _SERVER_AVAILABLE = False

# Mark all tests as xfail unless server.py is available
pytestmark = pytest.mark.xfail(
    not _SERVER_AVAILABLE,
    reason="server.py is not implemented",
    strict=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_tool_result(**kwargs: Any) -> ToolResult:
    """Return a success ToolResult template."""
    defaults: dict[str, Any] = {
        "ok": True,
        "summary": "Applied speed 2.0 to 2 clip(s). Output: out.otio.",
        "data": {
            "applied_count": 2,
            "speed": 2.0,
            "clip_indices": [0, 1],
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
# MCP annotations tests
# ---------------------------------------------------------------------------


class TestMcpAnnotations:
    """Validate MCP annotations for the clipwright_set_speed tool.

    readOnlyHint=False (writes a new OTIO file; input is never modified)
    destructiveHint=False (input timeline is non-destructive)
    idempotentHint=True (same input + same options -> same output timeline)
    openWorldHint=False (local filesystem only, no network access)
    """

    def _get_annotations(self) -> Any:
        # CR L-1: No public API available in FastMCP to retrieve tool info,
        # so relying on the private API (_tool_manager).
        tool = mcp._tool_manager.get_tool("clipwright_set_speed")  # noqa: SLF001
        assert tool is not None, "clipwright_set_speed must be registered in mcp"
        return tool.annotations

    def test_tool_is_registered(self) -> None:
        """clipwright_set_speed must be registered in mcp."""
        tool = mcp._tool_manager.get_tool("clipwright_set_speed")  # noqa: SLF001
        assert tool is not None

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
# options=None behavior (speed is required)
# ---------------------------------------------------------------------------


class TestOptionsNone:
    """When options=None, server must return INVALID_INPUT (speed is required)."""

    def test_options_none_returns_invalid_input(self) -> None:
        """options=None must return ok=False with INVALID_INPUT (speed is required)."""
        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_set_speed",
                {"timeline": "test.otio", "output": "out.otio"},
            )
        )
        assert structured["ok"] is False
        error = structured.get("error") or {}
        assert error.get("code") == "INVALID_INPUT"
        assert error.get("hint"), "hint must be non-empty"

    def test_options_none_not_a_default_success(self) -> None:
        """options=None must never succeed (speed is required, no sensible default)."""
        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_set_speed",
                {"timeline": "test.otio", "output": "out.otio"},
            )
        )
        # Must not be ok=True
        assert structured["ok"] is not True


# ---------------------------------------------------------------------------
# Success envelope shape
# ---------------------------------------------------------------------------


class TestSuccessEnvelopeShape:
    """Validate the success envelope shape returned via MCP."""

    def test_success_envelope_ok_summary_data_artifacts(self) -> None:
        """Success envelope must contain ok/summary/data/artifacts."""
        expected = _ok_tool_result()

        with patch(
            "clipwright_speed.server.set_speed",
            return_value=expected,
        ):
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_set_speed",
                    {
                        "timeline": "test.otio",
                        "output": "out.otio",
                        "options": {"speed": 2.0},
                    },
                )
            )

        assert structured["ok"] is True
        assert structured.get("summary"), "summary must be non-empty"
        data = structured.get("data", {})
        assert "applied_count" in data, "data must have applied_count"
        assert "speed" in data, "data must have speed"
        assert "clip_indices" in data, "data must have clip_indices"
        artifacts = structured.get("artifacts", [])
        assert len(artifacts) >= 1, "artifacts must be non-empty"

    def test_success_data_applied_count(self) -> None:
        """data.applied_count must be present in the success envelope."""
        expected = _ok_tool_result()
        with patch("clipwright_speed.server.set_speed", return_value=expected):
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_set_speed",
                    {
                        "timeline": "test.otio",
                        "output": "out.otio",
                        "options": {"speed": 2.0},
                    },
                )
            )
        assert structured["data"]["applied_count"] == 2

    def test_success_data_speed(self) -> None:
        """data.speed must match the requested speed."""
        expected = _ok_tool_result()
        with patch("clipwright_speed.server.set_speed", return_value=expected):
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_set_speed",
                    {
                        "timeline": "test.otio",
                        "output": "out.otio",
                        "options": {"speed": 2.0},
                    },
                )
            )
        assert structured["data"]["speed"] == pytest.approx(2.0)

    def test_success_data_clip_indices_is_list(self) -> None:
        """data.clip_indices must be a list."""
        expected = _ok_tool_result()
        with patch("clipwright_speed.server.set_speed", return_value=expected):
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_set_speed",
                    {
                        "timeline": "test.otio",
                        "output": "out.otio",
                        "options": {"speed": 2.0},
                    },
                )
            )
        assert isinstance(structured["data"]["clip_indices"], list)

    def test_success_artifacts_timeline_entry(self) -> None:
        """artifacts must contain entry with role='timeline' and format='otio'."""
        expected = _ok_tool_result()
        with patch("clipwright_speed.server.set_speed", return_value=expected):
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_set_speed",
                    {
                        "timeline": "test.otio",
                        "output": "out.otio",
                        "options": {"speed": 2.0},
                    },
                )
            )
        artifacts = structured.get("artifacts", [])
        tl_artifact = next((a for a in artifacts if a.get("role") == "timeline"), None)
        assert tl_artifact is not None, "artifacts must contain role='timeline'"
        assert tl_artifact.get("format") == "otio"


# ---------------------------------------------------------------------------
# MCP tool delegation
# ---------------------------------------------------------------------------


class TestMcpToolDelegation:
    """Validate delegation to speed.set_speed and envelope passthrough."""

    def test_success_delegates_to_set_speed(self) -> None:
        """On success, must call and delegate to set_speed."""
        expected = _ok_tool_result()

        with patch(
            "clipwright_speed.server.set_speed",
            return_value=expected,
        ) as mock_set_speed:
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_set_speed",
                    {
                        "timeline": "test.otio",
                        "output": "out.otio",
                        "options": {"speed": 2.0},
                    },
                )
            )

        mock_set_speed.assert_called_once()
        assert structured["ok"] is True

    def test_failure_envelope_returned_as_is(self) -> None:
        """When set_speed returns an error envelope, server returns it unchanged."""
        expected = _error_tool_result("FILE_NOT_FOUND")

        with patch(
            "clipwright_speed.server.set_speed",
            return_value=expected,
        ):
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_set_speed",
                    {
                        "timeline": "missing.otio",
                        "output": "out.otio",
                        "options": {"speed": 2.0},
                    },
                )
            )

        assert structured["ok"] is False
        assert structured.get("error") is not None
        assert structured["error"]["code"] == "FILE_NOT_FOUND"

    def test_set_speed_called_with_correct_args(self) -> None:
        """set_speed must be called with timeline, output, and options arguments."""
        captured_calls: list[dict[str, Any]] = []

        def _capture(**kwargs: Any) -> ToolResult:
            captured_calls.append(kwargs)
            return _ok_tool_result()

        with patch("clipwright_speed.server.set_speed", side_effect=_capture):
            asyncio.run(
                mcp.call_tool(
                    "clipwright_set_speed",
                    {
                        "timeline": "my_timeline.otio",
                        "output": "my_output.otio",
                        "options": {"speed": 1.5},
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
        """main must be defined in the clipwright_speed.server module."""
        import clipwright_speed.server as server_module

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
        tool = next(t for t in tools if t.name == "clipwright_set_speed")
        schema = tool.outputSchema or {}
        assert "ok" in schema.get("properties", {}), (
            "outputSchema must expose 'ok' property"
        )

    def test_structuredcontent_top_level_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_tool must return structuredContent with top-level 'ok' key."""
        monkeypatch.setattr(
            "clipwright_speed.server.set_speed",
            lambda **kw: _ok_tool_result(),
        )
        result = asyncio.run(
            mcp.call_tool(
                "clipwright_set_speed",
                {
                    "timeline": "t.otio",
                    "output": "out.otio",
                    "options": {"speed": 2.0},
                },
            )
        )
        content, structured = result
        assert structured is not None
        assert "ok" in structured
        assert structured["ok"] is True
