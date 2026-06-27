"""Tests for clipwright-speed set_speed / _set_speed_inner business logic.

Verifies the implemented clipwright-speed behavior. All tests in this module
exercise the fully implemented _set_speed_inner function.

Covered behaviors:
- AC-1 NON-DESTRUCTIVE: input file bytes unchanged; output is a distinct file
- AC-4 IDEMPOTENCY: apply twice -> exactly one clipwright warp, no stacking
- R-3 FOREIGN WARP SURVIVES: pre-existing non-clipwright LinearTimeWarp preserved
- R-7 ROUND-TRIP: save -> load -> time_scalar intact
- Applies to ALL clips when clip_index=None
- Applies to a SINGLE clip when clip_index=k (gap-aware index space)
- metadata["clipwright"] recorded with tool/version/kind/speed
- speed=1.0 accepted (warp still attached)
- Error cases (each asserts code + hint in the error envelope):
  - bad output extension -> INVALID_INPUT; message must not leak the raw suffix
  - missing output parent -> FILE_NOT_FOUND; message must not contain path separators
  - output outside timeline directory tree -> PATH_NOT_ALLOWED
  - output == timeline -> INVALID_INPUT
  - missing timeline -> FILE_NOT_FOUND
  - no video track (audio-only timeline) -> UNSUPPORTED_OPERATION
  - V1 track present but no clips (Gap only) -> UNSUPPORTED_OPERATION
  - clip_index out of range -> INVALID_INPUT; range in hint only, not in message
  - speed out of range (0.24 / 8.01) -> INVALID_INPUT with "0.25-8.0" hint
"""

from __future__ import annotations

import collections.abc
from pathlib import Path

import opentimelineio as otio
import pytest

from clipwright_speed.schemas import SetSpeedOptions
from clipwright_speed.speed import set_speed

# ---------------------------------------------------------------------------
# conftest imports (used by fixture injections)
# ---------------------------------------------------------------------------
# All fixtures are defined in conftest.py (tmp_dir, simple_timeline_file,
# gap_timeline_file, audio_only_timeline_file).


# ===========================================================================
# Helper
# ===========================================================================


def _get_v1_clips(tl: otio.schema.Timeline) -> list[otio.schema.Clip]:
    """Return all Clip items from the V1 (first Video) track, excluding gaps."""
    for track in tl.tracks:
        if track.kind == otio.schema.TrackKind.Video:
            return [item for item in track if isinstance(item, otio.schema.Clip)]
    return []


def _get_clipwright_warp(clip: otio.schema.Clip) -> otio.schema.LinearTimeWarp | None:
    """Return the clipwright-managed LinearTimeWarp on a clip, or None."""
    for effect in clip.effects:
        if isinstance(effect, otio.schema.LinearTimeWarp):
            cw = effect.metadata.get("clipwright", {})
            if (
                isinstance(cw, collections.abc.Mapping)
                and cw.get("tool") == "clipwright-speed"
            ):
                return effect
    return None


# ===========================================================================
# AC-1 NON-DESTRUCTIVE
# ===========================================================================


