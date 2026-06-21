"""test_server.py — Tests for clipwright-overlay server.py (MCP wrapper).

Validates the FastMCP registration, MCP annotations, options=None early error,
delegation contract, main() stdio launch, and docstring content requirements
for clipwright_add_overlay.

Architecture references:
  - architecture-report-20260622-013708.md §V2 (AUTHORITY, overrides v1)
  - v1 §7 ADR-OV-7 (annotations 4 values, envelope)
  - V2-4 [DC-AM-001 Med] readOnlyHint=True + MANDATED loudness-style docstring

Target contract:
  - FastMCP("clipwright-overlay") registers tool "clipwright_add_overlay".
  - annotations: readOnlyHint=True, destructiveHint=False,
      idempotentHint=True, openWorldHint=False (V2-4).
  - options is None -> error_result("INVALID_INPUT", ...) WITHOUT calling
      add_overlay; message/hint must mention required fields
      (image_path, start_sec, duration_sec).
  - Pure delegation: clipwright_add_overlay(timeline, output, options) calls
      add_overlay(timeline=..., output=..., options=...) and returns its
      ToolResult unchanged.
  - main() exists and calls mcp.run(transport="stdio").
  - Docstring must contain a loudness-style readOnlyHint=True rationale:
      the tool writes ONLY a new .otio; input media and timeline are never
      modified; the new-file write is OUTSIDE the readOnly scope.
      (V2-4 MANDATE: so the True choice is recorded and not re-flagged in
      review. The text/bgm asymmetry is intentional.)

Note: clipwright_overlay.server does NOT currently exist (Wave 4 deliverable).
All tests are expected to FAIL (Red) until impl-overlay-server lands —
specifically because the module is missing (ImportError), NOT due to a logic bug.
The conditional-xfail pattern below auto-deactivates once server.py is
implemented, so no test edit is needed in Wave 4.
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
    from clipwright_overlay.server import clipwright_add_overlay as server_add
    from clipwright_overlay.server import main, mcp

    _SERVER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _SERVER_AVAILABLE = False

# Mark all tests as xfail unless server.py is available.
# strict=True means an unexpected PASS will be reported as XPASS (error),
# which catches accidental early-Green before the impl lands.
pytestmark = pytest.mark.xfail(
    not _SERVER_AVAILABLE,
    reason="server.py is not implemented yet",
    strict=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_tool_result(**kwargs: Any) -> ToolResult:
    """Return a success ToolResult template for clipwright_add_overlay."""
    defaults: dict[str, Any] = {
        "ok": True,
        "summary": (
            "Added image overlay 'logo.png' at 1.0s for 3.0s. "
            "Timeline now has 1 image overlay(s). Output: out.otio."
        ),
        "data": {
            "applied": 1,
            "overlay_count": 1,
            "start_sec": 1.0,
            "duration_sec": 3.0,
        },
        "artifacts": [
            {
                "role": "timeline",
                "path": "/tmp/out.otio",
                "format": "otio",
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
    """Validate that clipwright_add_overlay is registered in the FastMCP instance."""

    def test_tool_is_registered(self) -> None:
        """clipwright_add_overlay must be registered in mcp."""
        tool = mcp._tool_manager.get_tool("clipwright_add_overlay")  # noqa: SLF001
        assert tool is not None, "clipwright_add_overlay must be registered in mcp"

    def test_mcp_server_name(self) -> None:
        """The FastMCP instance must be named 'clipwright-overlay' (ADR-OV-1)."""
        assert mcp.name == "clipwright-overlay"

    def test_tool_name_in_list_tools(self) -> None:
        """clipwright_add_overlay must appear in mcp.list_tools()."""
        tools = asyncio.run(mcp.list_tools())
        names = [t.name for t in tools]
        assert "clipwright_add_overlay" in names


# ---------------------------------------------------------------------------
# MCP annotations tests (V2-4 / ADR-OV-7)
# ---------------------------------------------------------------------------


class TestMcpAnnotations:
    """Validate MCP annotations for the clipwright_add_overlay tool.

    Per v1 §7 ADR-OV-7, AS REFINED BY V2-4 (AUTHORITY):
      readOnlyHint=True  (writes only a new .otio; existing resources untouched)
      destructiveHint=False (input timeline and media are non-destructive)
      idempotentHint=True  (same input + options -> same output timeline)
      openWorldHint=False  (local filesystem only; annotate layer is subprocess-free)

    NOTE: readOnlyHint=True DIFFERS from clipwright-text (False).
    This is the intentional overlay choice confirmed in V2-4.
    """

    def _get_annotations(self) -> Any:
        # Relies on FastMCP private API (_tool_manager).
        # May break on FastMCP upgrades; migrate to public API when available.
        tool = mcp._tool_manager.get_tool("clipwright_add_overlay")  # noqa: SLF001
        assert tool is not None, "clipwright_add_overlay must be registered in mcp"
        return tool.annotations

    def test_read_only_hint_is_true(self) -> None:
        """readOnlyHint=True: writes only a new .otio; input never modified (V2-4).

        This is intentionally True (unlike clipwright-text which is False).
        overlay is an annotate-layer tool; the new-file write is outside the
        readOnly scope per V2-4 rationale.
        """
        ann = self._get_annotations()
        assert ann.readOnlyHint is True

    def test_destructive_hint_is_false(self) -> None:
        """destructiveHint=False: input timeline and media remain unchanged."""
        ann = self._get_annotations()
        assert ann.destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        """idempotentHint=True: same input + options -> same output."""
        ann = self._get_annotations()
        assert ann.idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False: local filesystem only; annotate layer is subprocess-free."""
        ann = self._get_annotations()
        assert ann.openWorldHint is False


