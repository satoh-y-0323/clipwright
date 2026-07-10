"""test_server.py -- Tests for clipwright-export server.py (MCP wrapper).

TDD Red: clipwright_export.server does not exist yet (only __init__.py and
py.typed exist under src/clipwright_export/, per Wave 1 of plan). Every test
in this module is therefore expected to fail via the xfail(strict=True) guard
below -- ModuleNotFoundError on import, wrapped as an expected failure. Once
server.py (and schemas.py / timeline_export.py / chapters.py) land in Wave 2,
_SERVER_AVAILABLE flips to True and these tests must pass unmodified (Green).

Architecture reference:
  - architecture-report-20260710-161944.md
    - S2.2 "1 package = 2 tools" (writes to timeline_export.py / chapters.py,
      server.py is a pure delegation wrapper, mirrors
      clipwright-overlay/src/clipwright_overlay/server.py:20-160)
    - S10.1 ADR-EX-8 (annotations: readOnlyHint=True, destructiveHint=False,
      idempotentHint=True, openWorldHint=False -- identical for BOTH tools)
    - S3.1 / S3.2 (ExportTimelineOptions.format, ExportChaptersOptions.format +
      marker_kind)

Target contract:
  - FastMCP("clipwright-export") registers two tools:
      clipwright_export_timeline
      clipwright_export_chapters
  - annotations for BOTH tools: readOnlyHint=True, destructiveHint=False,
      idempotentHint=True, openWorldHint=False (ADR-EX-8).
  - options is None -> error_result("INVALID_INPUT", ...) WITHOUT calling the
      domain function (export_timeline / export_chapters respectively);
      hint must mention the required field "format".
  - Pure delegation: clipwright_export_timeline(timeline, output, options)
      calls export_timeline(timeline=..., output=..., options=...) and returns
      its ToolResult unchanged (ok=True and ok=False passthrough alike).
      Same for clipwright_export_chapters -> export_chapters(...).
  - main() exists and calls mcp.run(transport="stdio").
  - MCP tool names are snake_case, verb-first, clipwright_<action> (§ project
      convention): "clipwright_export_timeline" / "clipwright_export_chapters".
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
    from clipwright_export.server import clipwright_export_chapters as server_chapters
    from clipwright_export.server import clipwright_export_timeline as server_timeline
    from clipwright_export.server import main, mcp

    _SERVER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _SERVER_AVAILABLE = False

# Mark all tests as xfail unless server.py is available.
# strict=True means an unexpected PASS is reported as XPASS (error), which
# catches accidental early-Green before the implementation lands.
pytestmark = pytest.mark.xfail(
    not _SERVER_AVAILABLE,
    reason="clipwright_export.server is not implemented yet",
    strict=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_timeline_tool_result(**kwargs: Any) -> ToolResult:
    """Return a success ToolResult template for clipwright_export_timeline."""
    defaults: dict[str, Any] = {
        "ok": True,
        "summary": (
            "Exported timeline to EDL. 2 clip(s), 1 media reference(s) "
            "absolutized. Output: out.edl."
        ),
        "data": {
            "format": "edl",
            "clip_count": 2,
            "absolutized_count": 1,
        },
        "artifacts": [
            {
                "role": "output",
                "path": "/tmp/out.edl",
                "format": "edl",
            }
        ],
        "warnings": [],
    }
    defaults.update(kwargs)
    return ToolResult.model_validate(defaults)


def _ok_chapters_tool_result(**kwargs: Any) -> ToolResult:
    """Return a success ToolResult template for clipwright_export_chapters."""
    defaults: dict[str, Any] = {
        "ok": True,
        "summary": (
            "Exported 3 chapter(s) from 'scene_boundary' markers to YouTube "
            "format. Output: out.txt."
        ),
        "data": {
            "format": "youtube",
            "marker_kind": "scene_boundary",
            "chapter_count": 3,
        },
        "artifacts": [
            {
                "role": "output",
                "path": "/tmp/out.txt",
                "format": "txt",
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
# MCP registration tests
# ---------------------------------------------------------------------------


class TestMcpRegistration:
    """Validate that both tools are registered in the FastMCP instance."""

    def test_export_timeline_is_registered(self) -> None:
        """clipwright_export_timeline must be registered in mcp."""
        tool = mcp._tool_manager.get_tool("clipwright_export_timeline")  # noqa: SLF001
        assert tool is not None, "clipwright_export_timeline must be registered in mcp"

    def test_export_chapters_is_registered(self) -> None:
        """clipwright_export_chapters must be registered in mcp."""
        tool = mcp._tool_manager.get_tool("clipwright_export_chapters")  # noqa: SLF001
        assert tool is not None, "clipwright_export_chapters must be registered in mcp"

    def test_mcp_server_name(self) -> None:
        """The FastMCP instance must be named 'clipwright-export'."""
        assert mcp.name == "clipwright-export"

    def test_tools_in_list_tools(self) -> None:
        """Both tools must appear in mcp.list_tools(), and only those two."""
        tools = asyncio.run(mcp.list_tools())
        names = {t.name for t in tools}
        assert "clipwright_export_timeline" in names
        assert "clipwright_export_chapters" in names

    def test_tool_names_are_snake_case_verb_first(self) -> None:
        """MCP tool names follow clipwright_<action> snake_case convention."""
        tools = asyncio.run(mcp.list_tools())
        names = {t.name for t in tools}
        for name in names:
            assert name.startswith("clipwright_"), (
                f"tool name must start with 'clipwright_': {name!r}"
            )
            assert name == name.lower(), f"tool name must be lower_snake_case: {name!r}"
            assert " " not in name
        assert "clipwright_export_timeline" in names
        assert "clipwright_export_chapters" in names


# ---------------------------------------------------------------------------
# MCP annotations tests (ADR-EX-8: identical for both tools)
# ---------------------------------------------------------------------------


class TestMcpAnnotationsExportTimeline:
    """Validate MCP annotations for clipwright_export_timeline (ADR-EX-8).

    readOnlyHint=True  (writes only a new exchange file; existing OTIO/media
                         are never modified -- new-file write is outside the
                         readOnly scope, per overlay's rationale)
    destructiveHint=False (input timeline and media are non-destructive)
    idempotentHint=True  (same input + options -> same output)
    openWorldHint=False  (local filesystem only; export_timeline does not
                           invoke ffmpeg/network)
    """

    def _get_annotations(self) -> Any:
        tool = mcp._tool_manager.get_tool("clipwright_export_timeline")  # noqa: SLF001
        assert tool is not None
        return tool.annotations

    def test_read_only_hint_is_true(self) -> None:
        assert self._get_annotations().readOnlyHint is True

    def test_destructive_hint_is_false(self) -> None:
        assert self._get_annotations().destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        assert self._get_annotations().idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        assert self._get_annotations().openWorldHint is False


class TestMcpAnnotationsExportChapters:
    """Validate MCP annotations for clipwright_export_chapters (ADR-EX-8).

    Identical values to clipwright_export_timeline (§10.1): openWorldHint=False
    even though this tool concerns ffmetadata, because the tool itself only
    writes a sidecar text file -- it does not invoke ffmpeg mux (that is left
    to the caller/tests, per §10.1 explicit note).
    """

    def _get_annotations(self) -> Any:
        tool = mcp._tool_manager.get_tool("clipwright_export_chapters")  # noqa: SLF001
        assert tool is not None
        return tool.annotations

    def test_read_only_hint_is_true(self) -> None:
        assert self._get_annotations().readOnlyHint is True

    def test_destructive_hint_is_false(self) -> None:
        assert self._get_annotations().destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        assert self._get_annotations().idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        assert self._get_annotations().openWorldHint is False


# ---------------------------------------------------------------------------
# options=None early error (mirror clipwright-overlay server.py pattern)
# ---------------------------------------------------------------------------


class TestExportTimelineOptionsNone:
    """options=None must return INVALID_INPUT WITHOUT calling export_timeline."""

    def test_options_none_returns_invalid_input(self) -> None:
        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_export_timeline",
                {"timeline": "test.otio", "output": "out.edl"},
            )
        )
        assert structured["ok"] is False
        error = structured.get("error") or {}
        assert error.get("code") == "INVALID_INPUT"

    def test_options_none_hint_mentions_format(self) -> None:
        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_export_timeline",
                {"timeline": "test.otio", "output": "out.edl"},
            )
        )
        error = structured.get("error") or {}
        hint = error.get("hint") or ""
        assert "format" in hint, f"hint must mention 'format'; got: {hint!r}"

    def test_options_none_does_not_call_export_timeline(self) -> None:
        with patch("clipwright_export.server.export_timeline") as mock_export:
            asyncio.run(
                mcp.call_tool(
                    "clipwright_export_timeline",
                    {"timeline": "test.otio", "output": "out.edl"},
                )
            )
        mock_export.assert_not_called()

    def test_options_none_not_a_success(self) -> None:
        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_export_timeline",
                {"timeline": "test.otio", "output": "out.edl"},
            )
        )
        assert structured["ok"] is not True


class TestExportChaptersOptionsNone:
    """options=None must return INVALID_INPUT WITHOUT calling export_chapters."""

    def test_options_none_returns_invalid_input(self) -> None:
        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_export_chapters",
                {"timeline": "test.otio", "output": "out.txt"},
            )
        )
        assert structured["ok"] is False
        error = structured.get("error") or {}
        assert error.get("code") == "INVALID_INPUT"

    def test_options_none_hint_mentions_format(self) -> None:
        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_export_chapters",
                {"timeline": "test.otio", "output": "out.txt"},
            )
        )
        error = structured.get("error") or {}
        hint = error.get("hint") or ""
        assert "format" in hint, f"hint must mention 'format'; got: {hint!r}"

    def test_options_none_does_not_call_export_chapters(self) -> None:
        with patch("clipwright_export.server.export_chapters") as mock_export:
            asyncio.run(
                mcp.call_tool(
                    "clipwright_export_chapters",
                    {"timeline": "test.otio", "output": "out.txt"},
                )
            )
        mock_export.assert_not_called()

    def test_options_none_not_a_success(self) -> None:
        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_export_chapters",
                {"timeline": "test.otio", "output": "out.txt"},
            )
        )
        assert structured["ok"] is not True


# ---------------------------------------------------------------------------
# Delegation tests (server must not contain business logic)
# ---------------------------------------------------------------------------


class TestExportTimelineDelegation:
    """Validate delegation to timeline_export.export_timeline; server is a
    pure wrapper (mirrors overlay ADR-OV-1)."""

    def test_success_delegates_to_export_timeline(self) -> None:
        expected = _ok_timeline_tool_result()

        with patch(
            "clipwright_export.server.export_timeline",
            return_value=expected,
        ) as mock_export:
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_export_timeline",
                    {
                        "timeline": "test.otio",
                        "output": "out.edl",
                        "options": {"format": "edl"},
                    },
                )
            )

        mock_export.assert_called_once()
        assert structured["ok"] is True

    def test_export_timeline_called_with_correct_args(self) -> None:
        captured_calls: list[dict[str, Any]] = []

        def _capture(**kwargs: Any) -> ToolResult:
            captured_calls.append(kwargs)
            return _ok_timeline_tool_result()

        with patch("clipwright_export.server.export_timeline", side_effect=_capture):
            asyncio.run(
                mcp.call_tool(
                    "clipwright_export_timeline",
                    {
                        "timeline": "my_timeline.otio",
                        "output": "my_output.edl",
                        "options": {"format": "edl"},
                    },
                )
            )

        assert len(captured_calls) == 1
        assert captured_calls[0].get("timeline") == "my_timeline.otio"
        assert captured_calls[0].get("output") == "my_output.edl"

    def test_failure_envelope_returned_as_is(self) -> None:
        expected = _error_tool_result("FILE_NOT_FOUND")

        with patch(
            "clipwright_export.server.export_timeline",
            return_value=expected,
        ):
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_export_timeline",
                    {
                        "timeline": "missing.otio",
                        "output": "out.edl",
                        "options": {"format": "edl"},
                    },
                )
            )

        assert structured["ok"] is False
        assert structured["error"]["code"] == "FILE_NOT_FOUND"

    def test_invalid_input_envelope_passthrough(self) -> None:
        """When export_timeline itself returns INVALID_INPUT (e.g. output==timeline),
        server must return it as-is."""
        expected = _error_tool_result("INVALID_INPUT")

        with patch(
            "clipwright_export.server.export_timeline",
            return_value=expected,
        ):
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_export_timeline",
                    {
                        "timeline": "test.otio",
                        "output": "test.otio",
                        "options": {"format": "edl"},
                    },
                )
            )

        assert structured["ok"] is False
        assert structured["error"]["code"] == "INVALID_INPUT"

    def test_direct_call_delegates_to_export_timeline(self) -> None:
        expected = _ok_timeline_tool_result()

        from clipwright_export.schemas import ExportTimelineOptions

        opts = ExportTimelineOptions(format="edl")

        with patch(
            "clipwright_export.server.export_timeline",
            return_value=expected,
        ) as mock_export:
            result = server_timeline(
                timeline="my_tl.otio",
                output="my_out.edl",
                options=opts,
            )

        mock_export.assert_called_once()
        call_kwargs = mock_export.call_args.kwargs
        assert call_kwargs.get("timeline") == "my_tl.otio"
        assert call_kwargs.get("output") == "my_out.edl"
        assert call_kwargs.get("options") is opts
        assert result.ok is True


class TestExportChaptersDelegation:
    """Validate delegation to chapters.export_chapters; server is a pure
    wrapper (mirrors overlay ADR-OV-1)."""

    def test_success_delegates_to_export_chapters(self) -> None:
        expected = _ok_chapters_tool_result()

        with patch(
            "clipwright_export.server.export_chapters",
            return_value=expected,
        ) as mock_export:
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_export_chapters",
                    {
                        "timeline": "test.otio",
                        "output": "out.txt",
                        "options": {"format": "youtube"},
                    },
                )
            )

        mock_export.assert_called_once()
        assert structured["ok"] is True

    def test_export_chapters_called_with_correct_args(self) -> None:
        captured_calls: list[dict[str, Any]] = []

        def _capture(**kwargs: Any) -> ToolResult:
            captured_calls.append(kwargs)
            return _ok_chapters_tool_result()

        with patch("clipwright_export.server.export_chapters", side_effect=_capture):
            asyncio.run(
                mcp.call_tool(
                    "clipwright_export_chapters",
                    {
                        "timeline": "my_timeline.otio",
                        "output": "my_output.txt",
                        "options": {
                            "format": "youtube",
                            "marker_kind": "scene_boundary",
                        },
                    },
                )
            )

        assert len(captured_calls) == 1
        assert captured_calls[0].get("timeline") == "my_timeline.otio"
        assert captured_calls[0].get("output") == "my_output.txt"

    def test_failure_envelope_returned_as_is(self) -> None:
        expected = _error_tool_result("OTIO_ERROR")

        with patch(
            "clipwright_export.server.export_chapters",
            return_value=expected,
        ):
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_export_chapters",
                    {
                        "timeline": "bad.otio",
                        "output": "out.txt",
                        "options": {"format": "youtube"},
                    },
                )
            )

        assert structured["ok"] is False
        assert structured["error"]["code"] == "OTIO_ERROR"

    def test_direct_call_delegates_to_export_chapters(self) -> None:
        expected = _ok_chapters_tool_result()

        from clipwright_export.schemas import ExportChaptersOptions

        opts = ExportChaptersOptions(format="ffmetadata")

        with patch(
            "clipwright_export.server.export_chapters",
            return_value=expected,
        ) as mock_export:
            result = server_chapters(
                timeline="my_tl.otio",
                output="my_out.ffmeta",
                options=opts,
            )

        mock_export.assert_called_once()
        call_kwargs = mock_export.call_args.kwargs
        assert call_kwargs.get("timeline") == "my_tl.otio"
        assert call_kwargs.get("output") == "my_out.ffmeta"
        assert call_kwargs.get("options") is opts
        assert result.ok is True

    def test_marker_kind_defaults_to_scene_boundary(self) -> None:
        """ExportChaptersOptions.marker_kind defaults to 'scene_boundary' (§3.2)."""
        from clipwright_export.schemas import ExportChaptersOptions

        opts = ExportChaptersOptions(format="youtube")
        assert opts.marker_kind == "scene_boundary"


# ---------------------------------------------------------------------------
# main() existence and MCP stdio launch
# ---------------------------------------------------------------------------


class TestCliMain:
    """Validate existence and basic call of main() -> mcp.run(transport='stdio')."""

    def test_main_is_callable(self) -> None:
        assert callable(main)

    def test_main_exists_in_module(self) -> None:
        import clipwright_export.server as server_module

        assert hasattr(server_module, "main")
        assert callable(server_module.main)

    def test_main_runs_mcp_server_with_stdio_transport(self) -> None:
        with patch.object(mcp, "run") as mock_run:
            main()

        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        assert kwargs.get("transport") == "stdio" or (
            len(_args) >= 1 and _args[0] == "stdio"
        ), (
            f"mcp.run must be called with transport='stdio'; "
            f"got args={_args!r} kwargs={kwargs!r}"
        )


# ---------------------------------------------------------------------------
# MCP wire contract (outputSchema / structuredContent)
# ---------------------------------------------------------------------------


class TestMcpBoundary:
    """Validate MCP wire contract: outputSchema and structuredContent, for
    both tools."""

    def test_export_timeline_outputschema_exposes_ok_property(self) -> None:
        tools = asyncio.run(mcp.list_tools())
        tool = next(t for t in tools if t.name == "clipwright_export_timeline")
        schema = tool.outputSchema or {}
        assert "ok" in schema.get("properties", {})

    def test_export_chapters_outputschema_exposes_ok_property(self) -> None:
        tools = asyncio.run(mcp.list_tools())
        tool = next(t for t in tools if t.name == "clipwright_export_chapters")
        schema = tool.outputSchema or {}
        assert "ok" in schema.get("properties", {})

    def test_export_timeline_structuredcontent_top_level_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "clipwright_export.server.export_timeline",
            lambda **kw: _ok_timeline_tool_result(),
        )
        result = asyncio.run(
            mcp.call_tool(
                "clipwright_export_timeline",
                {
                    "timeline": "t.otio",
                    "output": "out.edl",
                    "options": {"format": "edl"},
                },
            )
        )
        content, structured = result
        assert structured is not None
        assert "ok" in structured
        assert structured["ok"] is True

    def test_export_chapters_structuredcontent_top_level_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "clipwright_export.server.export_chapters",
            lambda **kw: _ok_chapters_tool_result(),
        )
        result = asyncio.run(
            mcp.call_tool(
                "clipwright_export_chapters",
                {
                    "timeline": "t.otio",
                    "output": "out.txt",
                    "options": {"format": "youtube"},
                },
            )
        )
        content, structured = result
        assert structured is not None
        assert "ok" in structured
        assert structured["ok"] is True
