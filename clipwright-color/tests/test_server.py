"""test_server.py — Tests for clipwright_color.server (MCP registration + delegation).

Verification points:
  - Tool registered as clipwright_detect_color
  - annotations: readOnlyHint=True / destructiveHint=False / idempotentHint=True / openWorldHint=False
  - options=None -> DetectColorOptions() defaults applied
  - Delegates to color.detect_color

Requirements: FR-4 (annotations), architecture-report §5 server.py.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from clipwright.schemas import Artifact, ToolResult

from clipwright_color.schemas import (
    DetectColorOptions,  # type: ignore[import-not-found]
)
from clipwright_color.server import main, mcp  # type: ignore[import-not-found]

# ===========================================================================
# Helpers
# ===========================================================================


def _ok_tool_result(**kwargs: object) -> ToolResult:
    base = ToolResult(
        ok=True,
        summary=str(kwargs.get("summary", "ok")),
        data={},
        artifacts=[],
        warnings=[],
    )
    return base


def _get_tool_annotations() -> object:
    # FastMCP does not expose a public API, so we use _tool_manager for testing.
    tool = mcp._tool_manager.get_tool("clipwright_detect_color")  # noqa: SLF001
    assert tool is not None, "clipwright_detect_color must be registered in mcp"
    return tool.annotations


# ===========================================================================
# MCP registration
# ===========================================================================


class TestMcpRegistration:
    """clipwright_detect_color must be registered in MCP."""

    def test_tool_is_registered(self) -> None:
        """clipwright_detect_color must exist in the MCP tool list."""
        tool = mcp._tool_manager.get_tool("clipwright_detect_color")  # noqa: SLF001
        assert tool is not None, "clipwright_detect_color is not registered in MCP."


# ===========================================================================
# MCP annotations (FR-4)
# ===========================================================================


class TestMcpAnnotations:
    """Verify MCP annotations for clipwright_detect_color (FR-4)."""

    def test_read_only_hint_is_true(self) -> None:
        """readOnlyHint=True: input media is not modified."""
        annotations = _get_tool_annotations()
        assert annotations.readOnlyHint is True  # type: ignore[union-attr]

    def test_destructive_hint_is_false(self) -> None:
        """destructiveHint=False: not a destructive operation."""
        annotations = _get_tool_annotations()
        assert annotations.destructiveHint is False  # type: ignore[union-attr]

    def test_idempotent_hint_is_true(self) -> None:
        """idempotentHint=True: same input produces same output."""
        annotations = _get_tool_annotations()
        assert annotations.idempotentHint is True  # type: ignore[union-attr]

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False: no network access."""
        annotations = _get_tool_annotations()
        assert annotations.openWorldHint is False  # type: ignore[union-attr]


# ===========================================================================
# Delegation to color.detect_color
# ===========================================================================


class TestDelegation:
    """clipwright_detect_color must delegate to color.detect_color."""

    def test_success_delegates_to_detect_color(self) -> None:
        """detect_color must be called on success and its result returned."""
        with patch(
            "clipwright_color.server.detect_color",
            return_value=_ok_tool_result(summary="done"),
        ) as mock_fn:
            _content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_detect_color",
                    {"media": "in.mp4", "output": "out.otio"},
                )
            )

        mock_fn.assert_called_once()
        assert structured["ok"] is True
        assert structured.get("summary") == "done"

    def test_error_result_propagates(self) -> None:
        """An error ToolResult returned by detect_color must propagate as-is."""
        from clipwright.schemas import ToolError

        error_tr = ToolResult(
            ok=False,
            error=ToolError(
                code="INVALID_INPUT",
                message="test error",
                hint="test hint",
            ),
        )
        with patch(
            "clipwright_color.server.detect_color",
            return_value=error_tr,
        ):
            _content, structured = asyncio.run(
                mcp.call_tool(
                    "clipwright_detect_color",
                    {"media": "in.mp4", "output": "out.otio"},
                )
            )

        assert structured["ok"] is False
        assert structured.get("error") is not None
        assert structured["error"]["code"] == "INVALID_INPUT"

    def test_media_and_output_forwarded(self) -> None:
        """media / output must be correctly forwarded to detect_color."""
        with patch(
            "clipwright_color.server.detect_color",
            return_value=_ok_tool_result(),
        ) as mock_fn:
            asyncio.run(
                mcp.call_tool(
                    "clipwright_detect_color",
                    {
                        "media": "/path/to/video.mp4",
                        "output": "/path/to/out.otio",
                    },
                )
            )

        _args, kwargs = mock_fn.call_args
        assert kwargs.get("media") == "/path/to/video.mp4"
        assert kwargs.get("output") == "/path/to/out.otio"

    def test_timeline_forwarded_when_specified(self) -> None:
        """The timeline argument must be correctly forwarded to detect_color."""
        with patch(
            "clipwright_color.server.detect_color",
            return_value=_ok_tool_result(),
        ) as mock_fn:
            asyncio.run(
                mcp.call_tool(
                    "clipwright_detect_color",
                    {
                        "media": "in.mp4",
                        "output": "out.otio",
                        "timeline": "existing.otio",
                    },
                )
            )

        _args, kwargs = mock_fn.call_args
        assert kwargs.get("timeline") == "existing.otio"

    def test_timeline_none_is_forwarded(self) -> None:
        """timeline=None must be the default when omitted."""
        with patch(
            "clipwright_color.server.detect_color",
            return_value=_ok_tool_result(),
        ) as mock_fn:
            asyncio.run(
                mcp.call_tool(
                    "clipwright_detect_color",
                    {"media": "in.mp4", "output": "out.otio"},
                )
            )

        _args, kwargs = mock_fn.call_args
        assert kwargs.get("timeline") is None


