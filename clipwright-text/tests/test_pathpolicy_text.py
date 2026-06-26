"""test_pathpolicy_text.py — Red-phase tests for text output-placement policy update.

Policy change (impl-transform target): the co-location constraint is removed from
add_text.  After impl-transform, output may be placed in any directory provided:
  - parent directory exists
  - output extension is .otio
  - output path does not resolve to the same file as the input timeline

Red state (before impl-transform):
  - text.py _check_output_within_timeline_dir (L60) raises PATH_NOT_ALLOWED when
    output is in a different directory than the timeline.
  - output == timeline currently raises INVALID_INPUT, not PATH_NOT_ALLOWED.

Test groups:
  A. output in different directory from timeline → ok=True (new policy)
  B. output == timeline → PATH_NOT_ALLOWED (error code change from INVALID_INPUT)
  C. DC-AM-003: mixed relative/absolute media refs preserved after round-trip
     when output resides outside the timeline directory
  D. preserved checks: .otio extension, parent dir existence, missing timeline
     (regression guards; expected Green)
"""

from __future__ import annotations

import collections.abc
from pathlib import Path

import opentimelineio as otio

from clipwright_text.schemas import AddTextOptions
from clipwright_text.text import add_text

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RATE = 24.0
_DURATION_SEC = 10.0


def _make_clip(
    name: str,
    duration_sec: float = _DURATION_SEC,
    target_url: str | None = None,
) -> otio.schema.Clip:
    url = target_url if target_url is not None else f"file:///media/{name}.mp4"
    ref = otio.schema.ExternalReference(target_url=url)
    sr = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, _RATE),
        duration=otio.opentime.RationalTime(duration_sec * _RATE, _RATE),
    )
    return otio.schema.Clip(name=name, media_reference=ref, source_range=sr)


def _make_v1_timeline(n_clips: int = 2) -> otio.schema.Timeline:
    """Build a minimal timeline with a V1 video track and n_clips clips."""
    tl = otio.schema.Timeline(name="text_tl")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(v1)
    tl.tracks.append(a1)
    for i in range(n_clips):
        v1.append(_make_clip(f"clip{i}"))
    return tl


def _write_timeline(tl: otio.schema.Timeline, path: Path) -> None:
    otio.adapters.write_to_file(tl, str(path))


def _default_opts(**overrides: object) -> AddTextOptions:
    """Return valid AddTextOptions with sensible defaults."""
    base: dict[str, object] = {
        "text": "Hello World",
        "start_sec": 1.0,
        "duration_sec": 3.0,
    }
    base.update(overrides)
    return AddTextOptions(**base)  # type: ignore[arg-type]


# ===========================================================================
# A. output in different directory from timeline → ok=True (new policy)
# ===========================================================================


