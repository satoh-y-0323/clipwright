"""test_nle_sync.py -- Tests for clipwright-speed NLE mirror-sync.

TDD Red phase (architecture-report-20260725-100022.md SS2, ADR-MS-1~5):
speed._set_speed_inner today only touches the V1 (video) clip -- it strips
and re-appends a clipwright LinearTimeWarp on the target V1 clip(s) and never
looks at Audio-track items at all. Every test below documents the *target*
(post-implementation) contract; each fails today for one of two reasons:
  - mirror-sync tests: Audio-track mirror clips keep their pre-existing
    (empty) effects list untouched -- no clipwright LinearTimeWarp is ever
    found on them, because find_mirror_clips/Step 9 mirror-sync does not
    exist yet.
  - envelope tests: result.data has no "mirrored_audio_clips_updated" key at
    all (Step 11 does not build it yet), so any assertion on that key raises
    a KeyError-shaped AssertionError.

Target behaviors under test:
  - ADR-MS-2: applying speed to a V1 clip also strips+re-appends a matching
    clipwright LinearTimeWarp (same time_scalar) on every linked Audio mirror
    clip (via core find_mirror_clips / Resolve_OTIO Link Group ID). Mirror
    source_range/available_range are never rewritten.
  - ADR-MS-3: mirror clips never receive clip-level metadata["clipwright"]
    (effect-level metadata only) -- this assertion already holds true today
    (nothing touches mirrors yet), so it is a forward-looking pin, not a Red
    assertion.
  - ADR-MS-5: data["mirrored_audio_clips_updated"] is always present (int,
    0 when nothing was mirrored); summary gains a trailing sync sentence
    only when count > 0, and stays byte-identical to the existing 3-sentence
    form when count == 0.
  - Non-conform / bgm-degraded timelines: mirrored_audio_clips_updated == 0,
    no mutation of Audio-track items, no regression in pre-existing (non-NLE)
    behavior.
"""

from __future__ import annotations

import collections.abc
from pathlib import Path

import opentimelineio as otio
from clipwright.otio_utils import get_clipwright_metadata, load_timeline, save_timeline

from clipwright_speed.schemas import SetSpeedOptions
from clipwright_speed.speed import set_speed

# ---------------------------------------------------------------------------
# conftest imports (used by fixture injections)
# ---------------------------------------------------------------------------
# tmp_dir / simple_timeline_file / conformed_timeline_file /
# conformed_multistream_timeline_file / conformed_bgm_timeline_file are all
# defined in conftest.py.


# ===========================================================================
# Helpers
# ===========================================================================


def _audio_clips(tl: otio.schema.Timeline) -> list[otio.schema.Clip]:
    """Return every Clip item on every Audio-kind track, in track order."""
    clips: list[otio.schema.Clip] = []
    for track in tl.tracks:
        if track.kind != otio.schema.TrackKind.Audio:
            continue
        clips.extend(item for item in track if isinstance(item, otio.schema.Clip))
    return clips


def _audio_clips_by_track(tl: otio.schema.Timeline) -> list[list[otio.schema.Clip]]:
    """Return Clip items grouped per Audio-kind track, in track order."""
    grouped: list[list[otio.schema.Clip]] = []
    for track in tl.tracks:
        if track.kind != otio.schema.TrackKind.Audio:
            continue
        grouped.append([item for item in track if isinstance(item, otio.schema.Clip)])
    return grouped


def _clipwright_warps(clip: otio.schema.Clip) -> list[otio.schema.LinearTimeWarp]:
    """Return every clipwright-authored (kind=='speed') LinearTimeWarp on a clip."""
    warps: list[otio.schema.LinearTimeWarp] = []
    for effect in clip.effects:
        if not isinstance(effect, otio.schema.LinearTimeWarp):
            continue
        cw = get_clipwright_metadata(effect)
        if isinstance(cw, collections.abc.Mapping) and cw.get("kind") == "speed":
            warps.append(effect)
    return warps


# ===========================================================================
# ADR-MS-2: mirror-sync applies the matching warp to linked Audio mirrors
# ===========================================================================


