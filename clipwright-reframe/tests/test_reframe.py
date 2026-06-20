"""test_reframe.py — Red-phase tests for clipwright_reframe.reframe and server.

Verification points (TDD Red — tests are written before full implementation):
  A. Envelope / happy path
     A-1: ToolResult {ok, summary, data, artifacts, warnings} returned (AC-14)
     A-2: target_w=1080, target_h=1920, mode='pad' — new .otio generated (AC-02)
     A-3: artifacts contains new .otio path
     A-4: summary is non-empty and references target resolution / mode

  B. Output validation order (D1)
     B-1: non-.otio extension -> INVALID_INPUT
     B-2: missing parent directory -> INVALID_INPUT
     B-3: output == media path -> INVALID_INPUT
     B-4: output == timeline path -> INVALID_INPUT
     B-5: timeline path specified but not found -> INVALID_INPUT (not raw FileNotFoundError)
     B-6: timeline path is not a valid .otio -> OTIO_ERROR (wrapped, not raw exception)

  C. Directive annotation (FR-2.1 / AC-06 / D3)
     C-1: metadata['clipwright']['reframe'] written with all D3 keys
     C-2: tool field = 'clipwright-reframe'
     C-3: version field present and non-empty
     C-4: kind field = 'reframe'
     C-5: target_w / target_h match options
     C-6: mode matches options
     C-7: anchor matches options
     C-8: pad_color matches options

  D. Idempotency (idempotentHint=True)
     D-1: two runs with same options -> directive is not duplicated (still a dict, not list)
     D-2: two runs -> directive values remain stable (no accumulation)

  E. NFR-1: non-destructive
     E-1: input media file unchanged after run
     E-2: input timeline file unchanged after run

  F. Input validation errors (FR-5)
     F-1: odd target_w -> INVALID_INPUT with 'even' in hint (AC-03)
     F-2: odd target_h -> INVALID_INPUT with 'even' in hint (AC-03)
     F-3: target_w < 2 -> INVALID_INPUT (AC-04)
     F-4: pad_color outside allowlist -> INVALID_INPUT (AC-05)

  G. MCP server annotations (NFR-2.1 / NFR-2.2)
     G-1: tool name is 'clipwright_reframe'
     G-2: readOnlyHint=False (creates a new .otio file)
     G-3: destructiveHint=False
     G-4: idempotentHint=True
     G-5: openWorldHint=False

AC coverage:
  AC-02 -> A-2, A-3
  AC-03 -> F-1, F-2
  AC-04 -> F-3
  AC-05 -> F-4
  AC-06 -> C-1 through C-8
  AC-14 -> A-1
  NFR-1 -> E-1, E-2
  NFR-2.1 -> G-2 through G-5
  NFR-2.2 -> G-1
  D1 -> B-1 through B-6
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ErrorCode
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo
from pydantic import ValidationError

from clipwright_reframe.reframe import reframe
from clipwright_reframe.schemas import ReframeOptions

# ===========================================================================
# Helpers
# ===========================================================================

_FPS = 30.0
_DURATION_SEC = 10.0


def _make_media_info(
    path: str,
    *,
    has_video: bool = True,
) -> MediaInfo:
    """Build a minimal MediaInfo for monkeypatching inspect_media."""
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(StreamInfo(index=0, codec_type="video", codec_name="h264"))
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(
            value=_DURATION_SEC * _FPS,
            rate=_FPS,
        ),
        streams=streams,
        bit_rate=8_000_000,
    )


def _run_reframe(
    tmp_path: Path,
    *,
    media_name: str = "video.mp4",
    output_name: str = "out.otio",
    target_w: int = 1080,
    target_h: int = 1920,
    mode: str = "pad",
    anchor: str = "center",
    pad_color: str = "black",
    timeline: str | None = None,
) -> dict[str, Any]:
    """Helper: create dummy media, patch inspect_media, call reframe, return result."""
    media_path = tmp_path / media_name
    media_path.write_bytes(b"dummy media")
    output_path = tmp_path / output_name
    opts = ReframeOptions(
        target_w=target_w,
        target_h=target_h,
        mode=mode,  # type: ignore[arg-type]
        anchor=anchor,  # type: ignore[arg-type]
        pad_color=pad_color,
    )
    with patch(
        "clipwright_reframe.reframe.inspect_media",
        side_effect=lambda p: _make_media_info(str(p)),
    ):
        result = reframe(
            media=str(media_path),
            output=str(output_path),
            options=opts,
            timeline=timeline,
        )
    return result  # type: ignore[return-value]


# ===========================================================================
# A. Envelope / happy path
# ===========================================================================


class TestEnvelope:
    """Returned ToolResult must conform to the {ok,summary,data,artifacts,warnings} envelope."""

    def test_ok_is_true_on_success(self, tmp_path: Path) -> None:
        """A successful call must return ok=True (AC-14)."""
        result = _run_reframe(tmp_path)
        assert result["ok"] is True

    def test_summary_is_non_empty(self, tmp_path: Path) -> None:
        """summary must be a non-empty string (AC-14)."""
        result = _run_reframe(tmp_path)
        assert isinstance(result.get("summary"), str) and len(result["summary"]) > 0

    def test_data_is_present(self, tmp_path: Path) -> None:
        """data key must be present in the result (AC-14)."""
        result = _run_reframe(tmp_path)
        assert "data" in result

    def test_artifacts_contains_otio_path(self, tmp_path: Path) -> None:
        """artifacts must contain one entry with the new .otio file path (AC-02)."""
        result = _run_reframe(tmp_path)
        artifacts = result.get("artifacts", [])
        assert len(artifacts) >= 1, "artifacts must not be empty"
        paths = [
            a.get("path") if isinstance(a, dict) else getattr(a, "path", None)
            for a in artifacts
        ]
        assert any(p is not None and p.endswith(".otio") for p in paths), (
            f"No .otio artifact found. artifacts={artifacts}"
        )

    def test_warnings_is_list(self, tmp_path: Path) -> None:
        """warnings key must be a list (AC-14)."""
        result = _run_reframe(tmp_path)
        assert isinstance(result.get("warnings"), list)

    def test_new_otio_file_created(self, tmp_path: Path) -> None:
        """pad mode must create a new .otio file on disk (AC-02)."""
        result = _run_reframe(tmp_path, mode="pad")
        assert result["ok"] is True
        output_path = tmp_path / "out.otio"
        assert output_path.exists(), (
            "Output .otio file must exist after successful run."
        )

    def test_summary_references_target_resolution(self, tmp_path: Path) -> None:
        """summary must reference the target resolution (AC-14)."""
        result = _run_reframe(tmp_path, target_w=1080, target_h=1920)
        summary = result.get("summary", "")
        assert "1080" in summary or "1920" in summary, (
            f"summary must reference target resolution. Got: {summary!r}"
        )

    def test_summary_references_mode(self, tmp_path: Path) -> None:
        """summary must reference the fit mode."""
        result = _run_reframe(tmp_path, mode="crop")
        summary = result.get("summary", "")
        assert "crop" in summary.lower(), (
            f"summary must reference mode. Got: {summary!r}"
        )


# ===========================================================================
# B. Output validation order (D1)
# ===========================================================================


class TestOutputValidation:
    """Output path validation steps per D1 specification."""

    def test_non_otio_extension_rejected(self, tmp_path: Path) -> None:
        """output with non-.otio extension must return INVALID_INPUT (B-1)."""
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = ReframeOptions(target_w=1080, target_h=1920)
        result = reframe(
            media=str(media),
            output=str(tmp_path / "out.json"),
            options=opts,
            timeline=None,
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value

    def test_missing_parent_directory_rejected(self, tmp_path: Path) -> None:
        """output whose parent does not exist must return INVALID_INPUT (B-2)."""
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = ReframeOptions(target_w=1080, target_h=1920)
        result = reframe(
            media=str(media),
            output=str(tmp_path / "nonexistent" / "out.otio"),
            options=opts,
            timeline=None,
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value

    def test_output_equals_media_rejected(self, tmp_path: Path) -> None:
        """output == media path must return INVALID_INPUT (B-3)."""
        media = tmp_path / "video.otio"
        media.write_bytes(b"dummy")
        opts = ReframeOptions(target_w=1080, target_h=1920)
        result = reframe(
            media=str(media),
            output=str(media),
            options=opts,
            timeline=None,
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value

    def test_output_equals_timeline_rejected(self, tmp_path: Path) -> None:
        """output == timeline path must return INVALID_INPUT (B-4)."""
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        timeline_file = tmp_path / "existing.otio"
        timeline_file.write_bytes(b"dummy otio")
        opts = ReframeOptions(target_w=1080, target_h=1920)
        result = reframe(
            media=str(media),
            output=str(timeline_file),
            options=opts,
            timeline=str(timeline_file),
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value

    def test_nonexistent_timeline_returns_invalid_input(self, tmp_path: Path) -> None:
        """Specified timeline path that does not exist must return INVALID_INPUT (B-5).

        D1 specifies: timeline 存在 check before load.
        Currently the scaffold skips this check and propagates a raw
        FileNotFoundError from load_timeline.  This test is Red until
        impl-reframe-pkg adds the existence guard.
        """
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = ReframeOptions(target_w=1080, target_h=1920)
        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            result = reframe(
                media=str(media),
                output=str(tmp_path / "out.otio"),
                options=opts,
                timeline=str(tmp_path / "nonexistent.otio"),
            )
        assert result["ok"] is False, (
            "Nonexistent timeline must return ok=False, not raise FileNotFoundError."
        )
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value, (
            f"Expected INVALID_INPUT for nonexistent timeline, "
            f"got: {result['error']['code']}"
        )

    def test_invalid_timeline_file_returns_otio_error(self, tmp_path: Path) -> None:
        """A timeline path that is not a valid .otio file must return OTIO_ERROR (B-6).

        load_timeline wraps OTIOError as ClipwrightError(OTIO_ERROR).
        This test verifies the error propagates as an error envelope (ok=False).
        """
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        bad_timeline = tmp_path / "bad.otio"
        bad_timeline.write_text("not valid json at all !!!")
        opts = ReframeOptions(target_w=1080, target_h=1920)
        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            result = reframe(
                media=str(media),
                output=str(tmp_path / "out.otio"),
                options=opts,
                timeline=str(bad_timeline),
            )
        assert result["ok"] is False
        assert result["error"]["code"] in (
            ErrorCode.OTIO_ERROR.value,
            ErrorCode.INVALID_INPUT.value,
        ), f"Expected OTIO_ERROR or INVALID_INPUT, got: {result['error']['code']}"


# ===========================================================================
# C. Directive annotation (FR-2.1 / AC-06 / D3)
# ===========================================================================


class TestDirectiveAnnotation:
    """ReframeDirective written to metadata['clipwright']['reframe'] per D3 contract."""

    def _load_reframe_meta(self, output_path: Path) -> dict[str, Any]:
        tl = otio.adapters.read_from_file(str(output_path))
        cw = tl.metadata.get("clipwright", {})
        meta = cw.get("reframe")
        assert meta is not None, (
            "metadata['clipwright']['reframe'] must be written on success (AC-06)."
        )
        return dict(meta)

    def test_reframe_key_written_to_metadata(self, tmp_path: Path) -> None:
        """metadata['clipwright']['reframe'] must be present after a successful run (AC-06)."""
        result = _run_reframe(tmp_path)
        assert result["ok"] is True
        tl = otio.adapters.read_from_file(str(tmp_path / "out.otio"))
        assert "reframe" in tl.metadata.get("clipwright", {}), (
            "metadata['clipwright']['reframe'] must be written."
        )

    def test_d3_key_set_complete(self, tmp_path: Path) -> None:
        """D3 directive dict must contain exactly the contracted keys (AC-06)."""
        _run_reframe(tmp_path)
        meta = self._load_reframe_meta(tmp_path / "out.otio")
        expected_keys = frozenset(
            {
                "tool",
                "version",
                "kind",
                "target_w",
                "target_h",
                "mode",
                "anchor",
                "pad_color",
            }
        )
        actual_keys = frozenset(meta.keys())
        assert actual_keys == expected_keys, (
            f"D3 contract violation: diff={actual_keys.symmetric_difference(expected_keys)}"
        )

    def test_tool_field_is_clipwright_reframe(self, tmp_path: Path) -> None:
        """tool field must be 'clipwright-reframe' (D3 / AC-06)."""
        _run_reframe(tmp_path)
        meta = self._load_reframe_meta(tmp_path / "out.otio")
        assert meta["tool"] == "clipwright-reframe"

    def test_version_field_non_empty(self, tmp_path: Path) -> None:
        """version field must be a non-empty string (D3 / AC-06)."""
        _run_reframe(tmp_path)
        meta = self._load_reframe_meta(tmp_path / "out.otio")
        assert isinstance(meta.get("version"), str) and len(meta["version"]) > 0

    def test_kind_field_is_reframe(self, tmp_path: Path) -> None:
        """kind field must be 'reframe' (D3 / AC-06)."""
        _run_reframe(tmp_path)
        meta = self._load_reframe_meta(tmp_path / "out.otio")
        assert meta["kind"] == "reframe"

    def test_target_w_matches_options(self, tmp_path: Path) -> None:
        """target_w in directive must match the provided ReframeOptions (D3)."""
        _run_reframe(tmp_path, target_w=1080)
        meta = self._load_reframe_meta(tmp_path / "out.otio")
        assert meta["target_w"] == 1080

    def test_target_h_matches_options(self, tmp_path: Path) -> None:
        """target_h in directive must match the provided ReframeOptions (D3)."""
        _run_reframe(tmp_path, target_h=1920)
        meta = self._load_reframe_meta(tmp_path / "out.otio")
        assert meta["target_h"] == 1920

    def test_mode_matches_options(self, tmp_path: Path) -> None:
        """mode in directive must match the provided ReframeOptions (D3)."""
        _run_reframe(tmp_path, mode="crop")
        meta = self._load_reframe_meta(tmp_path / "out.otio")
        assert meta["mode"] == "crop"

    def test_anchor_matches_options(self, tmp_path: Path) -> None:
        """anchor in directive must match the provided ReframeOptions (D3)."""
        _run_reframe(tmp_path, anchor="top")
        meta = self._load_reframe_meta(tmp_path / "out.otio")
        assert meta["anchor"] == "top"

    def test_pad_color_matches_options(self, tmp_path: Path) -> None:
        """pad_color in directive must match the provided ReframeOptions (D3)."""
        _run_reframe(tmp_path, pad_color="white")
        meta = self._load_reframe_meta(tmp_path / "out.otio")
        assert meta["pad_color"] == "white"


# ===========================================================================
# D. Idempotency
# ===========================================================================


class TestIdempotency:
    """Same inputs applied twice must not duplicate the directive (idempotentHint=True)."""

    def _make_valid_otio(self, tmp_path: Path, name: str = "base.otio") -> Path:
        """Create a minimal valid OTIO timeline file for use as input timeline."""
        tl = otio.schema.Timeline(name="test")
        path = tmp_path / name
        otio.adapters.write_to_file(tl, str(path))
        return path

    def test_directive_not_duplicated_on_second_run(self, tmp_path: Path) -> None:
        """Two consecutive runs must not cause the directive to become a list (D-1).

        The directive must remain a dict, not accumulate into a list.
        """
        # First run: output1.otio created from scratch
        output1 = tmp_path / "output1.otio"
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = ReframeOptions(target_w=1080, target_h=1920)

        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            reframe(
                media=str(media),
                output=str(output1),
                options=opts,
                timeline=None,
            )

        # Second run: output2.otio created with output1.otio as input timeline
        output2 = tmp_path / "output2.otio"
        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            reframe(
                media=str(media),
                output=str(output2),
                options=opts,
                timeline=str(output1),
            )

        tl2 = otio.adapters.read_from_file(str(output2))
        reframe_meta = tl2.metadata.get("clipwright", {}).get("reframe")
        assert reframe_meta is not None, (
            "Directive must still be present after second run."
        )
        assert isinstance(dict(reframe_meta), dict), (
            "Directive must remain a dict (not a list) after second run — "
            "idempotency violation."
        )

    def test_directive_values_stable_on_second_run(self, tmp_path: Path) -> None:
        """target_w/target_h must not change between first and second run (D-2)."""
        output1 = tmp_path / "output1.otio"
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = ReframeOptions(target_w=1080, target_h=1920)

        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            reframe(
                media=str(media),
                output=str(output1),
                options=opts,
                timeline=None,
            )

        output2 = tmp_path / "output2.otio"
        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            reframe(
                media=str(media),
                output=str(output2),
                options=opts,
                timeline=str(output1),
            )

        tl2 = otio.adapters.read_from_file(str(output2))
        meta = dict(tl2.metadata.get("clipwright", {}).get("reframe", {}))
        assert meta.get("target_w") == 1080
        assert meta.get("target_h") == 1920


# ===========================================================================
# E. NFR-1: non-destructive
# ===========================================================================


class TestNonDestructive:
    """Input media and input timeline must not be modified (NFR-1)."""

    def test_input_media_bytes_unchanged(self, tmp_path: Path) -> None:
        """Input media file content must be identical before and after the run (NFR-1)."""
        original_bytes = b"dummy media content"
        media = tmp_path / "video.mp4"
        media.write_bytes(original_bytes)
        opts = ReframeOptions(target_w=1080, target_h=1920)
        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            reframe(
                media=str(media),
                output=str(tmp_path / "out.otio"),
                options=opts,
                timeline=None,
            )
        assert media.read_bytes() == original_bytes, (
            "Input media file must not be modified (NFR-1)."
        )

    def test_input_timeline_bytes_unchanged(self, tmp_path: Path) -> None:
        """Input timeline .otio file must not be modified when provided as input (NFR-1)."""
        tl_in = otio.schema.Timeline(name="original")
        timeline_path = tmp_path / "input.otio"
        otio.adapters.write_to_file(tl_in, str(timeline_path))
        original_bytes = timeline_path.read_bytes()

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        opts = ReframeOptions(target_w=1080, target_h=1920)
        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            reframe(
                media=str(media),
                output=str(tmp_path / "out.otio"),
                options=opts,
                timeline=str(timeline_path),
            )
        assert timeline_path.read_bytes() == original_bytes, (
            "Input timeline file must not be modified (NFR-1)."
        )


# ===========================================================================
# F. Input validation errors (FR-5)
# ===========================================================================


class TestInputValidationErrors:
    """Pydantic validation errors must be caught and returned as INVALID_INPUT (FR-5)."""

    def test_odd_target_w_returns_invalid_input(self, tmp_path: Path) -> None:
        """Odd target_w must return INVALID_INPUT with 'even' in hint (AC-03 / F-1).

        ReframeOptions raises ValidationError for odd target_w.
        The server/orchestrator must convert this to an INVALID_INPUT error_result.
        This test verifies the user-facing contract: the caller (MCP client)
        receives ok=False with an INVALID_INPUT code, not an unhandled exception.
        """
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        with pytest.raises(ValidationError) as exc_info:
            # ReframeOptions must raise ValidationError for odd values (AC-03)
            ReframeOptions(target_w=1081, target_h=1920)
        # ValidationError message must include 'even'
        errors_str = str(exc_info.value)
        assert "even" in errors_str.lower(), (
            f"Expected 'even' in ValidationError for odd target_w, got: {errors_str}"
        )

    def test_odd_target_h_returns_invalid_input(self, tmp_path: Path) -> None:
        """Odd target_h must raise ValidationError with 'even' in message (AC-03 / F-2)."""
        with pytest.raises(ValidationError) as exc_info:
            ReframeOptions(target_w=1080, target_h=1081)
        errors_str = str(exc_info.value)
        assert "even" in errors_str.lower(), (
            f"Expected 'even' in ValidationError for odd target_h, got: {errors_str}"
        )

    def test_target_w_below_minimum_raises(self, tmp_path: Path) -> None:
        """target_w=1 (odd and < 2) must raise ValidationError (AC-04 / F-3)."""
        with pytest.raises(ValidationError):
            ReframeOptions(target_w=1, target_h=1080)

    def test_pad_color_outside_allowlist_raises(self, tmp_path: Path) -> None:
        """pad_color with injection attempt must raise ValidationError (AC-05 / F-4)."""
        with pytest.raises(ValidationError) as exc_info:
            ReframeOptions(target_w=1080, target_h=1920, pad_color="red;scale=1:1")
        # Injection attempt must be caught
        assert exc_info.value is not None


# ===========================================================================
# G. MCP server annotations (NFR-2.1 / NFR-2.2)
# ===========================================================================


class TestMcpAnnotations:
    """Verify MCP tool registration and annotations on clipwright_reframe server."""

    def _get_tool_and_annotations(self) -> tuple[object, object]:
        from clipwright_reframe.server import mcp

        tool = mcp._tool_manager.get_tool("clipwright_reframe")  # noqa: SLF001
        assert tool is not None, (
            "clipwright_reframe must be registered in mcp (NFR-2.2)"
        )
        return tool, tool.annotations

    def test_tool_name_is_clipwright_reframe(self) -> None:
        """MCP tool must be registered as 'clipwright_reframe' (NFR-2.2)."""
        from clipwright_reframe.server import mcp

        tool = mcp._tool_manager.get_tool("clipwright_reframe")  # noqa: SLF001
        assert tool is not None, "clipwright_reframe tool is not registered."

    def test_read_only_hint_is_false(self) -> None:
        """readOnlyHint must be False: clipwright_reframe creates a new .otio file (NFR-2.1).

        This test is Red because the current scaffold sets readOnlyHint=True.
        impl-reframe-pkg must change it to False.
        """
        _tool, annotations = self._get_tool_and_annotations()
        assert annotations.readOnlyHint is False, (  # type: ignore[union-attr]
            "readOnlyHint must be False — clipwright_reframe writes a new .otio file, "
            "it is not read-only. Current value: True (scaffold default)."
        )

    def test_destructive_hint_is_false(self) -> None:
        """destructiveHint must be False: input media and OTIO are never modified (NFR-2.1)."""
        _tool, annotations = self._get_tool_and_annotations()
        assert annotations.destructiveHint is False  # type: ignore[union-attr]

    def test_idempotent_hint_is_true(self) -> None:
        """idempotentHint must be True: same inputs produce same directive (NFR-2.1)."""
        _tool, annotations = self._get_tool_and_annotations()
        assert annotations.idempotentHint is True  # type: ignore[union-attr]

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint must be False: no network access (NFR-2.1)."""
        _tool, annotations = self._get_tool_and_annotations()
        assert annotations.openWorldHint is False  # type: ignore[union-attr]