class TestNonDestructive:
    """Input timeline file must not be modified after set_speed."""

    def test_input_file_bytes_unchanged(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """Input file bytes must be identical before and after set_speed."""
        before = simple_timeline_file.read_bytes()
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=2.0)
        set_speed(str(simple_timeline_file), str(output), opts)
        after = simple_timeline_file.read_bytes()
        assert before == after, "Input timeline file was modified by set_speed"

    def test_output_is_distinct_file(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """Output file must be a different path from the input timeline."""
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is True, (
            f"Expected ok=True, got error: {result.get('error')}"
        )
        # output must exist and be distinct from input
        assert output.exists()
        assert output.resolve() != simple_timeline_file.resolve()


# ===========================================================================
# Apply to ALL clips (clip_index=None)
# ===========================================================================


class TestApplyToAllClips:
    """When clip_index=None, all clips in V1 must receive the warp."""

    def test_all_clips_get_warp(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """All clips in V1 must have a clipwright LinearTimeWarp after set_speed."""
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=2.0, clip_index=None)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is True, f"Expected ok=True: {result.get('error')}"

        out_tl = otio.adapters.read_from_file(str(output))
        clips = _get_v1_clips(out_tl)
        assert len(clips) >= 1, "Expected at least one clip in V1"
        for clip in clips:
            warp = _get_clipwright_warp(clip)
            assert warp is not None, f"Clip {clip.name!r} missing clipwright warp"
            assert warp.time_scalar == pytest.approx(2.0)

    def test_applied_count_matches_all_clips(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """data.applied_count must equal the number of clips when clip_index=None."""
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=2.0, clip_index=None)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is True
        data = result.get("data", {})
        assert data.get("applied_count") == 2  # simple_timeline has 2 clips


# ===========================================================================
# Apply to SINGLE clip (clip_index=k, gap-aware)
# ===========================================================================


class TestApplyToSingleClip:
    """When clip_index=k, only the k-th clip (gap-excluded) must receive the warp."""

    def test_single_clip_warp_applied(
        self, gap_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """clip_index=1 must apply warp only to Clip1 (not Clip0 or Clip2).

        Gap timeline: [Clip0, Gap, Clip1, Clip2]
        clip_index=1 -> Clip1
        """
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=3.0, clip_index=1)
        result = set_speed(str(gap_timeline_file), str(output), opts)
        assert result["ok"] is True, f"Expected ok=True: {result.get('error')}"

        out_tl = otio.adapters.read_from_file(str(output))
        clips = _get_v1_clips(out_tl)
        assert len(clips) == 3  # [Clip0, Clip1, Clip2] (gap excluded)

        # Only Clip1 (index 1) should have clipwright warp
        assert _get_clipwright_warp(clips[0]) is None, "Clip0 should NOT have warp"
        warp1 = _get_clipwright_warp(clips[1])
        assert warp1 is not None, "Clip1 should have warp"
        assert warp1.time_scalar == pytest.approx(3.0)
        assert _get_clipwright_warp(clips[2]) is None, "Clip2 should NOT have warp"

    def test_single_clip_index_zero(
        self, gap_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """clip_index=0 must apply warp only to Clip0."""
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=0.5, clip_index=0)
        result = set_speed(str(gap_timeline_file), str(output), opts)
        assert result["ok"] is True

        out_tl = otio.adapters.read_from_file(str(output))
        clips = _get_v1_clips(out_tl)
        warp0 = _get_clipwright_warp(clips[0])
        assert warp0 is not None
        assert warp0.time_scalar == pytest.approx(0.5)
        assert _get_clipwright_warp(clips[1]) is None
        assert _get_clipwright_warp(clips[2]) is None


# ===========================================================================
# metadata["clipwright"] recorded
# ===========================================================================


class TestClipwrightMetadata:
    """metadata["clipwright"] must be set on each modified clip's LinearTimeWarp."""

    def test_metadata_tool_key(self, simple_timeline_file: Path, tmp_dir: Path) -> None:
        """metadata["clipwright"]["tool"] must be 'clipwright-speed'."""
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is True

        out_tl = otio.adapters.read_from_file(str(output))
        clips = _get_v1_clips(out_tl)
        for clip in clips:
            warp = _get_clipwright_warp(clip)
            assert warp is not None
            cw = dict(warp.metadata.get("clipwright", {}))
            assert cw.get("tool") == "clipwright-speed"

    def test_metadata_kind_key(self, simple_timeline_file: Path, tmp_dir: Path) -> None:
        """metadata["clipwright"]["kind"] must be 'speed'."""
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is True

        out_tl = otio.adapters.read_from_file(str(output))
        for clip in _get_v1_clips(out_tl):
            warp = _get_clipwright_warp(clip)
            assert warp is not None
            cw = dict(warp.metadata.get("clipwright", {}))
            assert cw.get("kind") == "speed"

    def test_metadata_speed_value(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """metadata["clipwright"]["speed"] must record the applied speed value."""
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=1.5)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is True

        out_tl = otio.adapters.read_from_file(str(output))
        for clip in _get_v1_clips(out_tl):
            warp = _get_clipwright_warp(clip)
            assert warp is not None
            cw = dict(warp.metadata.get("clipwright", {}))
            assert cw.get("speed") == pytest.approx(1.5)

    def test_metadata_version_present(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """metadata["clipwright"]["version"] must be present."""
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is True

        out_tl = otio.adapters.read_from_file(str(output))
        for clip in _get_v1_clips(out_tl):
            warp = _get_clipwright_warp(clip)
            assert warp is not None
            cw = dict(warp.metadata.get("clipwright", {}))
            assert "version" in cw


# ===========================================================================
# AC-4 IDEMPOTENCY
# ===========================================================================


class TestIdempotency:
    """Applying set_speed twice must not stack warps."""

    def test_apply_twice_same_speed_no_stacking(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """Applying the same speed twice results in exactly one clipwright warp."""
        output1 = tmp_dir / "out1.otio"
        output2 = tmp_dir / "out2.otio"
        opts = SetSpeedOptions(speed=2.0)

        r1 = set_speed(str(simple_timeline_file), str(output1), opts)
        assert r1["ok"] is True
        r2 = set_speed(str(output1), str(output2), opts)
        assert r2["ok"] is True

        out_tl = otio.adapters.read_from_file(str(output2))
        for clip in _get_v1_clips(out_tl):
            # Count only clipwright-speed warps
            cw_warps = [
                e
                for e in clip.effects
                if isinstance(e, otio.schema.LinearTimeWarp)
                and isinstance(e.metadata.get("clipwright"), collections.abc.Mapping)
                and e.metadata["clipwright"].get("tool") == "clipwright-speed"
            ]
            assert len(cw_warps) == 1, (
                f"Clip {clip.name!r}: expected 1 clipwright warp, got {len(cw_warps)}"
            )

    def test_apply_twice_different_speed_last_wins(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """Applying with different speeds: second speed replaces first (last wins)."""
        output1 = tmp_dir / "out1.otio"
        output2 = tmp_dir / "out2.otio"

        r1 = set_speed(
            str(simple_timeline_file), str(output1), SetSpeedOptions(speed=2.0)
        )
        assert r1["ok"] is True
        r2 = set_speed(str(output1), str(output2), SetSpeedOptions(speed=4.0))
        assert r2["ok"] is True

        out_tl = otio.adapters.read_from_file(str(output2))
        for clip in _get_v1_clips(out_tl):
            warp = _get_clipwright_warp(clip)
            assert warp is not None
            assert warp.time_scalar == pytest.approx(4.0), (
                "Second speed (4.0) must replace first (2.0)"
            )
            # No duplicate warp
            cw_warps = [
                e
                for e in clip.effects
                if isinstance(e, otio.schema.LinearTimeWarp)
                and isinstance(e.metadata.get("clipwright"), collections.abc.Mapping)
                and e.metadata["clipwright"].get("tool") == "clipwright-speed"
            ]
            assert len(cw_warps) == 1


# ===========================================================================
# R-3 FOREIGN WARP SURVIVES
# ===========================================================================


class TestForeignWarpSurvives:
    """Pre-existing non-clipwright LinearTimeWarp must be preserved."""

    def test_foreign_warp_preserved(self, tmp_dir: Path) -> None:
        """A LinearTimeWarp without clipwright metadata must survive set_speed."""
        # Build a timeline with a clip that already has a foreign warp
        tl = otio.schema.Timeline(name="foreign_warp_tl")
        v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
        tl.tracks.append(v1)
        tl.tracks.append(a1)

        ref = otio.schema.ExternalReference(target_url="file:///media/clip0.mp4")
        sr = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, 24.0),
            duration=otio.opentime.RationalTime(120.0, 24.0),
        )
        clip = otio.schema.Clip(name="clip0", media_reference=ref, source_range=sr)

        # Attach a foreign warp (no clipwright metadata)
        foreign_warp = otio.schema.LinearTimeWarp(time_scalar=0.75)
        # No metadata["clipwright"] set -> this is foreign
        clip.effects.append(foreign_warp)
        v1.append(clip)

        input_path = tmp_dir / "foreign.otio"
        otio.adapters.write_to_file(tl, str(input_path))

        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(input_path), str(output), opts)
        assert result["ok"] is True

        out_tl = otio.adapters.read_from_file(str(output))
        clips = _get_v1_clips(out_tl)
        assert len(clips) == 1

        effects = clips[0].effects
        # At least 2 effects: the foreign warp + the new clipwright warp
        assert len(effects) >= 2, (
            "Foreign warp must be preserved alongside clipwright warp"
        )

        # Verify foreign warp is still there
        foreign_warps = [
            e
            for e in effects
            if isinstance(e, otio.schema.LinearTimeWarp)
            and not (
                isinstance(e.metadata.get("clipwright"), collections.abc.Mapping)
                and e.metadata["clipwright"].get("tool") == "clipwright-speed"
            )
        ]
        assert len(foreign_warps) == 1, "Foreign LinearTimeWarp must be preserved"
        assert foreign_warps[0].time_scalar == pytest.approx(0.75)


# ===========================================================================
# R-7 ROUND-TRIP
# ===========================================================================


class TestRoundTrip:
    """Save and reload the output timeline; time_scalar must be intact."""

    def test_round_trip_time_scalar_intact(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """After save -> load_timeline -> time_scalar must match the input speed."""
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=3.5)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is True

        # Reload
        reloaded = otio.adapters.read_from_file(str(output))
        clips = _get_v1_clips(reloaded)
        for clip in clips:
            warp = _get_clipwright_warp(clip)
            assert warp is not None
            assert warp.time_scalar == pytest.approx(3.5), (
                "time_scalar must survive save -> load round-trip"
            )


# ===========================================================================
# speed=1.0 accepted (warp still attached)
# ===========================================================================


class TestSpeedOneAttachesWarp:
    """speed=1.0 is valid; a LinearTimeWarp with time_scalar=1.0 must be attached."""

    def test_speed_1_0_warp_attached(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """speed=1.0 must attach a warp with time_scalar=1.0 (no-op warp)."""
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=1.0)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is True

        out_tl = otio.adapters.read_from_file(str(output))
        for clip in _get_v1_clips(out_tl):
            warp = _get_clipwright_warp(clip)
            assert warp is not None, "speed=1.0 must still attach a clipwright warp"
            assert warp.time_scalar == pytest.approx(1.0)


# ===========================================================================
# Error cases
# ===========================================================================


class TestBadOutputExtension:
    """Bad output extension must return INVALID_INPUT."""

    def test_bad_extension_returns_invalid_input(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """output with .mp4 extension must return INVALID_INPUT."""
        output = tmp_dir / "out.mp4"
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "INVALID_INPUT"
        assert error.get("hint"), "hint must be non-empty"

    def test_no_extension_returns_invalid_input(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """output without extension must return INVALID_INPUT."""
        output = tmp_dir / "out_no_ext"
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "INVALID_INPUT"


class TestMissingOutputParent:
    """Missing output parent directory must return FILE_NOT_FOUND."""

    def test_missing_parent_returns_file_not_found(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """output path with non-existent parent must return FILE_NOT_FOUND."""
        output = tmp_dir / "nonexistent_dir" / "out.otio"
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "FILE_NOT_FOUND"
        assert error.get("hint"), "hint must be non-empty"


class TestOutputEqualsTimeline:
    """output == timeline path must return PATH_NOT_ALLOWED (impl-transform policy)."""

    def test_output_equals_timeline_returns_path_not_allowed(
        self, simple_timeline_file: Path
    ) -> None:
        """output path identical to timeline path must return PATH_NOT_ALLOWED.

        Policy updated in impl-transform: check_output_not_source raises
        PATH_NOT_ALLOWED (previously INVALID_INPUT).
        """
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(simple_timeline_file), str(simple_timeline_file), opts)
        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "PATH_NOT_ALLOWED"
        assert error.get("hint"), "hint must be non-empty"


class TestMissingTimeline:
    """Missing timeline file must return FILE_NOT_FOUND."""

    def test_missing_timeline_returns_file_not_found(self, tmp_dir: Path) -> None:
        """Non-existent timeline path must return FILE_NOT_FOUND."""
        timeline = tmp_dir / "does_not_exist.otio"
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(timeline), str(output), opts)
        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "FILE_NOT_FOUND"
        assert error.get("hint"), "hint must be non-empty"


class TestNoVideoTrack:
    """Timeline with no video track (or empty V1) must return UNSUPPORTED_OPERATION."""

    def test_no_video_track_returns_unsupported(
        self, audio_only_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """Audio-only timeline must return UNSUPPORTED_OPERATION."""
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(audio_only_timeline_file), str(output), opts)
        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "UNSUPPORTED_OPERATION"
        assert error.get("hint"), "hint must be non-empty"


class TestClipIndexOutOfRange:
    """clip_index exceeding clip count must return INVALID_INPUT with range hint."""

    def test_clip_index_out_of_range(
        self, gap_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """clip_index=99 on a 3-clip timeline must return INVALID_INPUT.

        The hint must contain '0-2' (0 to max_index) so AI knows the valid range.
        gap_timeline: [Clip0, Gap, Clip1, Clip2] -> clips at index 0,1,2
        """
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=2.0, clip_index=99)
        result = set_speed(str(gap_timeline_file), str(output), opts)
        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "INVALID_INPUT"
        hint = error.get("hint", "")
        assert hint, "hint must be non-empty"
        assert "0-2" in hint, f"Hint must include valid range '0-2', got: {hint!r}"

    def test_clip_index_exactly_clip_count_rejected(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """clip_index equal to clip count (out-of-range by 1) must return INVALID_INPUT.

        simple_timeline has 2 clips (indices 0,1); clip_index=2 is out of range.
        The hint must contain '0-1'.
        """
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=2.0, clip_index=2)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "INVALID_INPUT"
        hint = error.get("hint", "")
        assert "0-1" in hint, f"Hint must include valid range '0-1', got: {hint!r}"


class TestSpeedOutOfRange:
    """speed outside 0.25-8.0 must return INVALID_INPUT with 0.25-8.0 hint.

    Per decision OQ-1, this validation is done manually inside _set_speed_inner.
    The test asserts the error envelope shape (code + hint containing range).
    """

    def test_speed_too_low_returns_invalid_input(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """speed=0.24 (below 0.25) must return INVALID_INPUT with range hint."""
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=0.24)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "INVALID_INPUT"
        hint = error.get("hint", "")
        assert hint, "hint must be non-empty"
        assert "0.25" in hint and "8.0" in hint, (
            f"Hint must mention '0.25' and '8.0' range, got: {hint!r}"
        )

    def test_speed_too_high_returns_invalid_input(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """speed=8.01 (above 8.0) must return INVALID_INPUT with range hint."""
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=8.01)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "INVALID_INPUT"
        hint = error.get("hint", "")
        assert "0.25" in hint and "8.0" in hint, (
            f"Hint must mention '0.25' and '8.0' range, got: {hint!r}"
        )

    def test_speed_range_error_envelope_shape(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """Speed range error envelope must have ok=False, error.code, error.message, error.hint."""
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=0.1)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "INVALID_INPUT", "code must be INVALID_INPUT"
        assert error.get("message"), "message must be non-empty"
        assert error.get("hint"), "hint must be non-empty"
        # Hint must contain exact phrase per OQ-1
        assert "0.25-8.0" in error["hint"], (
            f"Hint must say 'Set speed within 0.25-8.0.' got: {error['hint']!r}"
        )


# ===========================================================================
# Success envelope shape
# ===========================================================================


class TestSuccessEnvelopeShape:
    """Success result must contain ok/summary/data{applied_count,speed,clip_indices}/artifacts."""

    def test_ok_is_true(self, simple_timeline_file: Path, tmp_dir: Path) -> None:
        """Success result ok must be True."""
        output = tmp_dir / "out.otio"
        result = set_speed(
            str(simple_timeline_file), str(output), SetSpeedOptions(speed=2.0)
        )
        assert result["ok"] is True

    def test_summary_is_non_empty(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """Success result summary must be non-empty."""
        output = tmp_dir / "out.otio"
        result = set_speed(
            str(simple_timeline_file), str(output), SetSpeedOptions(speed=2.0)
        )
        assert result["ok"] is True
        assert result.get("summary"), "summary must be non-empty"

    def test_data_applied_count(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """data.applied_count must be present and correct."""
        output = tmp_dir / "out.otio"
        result = set_speed(
            str(simple_timeline_file), str(output), SetSpeedOptions(speed=2.0)
        )
        assert result["ok"] is True
        data = result.get("data", {})
        assert "applied_count" in data, "data must contain applied_count"
        assert data["applied_count"] == 2  # simple_timeline has 2 clips

    def test_data_speed(self, simple_timeline_file: Path, tmp_dir: Path) -> None:
        """data.speed must match the requested speed."""
        output = tmp_dir / "out.otio"
        result = set_speed(
            str(simple_timeline_file), str(output), SetSpeedOptions(speed=1.5)
        )
        assert result["ok"] is True
        data = result.get("data", {})
        assert "speed" in data
        assert data["speed"] == pytest.approx(1.5)

    def test_data_clip_indices(self, simple_timeline_file: Path, tmp_dir: Path) -> None:
        """data.clip_indices must be a list of affected clip indices."""
        output = tmp_dir / "out.otio"
        result = set_speed(
            str(simple_timeline_file), str(output), SetSpeedOptions(speed=2.0)
        )
        assert result["ok"] is True
        data = result.get("data", {})
        assert "clip_indices" in data
        assert isinstance(data["clip_indices"], list)

    def test_artifacts_contains_timeline(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """artifacts must contain one entry with role='timeline' and format='otio'."""
        output = tmp_dir / "out.otio"
        result = set_speed(
            str(simple_timeline_file), str(output), SetSpeedOptions(speed=2.0)
        )
        assert result["ok"] is True
        artifacts = result.get("artifacts", [])
        assert len(artifacts) >= 1, "artifacts must contain at least one entry"
        tl_artifact = next(
            (
                a
                for a in artifacts
                if (a.get("role") if isinstance(a, dict) else getattr(a, "role", None))
                == "timeline"
            ),
            None,
        )
        assert tl_artifact is not None, "artifacts must contain a 'timeline' role entry"
        fmt = (
            tl_artifact.get("format")
            if isinstance(tl_artifact, dict)
            else getattr(tl_artifact, "format", None)
        )
        assert fmt == "otio", f"timeline artifact format must be 'otio', got {fmt!r}"


# ===========================================================================
# SR M-1 — full-path leak: missing output parent message must not contain paths
# ===========================================================================


class TestMissingOutputParentNoPathLeak:
    """SR M-1: FILE_NOT_FOUND message for missing parent must not expose the path.

    The message is a fixed sentence with no directory path or path separators.
    The hint may guide but must not embed the absolute parent path either.
    """

    def test_missing_parent_message_no_path_separator(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """error.message must not contain '/' or '\\' when output parent is missing."""
        output = tmp_dir / "nonexistent_dir" / "out.otio"
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "FILE_NOT_FOUND"
        message = error.get("message", "")
        assert "/" not in message, (
            f"message must not contain '/' (path leak), got: {message!r}"
        )
        assert "\\" not in message, (
            f"message must not contain '\\\\' (path leak), got: {message!r}"
        )

    def test_missing_parent_message_no_absolute_path(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """error.message must not contain the absolute parent directory path."""
        output = tmp_dir / "nonexistent_dir" / "out.otio"
        parent_str = str(output.parent)
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "FILE_NOT_FOUND"
        message = error.get("message", "")
        assert parent_str not in message, (
            f"message must not embed the absolute parent path, got: {message!r}"
        )
        # hint may guide, but must not embed the absolute path either
        hint = error.get("hint", "")
        assert parent_str not in hint, (
            f"hint must not embed the absolute parent path, got: {hint!r}"
        )


# ===========================================================================
# SR L-1 — suffix value leak: bad extension message must not expose the suffix
# ===========================================================================


class TestBadExtensionNoSuffixLeak:
    """SR L-1: INVALID_INPUT message for a bad extension must not expose the raw suffix."""

    def test_bad_extension_message_no_raw_suffix(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """error.message must not contain 'mp4' or '.mp4' when extension is wrong."""
        output = tmp_dir / "out.mp4"
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "INVALID_INPUT"
        message = error.get("message", "")
        assert "mp4" not in message.lower(), (
            f"message must not contain raw suffix 'mp4', got: {message!r}"
        )
        assert ".mp4" not in message.lower(), (
            f"message must not contain '.mp4', got: {message!r}"
        )

    def test_bad_extension_hint_may_mention_otio(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """hint may mention '.otio' as the correct extension (generic example)."""
        output = tmp_dir / "out.mp4"
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is False
        error = result.get("error") or {}
        hint = error.get("hint", "")
        assert hint, "hint must be non-empty"
        # hint should guide toward .otio; checking at least one keyword is present
        assert ".otio" in hint.lower() or "otio" in hint.lower(), (
            f"hint should mention '.otio' as the required extension, got: {hint!r}"
        )


# ===========================================================================
# Output placement: output may be in any directory (impl-transform policy)
# ===========================================================================


class TestOutputBoundaryCheck:
    """Output may reside in any directory whose parent exists (impl-transform).

    Co-location constraint removed in impl-transform.  Output outside the
    timeline directory is now allowed; only output==timeline is rejected.
    """

    def test_output_outside_timeline_dir_allowed(self, tmp_path: Path) -> None:
        """Output outside timeline directory must succeed (co-location removed).

        Policy updated in impl-transform: no boundary check; output may be
        placed in any directory, including outside the timeline's directory tree.

        Layout:
          tmp_path/proj/timeline.otio   <- timeline
          tmp_path/elsewhere/out.otio   <- output (outside proj/ — now allowed)
        """
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        elsewhere_dir = tmp_path / "elsewhere"
        elsewhere_dir.mkdir()

        # Build and write a simple timeline inside proj/
        tl = otio.schema.Timeline(name="boundary_tl")
        v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
        tl.tracks.append(v1)
        tl.tracks.append(a1)
        ref = otio.schema.ExternalReference(target_url="file:///media/clip.mp4")
        sr = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, 24.0),
            duration=otio.opentime.RationalTime(120.0, 24.0),
        )
        v1.append(otio.schema.Clip(name="clip0", media_reference=ref, source_range=sr))
        timeline_path = proj_dir / "timeline.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        output = elsewhere_dir / "out.otio"
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(timeline_path), str(output), opts)
        assert result["ok"] is True, (
            f"Output outside timeline dir must be allowed; got: {result.get('error')}"
        )

    def test_output_within_timeline_dir_allowed(self, tmp_path: Path) -> None:
        """Output within the timeline directory (same dir) must succeed normally."""
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()

        tl = otio.schema.Timeline(name="boundary_tl")
        v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
        tl.tracks.append(v1)
        tl.tracks.append(a1)
        ref = otio.schema.ExternalReference(target_url="file:///media/clip.mp4")
        sr = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, 24.0),
            duration=otio.opentime.RationalTime(120.0, 24.0),
        )
        v1.append(otio.schema.Clip(name="clip0", media_reference=ref, source_range=sr))
        timeline_path = proj_dir / "timeline.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        output = proj_dir / "output.otio"
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(timeline_path), str(output), opts)
        # Must NOT be rejected with PATH_NOT_ALLOWED
        error = result.get("error") or {}
        assert error.get("code") != "PATH_NOT_ALLOWED", (
            "Output within the timeline directory must be allowed"
        )
        assert result["ok"] is True, (
            f"Expected ok=True for valid same-dir output, got: {error}"
        )

    def test_output_in_subdir_of_timeline_dir_allowed(self, tmp_path: Path) -> None:
        """Output in a recursive subdirectory of the timeline directory must succeed."""
        proj_dir = tmp_path / "proj"
        sub_dir = proj_dir / "exports"
        proj_dir.mkdir()
        sub_dir.mkdir()

        tl = otio.schema.Timeline(name="boundary_tl")
        v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
        tl.tracks.append(v1)
        tl.tracks.append(a1)
        ref = otio.schema.ExternalReference(target_url="file:///media/clip.mp4")
        sr = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, 24.0),
            duration=otio.opentime.RationalTime(120.0, 24.0),
        )
        v1.append(otio.schema.Clip(name="clip0", media_reference=ref, source_range=sr))
        timeline_path = proj_dir / "timeline.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        output = sub_dir / "result.otio"
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(timeline_path), str(output), opts)
        error = result.get("error") or {}
        assert error.get("code") != "PATH_NOT_ALLOWED", (
            "Output in a subdirectory of the timeline directory must be allowed"
        )
        assert result["ok"] is True, (
            f"Expected ok=True for valid subdir output, got: {error}"
        )


