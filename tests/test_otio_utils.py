"""test_otio_utils.py — Tests for otio_utils.py.

Target (§6 / §13.5):
- new_timeline: create tracks in [V1(kind=Video), A1(kind=Audio)] order
- load_timeline / save_timeline (atomic: temp → os.replace)
- add_clip / add_gap / add_marker
- set_clipwright_metadata / get_clipwright_metadata (under metadata["clipwright"])
- summarize_timeline:
    - always returns all items (clip_count, gap_count, marker_count, total_duration,
      markers)
    - total_duration = maximum of all track lengths (not the sum)
    - rate = V1 rate if V1 exists, else 1000.0
    - returns RationalTime(0, global rate) when there are no clips
"""

from __future__ import annotations

from pathlib import Path

import pytest

# --- Import ---
from clipwright.otio_utils import (
    add_clip,
    add_gap,
    add_marker,
    get_clipwright_metadata,
    load_timeline,
    new_timeline,
    save_timeline,
    set_clipwright_metadata,
    summarize_timeline,
)

# ===========================================================================
# new_timeline (§13.5 DC-AS-001 flat index / track order)
# ===========================================================================


class TestNewTimeline:
    """Contract for new_timeline track composition and types."""

    def test_returns_timeline(self) -> None:
        """Returns a Timeline object."""
        import opentimelineio as otio

        tl = new_timeline("test")
        assert isinstance(tl, otio.schema.Timeline)

    def test_timeline_name(self) -> None:
        """The name argument is stored in timeline.name."""
        tl = new_timeline("my_project")
        assert tl.name == "my_project"

    def test_has_two_tracks(self) -> None:
        """Two tracks: V1 and A1 (§13.5 DC-AS-001)."""
        tl = new_timeline("test")
        assert len(tl.tracks) == 2

    def test_track0_is_video(self) -> None:
        """track=0 (index 0) is kind=Video (V1) (§13.5 DC-AS-001)."""
        import opentimelineio as otio

        tl = new_timeline("test")
        assert tl.tracks[0].kind == otio.schema.TrackKind.Video

    def test_track1_is_audio(self) -> None:
        """track=1 (index 1) is kind=Audio (A1) (§13.5 DC-AS-001)."""
        import opentimelineio as otio

        tl = new_timeline("test")
        assert tl.tracks[1].kind == otio.schema.TrackKind.Audio

    def test_track_order_v1_before_a1(self) -> None:
        """Track order is [V1, A1]; Video comes first (§13.5 DC-AS-001)."""
        import opentimelineio as otio

        tl = new_timeline("test")
        assert tl.tracks[0].kind == otio.schema.TrackKind.Video
        assert tl.tracks[1].kind == otio.schema.TrackKind.Audio

    def test_track_names(self) -> None:
        """Tracks are named V1 and A1."""
        tl = new_timeline("test")
        assert tl.tracks[0].name == "V1"
        assert tl.tracks[1].name == "A1"

    def test_tracks_empty_initially(self) -> None:
        """Both tracks are empty immediately after creation."""
        tl = new_timeline("test")
        assert len(tl.tracks[0]) == 0
        assert len(tl.tracks[1]) == 0


# ===========================================================================
# load_timeline / save_timeline (atomic write)
# ===========================================================================


class TestLoadSaveTimeline:
    """I/O contract for load_timeline / save_timeline."""

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        """timeline.name is preserved through save → load."""
        tl = new_timeline("roundtrip")
        path = str(tmp_path / "timeline.otio")
        save_timeline(tl, path)
        loaded = load_timeline(path)
        assert loaded.name == "roundtrip"

    def test_saved_file_exists(self, tmp_path: Path) -> None:
        """save_timeline actually creates a file."""
        tl = new_timeline("check_file")
        path = str(tmp_path / "out.otio")
        save_timeline(tl, path)
        assert Path(path).is_file()

    def test_load_preserves_tracks(self, tmp_path: Path) -> None:
        """Loaded timeline preserves the track count."""
        tl = new_timeline("track_test")
        path = str(tmp_path / "track.otio")
        save_timeline(tl, path)
        loaded = load_timeline(path)
        assert len(loaded.tracks) == 2

    def test_atomic_write_no_temp_file_left(self, tmp_path: Path) -> None:
        """No temp file remains after save_timeline completes (atomic write)."""
        tl = new_timeline("atomic")
        path = tmp_path / "atomic.otio"
        save_timeline(tl, str(path))
        # No file with .tmp or similar extension should remain
        leftover = list(tmp_path.glob("*.tmp"))
        assert leftover == []

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        """An existing file can be overwritten (atomic os.replace)."""
        path = str(tmp_path / "overwrite.otio")
        tl1 = new_timeline("first")
        save_timeline(tl1, path)
        tl2 = new_timeline("second")
        save_timeline(tl2, path)
        loaded = load_timeline(path)
        assert loaded.name == "second"


# ===========================================================================
# add_clip (§6 otio_utils)
# ===========================================================================


class TestAddClip:
    """Contract for add_clip."""

    def test_adds_clip_to_track(self) -> None:
        """add_clip appends one clip to the track."""

        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("clip_test")
        track = tl.tracks[0]  # V1
        media = MediaRef(target_url="/path/to/video.mp4")
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=90.0, rate=30.0),
        )
        add_clip(track, media, source_range)
        assert len(track) == 1

    def test_added_clip_is_clip_type(self) -> None:
        """The added item is an OTIO Clip."""
        import opentimelineio as otio

        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("clip_type_test")
        track = tl.tracks[0]
        media = MediaRef(target_url="/path/to/video.mp4")
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=60.0, rate=30.0),
        )
        add_clip(track, media, source_range)
        assert isinstance(track[0], otio.schema.Clip)

    def test_clip_name_optional(self) -> None:
        """The name argument sets the clip name."""

        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("clip_name")
        track = tl.tracks[0]
        media = MediaRef(target_url="/path/to/video.mp4")
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=30.0, rate=30.0),
        )
        add_clip(track, media, source_range, name="intro")
        assert track[0].name == "intro"

    def test_clip_media_reference_url(self) -> None:
        """target_url is set in the clip's media_reference."""
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("url_test")
        track = tl.tracks[0]
        media = MediaRef(target_url="/path/to/clip.mp4")
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=30.0, rate=30.0),
        )
        add_clip(track, media, source_range)
        clip = track[0]
        assert clip.media_reference.target_url == "/path/to/clip.mp4"

    def test_clip_source_range_preserved(self) -> None:
        """The clip's source_range is set correctly from a TimeRangeModel."""
        import opentimelineio as otio

        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("range_test")
        track = tl.tracks[0]
        media = MediaRef(target_url="/video.mp4")
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=10.0, rate=30.0),
            duration=RationalTimeModel(value=60.0, rate=30.0),
        )
        add_clip(track, media, source_range)
        clip = track[0]
        assert clip.source_range.start_time == otio.opentime.RationalTime(
            value=10.0, rate=30.0
        )
        assert clip.source_range.duration == otio.opentime.RationalTime(
            value=60.0, rate=30.0
        )

    def test_available_range_set_when_media_ref_has_it(self) -> None:
        """ADR-4: media.available_range is wired into the clip's
        media_reference.available_range as a converted TimeRange."""
        import opentimelineio as otio

        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("available_range_test")
        track = tl.tracks[0]
        available_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=300.0, rate=30.0),
        )
        media = MediaRef(
            target_url="/path/to/video.mp4",
            available_range=available_range,
        )
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=10.0, rate=30.0),
            duration=RationalTimeModel(value=60.0, rate=30.0),
        )
        add_clip(track, media, source_range)
        clip = track[0]
        ref_available_range = clip.media_reference.available_range
        assert ref_available_range is not None
        assert ref_available_range.start_time == otio.opentime.RationalTime(
            value=0.0, rate=30.0
        )
        assert ref_available_range.duration == otio.opentime.RationalTime(
            value=300.0, rate=30.0
        )

    def test_available_range_none_when_media_ref_omits_it(self) -> None:
        """Backward compatibility: media.available_range=None leaves the
        ExternalReference.available_range unset (existing behaviour)."""
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("available_range_none_test")
        track = tl.tracks[0]
        media = MediaRef(target_url="/path/to/video.mp4")
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=60.0, rate=30.0),
        )
        add_clip(track, media, source_range)
        clip = track[0]
        assert clip.media_reference.available_range is None

    def test_available_range_independent_of_source_range(self) -> None:
        """available_range and source_range are set independently; their
        values need not match (available_range spans the whole source
        media while source_range is the trimmed-in portion)."""
        import opentimelineio as otio

        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("available_range_independent_test")
        track = tl.tracks[0]
        available_range = TimeRangeModel(
            start_time=RationalTimeModel(value=5.0, rate=25.0),
            duration=RationalTimeModel(value=1000.0, rate=25.0),
        )
        media = MediaRef(
            target_url="/path/to/video.mp4",
            available_range=available_range,
        )
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=100.0, rate=25.0),
            duration=RationalTimeModel(value=50.0, rate=25.0),
        )
        add_clip(track, media, source_range)
        clip = track[0]

        ref_available_range = clip.media_reference.available_range
        assert ref_available_range is not None
        assert ref_available_range.start_time == otio.opentime.RationalTime(
            value=5.0, rate=25.0
        )
        assert ref_available_range.duration == otio.opentime.RationalTime(
            value=1000.0, rate=25.0
        )
        assert ref_available_range.start_time != clip.source_range.start_time
        assert ref_available_range.duration != clip.source_range.duration
        assert clip.source_range.start_time == otio.opentime.RationalTime(
            value=100.0, rate=25.0
        )
        assert clip.source_range.duration == otio.opentime.RationalTime(
            value=50.0, rate=25.0
        )