class TestMirrorSyncAllClips:
    """Applying speed to all V1 clips must sync every linked Audio mirror clip."""

    def test_all_mirror_clips_get_single_warp_matching_scalar(
        self, conformed_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """Every Audio mirror clip gets exactly one clipwright warp, scalar==2.0."""
        tl_before = load_timeline(str(conformed_timeline_file))
        pre_ranges = [
            (clip.source_range, clip.media_reference.available_range)
            for clip in _audio_clips(tl_before)
        ]
        assert pre_ranges, "fixture must produce at least one audio mirror clip"

        output = tmp_dir / "out.otio"
        result = set_speed(
            str(conformed_timeline_file), str(output), SetSpeedOptions(speed=2.0)
        )
        assert result.model_dump()["ok"] is True

        tl_after = load_timeline(str(output))
        mirror_clips = _audio_clips(tl_after)
        assert len(mirror_clips) == len(pre_ranges)

        for clip, (before_sr, before_avail) in zip(
            mirror_clips, pre_ranges, strict=True
        ):
            warps = _clipwright_warps(clip)
            assert len(warps) == 1, (
                f"expected exactly 1 clipwright warp on mirror clip {clip.name!r}, "
                f"got {len(warps)}"
            )
            assert warps[0].time_scalar == 2.0

            assert clip.source_range.start_time == before_sr.start_time
            assert clip.source_range.duration == before_sr.duration
            if before_avail is not None:
                avail = clip.media_reference.available_range
                assert avail is not None
                assert avail.start_time == before_avail.start_time
                assert avail.duration == before_avail.duration


class TestMirrorSyncMultistream:
    """A1 and A2 mirror tracks must both receive the matching warp."""

    def test_a1_and_a2_mirrors_both_get_warp(
        self, conformed_multistream_timeline_file: Path, tmp_dir: Path
    ) -> None:
        output = tmp_dir / "out.otio"
        result = set_speed(
            str(conformed_multistream_timeline_file),
            str(output),
            SetSpeedOptions(speed=2.0),
        )
        assert result.model_dump()["ok"] is True

        tl_after = load_timeline(str(output))
        tracks = _audio_clips_by_track(tl_after)
        assert len(tracks) >= 2, "fixture must produce A1 and A2 mirror tracks"
        for track_clips in tracks[:2]:
            assert track_clips, "each mirror track must have at least one clip"
            for clip in track_clips:
                warps = _clipwright_warps(clip)
                assert len(warps) == 1
                assert warps[0].time_scalar == 2.0


class TestMirrorSyncSingleClipIndex:
    """clip_index=0 must only sync the mirror linked to that V1 clip."""

    def test_only_target_clip_mirror_gets_warp(
        self, conformed_timeline_file: Path, tmp_dir: Path
    ) -> None:
        output = tmp_dir / "out.otio"
        result = set_speed(
            str(conformed_timeline_file),
            str(output),
            SetSpeedOptions(speed=2.0, clip_index=0),
        )
        assert result.model_dump()["ok"] is True

        tl_after = load_timeline(str(output))
        mirror_clips = _audio_clips(tl_after)
        assert len(mirror_clips) == 2, "fixture has one mirror per V1 clip (clip0/1)"

        target_mirror, other_mirror = mirror_clips[0], mirror_clips[1]
        target_warps = _clipwright_warps(target_mirror)
        assert len(target_warps) == 1
        assert target_warps[0].time_scalar == 2.0
        assert len(other_mirror.effects) == 0, (
            "the mirror linked to the untouched V1 clip must have no effects"
        )


class TestMirrorSyncIdempotent:
    """Reapplying speed must replace, not stack, the mirror's clipwright warp."""

    def test_reapplying_speed_replaces_mirror_warp_not_stacks(
        self, conformed_timeline_file: Path, tmp_dir: Path
    ) -> None:
        first = tmp_dir / "first.otio"
        result1 = set_speed(
            str(conformed_timeline_file), str(first), SetSpeedOptions(speed=2.0)
        )
        assert result1.model_dump()["ok"] is True

        second = tmp_dir / "second.otio"
        result2 = set_speed(str(first), str(second), SetSpeedOptions(speed=0.5))
        assert result2.model_dump()["ok"] is True

        tl_after = load_timeline(str(second))
        mirror_clips = _audio_clips(tl_after)
        assert mirror_clips
        for clip in mirror_clips:
            warps = _clipwright_warps(clip)
            assert len(warps) == 1, "reapplying speed must not stack mirror warps"
            assert warps[0].time_scalar == 0.5


class TestMirrorSyncForeignWarpPreserved:
    """A pre-existing non-clipwright LinearTimeWarp on a mirror must survive (R-3)."""

    def test_foreign_warp_on_mirror_survives_alongside_clipwright_warp(
        self, conformed_timeline_file: Path, tmp_dir: Path
    ) -> None:
        tl = load_timeline(str(conformed_timeline_file))
        mirror_clips = _audio_clips(tl)
        assert mirror_clips
        foreign = otio.schema.LinearTimeWarp(name="external_warp", time_scalar=1.5)
        mirror_clips[0].effects.append(foreign)

        seeded = tmp_dir / "seeded.otio"
        save_timeline(tl, str(seeded))

        output = tmp_dir / "out.otio"
        result = set_speed(str(seeded), str(output), SetSpeedOptions(speed=2.0))
        assert result.model_dump()["ok"] is True

        tl_after = load_timeline(str(output))
        target_mirror = _audio_clips(tl_after)[0]
        foreign_effects = [
            e
            for e in target_mirror.effects
            if isinstance(e, otio.schema.LinearTimeWarp) and e.name == "external_warp"
        ]
        assert len(foreign_effects) == 1, "foreign warp on mirror must survive (R-3)"
        assert len(_clipwright_warps(target_mirror)) == 1


# ===========================================================================
# Degraded / non-conform paths: zero mirrors, no regression
# ===========================================================================


class TestMirrorSyncBgmDegraded:
    """A pre-existing non-mirroring (bgm) Audio track must never be touched."""

    def test_bgm_track_not_mirrored_zero_updates(
        self, conformed_bgm_timeline_file: Path, tmp_dir: Path
    ) -> None:
        output = tmp_dir / "out.otio"
        result = set_speed(
            str(conformed_bgm_timeline_file), str(output), SetSpeedOptions(speed=2.0)
        )
        data = result.model_dump()
        assert data["ok"] is True
        assert data["data"].get("mirrored_audio_clips_updated") == 0

        tl_after = load_timeline(str(output))
        bgm_clips = _audio_clips(tl_after)
        assert bgm_clips, "fixture must retain the pre-existing bgm clip"
        for clip in bgm_clips:
            assert len(clip.effects) == 0
            cw = get_clipwright_metadata(clip)
            assert cw.get("kind") == "bgm", "pre-existing bgm annotation must survive"
            assert cw.get("tool") != "clipwright-speed"


class TestNonConformRegressionPin:
    """A non-conform timeline (no Resolve_OTIO marker) must behave as before."""

    def test_non_conform_timeline_zero_mirror_updates_no_resolve_otio(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        output = tmp_dir / "out.otio"
        result = set_speed(
            str(simple_timeline_file), str(output), SetSpeedOptions(speed=2.0)
        )
        data = result.model_dump()
        assert data["ok"] is True
        assert data["data"].get("mirrored_audio_clips_updated") == 0

        tl_after = load_timeline(str(output))
        assert "Resolve_OTIO" not in tl_after.metadata
        for track in tl_after.tracks:
            if track.kind != otio.schema.TrackKind.Audio:
                continue
            for clip in track:
                if isinstance(clip, otio.schema.Clip):
                    assert len(clip.effects) == 0


# ===========================================================================
# ADR-MS-3: no clip-level clipwright metadata on mirrors (forward-looking pin)
# ===========================================================================


class TestAdrMs3NoClipLevelMetadataOnMirror:
    """Mirror clips must never carry clip-level metadata["clipwright"].

    This assertion already holds true today (nothing touches mirrors yet in
    the current implementation), so unlike the mirror-sync tests above this
    one is a forward-looking regression pin rather than a Red assertion: it
    guards the future mirror-sync implementation against accidentally
    stamping clip-level metadata onto a mirror (ADR-MS-3 explicitly forbids
    this; only effect-level metadata is allowed).
    """

    def test_mirror_clip_has_no_clip_level_clipwright_metadata(
        self, conformed_timeline_file: Path, tmp_dir: Path
    ) -> None:
        output = tmp_dir / "out.otio"
        result = set_speed(
            str(conformed_timeline_file), str(output), SetSpeedOptions(speed=2.0)
        )
        assert result.model_dump()["ok"] is True

        tl_after = load_timeline(str(output))
        for clip in _audio_clips(tl_after):
            assert "clipwright" not in clip.metadata, (
                "ADR-MS-3: mirror clips must carry effect-level clipwright "
                "metadata only, never clip-level metadata"
            )


# ===========================================================================
# ADR-MS-5: envelope additive contract
# ===========================================================================


class TestEnvelopeMirroredAudioClipsUpdated:
    """data["mirrored_audio_clips_updated"] must always be present (ADR-MS-5)."""

    def test_data_always_has_mirrored_audio_clips_updated_int(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        output = tmp_dir / "out.otio"
        result = set_speed(
            str(simple_timeline_file), str(output), SetSpeedOptions(speed=2.0)
        )
        data = result.model_dump()
        assert data["ok"] is True
        assert "mirrored_audio_clips_updated" in data["data"]
        assert isinstance(data["data"]["mirrored_audio_clips_updated"], int)

    def test_summary_includes_sync_sentence_when_count_positive(
        self, conformed_timeline_file: Path, tmp_dir: Path
    ) -> None:
        output = tmp_dir / "out.otio"
        result = set_speed(
            str(conformed_timeline_file), str(output), SetSpeedOptions(speed=2.0)
        )
        data = result.model_dump()
        assert data["ok"] is True
        assert data["data"]["mirrored_audio_clips_updated"] > 0
        summary = data["summary"] or ""
        assert "mirror" in summary.lower() or "sync" in summary.lower(), (
            "summary must gain a trailing NLE-sync sentence when count > 0"
        )

    def test_summary_unchanged_when_count_zero(
        self, simple_timeline_file: Path, tmp_dir: Path
    ) -> None:
        """count==0 must leave the existing 3-sentence summary byte-identical."""
        output = tmp_dir / "out.otio"
        result = set_speed(
            str(simple_timeline_file), str(output), SetSpeedOptions(speed=2.0)
        )
        data = result.model_dump()
        assert data["ok"] is True
        assert data["data"]["mirrored_audio_clips_updated"] == 0
        expected = (
            f"Applied speed 2.0x to 2 clip(s). Output: {output.name}. "
            f"Estimated rendered duration scales by 1/2.0."
        )
        assert data["summary"] == expected, (
            "count==0 must not append a 4th (sync) sentence to the existing summary"
        )
