"""test_server.py — Tests for clipwright-transition server.py (MCP wrapper).

Validates the FastMCP registration, MCP annotations, delegation contract,
and main() stdio launch for clipwright_add_transition.

Architecture references:
  - plan-report-20260623-223418.md task impl-transition-server
  - ADR-T-1: add_transition is the sole error boundary; server has zero logic.
  - annotations: readOnlyHint=True, destructiveHint=False,
      idempotentHint=True, openWorldHint=False (project-conventions.md).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from clipwright.schemas import ToolError, ToolResult

from clipwright_transition.schemas import AddTransitionOptions, TransitionSpec

# ---------------------------------------------------------------------------
# Attempt to import server.py (_SERVER_AVAILABLE = False if not implemented)
# ---------------------------------------------------------------------------

try:
    from clipwright_transition.server import clipwright_add_transition as server_add
    from clipwright_transition.server import main, mcp

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


def _uniform_options() -> AddTransitionOptions:
    """Return a minimal uniform-mode AddTransitionOptions for test use."""
    return AddTransitionOptions(
        uniform=TransitionSpec(type="fade", duration_sec=0.5),
    )


def _ok_tool_result(**kwargs: Any) -> ToolResult:
    """Return a success ToolResult template."""
    defaults: dict[str, Any] = {
        "ok": True,
        "summary": "Applied 1 transition(s) in uniform mode to 'output.otio'.",
        "data": {"boundary_count": 1, "mode": "uniform", "output": "output.otio"},
        "artifacts": [{"role": "timeline", "path": "output.otio", "format": "otio"}],
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
    """Validate that clipwright_add_transition is registered in the FastMCP instance."""

    def test_tool_is_registered(self) -> None:
        """clipwright_add_transition must be registered in mcp."""
        tool = mcp._tool_manager.get_tool("clipwright_add_transition")  # noqa: SLF001
        assert tool is not None, "clipwright_add_transition must be registered in mcp"

    def test_mcp_server_name(self) -> None:
        """The FastMCP instance must be named 'clipwright-transition'."""
        assert mcp.name == "clipwright-transition"


# ---------------------------------------------------------------------------
# MCP annotations tests
# ---------------------------------------------------------------------------


class TestMcpAnnotations:
    """Validate MCP annotations for the clipwright_add_transition tool.

    readOnlyHint=True  (does not modify input media or OTIO)
    destructiveHint=False  (non-destructive; generates new .otio only)
    idempotentHint=True  (same inputs -> same timeline)
    openWorldHint=False  (no external network access; local files only)
    """

    def _get_annotations(self) -> Any:
        # Relies on FastMCP private API (_tool_manager).
        # May break on FastMCP upgrades; migrate to public API when available.
        tool = mcp._tool_manager.get_tool("clipwright_add_transition")  # noqa: SLF001
        assert tool is not None, "clipwright_add_transition must be registered in mcp"
        return tool.annotations

    def test_read_only_hint_is_true(self) -> None:
        """readOnlyHint=True: does not modify input media or OTIO."""
        ann = self._get_annotations()
        assert ann.readOnlyHint is True

    def test_destructive_hint_is_false(self) -> None:
        """destructiveHint=False: input media and existing OTIO remain unchanged."""
        ann = self._get_annotations()
        assert ann.destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        """idempotentHint=True: same inputs -> same OTIO output."""
        ann = self._get_annotations()
        assert ann.idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False: no external network access; local files only."""
        ann = self._get_annotations()
        assert ann.openWorldHint is False


# ---------------------------------------------------------------------------
# Delegation tests (server must not contain business logic)
# ---------------------------------------------------------------------------


