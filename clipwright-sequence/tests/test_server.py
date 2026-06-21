"""test_server.py — Red-phase tests for clipwright-sequence server.py (MCP wrapper).

All tests are expected to FAIL (ImportError / ModuleNotFoundError) until
impl-server lands, because clipwright_sequence.server does not yet exist.
The correct Red reason is: the server module is missing.

Architecture references:
  - architecture-report-20260621-205501.md §2 ADR-SEQ-2 (server signature / annotations)
  - §V2.9 (DC-AM-003 approx total mentioned in docstring)
  - §V2.5b (DC-AS-005 symlink unsupported mentioned in docstring)
  - §V2.12 (DC-GP-003 clips Field max_length=1000)

Target contract:
  - FastMCP("clipwright-sequence") registers tool "clipwright_build_sequence".
  - annotations: readOnlyHint=True, destructiveHint=False,
      idempotentHint=True, openWorldHint=False.
  - clips Annotated Field carries max_length=1000 (schema-layer guard DC-GP-003).
  - Pure delegation: clipwright_build_sequence(clips, output) calls
      build_sequence(clips=clips, output=output) and returns its ToolResult unchanged.
  - main() exists and calls mcp.run(transport="stdio").
  - docstring/Field text notes approx total (DC-AM-003) and that symlink
    sources are unsupported (DC-AS-005).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from clipwright.schemas import ToolError, ToolResult

from clipwright_sequence.schemas import SequenceClip

# ---------------------------------------------------------------------------
# Attempt to import server.py (_SERVER_AVAILABLE = False if not implemented)
# ---------------------------------------------------------------------------

try:
    from clipwright_sequence.server import clipwright_build_sequence as server_build
    from clipwright_sequence.server import main, mcp

    _SERVER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _SERVER_AVAILABLE = False

# Mark all tests as xfail unless server.py is available
pytestmark = pytest.mark.xfail(
    not _SERVER_AVAILABLE,
    reason="server.py is not implemented yet",
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


def _make_clip(media: str = "video.mp4") -> SequenceClip:
    """Return a minimal SequenceClip for test use."""
    return SequenceClip(media=media)


# ---------------------------------------------------------------------------
# MCP registration tests
# ---------------------------------------------------------------------------


class TestMcpRegistration:
    """Validate that clipwright_build_sequence is registered in the FastMCP instance."""

    def test_tool_is_registered(self) -> None:
        """clipwright_build_sequence must be registered in mcp."""
        tool = mcp._tool_manager.get_tool("clipwright_build_sequence")  # noqa: SLF001
        assert tool is not None, "clipwright_build_sequence must be registered in mcp"

    def test_mcp_server_name(self) -> None:
        """The FastMCP instance must be named 'clipwright-sequence' (ADR-SEQ-2)."""
        assert mcp.name == "clipwright-sequence"


# ---------------------------------------------------------------------------
# MCP annotations tests (ADR-SEQ-2 / requirements §2.7)
# ---------------------------------------------------------------------------


class TestMcpAnnotations:
    """Validate MCP annotations for the clipwright_build_sequence tool.

    Per ADR-SEQ-2 / requirements §2.7:
      readOnlyHint=True  (does not modify input media or OTIO)
      destructiveHint=False  (non-destructive; generates new .otio only)
      idempotentHint=True  (same inputs -> same timeline)
      openWorldHint=False  (no external network access; local files only)
    """

    def _get_annotations(self) -> Any:
        # Relies on FastMCP private API (_tool_manager).
        # May break on FastMCP upgrades; migrate to public API when available.
        tool = mcp._tool_manager.get_tool("clipwright_build_sequence")  # noqa: SLF001
        assert tool is not None, "clipwright_build_sequence must be registered in mcp"
        return tool.annotations

    def test_read_only_hint_is_true(self) -> None:
        """readOnlyHint=True: does not modify input media or OTIO (ADR-SEQ-2)."""
        ann = self._get_annotations()
        assert ann.readOnlyHint is True

    def test_destructive_hint_is_false(self) -> None:
        """destructiveHint=False: input media and existing OTIO remain unchanged."""
        ann = self._get_annotations()
        assert ann.destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        """idempotentHint=True: same input clips + output -> same OTIO (ADR-SEQ-2)."""
        ann = self._get_annotations()
        assert ann.idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False: no external network access; local files only."""
        ann = self._get_annotations()
        assert ann.openWorldHint is False


# ---------------------------------------------------------------------------
# clips Field max_length=1000 introspection (DC-GP-003 schema layer)
# ---------------------------------------------------------------------------


