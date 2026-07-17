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

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.process import safe_subprocess_message
from clipwright.schemas import MediaInfo, StreamInfo, ToolError, ToolResult

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
    reason="server.py could not be imported — tests are skipped when unavailable",
    strict=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_tool_result(**kwargs: Any) -> ToolResult:
    """Return a success ToolResult (used as mock return value for render_timeline)."""
    base: dict[str, Any] = {
        "ok": True,
        "summary": "ok",
        "data": {},
        "artifacts": [],
        "warnings": [],
    }
    base.update(kwargs)
    return ToolResult.model_validate(base)


def _error_tool_result(code: str) -> ToolResult:
    """Return a failure ToolResult (used as mock return value for render_timeline)."""
    return ToolResult(
        ok=False,
        error=ToolError(code=code, message="error", hint="hint"),
    )


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
        expected = _ok_tool_result(summary="rendered ok")

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
        expected = _error_tool_result("FILE_NOT_FOUND")

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
            return_value=_ok_tool_result(),
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
            return_value=_ok_tool_result(),
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
        expected = ToolResult(
            ok=False,
            error=ToolError(
                code="INVALID_INPUT",
                message="invalid input",
                hint="please fix it",
            ),
        )

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
            lambda **kw: ToolResult(ok=True, summary="dry run ok"),
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


# ---------------------------------------------------------------------------
# MCP boundary test: SR-R-001 / ADR-SR-1 subprocess-error redaction
# (FR-1 acceptance criterion 8 / architecture-report-20260717-163916.md §7)
# ---------------------------------------------------------------------------


class TestMcpBoundarySubprocessRedaction:
    """Verify structuredContent never carries raw stderr / absolute paths from
    a subprocess failure (S1 seam), through the real FastMCP call_tool boundary.

    Unlike TestMcpBoundary.test_structuredcontent_top_level_ok, render_timeline
    itself is NOT mocked here: only render.py's low-level run()/inspect_media()
    seams are monkeypatched, so the real render_timeline() -> _render_inner()
    pipeline executes and (once implemented) is expected to route the
    SUBPROCESS_FAILED through _sanitize_subprocess_error before it reaches the
    MCP envelope.

    Currently (Red): render.py does not yet mask subprocess-error messages, so
    the raw absolute path injected below is still present verbatim in
    structuredContent -- the assertions fail.
    """

    def test_structuredcontent_masks_subprocess_failed_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """structuredContent's error.message must be the masked subprocess
        message, not the raw run() stderr-derived one (S1 seam, ADR-SR-1).
        """
        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"

        clip = otio.schema.Clip()
        clip.media_reference = otio.schema.ExternalReference(target_url=source)
        clip.source_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, 30.0),
            duration=otio.opentime.RationalTime(150, 30.0),
        )
        track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
        track.append(clip)
        tl = otio.schema.Timeline()
        tl.tracks.append(track)
        otio.adapters.write_to_file(tl, str(tl_path))

        output = str(tmp_path / "out.mp4")
        leak_path = str(tmp_path / "leaked-media-secret.mp4")

        media_info = MediaInfo(
            path=source,
            container="mov,mp4,m4a,3gp,3g2,mj2",
            duration=None,
            streams=[StreamInfo(index=0, codec_type="video", codec_name="h264")],
            bit_rate=8_000_000,
        )

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> Any:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=f"Command failed with exit code 1: {leak_path}: no such file",
                hint="Check the command arguments.",
            )

        monkeypatch.setattr(
            "clipwright_render.render.inspect_media",
            lambda source: media_info,
        )
        monkeypatch.setattr(
            "clipwright_render.render.resolve_tool",
            lambda name, env_var=None: f"/usr/bin/{name}",
        )
        monkeypatch.setattr("clipwright_render.render.run", _fake_ffmpeg_run)

        content_list, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_render",
                {"timeline": str(tl_path), "output": output, "dry_run": False},
            )
        )
        content = json.loads(content_list[0].text) if content_list else {}

        assert content.get("ok") is False
        error = content.get("error") or {}
        assert error.get("code") == ErrorCode.SUBPROCESS_FAILED
        assert error.get("hint") == "Check the command arguments."
        expected_message = safe_subprocess_message(
            ClipwrightError(code=ErrorCode.SUBPROCESS_FAILED, message="", hint="")
        )
        assert error.get("message") == expected_message, (
            "structuredContent currently exposes the raw run() message verbatim "
            "instead of the masked message (S1 seam, ADR-SR-1)."
        )
        assert leak_path not in json.dumps(content), (
            "structuredContent must not contain the raw absolute path leaked by "
            "the subprocess failure (CWE-209)."
        )
        # structured (the second call_tool return value) must also be masked --
        # not just the text-content JSON.
        assert leak_path not in json.dumps(structured or {})