# ===========================================================================
# SR L-2 — clip_index OOR: diagnostic numbers must be in hint only, not message
# ===========================================================================


class TestClipIndexOORMessageNoNumbers:
    """SR L-2: clip_index OOR must keep diagnostic numbers in hint only.

    message must NOT contain the numeric clip_index value or the clip count.
    hint MUST contain the valid range in '0-{max_index}' format.
    """

    def test_clip_index_oor_message_no_numeric_index(
        self, gap_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """message must not contain the out-of-range clip_index value (99).

        gap_timeline: [Clip0, Gap, Clip1, Clip2] -> 3 clips (indices 0-2).
        """
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=2.0, clip_index=99)
        result = set_speed(str(gap_timeline_file), str(output), opts)
        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "INVALID_INPUT"
        message = error.get("message", "")
        assert "99" not in message, (
            f"message must not contain the clip_index value 99, got: {message!r}"
        )
        # clip count (3) must also not appear in message
        assert "3" not in message, (
            f"message must not contain clip count '3', got: {message!r}"
        )

    def test_clip_index_oor_hint_contains_range(
        self, gap_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """hint must contain '0-2' range guidance for gap_timeline (3 clips)."""
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=2.0, clip_index=99)
        result = set_speed(str(gap_timeline_file), str(output), opts)
        assert result["ok"] is False
        error = result.get("error") or {}
        hint = error.get("hint", "")
        assert "0-2" in hint, f"hint must contain valid range '0-2', got: {hint!r}"

    def test_clip_index_oor_simple_message_no_numeric_index(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """message must not contain clip_index=2 or clip count=2 for simple timeline.

        simple_timeline: 2 clips (indices 0-1); clip_index=2 is out of range.
        """
        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=2.0, clip_index=2)
        result = set_speed(str(simple_timeline_file), str(output), opts)
        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "INVALID_INPUT"
        message = error.get("message", "")
        # message must not embed the clip_index (2) or clip count (2)
        assert "2" not in message, (
            f"message must not contain numeric clip info '2', got: {message!r}"
        )
        # hint must still contain the valid range
        hint = error.get("hint", "")
        assert "0-1" in hint, f"hint must contain valid range '0-1', got: {hint!r}"


