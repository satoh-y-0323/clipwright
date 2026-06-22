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
  - outputSchema is typed ToolResult (MCP boundary)
  - data/summary backend additive fields (AC-3 / ADR-5' / ADR-7'(e)):
    backend.device / backend.detail / whisper_wall_seconds / realtime_factor
    backward-compat: segment_count / language / total_duration_seconds unchanged
    summary contains " Backend:" (DC-AM-004)
    realtime None case: wall<=0 yields realtime_factor=None, no "0.0x" in summary
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from clipwright.schemas import (
    MediaInfo,
    RationalTimeModel,
    StreamInfo,
    ToolError,
    ToolResult,
)

from clipwright_transcribe.captions import Segment
from clipwright_transcribe.schemas import TranscribeOptions
from clipwright_transcribe.server import (
    clipwright_transcribe as server_transcribe,
)
from clipwright_transcribe.server import main, mcp
from clipwright_transcribe.transcribe import transcribe_media

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FPS = 30.0


def _make_media_info(
    path: str,
    *,
    duration_sec: float = 10.0,
    rate: float = _FPS,
) -> MediaInfo:
    """Build a MediaInfo with video+audio for backend-field tests."""
    streams: list[StreamInfo] = [
        StreamInfo(index=0, codec_type="video", codec_name="h264"),
        StreamInfo(index=1, codec_type="audio", codec_name="aac"),
    ]
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=duration_sec * rate, rate=rate),
        streams=streams,
        bit_rate=8_000_000,
    )


def _seg(start_sec: float, end_sec: float, text: str) -> Segment:
    return {"start_sec": start_sec, "end_sec": end_sec, "text": text}


def _ok_tool_result(**kwargs: Any) -> ToolResult:
    base = ToolResult(
        ok=True,
        summary="ok",
        data={},
        artifacts=[],
        warnings=[],
    )
    return base.model_copy(update=kwargs)


def _error_tool_result(code: str) -> ToolResult:
    return ToolResult(
        ok=False,
        error=ToolError(code=code, message="error", hint="hint"),
    )


# ---------------------------------------------------------------------------
# MCP annotations (§6.2, detect-class, TR-AD-11)
# ---------------------------------------------------------------------------