class TestOutputOutsideTimelineDir:
    """After impl-transform, output may live outside the timeline directory.

    Red: current _check_output_within_timeline_dir in text.py (L60) returns
    PATH_NOT_ALLOWED when output is not under the timeline's parent directory.
    """

    def test_output_in_sibling_dir_allowed(self, tmp_path: Path) -> None:
        """Output in a sibling directory must succeed (removed co-location constraint).

        Red: current code returns ok=False with PATH_NOT_ALLOWED.
        """
        proj_dir = tmp_path / "project"
        work_dir = tmp_path / "work"
        proj_dir.mkdir()
        work_dir.mkdir()

        tl = _make_v1_timeline()
        tl_path = proj_dir / "timeline.otio"
        _write_timeline(tl, tl_path)

        output = work_dir / "annotated.otio"
        result = add_text(str(tl_path), str(output), _default_opts())

        # New policy: different directory is allowed.
        # Red: current code returns ok=False (PATH_NOT_ALLOWED).
        assert result["ok"] is True, (
            f"Output in sibling dir must be allowed; got: {result.get('error')}"
        )

    def test_output_in_parent_dir_allowed(self, tmp_path: Path) -> None:
        """Output placed in the parent directory of the timeline must succeed.

        Red: parent dir is outside the timeline's own dir tree; current boundary
        check returns PATH_NOT_ALLOWED.
        """
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()

        tl = _make_v1_timeline()
        tl_path = proj_dir / "timeline.otio"
        _write_timeline(tl, tl_path)

        output = tmp_path / "out.otio"
        result = add_text(str(tl_path), str(output), _default_opts())

        assert result["ok"] is True, (
            f"Output in parent dir must be allowed; got: {result.get('error')}"
        )

    def test_output_in_deeply_nested_external_dir_allowed(self, tmp_path: Path) -> None:
        """Output deeply nested under an unrelated directory must succeed.

        Red: any path outside the timeline directory tree is currently rejected.
        """
        src_dir = tmp_path / "src" / "footage"
        out_dir = tmp_path / "artifacts" / "text" / "v1"
        src_dir.mkdir(parents=True)
        out_dir.mkdir(parents=True)

        tl = _make_v1_timeline()
        tl_path = src_dir / "timeline.otio"
        _write_timeline(tl, tl_path)

        output = out_dir / "annotated.otio"
        result = add_text(str(tl_path), str(output), _default_opts())

        assert result["ok"] is True, (
            f"Output in unrelated nested dir must be allowed; got: {result.get('error')}"
        )


# ===========================================================================
# B. output == timeline → PATH_NOT_ALLOWED (error code change from INVALID_INPUT)
# ===========================================================================


class TestOutputEqualsSource:
    """check_output_not_source: output == timeline must return PATH_NOT_ALLOWED.

    Red: current code (step 4 of _add_text_inner) raises INVALID_INPUT for this
    case.  After impl-transform delegates to check_output_not_source, the error
    code must be PATH_NOT_ALLOWED.
    """

    def test_output_equals_timeline_path_not_allowed(self, tmp_path: Path) -> None:
        """output path identical to timeline must return PATH_NOT_ALLOWED.

        Red: current code returns INVALID_INPUT (code mismatch).
        """
        tl = _make_v1_timeline()
        tl_path = tmp_path / "timeline.otio"
        _write_timeline(tl, tl_path)

        result = add_text(str(tl_path), str(tl_path), _default_opts())

        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "PATH_NOT_ALLOWED", (
            f"output == timeline must return PATH_NOT_ALLOWED; "
            f"got {error.get('code')!r} (hint: {error.get('hint')!r})"
        )
        assert error.get("hint"), "hint must be non-empty"

    def test_output_equals_timeline_no_path_in_message(self, tmp_path: Path) -> None:
        """CWE-209: error message must not expose the full filesystem path.

        Red: error code assertion above drives the Red; this is an additional
        CWE-209 guard for the new PATH_NOT_ALLOWED path.
        """
        tl = _make_v1_timeline()
        tl_path = tmp_path / "private" / "project.otio"
        tl_path.parent.mkdir(parents=True)
        _write_timeline(tl, tl_path)

        result = add_text(str(tl_path), str(tl_path), _default_opts())

        assert result["ok"] is False
        assert (result.get("error") or {}).get("code") == "PATH_NOT_ALLOWED"
        message = (result.get("error") or {}).get("message", "")
        hint = (result.get("error") or {}).get("hint", "")
        assert str(tmp_path) not in message
        assert str(tmp_path) not in hint


# ===========================================================================
# C. DC-AM-003: mixed relative/absolute media refs preserved in round-trip
# ===========================================================================


