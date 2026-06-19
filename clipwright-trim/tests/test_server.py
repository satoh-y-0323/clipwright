"""test_server.py — Red tests for clipwright-trim server.py (MCP wrapper).

Target:
  - clipwright_trim tool must be registered in MCP and delegate to trim.trim_media
  - MCP annotations (FR-10):
    readOnlyHint=True / destructiveHint=False / idempotentHint=True / openWorldHint=False
  - options=None must be resolved to TrimOptions() before delegation (§2.1 / ADR-4)
  - options=None (both keep/drop empty) -> full-duration passthrough -> ok=True (confirmed spec)
  - No business logic or error conversion in server; trim_media is the sole boundary
  - Existence and callability of main() -> mcp.run(transport="stdio")
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
    from clipwright_trim.server import clipwright_trim as server_trim
    from clipwright_trim.server import main, mcp

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
# MCP annotations tests (FR-10)
# ---------------------------------------------------------------------------


class TestMcpAnnotations:
    """Validate MCP annotations for the clipwright_trim tool.

    Per FR-10 / §2.1:
      readOnlyHint=True  (does not modify input media or OTIO)
      destructiveHint=False  (non-destructive; generates new .otio only)
      idempotentHint=True  (same inputs -> same timeline)
      openWorldHint=False  (no external network access; local files only)
    """

    def _get_annotations(self) -> Any:
        # CR L-1 analogue: relies on FastMCP private API (_tool_manager).
        # May break on FastMCP upgrades; migrate to public API when available.
        tool = mcp._tool_manager.get_tool("clipwright_trim")  # noqa: SLF001
        assert tool is not None, "clipwright_trim must be registered in mcp"
        return tool.annotations

    def test_tool_is_registered(self) -> None:
        """clipwright_trim must be registered in mcp."""
        tool = mcp._tool_manager.get_tool("clipwright_trim")  # noqa: SLF001
        assert tool is not None

    def test_read_only_hint_is_true(self) -> None:
        """readOnlyHint=True: trim does not modify input media (FR-10)."""
        ann = self._get_annotations()
        assert ann.readOnlyHint is True

    def test_destructive_hint_is_false(self) -> None:
        """destructiveHint=False: input media and existing OTIO remain unchanged (FR-10)."""
        ann = self._get_annotations()
        assert ann.destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        """idempotentHint=True: same input + same parameters -> same OTIO (FR-10)."""
        ann = self._get_annotations()
        assert ann.idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False: no external network access; local files only (FR-10)."""
        ann = self._get_annotations()
        assert ann.openWorldHint is False


# ---------------------------------------------------------------------------
# options=None passthrough resolution (§2.1 / ADR-4 confirmed spec)
# ---------------------------------------------------------------------------


