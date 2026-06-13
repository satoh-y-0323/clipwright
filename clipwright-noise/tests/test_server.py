"""test_server.py — Full tests for server.py (MCP + CLI).

Target:
  - clipwright_detect_noise is registered with MCP
  - annotations: readOnlyHint=True / destructiveHint=False / idempotentHint=True / openWorldHint=False
  - When options=None, the default DetectNoiseOptions() is passed to the delegate
  - timeline=None is the default
  - Delegates to noise.detect_noise
  - main() calls mcp.run(transport="stdio")
  - outputSchema exposes 'ok' property (MCP boundary contract)
  - structuredContent has top-level 'ok' (no FastMCP union wrapping)
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from clipwright.schemas import Artifact, ToolError, ToolResult

from clipwright_noise.schemas import DetectNoiseOptions
from clipwright_noise.server import clipwright_detect_noise as server_action
from clipwright_noise.server import main, mcp

# ===========================================================================
# Helpers
# ===========================================================================


def _ok_tool_result(**kwargs: object) -> ToolResult:
    """Helper to generate a test ok ToolResult."""
    return ToolResult(
        ok=True,
        summary="Noise detected.",
        data={},
        artifacts=[Artifact(role="timeline", path="output.otio", format="otio")],
        warnings=[],
    )


def _get_tool_annotations() -> Any:
    # FastMCP has no public API for retrieving registered tools,
    # so the private attribute _tool_manager is used for testing purposes.
    tool = mcp._tool_manager.get_tool("clipwright_detect_noise")  # noqa: SLF001
    assert tool is not None, "clipwright_detect_noise must be registered with mcp"
    return tool.annotations


# ===========================================================================
# MCP registration and annotations verification
# ===========================================================================


class TestMcpRegistration:
    """clipwright_detect_noise must be correctly registered with MCP."""

    def test_tool_is_registered(self) -> None:
        """clipwright_detect_noise must be present in the MCP tool list."""
        # FastMCP has no public API for retrieving registered tools; using private attribute.
        tool = mcp._tool_manager.get_tool("clipwright_detect_noise")  # noqa: SLF001
        assert tool is not None, "clipwright_detect_noise is not registered with MCP."


class TestMcpAnnotations:
    """MCP annotations check for detect-type tools (design §2.4)."""

    def test_read_only_hint_is_true(self) -> None:
        """readOnlyHint=True: input media is not modified."""
        annotations = _get_tool_annotations()
        assert annotations.readOnlyHint is True

    def test_destructive_hint_is_false(self) -> None:
        """destructiveHint=False: not a destructive operation."""
        annotations = _get_tool_annotations()
        assert annotations.destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        """idempotentHint=True: same input produces same output."""
        annotations = _get_tool_annotations()
        assert annotations.idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False: no network access."""
        annotations = _get_tool_annotations()
        assert annotations.openWorldHint is False


# ===========================================================================
# Delegation (to detect_noise)
# ===========================================================================