class TestMcpAnnotations:
    """Verify MCP annotations for the clipwright_transcribe tool."""

    def _get_annotations(self) -> object:
        # CR L-1: No public FastMCP API exists to retrieve tool info, so the private
        # _tool_manager API is used (same approach as the silence package).
        tool = mcp._tool_manager.get_tool("clipwright_transcribe")  # noqa: SLF001
        assert tool is not None, "clipwright_transcribe must be registered in mcp"
        return tool.annotations

    def test_tool_is_registered(self) -> None:
        tool = mcp._tool_manager.get_tool("clipwright_transcribe")  # noqa: SLF001
        assert tool is not None

    def test_read_only_hint_is_true(self) -> None:
        assert self._get_annotations().readOnlyHint is True  # type: ignore[union-attr]

    def test_destructive_hint_is_false(self) -> None:
        assert self._get_annotations().destructiveHint is False  # type: ignore[union-attr]

    def test_idempotent_hint_is_true(self) -> None:
        assert self._get_annotations().idempotentHint is True  # type: ignore[union-attr]

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False (fully offline, no network dependency; TR-AD-11)."""
        assert self._get_annotations().openWorldHint is False  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Delegation and envelope pass-through
# ---------------------------------------------------------------------------


class TestDelegation:
    def test_success_delegates(self) -> None:
        expected = _ok_tool_result(summary="transcribed")
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=expected,
        ) as mock_t:
            result = server_transcribe(media="v.mp4", output="o.otio", options=None)
        mock_t.assert_called_once()
        assert result.ok is True

    def test_failure_passthrough(self) -> None:
        expected = _error_tool_result("FILE_NOT_FOUND")
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=expected,
        ):
            result = server_transcribe(
                media="missing.mp4", output="o.otio", options=None
            )
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == "FILE_NOT_FOUND"

    def test_error_envelope_has_code_message_hint(self) -> None:
        expected = _error_tool_result("DEPENDENCY_MISSING")
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=expected,
        ):
            result = server_transcribe(media="v.mp4", output="o.otio", options=None)
        assert result.error is not None
        assert result.error.code
        assert result.error.message
        assert result.error.hint

    def test_options_none_uses_default(self) -> None:
        """When options=None, TranscribeOptions() defaults are passed to the
        delegate."""
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=_ok_tool_result(),
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
            return_value=_ok_tool_result(),
        ) as mock_t:
            server_transcribe(media="v.mp4", output="o.otio", options=opts)
        _args, kwargs = mock_t.call_args
        assert kwargs.get("options") is opts


# ---------------------------------------------------------------------------
# MCP boundary: outputSchema and structuredContent
# ---------------------------------------------------------------------------


class TestMcpBoundary:
    def test_outputschema_is_typed(self) -> None:
        """outputSchema must expose typed ToolResult fields (ok, summary, etc.)."""
        tools = asyncio.run(mcp.list_tools())
        target = next((t for t in tools if t.name == "clipwright_transcribe"), None)
        assert target is not None, "clipwright_transcribe tool must be listed"
        schema = target.outputSchema or {}
        props = schema.get("properties") or {}
        assert "ok" in props, (
            f"outputSchema must be typed ToolResult with 'ok' property; got: {props}"
        )

    def test_structuredcontent_top_level_ok(self) -> None:
        """call_tool response must expose 'ok' at top level (no wrapping).

        FastMCP call_tool returns a tuple (content_list, structured_dict).
        The text content must contain 'ok' at the top level (not wrapped).
        """
        with patch(
            "clipwright_transcribe.server.transcribe_media",
            return_value=ToolResult(
                ok=True,
                summary="ok",
                data={},
                artifacts=[],
                warnings=[],
            ),
        ):
            result = asyncio.run(
                mcp.call_tool(
                    "clipwright_transcribe",
                    {"media": "/fake.mp4", "output": "/fake.otio"},
                )
            )
        assert result, "call_tool must return non-empty result"
        # result is a tuple: (list[TextContent], structured_dict)
        content_list = result[0]
        assert content_list, "content list must be non-empty"
        content = json.loads(content_list[0].text)
        assert "ok" in content, (
            f"structuredContent must not be wrapped; top-level keys: {list(content.keys())}"
        )


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


# ---------------------------------------------------------------------------
# data / summary backend additive fields (AC-3 / ADR-5' / ADR-7'(e))
# Drives transcribe_media with _run_whisper mocked as WhisperRun(...)
# Red phase: WhisperRun is not yet implemented in transcribe.py
# ---------------------------------------------------------------------------


def _whisper_run(
    segments: list[Segment],
    language: str = "en",
    device: str = "cpu",
    detail: str = "cpu",
    wall: float = 1.0,
) -> Any:
    """Factory that returns a WhisperRun namedtuple for use as _run_whisper mock.

    Importing WhisperRun here intentionally triggers ImportError when the
    class is not yet implemented (Red phase).
    """
    # This import will raise ImportError until WhisperRun is implemented.
    from clipwright_transcribe.transcribe import WhisperRun  # noqa: PLC0415

    return WhisperRun(
        segments=segments,
        language=language,
        backend={"device": device, "detail": detail},
        wall_seconds=wall,
    )


class TestDataSummaryBackend:
    """Verify additive backend fields in data/summary (AC-3 / ADR-5' / ADR-7'(e)).

    _run_whisper is patched to return WhisperRun(...) so that transcribe_media
    is exercised end-to-end through the real orchestration path.

    Red phase: these tests fail until WhisperRun is implemented and
    _transcribe_inner is updated to consume it.
    """

    def _run_with_wall(
        self, tmp_path: Path, wall: float = 2.0
    ) -> tuple[ToolResult, str]:
        """Run transcribe_media with mocked inspect_media and _run_whisper.

        Returns (result, media_path).
        """
        media = tmp_path / "video.mp4"
        media.write_bytes(b"fake")
        model = tmp_path / "ggml-base.bin"
        model.write_bytes(b"fake-model")
        output = tmp_path / "out.otio"

        segs = [_seg(0.0, 1.0, "hello"), _seg(1.5, 2.5, "world")]
        mock_return = _whisper_run(
            segs, language="en", device="cpu", detail="cpu", wall=wall
        )

        with (
            patch(
                "clipwright_transcribe.transcribe.inspect_media",
                return_value=_make_media_info(str(media)),
            ),
            patch(
                "clipwright_transcribe.transcribe._run_whisper",
                return_value=mock_return,
            ),
        ):
            result = transcribe_media(
                str(media), str(output), TranscribeOptions(model_path=str(model))
            )
        return result, str(media)

    # --- AC-3: additive backend fields exist ---

    def test_data_has_backend_device(self, tmp_path: Path) -> None:
        """data["backend"]["device"] must be present (AC-3)."""
        result, _ = self._run_with_wall(tmp_path)
        assert result.ok is True
        assert "backend" in result.data
        assert "device" in result.data["backend"]

    def test_data_has_backend_detail(self, tmp_path: Path) -> None:
        """data["backend"]["detail"] must be present (AC-3)."""
        result, _ = self._run_with_wall(tmp_path)
        assert "detail" in result.data["backend"]

    def test_data_has_whisper_wall_seconds(self, tmp_path: Path) -> None:
        """data["whisper_wall_seconds"] must be present (AC-3)."""
        result, _ = self._run_with_wall(tmp_path)
        assert "whisper_wall_seconds" in result.data

    def test_data_has_realtime_factor(self, tmp_path: Path) -> None:
        """data["realtime_factor"] must be present (AC-3)."""
        result, _ = self._run_with_wall(tmp_path)
        assert "realtime_factor" in result.data

    def test_data_realtime_factor_type_float_or_none(self, tmp_path: Path) -> None:
        """data["realtime_factor"] must be float or None (ADR-4')."""
        result, _ = self._run_with_wall(tmp_path)
        rf = result.data["realtime_factor"]
        assert rf is None or isinstance(rf, float)

    # --- backward-compat: existing fields unchanged ---

    def test_backward_compat_segment_count(self, tmp_path: Path) -> None:
        """segment_count must still be present and correct (backward-compat)."""
        result, _ = self._run_with_wall(tmp_path)
        assert result.data["segment_count"] == 2

    def test_backward_compat_language(self, tmp_path: Path) -> None:
        """language must still be present and correct (backward-compat)."""
        result, _ = self._run_with_wall(tmp_path)
        assert result.data["language"] == "en"

    def test_backward_compat_total_duration_seconds(self, tmp_path: Path) -> None:
        """total_duration_seconds must still be present (backward-compat)."""
        result, _ = self._run_with_wall(tmp_path)
        assert "total_duration_seconds" in result.data

    def test_backward_compat_segments_not_in_data(self, tmp_path: Path) -> None:
        """Full segment list must not appear in data (backward-compat)."""
        result, _ = self._run_with_wall(tmp_path)
        assert "segments" not in result.data

    # --- DC-AM-004: summary contains " Backend:" (leading space) ---

    def test_summary_contains_backend_label(self, tmp_path: Path) -> None:
        """Summary must contain \" Backend:\" with leading space (DC-AM-004)."""
        result, _ = self._run_with_wall(tmp_path)
        assert result.summary is not None
        assert " Backend:" in result.summary

    # --- DC-AM-001: realtime None case (wall<=0 -> None, no "0.0x" in summary) ---

    def test_realtime_none_when_wall_zero(self, tmp_path: Path) -> None:
        """When wall_seconds=0.0, realtime_factor must be None (ADR-4' / DC-AM-001)."""
        result, _ = self._run_with_wall(tmp_path, wall=0.0)
        assert result.ok is True
        assert result.data["realtime_factor"] is None

    def test_summary_no_0x_when_realtime_none(self, tmp_path: Path) -> None:
        """When realtime_factor is None, summary must not contain \"0.0x\" (DC-AM-001)."""
        result, _ = self._run_with_wall(tmp_path, wall=0.0)
        assert result.summary is not None
        assert "0.0x" not in result.summary

    def test_summary_still_has_backend_when_realtime_none(self, tmp_path: Path) -> None:
        """When realtime_factor is None, summary ends with \" Backend: cpu.\" (ADR-5')."""
        result, _ = self._run_with_wall(tmp_path, wall=0.0)
        assert result.summary is not None
        assert " Backend: cpu." in result.summary
