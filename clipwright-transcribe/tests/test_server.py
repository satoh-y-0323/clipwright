"""test_server.py — Tests for clipwright-transcribe server.py (MCP + CLI).

Target:
  - clipwright_transcribe tool is registered in MCP and delegates to
    transcribe.transcribe_media
  - MCP annotations (§6.2, detect-class, TR-AD-11):
    readOnlyHint:true / destructiveHint:false / idempotentHint:true /
    openWorldHint:false
  - Success and error envelope pass-through
  - options=None uses TranscribeOptions() defaults
  - main() calls mcp.run(transport="stdio")
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from clipwright_transcribe.schemas import TranscribeOptions
from clipwright_transcribe.server import (
    clipwright_transcribe as server_transcribe,
)
from clipwright_transcribe.server import main, mcp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_envelope(**kwargs: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ok": True,
        "summary": "ok",
        "data": {},
        "artifacts": [],
        "warnings": [],
    }
    base.update(kwargs)
    return base


def _error_envelope(code: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"code": code, "message": "error", "hint": "hint"},
    }


# ---------------------------------------------------------------------------
# MCP annotations (§6.2, detect-class, TR-AD-11)
# ---------------------------------------------------------------------------


class TestMcpAnnotations:
    """Verify MCP annotations for the clipwright_transcribe tool."""

    def _get_annotations(self) -> Any:
        # CR L-1: No public FastMCP API exists to retrieve tool info, so the private
        # _tool_manager API is used (same approach as the silence package).
        tool = mcp._tool_manager.get_tool("clipwright_transcribe")  # noqa: SLF001
        assert tool is not None, "clipwright_transcribe must be registered in mcp"
        return tool.annotations

    def test_tool_is_registered(self) -> None:
        tool = mcp._tool_manager.get_tool("clipwright_transcribe")  # noqa: SLF001
        assert tool is not None

    def test_read_only_hint_is_true(self) -> None:
        assert self._get_annotations().readOnlyHint is True

    def test_destructive_hint_is_false(self) -> None:
        assert self._get_annotations().destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        assert self._get_annotations().idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False (fully offline, no network dependency; TR-AD-11)."""
        assert self._get_annotations().openWorldHint is False


# ---------------------------------------------------------------------------
# Delegation and envelope pass-through
# ---------------------------------------------------------------------------


class TestDelegation:
    def test_success_delegates(self) -> None:
        expected = _ok_envelope(summary="transcribed")
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=expected,
        ) as mock_t:
            result = server_transcribe(media="v.mp4", output="o.otio", options=None)
        mock_t.assert_called_once()
        assert result["ok"] is True

    def test_failure_passthrough(self) -> None:
        expected = _error_envelope("FILE_NOT_FOUND")
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=expected,
        ):
            result = server_transcribe(
                media="missing.mp4", output="o.otio", options=None
            )
        assert result["ok"] is False
        assert result["error"]["code"] == "FILE_NOT_FOUND"

    def test_error_envelope_has_code_message_hint(self) -> None:
        expected = _error_envelope("DEPENDENCY_MISSING")
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=expected,
        ):
            result = server_transcribe(media="v.mp4", output="o.otio", options=None)
        error = result["error"]
        assert "code" in error
        assert "message" in error
        assert "hint" in error

    def test_options_none_uses_default(self) -> None:
        """When options=None, TranscribeOptions() defaults are passed to the
        delegate."""
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=_ok_envelope(),
        ) as mock_t:
            server_transcribe(media="v.mp4", output="o.otio", options=None)
        _args, kwargs = mock_t.call_args
        passed = kwargs.get("options")
        assert isinstance(passed, TranscribeOptions)
        assert passed.language is None

    def test_options_passed_through(self) -> None:
        """Explicitly provided options are forwarded unchanged to the delegate."""
        opts = TranscribeOptions(language="ja", initial_prompt="clipwright")
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=_ok_envelope(),
        ) as mock_t:
            server_transcribe(media="v.mp4", output="o.otio", options=opts)
        _args, kwargs = mock_t.call_args
        assert kwargs.get("options") is opts


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
