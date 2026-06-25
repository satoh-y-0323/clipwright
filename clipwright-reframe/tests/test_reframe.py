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

  H. track mode / fallback (wave2-B Red phase)
     H-1: mode='track' -> track_cli spawned, directive has mode='track' and track (AC-01改)
     H-2: track_cli DEPENDENCY_MISSING -> ok:true, constant-center track, warning has '[track]'
     H-3: track_cli SUBPROCESS_FAILED -> ok:true, constant-center track, warning set (AC-03)
     H-4: track_cli run() raises -> ok:true, constant-center fallback, warning (AC-03)
     H-5: non-destructive: input bytes / mtime unchanged after track run (AC-07)
     H-6: idempotent: two track runs produce identical track lists (AC-08)
     H-7: CWE-209: warning text must not contain full media path or stack trace

AC coverage:
  AC-01改 -> H-1
  AC-02   -> A-2, A-3
  AC-03   -> F-1, F-2, F-1s, F-2s, H-3, H-4
  AC-04   -> F-3, F-3s
  AC-05   -> F-4, F-4s
  AC-06   -> C-1 through C-8
  AC-07   -> H-5
  AC-08   -> H-6
  AC-14   -> A-1
  NFR-1   -> E-1, E-2, H-5
  NFR-2.1 -> G-2 through G-5
  NFR-2.2 -> G-1
  D1      -> B-1 through B-7
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

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


# ===========================================================================
# H. track mode and fallback (wave2-B Red phase)
#
# Design note (DC-AS-007 / architecture-report §5):
#   The fallback constant-center track [{t_s:0, cx:0.5, cy:0.5}] is processed
#   through the crop-from-source path (render side).  This is *distinct* from
#   the existing scale-first static crop path (mode='crop').  Pixel values will
#   differ by design; tests here only verify the reframe-side directive contract,
#   not render-side pixel output.
# ===========================================================================

_CONSTANT_CENTER_TRACK = [{"t_s": 0.0, "cx": 0.5, "cy": 0.5}]

_DEPENDENCY_MISSING_JSON = json.dumps(
    {
        "error": {
            "code": "DEPENDENCY_MISSING",
            "message": "numpy is not installed",
            "hint": "pip install 'clipwright-reframe[track]'",
        }
    }
)
_SUBPROCESS_FAILED_JSON = json.dumps(
    {
        "error": {
            "code": "SUBPROCESS_FAILED",
            "message": "ffmpeg exited with code 1",
            "hint": "Check the media file.",
        }
    }
)


def _make_track_result_json(n_kf: int = 3) -> str:
    """Return a valid track_cli JSON response with n_kf keyframes."""
    track = [
        {"t_s": float(i) * 0.25, "cx": 0.3 + 0.1 * i, "cy": 0.5} for i in range(n_kf)
    ]
    return json.dumps(
        {
            "track": track,
            "diagnostics": {
                "frames_analyzed": 12,
                "keyframes_before_decimation": n_kf,
                "keyframes_after_decimation": n_kf,
                "dropped": 0,
                "hold_fraction": 0.0,
                "sampling_fps": 4.0,
                "width": 160,
                "height": 90,
            },
        }
    )


def _make_track_opts(target_w: int = 1080, target_h: int = 1920) -> ReframeOptions:
    """Build ReframeOptions with mode='track'."""
    return ReframeOptions(
        target_w=target_w,
        target_h=target_h,
        mode="track",
        anchor="center",
        pad_color="black",
    )


def _run_track_reframe(
    tmp_path: Path,
    *,
    track_cli_stdout: str,
    track_cli_returncode: int = 0,
    media_name: str = "video.mp4",
    output_name: str = "out.otio",
    target_w: int = 1080,
    target_h: int = 1920,
) -> dict[str, Any]:
    """Helper: call reframe(mode='track') with track_cli mocked via core run.

    track_cli is spawned as a subprocess via core run; we mock run() to return
    the provided JSON on stdout so no real numpy/ffmpeg is needed.
    """
    media_path = tmp_path / media_name
    media_path.write_bytes(b"dummy media")
    output_path = tmp_path / output_name

    opts = _make_track_opts(target_w=target_w, target_h=target_h)

    fake_run_result = MagicMock()
    fake_run_result.returncode = track_cli_returncode
    fake_run_result.stdout = track_cli_stdout
    fake_run_result.stderr = ""

    with (
        patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ),
        patch(
            "clipwright.process.run",
            return_value=fake_run_result,
        ),
    ):
        result = reframe(
            media=str(media_path),
            output=str(output_path),
            options=opts,
            timeline=None,
        )
    return result  # type: ignore[return-value]