# ===========================================================================
# CR L-5 — V1 present but no clips (Gap only) -> UNSUPPORTED_OPERATION
# ===========================================================================


class TestNoClipsInVideoTrack:
    """CR L-5: V1 video track exists but contains no clips (only Gap or empty).

    Distinct from the audio-only (no V1) case tested by TestNoVideoTrack.
    The 'No clips found in the video track.' branch must return UNSUPPORTED_OPERATION.
    """

    def test_no_clips_in_video_track(self, tmp_dir: Path) -> None:
        """V1 track with a Gap but no Clip must return UNSUPPORTED_OPERATION."""
        tl = otio.schema.Timeline(name="gap_only_tl")
        v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
        tl.tracks.append(v1)
        tl.tracks.append(a1)

        # V1 has only a Gap, no Clip
        gap_sr = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, 24.0),
            duration=otio.opentime.RationalTime(120.0, 24.0),
        )
        v1.append(otio.schema.Gap(source_range=gap_sr))

        a1.append(
            otio.schema.Clip(
                name="audio_clip",
                media_reference=otio.schema.ExternalReference(
                    target_url="file:///media/audio.wav"
                ),
                source_range=gap_sr,
            )
        )

        input_path = tmp_dir / "gap_only.otio"
        otio.adapters.write_to_file(tl, str(input_path))

        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(input_path), str(output), opts)

        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "UNSUPPORTED_OPERATION", (
            f"V1-present-but-no-clips must yield UNSUPPORTED_OPERATION, "
            f"got code={error.get('code')!r}"
        )
        assert error.get("hint"), "hint must be non-empty"

    def test_empty_video_track(self, tmp_dir: Path) -> None:
        """Completely empty V1 track (no items at all) must return UNSUPPORTED_OPERATION."""
        tl = otio.schema.Timeline(name="empty_v1_tl")
        v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
        tl.tracks.append(v1)
        tl.tracks.append(a1)
        # v1 is intentionally left empty (no items appended)

        a_sr = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, 24.0),
            duration=otio.opentime.RationalTime(120.0, 24.0),
        )
        a1.append(
            otio.schema.Clip(
                name="audio_clip",
                media_reference=otio.schema.ExternalReference(
                    target_url="file:///media/audio.wav"
                ),
                source_range=a_sr,
            )
        )

        input_path = tmp_dir / "empty_v1.otio"
        otio.adapters.write_to_file(tl, str(input_path))

        output = tmp_dir / "out.otio"
        opts = SetSpeedOptions(speed=2.0)
        result = set_speed(str(input_path), str(output), opts)

        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "UNSUPPORTED_OPERATION", (
            f"Empty V1 track must yield UNSUPPORTED_OPERATION, "
            f"got code={error.get('code')!r}"
        )
        assert error.get("hint"), "hint must be non-empty"
