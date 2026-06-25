"""test_reframe.py — Tests for clipwright_reframe.reframe and server.

Verification points:
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
     B-7: output outside media directory -> PATH_NOT_ALLOWED

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
     F-1: odd target_w -> INVALID_INPUT via reframe() (AC-03)
     F-2: odd target_h -> INVALID_INPUT via reframe() (AC-03)
     F-3: target_w < 2 -> INVALID_INPUT via reframe() (AC-04)
     F-4: pad_color outside allowlist -> INVALID_INPUT via reframe() (AC-05)
     F-1s: odd target_w -> ValidationError from schema (AC-03, schema contract)
     F-2s: odd target_h -> ValidationError from schema (AC-03, schema contract)
     F-3s: target_w < 2 -> ValidationError from schema (AC-04, schema contract)
     F-4s: pad_color injection -> ValidationError from schema (AC-05, schema contract)

  G. MCP server annotations (NFR-2.1 / NFR-2.2)
     G-1: tool name is 'clipwright_reframe'
     G-2: readOnlyHint=False (creates a new .otio file)
     G-3: destructiveHint=False
     G-4: idempotentHint=True
     G-5: openWorldHint=False

AC coverage:
  AC-02 -> A-2, A-3
  AC-03 -> F-1, F-2, F-1s, F-2s
  AC-04 -> F-3, F-3s
  AC-05 -> F-4, F-4s
  AC-06 -> C-1 through C-8
  AC-14 -> A-1
  NFR-1 -> E-1, E-2
  NFR-2.1 -> G-2 through G-5
  NFR-2.2 -> G-1
  D1 -> B-1 through B-7
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
    has_audio: bool = True,
) -> MediaInfo:
    """Build a minimal MediaInfo for monkeypatching inspect_media."""
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(StreamInfo(index=0, codec_type="video", codec_name="h264"))
    if has_audio:
        streams.append(StreamInfo(index=1, codec_type="audio", codec_name="aac"))
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
    has_audio: bool = True,
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
        side_effect=lambda p: _make_media_info(str(p), has_audio=has_audio),
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

        D1 specifies: timeline existence check before load.
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

    def test_output_outside_media_dir_rejected(self, tmp_path: Path) -> None:
        """output outside the media directory must return PATH_NOT_ALLOWED (B-7).

        _check_output_within_media_dir enforces CWE-22 path-traversal prevention.
        """
        media_dir = tmp_path / "media"
        media_dir.mkdir()
        other_dir = tmp_path / "other"
        other_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")
        opts = ReframeOptions(target_w=1080, target_h=1920)
        result = reframe(
            media=str(media),
            output=str(other_dir / "out.otio"),
            options=opts,
            timeline=None,
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED.value, (
            f"Expected PATH_NOT_ALLOWED for output outside media dir, "
            f"got: {result['error']['code']}"
        )


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
                "track",
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

    def test_audio_less_media_no_audio_clip_in_timeline(self, tmp_path: Path) -> None:
        """Audio-less media must not populate the Audio track in the new timeline.

        Regression guard for L-1 fix: _add_full_clip skips Audio tracks when
        has_audio=False, preventing render from treating the timeline as audio-bearing.

        Verification:
          - Video track has exactly 1 full-length clip (V1 always populated).
          - Audio track has 0 clips (A1 must remain empty for audio-less sources).
        """
        result = _run_reframe(tmp_path, has_audio=False)
        assert result["ok"] is True, f"Expected ok=True, got: {result}"

        tl = otio.adapters.read_from_file(str(tmp_path / "out.otio"))

        video_clips = [
            clip
            for track in tl.video_tracks()
            for clip in track
            if isinstance(clip, otio.schema.Clip)
        ]
        audio_clips = [
            clip
            for track in tl.audio_tracks()
            for clip in track
            if isinstance(clip, otio.schema.Clip)
        ]

        assert len(video_clips) == 1, (
            f"Video track must have exactly 1 clip for audio-less media, "
            f"got {len(video_clips)}."
        )
        assert len(audio_clips) == 0, (
            f"Audio track must have 0 clips for audio-less media (L-1 regression), "
            f"got {len(audio_clips)}."
        )

    def test_audio_bearing_media_has_audio_clip_in_timeline(
        self, tmp_path: Path
    ) -> None:
        """Audio-bearing media must populate both V1 and A1 tracks in the new timeline.

        Contrast guard: when has_audio=True (default), the Audio track must also
        receive a full-length clip alongside the Video track.
        """
        result = _run_reframe(tmp_path, has_audio=True, output_name="out_audio.otio")
        assert result["ok"] is True, f"Expected ok=True, got: {result}"

        tl = otio.adapters.read_from_file(str(tmp_path / "out_audio.otio"))

        video_clips = [
            clip
            for track in tl.video_tracks()
            for clip in track
            if isinstance(clip, otio.schema.Clip)
        ]
        audio_clips = [
            clip
            for track in tl.audio_tracks()
            for clip in track
            if isinstance(clip, otio.schema.Clip)
        ]

        assert len(video_clips) == 1, (
            f"Video track must have exactly 1 clip for audio-bearing media, "
            f"got {len(video_clips)}."
        )
        assert len(audio_clips) == 1, (
            f"Audio track must have exactly 1 clip for audio-bearing media, "
            f"got {len(audio_clips)}."
        )


# ===========================================================================
# D. Idempotency
# ===========================================================================


class TestIdempotency:
    """Same inputs applied twice must not duplicate the directive (idempotentHint=True)."""

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
#
# Two layers of tests:
#   - test_*_raises_validation_error: schema-level tests (ValidationError from pydantic)
#   - test_*_returns_invalid_input: contract-level tests (reframe() -> ok=False / INVALID_INPUT)
#
# The contract tests use model_construct() to bypass schema validation and produce
# invalid ReframeOptions, then call reframe() to verify _reframe_inner's defensive
# re-validation (step 0) returns the correct error envelope.
# ===========================================================================


class TestInputValidationErrors:
    """Schema and contract validation for input constraints (FR-5)."""

    # ---
    # Schema-level tests (ValidationError from Pydantic)
    # ---

    def test_odd_target_w_raises_validation_error(self) -> None:
        """Odd target_w must raise ValidationError with 'even' in message (AC-03 / F-1s)."""
        with pytest.raises(ValidationError) as exc_info:
            ReframeOptions(target_w=1081, target_h=1920)
        errors_str = str(exc_info.value)
        assert "even" in errors_str.lower(), (
            f"Expected 'even' in ValidationError for odd target_w, got: {errors_str}"
        )

    def test_odd_target_h_raises_validation_error(self) -> None:
        """Odd target_h must raise ValidationError with 'even' in message (AC-03 / F-2s)."""
        with pytest.raises(ValidationError) as exc_info:
            ReframeOptions(target_w=1080, target_h=1081)
        errors_str = str(exc_info.value)
        assert "even" in errors_str.lower(), (
            f"Expected 'even' in ValidationError for odd target_h, got: {errors_str}"
        )

    def test_target_w_below_minimum_raises_validation_error(self) -> None:
        """target_w=1 (odd and < 2) must raise ValidationError (AC-04 / F-3s)."""
        with pytest.raises(ValidationError):
            ReframeOptions(target_w=1, target_h=1080)

    def test_pad_color_injection_raises_validation_error(self) -> None:
        """pad_color with injection attempt must raise ValidationError (AC-05 / F-4s)."""
        with pytest.raises(ValidationError):
            ReframeOptions(target_w=1080, target_h=1920, pad_color="red;scale=1:1")

    # ---
    # Contract-level tests: reframe() returns INVALID_INPUT (via defensive re-validation)
    #
    # model_construct() bypasses Pydantic validators, producing invalid options.
    # _reframe_inner step 0 calls model_validate() and converts ValidationError
    # to ClipwrightError(INVALID_INPUT), which the public reframe() wraps as ok=False.
    # ---

    def test_odd_target_w_returns_invalid_input(self, tmp_path: Path) -> None:
        """Odd target_w passed via model_construct must return INVALID_INPUT (AC-03 / F-1)."""
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = ReframeOptions.model_construct(
            target_w=1081,
            target_h=1920,
            mode="pad",
            anchor="center",
            pad_color="black",
        )
        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            result = reframe(
                media=str(media),
                output=str(output),
                options=opts,
                timeline=None,
            )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value, (
            f"Expected INVALID_INPUT for odd target_w, got: {result['error']['code']}"
        )

    def test_odd_target_h_returns_invalid_input(self, tmp_path: Path) -> None:
        """Odd target_h passed via model_construct must return INVALID_INPUT (AC-03 / F-2)."""
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = ReframeOptions.model_construct(
            target_w=1080,
            target_h=1081,
            mode="pad",
            anchor="center",
            pad_color="black",
        )
        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            result = reframe(
                media=str(media),
                output=str(output),
                options=opts,
                timeline=None,
            )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value, (
            f"Expected INVALID_INPUT for odd target_h, got: {result['error']['code']}"
        )

    def test_target_w_below_minimum_returns_invalid_input(self, tmp_path: Path) -> None:
        """target_w=1 via model_construct must return INVALID_INPUT (AC-04 / F-3)."""
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = ReframeOptions.model_construct(
            target_w=1,
            target_h=1920,
            mode="pad",
            anchor="center",
            pad_color="black",
        )
        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            result = reframe(
                media=str(media),
                output=str(output),
                options=opts,
                timeline=None,
            )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value, (
            f"Expected INVALID_INPUT for target_w=1, got: {result['error']['code']}"
        )

    def test_pad_color_outside_allowlist_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """pad_color injection via model_construct must return INVALID_INPUT (AC-05 / F-4)."""
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = ReframeOptions.model_construct(
            target_w=1080,
            target_h=1920,
            mode="pad",
            anchor="center",
            pad_color="red;scale=1:1",
        )
        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            result = reframe(
                media=str(media),
                output=str(output),
                options=opts,
                timeline=None,
            )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value, (
            f"Expected INVALID_INPUT for unsafe pad_color, got: {result['error']['code']}"
        )


# ===========================================================================
# G. MCP server annotations (NFR-2.1 / NFR-2.2)
# ===========================================================================


class TestMcpAnnotations:
    """Verify MCP tool registration and annotations on clipwright_reframe server."""

    def _get_tool_and_annotations(self) -> tuple[object, object]:
        from clipwright_reframe.server import mcp

        # FastMCP does not expose a stable public API for retrieving a registered
        # tool by name.  We access the private _tool_manager here because there is
        # no public alternative as of FastMCP 2.x.  This may break on a FastMCP
        # major version upgrade — if it does, update the accessor to match the
        # then-current API.
        tool = mcp._tool_manager.get_tool("clipwright_reframe")  # noqa: SLF001
        assert tool is not None, (
            "clipwright_reframe must be registered in mcp (NFR-2.2)"
        )
        return tool, tool.annotations

    def test_tool_name_is_clipwright_reframe(self) -> None:
        """MCP tool must be registered as 'clipwright_reframe' (NFR-2.2)."""
        from clipwright_reframe.server import mcp

        # FastMCP does not expose a stable public API for tool lookup; accessing
        # _tool_manager is necessary (see _get_tool_and_annotations for rationale).
        tool = mcp._tool_manager.get_tool("clipwright_reframe")  # noqa: SLF001
        assert tool is not None, "clipwright_reframe tool is not registered."

    def test_read_only_hint_is_false(self) -> None:
        """readOnlyHint must be False: clipwright_reframe creates a new .otio file (NFR-2.1)."""
        _tool, annotations = self._get_tool_and_annotations()
        assert annotations.readOnlyHint is False, (  # type: ignore[union-attr]
            "readOnlyHint must be False — clipwright_reframe writes a new .otio file."
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