class TestMcpToolDelegation:
    """Validate delegation to transition.add_transition; server must be a pure wrapper.

    ADR-T-1: server has zero business logic or error conversion.
    add_transition is the sole error boundary.
    """

    def test_success_delegates_to_add_transition(self) -> None:
        """On success, must call add_transition and return its result as ToolResult."""
        expected_dict = _ok_tool_result().model_dump()
        opts = _uniform_options()

        with patch(
            "clipwright_transition.server.add_transition",
            return_value=expected_dict,
        ) as mock_add:
            result = server_add(
                timeline="input.otio", output="output.otio", options=opts
            )

        mock_add.assert_called_once()
        assert result.ok is True
        assert result.summary is not None

    def test_add_transition_called_with_correct_args(self) -> None:
        """timeline, output, and options must be forwarded to add_transition unchanged."""
        opts = _uniform_options()
        expected_dict = _ok_tool_result().model_dump()

        with patch(
            "clipwright_transition.server.add_transition",
            return_value=expected_dict,
        ) as mock_add:
            server_add(
                timeline="/path/to/input.otio",
                output="/path/to/output.otio",
                options=opts,
            )

        call_kwargs = mock_add.call_args.kwargs
        assert call_kwargs["timeline"] == "/path/to/input.otio"
        assert call_kwargs["output"] == "/path/to/output.otio"
        assert call_kwargs["options"] is opts

    def test_error_envelope_passthrough_file_not_found(self) -> None:
        """When add_transition returns FILE_NOT_FOUND error, server must return it as-is."""
        expected = _error_tool_result("FILE_NOT_FOUND")
        opts = _uniform_options()

        with patch(
            "clipwright_transition.server.add_transition",
            return_value=expected.model_dump(),
        ):
            result = server_add(
                timeline="missing.otio", output="output.otio", options=opts
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "FILE_NOT_FOUND"

    def test_error_envelope_passthrough_invalid_input(self) -> None:
        """When add_transition returns INVALID_INPUT, server must return it as-is."""
        expected = ToolResult(
            ok=False,
            error=ToolError(
                code="INVALID_INPUT",
                message="Output path and input timeline path are the same.",
                hint="Use a different output path.",
            ),
        )
        opts = _uniform_options()

        with patch(
            "clipwright_transition.server.add_transition",
            return_value=expected.model_dump(),
        ):
            result = server_add(timeline="same.otio", output="same.otio", options=opts)

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "INVALID_INPUT"
        assert result.error.message
        assert result.error.hint

    def test_ok_envelope_structure(self) -> None:
        """Success envelope must contain ok/summary/data/artifacts/warnings."""
        expected = _ok_tool_result()
        opts = _uniform_options()

        with patch(
            "clipwright_transition.server.add_transition",
            return_value=expected.model_dump(),
        ):
            result = server_add(
                timeline="input.otio", output="output.otio", options=opts
            )

        assert result.ok is True
        assert result.summary is not None
        assert result.data is not None
        assert result.artifacts is not None
        assert result.warnings is not None

    def test_result_is_tool_result_instance(self) -> None:
        """server must return a ToolResult instance (not a raw dict)."""
        opts = _uniform_options()

        with patch(
            "clipwright_transition.server.add_transition",
            return_value=_ok_tool_result().model_dump(),
        ):
            result = server_add(
                timeline="input.otio", output="output.otio", options=opts
            )

        assert isinstance(result, ToolResult)


# ---------------------------------------------------------------------------
# main() existence and MCP stdio launch
# ---------------------------------------------------------------------------


class TestCliMain:
    """Validate existence and basic call of main() -> mcp.run(transport='stdio')."""

    def test_main_is_callable(self) -> None:
        """main() must exist and be callable."""
        assert callable(main)

    def test_main_exists_in_module(self) -> None:
        """main must be defined in the clipwright_transition.server module."""
        import clipwright_transition.server as server_module

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
        tool = next((t for t in tools if t.name == "clipwright_add_transition"), None)
        assert tool is not None, "clipwright_add_transition must appear in list_tools()"
        schema = tool.outputSchema or {}
        assert "ok" in schema.get("properties", {}), (
            "outputSchema must expose 'ok' property"
        )

    def test_structuredcontent_top_level_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """call_tool must return structuredContent with top-level 'ok' key."""
        monkeypatch.setattr(
            "clipwright_transition.server.add_transition",
            lambda **kw: ToolResult(
                ok=True,
                summary="Applied 1 transition(s) in uniform mode to 'output.otio'.",
                data={"boundary_count": 1, "mode": "uniform", "output": "output.otio"},
                artifacts=[
                    {"role": "timeline", "path": "output.otio", "format": "otio"}
                ],
                warnings=[],
            ).model_dump(),
        )
        opts = _uniform_options()
        result = asyncio.run(
            mcp.call_tool(
                "clipwright_add_transition",
                {
                    "timeline": "input.otio",
                    "output": "output.otio",
                    "options": opts.model_dump(),
                },
            )
        )
        content, structured = result
        assert structured is not None
        assert "ok" in structured
        assert structured["ok"] is True
