"""test_bgm.py — Contract tests for the add_bgm orchestration layer (Red phase).

Mocking policy:
  - clipwright_bgm.bgm.inspect_media is monkeypatched to supply MediaInfo.
    This indirectly verifies that ffprobe is not called via subprocess (ADR-B2-r2).
  - add_bgm does not call ffmpeg (OTIO operations only, ADR-B1).

Test scope:
  5. Success path: A2 Audio track is added and a BGM clip is placed.
     source_range is fixed to full BGM media length (0–bgm_duration) (DC-AS-003, ADR-B2-r2).
     New output timeline is created; input timeline is unchanged (non-destructive, M5).
  6. BGM clip metadata["clipwright"] is annotated via writer BgmDirective (ADR-B3/B9-r2).
  7. Re-invocation detection (DC-AS-002/AM-005, ADR-B2-r3):
     Existing kind=='bgm' clip → INVALID_INPUT.
     A1-only timeline must not be rejected (must not break the success path).
  8. BGM duration is obtained via mocked inspect_media (bgm.py must not call ffprobe directly).
     inspect_media failure (ClipwrightError) → add_bgm formats a ToolResult error (no absolute path).
  9. BGM input extension whitelist (DC-AM-007, ADR-B2-r3):
     Disallowed extension → INVALID_INPUT.
     Allowed set = {mp3,wav,m4a,aac,flac,ogg,opus,mp4,mkv,mov,webm}.
  10. bgm not under the same dir as timeline → PATH_NOT_ALLOWED.
      bgm absent → FILE_NOT_FOUND, basename only.
  11. output == input timeline / existing output collision → appropriate error (non-destructive).
  12. Return value envelope: ok=True, summary contains BGM placement summary, artifacts has output timeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.otio_utils import load_timeline, save_timeline

from clipwright_bgm.bgm import add_bgm
from clipwright_bgm.schemas import BgmDirective, BgmOptions
from tests.conftest import BGM_DURATION_SEC, BGM_RATE

# ===========================================================================
# Helpers
# ===========================================================================


def _make_simple_timeline() -> otio.schema.Timeline:
    """Return a Timeline with two tracks: V1 (Video) and A1 (Audio)."""
    tl = otio.schema.Timeline(name="test_timeline")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(v1)
    tl.tracks.append(a1)
    return tl


def _save_timeline_to_file(tl: otio.schema.Timeline, path: Path) -> None:
    """Helper to save a Timeline to a file."""
    save_timeline(tl, str(path))


def _get_bgm_clips(tl: otio.schema.Timeline) -> list[otio.schema.Clip]:
    """Collect and return all Clips with kind=='bgm' from the timeline."""
    bgm_clips = []
    for track in tl.tracks:
        if track.kind == otio.schema.TrackKind.Audio:
            for item in track:
                if isinstance(item, otio.schema.Clip):
                    meta = item.metadata.get("clipwright", {})
                    if meta.get("kind") == "bgm":
                        bgm_clips.append(item)
    return bgm_clips


# ===========================================================================
# Test scope 5: Success path - A2 track addition, BGM clip placement, non-destructive
# ===========================================================================


class TestAddBgmNormalCase:
    """add_bgm success path: A2 track added, BGM clip placed, input timeline unchanged."""

    def test_a2_audio_track_is_added(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """After add_bgm, an A2 Audio track must be present in the timeline."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is True
        out_tl = load_timeline(str(output_path))
        audio_tracks = [
            t for t in out_tl.tracks if t.kind == otio.schema.TrackKind.Audio
        ]
        assert len(audio_tracks) >= 2, (
            "At least 2 Audio tracks including A2 are required"
        )
        track_names = [t.name for t in audio_tracks]
        assert "A2" in track_names, "A2 Audio track must exist"

    def test_bgm_clip_is_placed_in_a2_track(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """Exactly one BGM clip must be placed in the A2 track."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is True
        out_tl = load_timeline(str(output_path))
        bgm_clips = _get_bgm_clips(out_tl)
        assert len(bgm_clips) == 1, (
            "Exactly one BGM clip must be present in the A2 track"
        )

    def test_source_range_equals_bgm_full_duration(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """BGM clip source_range must equal the full BGM media length (0–bgm_duration) (DC-AS-003, ADR-B2-r2)."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        out_tl = load_timeline(str(output_path))
        bgm_clips = _get_bgm_clips(out_tl)
        assert len(bgm_clips) == 1
        clip = bgm_clips[0]
        assert clip.source_range is not None
        start_sec = otio.opentime.to_seconds(clip.source_range.start_time)
        duration_sec = otio.opentime.to_seconds(clip.source_range.duration)
        assert start_sec == pytest.approx(0.0), "source_range start must be 0 seconds"
        assert duration_sec == pytest.approx(BGM_DURATION_SEC), (
            f"source_range duration must equal the full BGM length {BGM_DURATION_SEC}s"
        )

    def test_input_timeline_is_unchanged(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """add_bgm must not modify the input timeline file (non-destructive, M5)."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)
        original_content = timeline_path.read_bytes()

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert timeline_path.read_bytes() == original_content, (
            "Input timeline file bytes have changed (non-destructive violation)"
        )

    def test_output_timeline_is_a_new_file(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """add_bgm must create a new output file distinct from the input timeline."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert output_path.exists(), "Output timeline file must be created"
        assert timeline_path != output_path, "Input and output must be different files"


# ===========================================================================
# Test scope 6: BGM clip metadata must be annotated via writer BgmDirective
# ===========================================================================


class TestAddBgmMetadata:
    """BGM clip metadata["clipwright"] must contain a BgmDirective-format annotation (ADR-B3/B9-r2)."""

    def test_clipwright_metadata_exists_on_bgm_clip(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """BGM clip metadata must contain the "clipwright" key."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        out_tl = load_timeline(str(output_path))
        bgm_clips = _get_bgm_clips(out_tl)
        assert len(bgm_clips) == 1
        meta = bgm_clips[0].metadata.get("clipwright")
        assert meta is not None, 'BGM clip must have metadata["clipwright"]'

    def test_bgm_metadata_tool_field(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """BGM clip metadata tool field must be "clipwright-bgm"."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        out_tl = load_timeline(str(output_path))
        bgm_clips = _get_bgm_clips(out_tl)
        meta = bgm_clips[0].metadata["clipwright"]
        assert meta["tool"] == "clipwright-bgm"

    def test_bgm_metadata_kind_is_bgm(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """BGM clip metadata kind field must be "bgm"."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        out_tl = load_timeline(str(output_path))
        bgm_clips = _get_bgm_clips(out_tl)
        meta = bgm_clips[0].metadata["clipwright"]
        assert meta["kind"] == "bgm"

    def test_bgm_metadata_volume_db_matches_options(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """BGM clip metadata volume_db must match the options value."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-12.0),
            )

        out_tl = load_timeline(str(output_path))
        bgm_clips = _get_bgm_clips(out_tl)
        meta = bgm_clips[0].metadata["clipwright"]
        assert meta["volume_db"] == pytest.approx(-12.0)

    def test_bgm_metadata_fade_fields_match_options(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """BGM clip metadata fade_in/out_sec must match the options values."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0, fade_in_sec=1.5, fade_out_sec=2.0),
            )

        out_tl = load_timeline(str(output_path))
        bgm_clips = _get_bgm_clips(out_tl)
        meta = bgm_clips[0].metadata["clipwright"]
        assert meta["fade_in_sec"] == pytest.approx(1.5)
        assert meta["fade_out_sec"] == pytest.approx(2.0)

    def test_bgm_metadata_ducking_matches_options(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """BGM clip metadata ducking fields must match the options values."""
        from clipwright_bgm.schemas import DuckingOptions

        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)
        opts = BgmOptions(
            volume_db=-6.0,
            ducking=DuckingOptions(enabled=True, threshold=0.08, ratio=6.0),
        )

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=opts,
            )

        out_tl = load_timeline(str(output_path))
        bgm_clips = _get_bgm_clips(out_tl)
        meta = bgm_clips[0].metadata["clipwright"]
        assert meta["ducking"]["enabled"] is True
        assert meta["ducking"]["threshold"] == pytest.approx(0.08)
        assert meta["ducking"]["ratio"] == pytest.approx(6.0)

    def test_bgm_metadata_is_valid_bgm_directive(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """BGM clip metadata["clipwright"] must be reconstructible as a BgmDirective (DC-AS-001)."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        out_tl = load_timeline(str(output_path))
        bgm_clips = _get_bgm_clips(out_tl)
        meta = bgm_clips[0].metadata["clipwright"]
        # Verify that the metadata can be reconstructed as a BgmDirective
        directive = BgmDirective(**meta)
        assert directive.kind == "bgm"
        assert directive.tool == "clipwright-bgm"


# ===========================================================================
# Test scope 7: Re-invocation detection (existing kind=='bgm' clip → INVALID_INPUT)
# ===========================================================================


class TestAddBgmDuplicateDetection:
    """Re-invocation detection: existing kind=='bgm' clip → INVALID_INPUT (DC-AS-002/AM-005, ADR-B2-r3)."""

    def _add_bgm_clip_to_timeline(
        self, tl: otio.schema.Timeline, bgm_path: Path
    ) -> None:
        """Helper to manually add a kind=='bgm' clip to a timeline (simulates a prior add_bgm call)."""
        a2 = otio.schema.Track(name="A2", kind=otio.schema.TrackKind.Audio)
        ref = otio.schema.ExternalReference(target_url=str(bgm_path))
        source_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, BGM_RATE),
            duration=otio.opentime.RationalTime(BGM_DURATION_SEC * BGM_RATE, BGM_RATE),
        )
        bgm_clip = otio.schema.Clip(
            name=bgm_path.name,
            media_reference=ref,
            source_range=source_range,
            metadata={"clipwright": {"kind": "bgm", "tool": "clipwright-bgm"}},
        )
        a2.append(bgm_clip)
        tl.tracks.append(a2)

    def test_existing_bgm_clip_raises_invalid_input(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """Timeline that already has a kind=='bgm' clip → INVALID_INPUT (ADR-B2-r3)."""
        tl = _make_simple_timeline()
        self._add_bgm_clip_to_timeline(tl, bgm_audio_file)
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_a1_audio_track_only_does_not_trigger_duplicate_error(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """A1-only timeline (no kind=='bgm' clip) must not trigger a re-invocation error (must not break success path, ADR-B4-r2)."""
        # new_timeline always has A1, so A1 alone must not be rejected
        tl = _make_simple_timeline()  # V1 + A1 (no BGM clip)
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is True, (
            "A1-only timeline must be treated as having no BGM and processed successfully"
        )

    def test_duplicate_error_message_does_not_contain_clip_name(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """Re-invocation error message/hint must not contain the existing clip name (SR L-2, fixed text)."""
        tl = _make_simple_timeline()
        # Use a distinctive name so that any leakage into the error message is detectable
        a2 = otio.schema.Track(name="A2", kind=otio.schema.TrackKind.Audio)
        ref = otio.schema.ExternalReference(target_url=str(bgm_audio_file))
        source_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, BGM_RATE),
            duration=otio.opentime.RationalTime(BGM_DURATION_SEC * BGM_RATE, BGM_RATE),
        )
        bgm_clip = otio.schema.Clip(
            name="EXISTING_CLIP_SENTINEL_NAME",  # distinctive value to detect leakage into error messages
            media_reference=ref,
            source_range=source_range,
            metadata={"clipwright": {"kind": "bgm", "tool": "clipwright-bgm"}},
        )
        a2.append(bgm_clip)
        tl.tracks.append(a2)
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"
        error_message = result["error"]["message"]
        error_hint = result["error"]["hint"]
        # Existing clip name must not appear in message/hint (SR L-2, fixed text)
        assert "EXISTING_CLIP_SENTINEL_NAME" not in error_message, (
            "Re-invocation error message contains the existing clip name (SR L-2)"
        )
        assert "EXISTING_CLIP_SENTINEL_NAME" not in error_hint, (
            "Re-invocation error hint contains the existing clip name (SR L-2)"
        )

    def test_duplicate_detection_is_based_on_kind_not_track_name(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """Re-invocation detection must be based on kind=='bgm' clip presence, not track name 'A2' (ADR-B2-r3).

        A kind=='bgm' clip on a track with a name other than 'A2' must also trigger INVALID_INPUT.
        """
        tl = _make_simple_timeline()
        # Add a kind=='bgm' clip on a track named "BGM_CUSTOM" instead of "A2"
        bgm_track = otio.schema.Track(
            name="BGM_CUSTOM", kind=otio.schema.TrackKind.Audio
        )
        ref = otio.schema.ExternalReference(target_url=str(bgm_audio_file))
        source_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, BGM_RATE),
            duration=otio.opentime.RationalTime(BGM_DURATION_SEC * BGM_RATE, BGM_RATE),
        )
        bgm_clip = otio.schema.Clip(
            name=bgm_audio_file.name,
            media_reference=ref,
            source_range=source_range,
            metadata={"clipwright": {"kind": "bgm", "tool": "clipwright-bgm"}},
        )
        bgm_track.append(bgm_clip)
        tl.tracks.append(bgm_track)
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT", (
            "INVALID_INPUT must be returned regardless of track name when a kind=='bgm' clip exists"
        )


# ===========================================================================
# Test scope 8: BGM duration via inspect_media; error formatting on failure
# ===========================================================================


class TestAddBgmInspectMedia:
    """BGM duration must be obtained via inspect_media; failures must be formatted as errors (ADR-B2-r2)."""

    def test_inspect_media_is_called_for_bgm_duration(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """add_bgm must call inspect_media to obtain the BGM duration."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch(
            "clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm
        ) as mock_inspect:
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        mock_inspect.assert_called_once()
        call_args = mock_inspect.call_args
        # The path passed to inspect_media must be the BGM file path
        called_path = call_args[0][0] if call_args[0] else call_args[1].get("media", "")
        assert (
            str(bgm_audio_file.name) in called_path
            or str(bgm_audio_file) in called_path
        )

    def test_inspect_media_failure_returns_error_envelope(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
    ) -> None:
        """When inspect_media raises ClipwrightError, add_bgm must return a ToolResult error envelope."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        def _raise_inspect_error(*args: Any, **kwargs: Any) -> None:
            raise ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="ffprobe not found on PATH",
                hint="Install ffprobe.",
            )

        with patch(
            "clipwright_bgm.bgm.inspect_media", side_effect=_raise_inspect_error
        ):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is False
        assert result["error"]["code"] in (
            "DEPENDENCY_MISSING",
            "INVALID_INPUT",
            "SUBPROCESS_FAILED",
        )

    def test_inspect_media_failure_does_not_expose_absolute_path(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
    ) -> None:
        """Error message on inspect_media failure must not expose the absolute path (CWE-209, ADR-B2-r2)."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        def _raise_inspect_error(*args: Any, **kwargs: Any) -> None:
            raise ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message=f"ffprobe not found: {bgm_audio_file}",
                hint="Install ffprobe.",
            )

        with patch(
            "clipwright_bgm.bgm.inspect_media", side_effect=_raise_inspect_error
        ):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is False
        error_message = result["error"]["message"]
        # Absolute path (tmp_timeline_dir) must not appear in the error message
        assert str(tmp_timeline_dir) not in error_message, (
            "Error message exposes an absolute path (CWE-209)"
        )


# ===========================================================================
# Test scope 9: BGM input extension whitelist (DC-AM-007, ADR-B2-r3)
# ===========================================================================


class TestAddBgmExtensionWhitelist:
    """BGM input extension whitelist validation (DC-AM-007, ADR-B2-r3)."""

    @pytest.mark.parametrize(
        "ext",
        [
            "mp3",
            "wav",
            "m4a",
            "aac",
            "flac",
            "ogg",
            "opus",
            "mp4",
            "mkv",
            "mov",
            "webm",
        ],
    )
    def test_allowed_extension_accepted(
        self,
        tmp_timeline_dir: Path,
        media_info_bgm: Any,
        ext: str,
    ) -> None:
        """BGM file with allowed extension .{ext} must be accepted."""
        bgm_file = tmp_timeline_dir / f"bgm.{ext}"
        bgm_file.write_bytes(b"dummy bgm")
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / f"output_{ext}.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is True, f".{ext} is an allowed extension"

    @pytest.mark.parametrize(
        "ext",
        ["txt", "py", "mp3.bak", "avi", "wmv", "exe", "sh"],
    )
    def test_disallowed_extension_returns_invalid_input(
        self,
        tmp_timeline_dir: Path,
        media_info_bgm: Any,
        ext: str,
    ) -> None:
        """BGM file with a disallowed extension must return INVALID_INPUT."""
        bgm_file = tmp_timeline_dir / f"bgm.{ext}"
        bgm_file.write_bytes(b"not bgm")
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / f"output_{ext}.otio"
        _save_timeline_to_file(tl, timeline_path)

        result = add_bgm(
            timeline=str(timeline_path),
            bgm=str(bgm_file),
            output=str(output_path),
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"


# ===========================================================================
# Test scope 10: Boundary validation and FILE_NOT_FOUND
# ===========================================================================


class TestAddBgmPathValidation:
    """BGM path boundary validation and file-not-found checks (ADR-B8, ADR-B10)."""

    def test_bgm_outside_timeline_dir_succeeds(
        self,
        tmp_timeline_dir: Path,
        media_info_bgm: Any,
        tmp_path: Path,
    ) -> None:
        """External bgm (outside the timeline directory) must be accepted under the new policy.

        Previously (ADR-B8) bgm was required to be in the same directory as the timeline.
        New policy: bgm may be any existing regular non-symlink file (external allowed).

        Red: _check_bgm_within_timeline_dir still enforces co-location → PATH_NOT_ALLOWED.
        """
        # tmp_path is a different dir (parent of tmp_timeline_dir)
        outside_bgm = tmp_path / "outside_bgm.mp3"
        outside_bgm.write_bytes(b"outside bgm")
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(outside_bgm),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        # RED: currently returns PATH_NOT_ALLOWED; new policy must succeed
        assert result["ok"] is True, (
            "External bgm must be accepted under the new path-boundary policy. "
            f"Got error: {result.get('error')}"
        )

    def test_bgm_file_not_found_returns_file_not_found(
        self,
        tmp_timeline_dir: Path,
        media_info_bgm: Any,
    ) -> None:
        """When the bgm file does not exist, FILE_NOT_FOUND must be returned (ADR-B10)."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)
        nonexistent_bgm = tmp_timeline_dir / "nonexistent_bgm.mp3"

        result = add_bgm(
            timeline=str(timeline_path),
            bgm=str(nonexistent_bgm),
            output=str(output_path),
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "FILE_NOT_FOUND"

    def test_file_not_found_error_message_contains_basename_only(
        self,
        tmp_timeline_dir: Path,
    ) -> None:
        """FILE_NOT_FOUND error message must not contain the absolute path — basename only (ADR-B10, CWE-209)."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)
        nonexistent_bgm = tmp_timeline_dir / "missing_bgm.mp3"

        result = add_bgm(
            timeline=str(timeline_path),
            bgm=str(nonexistent_bgm),
            output=str(output_path),
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False
        error_message = result["error"]["message"]
        assert str(tmp_timeline_dir) not in error_message, (
            "Error message contains an absolute path (CWE-209)"
        )
        assert "missing_bgm.mp3" in error_message, (
            "Error message must contain the basename"
        )

    def test_timeline_file_not_found_returns_error(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
    ) -> None:
        """When the input timeline file does not exist, an error must be returned."""
        nonexistent_timeline = tmp_timeline_dir / "nonexistent_timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"

        result = add_bgm(
            timeline=str(nonexistent_timeline),
            bgm=str(bgm_audio_file),
            output=str(output_path),
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False


# ===========================================================================
# Test scope 10b: output path boundary validation (outside timeline directory)
# ===========================================================================


class TestAddBgmOutputPathBoundary:
    """Output path boundary: output may now be placed outside the timeline directory (new policy).

    Previously (SR L-3) output was required to be in the same directory as the timeline.
    New policy: output parent directory must exist and output must not equal any source;
    co-location with the timeline is no longer required.
    """

    def test_output_outside_timeline_dir_succeeds(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
        tmp_path: Path,
    ) -> None:
        """add_bgm must succeed when output is placed outside the timeline directory (new policy).

        Previously PATH_NOT_ALLOWED was returned (SR L-3); new policy relaxes this restriction.

        Red: _check_output_within_timeline_dir still enforces co-location → PATH_NOT_ALLOWED.
        """
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        _save_timeline_to_file(tl, timeline_path)
        # tmp_path is outside tmp_timeline_dir (directly under the parent directory)
        outside_output = tmp_path / "outside_output.otio"

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(outside_output),
                options=BgmOptions(volume_db=-6.0),
            )

        # RED: currently returns PATH_NOT_ALLOWED; new policy must succeed
        assert result["ok"] is True, (
            "output outside the timeline directory must be accepted under the new policy. "
            f"Got error: {result.get('error')}"
        )


# ===========================================================================
# Test scope 10c: inspect_media returns duration=None
# ===========================================================================


class TestAddBgmDurationNone:
    """When inspect_media returns a MediaInfo with duration=None, INVALID_INPUT must be returned (CR M-2)."""

    def test_duration_none_returns_invalid_input(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
    ) -> None:
        """inspect_media returns duration=None → INVALID_INPUT (no AttributeError leakage, CR M-2)."""
        from clipwright.schemas import MediaInfo, StreamInfo

        media_info_no_duration = MediaInfo(
            path=str(bgm_audio_file),
            container="mp4",
            duration=None,  # e.g., when there is no audio stream and duration cannot be obtained
            streams=[
                StreamInfo(
                    index=0,
                    codec_type="video",
                    codec_name="h264",
                )
            ],
            bit_rate=1_000_000,
        )
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch(
            "clipwright_bgm.bgm.inspect_media", return_value=media_info_no_duration
        ):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT", (
            "INVALID_INPUT must be returned when duration=None (CR M-2, no AttributeError leakage)"
        )
        # Verify that AttributeError does not leak (result is contained in the ok=False envelope)
        assert "error" in result


# ===========================================================================
# Test scope 11: output == input timeline / existing output collision
# ===========================================================================


class TestAddBgmOutputCollision:
    """output == input timeline / existing output collision must return errors (non-destructive, ADR-B10)."""

    def test_output_same_as_input_timeline_returns_error(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """When output == input timeline path, an error must be returned (overwrite forbidden, M5).

        check_output_not_source raises PATH_NOT_ALLOWED when output equals any source.
        Acceptable codes: INVALID_INPUT or PATH_NOT_ALLOWED (both reject the operation).
        """
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        _save_timeline_to_file(tl, timeline_path)

        result = add_bgm(
            timeline=str(timeline_path),
            bgm=str(bgm_audio_file),
            output=str(timeline_path),  # output == input
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False
        assert result["error"]["code"] in ("INVALID_INPUT", "PATH_NOT_ALLOWED"), (
            f"output == timeline must be rejected. Got: {result['error']['code']!r}"
        )

    def test_output_already_exists_returns_error(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """When an existing file is at the output path, INVALID_INPUT must be returned (overwrite forbidden)."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)
        # Pre-create the output file to simulate a collision
        output_path.write_bytes(b"existing output content")

        result = add_bgm(
            timeline=str(timeline_path),
            bgm=str(bgm_audio_file),
            output=str(output_path),
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"


# ===========================================================================
# Test scope 12: Return value envelope
# ===========================================================================


class TestAddBgmResultEnvelope:
    """Contract check for add_bgm return value envelope (ok, summary, artifacts)."""

    def test_result_ok_is_true(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """ok=True must be returned on the success path."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is True

    def test_result_summary_is_nonempty(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """summary must be non-empty on the success path (must contain key points for AI decision-making)."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["summary"]
        assert len(result["summary"]) > 0

    def test_result_artifacts_contains_output_timeline(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """artifacts must contain the output timeline path on the success path."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        artifacts = result.get("artifacts", [])
        assert len(artifacts) >= 1, "artifacts must have at least one entry"
        artifact_paths = [
            a["path"] if isinstance(a, dict) else a.path for a in artifacts
        ]
        assert any(
            str(output_path) in p or p.endswith("output.otio") for p in artifact_paths
        ), "artifacts must contain the output timeline path"

    def test_result_has_required_envelope_keys(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """ok/summary/data/artifacts/warnings keys must exist on the success path (§6.3)."""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        for key in ("ok", "summary", "data", "artifacts", "warnings"):
            assert key in result, f"Envelope is missing key {key!r}"
