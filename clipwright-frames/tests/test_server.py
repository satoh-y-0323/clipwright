"""test_server.py — Tests for clipwright-frames server.py MCP boundary.

Target:
  - clipwright_extract_frames tool registered in MCP, delegates to extract.extract_frames
  - MCP annotations:
    readOnlyHint=False / destructiveHint=False / idempotentHint=True / openWorldHint=False
    (readOnlyHint=False because the tool writes frames and artifacts to output_dir)
  - options=None -> ExtractFramesOptions() default is used
  - extract_frames is called with the correct arguments
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
    from clipwright_frames.server import main, mcp

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
        "summary": "ok",
        "data": {},
        "artifacts": [],
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
    """Validate MCP annotations for the clipwright_extract_frames tool.

    readOnlyHint=False (writes frames and artifacts to output_dir; input media
    is never modified) / destructiveHint=False / idempotentHint=True /
    openWorldHint=False (architecture-report §8: explicitly False for frames).
    """

    def _get_annotations(self) -> Any:
        # CR L-1: No public API available in FastMCP to retrieve tool info,
        # so relying on the private API (_tool_manager).
        # This test may break if _tool_manager is changed or removed in a FastMCP upgrade.
        # Migrate to a public API once one is available.
        tool = mcp._tool_manager.get_tool(  # noqa: SLF001
            "clipwright_extract_frames"
        )
        assert tool is not None, "clipwright_extract_frames must be registered in mcp"
        return tool.annotations

    def test_tool_is_registered(self) -> None:
        """clipwright_extract_frames must be registered in mcp."""
        # CR L-1: _tool_manager is a private API. Risk of breaking on FastMCP updates.
        tool = mcp._tool_manager.get_tool(  # noqa: SLF001
            "clipwright_extract_frames"
        )
        assert tool is not None

    def test_read_only_hint_is_false(self) -> None:
        """readOnlyHint=False (writes frames and artifacts to output_dir)."""
        ann = self._get_annotations()
        assert ann.readOnlyHint is False

    def test_destructive_hint_is_false(self) -> None:
        """destructiveHint=False (input media remains unchanged)."""
        ann = self._get_annotations()
        assert ann.destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        """idempotentHint=True (same input + same options -> same output frames)."""
        ann = self._get_annotations()
        assert ann.idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False (architecture-report §8: explicitly False for frames)."""
        ann = self._get_annotations()
        assert ann.openWorldHint is False


# ---------------------------------------------------------------------------
# Default options resolution
# ---------------------------------------------------------------------------


class TestDefaultOptions:
    """When options=None, ExtractFramesOptions() defaults are used."""

    def test_options_none_uses_defaults(self) -> None:
        """options omitted -> extract_frames called with ExtractFramesOptions()."""
        from clipwright_frames.schemas import ExtractFramesOptions

        captured_calls: list[dict[str, Any]] = []

        def _capture(**kwargs: Any) -> ToolResult:
            captured_calls.append(kwargs)
            return _ok_tool_result()

        with patch("clipwright_frames.server.extract_frames", side_effect=_capture):
            asyncio.run(
                mcp.call_tool(
                    "clipwright_extract_frames",
                    {"media": "video.mp4", "output_dir": "/tmp/out"},
                )
            )

        assert len(captured_calls) == 1
        call_opts = captured_calls[0].get("options")
        assert call_opts is not None
        assert isinstance(call_opts, ExtractFramesOptions)
        # Verify defaults
        assert call_opts.mode == "interval"
        assert call_opts.interval_sec == pytest.approx(10.0)
        assert call_opts.format == "jpeg"
        assert call_opts.quality == 2
        assert call_opts.max_width is None

    def test_options_provided_passed_through(self) -> None:
        """Explicit options dict is forwarded to extract_frames as ExtractFramesOptions."""
        from clipwright_frames.schemas import ExtractFramesOptions

        captured_calls: list[dict[str, Any]] = []

        def _capture(**kwargs: Any) -> ToolResult:
            captured_calls.append(kwargs)
            return _ok_tool_result()

        with patch("clipwright_frames.server.extract_frames", side_effect=_capture):
            asyncio.run(
                mcp.call_tool(
                    "clipwright_extract_frames",
                    {
                        "media": "video.mp4",
                        "output_dir": "/tmp/out",
                        "options": {
                            "mode": "timestamps",
                            "timestamps": [1.0, 5.0, 10.0],
                            "format": "png",
                        },
                    },
                )
            )

        assert len(captured_calls) == 1
        call_opts = captured_calls[0].get("options")
        assert call_opts is not None
        assert isinstance(call_opts, ExtractFramesOptions)
        assert call_opts.mode == "timestamps"
        assert call_opts.timestamps == [1.0, 5.0, 10.0]
        assert call_opts.format == "png"


# ---------------------------------------------------------------------------
# MCP tool invocation: delegation to extract.extract_frames
# ---------------------------------------------------------------------------