# ===========================================================================
# Default value when options=None
# ===========================================================================


class TestOptionsDefault:
    """When options=None, DetectColorOptions() defaults must be used."""

    def test_options_none_uses_default_detect_color_options(self) -> None:
        """options=None -> target_luma=128.0 / sample_interval_sec=1.0 defaults must be passed."""
        with patch(
            "clipwright_color.server.detect_color",
            return_value=_ok_tool_result(),
        ) as mock_fn:
            asyncio.run(
                mcp.call_tool(
                    "clipwright_detect_color",
                    {"media": "in.mp4", "output": "out.otio"},
                )
            )

        _args, kwargs = mock_fn.call_args
        passed = kwargs.get("options")
        assert isinstance(passed, DetectColorOptions), (
            f"options is not DetectColorOptions: {type(passed)}"
        )
        assert passed.target_luma == 128.0
        assert passed.sample_interval_sec == 1.0

    def test_options_explicit_is_forwarded(self) -> None:
        """An explicitly specified options value must be forwarded as-is."""
        with patch(
            "clipwright_color.server.detect_color",
            return_value=_ok_tool_result(),
        ) as mock_fn:
            asyncio.run(
                mcp.call_tool(
                    "clipwright_detect_color",
                    {
                        "media": "in.mp4",
                        "output": "out.otio",
                        "options": {"target_luma": 200.0, "sample_interval_sec": 2.0},
                    },
                )
            )

        _args, kwargs = mock_fn.call_args
        passed = kwargs.get("options")
        assert isinstance(passed, DetectColorOptions) and passed.target_luma == 200.0


# ===========================================================================
# main() — stdio launch
# ===========================================================================


class TestCliMain:
    """main() must launch the MCP server over stdio."""

    def test_main_runs_mcp_with_stdio_transport(self) -> None:
        """main() must call mcp.run(transport='stdio')."""
        with patch.object(mcp, "run") as mock_run:
            main()

        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        assert kwargs.get("transport") == "stdio" or (
            len(_args) >= 1 and _args[0] == "stdio"
        ), f"transport='stdio' was not passed. args={_args}, kwargs={kwargs}"


# ===========================================================================
# MCP boundary: outputSchema and structuredContent
# ===========================================================================


class TestMcpBoundary:
    def test_outputschema_is_typed(self) -> None:
        """outputSchema must expose 'ok' property (FastMCP typed return)."""
        tools = asyncio.run(mcp.list_tools())
        tool = next(t for t in tools if t.name == "clipwright_detect_color")
        schema = tool.outputSchema or {}
        assert "ok" in schema.get("properties", {}), (
            "outputSchema must expose 'ok' property"
        )

    def test_structuredcontent_top_level_ok(self, monkeypatch: object) -> None:
        """call_tool must return structuredContent with top-level ok=True on success."""
        monkeypatch.setattr(  # type: ignore[union-attr]
            "clipwright_color.server.detect_color",
            lambda **kw: ToolResult(
                ok=True,
                summary="Color detected.",
                data={},
                artifacts=[Artifact(role="timeline", path="out.otio", format="otio")],
                warnings=[],
            ),
        )
        result = asyncio.run(
            mcp.call_tool(
                "clipwright_detect_color",
                {"media": "m.mp4", "output": "out.otio"},
            )
        )
        content, structured = result
        assert structured is not None
        assert "ok" in structured
        assert structured["ok"] is True


# ===========================================================================
# New options surfaced in input schema / docstring (ADR-CO-7 / FR-1 / FR-3 / FR-5)
# ===========================================================================