# ===========================================================================
# add_gap (§6 otio_utils)
# ===========================================================================


class TestAddGap:
    """Contract for add_gap."""

    def test_adds_gap_to_track(self) -> None:
        """add_gap appends one gap to the track."""

        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("gap_test")
        track = tl.tracks[0]
        duration = RationalTimeModel(value=30.0, rate=30.0)
        add_gap(track, duration)
        assert len(track) == 1

    def test_added_item_is_gap_type(self) -> None:
        """The added item is an OTIO Gap."""
        import opentimelineio as otio

        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("gap_type")
        track = tl.tracks[0]
        duration = RationalTimeModel(value=30.0, rate=30.0)
        add_gap(track, duration)
        assert isinstance(track[0], otio.schema.Gap)

    def test_gap_duration_preserved(self) -> None:
        """The gap's duration is set correctly from a RationalTimeModel."""
        import opentimelineio as otio

        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("gap_dur")
        track = tl.tracks[0]
        duration = RationalTimeModel(value=15.0, rate=30.0)
        add_gap(track, duration)
        gap = track[0]
        assert gap.source_range.duration == otio.opentime.RationalTime(
            value=15.0, rate=30.0
        )


# ===========================================================================
# add_marker (§13.5 DC-GP-001 re: attach marker to the track itself)
# ===========================================================================


class TestAddMarker:
    """Contract for add_marker (§13.5 DC-GP-001 re)."""

    def test_adds_marker_to_track(self) -> None:
        """add_marker appends one marker to the track."""
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("marker_test")
        track = tl.tracks[0]  # V1
        marked_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=1.0, rate=30.0),
        )
        add_marker(track, marked_range, "chapter1")
        assert len(track.markers) == 1

    def test_marker_name_preserved(self) -> None:
        """The marker's name is set."""
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("marker_name")
        track = tl.tracks[0]
        marked_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=1.0, rate=30.0),
        )
        add_marker(track, marked_range, "intro_start")
        assert track.markers[0].name == "intro_start"

    def test_empty_track_add_marker_succeeds(self) -> None:
        """add_marker on an empty track succeeds (DC-GP-001 re); no clip required."""
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("empty_marker")
        track = tl.tracks[0]
        assert len(track) == 0  # no clips
        marked_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=1.0, rate=30.0),
        )
        add_marker(track, marked_range, "on_empty_track")
        assert len(track.markers) == 1

    def test_marker_color_optional(self) -> None:
        """The color argument sets the marker colour."""
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("marker_color")
        track = tl.tracks[0]
        marked_range = TimeRangeModel(
            start_time=RationalTimeModel(value=5.0, rate=30.0),
            duration=RationalTimeModel(value=1.0, rate=30.0),
        )
        add_marker(track, marked_range, "red_marker", color="RED")
        marker = track.markers[0]
        # colour must be set (OTIO Marker.color is treated as str)
        assert marker.color is not None

    def test_marker_marked_range_preserved(self) -> None:
        """The marker's marked_range is set correctly from a TimeRangeModel."""
        import opentimelineio as otio

        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("marker_range")
        track = tl.tracks[0]
        marked_range = TimeRangeModel(
            start_time=RationalTimeModel(value=10.0, rate=30.0),
            duration=RationalTimeModel(value=2.0, rate=30.0),
        )
        add_marker(track, marked_range, "range_check")
        m = track.markers[0]
        assert m.marked_range.start_time == otio.opentime.RationalTime(
            value=10.0, rate=30.0
        )

    def test_add_marker_to_audio_track(self) -> None:
        """A marker can be attached to the A1 (audio) track as well."""
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("audio_marker")
        audio_track = tl.tracks[1]  # A1
        marked_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=1.0, rate=30.0),
        )
        add_marker(audio_track, marked_range, "audio_cue")
        assert len(audio_track.markers) == 1


# ===========================================================================
# set_clipwright_metadata / get_clipwright_metadata (under metadata["clipwright"])
# ===========================================================================


class TestClipwrightMetadata:
    """Contract for set/get_clipwright_metadata (§4.3 convention)."""

    def test_set_and_get_roundtrip(self) -> None:
        """get retrieves the same dict that was set."""
        tl = new_timeline("meta_test")
        data = {"tool": "silence_detect", "version": "0.1.0"}
        set_clipwright_metadata(tl, data)
        result = get_clipwright_metadata(tl)
        assert result == data

    def test_stored_under_clipwright_key(self) -> None:
        """Metadata is stored under metadata["clipwright"] (§4.3 convention)."""
        tl = new_timeline("meta_key_test")
        set_clipwright_metadata(tl, {"kind": "analysis"})
        # Inspect the OTIO metadata dict directly
        assert "clipwright" in tl.metadata
        assert tl.metadata["clipwright"]["kind"] == "analysis"

    def test_get_returns_empty_dict_if_not_set(self) -> None:
        """get returns an empty dict when no metadata has been set."""
        tl = new_timeline("no_meta")
        result = get_clipwright_metadata(tl)
        assert result == {}

    def test_can_set_metadata_on_clip(self) -> None:
        """set/get work on Clip objects too."""

        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("clip_meta")
        track = tl.tracks[0]
        media = MediaRef(target_url="/v.mp4")
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=30.0, rate=30.0),
        )
        add_clip(track, media, source_range)
        clip = track[0]
        set_clipwright_metadata(clip, {"confidence": 0.95})
        assert get_clipwright_metadata(clip) == {"confidence": 0.95}

    def test_no_contamination_outside_clipwright_key(self) -> None:
        """Keys outside metadata["clipwright"] are not affected (§4.3)."""
        tl = new_timeline("no_contam")
        # Pre-set a key for another tool
        tl.metadata["other_tool"] = {"data": 42}
        set_clipwright_metadata(tl, {"tool": "test"})
        # other_tool must remain unchanged
        assert tl.metadata["other_tool"] == {"data": 42}


# ===========================================================================
# summarize_timeline (§13.5 DC-AM-001 re / DC-AM-002 re)
# ===========================================================================