# ---------------------------------------------------------------------------
# options=None early error (mirror clipwright-text server.py L61-69 pattern)
# ---------------------------------------------------------------------------


class TestOptionsNone:
    """When options=None, server must return INVALID_INPUT WITHOUT calling add_overlay."""

    def test_options_none_returns_invalid_input(self) -> None:
        """options=None must return ok=False with INVALID_INPUT."""
        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_add_overlay",
                {"timeline": "test.otio", "output": "out.otio"},
            )
        )
        assert structured["ok"] is False
        error = structured.get("error") or {}
        assert error.get("code") == "INVALID_INPUT"

    def test_options_none_hint_mentions_required_fields(self) -> None:
        """options=None hint must mention required fields: image_path, start_sec, duration_sec."""
        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_add_overlay",
                {"timeline": "test.otio", "output": "out.otio"},
            )
        )
        assert structured["ok"] is False
        error = structured.get("error") or {}
        hint = error.get("hint") or ""
        # All three required fields must be mentioned
        assert "image_path" in hint, f"hint must mention 'image_path'; got: {hint!r}"
        assert "start_sec" in hint, f"hint must mention 'start_sec'; got: {hint!r}"
        assert "duration_sec" in hint, (
            f"hint must mention 'duration_sec'; got: {hint!r}"
        )

    def test_options_none_does_not_call_add_overlay(self) -> None:
        """options=None must NOT call add_overlay (early return before delegation)."""
        with patch("clipwright_overlay.server.add_overlay") as mock_add:
            asyncio.run(
                mcp.call_tool(
                    "clipwright_add_overlay",
                    {"timeline": "test.otio", "output": "out.otio"},
                )
            )
        mock_add.assert_not_called()

    def test_options_none_not_a_success(self) -> None:
        """options=None must never return ok=True."""
        content, structured = asyncio.run(
            mcp.call_tool(
                "clipwright_add_overlay",
                {"timeline": "test.otio", "output": "out.otio"},
            )
        )
        assert structured["ok"] is not True


# ---------------------------------------------------------------------------
# Delegation tests (server must not contain business logic)
# ---------------------------------------------------------------------------