class TestClipsFieldMaxLength:
    """Validate that the clips Annotated Field carries max_length=1000 (DC-GP-003).

    The Field constraint is the schema-layer guard.  build_sequence also has
    an orchestration-layer guard, but this test verifies the server Field itself.
    """

    def test_clips_field_max_length_is_1000(self) -> None:
        """clips Annotated Field must carry max_length=1000 at the schema layer."""
        tool = mcp._tool_manager.get_tool("clipwright_build_sequence")  # noqa: SLF001
        assert tool is not None
        # The inputSchema is generated by FastMCP from the Annotated Field.
        # The clips array must have maxItems=1000 in the JSON schema.
        input_schema = tool.parameters  # dict (JSON schema)
        clips_schema = input_schema.get("properties", {}).get("clips", {})
        assert clips_schema.get("maxItems") == 1000, (
            "clips Field must carry max_length=1000 (DC-GP-003); "
            f"got maxItems={clips_schema.get('maxItems')!r}"
        )


# ---------------------------------------------------------------------------
# Delegation tests (server must not contain business logic)
# ---------------------------------------------------------------------------


class TestMcpToolDelegation:
    """Validate delegation to sequence.build_sequence; server must be a pure wrapper.

    ADR-SEQ-2: server has zero business logic or error conversion.
    build_sequence is the sole error boundary.
    """

    def test_success_delegates_to_build_sequence(self) -> None:
        """On success, must call and return build_sequence result unchanged."""
        expected = _ok_tool_result(
            summary=(
                "Assembled a 2-clip sequence (approx total 10.0s) from 2 source(s). "
                "Generated out.otio. Pass it to clipwright-render to concatenate into a single video."
            ),
            data={
                "clip_count": 2,
                "total_duration_sec": 10.0,
                "unique_source_count": 2,
            },
            artifacts=[{"role": "timeline", "path": "out.otio", "format": "otio"}],
        )

        clips = [_make_clip("a.mp4"), _make_clip("b.mp4")]
        with patch(
            "clipwright_sequence.server.build_sequence",
            return_value=expected,
        ) as mock_build:
            result = server_build(clips=clips, output="out.otio")

        mock_build.assert_called_once()
        assert result.ok is True
        assert result.summary is not None
        assert "approx total" in (result.summary or "")

    def test_build_sequence_called_with_correct_clips_and_output(self) -> None:
        """clips and output arguments must be forwarded to build_sequence unchanged."""
        clips = [_make_clip("a.mp4"), _make_clip("b.mp4")]
        with patch(
            "clipwright_sequence.server.build_sequence",
            return_value=_ok_tool_result(),
        ) as mock_build:
            server_build(clips=clips, output="/path/to/out.otio")

        call_kwargs = mock_build.call_args.kwargs
        assert call_kwargs["clips"] is clips
        assert call_kwargs["output"] == "/path/to/out.otio"

    def test_error_envelope_passthrough_file_not_found(self) -> None:
        """When build_sequence returns FILE_NOT_FOUND error, server must return it as-is."""
        expected = _error_tool_result("FILE_NOT_FOUND")

        clips = [_make_clip("missing.mp4")]
        with patch(
            "clipwright_sequence.server.build_sequence",
            return_value=expected,
        ):
            result = server_build(clips=clips, output="out.otio")

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "FILE_NOT_FOUND"

    def test_error_envelope_passthrough_invalid_input(self) -> None:
        """When build_sequence returns INVALID_INPUT, server must return it as-is."""
        expected = ToolResult(
            ok=False,
            error=ToolError(
                code="INVALID_INPUT",
                message="No clips were provided.",
                hint="Provide at least one SequenceClip in the clips list.",
            ),
        )

        clips: list[SequenceClip] = []
        with patch(
            "clipwright_sequence.server.build_sequence",
            return_value=expected,
        ):
            result = server_build(clips=clips, output="out.otio")

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "INVALID_INPUT"
        assert result.error.message
        assert result.error.hint

    def test_error_envelope_passthrough_path_not_allowed(self) -> None:
        """When build_sequence returns PATH_NOT_ALLOWED, server must return it as-is."""
        expected = _error_tool_result("PATH_NOT_ALLOWED")

        clips = [_make_clip("outside.mp4")]
        with patch(
            "clipwright_sequence.server.build_sequence",
            return_value=expected,
        ):
            result = server_build(clips=clips, output="out.otio")

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "PATH_NOT_ALLOWED"

    def test_error_envelope_passthrough_dependency_missing(self) -> None:
        """When build_sequence returns DEPENDENCY_MISSING, server must return it as-is."""
        expected = _error_tool_result("DEPENDENCY_MISSING")

        clips = [_make_clip("video.mp4")]
        with patch(
            "clipwright_sequence.server.build_sequence",
            return_value=expected,
        ):
            result = server_build(clips=clips, output="out.otio")

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "DEPENDENCY_MISSING"
        assert result.error.message
        assert result.error.hint

    def test_ok_envelope_structure(self) -> None:
        """Success envelope must contain ok/summary/data/artifacts/warnings."""
        expected = _ok_tool_result(
            summary=(
                "Assembled a 1-clip sequence (approx total 5.0s) from 1 source(s). "
                "Generated out.otio. Pass it to clipwright-render to concatenate into a single video."
            ),
            data={
                "clip_count": 1,
                "total_duration_sec": 5.0,
                "unique_source_count": 1,
            },
            artifacts=[{"role": "timeline", "path": "out.otio", "format": "otio"}],
            warnings=[],
        )

        clips = [_make_clip("video.mp4")]
        with patch(
            "clipwright_sequence.server.build_sequence",
            return_value=expected,
        ):
            result = server_build(clips=clips, output="out.otio")

        assert result.ok is True
        assert result.summary is not None
        assert result.data is not None
        assert result.artifacts is not None
        assert result.warnings is not None