class TestSummarizeTimeline:
    """Contract for summarize_timeline.

    Always returns all items (no truncation).
    total_duration = maximum of all track lengths (not the sum).
    rate = V1 rate if V1 has clips, else 1000.0.
    Returns RationalTime(0, global rate) when there are no clips.
    """

    def test_empty_timeline_counts(self) -> None:
        """Empty timeline has clip_count=0, gap_count=0, marker_count=0."""
        tl = new_timeline("empty")
        summary = summarize_timeline(tl)
        assert summary["clip_count"] == 0
        assert summary["gap_count"] == 0
        assert summary["marker_count"] == 0

    def test_empty_timeline_total_duration_is_zero(self) -> None:
        """Empty timeline total_duration has value=0 (§13.5 DC-AM-002 re)."""
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("empty_dur")
        summary = summarize_timeline(tl)
        dur = summary["total_duration"]
        assert isinstance(dur, RationalTimeModel)
        assert dur.value == 0.0

    def test_empty_timeline_duration_rate_is_video_rate(self) -> None:
        """V1 track present → total_duration rate is V1's rate (§13.5 DC-AM-002 re).

        When V1 exists but has no clips, the V1 rate cannot be determined,
        so 1000.0 is also acceptable. This is confirmed after implementation.
        """
        tl = new_timeline("rate_check")
        summary = summarize_timeline(tl)
        dur = summary["total_duration"]
        # When V1 has no clips, rate may be 1000.0 (implementation dependent)
        assert dur.rate > 0

    def test_required_keys_present(self) -> None:
        """summarize_timeline return value contains all required keys."""
        tl = new_timeline("keys_check")
        summary = summarize_timeline(tl)
        for key in (
            "clip_count",
            "gap_count",
            "marker_count",
            "total_duration",
            "markers",
        ):
            assert key in summary, f"Required key {key!r} is missing from the result"

    def test_clip_count_increments(self) -> None:
        """clip_count increments as clips are added."""
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("clip_count")
        track = tl.tracks[0]
        for i in range(3):
            media = MediaRef(target_url=f"/v{i}.mp4")
            source_range = TimeRangeModel(
                start_time=RationalTimeModel(value=float(i * 30), rate=30.0),
                duration=RationalTimeModel(value=30.0, rate=30.0),
            )
            add_clip(track, media, source_range)
        summary = summarize_timeline(tl)
        assert summary["clip_count"] == 3

    def test_gap_count_increments(self) -> None:
        """gap_count increments as gaps are added."""
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("gap_count")
        track = tl.tracks[0]
        add_gap(track, RationalTimeModel(value=30.0, rate=30.0))
        add_gap(track, RationalTimeModel(value=15.0, rate=30.0))
        summary = summarize_timeline(tl)
        assert summary["gap_count"] == 2

    def test_marker_count_increments(self) -> None:
        """marker_count increments as markers are added."""
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("marker_count")
        track = tl.tracks[0]
        for i in range(5):
            marked_range = TimeRangeModel(
                start_time=RationalTimeModel(value=float(i * 10), rate=30.0),
                duration=RationalTimeModel(value=1.0, rate=30.0),
            )
            add_marker(track, marked_range, f"cue_{i}")
        summary = summarize_timeline(tl)
        assert summary["marker_count"] == 5

    def test_markers_list_all_returned(self) -> None:
        """markers returns all items without truncation (§13.5 DC-AM-001 re)."""
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("marker_all")
        track = tl.tracks[0]
        # Add 60 markers, exceeding the threshold of 50
        for i in range(60):
            marked_range = TimeRangeModel(
                start_time=RationalTimeModel(value=float(i), rate=30.0),
                duration=RationalTimeModel(value=1.0, rate=30.0),
            )
            add_marker(track, marked_range, f"m{i}")
        summary = summarize_timeline(tl)
        assert len(summary["markers"]) == 60

    def test_markers_list_contains_name(self) -> None:
        """Each element in the markers list contains the 'name' key."""
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("marker_name_field")
        track = tl.tracks[0]
        marked_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=1.0, rate=30.0),
        )
        add_marker(track, marked_range, "chapter1")
        summary = summarize_timeline(tl)
        assert summary["markers"][0]["name"] == "chapter1"

    def test_total_duration_is_max_not_sum(self) -> None:
        """total_duration is the max of track lengths, not the sum (§13.5 DC-AM-002 re).

        V1 has 90 frames, A1 has 60 frames →
        total_duration.value == 90.0 (not the sum 150).
        """
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("dur_max")
        video_track = tl.tracks[0]  # V1
        audio_track = tl.tracks[1]  # A1

        # Add 90 frames of clip to V1
        media = MediaRef(target_url="/video.mp4")
        add_clip(
            video_track,
            media,
            TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=90.0, rate=30.0),
            ),
        )
        # Add 60 frames of gap to A1
        add_gap(audio_track, RationalTimeModel(value=60.0, rate=30.0))

        summary = summarize_timeline(tl)
        dur = summary["total_duration"]
        # Maximum is V1's 90.0 (not the sum 150.0)
        # In seconds at rate=30: 90/30 = 3.0s > 60/30 = 2.0s
        assert dur.value == pytest.approx(90.0, rel=1e-6)

    def test_total_duration_rate_from_video_track(self) -> None:
        """When V1 has clips, total_duration rate comes from V1 (§13.5 DC-AM-002 re)."""
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("rate_from_v1")
        video_track = tl.tracks[0]
        media = MediaRef(target_url="/video.mp4")
        add_clip(
            video_track,
            media,
            TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=24.0),
                duration=RationalTimeModel(value=72.0, rate=24.0),
            ),
        )
        summary = summarize_timeline(tl)
        dur = summary["total_duration"]
        assert dur.rate == pytest.approx(24.0, rel=1e-6)

    def test_total_duration_rate_1000_when_no_video(self) -> None:
        """When V1 is empty and only A1 has a gap, rate=1000.0 (§13.5 DC-AM-002 re)."""
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("audio_only")
        audio_track = tl.tracks[1]
        add_gap(audio_track, RationalTimeModel(value=1000.0, rate=1000.0))
        summary = summarize_timeline(tl)
        dur = summary["total_duration"]
        assert dur.rate == pytest.approx(1000.0, rel=1e-6)

    def test_summary_with_real_otio_roundtrip(self, tmp_path: Path) -> None:
        """summarize_timeline returns the same clip_count after save → load."""
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("io_summary")
        track = tl.tracks[0]
        media = MediaRef(target_url="/v.mp4")
        add_clip(
            track,
            media,
            TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=30.0, rate=30.0),
            ),
        )
        path = str(tmp_path / "io.otio")
        save_timeline(tl, path)
        loaded = load_timeline(path)
        summary = summarize_timeline(loaded)
        assert summary["clip_count"] == 1

    def test_marker_count_no_double_counting(self) -> None:
        """Track markers and clip markers are not double-counted (H-3).

        N markers on track + M markers on clip → marker_count == N+M.
        """
        import opentimelineio as otio

        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("no_double_count")
        track = tl.tracks[0]

        # Add one clip
        media = MediaRef(target_url="/v.mp4")
        clip = add_clip(
            track,
            media,
            TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=30.0, rate=30.0),
            ),
        )

        # Add 3 markers to the track
        n = 3
        for i in range(n):
            mr = TimeRangeModel(
                start_time=RationalTimeModel(value=float(i), rate=30.0),
                duration=RationalTimeModel(value=1.0, rate=30.0),
            )
            add_marker(track, mr, f"track_marker_{i}")

        # Add 2 markers to the clip
        m = 2
        for i in range(m):
            mr_clip = otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(float(i), 30.0),
                duration=otio.opentime.RationalTime(1.0, 30.0),
            )
            clip_marker = otio.schema.Marker(
                name=f"clip_marker_{i}", marked_range=mr_clip
            )
            clip.markers.append(clip_marker)

        summary = summarize_timeline(tl)
        assert summary["marker_count"] == n + m, (
            f"track {n} + clip {m} = {n + m} total (no duplicates)"
            f" (actual: {summary['marker_count']})"
        )


# ===========================================================================
# M-4: warnings key contract for summarize_timeline
# ===========================================================================