class TestMcpToolDelegation:
    """Validate delegation to overlay.add_overlay; server must be a pure wrapper.

    ADR-OV-1: server has zero business logic. options=None is the only
    server-layer check; all other validation is in overlay.add_overlay.
    """

    def test_success_delegates_to_add_overlay(self) -> None:
        """On success, must call and return add_overlay result unchanged."""
        expected = _ok_tool_result()

        with patch(
            "clipwright_overlay.server.add_overlay",
            return_value=expected,
        ) as mock_add:
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_add_overlay",
                    {
                        "timeline": "test.otio",
                        "output": "out.otio",
                        "options": {
                            "image_path": "/tmp/logo.png",
                            "start_sec": 1.0,
                            "duration_sec": 3.0,
                        },
                    },
                )
            )

        mock_add.assert_called_once()
        assert structured["ok"] is True

    def test_add_overlay_called_with_correct_args(self) -> None:
        """add_overlay must be called with timeline, output, and options arguments."""
        captured_calls: list[dict[str, Any]] = []

        def _capture(**kwargs: Any) -> ToolResult:
            captured_calls.append(kwargs)
            return _ok_tool_result()

        with patch("clipwright_overlay.server.add_overlay", side_effect=_capture):
            asyncio.run(
                mcp.call_tool(
                    "clipwright_add_overlay",
                    {
                        "timeline": "my_timeline.otio",
                        "output": "my_output.otio",
                        "options": {
                            "image_path": "/tmp/logo.png",
                            "start_sec": 1.0,
                            "duration_sec": 3.0,
                        },
                    },
                )
            )

        assert len(captured_calls) == 1
        assert captured_calls[0].get("timeline") == "my_timeline.otio"
        assert captured_calls[0].get("output") == "my_output.otio"

    def test_failure_envelope_returned_as_is(self) -> None:
        """When add_overlay returns an error envelope, server returns it unchanged."""
        expected = _error_tool_result("FILE_NOT_FOUND")

        with patch(
            "clipwright_overlay.server.add_overlay",
            return_value=expected,
        ):
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_add_overlay",
                    {
                        "timeline": "missing.otio",
                        "output": "out.otio",
                        "options": {
                            "image_path": "/tmp/logo.png",
                            "start_sec": 1.0,
                            "duration_sec": 3.0,
                        },
                    },
                )
            )

        assert structured["ok"] is False
        assert structured.get("error") is not None
        assert structured["error"]["code"] == "FILE_NOT_FOUND"

    def test_path_not_allowed_envelope_passthrough(self) -> None:
        """When add_overlay returns PATH_NOT_ALLOWED, server must return it as-is."""
        expected = _error_tool_result("PATH_NOT_ALLOWED")

        with patch(
            "clipwright_overlay.server.add_overlay",
            return_value=expected,
        ):
            content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_add_overlay",
                    {
                        "timeline": "test.otio",
                        "output": "out.otio",
                        "options": {
                            "image_path": "/outside/logo.png",
                            "start_sec": 1.0,
                            "duration_sec": 3.0,
                        },
                    },
                )
            )

        assert structured["ok"] is False
        assert structured["error"]["code"] == "PATH_NOT_ALLOWED"

    def test_direct_call_delegates_to_add_overlay(self) -> None:
        """Direct call to server_add must delegate to add_overlay with all kwargs."""
        expected = _ok_tool_result()

        from clipwright_overlay.schemas import AddOverlayOptions

        opts = AddOverlayOptions(
            image_path="/tmp/logo.png",
            start_sec=1.0,
            duration_sec=3.0,
        )

        with patch(
            "clipwright_overlay.server.add_overlay",
            return_value=expected,
        ) as mock_add:
            result = server_add(
                timeline="my_tl.otio",
                output="my_out.otio",
                options=opts,
            )

        mock_add.assert_called_once()
        call_kwargs = mock_add.call_args.kwargs
        assert call_kwargs.get("timeline") == "my_tl.otio"
        assert call_kwargs.get("output") == "my_out.otio"
        assert call_kwargs.get("options") is opts
        assert result.ok is True


# ---------------------------------------------------------------------------
# Docstring content test (V2-4 loudness-style readOnlyHint=True rationale)
# ---------------------------------------------------------------------------