class TestNewOptionsInSchema:
    """Input schema and docstring must surface the new eq/WB/lut options (FR-1/FR-3/FR-5)."""

    def _get_tool_input_schema_str(self) -> str:
        """Return the full JSON-serialised inputSchema for clipwright_detect_color."""
        import json

        tools = asyncio.run(mcp.list_tools())
        tool = next(t for t in tools if t.name == "clipwright_detect_color")
        return json.dumps(tool.inputSchema or {})

    def _get_tool_description(self) -> str:
        """Return the description string for clipwright_detect_color."""
        tools = asyncio.run(mcp.list_tools())
        tool = next(t for t in tools if t.name == "clipwright_detect_color")
        return tool.description or ""

    # -------------------------------------------------------------------------
    # eq options (FR-1): saturation / contrast / gamma
    # -------------------------------------------------------------------------

    def test_schema_includes_saturation(self) -> None:
        """Input schema must include 'saturation' (DetectColorOptions FR-1).

        RED: DetectColorOptions has no saturation field yet → not in schema.
        """
        schema_str = self._get_tool_input_schema_str()
        assert '"saturation"' in schema_str, (
            "FR-1: input schema must include 'saturation' option."
            f" Schema excerpt: {schema_str[:300]}"
        )

    def test_schema_includes_contrast(self) -> None:
        """Input schema must include 'contrast' (DetectColorOptions FR-1).

        RED: DetectColorOptions has no contrast field yet → not in schema.
        """
        schema_str = self._get_tool_input_schema_str()
        assert '"contrast"' in schema_str, (
            "FR-1: input schema must include 'contrast' option."
        )

    def test_schema_includes_gamma(self) -> None:
        """Input schema must include 'gamma' (DetectColorOptions FR-1).

        RED: DetectColorOptions has no gamma field yet → not in schema.
        """
        schema_str = self._get_tool_input_schema_str()
        assert '"gamma"' in schema_str, (
            "FR-1: input schema must include 'gamma' option."
        )

    # -------------------------------------------------------------------------
    # WB override options (FR-3): temperature / tint
    # -------------------------------------------------------------------------

    def test_schema_includes_temperature(self) -> None:
        """Input schema must include 'temperature' option (FR-3).

        RED: DetectColorOptions has no temperature field yet → not in schema.
        """
        schema_str = self._get_tool_input_schema_str()
        assert '"temperature"' in schema_str, (
            "FR-3: input schema must include 'temperature' option."
        )

    def test_schema_includes_tint(self) -> None:
        """Input schema must include 'tint' option (FR-3).

        RED: DetectColorOptions has no tint field yet → not in schema.
        """
        schema_str = self._get_tool_input_schema_str()
        assert '"tint"' in schema_str, "FR-3: input schema must include 'tint' option."

    # -------------------------------------------------------------------------
    # LUT option (FR-5)
    # -------------------------------------------------------------------------

    def test_schema_includes_lut(self) -> None:
        """Input schema must include 'lut' option (FR-5).

        RED: DetectColorOptions has no lut field yet → not in schema.
        """
        schema_str = self._get_tool_input_schema_str()
        assert '"lut"' in schema_str, "FR-5: input schema must include 'lut' option."

    # -------------------------------------------------------------------------
    # Docstring / description: temperature/tint as normalised [-1,1] axes (ADR-CO-7)
    # -------------------------------------------------------------------------

    def test_docstring_mentions_temperature_option(self) -> None:
        """Tool description must mention 'temperature' option (FR-3 / ADR-CO-7).

        RED: current docstring does not mention temperature.
        """
        description = self._get_tool_description()
        assert "temperature" in description.lower(), (
            "ADR-CO-7: tool description must mention 'temperature'."
            f" Got description: {description[:200]!r}"
        )

    def test_docstring_mentions_tint_option(self) -> None:
        """Tool description must mention 'tint' option (FR-3 / ADR-CO-7).

        RED: current docstring does not mention tint.
        """
        description = self._get_tool_description()
        assert "tint" in description.lower(), (
            "ADR-CO-7: tool description must mention 'tint'."
        )

    def test_docstring_does_not_describe_temperature_in_kelvin(self) -> None:
        """ADR-CO-7: temperature must NOT be described in Kelvin; it is a normalised [-1,1] axis.

        RED: current docstring doesn't mention temperature at all, so 'kelvin' is absent —
        this test may pass trivially.  After implementation the description must remain
        Kelvin-free and use 'normalised'/'axis' or '[-1,1]' language instead.
        """
        description = self._get_tool_description()
        assert "kelvin" not in description.lower(), (
            "ADR-CO-7: temperature must NOT be described in Kelvin."
            " Use normalised [-1, 1] axis language instead."
        )

    def test_docstring_mentions_normalised_axes_for_wb_override(self) -> None:
        """ADR-CO-7: description must indicate temperature/tint are normalised [-1,1] axes.

        RED: current docstring does not mention temperature/tint at all.
        """
        description = self._get_tool_description()
        # At least one of the expected language markers must appear
        has_normalised = any(
            kw in description.lower()
            for kw in (
                "normalised",
                "normalized",
                "[-1",
                "[-1,",
                "axis",
                "warm",
                "cool",
            )
        )
        assert has_normalised, (
            "ADR-CO-7: description must mention normalised axes for temperature/tint"
            " (not Kelvin)."
            f" Got description: {description[:300]!r}"
        )