class TestSummarizeTimelineWarnings:
    """M-4: summarize_timeline return value includes a warnings key (list[str]).

    - Normal case: warnings == []
    - When _track_duration_sec fails with OTIO exception: at least one entry is recorded
    """

    def test_warnings_key_present_in_normal_case(self) -> None:
        """summarize_timeline return value contains the warnings key (M-4)."""
        tl = new_timeline("warn_normal")
        summary = summarize_timeline(tl)
        assert "warnings" in summary, (
            "summarize_timeline return value must have a 'warnings' key (M-4)"
        )

    def test_warnings_is_empty_list_in_normal_case(self) -> None:
        """Normal timeline has warnings == [] (M-4)."""
        tl = new_timeline("warn_empty")
        summary = summarize_timeline(tl)
        assert summary["warnings"] == [], (
            "warnings must be an empty list in the normal case (M-4)"
        )

    def test_warnings_is_list_type(self) -> None:
        """warnings is of type list[str] (M-4)."""
        tl = new_timeline("warn_type")
        summary = summarize_timeline(tl)
        assert isinstance(summary["warnings"], list), "warnings must be a list (M-4)"

    def test_required_keys_include_warnings(self) -> None:
        """summarize_timeline return keys include all expected + 'warnings' (M-4)."""
        tl = new_timeline("keys_with_warnings")
        summary = summarize_timeline(tl)
        expected_keys = {
            "clip_count",
            "gap_count",
            "marker_count",
            "total_duration",
            "markers",
            "warnings",
        }
        actual_keys = set(summary.keys())
        missing = expected_keys - actual_keys
        assert not missing, (
            f"summarize_timeline return value is missing keys: {missing} (M-4)"
        )

    def test_warnings_recorded_when_track_duration_raises_otio_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Warnings are recorded when _track_duration_sec raises OTIOError (M-4).

        monkeypatch induces track.duration() to raise OTIOError.
        _track_duration_sec catches only OTIO exceptions, returns 0.0, and
        summarize_timeline records at least one message in warnings.
        """
        import opentimelineio as otio

        tl = new_timeline("warn_otio_error")

        # monkeypatch track.duration() to raise OTIOError
        def _raise_otio_error(self: object) -> None:
            raise otio.exceptions.OTIOError("duration 取得失敗（テスト用）")

        monkeypatch.setattr(otio.schema.Track, "duration", _raise_otio_error)

        summary = summarize_timeline(tl)

        assert len(summary["warnings"]) >= 1, (
            "At least one warning must be recorded when an OTIO exception occurs (M-4)"
        )
        # NF-01: the warning must not include the raw OTIO internal error string
        assert "duration 取得失敗（テスト用）" not in summary["warnings"][0], (
            "warnings must not expose raw OTIO exception strings (NF-01)"
        )

    def test_warnings_with_clip_normal_case(self) -> None:
        """Timeline with clips also has warnings == [] in the normal case (M-4)."""
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("warn_with_clips")
        track = tl.tracks[0]
        media = MediaRef(target_url="/v.mp4")
        add_clip(
            track,
            media,
            TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=30.0, rate=30.0),
            ),
        )
        summary = summarize_timeline(tl)
        assert summary["warnings"] == []


# ===========================================================================
# L-1 / F-07: load_timeline assert → ClipwrightError replacement contract
# ===========================================================================


class TestLoadTimelineRaisesClipwrightErrorForNonTimeline:
    """L-1 / F-07: load_timeline raises ClipwrightError(OTIO_ERROR) for non-Timeline.

    assert isinstance(..., Timeline) is not safe in production (-O disables asserts).
    Pin the contract of replacing it with an explicit ClipwrightError.
    """

    def test_raises_clipwright_error_when_not_timeline(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """ClipwrightError(code=OTIO_ERROR) is raised when read_from_file returns
        a non-Timeline object (L-1 / F-07)."""
        import opentimelineio as otio

        from clipwright.errors import ClipwrightError, ErrorCode

        # monkeypatch read_from_file to return a Track instead of a Timeline
        monkeypatch.setattr(
            otio.adapters,
            "read_from_file",
            lambda path: otio.schema.Track(name="not_a_timeline"),
        )

        with pytest.raises(ClipwrightError) as exc_info:
            load_timeline(str(tmp_path / "dummy.otio"))

        assert exc_info.value.code == ErrorCode.OTIO_ERROR, (
            "ClipwrightError(code=OTIO_ERROR) must be raised for non-Timeline result"
            f" (actual: {exc_info.value.code}) (L-1 / F-07)"
        )

    def test_assert_error_not_raised_when_not_timeline(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """ClipwrightError is raised (not AssertionError) (L-1 / F-07).

        After replacing assert with ClipwrightError, confirms AssertionError is absent.
        """
        import opentimelineio as otio

        from clipwright.errors import ClipwrightError

        monkeypatch.setattr(
            otio.adapters,
            "read_from_file",
            lambda path: otio.schema.Track(name="not_a_timeline"),
        )

        with pytest.raises(ClipwrightError):
            load_timeline(str(tmp_path / "dummy.otio"))

        # ClipwrightError was raised = not AssertionError (implicit confirmation)

    def test_error_code_is_otio_error_for_none_result(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """ClipwrightError(OTIO_ERROR) is also raised when read_from_file returns None
        (L-1 / F-07)."""
        import opentimelineio as otio

        from clipwright.errors import ClipwrightError, ErrorCode

        monkeypatch.setattr(
            otio.adapters,
            "read_from_file",
            lambda path: None,  # None is also not a Timeline
        )

        with pytest.raises(ClipwrightError) as exc_info:
            load_timeline(str(tmp_path / "dummy.otio"))

        assert exc_info.value.code == ErrorCode.OTIO_ERROR


# ===========================================================================
# L-3: load_timeline responsibility to convert OTIO parser exceptions to ClipwrightError
# ===========================================================================


class TestLoadTimelineConvertsOTIOException:
    """L-3: load_timeline catches raw OTIO exceptions and converts them to
    ClipwrightError(OTIO_ERROR).

    server.py only needs to catch ClipwrightError; raw OTIO exceptions must not
    propagate to the server layer.
    """

    def test_otio_error_from_parser_is_converted_to_clipwright_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """read_from_file raises OTIOError → caller receives ClipwrightError (L-3)."""
        import opentimelineio as otio

        from clipwright.errors import ClipwrightError, ErrorCode

        def _raise_otio(path: str) -> None:
            raise otio.exceptions.OTIOError("parser error (test)")

        monkeypatch.setattr(otio.adapters, "read_from_file", _raise_otio)

        with pytest.raises(ClipwrightError) as exc_info:
            load_timeline(str(tmp_path / "bad.otio"))

        assert exc_info.value.code == ErrorCode.OTIO_ERROR, (
            "OTIO exception must be converted to ClipwrightError(code=OTIO_ERROR) (L-3)"
        )

    def test_raw_otio_exception_does_not_propagate(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Raw OTIOError does not reach the caller (L-3).

        A ClipwrightError is raised instead, so OTIOError never propagates.
        """
        import opentimelineio as otio

        from clipwright.errors import ClipwrightError

        def _raise_otio(path: str) -> None:
            raise otio.exceptions.OTIOError("raw OTIO exception (test)")

        monkeypatch.setattr(otio.adapters, "read_from_file", _raise_otio)

        # Confirm OTIOError does not pass through
        try:
            load_timeline(str(tmp_path / "bad.otio"))
            pytest.fail("No exception was raised (L-3)")
        except ClipwrightError:
            pass  # ClipwrightError arrived as expected
        except otio.exceptions.OTIOError:
            pytest.fail(
                "Raw OTIOError reached the caller. "
                "load_timeline must convert it to ClipwrightError (L-3)"
            )

    def test_clipwright_error_has_hint_on_otio_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Converted ClipwrightError includes a hint (L-3 / §6.4)."""
        import opentimelineio as otio

        from clipwright.errors import ClipwrightError

        def _raise_otio(path: str) -> None:
            raise otio.exceptions.OTIOError("hint check error (test)")

        monkeypatch.setattr(otio.adapters, "read_from_file", _raise_otio)

        with pytest.raises(ClipwrightError) as exc_info:
            load_timeline(str(tmp_path / "bad.otio"))

        assert exc_info.value.hint, (
            "ClipwrightError must have a hint set (§6.4 error contract) (L-3)"
        )


# ===========================================================================
# ADR-LT-1: load_timeline real-file failure modes (T-1..T-10)
# ===========================================================================


class TestLoadTimelineRealFileFailures:
    """ADR-LT-1: load_timeline converts real-file read/parse failures to
    ClipwrightError with the correct ErrorCode (FILE_NOT_FOUND / OTIO_ERROR),
    and never leaks directory components in the message (CWE-209).

    Uses real files written to disk (not monkeypatch) so the actual exception
    types raised by the OTIO adapter are exercised, per
    architecture-report-20260720-003853.md §3 layer 1 (T-1..T-10). The only
    monkeypatch-based cases are T-8/T-9, which pin exception types the OTIO
    adapter does not naturally raise from a real file on this platform.
    """

    _TRUNCATE_SENTINEL = object()

    @pytest.mark.parametrize(
        "case_id,content",
        [
            ("malformed_json", "{not valid json"),
            ("empty_file", ""),
            ("unknown_schema", '{"OTIO_SCHEMA": "NoSuchSchema.1"}'),
            ("missing_schema_key", '{"foo": 1}'),
            ("truncated_valid_file", _TRUNCATE_SENTINEL),
        ],
    )
    def test_broken_json_content_raises_otio_error(
        self, tmp_path: Path, case_id: str, content: object
    ) -> None:
        """T-1..T-5: malformed / empty / unknown-schema / schema-less /
        truncated JSON files raise ClipwrightError(OTIO_ERROR) (ADR-LT-1).

        T-5 truncates a real valid OTIO file (built via save_timeline) to its
        first half, so it is neither parseable JSON nor a complete schema.
        """
        from clipwright.errors import ClipwrightError, ErrorCode

        bad_path = tmp_path / f"{case_id}.otio"
        if content is self._TRUNCATE_SENTINEL:
            good_path = tmp_path / "source_for_truncation.otio"
            save_timeline(new_timeline("truncation-source"), str(good_path))
            full_text = good_path.read_text(encoding="utf-8")
            bad_path.write_text(full_text[: len(full_text) // 2], encoding="utf-8")
        else:
            assert isinstance(content, str)
            bad_path.write_text(content, encoding="utf-8")

        with pytest.raises(ClipwrightError) as exc_info:
            load_timeline(str(bad_path))

        assert exc_info.value.code == ErrorCode.OTIO_ERROR
        # T-10: message must not leak the directory component (CWE-209)
        assert str(tmp_path) not in exc_info.value.message
        assert exc_info.value.hint

    def test_non_timeline_schema_raises_otio_error_with_type_name(
        self, tmp_path: Path
    ) -> None:
        """T-6: a well-formed OTIO file whose root object is not a Timeline
        (a bare Clip) raises ClipwrightError(OTIO_ERROR) and the message
        names the offending type (existing isinstance-branch contract, L-3)."""
        import opentimelineio as otio

        from clipwright.errors import ClipwrightError, ErrorCode

        clip_path = tmp_path / "clip.otio"
        otio.adapters.write_to_file(otio.schema.Clip(name="lone-clip"), str(clip_path))

        with pytest.raises(ClipwrightError) as exc_info:
            load_timeline(str(clip_path))

        assert exc_info.value.code == ErrorCode.OTIO_ERROR
        assert "Clip" in exc_info.value.message

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        """T-7: a path with no file on disk raises
        ClipwrightError(FILE_NOT_FOUND), distinct from OTIO_ERROR (ADR-LT-1)."""
        from clipwright.errors import ClipwrightError, ErrorCode

        missing_path = tmp_path / "does_not_exist.otio"

        with pytest.raises(ClipwrightError) as exc_info:
            load_timeline(str(missing_path))

        assert exc_info.value.code == ErrorCode.FILE_NOT_FOUND
        # T-10: message must not leak the directory component (CWE-209)
        assert str(tmp_path) not in exc_info.value.message
        assert exc_info.value.hint

    def test_unexpected_exception_propagates_unconverted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """T-8: an exception type outside the enumerated catch list (a bare
        RuntimeError from the adapter) is not converted to ClipwrightError;
        it propagates unchanged so the server-layer INTERNAL boundary
        (ADR-LT-2) classifies it (ADR-LT-1 enumerated-catch contract, not a
        blanket `except Exception`)."""
        import opentimelineio as otio

        def _raise_runtime(path: str) -> None:
            raise RuntimeError("unexpected adapter failure (test)")

        monkeypatch.setattr(otio.adapters, "read_from_file", _raise_runtime)

        with pytest.raises(RuntimeError):
            load_timeline(str(tmp_path / "whatever.otio"))

    def test_permission_error_raises_otio_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """T-9: PermissionError (an OSError subclass) is converted to
        ClipwrightError(OTIO_ERROR) (ADR-LT-1). Injected via monkeypatch
        because permission-denied cannot be reliably reproduced with a real
        file on Windows."""
        import opentimelineio as otio

        from clipwright.errors import ClipwrightError, ErrorCode

        def _raise_permission(path: str) -> None:
            raise PermissionError("Permission denied (test)")

        monkeypatch.setattr(otio.adapters, "read_from_file", _raise_permission)

        with pytest.raises(ClipwrightError) as exc_info:
            load_timeline(str(tmp_path / "denied.otio"))

        assert exc_info.value.code == ErrorCode.OTIO_ERROR
        assert exc_info.value.hint


# ===========================================================================
# ADR-PB-1: load_timeline symlink rejection (pathpolicy-consistency batch,
# architecture-report-20260720-082027.md)
# ===========================================================================


def _probe_symlink_support() -> bool:
    """Return True when the runtime environment allows symlink creation.

    Executed once at module import (collection) time so pytest.mark.skipif
    can reference the result. Mirrors clipwright-bgm/tests/test_pathpolicy_bgm.py.
    """
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            real = base / "_probe_real.txt"
            real.write_bytes(b"probe")
            link = base / "_probe_link.txt"
            link.symlink_to(real)
        return True
    except OSError:
        return False


_SYMLINK_SUPPORTED: bool = _probe_symlink_support()
_SKIP_SYMLINK_REASON = (
    "Symlink creation requires elevated privileges on this system (WinError 1314)."
    " Enable Windows Developer Mode or run as Administrator."
)
_skip_no_symlinks = pytest.mark.skipif(
    not _SYMLINK_SUPPORTED,
    reason=_SKIP_SYMLINK_REASON,
)


def _try_symlink(link: Path, target: Path) -> None:
    """Create a symlink; skip the test if the OS refuses (Windows privilege)."""
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip(
            "Cannot create symlinks on this system (requires elevated privileges)"
        )


class TestLoadTimelineSymlinkRejection:
    """ADR-PB-1: load_timeline rejects a symlinked timeline path (leaf or
    intermediate directory component) with PATH_NOT_ALLOWED before ever
    attempting to read the file, and classifies a directory path as
    FILE_NOT_FOUND rather than OTIO_ERROR. This single core fix protects
    every caller that passes an unresolved timeline path to load_timeline
    (5 tool-layer-unguarded satellites plus server's project_dir path),
    without requiring per-satellite changes (G2 closure).

    Symlink-dependent cases (S-1/S-2) are probe-gated: SKIPPED on runtimes
    that cannot create symlinks (e.g. Windows without Developer Mode).
    S-4 involves no symlink and runs unconditionally on every OS.
    """

    @_skip_no_symlinks
    def test_leaf_symlink_timeline_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """S-1/S-3: a leaf symlink pointing at a valid .otio file is rejected
        with PATH_NOT_ALLOWED before the file is read. The message contains
        neither the tmp_path directory component nor the symlink's real
        target path (CWE-209), and hint is non-empty."""
        from clipwright.errors import ClipwrightError, ErrorCode

        real_path = tmp_path / "real.otio"
        save_timeline(new_timeline("real"), str(real_path))
        link_path = tmp_path / "link.otio"
        _try_symlink(link_path, real_path)

        with pytest.raises(ClipwrightError) as exc_info:
            load_timeline(str(link_path))

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED
        assert str(tmp_path) not in exc_info.value.message
        assert str(real_path) not in exc_info.value.message
        assert exc_info.value.hint

    @_skip_no_symlinks
    def test_intermediate_dir_symlink_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """S-2/S-3: a path whose intermediate directory component is a
        symlink to a real directory is rejected with PATH_NOT_ALLOWED,
        mirroring the leaf-symlink guard (ADR-PP-2 walks every path
        component, not just the leaf). Message leaks neither the tmp_path
        component nor the real directory path (CWE-209), hint is non-empty."""
        from clipwright.errors import ClipwrightError, ErrorCode

        real_dir = tmp_path / "real_dir"
        real_dir.mkdir()
        real_path = real_dir / "real.otio"
        save_timeline(new_timeline("real"), str(real_path))
        link_dir = tmp_path / "link_dir"
        _try_symlink(link_dir, real_dir)
        via_link_path = link_dir / "real.otio"

        with pytest.raises(ClipwrightError) as exc_info:
            load_timeline(str(via_link_path))

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED
        assert str(tmp_path) not in exc_info.value.message
        assert str(real_dir) not in exc_info.value.message
        assert exc_info.value.hint

    def test_directory_path_raises_file_not_found(self, tmp_path: Path) -> None:
        """S-4: passing a directory path to load_timeline raises
        ClipwrightError(FILE_NOT_FOUND), not OTIO_ERROR. This locks the
        intentional behaviour change from ADR-PB-1: the is_file() check now
        runs before read_from_file, so a directory is classified the same
        way satellites already treat a missing timeline (FILE_NOT_FOUND)
        instead of surfacing the prior IsADirectoryError -> OSError ->
        OTIO_ERROR path. No symlink is involved, so this runs
        unconditionally on every OS/environment."""
        from clipwright.errors import ClipwrightError, ErrorCode

        dir_path = tmp_path / "a_directory.otio"
        dir_path.mkdir()

        with pytest.raises(ClipwrightError) as exc_info:
            load_timeline(str(dir_path))

        assert exc_info.value.code == ErrorCode.FILE_NOT_FOUND
        assert str(tmp_path) not in exc_info.value.message
        assert exc_info.value.hint


# ===========================================================================
# L-5: partial update pattern for set_clipwright_metadata (prevent full key loss)
# ===========================================================================


class TestSetClipwrightMetadataPartialUpdate:
    """L-5: Regression tests pinning the get → update → set partial update pattern.

    Protects the usage contract documented in the set_clipwright_metadata docstring.
    Also confirms that a direct set (without get) replaces the entire clipwright dict.
    """

    def test_partial_update_preserves_existing_keys(self) -> None:
        """Existing keys are preserved with the get → merge → set pattern (L-5)."""
        tl = new_timeline("partial_update")

        # Set initial metadata
        initial_data = {"tool": "silence_detect", "version": "0.1.0"}
        set_clipwright_metadata(tl, initial_data)

        # Partial update: get → merge → set
        existing = get_clipwright_metadata(tl)
        existing["confidence"] = 0.95  # add a new key
        set_clipwright_metadata(tl, existing)

        result = get_clipwright_metadata(tl)

        # Existing keys must be preserved
        assert result["tool"] == "silence_detect", (
            "'tool' key must not be lost after partial update (L-5)"
        )
        assert result["version"] == "0.1.0", (
            "'version' key must not be lost after partial update (L-5)"
        )
        # New key must be set
        assert result["confidence"] == pytest.approx(0.95), (
            "'confidence' key added by partial update must be set (L-5)"
        )

    def test_direct_set_replaces_entire_clipwright_dict(self) -> None:
        """set_clipwright_metadata without get replaces the whole clipwright dict.

        (Inverse test documenting L-5 caution.)
        """
        tl = new_timeline("full_replace")

        # Initial setup
        set_clipwright_metadata(tl, {"tool": "silence_detect", "version": "0.1.0"})

        # Direct set replaces everything (intentional documented behaviour)
        set_clipwright_metadata(tl, {"confidence": 0.8})

        result = get_clipwright_metadata(tl)

        # tool / version are gone (direct set specification)
        assert "tool" not in result, (
            "Direct set replaces the entire dict — existing keys are gone (L-5 caution)"
        )
        # confidence is set
        assert result["confidence"] == pytest.approx(0.8)

    def test_partial_update_works_on_clip(self) -> None:
        """The partial update pattern also works on Clip objects (L-5)."""
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("clip_partial")
        track = tl.tracks[0]
        media = MediaRef(target_url="/v.mp4")
        clip = add_clip(
            track,
            media,
            TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=30.0, rate=30.0),
            ),
        )

        # Initial setup
        set_clipwright_metadata(clip, {"kind": "scene", "index": 1})

        # Partial update: get → merge → set
        existing = get_clipwright_metadata(clip)
        existing["label"] = "intro"
        set_clipwright_metadata(clip, existing)

        result = get_clipwright_metadata(clip)

        assert result["kind"] == "scene", (
            "'kind' must be preserved after partial update (L-5)"
        )
        assert result["index"] == 1, (
            "'index' must be preserved after partial update (L-5)"
        )
        assert result["label"] == "intro", (
            "'label' added by partial update must be set (L-5)"
        )

    def test_get_then_set_prevents_key_loss_after_multiple_updates(self) -> None:
        """All keys are preserved after multiple successive partial updates (L-5)."""
        tl = new_timeline("multi_update")

        set_clipwright_metadata(tl, {"a": 1})

        # First partial update
        data = get_clipwright_metadata(tl)
        data["b"] = 2
        set_clipwright_metadata(tl, data)

        # Second partial update
        data = get_clipwright_metadata(tl)
        data["c"] = 3
        set_clipwright_metadata(tl, data)

        result = get_clipwright_metadata(tl)

        assert result == {"a": 1, "b": 2, "c": 3}, (
            "All keys must be preserved after multiple partial updates (L-5)"
        )


# ===========================================================================
# get_markers — contract tests for the implemented get_markers function
# §2-1 architecture-report: get_markers(timeline, kind=None) -> list[Marker]
# ===========================================================================


class TestGetMarkers:
    """Contract tests for get_markers (implemented in otio_utils.py).

    Signature (§2-1):
        def get_markers(
            timeline: otio.schema.Timeline,
            kind: str | None = None,
        ) -> list[otio.schema.Marker]

    Verified observations:
    - kind=None: collect all markers from all tracks and all clips
    - kind="scene_boundary": return only markers where
      metadata["clipwright"]["kind"] == "scene_boundary"
    - both track markers and clip markers are collected
    - stable ordering: tracks in track order, clips in item order, markers in marker order
    - empty timeline returns []
    - markers without metadata["clipwright"] are excluded when kind is specified
    """

    # ------------------------------------------------------------------
    # GM-1: kind=None collects all markers from all tracks and all clips
    # ------------------------------------------------------------------

    def test_kind_none_returns_all_track_markers(self) -> None:
        """kind=None collects all markers attached to tracks (GM-1)."""
        import opentimelineio as otio

        from clipwright.otio_utils import get_markers

        tl = new_timeline("all_track_markers")
        track = tl.tracks[0]  # V1

        # Attach 3 markers directly to the track
        for i in range(3):
            mr = otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(float(i * 30), 30.0),
                duration=otio.opentime.RationalTime(1.0, 30.0),
            )
            track.markers.append(
                otio.schema.Marker(name=f"track_m{i}", marked_range=mr)
            )

        result = get_markers(tl, kind=None)
        assert len(result) == 3

    def test_kind_none_returns_all_clip_markers(self) -> None:
        """kind=None collects all markers attached to clips (GM-1)."""
        import opentimelineio as otio

        from clipwright.otio_utils import get_markers

        tl = new_timeline("all_clip_markers")
        track = tl.tracks[0]  # V1

        # Add one clip
        clip = otio.schema.Clip(
            name="clip0",
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0.0, 30.0),
                duration=otio.opentime.RationalTime(90.0, 30.0),
            ),
        )
        track.append(clip)

        # Attach 2 markers to the clip
        for i in range(2):
            mr = otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(float(i * 10), 30.0),
                duration=otio.opentime.RationalTime(1.0, 30.0),
            )
            clip.markers.append(otio.schema.Marker(name=f"clip_m{i}", marked_range=mr))

        result = get_markers(tl, kind=None)
        assert len(result) == 2

    def test_kind_none_combines_track_and_clip_markers(self) -> None:
        """kind=None collects track markers and clip markers together (GM-1)."""
        import opentimelineio as otio

        from clipwright.otio_utils import get_markers

        tl = new_timeline("combined_markers")
        track = tl.tracks[0]  # V1

        # Attach 1 marker to the track
        track.markers.append(
            otio.schema.Marker(
                name="on_track",
                marked_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0.0, 30.0),
                    duration=otio.opentime.RationalTime(1.0, 30.0),
                ),
            )
        )

        # Add a clip with 2 markers
        clip = otio.schema.Clip(
            name="clip0",
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0.0, 30.0),
                duration=otio.opentime.RationalTime(90.0, 30.0),
            ),
        )
        track.append(clip)
        for i in range(2):
            clip.markers.append(
                otio.schema.Marker(
                    name=f"on_clip_{i}",
                    marked_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(float(i), 30.0),
                        duration=otio.opentime.RationalTime(1.0, 30.0),
                    ),
                )
            )

        result = get_markers(tl, kind=None)
        assert len(result) == 3  # 1 track + 2 clip

    def test_kind_none_default_arg_same_as_explicit_none(self) -> None:
        """get_markers(tl) and get_markers(tl, kind=None) return the same result (GM-1)."""
        import opentimelineio as otio

        from clipwright.otio_utils import get_markers

        tl = new_timeline("default_arg")
        track = tl.tracks[0]
        track.markers.append(
            otio.schema.Marker(
                name="m",
                marked_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0.0, 30.0),
                    duration=otio.opentime.RationalTime(1.0, 30.0),
                ),
            )
        )

        result_default = get_markers(tl)
        result_explicit = get_markers(tl, kind=None)
        assert result_default == result_explicit

    # ------------------------------------------------------------------
    # GM-2: kind="scene_boundary" filters by metadata["clipwright"]["kind"]
    # ------------------------------------------------------------------

    def test_kind_filter_returns_only_matching_markers(self) -> None:
        """kind='scene_boundary' returns only markers with matching clipwright kind (GM-2)."""
        import opentimelineio as otio

        from clipwright.otio_utils import get_markers

        tl = new_timeline("kind_filter")
        track = tl.tracks[0]

        # Add a scene_boundary marker
        m_scene = otio.schema.Marker(
            name="scene1",
            marked_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0.0, 30.0),
                duration=otio.opentime.RationalTime(1.0, 30.0),
            ),
        )
        m_scene.metadata["clipwright"] = {"kind": "scene_boundary"}
        track.markers.append(m_scene)

        # Add a chapter marker (different kind)
        m_chapter = otio.schema.Marker(
            name="chapter1",
            marked_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(30.0, 30.0),
                duration=otio.opentime.RationalTime(1.0, 30.0),
            ),
        )
        m_chapter.metadata["clipwright"] = {"kind": "chapter"}
        track.markers.append(m_chapter)

        result = get_markers(tl, kind="scene_boundary")
        assert len(result) == 1
        assert result[0].name == "scene1"

    def test_kind_filter_excludes_non_matching_kind(self) -> None:
        """Markers with a different kind value are excluded when kind is specified (GM-2)."""
        import opentimelineio as otio

        from clipwright.otio_utils import get_markers

        tl = new_timeline("exclude_mismatch")
        track = tl.tracks[0]

        m = otio.schema.Marker(
            name="other_kind",
            marked_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0.0, 30.0),
                duration=otio.opentime.RationalTime(1.0, 30.0),
            ),
        )
        m.metadata["clipwright"] = {"kind": "chapter"}
        track.markers.append(m)

        result = get_markers(tl, kind="scene_boundary")
        assert len(result) == 0

    def test_kind_filter_works_on_clip_markers(self) -> None:
        """kind filter also applies to clip-attached markers (GM-2)."""
        import opentimelineio as otio

        from clipwright.otio_utils import get_markers

        tl = new_timeline("clip_kind_filter")
        track = tl.tracks[0]

        clip = otio.schema.Clip(
            name="clip0",
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0.0, 30.0),
                duration=otio.opentime.RationalTime(90.0, 30.0),
            ),
        )
        track.append(clip)

        # scene_boundary on the clip
        m_scene = otio.schema.Marker(
            name="scene_on_clip",
            marked_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(10.0, 30.0),
                duration=otio.opentime.RationalTime(1.0, 30.0),
            ),
        )
        m_scene.metadata["clipwright"] = {"kind": "scene_boundary"}
        clip.markers.append(m_scene)

        # chapter on the clip (should be excluded)
        m_chapter = otio.schema.Marker(
            name="chapter_on_clip",
            marked_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(20.0, 30.0),
                duration=otio.opentime.RationalTime(1.0, 30.0),
            ),
        )
        m_chapter.metadata["clipwright"] = {"kind": "chapter"}
        clip.markers.append(m_chapter)

        result = get_markers(tl, kind="scene_boundary")
        assert len(result) == 1
        assert result[0].name == "scene_on_clip"

    # ------------------------------------------------------------------
    # GM-3: both track markers and clip markers are collected
    # ------------------------------------------------------------------

    def test_collects_markers_from_audio_track(self) -> None:
        """Markers on the A1 (audio) track are also collected (GM-3)."""
        import opentimelineio as otio

        from clipwright.otio_utils import get_markers

        tl = new_timeline("audio_track_marker")
        audio_track = tl.tracks[1]  # A1

        audio_track.markers.append(
            otio.schema.Marker(
                name="audio_cue",
                marked_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0.0, 30.0),
                    duration=otio.opentime.RationalTime(1.0, 30.0),
                ),
            )
        )

        result = get_markers(tl, kind=None)
        assert len(result) == 1
        assert result[0].name == "audio_cue"

    def test_collects_markers_from_both_video_and_audio_tracks(self) -> None:
        """Markers on V1 and A1 are both collected (GM-3)."""
        import opentimelineio as otio

        from clipwright.otio_utils import get_markers

        tl = new_timeline("both_tracks")
        video_track = tl.tracks[0]
        audio_track = tl.tracks[1]

        video_track.markers.append(
            otio.schema.Marker(
                name="video_m",
                marked_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0.0, 30.0),
                    duration=otio.opentime.RationalTime(1.0, 30.0),
                ),
            )
        )
        audio_track.markers.append(
            otio.schema.Marker(
                name="audio_m",
                marked_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0.0, 30.0),
                    duration=otio.opentime.RationalTime(1.0, 30.0),
                ),
            )
        )

        result = get_markers(tl, kind=None)
        assert len(result) == 2

    # ------------------------------------------------------------------
    # GM-4: stable ordering — track order → clip item order → marker order
    # ------------------------------------------------------------------

    def test_stable_ordering_track_markers_before_clip_markers(self) -> None:
        """Track markers appear before clip markers for the same track (GM-4)."""
        import opentimelineio as otio

        from clipwright.otio_utils import get_markers

        tl = new_timeline("order_check")
        track = tl.tracks[0]

        # Track marker: should appear first
        track.markers.append(
            otio.schema.Marker(
                name="track_first",
                marked_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0.0, 30.0),
                    duration=otio.opentime.RationalTime(1.0, 30.0),
                ),
            )
        )

        # Clip marker: should appear after track marker
        clip = otio.schema.Clip(
            name="clip0",
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0.0, 30.0),
                duration=otio.opentime.RationalTime(60.0, 30.0),
            ),
        )
        track.append(clip)
        clip.markers.append(
            otio.schema.Marker(
                name="clip_second",
                marked_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(5.0, 30.0),
                    duration=otio.opentime.RationalTime(1.0, 30.0),
                ),
            )
        )

        result = get_markers(tl, kind=None)
        assert len(result) == 2
        assert result[0].name == "track_first"
        assert result[1].name == "clip_second"

    def test_stable_ordering_track_order_v1_before_a1(self) -> None:
        """V1 track markers appear before A1 track markers in the result (GM-4)."""
        import opentimelineio as otio

        from clipwright.otio_utils import get_markers

        tl = new_timeline("track_order")
        video_track = tl.tracks[0]  # V1 (index 0)
        audio_track = tl.tracks[1]  # A1 (index 1)

        video_track.markers.append(
            otio.schema.Marker(
                name="v1_marker",
                marked_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0.0, 30.0),
                    duration=otio.opentime.RationalTime(1.0, 30.0),
                ),
            )
        )
        audio_track.markers.append(
            otio.schema.Marker(
                name="a1_marker",
                marked_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0.0, 30.0),
                    duration=otio.opentime.RationalTime(1.0, 30.0),
                ),
            )
        )

        result = get_markers(tl, kind=None)
        assert len(result) == 2
        assert result[0].name == "v1_marker"
        assert result[1].name == "a1_marker"

    # ------------------------------------------------------------------
    # GM-5: empty timeline returns []
    # ------------------------------------------------------------------

    def test_empty_timeline_returns_empty_list(self) -> None:
        """Empty timeline (no tracks, no markers) returns [] (GM-5)."""
        from clipwright.otio_utils import get_markers

        tl = new_timeline("empty_tl")
        result = get_markers(tl, kind=None)
        assert result == []

    def test_timeline_with_tracks_but_no_markers_returns_empty(self) -> None:
        """Timeline with V1/A1 tracks but no markers returns [] (GM-5)."""
        from clipwright.otio_utils import get_markers

        tl = new_timeline("tracks_no_markers")
        result = get_markers(tl, kind=None)
        assert result == []

    def test_timeline_with_clips_but_no_markers_returns_empty(self) -> None:
        """Timeline with clips but no markers returns [] (GM-5)."""
        import opentimelineio as otio

        from clipwright.otio_utils import get_markers

        tl = new_timeline("clips_no_markers")
        track = tl.tracks[0]
        clip = otio.schema.Clip(
            name="clip0",
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0.0, 30.0),
                duration=otio.opentime.RationalTime(30.0, 30.0),
            ),
        )
        track.append(clip)

        result = get_markers(tl, kind=None)
        assert result == []

    # ------------------------------------------------------------------
    # GM-6: markers without metadata["clipwright"] are excluded when kind specified
    # ------------------------------------------------------------------

    def test_marker_without_clipwright_metadata_excluded_by_kind_filter(self) -> None:
        """Markers without metadata['clipwright'] are excluded when kind is set (GM-6)."""
        import opentimelineio as otio

        from clipwright.otio_utils import get_markers

        tl = new_timeline("no_meta_excluded")
        track = tl.tracks[0]

        # Marker with no clipwright metadata at all
        m_bare = otio.schema.Marker(
            name="bare_marker",
            marked_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0.0, 30.0),
                duration=otio.opentime.RationalTime(1.0, 30.0),
            ),
        )
        track.markers.append(m_bare)

        # This marker has clipwright metadata with the matching kind
        m_scene = otio.schema.Marker(
            name="scene_marker",
            marked_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(10.0, 30.0),
                duration=otio.opentime.RationalTime(1.0, 30.0),
            ),
        )
        m_scene.metadata["clipwright"] = {"kind": "scene_boundary"}
        track.markers.append(m_scene)

        result = get_markers(tl, kind="scene_boundary")
        assert len(result) == 1
        assert result[0].name == "scene_marker"

    def test_marker_without_clipwright_metadata_included_when_kind_none(self) -> None:
        """Markers without metadata['clipwright'] are included when kind=None (GM-6)."""
        import opentimelineio as otio

        from clipwright.otio_utils import get_markers

        tl = new_timeline("no_meta_included")
        track = tl.tracks[0]

        # Bare marker (no clipwright metadata)
        m_bare = otio.schema.Marker(
            name="bare_marker",
            marked_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0.0, 30.0),
                duration=otio.opentime.RationalTime(1.0, 30.0),
            ),
        )
        track.markers.append(m_bare)

        result = get_markers(tl, kind=None)
        assert len(result) == 1
        assert result[0].name == "bare_marker"

    # ------------------------------------------------------------------
    # GM-7: return type is list[otio.schema.Marker] (not dict)
    # ------------------------------------------------------------------

    def test_returns_marker_objects_not_dicts(self) -> None:
        """get_markers returns Marker objects, not summarized dicts (GM-7)."""
        import opentimelineio as otio

        from clipwright.otio_utils import get_markers

        tl = new_timeline("marker_objects")
        track = tl.tracks[0]
        mr = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, 30.0),
            duration=otio.opentime.RationalTime(1.0, 30.0),
        )
        track.markers.append(otio.schema.Marker(name="m0", marked_range=mr))

        result = get_markers(tl, kind=None)
        assert len(result) == 1
        assert isinstance(result[0], otio.schema.Marker)

    def test_marker_object_preserves_marked_range(self) -> None:
        """Returned Marker objects preserve the original marked_range (GM-7)."""
        import opentimelineio as otio

        from clipwright.otio_utils import get_markers

        tl = new_timeline("range_preserved")
        track = tl.tracks[0]

        expected_start = otio.opentime.RationalTime(15.0, 30.0)
        expected_duration = otio.opentime.RationalTime(3.0, 30.0)
        mr = otio.opentime.TimeRange(
            start_time=expected_start,
            duration=expected_duration,
        )
        track.markers.append(otio.schema.Marker(name="range_m", marked_range=mr))

        result = get_markers(tl, kind=None)
        assert len(result) == 1
        # Use RationalTime equality (no float approximation)
        assert result[0].marked_range.start_time == expected_start
        assert result[0].marked_range.duration == expected_duration

    # ------------------------------------------------------------------
    # GM-6 (補完): non-Mapping metadata["clipwright"] の型防衛
    # _marker_matches_kind は Mapping でない値を kind フィルタ時に除外する
    # ------------------------------------------------------------------

    def test_non_mapping_clipwright_metadata_excluded_by_kind_filter(self) -> None:
        """metadata['clipwright'] が Mapping でない場合、kind 指定時に除外される (GM-6).

        _marker_matches_kind は isinstance(..., Mapping) で型防衛しており、
        文字列・整数など非 Mapping 値が入ったマーカーは no-match として扱う。
        """
        import opentimelineio as otio

        from clipwright.otio_utils import get_markers

        tl = new_timeline("non_mapping_excluded")
        track = tl.tracks[0]

        # metadata["clipwright"] に非 Mapping 値（文字列）を設定したマーカー
        m_str = otio.schema.Marker(
            name="str_meta_marker",
            marked_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0.0, 30.0),
                duration=otio.opentime.RationalTime(1.0, 30.0),
            ),
        )
        m_str.metadata["clipwright"] = "scene_boundary"  # non-Mapping
        track.markers.append(m_str)

        # metadata["clipwright"] に非 Mapping 値（整数）を設定したマーカー
        m_int = otio.schema.Marker(
            name="int_meta_marker",
            marked_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(10.0, 30.0),
                duration=otio.opentime.RationalTime(1.0, 30.0),
            ),
        )
        m_int.metadata["clipwright"] = 42  # non-Mapping
        track.markers.append(m_int)

        # 正しい Mapping を持つマーカー（これだけが返るはず）
        m_valid = otio.schema.Marker(
            name="valid_marker",
            marked_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(20.0, 30.0),
                duration=otio.opentime.RationalTime(1.0, 30.0),
            ),
        )
        m_valid.metadata["clipwright"] = {"kind": "scene_boundary"}
        track.markers.append(m_valid)

        result = get_markers(tl, kind="scene_boundary")
        assert len(result) == 1, (
            "non-Mapping metadata['clipwright'] を持つマーカーは kind フィルタで除外される"
        )
        assert result[0].name == "valid_marker"

    def test_non_mapping_clipwright_metadata_included_when_kind_none(self) -> None:
        """metadata['clipwright'] が Mapping でなくても kind=None では含まれる (GM-6).

        kind=None はすべてのマーカーを返す。型防衛は kind 指定時のみ機能する。
        """
        import opentimelineio as otio

        from clipwright.otio_utils import get_markers

        tl = new_timeline("non_mapping_included")
        track = tl.tracks[0]

        # non-Mapping clipwright metadata
        m_str = otio.schema.Marker(
            name="non_mapping_m",
            marked_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0.0, 30.0),
                duration=otio.opentime.RationalTime(1.0, 30.0),
            ),
        )
        m_str.metadata["clipwright"] = "some_string"  # non-Mapping
        track.markers.append(m_str)

        result = get_markers(tl, kind=None)
        assert len(result) == 1, (
            "kind=None では non-Mapping metadata のマーカーも返される"
        )
        assert result[0].name == "non_mapping_m"
