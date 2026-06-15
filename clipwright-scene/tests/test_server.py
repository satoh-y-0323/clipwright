"""test_server.py — Tests for clipwright-scene server.py MCP boundary.

Target:
  - clipwright_detect_scenes tool registered in MCP, delegates to detect.detect_scenes
  - MCP annotations:
    readOnlyHint=False / destructiveHint=False / idempotentHint=True
    (readOnlyHint=False because the tool writes a new OTIO timeline to output)
  - options=None -> DetectScenesOptions() default is used
  - detect_scenes is called with the correct arguments
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
    from clipwright_scene.server import main, mcp

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
# MCP annotations tests (detect type)
# ---------------------------------------------------------------------------


class TestMcpAnnotations:
    """Validate MCP annotations for the clipwright_detect_scenes tool.

    readOnlyHint=False (writes a new OTIO timeline to output; input media is never
    modified) / destructiveHint=False / idempotentHint=True.
    """

    def _get_annotations(self) -> Any:
        # CR L-1: relying on private _tool_manager API.
        # This may break if FastMCP changes its internals.
        tool = mcp._tool_manager.get_tool(  # noqa: SLF001
            "clipwright_detect_scenes"
        )
        assert tool is not None, "clipwright_detect_scenes must be registered in mcp"
        return tool.annotations

    def test_tool_is_registered(self) -> None:
        """clipwright_detect_scenes must be registered in mcp."""
        tool = mcp._tool_manager.get_tool(  # noqa: SLF001
            "clipwright_detect_scenes"
        )
        assert tool is not None

    def test_read_only_hint_is_false(self) -> None:
        """readOnlyHint=False (writes a new OTIO timeline to output)."""
        ann = self._get_annotations()
        assert ann.readOnlyHint is False

    def test_destructive_hint_is_false(self) -> None:
        """destructiveHint=False (input media and OTIO remain unchanged)."""
        ann = self._get_annotations()
        assert ann.destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        """idempotentHint=True (same input + same options -> same OTIO markers)."""
        ann = self._get_annotations()
        assert ann.idempotentHint is True


# ---------------------------------------------------------------------------
# Default options resolution
# ---------------------------------------------------------------------------


class TestDefaultOptions:
    """When options=None, DetectScenesOptions() defaults are used."""

    def test_options_none_uses_defaults(self) -> None:
        """options omitted -> detect_scenes called with DetectScenesOptions()."""
        from clipwright_scene.schemas import DetectScenesOptions

        captured_calls: list[dict[str, Any]] = []

        def _capture(**kwargs: Any) -> ToolResult:
            captured_calls.append(kwargs)
            return _ok_tool_result()

        with patch("clipwright_scene.server.detect_scenes", side_effect=_capture):
            asyncio.run(
                mcp.call_tool(
                    "clipwright_detect_scenes",
                    {"media": "video.mp4", "output": "out.otio"},
                )
            )

        assert len(captured_calls) == 1
        call_opts = captured_calls[0].get("options")
        assert call_opts is not None
        assert isinstance(call_opts, DetectScenesOptions)
        # Verify defaults
        assert call_opts.threshold == pytest.approx(0.3)
        assert call_opts.min_scene_duration == pytest.approx(1.0)
        assert call_opts.backend == "ffmpeg"

    def test_options_provided_passed_through(self) -> None:
        """Explicit options dict is forwarded to detect_scenes as DetectScenesOptions."""
        from clipwright_scene.schemas import DetectScenesOptions

        captured_calls: list[dict[str, Any]] = []

        def _capture(**kwargs: Any) -> ToolResult:
            captured_calls.append(kwargs)
            return _ok_tool_result()

        with patch("clipwright_scene.server.detect_scenes", side_effect=_capture):
            asyncio.run(
                mcp.call_tool(
                    "clipwright_detect_scenes",
                    {
                        "media": "video.mp4",
                        "output": "out.otio",
                        "options": {
                            "threshold": 0.5,
                            "min_scene_duration": 2.0,
                            "backend": "pyscenedetect",
                        },
                    },
                )
            )

        assert len(captured_calls) == 1
        call_opts = captured_calls[0].get("options")
        assert call_opts is not None
        assert isinstance(call_opts, DetectScenesOptions)
        assert call_opts.threshold == pytest.approx(0.5)
        assert call_opts.backend == "pyscenedetect"


# ---------------------------------------------------------------------------
# MCP tool invocation: delegation to detect.detect_scenes
# ---------------------------------------------------------------------------


class TestMcpToolDelegation:
    """Validate delegation to detect.detect_scenes and envelope passthrough."""

    def test_success_delegates_to_detect_scenes(self) -> None:
        """On success, must call and delegate to detect_scenes."""
        expected = _ok_tool_result(summary="detected ok")

        with patch(
            "clipwright_scene.server.detect_scenes",
            return_value=expected,
        ) as mock_detect:
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_detect_scenes",
                    {"media": "video.mp4", "output": "out.otio"},
                )
            )

        mock_detect.assert_called_once()
        assert structured["ok"] is True

    def test_failure_envelope_returned_as_is(self) -> None:
        """When detect_scenes returns an error envelope, server returns it unchanged."""
        expected = _error_tool_result("FILE_NOT_FOUND")

        with patch(
            "clipwright_scene.server.detect_scenes",
            return_value=expected,
        ):
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_detect_scenes",
                    {"media": "missing.mp4", "output": "out.otio"},
                )
            )

        assert structured["ok"] is False
        assert structured.get("error") is not None
        assert structured["error"]["code"] == "FILE_NOT_FOUND"

    def test_detect_scenes_called_with_media_and_output(self) -> None:
        """detect_scenes must be called with the media and output arguments."""
        captured_calls: list[dict[str, Any]] = []

        def _capture(**kwargs: Any) -> ToolResult:
            captured_calls.append(kwargs)
            return _ok_tool_result()

        with patch("clipwright_scene.server.detect_scenes", side_effect=_capture):
            asyncio.run(
                mcp.call_tool(
                    "clipwright_detect_scenes",
                    {"media": "my_video.mp4", "output": "my_output.otio"},
                )
            )

        assert len(captured_calls) == 1
        assert captured_calls[0].get("media") == "my_video.mp4"
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
        """main must be defined in the clipwright_scene.server module."""
        import clipwright_scene.server as server_module

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
        tool = next(t for t in tools if t.name == "clipwright_detect_scenes")
        schema = tool.outputSchema or {}
        assert "ok" in schema.get("properties", {}), (
            "outputSchema must expose 'ok' property"
        )

    def test_structuredcontent_top_level_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_tool must return structuredContent with top-level 'ok' key."""
        monkeypatch.setattr(
            "clipwright_scene.server.detect_scenes",
            lambda media, output, options, timeline=None: ToolResult(
                ok=True,
                summary="Scene boundaries detected.",
                data={},
                artifacts=[],
                warnings=[],
            ),
        )
        result = asyncio.run(
            mcp.call_tool(
                "clipwright_detect_scenes",
                {"media": "m.mp4", "output": "out.otio"},
            )
        )
        content, structured = result
        assert structured is not None
        assert "ok" in structured
        assert structured["ok"] is True