class TestDCAM003MixedMediaRefs:
    """DC-AM-003: mixed relative/absolute media references must survive add_text.

    add_text is a transform tool: it loads a timeline, adds text_overlay markers,
    and saves to a new path.  Media references (target_url strings) are NOT
    modified by add_text; they must be written to the output file unchanged.

    Red: these tests depend on add_text succeeding with output in a different
    directory than the timeline (group A above).  Currently, the boundary check
    causes ok=False, so the assertions on the output OTIO are never reached.
    """

    def test_absolute_url_preserved_after_add_text(self, tmp_path: Path) -> None:
        """Absolute media reference in timeline survives add_text unchanged.

        Red: add_text currently returns PATH_NOT_ALLOWED (output outside timeline
        dir), so the output OTIO is never written.
        """
        proj_dir = tmp_path / "proj"
        work_dir = tmp_path / "work"
        proj_dir.mkdir()
        work_dir.mkdir()

        abs_url = "file:///absolute/path/to/clip.mp4"
        tl = otio.schema.Timeline(name="abs_ref_tl")
        v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
        tl.tracks.append(v1)
        tl.tracks.append(a1)
        v1.append(_make_clip("clip0", target_url=abs_url))

        tl_path = proj_dir / "timeline.otio"
        _write_timeline(tl, tl_path)

        output = work_dir / "annotated.otio"
        result = add_text(str(tl_path), str(output), _default_opts())

        # Red: currently ok=False because output is outside timeline dir.
        assert result["ok"] is True, (
            f"add_text with output outside timeline dir must succeed; "
            f"got: {result.get('error')}"
        )

        out_tl = otio.adapters.read_from_file(str(output))
        video_clips = [
            item
            for track in out_tl.tracks
            if track.kind == otio.schema.TrackKind.Video
            for item in track
            if isinstance(item, otio.schema.Clip)
        ]
        assert len(video_clips) == 1
        assert video_clips[0].media_reference.target_url == abs_url, (
            f"Absolute media ref must be preserved; "
            f"got {video_clips[0].media_reference.target_url!r}"
        )

    def test_mixed_refs_both_preserved_in_output_outside_timeline_dir(
        self, tmp_path: Path
    ) -> None:
        """Relative and absolute refs both survive add_text when output is outside
        the timeline directory (DC-AM-003 core scenario).

        Layout:
          tmp_path/proj/timeline.otio  (clip0: relative URL, clip1: absolute URL)
          tmp_path/work/annotated.otio (output; outside proj/)

        Red: add_text currently returns PATH_NOT_ALLOWED (output not under proj/).
        After impl-transform, both refs must be unchanged in annotated.otio.
        """
        proj_dir = tmp_path / "proj"
        work_dir = tmp_path / "work"
        proj_dir.mkdir()
        work_dir.mkdir()

        rel_url = "clip0.mp4"  # stored as-is in OTIO (relative)
        abs_url = "file:///external/media/clip1.mp4"

        tl = otio.schema.Timeline(name="mixed_refs_tl")
        v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
        tl.tracks.append(v1)
        tl.tracks.append(a1)
        v1.append(_make_clip("clip0", target_url=rel_url))
        v1.append(_make_clip("clip1", target_url=abs_url))

        tl_path = proj_dir / "timeline.otio"
        _write_timeline(tl, tl_path)

        output = work_dir / "annotated.otio"
        result = add_text(str(tl_path), str(output), _default_opts())

        # Red: currently ok=False (output outside timeline dir is rejected).
        assert result["ok"] is True, (
            f"add_text with mixed refs and output outside timeline dir must succeed; "
            f"got: {result.get('error')}"
        )

        out_tl = otio.adapters.read_from_file(str(output))
        clips = [
            item
            for track in out_tl.tracks
            if track.kind == otio.schema.TrackKind.Video
            for item in track
            if isinstance(item, otio.schema.Clip)
        ]
        assert len(clips) == 2

        assert clips[0].media_reference.target_url == rel_url, (
            f"Relative URL must be preserved as-is; "
            f"got {clips[0].media_reference.target_url!r}"
        )
        assert clips[1].media_reference.target_url == abs_url, (
            f"Absolute URL must be preserved; "
            f"got {clips[1].media_reference.target_url!r}"
        )

    def test_text_overlay_marker_added_to_output_with_mixed_refs(
        self, tmp_path: Path
    ) -> None:
        """Marker is added and refs are preserved simultaneously (combined check).

        After add_text on a timeline with mixed refs:
          - output has 1 text_overlay marker
          - both media refs are unchanged

        Red: add_text returns PATH_NOT_ALLOWED before the marker is written.
        """
        proj_dir = tmp_path / "proj"
        work_dir = tmp_path / "work"
        proj_dir.mkdir()
        work_dir.mkdir()

        rel_url = "raw.mp4"
        abs_url = "file:///stock/footage.mp4"

        tl = otio.schema.Timeline(name="combined_tl")
        v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
        tl.tracks.append(v1)
        tl.tracks.append(a1)
        v1.append(_make_clip("c0", target_url=rel_url))
        v1.append(_make_clip("c1", target_url=abs_url))

        tl_path = proj_dir / "tl.otio"
        _write_timeline(tl, tl_path)

        output = work_dir / "tl_annotated.otio"
        result = add_text(str(tl_path), str(output), _default_opts())

        # Red: currently PATH_NOT_ALLOWED because output is outside timeline dir.
        assert result["ok"] is True, f"Expected ok=True; got: {result.get('error')}"

        out_tl = otio.adapters.read_from_file(str(output))

        # One text_overlay marker must be present.
        text_markers = [
            m
            for track in out_tl.tracks
            if track.kind == otio.schema.TrackKind.Video
            for m in track.markers
            if (
                isinstance(m.metadata.get("clipwright"), collections.abc.Mapping)
                and m.metadata["clipwright"].get("kind") == "text_overlay"
            )
        ]
        assert len(text_markers) == 1, (
            f"Expected 1 text_overlay marker; got {len(text_markers)}"
        )

        # Both media refs must be unchanged.
        clips = [
            item
            for track in out_tl.tracks
            if track.kind == otio.schema.TrackKind.Video
            for item in track
            if isinstance(item, otio.schema.Clip)
        ]
        assert clips[0].media_reference.target_url == rel_url
        assert clips[1].media_reference.target_url == abs_url