class TestOptionsNoneResolution:
    """Validate that options=None is resolved to TrimOptions() before delegation.

    Confirmed spec (plan-report §0, item 1):
      options=None -> TrimOptions() (both keep/drop empty) -> full-duration passthrough -> ok=True.
    Server must NOT pass None to trim_media; it must resolve it first.
    """

    def test_options_none_passes_trim_options_instance_to_trim_media(self) -> None:
        """options=None must be resolved to TrimOptions() and passed to trim_media."""
        from clipwright_trim.schemas import TrimOptions

        expected = _ok_tool_result(
            summary="Kept 1 range(s) (total 10.0s) from source duration 10.0s (keep mode). Generated out.otio.",
            data={
                "clip_count": 1,
                "kept_duration_sec": 10.0,
                "source_duration_sec": 10.0,
                "mode": "keep",
            },
            artifacts=[{"role": "timeline", "path": "out.otio", "format": "otio"}],
        )

        with patch(
            "clipwright_trim.server.trim_media",
            return_value=expected,
        ) as mock_trim:
            result = server_trim(
                media="video.mp4",
                output="out.otio",
                options=None,
            )

        mock_trim.assert_called_once()
        call_kwargs = mock_trim.call_args.kwargs
        # options must be a TrimOptions instance (not None)
        assert "options" in call_kwargs
        resolved = call_kwargs["options"]
        assert isinstance(resolved, TrimOptions), (
            "server must resolve options=None to TrimOptions() before calling trim_media"
        )
        assert result.ok is True

    def test_options_none_resolved_options_has_empty_keep_drop(self) -> None:
        """Resolved TrimOptions() from options=None must have empty keep and drop lists."""
        from clipwright_trim.schemas import TrimOptions

        with patch(
            "clipwright_trim.server.trim_media",
            return_value=_ok_tool_result(),
        ) as mock_trim:
            server_trim(
                media="video.mp4",
                output="out.otio",
                options=None,
            )

        call_kwargs = mock_trim.call_args.kwargs
        resolved: TrimOptions = call_kwargs["options"]
        assert resolved.keep == []
        assert resolved.drop == []
        assert resolved.padding_sec == 0.0

    def test_options_none_yields_ok_true_passthrough(self) -> None:
        """options=None (both empty) must yield ok=True (full-duration passthrough, not error).

        Confirmed spec (plan-report §0, item 1): ADR-4 reject is NOT adopted.
        Both-empty is passthrough, not INVALID_INPUT.
        """
        expected = _ok_tool_result(
            data={
                "clip_count": 1,
                "kept_duration_sec": 10.0,
                "source_duration_sec": 10.0,
                "mode": "keep",
            }
        )

        with patch(
            "clipwright_trim.server.trim_media",
            return_value=expected,
        ):
            result = server_trim(
                media="video.mp4",
                output="out.otio",
                options=None,
            )

        assert result.ok is True

    def test_explicit_trim_options_passed_through_unchanged(self) -> None:
        """When options is already a TrimOptions instance, it must be passed to trim_media as-is."""
        from clipwright_trim.schemas import TrimOptions, TrimRange

        opts = TrimOptions(keep=[TrimRange(start_sec=1.0, end_sec=5.0)])

        with patch(
            "clipwright_trim.server.trim_media",
            return_value=_ok_tool_result(),
        ) as mock_trim:
            server_trim(
                media="video.mp4",
                output="out.otio",
                options=opts,
            )

        call_kwargs = mock_trim.call_args.kwargs
        assert call_kwargs["options"] is opts


# ---------------------------------------------------------------------------
# MCP tool delegation: server has no business logic (§2.1)
# ---------------------------------------------------------------------------


