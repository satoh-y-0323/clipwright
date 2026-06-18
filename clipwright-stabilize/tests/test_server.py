"""test_server.py — Tests for clipwright_stabilize.server (MCP registration + delegation).

Verification points:
  - Tool registered as clipwright_detect_shake.
  - annotations: readOnlyHint=False / destructiveHint=False / idempotentHint=True
    / openWorldHint=False (SR-NEW: .trf + .otio are generated as side-products).
  - options=None -> DetectShakeOptions() defaults applied (shakiness=5/accuracy=15/smoothing=30).
  - Delegates to stabilize.detect_shake.

Requirements: FR-1-6 (annotations), architecture-report §5 server.py.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from clipwright.schemas import Artifact, ToolResult
from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
    DetectShakeOptions,
)
from clipwright_stabilize.server import main, mcp  # type: ignore[import-not-found]

# ===========================================================================
# Helpers
# ===========================================================================


def _ok_tool_result(**kwargs: object) -> ToolResult:
    base = ToolResult(
        ok=True,
        summary=str(kwargs.get("summary", "ok")),
        data={},
        artifacts=[],
        warnings=[],
    )
    return base


def _get_tool_annotations() -> object:
    # FastMCP does not expose a public API for annotations, use _tool_manager.
    tool = mcp._tool_manager.get_tool("clipwright_detect_shake")  # noqa: SLF001
    assert tool is not None, "clipwright_detect_shake must be registered in mcp"
    return tool.annotations


# ===========================================================================
# MCP registration
# ===========================================================================


class TestMcpRegistration:
    """clipwright_detect_shake must be registered in MCP."""

    def test_tool_is_registered(self) -> None:
        """clipwright_detect_shake must exist in the MCP tool list."""
        tool = mcp._tool_manager.get_tool("clipwright_detect_shake")  # noqa: SLF001
        assert tool is not None, "clipwright_detect_shake is not registered in MCP."


# ===========================================================================
# MCP annotations (FR-1-6 / ADR-ST-4)
# ===========================================================================


class TestMcpAnnotations:
    """Verify MCP annotations for clipwright_detect_shake (FR-1-6)."""

    def test_read_only_hint_is_false(self) -> None:
        """readOnlyHint=False: .trf binary and .otio are generated as side-products."""
        annotations = _get_tool_annotations()
        assert annotations.readOnlyHint is False  # type: ignore[union-attr]

    def test_destructive_hint_is_false(self) -> None:
        """destructiveHint=False: not a destructive operation."""
        annotations = _get_tool_annotations()
        assert annotations.destructiveHint is False  # type: ignore[union-attr]

    def test_idempotent_hint_is_true(self) -> None:
        """idempotentHint=True: same input produces same output."""
        annotations = _get_tool_annotations()
        assert annotations.idempotentHint is True  # type: ignore[union-attr]

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False: no network access."""
        annotations = _get_tool_annotations()
        assert annotations.openWorldHint is False  # type: ignore[union-attr]


# ===========================================================================
# Delegation to stabilize.detect_shake
# ===========================================================================


class TestDelegation:
    """clipwright_detect_shake must delegate to stabilize.detect_shake."""

    def test_success_delegates_to_detect_shake(self) -> None:
        """detect_shake must be called on success and its result returned."""
        with patch(
            "clipwright_stabilize.server.detect_shake",
            return_value=_ok_tool_result(summary="done"),
        ) as mock_fn:
            _content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_detect_shake",
                    {"media": "in.mp4", "output": "out.otio"},
                )
            )

        mock_fn.assert_called_once()
        assert structured["ok"] is True
        assert structured.get("summary") == "done"

    def test_error_result_propagates(self) -> None:
        """An error ToolResult returned by detect_shake must propagate as-is."""
        from clipwright.schemas import ToolError

        error_tr = ToolResult(
            ok=False,
            error=ToolError(
                code="INVALID_INPUT",
                message="test error",
                hint="test hint",
            ),
        )
        with patch(
            "clipwright_stabilize.server.detect_shake",
            return_value=error_tr,
        ):
            _content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_detect_shake",
                    {"media": "in.mp4", "output": "out.otio"},
                )
            )

        assert structured["ok"] is False
        assert structured.get("error") is not None
        assert structured["error"]["code"] == "INVALID_INPUT"

    def test_media_and_output_forwarded(self) -> None:
        """media / output must be correctly forwarded to detect_shake."""
        with patch(
            "clipwright_stabilize.server.detect_shake",
            return_value=_ok_tool_result(),
        ) as mock_fn:
            asyncio.run(
                mcp.call_tool(
                    "clipwright_detect_shake",
                    {
                        "media": "/path/to/video.mp4",
                        "output": "/path/to/out.otio",
                    },
                )
            )

        _args, kwargs = mock_fn.call_args
        assert kwargs.get("media") == "/path/to/video.mp4"
        assert kwargs.get("output") == "/path/to/out.otio"

    def test_timeline_forwarded_when_specified(self) -> None:
        """The timeline argument must be correctly forwarded to detect_shake."""
        with patch(
            "clipwright_stabilize.server.detect_shake",
            return_value=_ok_tool_result(),
        ) as mock_fn:
            asyncio.run(
                mcp.call_tool(
                    "clipwright_detect_shake",
                    {
                        "media": "in.mp4",
                        "output": "out.otio",
                        "timeline": "existing.otio",
                    },
                )
            )

        _args, kwargs = mock_fn.call_args
        assert kwargs.get("timeline") == "existing.otio"

    def test_timeline_none_is_forwarded(self) -> None:
        """timeline=None must be the default when omitted."""
        with patch(
            "clipwright_stabilize.server.detect_shake",
            return_value=_ok_tool_result(),
        ) as mock_fn:
            asyncio.run(
                mcp.call_tool(
                    "clipwright_detect_shake",
                    {"media": "in.mp4", "output": "out.otio"},
                )
            )

        _args, kwargs = mock_fn.call_args
        assert kwargs.get("timeline") is None