class TestMcpToolDelegation:
    """Validate delegation to extract.extract_frames and envelope passthrough."""

    def test_success_delegates_to_extract_frames(self) -> None:
        """On success, must call and delegate to extract_frames."""
        expected = _ok_tool_result(summary="extracted ok")

        with patch(
            "clipwright_frames.server.extract_frames",
            return_value=expected,
        ) as mock_extract:
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_extract_frames",
                    {"media": "video.mp4", "output_dir": "/tmp/out"},
                )
            )

        mock_extract.assert_called_once()
        assert structured["ok"] is True

    def test_failure_envelope_returned_as_is(self) -> None:
        """When extract_frames returns an error envelope, server returns it unchanged."""
        expected = _error_tool_result("FILE_NOT_FOUND")

        with patch(
            "clipwright_frames.server.extract_frames",
            return_value=expected,
        ):
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_extract_frames",
                    {"media": "missing.mp4", "output_dir": "/tmp/out"},
                )
            )

        assert structured["ok"] is False
        assert structured.get("error") is not None
        assert structured["error"]["code"] == "FILE_NOT_FOUND"

    def test_extract_frames_called_with_media_and_output_dir(self) -> None:
        """extract_frames must be called with the media and output_dir arguments.

        options type/value verified in TestDefaultOptions; here only media/output_dir
        passthrough is checked.
        """
        captured_calls: list[dict[str, Any]] = []

        def _capture(**kwargs: Any) -> ToolResult:
            captured_calls.append(kwargs)
            return _ok_tool_result()

        with patch("clipwright_frames.server.extract_frames", side_effect=_capture):
            asyncio.run(
                mcp.call_tool(
                    "clipwright_extract_frames",
                    {"media": "my_video.mp4", "output_dir": "/tmp/my_output"},
                )
            )

        assert len(captured_calls) == 1
        assert captured_calls[0].get("media") == "my_video.mp4"
        assert captured_calls[0].get("output_dir") == "/tmp/my_output"

    def test_server_has_no_logic_only_delegates(self) -> None:
        """Server must not transform or modify the result from extract_frames.

        The envelope returned by extract_frames must be passed through as-is.
        """
        expected = _ok_tool_result(
            summary="Extracted 3 frame(s) from video.mp4 in interval mode.",
            data={"frame_count": 3, "mode": "interval", "format": "jpeg"},
        )

        with patch(
            "clipwright_frames.server.extract_frames",
            return_value=expected,
        ):
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_extract_frames",
                    {"media": "video.mp4", "output_dir": "/tmp/out"},
                )
            )

        assert structured["ok"] is True
        assert (
            structured["summary"]
            == "Extracted 3 frame(s) from video.mp4 in interval mode."
        )
        assert structured["data"]["frame_count"] == 3


# ---------------------------------------------------------------------------
# MCP boundary input validation
# ---------------------------------------------------------------------------


class TestMcpInputValidation:
    """MCP boundary input validation tests.

    These tests pass path traversal and invalid inputs directly to MCP
    without mocking extract_frames, so the real input validation in extract.py
    is exercised. The test asserts ok=False is returned regardless of the
    specific error code.
    """

    def test_path_traversal_media_returns_error(self) -> None:
        """Path traversal input via MCP must return ok=False."""
        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_extract_frames",
                {
                    "media": "../../../etc/passwd",
                    "output_dir": "/tmp/out",
                },
            )
        )
        assert structured["ok"] is False
        assert structured.get("error") is not None

    def test_nonexistent_media_returns_error(self) -> None:
        """Nonexistent media path via MCP must return ok=False."""
        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_extract_frames",
                {
                    "media": "nonexistent_file_that_does_not_exist.mp4",
                    "output_dir": "/tmp/out",
                },
            )
        )
        assert structured["ok"] is False
        assert structured.get("error") is not None


# ---------------------------------------------------------------------------
# main() existence test
# ---------------------------------------------------------------------------


class TestCliMain:
    """Validate existence and callability of main()."""

    def test_main_is_callable(self) -> None:
        """main() function must exist and be callable."""
        assert callable(main)

    def test_main_exists_in_module(self) -> None:
        """main must be defined in the clipwright_frames.server module."""
        import clipwright_frames.server as server_module

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
        tool = next(t for t in tools if t.name == "clipwright_extract_frames")
        schema = tool.outputSchema or {}
        assert "ok" in schema.get("properties", {}), (
            "outputSchema must expose 'ok' property"
        )

    def test_structuredcontent_top_level_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_tool must return structuredContent with top-level 'ok' key."""
        monkeypatch.setattr(
            "clipwright_frames.server.extract_frames",
            lambda **kw: ToolResult(
                ok=True,
                summary="Extracted 5 frame(s) from video.mp4 in interval mode.",
                data={},
                artifacts=[],
                warnings=[],
            ),
        )
        result = asyncio.run(
            mcp.call_tool(
                "clipwright_extract_frames",
                {"media": "m.mp4", "output_dir": "/tmp/out"},
            )
        )
        content, structured = result
        assert structured is not None
        assert "ok" in structured
        assert structured["ok"] is True