class TestMcpToolDelegation:
    """Validate delegation to trim.trim_media; server must not perform error conversion.

    Server is a thin wrapper: options resolution + call to trim_media only.
    Any error envelope produced by trim_media must be returned as-is.
    """

    def test_success_delegates_to_trim_media(self) -> None:
        """On success, must call and return trim_media result unchanged."""
        expected = _ok_tool_result(summary="trimmed ok")

        with patch(
            "clipwright_trim.server.trim_media",
            return_value=expected,
        ) as mock_trim:
            result = server_trim(
                media="video.mp4",
                output="out.otio",
                options=None,
            )

        mock_trim.assert_called_once()
        assert result.ok is True
        assert result.summary == "trimmed ok"

    def test_trim_media_called_with_correct_media_and_output(self) -> None:
        """media and output arguments must be forwarded to trim_media unchanged."""
        with patch(
            "clipwright_trim.server.trim_media",
            return_value=_ok_tool_result(),
        ) as mock_trim:
            server_trim(
                media="/path/to/video.mp4",
                output="/path/to/out.otio",
                options=None,
            )

        call_kwargs = mock_trim.call_args.kwargs
        assert call_kwargs["media"] == "/path/to/video.mp4"
        assert call_kwargs["output"] == "/path/to/out.otio"

    def test_error_envelope_passthrough_file_not_found(self) -> None:
        """When trim_media returns FILE_NOT_FOUND error, server must return it as-is."""
        expected = _error_tool_result("FILE_NOT_FOUND")

        with patch(
            "clipwright_trim.server.trim_media",
            return_value=expected,
        ):
            result = server_trim(
                media="missing.mp4",
                output="out.otio",
                options=None,
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "FILE_NOT_FOUND"

    def test_error_envelope_passthrough_dependency_missing(self) -> None:
        """When trim_media returns DEPENDENCY_MISSING error, server must return it as-is."""
        expected = _error_tool_result("DEPENDENCY_MISSING")

        with patch(
            "clipwright_trim.server.trim_media",
            return_value=expected,
        ):
            result = server_trim(
                media="video.mp4",
                output="out.otio",
                options=None,
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "DEPENDENCY_MISSING"
        assert result.error.message
        assert result.error.hint

    def test_error_envelope_passthrough_invalid_input(self) -> None:
        """When trim_media returns INVALID_INPUT error, server must return it as-is."""
        expected = ToolResult(
            ok=False,
            error=ToolError(
                code="INVALID_INPUT",
                message="Both keep and drop were provided.",
                hint="Provide exactly one of keep or drop.",
            ),
        )

        with patch(
            "clipwright_trim.server.trim_media",
            return_value=expected,
        ):
            result = server_trim(
                media="video.mp4",
                output="out.otio",
                options=None,
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "INVALID_INPUT"
        assert result.error.message
        assert result.error.hint

    def test_ok_envelope_structure(self) -> None:
        """Success envelope must contain ok/summary/data/artifacts/warnings."""
        expected = _ok_tool_result(
            summary=(
                "Kept 2 range(s) (total 8.0s) from source duration 30.0s (keep mode). "
                "Generated out.otio."
            ),
            data={
                "clip_count": 2,
                "kept_duration_sec": 8.0,
                "source_duration_sec": 30.0,
                "mode": "keep",
            },
            artifacts=[{"role": "timeline", "path": "out.otio", "format": "otio"}],
            warnings=[],
        )

        with patch(
            "clipwright_trim.server.trim_media",
            return_value=expected,
        ):
            result = server_trim(
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
# main() existence and MCP stdio launch (DC-GP-002 equivalent)
# ---------------------------------------------------------------------------


class TestCliMain:
    """Validate existence and basic call of main() -> mcp.run(transport='stdio')."""

    def test_main_is_callable(self) -> None:
        """main() must exist and be callable."""
        assert callable(main)

    def test_main_exists_in_module(self) -> None:
        """main must be defined in the clipwright_trim.server module."""
        import clipwright_trim.server as server_module

        assert hasattr(server_module, "main")
        assert callable(server_module.main)

    def test_main_runs_mcp_server_with_stdio_transport(self) -> None:
        """main() must call mcp.run(transport='stdio') (DC-GP-002: MCP stdio launch).

        Does not perform actual stdio launch; confirmed via mock of mcp.run.
        """
        with patch.object(mcp, "run") as mock_run:
            main()

        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        assert kwargs.get("transport") == "stdio" or (
            len(_args) >= 1 and _args[0] == "stdio"
        )


# ---------------------------------------------------------------------------
# MCP boundary tests: outputSchema typing and structuredContent
# ---------------------------------------------------------------------------


class TestMcpBoundary:
    """Validate MCP wire contract: typed outputSchema and structuredContent."""

    def test_outputschema_exposes_ok_property(self) -> None:
        """outputSchema must expose 'ok' property (typed ToolResult via FastMCP)."""
        tools = asyncio.run(mcp.list_tools())
        tool = next((t for t in tools if t.name == "clipwright_trim"), None)
        assert tool is not None, "clipwright_trim must appear in list_tools()"
        schema = tool.outputSchema or {}
        assert "ok" in schema.get("properties", {}), (
            "outputSchema must expose 'ok' property"
        )

    def test_structuredcontent_top_level_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_tool must return structuredContent with top-level 'ok' key."""
        monkeypatch.setattr(
            "clipwright_trim.server.trim_media",
            lambda **kw: ToolResult(
                ok=True,
                summary="Trimmed.",
                data={},
                artifacts=[],
                warnings=[],
            ),
        )
        result = asyncio.run(
            mcp.call_tool(
                "clipwright_trim",
                {"media": "m.mp4", "output": "out.otio"},
            )
        )
        content, structured = result
        assert structured is not None
        assert "ok" in structured
        assert structured["ok"] is True
