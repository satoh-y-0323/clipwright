"""test_server.py — Full test suite for server.py (MCP + CLI).

Targets:
  - clipwright_detect_loudness is registered in MCP
  - annotations: readOnlyHint=True / destructiveHint=False / idempotentHint=True / openWorldHint=False
  - When options=None, default DetectLoudnessOptions() is passed to the delegate
  - timeline=None is the default
  - Delegates to loudness.detect_loudness
  - main() calls mcp.run(transport="stdio")
  - outputSchema exposes 'ok' property (MCP boundary)
  - structuredContent top-level 'ok' is True on success (MCP boundary)
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from clipwright.schemas import Artifact, ToolResult

from clipwright_loudness.schemas import DetectLoudnessOptions
from clipwright_loudness.server import clipwright_detect_loudness as server_action
from clipwright_loudness.server import main, mcp

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
    # FastMCP does not expose a public API to retrieve registered tools,
    # so we access the private _tool_manager attribute for test purposes.
    tool = mcp._tool_manager.get_tool("clipwright_detect_loudness")  # noqa: SLF001
    assert tool is not None, "clipwright_detect_loudness must be registered in mcp"
    return tool.annotations


# ===========================================================================
# MCP registration and annotations
# ===========================================================================


class TestMcpRegistration:
    """clipwright_detect_loudness must be correctly registered in MCP."""

    def test_tool_is_registered(self) -> None:
        """clipwright_detect_loudness must exist in the MCP tool list."""
        tool = mcp._tool_manager.get_tool("clipwright_detect_loudness")  # noqa: SLF001
        assert tool is not None, "clipwright_detect_loudness is not registered in MCP."


class TestMcpAnnotations:
    """Verify MCP annotations for the detect tool (design §2.4, project-conventions.md)."""

    def test_read_only_hint_is_true(self) -> None:
        """readOnlyHint=True: input media is not modified."""
        annotations = _get_tool_annotations()
        assert annotations.readOnlyHint is True  # type: ignore[union-attr]

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
# Delegation (to detect_loudness)
# ===========================================================================


class TestDelegation:
    """clipwright_detect_loudness must correctly delegate to loudness.detect_loudness."""

    def test_success_delegates_to_detect_loudness(self) -> None:
        """detect_loudness must be called on success and its result returned."""
        with patch(
            "clipwright_loudness.server.detect_loudness",
            return_value=_ok_tool_result(summary="done"),
        ) as mock_fn:
            result = server_action(
                media="in.mp4", output="out.otio", options=None, timeline=None
            )

        mock_fn.assert_called_once()
        assert result.ok is True
        assert result.summary == "done"

    def test_error_result_propagates(self) -> None:
        """An error ToolResult returned by detect_loudness must propagate as-is."""
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
            "clipwright_loudness.server.detect_loudness",
            return_value=error_tr,
        ):
            result = server_action(
                media="in.mp4", output="out.otio", options=None, timeline=None
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "INVALID_INPUT"

    def test_media_and_output_forwarded(self) -> None:
        """media / output must be correctly forwarded to detect_loudness."""
        with patch(
            "clipwright_loudness.server.detect_loudness",
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
        """The timeline argument must be correctly forwarded to detect_loudness."""
        with patch(
            "clipwright_loudness.server.detect_loudness",
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
        """timeline=None must be forwarded to detect_loudness (default when omitted)."""
        with patch(
            "clipwright_loudness.server.detect_loudness",
            return_value=_ok_tool_result(),
        ) as mock_fn:
            server_action(
                media="in.mp4", output="out.otio", options=None, timeline=None
            )

        _args, kwargs = mock_fn.call_args
        assert kwargs.get("timeline") is None


# ===========================================================================
# Default value when options=None
# ===========================================================================


class TestOptionsDefault:
    """When options=None, DetectLoudnessOptions() defaults must be used."""

    def test_options_none_uses_default_detect_loudness_options(self) -> None:
        """options=None -> mode=loudnorm / scope=track defaults must be passed."""
        with patch(
            "clipwright_loudness.server.detect_loudness",
            return_value=_ok_tool_result(),
        ) as mock_fn:
            server_action(
                media="in.mp4", output="out.otio", options=None, timeline=None
            )

        _args, kwargs = mock_fn.call_args
        passed = kwargs.get("options")
        assert isinstance(passed, DetectLoudnessOptions), (
            f"options is not DetectLoudnessOptions: {type(passed)}"
        )
        assert passed.mode == "loudnorm"
        assert passed.scope == "track"

    def test_options_explicit_is_forwarded(self) -> None:
        """An explicitly specified options value must be forwarded as-is."""
        custom_opts = DetectLoudnessOptions(mode="peak", scope="track")
        with patch(
            "clipwright_loudness.server.detect_loudness",
            return_value=_ok_tool_result(),
        ) as mock_fn:
            server_action(
                media="in.mp4", output="out.otio", options=custom_opts, timeline=None
            )

        _args, kwargs = mock_fn.call_args
        passed = kwargs.get("options")
        assert passed is custom_opts or (
            isinstance(passed, DetectLoudnessOptions) and passed.mode == "peak"
        )


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


# ===========================================================================
# MCP boundary: outputSchema and structuredContent
# ===========================================================================


class TestMcpBoundary:
    def test_outputschema_is_typed(self) -> None:
        """outputSchema must expose 'ok' property (FastMCP typed return)."""
        tools = asyncio.run(mcp.list_tools())
        tool = next(t for t in tools if t.name == "clipwright_detect_loudness")
        schema = tool.outputSchema or {}
        assert "ok" in schema.get("properties", {}), (
            "outputSchema must expose 'ok' property"
        )

    def test_structuredcontent_top_level_ok(self, monkeypatch: object) -> None:
        """call_tool must return structuredContent with top-level ok=True on success."""
        monkeypatch.setattr(  # type: ignore[union-attr]
            "clipwright_loudness.server.detect_loudness",
            lambda **kw: ToolResult(
                ok=True,
                summary="Loudness detected.",
                data={},
                artifacts=[Artifact(role="timeline", path="out.otio", format="otio")],
                warnings=[],
            ),
        )
        result = asyncio.run(
            mcp.call_tool(
                "clipwright_detect_loudness",
                {"media": "m.mp4", "output": "out.otio"},
            )
        )
        content, structured = result
        assert structured is not None
        assert "ok" in structured
        assert structured["ok"] is True
