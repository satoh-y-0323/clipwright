"""test_available_range.py — TDD Red for BGM clip ExternalReference.available_range wiring.

Context (GitHub Issue #1 / architecture-report ADR-4, plan-report task test-bgm):
  bgm is a full-length tool (inline ExternalReference construction, Pattern B).
  available_range may equal source_range (both are the full BGM media length,
  0..bgm_duration). Currently bgm.py builds the ExternalReference without
  available_range, so it stays None and every assertion below fails (Red).
  Only the newly-added BGM clip (kind=='bgm') is in scope; bgm is an
  accumulate-style tool so pre-existing V1/A1 clips are untouched.

Mocking policy mirrors test_bgm.py: inspect_media is monkeypatched so no real
ffprobe subprocess is invoked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
from clipwright.otio_utils import load_timeline, save_timeline

from clipwright_bgm.bgm import add_bgm
from clipwright_bgm.schemas import BgmOptions

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


def _get_bgm_clips(tl: otio.schema.Timeline) -> list[otio.schema.Clip]:
    """Collect and return all newly-added Clips with kind=='bgm' from the timeline."""
    bgm_clips = []
    for track in tl.tracks:
        if track.kind == otio.schema.TrackKind.Audio:
            for item in track:
                if isinstance(item, otio.schema.Clip):
                    meta = item.metadata.get("clipwright", {})
                    if meta.get("kind") == "bgm":
                        bgm_clips.append(item)
    return bgm_clips


def _add_bgm_and_get_clip(
    tmp_timeline_dir: Path,
    bgm_audio_file: Path,
    media_info_bgm: Any,
) -> otio.schema.Clip:
    """Run add_bgm against a fresh simple timeline and return the placed BGM clip."""
    tl = _make_simple_timeline()
    timeline_path = tmp_timeline_dir / "timeline.otio"
    output_path = tmp_timeline_dir / "output.otio"
    save_timeline(tl, str(timeline_path))

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
    assert len(bgm_clips) == 1
    return bgm_clips[0]


# ===========================================================================
# Test scope: available_range wiring on the newly-added BGM clip (ADR-4)
# ===========================================================================


class TestBgmAvailableRange:
    """available_range must be wired on the BGM clip's ExternalReference (full-length)."""

    def test_available_range_is_set(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """The BGM clip's ExternalReference.available_range must not be None."""
        clip = _add_bgm_and_get_clip(tmp_timeline_dir, bgm_audio_file, media_info_bgm)

        ref = clip.media_reference
        assert ref.available_range is not None, (
            "ExternalReference.available_range must be set for the BGM clip "
            "(ADR-4, full-length pattern)"
        )

    def test_available_range_equals_full_bgm_duration(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """available_range must equal the full BGM media length (same as source_range)."""
        clip = _add_bgm_and_get_clip(tmp_timeline_dir, bgm_audio_file, media_info_bgm)

        ref = clip.media_reference
        assert ref.available_range is not None

        rate = media_info_bgm.duration.rate
        expected = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, rate),
            duration=otio.opentime.RationalTime(media_info_bgm.duration.value, rate),
        )
        assert ref.available_range == expected, (
            "available_range must be the full BGM duration (full-length pattern, "
            "may equal source_range)"
        )

    def test_source_range_is_subset_of_available_range(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """source_range must fit within available_range (source_range subseteq available_range)."""
        clip = _add_bgm_and_get_clip(tmp_timeline_dir, bgm_audio_file, media_info_bgm)

        ref = clip.media_reference
        assert ref.available_range is not None

        source_range = clip.source_range
        assert source_range is not None

        source_end = source_range.start_time + source_range.duration
        available_end = ref.available_range.start_time + ref.available_range.duration

        assert source_range.start_time >= ref.available_range.start_time
        assert source_end <= available_end