class TestDelegation:
    """clipwright_detect_noise must correctly delegate to noise.detect_noise."""

    def test_success_delegates_to_detect_noise(self) -> None:
        """detect_noise must be called on success and the result must be returned."""
        with patch(
            "clipwright_noise.server.detect_noise",
            return_value=_ok_tool_result(summary="done"),
        ) as mock_fn:
            result = server_action(
                media="in.mp4", output="out.otio", options=None, timeline=None
            )

        mock_fn.assert_called_once()
        assert result.ok is True
        assert result.summary == "Noise detected."

    def test_error_result_propagates(self) -> None:
        """When detect_noise returns an error envelope, it must propagate as-is."""
        error_result = ToolResult(
            ok=False,
            error=ToolError(
                code="INVALID_INPUT",
                message="test error",
                hint="test hint",
            ),
        )
        with patch(
            "clipwright_noise.server.detect_noise",
            return_value=error_result,
        ):
            result = server_action(
                media="in.mp4", output="out.otio", options=None, timeline=None
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "INVALID_INPUT"

    def test_media_and_output_forwarded(self) -> None:
        """media / output must be correctly forwarded to detect_noise."""
        with patch(
            "clipwright_noise.server.detect_noise",
            return_value=_ok_tool_result(),
        ) as mock_fn:
            server_action(
                media="/path/to/video.mp4",
                output="/path/to/out.otio",
                options=None,
                timeline=None,
            )

        _args, kwargs = mock_fn.call_args
        assert kwargs.get("media") == "/path/to/video.mp4"
        assert kwargs.get("output") == "/path/to/out.otio"

    def test_timeline_forwarded_when_specified(self) -> None:
        """The timeline argument must be correctly forwarded to detect_noise."""
        with patch(
            "clipwright_noise.server.detect_noise",
            return_value=_ok_tool_result(),
        ) as mock_fn:
            server_action(
                media="in.mp4",
                output="out.otio",
                options=None,
                timeline="existing.otio",
            )

        _args, kwargs = mock_fn.call_args
        assert kwargs.get("timeline") == "existing.otio"

    def test_timeline_none_is_forwarded(self) -> None:
        """timeline=None must be forwarded to detect_noise (default when omitted)."""
        with patch(
            "clipwright_noise.server.detect_noise",
            return_value=_ok_tool_result(),
        ) as mock_fn:
            server_action(
                media="in.mp4", output="out.otio", options=None, timeline=None
            )

        _args, kwargs = mock_fn.call_args
        assert kwargs.get("timeline") is None


# ===========================================================================
# Default values when options=None
# ===========================================================================


class TestOptionsDefault:
    """When options=None, DetectNoiseOptions() must be used."""

    def test_options_none_uses_default_detect_noise_options(self) -> None:
        """options=None → default backend=afftdn / strength=medium must be passed."""
        with patch(
            "clipwright_noise.server.detect_noise",
            return_value=_ok_tool_result(),
        ) as mock_fn:
            server_action(
                media="in.mp4", output="out.otio", options=None, timeline=None
            )

        _args, kwargs = mock_fn.call_args
        passed = kwargs.get("options")
        assert isinstance(passed, DetectNoiseOptions), (
            f"options is not DetectNoiseOptions: {type(passed)}"
        )
        assert passed.backend == "afftdn"
        assert passed.strength == "medium"

    def test_options_explicit_is_forwarded(self) -> None:
        """When options is explicitly specified, it must be forwarded as-is."""
        custom_opts = DetectNoiseOptions(backend="deepfilternet", strength="strong")
        with patch(
            "clipwright_noise.server.detect_noise",
            return_value=_ok_tool_result(),
        ) as mock_fn:
            server_action(
                media="in.mp4", output="out.otio", options=custom_opts, timeline=None
            )

        _args, kwargs = mock_fn.call_args
        passed = kwargs.get("options")
        assert passed is custom_opts or (
            isinstance(passed, DetectNoiseOptions)
            and passed.backend == "deepfilternet"
            and passed.strength == "strong"
        )


# ===========================================================================
# Test scope: MCP outputSchema and structuredContent
# ===========================================================================


class TestMcpBoundary:
    """FastMCP must expose a typed outputSchema and return structuredContent."""

    def test_outputschema_is_typed(self) -> None:
        """outputSchema must expose 'ok' property when return type is ToolResult."""
        tools = asyncio.run(mcp.list_tools())
        tool = next(t for t in tools if t.name == "clipwright_detect_noise")
        schema = tool.outputSchema or {}
        assert "ok" in schema.get("properties", {}), (
            "outputSchema must expose 'ok' property"
        )

    def test_structuredcontent_top_level_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_tool must return structuredContent with top-level 'ok'."""
        monkeypatch.setattr(
            "clipwright_noise.server.detect_noise",
            lambda **kw: ToolResult(
                ok=True,
                summary="Noise detected.",
                data={},
                artifacts=[Artifact(role="timeline", path="out.otio", format="otio")],
                warnings=[],
            ),
        )
        result = asyncio.run(
            mcp.call_tool(
                "clipwright_detect_noise",
                {"media": "in.mp4", "output": "out.otio"},
            )
        )
        content, structured = result
        assert structured is not None, "structuredContent must not be None"
        assert "ok" in structured, "structuredContent must have top-level 'ok'"
        assert structured["ok"] is True


# ===========================================================================
# main() — stdio launch
# ===========================================================================


class TestCliMain:
    """main() must launch the MCP server over stdio."""

    def test_main_runs_mcp_with_stdio_transport(self) -> None:
        """main() must call mcp.run(transport="stdio")."""
        with patch.object(mcp, "run") as mock_run:
            main()

        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        assert kwargs.get("transport") == "stdio" or (
            len(_args) >= 1 and _args[0] == "stdio"
        ), f"transport='stdio' was not passed. args={_args}, kwargs={kwargs}"