# ---------------------------------------------------------------------------
# Docstring / Field description content (DC-AM-003 / DC-AS-005)
# ---------------------------------------------------------------------------


class TestDocstringContent:
    """Validate that the tool docstring and Field descriptions mention required items.

    DC-AM-003: total_duration_sec is an approximation; the docstring must
    mention this (e.g. "approx" or "estimate").
    DC-AS-005: symlink sources are unsupported; the docstring or Field
    description must mention this explicitly.
    """

    def _get_tool_docstring(self) -> str:
        """Return the docstring of clipwright_build_sequence."""
        return server_build.__doc__ or ""

    def test_docstring_mentions_approx_total(self) -> None:
        """Docstring must mention that total_duration_sec is approximate (DC-AM-003)."""
        doc = self._get_tool_docstring()
        assert "approx" in doc.lower() or "estimate" in doc.lower(), (
            "clipwright_build_sequence docstring must mention that total_duration_sec "
            "is approximate (DC-AM-003). Got: " + repr(doc[:200])
        )

    def test_docstring_mentions_symlink_unsupported(self) -> None:
        """Docstring must mention that symlink sources are unsupported (DC-AS-005)."""
        doc = self._get_tool_docstring()
        assert "symlink" in doc.lower(), (
            "clipwright_build_sequence docstring must mention that symlink sources "
            "are not supported (DC-AS-005). Got: " + repr(doc[:200])
        )

    def test_clips_field_description_exists(self) -> None:
        """The clips Field must have a description string."""
        tool = mcp._tool_manager.get_tool("clipwright_build_sequence")  # noqa: SLF001
        assert tool is not None
        input_schema = tool.parameters
        clips_schema = input_schema.get("properties", {}).get("clips", {})
        # The description is surfaced via the array items or the field itself
        # (FastMCP may hoist it to the array or keep it on the property).
        # Either way, a non-empty description must exist somewhere in clips_schema.
        desc = clips_schema.get("description", "")
        assert isinstance(desc, str) and len(desc) > 0, (
            "clips Field must have a non-empty description"
        )


# ---------------------------------------------------------------------------
# main() existence and MCP stdio launch
# ---------------------------------------------------------------------------


class TestCliMain:
    """Validate existence and basic call of main() -> mcp.run(transport='stdio')."""

    def test_main_is_callable(self) -> None:
        """main() must exist and be callable."""
        assert callable(main)

    def test_main_exists_in_module(self) -> None:
        """main must be defined in the clipwright_sequence.server module."""
        import clipwright_sequence.server as server_module

        assert hasattr(server_module, "main")
        assert callable(server_module.main)

    def test_main_runs_mcp_server_with_stdio_transport(self) -> None:
        """main() must call mcp.run(transport='stdio') (MCP stdio launch).

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
        tool = next((t for t in tools if t.name == "clipwright_build_sequence"), None)
        assert tool is not None, "clipwright_build_sequence must appear in list_tools()"
        schema = tool.outputSchema or {}
        assert "ok" in schema.get("properties", {}), (
            "outputSchema must expose 'ok' property"
        )

    def test_structuredcontent_top_level_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_tool must return structuredContent with top-level 'ok' key."""
        monkeypatch.setattr(
            "clipwright_sequence.server.build_sequence",
            lambda **kw: ToolResult(
                ok=True,
                summary="Assembled a 1-clip sequence (approx total 5.0s) from 1 source(s). Generated out.otio. Pass it to clipwright-render to concatenate into a single video.",
                data={
                    "clip_count": 1,
                    "total_duration_sec": 5.0,
                    "unique_source_count": 1,
                },
                artifacts=[],
                warnings=[],
            ),
        )
        result = asyncio.run(
            mcp.call_tool(
                "clipwright_build_sequence",
                {
                    "clips": [{"media": "video.mp4"}],
                    "output": "out.otio",
                },
            )
        )
        content, structured = result
        assert structured is not None
        assert "ok" in structured
        assert structured["ok"] is True