# ===========================================================================
# D. Preserved checks: .otio extension, parent dir, missing timeline
#    (regression guards; expected Green — confirm impl-transform does not break)
# ===========================================================================


class TestPreservedPathChecks:
    """These checks existed before impl-transform and must remain in force.

    These tests are expected to be Green before and after impl-transform.
    They guard against regressions introduced during the refactor.
    """

    def test_non_otio_extension_returns_invalid_input(self, tmp_path: Path) -> None:
        """output with an extension other than .otio must return INVALID_INPUT."""
        tl = _make_v1_timeline()
        tl_path = tmp_path / "timeline.otio"
        _write_timeline(tl, tl_path)

        result = add_text(str(tl_path), str(tmp_path / "out.mp4"), _default_opts())

        assert result["ok"] is False
        assert (result.get("error") or {}).get("code") == "INVALID_INPUT"

    def test_missing_parent_dir_returns_file_not_found(self, tmp_path: Path) -> None:
        """output whose parent directory does not exist must return FILE_NOT_FOUND."""
        tl = _make_v1_timeline()
        tl_path = tmp_path / "timeline.otio"
        _write_timeline(tl, tl_path)

        output = tmp_path / "nonexistent_dir" / "out.otio"
        result = add_text(str(tl_path), str(output), _default_opts())

        assert result["ok"] is False
        assert (result.get("error") or {}).get("code") == "FILE_NOT_FOUND"

    def test_missing_timeline_returns_file_not_found(self, tmp_path: Path) -> None:
        """Non-existent timeline must return FILE_NOT_FOUND."""
        missing = tmp_path / "does_not_exist.otio"
        output = tmp_path / "out.otio"

        result = add_text(str(missing), str(output), _default_opts())

        assert result["ok"] is False
        assert (result.get("error") or {}).get("code") == "FILE_NOT_FOUND"