class TestDocstringContent:
    """Validate that the tool docstring contains the V2-4 MANDATED rationale.

    V2-4 (AUTHORITY): 'bare True' is insufficient. The docstring MUST contain
    a loudness-style rationale explaining why readOnlyHint=True is correct for
    a tool that writes a new file, so the choice is recorded and not re-flagged
    in future reviews.

    Required content (all three phrases must appear):
      - the tool writes only a new .otio (not readOnly in the strictest sense,
        but the new-file write is outside the readOnly scope)
      - input media and timeline are never modified
      - the new-file write is outside the readOnly scope (or equivalent)
    """

    def _get_tool_docstring(self) -> str:
        """Return the docstring of clipwright_add_overlay."""
        return server_add.__doc__ or ""

    def test_docstring_mentions_writes_only_new_otio(self) -> None:
        """Docstring must state the tool writes only a new .otio file (V2-4 rationale)."""
        doc = self._get_tool_docstring()
        doc_lower = doc.lower()
        assert ".otio" in doc_lower or "otio" in doc_lower, (
            "clipwright_add_overlay docstring must mention .otio output "
            "(V2-4 rationale). Got: " + repr(doc[:300])
        )
        # Must assert that it only writes NEW output
        assert "new" in doc_lower or "only" in doc_lower, (
            "clipwright_add_overlay docstring must mention writing only a new "
            "file (V2-4 rationale). Got: " + repr(doc[:300])
        )

    def test_docstring_mentions_input_never_modified(self) -> None:
        """Docstring must state input media/timeline are never modified (V2-4)."""
        doc = self._get_tool_docstring()
        doc_lower = doc.lower()
        # Must mention non-modification of input (never modified / unchanged / non-destructive)
        assert (
            "never modified" in doc_lower
            or "not modified" in doc_lower
            or "unchanged" in doc_lower
            or "non-destructive" in doc_lower
        ), (
            "clipwright_add_overlay docstring must state that input media and "
            "timeline are never modified (V2-4 rationale). Got: " + repr(doc[:300])
        )

    def test_docstring_mentions_outside_readonly_scope(self) -> None:
        """Docstring must explain the new-file write is outside readOnly scope (V2-4)."""
        doc = self._get_tool_docstring()
        doc_lower = doc.lower()
        # Must contain the 'outside the readonly scope' rationale or equivalent
        assert (
            "outside" in doc_lower
            or "readonly" in doc_lower.replace(" ", "")
            or "read-only" in doc_lower
            or "read only" in doc_lower
        ), (
            "clipwright_add_overlay docstring must mention that the new-file "
            "write is outside the readOnly scope (V2-4 rationale). "
            "Got: " + repr(doc[:300])
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
        """main must be defined in the clipwright_overlay.server module."""
        import clipwright_overlay.server as server_module

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
        ), (
            f"mcp.run must be called with transport='stdio'; "
            f"got args={_args!r} kwargs={kwargs!r}"
        )


# ---------------------------------------------------------------------------
# MCP wire contract (outputSchema / structuredContent)
# ---------------------------------------------------------------------------


class TestMcpBoundary:
    """Validate MCP wire contract: outputSchema and structuredContent."""

    def test_outputschema_exposes_ok_property(self) -> None:
        """outputSchema must expose 'ok' property (typed ToolResult via FastMCP)."""
        tools = asyncio.run(mcp.list_tools())
        tool = next(t for t in tools if t.name == "clipwright_add_overlay")
        schema = tool.outputSchema or {}
        assert "ok" in schema.get("properties", {}), (
            "outputSchema must expose 'ok' property"
        )

    def test_structuredcontent_top_level_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_tool must return structuredContent with top-level 'ok' key."""
        monkeypatch.setattr(
            "clipwright_overlay.server.add_overlay",
            lambda **kw: _ok_tool_result(),
        )
        result = asyncio.run(
            mcp.call_tool(
                "clipwright_add_overlay",
                {
                    "timeline": "t.otio",
                    "output": "out.otio",
                    "options": {
                        "image_path": "/tmp/logo.png",
                        "start_sec": 1.0,
                        "duration_sec": 3.0,
                    },
                },
            )
        )
        content, structured = result
        assert structured is not None
        assert "ok" in structured
        assert structured["ok"] is True
