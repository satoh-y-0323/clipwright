"""test_server.py — Tests for clipwright-wrap server.py (MCP + CLI).

Scope:
  - clipwright_wrap_captions tool is registered in MCP and delegates to wrap.wrap_captions
  - MCP annotations (WR-AD-10):
    readOnlyHint:true / destructiveHint:false / idempotentHint:true / openWorldHint:false
  - Success/failure envelope pass-through
  - When options is None, WrapCaptionsOptions() defaults are used (language="ja"/max_chars=16/max_lines=2)
  - main() calls mcp.run(transport="stdio")

DC-GP-001 language responsibility verification policy (important):
  server.py is a thin wrapper with no error-conversion responsibility at the MCP boundary
  (same pattern as transcribe server). Unsupported languages originate as Pydantic
  ValidationError at WrapCaptionsOptions construction time; server.py does not create
  an if-branch that re-validates language and converts it to INVALID_INPUT.
  These tests mock wrap.wrap_captions to verify "delegation only".
  Language validation is the schema's responsibility (verified separately in test_schemas.py).
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from clipwright.schemas import Artifact, ToolError, ToolResult

from clipwright_wrap.schemas import WrapCaptionsOptions
from clipwright_wrap.server import (
    clipwright_wrap_captions as server_wrap_captions,
)
from clipwright_wrap.server import main, mcp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_envelope(**kwargs: object) -> ToolResult:
    base = ToolResult(
        ok=True,
        summary="ok",
        data={},
        artifacts=[],
        warnings=[],
    )
    return base.model_copy(update=kwargs)


def _error_envelope(code: str) -> ToolResult:
    return ToolResult(
        ok=False,
        error=ToolError(code=code, message="error", hint="hint"),
    )


# ---------------------------------------------------------------------------
# MCP annotations（WR-AD-10）
# ---------------------------------------------------------------------------


class TestMcpAnnotations:
    """Verify the MCP annotations of the clipwright_wrap_captions tool."""

    def _get_annotations(self) -> object:
        # No public API exists in FastMCP to retrieve tool info, so
        # the private API (_tool_manager) is used here (same policy as transcribe/silence).
        tool = mcp._tool_manager.get_tool("clipwright_wrap_captions")  # noqa: SLF001
        assert tool is not None, "clipwright_wrap_captions must be registered in mcp"
        return tool.annotations

    def test_tool_is_registered(self) -> None:
        tool = mcp._tool_manager.get_tool("clipwright_wrap_captions")  # noqa: SLF001
        assert tool is not None

    def test_read_only_hint_is_true(self) -> None:
        assert self._get_annotations().readOnlyHint is True

    def test_destructive_hint_is_false(self) -> None:
        assert self._get_annotations().destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        assert self._get_annotations().idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False (fully offline, no network dependency; WR-AD-10)."""
        assert self._get_annotations().openWorldHint is False


# ---------------------------------------------------------------------------
# Delegation and envelope pass-through
# ---------------------------------------------------------------------------


class TestDelegation:
    def test_success_delegates(self) -> None:
        expected = _ok_envelope(summary="wrapped")
        with patch(
            "clipwright_wrap.server.wrap_captions",
            return_value=expected,
        ) as mock_w:
            result = server_wrap_captions(
                input="in.srt", output="out.srt", options=None
            )
        mock_w.assert_called_once()
        assert result.ok is True

    def test_failure_passthrough(self) -> None:
        expected = _error_envelope("FILE_NOT_FOUND")
        with patch(
            "clipwright_wrap.server.wrap_captions",
            return_value=expected,
        ):
            result = server_wrap_captions(
                input="missing.srt", output="out.srt", options=None
            )
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "FILE_NOT_FOUND"

    def test_error_envelope_has_code_message_hint(self) -> None:
        expected = _error_envelope("DEPENDENCY_MISSING")
        with patch(
            "clipwright_wrap.server.wrap_captions",
            return_value=expected,
        ):
            result = server_wrap_captions(
                input="in.srt", output="out.srt", options=None
            )
        assert result.error is not None
        assert result.error.code
        assert result.error.message
        assert result.error.hint

    def test_options_none_uses_default(self) -> None:
        """When options=None, WrapCaptionsOptions() defaults are passed to the delegate.

        Defaults: language="ja" / max_chars=16 / max_lines=2 (WR-AD-05).
        """
        with patch(
            "clipwright_wrap.server.wrap_captions",
            return_value=_ok_envelope(),
        ) as mock_w:
            server_wrap_captions(input="in.srt", output="out.srt", options=None)
        _args, kwargs = mock_w.call_args
        passed = kwargs.get("options")
        assert isinstance(passed, WrapCaptionsOptions)
        assert passed.language == "ja"
        assert passed.max_chars == 16
        assert passed.max_lines == 2

    def test_options_passed_through(self) -> None:
        """Specified options are passed to the delegate as-is."""
        opts = WrapCaptionsOptions(language="zh-hans", max_chars=20, max_lines=3)
        with patch(
            "clipwright_wrap.server.wrap_captions",
            return_value=_ok_envelope(),
        ) as mock_w:
            server_wrap_captions(input="in.srt", output="out.srt", options=opts)
        _args, kwargs = mock_w.call_args
        assert kwargs.get("options") is opts

    def test_server_does_not_validate_language_itself(self) -> None:
        """server.py must not create an if-branch that re-validates language and converts it to INVALID_INPUT.

        DC-GP-001: language validation is the responsibility of WrapCaptionsOptions (schema).
        server only delegates to wrap.wrap_captions; if the mock returns ok:True,
        the result must pass through unchanged (no double conversion).
        """
        # Mock wrap_captions to return ok:True and confirm server performs no conversion
        expected = _ok_envelope(summary="no double conversion")
        opts = WrapCaptionsOptions(language="ja")  # valid language
        with patch(
            "clipwright_wrap.server.wrap_captions",
            return_value=expected,
        ) as mock_w:
            result = server_wrap_captions(
                input="in.srt", output="out.srt", options=opts
            )
        # server returns the result as-is (no conversion)
        assert result.ok is True
        assert result.summary == "no double conversion"
        mock_w.assert_called_once()


# ---------------------------------------------------------------------------
# MCP boundary: outputSchema and structuredContent
# ---------------------------------------------------------------------------


class TestMcpBoundary:
    def test_outputschema_is_typed(self) -> None:
        tools = asyncio.run(mcp.list_tools())
        tool = next(t for t in tools if t.name == "clipwright_wrap_captions")
        schema = tool.outputSchema or {}
        assert "ok" in schema.get("properties", {}), (
            "outputSchema must expose 'ok' property"
        )

    def test_structuredcontent_top_level_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "clipwright_wrap.server.wrap_captions",
            lambda **kw: ToolResult(
                ok=True,
                summary="Wrap completed.",
                data={},
                artifacts=[Artifact(role="subtitle", path="out.srt", format="srt")],
                warnings=[],
            ),
        )
        result = asyncio.run(
            mcp.call_tool(
                "clipwright_wrap_captions", {"input": "in.srt", "output": "out.srt"}
            )
        )
        content, structured = result
        assert structured is not None
        assert "ok" in structured
        assert structured["ok"] is True


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------


class TestCliMain:
    def test_main_is_callable(self) -> None:
        assert callable(main)

    def test_main_runs_mcp_stdio(self) -> None:
        """main() calls mcp.run(transport="stdio")."""
        with patch.object(mcp, "run") as mock_run:
            main()
        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        assert kwargs.get("transport") == "stdio" or (
            len(_args) >= 1 and _args[0] == "stdio"
        )