class TestTrackMode:
    """track mode and fallback behaviour (H-1 through H-7, AC-01改 / AC-02 / AC-03 / AC-07 / AC-08)."""

    # -----------------------------------------------------------------------
    # H-1: track_cli spawned, directive has mode='track' and valid track (AC-01改)
    # -----------------------------------------------------------------------

    def test_track_mode_directive_has_mode_track(self, tmp_path: Path) -> None:
        """mode='track' run must write directive with mode=='track' (AC-01改, DC-AM-001).

        Identification is by mode field, not version (DC-AM-001).

        Red: reframe.py has no mode='track' branch yet — returns mode='track' from
        options but without calling track_cli or storing the track list.
        """
        result = _run_track_reframe(
            tmp_path, track_cli_stdout=_make_track_result_json(3)
        )

        assert result["ok"] is True, f"Expected ok=True, got: {result}"

        tl = otio.adapters.read_from_file(str(tmp_path / "out.otio"))
        meta = dict(tl.metadata.get("clipwright", {}).get("reframe", {}))
        assert meta.get("mode") == "track", (
            f"Directive mode must be 'track', got: {meta.get('mode')!r}"
        )

    def test_track_mode_directive_stores_track_list(self, tmp_path: Path) -> None:
        """mode='track' directive must contain non-empty track list from track_cli (AC-01改).

        Red: reframe.py does not call track_cli; directive.track will be None.
        """
        result = _run_track_reframe(
            tmp_path, track_cli_stdout=_make_track_result_json(3)
        )

        assert result["ok"] is True
        tl = otio.adapters.read_from_file(str(tmp_path / "out.otio"))
        meta = dict(tl.metadata.get("clipwright", {}).get("reframe", {}))
        track = meta.get("track")
        assert track is not None, "track must be present in directive for mode='track'"
        assert isinstance(track, list), f"track must be a list, got {type(track)}"
        assert len(track) >= 1, "track must have at least one keyframe"

    def test_track_mode_track_elements_in_range(self, tmp_path: Path) -> None:
        """All track elements must have cx, cy in [0.0, 1.0] and t_s >= 0 (AC-01改).

        Red: same as above — track_cli not called.
        """
        result = _run_track_reframe(
            tmp_path, track_cli_stdout=_make_track_result_json(3)
        )

        assert result["ok"] is True
        tl = otio.adapters.read_from_file(str(tmp_path / "out.otio"))
        meta = dict(tl.metadata.get("clipwright", {}).get("reframe", {}))
        track = meta.get("track", [])
        for kf in track:
            assert 0.0 <= kf["cx"] <= 1.0, f"cx={kf['cx']} out of [0,1]"
            assert 0.0 <= kf["cy"] <= 1.0, f"cy={kf['cy']} out of [0,1]"
            assert kf["t_s"] >= 0.0, f"t_s={kf['t_s']} must be non-negative"

    def test_track_mode_t_s_ascending(self, tmp_path: Path) -> None:
        """track t_s must be strictly ascending (no duplicates).

        Red: track_cli not called; directive.track is None.
        """
        result = _run_track_reframe(
            tmp_path, track_cli_stdout=_make_track_result_json(3)
        )

        assert result["ok"] is True
        tl = otio.adapters.read_from_file(str(tmp_path / "out.otio"))
        meta = dict(tl.metadata.get("clipwright", {}).get("reframe", {}))
        track = meta.get("track", [])
        if len(track) >= 2:
            for i in range(1, len(track)):
                assert track[i]["t_s"] > track[i - 1]["t_s"], (
                    f"t_s not strictly ascending at index {i}: "
                    f"{track[i - 1]['t_s']} >= {track[i]['t_s']}"
                )

    def test_track_mode_new_otio_created(self, tmp_path: Path) -> None:
        """mode='track' must create a new .otio file (AC-01改 / AC-02).

        Red: reframe.py mode='track' branch absent; may or may not create file.
        """
        result = _run_track_reframe(
            tmp_path, track_cli_stdout=_make_track_result_json(3)
        )

        assert result["ok"] is True
        assert (tmp_path / "out.otio").exists()

    # -----------------------------------------------------------------------
    # H-2: DEPENDENCY_MISSING fallback (AC-02 numpy)
    # -----------------------------------------------------------------------

    def test_dependency_missing_ok_true(self, tmp_path: Path) -> None:
        """track_cli DEPENDENCY_MISSING must return ok:true (graceful fallback, AC-02).

        Red: reframe.py has no track_cli spawn or fallback logic.
        """
        result = _run_track_reframe(tmp_path, track_cli_stdout=_DEPENDENCY_MISSING_JSON)
        assert result["ok"] is True, (
            "DEPENDENCY_MISSING must not propagate as ok=False; "
            "expected graceful constant-center track fallback."
        )

    def test_dependency_missing_constant_center_track(self, tmp_path: Path) -> None:
        """DEPENDENCY_MISSING fallback must write constant-center track [{0, 0.5, 0.5}].

        Red: reframe.py does not call track_cli; directive.track is None.
        """
        result = _run_track_reframe(tmp_path, track_cli_stdout=_DEPENDENCY_MISSING_JSON)
        assert result["ok"] is True
        tl = otio.adapters.read_from_file(str(tmp_path / "out.otio"))
        meta = dict(tl.metadata.get("clipwright", {}).get("reframe", {}))
        track = meta.get("track")
        assert track == _CONSTANT_CENTER_TRACK, (
            f"Expected constant-center track {_CONSTANT_CENTER_TRACK}, got {track}"
        )

    def test_dependency_missing_warning_contains_track_extra(
        self, tmp_path: Path
    ) -> None:
        """DEPENDENCY_MISSING warning must mention '[track]' install hint (AC-02).

        Red: reframe.py does not produce fallback warnings.
        """
        result = _run_track_reframe(tmp_path, track_cli_stdout=_DEPENDENCY_MISSING_JSON)
        assert result["ok"] is True
        warnings = result.get("warnings", [])
        assert any("[track]" in str(w) for w in warnings), (
            f"Expected '[track]' install hint in warnings, got: {warnings}"
        )

    # -----------------------------------------------------------------------
    # H-3: SUBPROCESS_FAILED fallback (AC-03)
    # -----------------------------------------------------------------------

    def test_subprocess_failed_ok_true(self, tmp_path: Path) -> None:
        """track_cli SUBPROCESS_FAILED must return ok:true (fallback, AC-03).

        Red: reframe.py no track_cli call; returns ok:true from existing logic
        but without fallback track.
        """
        result = _run_track_reframe(tmp_path, track_cli_stdout=_SUBPROCESS_FAILED_JSON)
        assert result["ok"] is True

    def test_subprocess_failed_constant_center_track(self, tmp_path: Path) -> None:
        """SUBPROCESS_FAILED must write constant-center track (AC-03).

        Red: track_cli not called; directive.track is None.
        """
        result = _run_track_reframe(tmp_path, track_cli_stdout=_SUBPROCESS_FAILED_JSON)
        assert result["ok"] is True
        tl = otio.adapters.read_from_file(str(tmp_path / "out.otio"))
        meta = dict(tl.metadata.get("clipwright", {}).get("reframe", {}))
        assert meta.get("track") == _CONSTANT_CENTER_TRACK, (
            f"SUBPROCESS_FAILED fallback must write constant-center track, "
            f"got: {meta.get('track')}"
        )

    def test_subprocess_failed_warning_set(self, tmp_path: Path) -> None:
        """SUBPROCESS_FAILED must produce at least one warning (AC-03).

        Red: no fallback warning generated.
        """
        result = _run_track_reframe(tmp_path, track_cli_stdout=_SUBPROCESS_FAILED_JSON)
        assert result["ok"] is True
        warnings = result.get("warnings", [])
        assert len(warnings) >= 1, (
            "SUBPROCESS_FAILED fallback must emit at least one warning"
        )

    # -----------------------------------------------------------------------
    # H-4: run() raises exception -> fallback (AC-03)
    # -----------------------------------------------------------------------

    def test_run_raises_constant_center_fallback(self, tmp_path: Path) -> None:
        """If core run() raises ClipwrightError, fallback must be constant-center track.

        Red: reframe.py does not call track_cli.
        """
        from clipwright.errors import ClipwrightError, ErrorCode

        media_path = tmp_path / "video.mp4"
        media_path.write_bytes(b"dummy")
        output_path = tmp_path / "out.otio"
        opts = _make_track_opts()

        with (
            patch(
                "clipwright_reframe.reframe.inspect_media",
                side_effect=lambda p: _make_media_info(str(p)),
            ),
            patch(
                "clipwright.process.run",
                side_effect=ClipwrightError(
                    code=ErrorCode.SUBPROCESS_FAILED,
                    message="run failed",
                    hint="check ffmpeg",
                ),
            ),
        ):
            result = reframe(
                media=str(media_path),
                output=str(output_path),
                options=opts,
                timeline=None,
            )

        assert result["ok"] is True, (
            "run() raising must not propagate as ok=False; expected fallback."
        )
        tl = otio.adapters.read_from_file(str(output_path))
        meta = dict(tl.metadata.get("clipwright", {}).get("reframe", {}))
        assert meta.get("track") == _CONSTANT_CENTER_TRACK, (
            f"run() raise fallback must write constant-center track, "
            f"got: {meta.get('track')}"
        )

    # -----------------------------------------------------------------------
    # H-5: non-destructive — input bytes / mtime unchanged (AC-07)
    # -----------------------------------------------------------------------

    def test_track_mode_input_media_bytes_unchanged(self, tmp_path: Path) -> None:
        """Input media bytes must be identical before and after track run (AC-07 / NFR-1).

        Red: reframe.py mode='track' branch absent; existing code already passes
        this for mode='pad', but we need explicit coverage for mode='track'.
        """
        original_bytes = b"specific dummy content for track test"
        media_path = tmp_path / "video.mp4"
        media_path.write_bytes(original_bytes)
        output_path = tmp_path / "out.otio"

        opts = _make_track_opts()
        fake_run = MagicMock(
            returncode=0,
            stdout=_make_track_result_json(2),
            stderr="",
        )

        with (
            patch(
                "clipwright_reframe.reframe.inspect_media",
                side_effect=lambda p: _make_media_info(str(p)),
            ),
            patch("clipwright.process.run", return_value=fake_run),
        ):
            result = reframe(
                media=str(media_path),
                output=str(output_path),
                options=opts,
                timeline=None,
            )

        assert result["ok"] is True
        assert media_path.read_bytes() == original_bytes, (
            "Input media must not be modified by track run (AC-07)."
        )

    # -----------------------------------------------------------------------
    # H-6: idempotent — two track runs produce identical track lists (AC-08)
    # -----------------------------------------------------------------------

    def test_track_mode_idempotent_track_list(self, tmp_path: Path) -> None:
        """Two track runs on same media must produce identical track keyframe lists (AC-08).

        Red: track_cli not called; track is always None, so comparison trivially
        passes but the test intent (non-None track identity) will fail.
        """
        opts = _make_track_opts()
        fake_run = MagicMock(
            returncode=0,
            stdout=_make_track_result_json(3),
            stderr="",
        )

        media_path = tmp_path / "video.mp4"
        media_path.write_bytes(b"dummy")

        def _run_once(suffix: str) -> list[Any]:
            out = tmp_path / f"out_{suffix}.otio"
            with (
                patch(
                    "clipwright_reframe.reframe.inspect_media",
                    side_effect=lambda p: _make_media_info(str(p)),
                ),
                patch("clipwright.process.run", return_value=fake_run),
            ):
                reframe(
                    media=str(media_path),
                    output=str(out),
                    options=opts,
                    timeline=None,
                )
            tl = otio.adapters.read_from_file(str(out))
            meta = dict(tl.metadata.get("clipwright", {}).get("reframe", {}))
            return meta.get("track")  # type: ignore[return-value]

        track1 = _run_once("run1")
        track2 = _run_once("run2")

        assert track1 is not None, "First track run must produce a non-None track"
        assert track2 is not None, "Second track run must produce a non-None track"
        assert track1 == track2, (
            f"Idempotency violated: track lists differ.\n"
            f"  run1: {track1}\n  run2: {track2}"
        )

    # -----------------------------------------------------------------------
    # H-7: CWE-209 — warning must not expose path or stack
    # -----------------------------------------------------------------------

    def test_warning_no_path_leak_on_dependency_missing(self, tmp_path: Path) -> None:
        """CWE-209: DEPENDENCY_MISSING warning must not echo the media file path.

        Red: reframe.py does not generate fallback warnings.
        """
        media_path = tmp_path / "secret_video_name.mp4"
        media_path.write_bytes(b"dummy")
        output_path = tmp_path / "out.otio"
        opts = _make_track_opts()
        fake_run = MagicMock(
            returncode=0,
            stdout=_DEPENDENCY_MISSING_JSON,
            stderr="",
        )

        with (
            patch(
                "clipwright_reframe.reframe.inspect_media",
                side_effect=lambda p: _make_media_info(str(p)),
            ),
            patch("clipwright.process.run", return_value=fake_run),
        ):
            result = reframe(
                media=str(media_path),
                output=str(output_path),
                options=opts,
                timeline=None,
            )

        assert result["ok"] is True
        warnings = result.get("warnings", [])
        for w in warnings:
            w_str = str(w)
            assert "secret_video_name" not in w_str, (
                f"CWE-209: media filename must not appear in warning: {w_str!r}"
            )
            assert str(tmp_path) not in w_str, (
                f"CWE-209: full path must not appear in warning: {w_str!r}"
            )
            assert "Traceback" not in w_str, (
                f"CWE-209: stack trace must not appear in warning: {w_str!r}"
            )

    def test_warning_no_stack_leak_on_run_exception(self, tmp_path: Path) -> None:
        """CWE-209: warning from run() exception must not contain stack trace.

        Red: reframe.py does not call track_cli, so no warning is generated at all.
        """
        from clipwright.errors import ClipwrightError, ErrorCode

        media_path = tmp_path / "video.mp4"
        media_path.write_bytes(b"dummy")
        output_path = tmp_path / "out.otio"
        opts = _make_track_opts()

        with (
            patch(
                "clipwright_reframe.reframe.inspect_media",
                side_effect=lambda p: _make_media_info(str(p)),
            ),
            patch(
                "clipwright.process.run",
                side_effect=ClipwrightError(
                    code=ErrorCode.SUBPROCESS_FAILED,
                    message="internal error",
                    hint="retry",
                ),
            ),
        ):
            result = reframe(
                media=str(media_path),
                output=str(output_path),
                options=opts,
                timeline=None,
            )

        assert result["ok"] is True
        for w in result.get("warnings", []):
            assert "Traceback" not in str(w), (
                f"CWE-209: stack trace must not appear in warning: {w!r}"
            )

    def test_subprocess_failed_warning_no_raw_path_or_stderr(
        self, tmp_path: Path
    ) -> None:
        """SUBPROCESS_FAILED warning must not contain absolute paths, Traceback, or
        raw stderr content (CWE-209 / SR-M-2).
        """
        from clipwright.errors import ClipwrightError, ErrorCode

        media_path = tmp_path / "internal_video_secret.mp4"
        media_path.write_bytes(b"dummy")
        output_path = tmp_path / "out.otio"
        opts = _make_track_opts()

        with (
            patch(
                "clipwright_reframe.reframe.inspect_media",
                side_effect=lambda p: _make_media_info(str(p)),
            ),
            patch(
                "clipwright.process.run",
                side_effect=ClipwrightError(
                    code=ErrorCode.SUBPROCESS_FAILED,
                    message="internal subprocess failed",
                    hint="check arguments",
                ),
            ),
        ):
            result = reframe(
                media=str(media_path),
                output=str(output_path),
                options=opts,
                timeline=None,
            )

        assert result["ok"] is True
        warnings_out = result.get("warnings", [])
        assert len(warnings_out) >= 1, "SUBPROCESS_FAILED must emit a warning"
        for w in warnings_out:
            w_str = str(w)
            # Negative assertions: raw internals must not leak (SR-M-2 / CWE-209).
            assert "internal_video_secret" not in w_str, (
                f"Absolute path must not appear in warning: {w_str!r}"
            )
            assert str(tmp_path) not in w_str, (
                f"Absolute path must not appear in warning: {w_str!r}"
            )
            assert "Traceback" not in w_str, (
                f"Stack trace must not appear in warning: {w_str!r}"
            )
            # Raw stderr content guard.
            assert "check arguments" not in w_str, (
                f"Raw hint text must not appear in warning: {w_str!r}"
            )


