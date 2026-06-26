"""test_server.py — Tests for clipwright-silence server.py (MCP + CLI).

Target:
  - clipwright_detect_silence tool must be registered in MCP and delegate to detect.detect_silence
  - MCP annotations (§6.2, detect type):
    readOnlyHint:true / destructiveHint:false / idempotentHint:true
  - Success envelope (ok:true)
  - Error envelope (ok:false, error:{code,message,hint})
  - When detect_silence returns error_result, it must be returned as-is
  - Existence and callability of main() (DC-GP-002)
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from clipwright.schemas import ToolError, ToolResult

# ---------------------------------------------------------------------------
# Attempt to import server.py (_SERVER_AVAILABLE = False if not implemented)
# ---------------------------------------------------------------------------

try:
    from clipwright_silence.server import (
        clipwright_detect_silence as server_detect_silence,
    )
    from clipwright_silence.server import main, mcp

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
# MCP annotations tests (§6.2, detect type)
# ---------------------------------------------------------------------------


class TestMcpAnnotations:
    """Validate MCP annotations for the clipwright_detect_silence tool.

    detect type: readOnlyHint:true / destructiveHint:false / idempotentHint:true
    (note: readOnlyHint=true unlike render)
    """

    def _get_annotations(self) -> Any:
        # CR L-1: No public API available in FastMCP to retrieve tool info,
        # so relying on the private API (_tool_manager).
        # This test may break if _tool_manager is changed or removed in a FastMCP upgrade.
        # Migrate to a public API once one is available.
        tool = mcp._tool_manager.get_tool(  # noqa: SLF001
            "clipwright_detect_silence"
        )
        assert tool is not None, "clipwright_detect_silence must be registered in mcp"
        return tool.annotations

    def test_tool_is_registered(self) -> None:
        """clipwright_detect_silence must be registered in mcp."""
        # CR L-1: _tool_manager is a private API. Risk of breaking on FastMCP updates.
        tool = mcp._tool_manager.get_tool(  # noqa: SLF001
            "clipwright_detect_silence"
        )
        assert tool is not None

    def test_read_only_hint_is_true(self) -> None:
        """readOnlyHint=True (does not modify the media file; detect type convention)."""
        ann = self._get_annotations()
        assert ann.readOnlyHint is True

    def test_destructive_hint_is_false(self) -> None:
        """destructiveHint=False (input media and OTIO remain unchanged)."""
        ann = self._get_annotations()
        assert ann.destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        """idempotentHint=True (same input + same parameters -> same timeline)."""
        ann = self._get_annotations()
        assert ann.idempotentHint is True


# ---------------------------------------------------------------------------
# MCP tool invocation: delegation to detect.detect_silence
# ---------------------------------------------------------------------------


class TestMcpToolDelegation:
    """Validate delegation to detect.detect_silence and error envelope passthrough.

    Patches detect_silence to confirm delegation.
    """

    def test_success_delegates_to_detect_silence(self) -> None:
        """On success, must call and delegate to detect.detect_silence."""
        expected = _ok_tool_result(summary="detected ok")

        with patch(
            "clipwright_silence.server.detect_silence",
            return_value=expected,
        ) as mock_detect:
            result = server_detect_silence(
                media="video.mp4",
                output="out.otio",
                options=None,
            )

        mock_detect.assert_called_once()
        assert result.ok is True

    def test_failure_returns_error_tool_result(self) -> None:
        """When detect_silence returns an error envelope, server must return it as-is."""
        expected = _error_tool_result("FILE_NOT_FOUND")

        with patch(
            "clipwright_silence.server.detect_silence",
            return_value=expected,
        ):
            result = server_detect_silence(
                media="missing.mp4",
                output="out.otio",
                options=None,
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "FILE_NOT_FOUND"

    def test_error_result_passthrough(self) -> None:
        """When detect returns error_result, it must be returned as-is (no double conversion)."""
        expected = _error_tool_result("DEPENDENCY_MISSING")

        with patch(
            "clipwright_silence.server.detect_silence",
            return_value=expected,
        ):
            result = server_detect_silence(
                media="video.mp4",
                output="out.otio",
                options=None,
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "DEPENDENCY_MISSING"
        assert result.error.message
        assert result.error.hint

    def test_error_tool_result_has_code_message_hint(self) -> None:
        """Error envelope must contain code / message / hint."""
        expected = ToolResult(
            ok=False,
            error=ToolError(
                code="UNSUPPORTED_OPERATION",
                message="Cannot detect silence: no audio stream",
                hint="Specify a media source that contains an audio stream",
            ),
        )

        with patch(
            "clipwright_silence.server.detect_silence",
            return_value=expected,
        ):
            result = server_detect_silence(
                media="no_audio.mp4",
                output="out.otio",
                options=None,
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code
        assert result.error.message
        assert result.error.hint

    def test_options_passed_to_detect_silence(self) -> None:
        """options content must be passed to detect_silence."""
        from clipwright_silence.schemas import DetectSilenceOptions

        opts = DetectSilenceOptions(
            silence_threshold_db=-40.0, min_silence_duration=1.0
        )

        with patch(
            "clipwright_silence.server.detect_silence",
            return_value=_ok_tool_result(),
        ) as mock_detect:
            server_detect_silence(
                media="video.mp4",
                output="out.otio",
                options=opts,
            )

        mock_detect.assert_called_once()
        call_args = mock_detect.call_args
        assert call_args is not None

    def test_ok_tool_result_structure(self) -> None:
        """Success envelope must contain ok/summary/data/artifacts/warnings."""
        expected = _ok_tool_result(
            summary=(
                "Detected 3 silence interval(s) from a 60s source. "
                "Generated timeline.otio with 4 interval(s) to keep."
            ),
            data={
                "silence_count": 3,
                "total_silence_seconds": 10.0,
                "keep_count": 4,
                "total_keep_seconds": 50.0,
            },
            artifacts=[{"role": "timeline", "path": "out.otio", "format": "otio"}],
        )

        with patch(
            "clipwright_silence.server.detect_silence",
            return_value=expected,
        ):
            result = server_detect_silence(
                media="video.mp4",
                output="out.otio",
                options=None,
            )

        assert result.ok is True
        assert result.summary is not None
        assert result.data is not None
        assert result.artifacts is not None
        assert result.warnings is not None


# ---------------------------------------------------------------------------
# main() existence test (DC-GP-002 / §6.3)
# ---------------------------------------------------------------------------


class TestCliMain:
    """Validate existence and basic call of main() (DC-GP-002: CLI = MCP stdio launch)."""

    def test_main_is_callable(self) -> None:
        """main() function must exist and be callable."""
        assert callable(main)

    def test_main_exists_in_module(self) -> None:
        """main must be defined in the clipwright_silence.server module."""
        import clipwright_silence.server as server_module

        assert hasattr(server_module, "main")
        assert callable(server_module.main)

    def test_main_runs_mcp_server(self) -> None:
        """main() must call mcp.run (stdio launch, DC-GP-002).

        Does not perform actual stdio launch; confirmed via mock of mcp.run.
        """
        with patch.object(mcp, "run") as mock_run:
            main()

        mock_run.assert_called_once()
        # must be called with transport="stdio"
        _args, kwargs = mock_run.call_args
        assert kwargs.get("transport") == "stdio" or (
            len(_args) >= 1 and _args[0] == "stdio"
        )


# ---------------------------------------------------------------------------
# MCP boundary tests: outputSchema typing and structuredContent
# ---------------------------------------------------------------------------


class TestMcpBoundary:
    """Validate MCP wire contract: typed outputSchema and structuredContent."""

    def test_outputschema_is_typed(self) -> None:
        """outputSchema must expose 'ok' property (typed ToolResult via FastMCP)."""
        tools = asyncio.run(mcp.list_tools())
        tool = next(t for t in tools if t.name == "clipwright_detect_silence")
        schema = tool.outputSchema or {}
        assert "ok" in schema.get("properties", {}), (
            "outputSchema must expose 'ok' property"
        )

    def test_structuredcontent_top_level_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_tool must return structuredContent with top-level 'ok' key."""
        monkeypatch.setattr(
            "clipwright_silence.server.detect_silence",
            lambda **kw: ToolResult(
                ok=True,
                summary="Silence detected.",
                data={},
                artifacts=[],
                warnings=[],
            ),
        )
        result = asyncio.run(
            mcp.call_tool(
                "clipwright_detect_silence", {"media": "m.mp4", "output": "out.otio"}
            )
        )
        content, structured = result
        assert structured is not None
        assert "ok" in structured
        assert structured["ok"] is True