# ===========================================================================
# Default value when options=None
# ===========================================================================


class TestOptionsDefault:
    """When options=None, DetectShakeOptions() defaults must be used."""

    def test_options_none_uses_default_detect_shake_options(self) -> None:
        """options=None -> shakiness=5 / accuracy=15 / smoothing=30 defaults passed."""
        with patch(
            "clipwright_stabilize.server.detect_shake",
            return_value=_ok_tool_result(),
        ) as mock_fn:
            asyncio.run(
                mcp.call_tool(
                    "clipwright_detect_shake",
                    {"media": "in.mp4", "output": "out.otio"},
                )
            )

        _args, kwargs = mock_fn.call_args
        passed = kwargs.get("options")
        assert isinstance(passed, DetectShakeOptions), (
            f"options is not DetectShakeOptions: {type(passed)}"
        )
        assert passed.shakiness == 5
        assert passed.accuracy == 15
        assert passed.smoothing == 30

    def test_options_explicit_is_forwarded(self) -> None:
        """An explicitly specified options value must be forwarded as-is."""
        with patch(
            "clipwright_stabilize.server.detect_shake",
            return_value=_ok_tool_result(),
        ) as mock_fn:
            asyncio.run(
                mcp.call_tool(
                    "clipwright_detect_shake",
                    {
                        "media": "in.mp4",
                        "output": "out.otio",
                        "options": {"shakiness": 8, "accuracy": 12, "smoothing": 60},
                    },
                )
            )

        _args, kwargs = mock_fn.call_args
        passed = kwargs.get("options")
        assert isinstance(passed, DetectShakeOptions) and passed.shakiness == 8


# ===========================================================================
# main() — stdio launch
# ===========================================================================


class TestCliMain:
    """main() must launch the MCP server over stdio."""

    def test_main_runs_mcp_with_stdio_transport(self) -> None:
        """main() must call mcp.run(transport='stdio')."""
        with patch.object(mcp, "run") as mock_run:
            main()

        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        assert kwargs.get("transport") == "stdio" or (
            len(_args) >= 1 and _args[0] == "stdio"
        ), f"transport='stdio' was not passed. args={_args}, kwargs={kwargs}"


# ===========================================================================
# MCP boundary: outputSchema and structuredContent
# ===========================================================================


class TestMcpBoundary:
    def test_outputschema_is_typed(self) -> None:
        """outputSchema must expose 'ok' property (FastMCP typed return)."""
        tools = asyncio.run(mcp.list_tools())
        tool = next(t for t in tools if t.name == "clipwright_detect_shake")
        schema = tool.outputSchema or {}
        assert "ok" in schema.get("properties", {}), (
            "outputSchema must expose 'ok' property"
        )

    def test_structuredcontent_top_level_ok(self, monkeypatch: object) -> None:
        """call_tool must return structuredContent with top-level ok=True on success."""
        monkeypatch.setattr(  # type: ignore[union-attr]
            "clipwright_stabilize.server.detect_shake",
            lambda **kw: ToolResult(
                ok=True,
                summary="Shake analysis complete.",
                data={},
                artifacts=[
                    Artifact(role="timeline", path="out.otio", format="otio"),
                    Artifact(role="analysis", path="out.stabilize.trf", format="trf"),
                ],
                warnings=[],
            ),
        )
        result = asyncio.run(
            mcp.call_tool(
                "clipwright_detect_shake",
                {"media": "m.mp4", "output": "out.otio"},
            )
        )
        content, structured = result
        assert structured is not None
        assert "ok" in structured
        assert structured["ok"] is True