# ===========================================================================
# N_max sync guard (SR-L-3)
# ===========================================================================


class TestNMaxSync:
    """Lock that reframe._TRACK_MAX_KEYFRAMES, track_cli._DEFAULT_N_MAX, and
    render's _N_MAX_TRACK all share the confirmed N_max=80 value (SR-L-3).

    Independent-copy pattern: each package holds its own constant.  This test
    acts as a CI lock so a change in one constant triggers a visible test failure.
    """

    def test_reframe_track_max_keyframes_equals_track_cli_default(self) -> None:
        """reframe._TRACK_MAX_KEYFRAMES must equal track_cli._DEFAULT_N_MAX."""
        from clipwright_reframe import reframe as reframe_mod
        from clipwright_reframe import track_cli

        assert reframe_mod._TRACK_MAX_KEYFRAMES == track_cli._DEFAULT_N_MAX, (
            f"N_max mismatch: reframe={reframe_mod._TRACK_MAX_KEYFRAMES}"
            f" track_cli={track_cli._DEFAULT_N_MAX}.  Update both constants together."
        )

    def test_reframe_track_max_keyframes_equals_n_max_80(self) -> None:
        """Confirmed adjudicated value is 80 (spike report test-report-spike_exprlen.md)."""
        from clipwright_reframe import reframe as reframe_mod

        assert reframe_mod._TRACK_MAX_KEYFRAMES == 80, (
            "N_max adjudicated value must be 80 (ffmpeg av_expr_parse cap=96 with margin)."
        )
