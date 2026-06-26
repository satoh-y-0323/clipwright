"""Tests for clipwright-overlay path-policy contract.

Covers the boundary contract for overlay (accumulate type):
  P-1  output may be placed outside the input timeline's directory.
  P-2  image_path stored via media_ref_for_otio:
         inside otio_dir  -> relative posix (behaviour-preserving)
         outside otio_dir -> absolute path  (no '../' traversal stored)
  P-3  output == timeline -> PATH_NOT_ALLOWED (CR-M-5 unified code)
  P-4  DC-AM-003: existing relative media refs + external absolute image
       survive a load->save round-trip intact.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import opentimelineio as otio
from _imgbytes import DUMMY_PNG_BYTES as _DUMMY_PNG_BYTES

from clipwright_overlay.overlay import add_overlay
from clipwright_overlay.schemas import AddOverlayOptions

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RATE = 24.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_clip(
    name: str, duration_sec: float = 10.0, rate: float = _RATE
) -> otio.schema.Clip:
    ref = otio.schema.ExternalReference(target_url=f"file:///media/{name}.mp4")
    sr = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, rate),
        duration=otio.opentime.RationalTime(duration_sec * rate, rate),
    )
    return otio.schema.Clip(name=name, media_reference=ref, source_range=sr)


def _make_clip_with_relative_url(
    name: str, rel_url: str, duration_sec: float = 10.0, rate: float = _RATE
) -> otio.schema.Clip:
    """Build a Clip whose ExternalReference uses a relative target_url (DC-AM-003)."""
    ref = otio.schema.ExternalReference(target_url=rel_url)
    sr = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, rate),
        duration=otio.opentime.RationalTime(duration_sec * rate, rate),
    )
    return otio.schema.Clip(name=name, media_reference=ref, source_range=sr)


def _make_v1_timeline(n_clips: int = 1, rate: float = _RATE) -> otio.schema.Timeline:
    tl = otio.schema.Timeline(name="test_tl")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(v1)
    tl.tracks.append(a1)
    for i in range(n_clips):
        v1.append(_make_clip(f"clip{i}", rate=rate))
    return tl


def _make_v1_timeline_with_relative_url() -> otio.schema.Timeline:
    """Build a V1 timeline whose single clip uses a relative target_url.

    Simulates an existing project where the clip media file lives alongside
    the OTIO file (i.e. the target_url was written by a create-type tool
    using media_ref_for_otio when the media was inside the otio_dir).
    """
    tl = otio.schema.Timeline(name="mixed_tl")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(v1)
    tl.tracks.append(a1)
    v1.append(_make_clip_with_relative_url("clip0", "media/clip0.mp4"))
    return tl


def _write_timeline(tl: otio.schema.Timeline, path: Path) -> None:
    otio.adapters.write_to_file(tl, str(path))


def _read_timeline(path: Path) -> otio.schema.Timeline:
    return otio.adapters.read_from_file(str(path))  # type: ignore[no-any-return]


def _write_dummy_png(path: Path) -> None:
    path.write_bytes(_DUMMY_PNG_BYTES)


def _default_opts(image_path: str, **overrides: object) -> AddOverlayOptions:
    """Return AddOverlayOptions with valid defaults.  fade secs pinned to 0.0."""
    base: dict[str, object] = {
        "image_path": image_path,
        "start_sec": 1.0,
        "duration_sec": 3.0,
        "x": "(W-w)/2",
        "y": "(H-h)/2",
        "scale": 1.0,
        "opacity": 1.0,
        "fade_in_sec": 0.0,
        "fade_out_sec": 0.0,
    }
    base.update(overrides)
    return AddOverlayOptions(**base)  # type: ignore[arg-type]


def _get_image_overlay_markers(tl: otio.schema.Timeline) -> list[otio.schema.Marker]:
    for track in tl.tracks:
        if track.kind == otio.schema.TrackKind.Video:
            return [
                m
                for m in track.markers
                if m.metadata.get("clipwright", {}).get("kind") == "image_overlay"
            ]
    return []


# ===========================================================================
# P-1: output outside timeline dir is allowed
# ===========================================================================


class TestOutputOutsideTimelineDir:
    """output may be placed in a directory outside the input timeline's directory.

    The co-location restriction was removed (impl-overlay); only output != source matters.
    """

    def test_output_in_separate_dir_returns_ok(self) -> None:
        """output in a dir outside the timeline dir must return ok=True."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            proj = tmp / "proj"
            work = tmp / "work"
            proj.mkdir()
            work.mkdir()

            tl = _make_v1_timeline()
            inp = proj / "in.otio"
            _write_timeline(tl, inp)

            # Image is co-located with the input timeline (will be stored as relative)
            img = proj / "logo.png"
            _write_dummy_png(img)

            # output lives in work/, NOT in proj/ where the timeline lives
            out = work / "out.otio"

            opts = _default_opts(str(img))
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True, (
                f"output outside timeline dir must be allowed; "
                f"got error: {result.get('error')}"
            )

    def test_output_in_separate_dir_marker_written(self) -> None:
        """Marker is added to the output timeline even when output is outside proj dir."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            proj = tmp / "proj"
            work = tmp / "work"
            proj.mkdir()
            work.mkdir()

            tl = _make_v1_timeline()
            inp = proj / "in.otio"
            _write_timeline(tl, inp)

            img = proj / "logo.png"
            _write_dummy_png(img)

            out = work / "out.otio"
            opts = _default_opts(str(img))
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True
            out_tl = _read_timeline(out)
            markers = _get_image_overlay_markers(out_tl)
            assert len(markers) == 1, (
                f"Expected 1 marker in output timeline, got {len(markers)}"
            )

    def test_output_in_separate_dir_artifact_path_correct(self) -> None:
        """artifacts[role=timeline].path must equal the resolved output path."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            proj = tmp / "proj"
            work = tmp / "work"
            proj.mkdir()
            work.mkdir()

            tl = _make_v1_timeline()
            inp = proj / "in.otio"
            _write_timeline(tl, inp)

            img = proj / "logo.png"
            _write_dummy_png(img)

            out = work / "out.otio"
            opts = _default_opts(str(img))
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True
            artifacts = result.get("artifacts") or []
            tl_art = next(
                (
                    a
                    for a in artifacts
                    if (
                        a.get("role")
                        if isinstance(a, dict)
                        else getattr(a, "role", None)
                    )
                    == "timeline"
                ),
                None,
            )
            assert tl_art is not None, "artifacts must contain a 'timeline' entry"
            art_path = (
                tl_art.get("path")
                if isinstance(tl_art, dict)
                else getattr(tl_art, "path", None)
            )
            assert str(out.resolve()) == str(art_path), (
                f"artifact path must equal resolved output path; "
                f"expected {out.resolve()!r}, got {art_path!r}"
            )


# ===========================================================================
# P-2: image reference storage via media_ref_for_otio
# ===========================================================================


class TestImageReferenceStorage:
    """image_path stored as relative when inside otio_dir, absolute when outside.

    media_ref_for_otio rule (ADR-PP-1):
      - source under otio_dir -> relative posix
      - source outside otio_dir -> absolute path (no '../' traversal stored)
    """

    def test_image_inside_otio_dir_stored_as_relative(self) -> None:
        """Image co-located with output OTIO -> stored as relative posix.

        This is behaviour-preserving: the 'inside -> relative' branch of
        media_ref_for_otio matches the previous always-relative implementation
        for the co-located case.

        GREEN (regression guard): currently passes; must continue to pass after
        impl-overlay replaces _overlay_metadata_dict with media_ref_for_otio.
        """
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(str(img))
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True
            out_tl = _read_timeline(out)
            markers = _get_image_overlay_markers(out_tl)
            assert len(markers) == 1
            stored = markers[0].metadata["clipwright"]["image_path"]

            # Image is inside otio_dir -> stored path must be relative
            assert not Path(str(stored)).is_absolute(), (
                f"image inside otio_dir must be stored as relative; got: {stored!r}"
            )
            # Must match the expected relative posix path
            expected_rel = Path(
                os.path.relpath(img.resolve(), out.resolve().parent)
            ).as_posix()
            assert stored == expected_rel, (
                f"Expected relative posix {expected_rel!r}, got {stored!r}"
            )

    def test_image_in_subdir_of_otio_dir_stored_as_relative(self) -> None:
        """Image in recursive subdir of otio_dir -> relative posix (inside branch).

        GREEN (regression guard): behaviour-preserving for deep subdir images.
        """
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            subdir = tmp / "assets" / "logos"
            subdir.mkdir(parents=True)

            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = subdir / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(str(img))
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True
            out_tl = _read_timeline(out)
            markers = _get_image_overlay_markers(out_tl)
            assert len(markers) == 1
            stored = markers[0].metadata["clipwright"]["image_path"]

            # Image is inside otio_dir (subdir) -> relative
            assert not Path(str(stored)).is_absolute(), (
                f"image in subdir of otio_dir must be relative; got: {stored!r}"
            )
            expected_rel = Path(
                os.path.relpath(img.resolve(), out.resolve().parent)
            ).as_posix()
            assert stored == expected_rel

    def test_image_outside_otio_dir_stored_as_absolute(self) -> None:
        """Image outside output OTIO dir -> stored as absolute path.

        media_ref_for_otio rule: image outside otio_dir -> absolute path (no '../' stored).
        """
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            proj = tmp / "proj"
            media = tmp / "media"
            proj.mkdir()
            media.mkdir()

            tl = _make_v1_timeline()
            inp = proj / "in.otio"
            out = proj / "out.otio"
            _write_timeline(tl, inp)

            # Image lives in media/, outside proj/ (the otio_dir)
            img = media / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(str(img))
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True, (
                f"image outside otio_dir must be allowed; "
                f"got error: {result.get('error')}"
            )
            out_tl = _read_timeline(out)
            markers = _get_image_overlay_markers(out_tl)
            assert len(markers) == 1
            stored = markers[0].metadata["clipwright"]["image_path"]

            # Image is outside otio_dir -> stored as absolute
            assert Path(str(stored)).is_absolute(), (
                f"image outside otio_dir must be stored as absolute; got: {stored!r}"
            )
            # Absolute path must resolve to the original image
            assert Path(str(stored)).resolve() == img.resolve(), (
                f"Absolute stored path must resolve to the original image; "
                f"got {stored!r}"
            )

    def test_outside_image_stored_as_absolute_not_traversal(self) -> None:
        """Image in sibling dir -> stored as absolute, never as '../sibling/logo.png'.

        Relative traversal paths ('../...') must never appear in stored marker
        metadata. When the image is outside otio_dir, media_ref_for_otio returns
        the absolute path instead of computing a '../'-prefixed relative path.
        """
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            proj = tmp / "proj"
            images = tmp / "images"
            proj.mkdir()
            images.mkdir()

            tl = _make_v1_timeline()
            inp = proj / "in.otio"
            out = proj / "out.otio"
            _write_timeline(tl, inp)

            # Image is in a sibling dir; relative path from proj would be '../images/logo.png'
            img = images / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(str(img))
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True, (
                f"image in sibling dir must be allowed; "
                f"got error: {result.get('error')}"
            )
            out_tl = _read_timeline(out)
            markers = _get_image_overlay_markers(out_tl)
            assert len(markers) == 1
            stored = str(markers[0].metadata["clipwright"]["image_path"])

            # Must never store a relative traversal path
            assert ".." not in stored, (
                f"Stored image_path must not contain '..' traversal; got: {stored!r}"
            )
            # Must be absolute
            assert Path(stored).is_absolute(), (
                f"Image outside otio_dir must be stored as absolute; got: {stored!r}"
            )


# ===========================================================================
# P-3: output == source rejected (regression guard)
# ===========================================================================


class TestOutputEqualsSourceRejected:
    """output == input timeline must return PATH_NOT_ALLOWED (CR-M-5).

    Verifies that the output != source check uses PATH_NOT_ALLOWED (path policy
    violation) rather than the generic INVALID_INPUT.
    """

    def test_output_equals_timeline_returns_path_not_allowed(self) -> None:
        """output path same as input timeline -> PATH_NOT_ALLOWED."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(str(img))
            result = add_overlay(str(inp), str(inp), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "PATH_NOT_ALLOWED", (
                f"output == timeline must return PATH_NOT_ALLOWED; "
                f"got {error.get('code')!r}"
            )
            assert error.get("hint")


# ===========================================================================
# P-4: DC-AM-003 — mixed relative/absolute refs round-trip
# ===========================================================================


class TestMixedRefRoundTrip:
    """DC-AM-003: existing relative clip refs + external absolute image marker are safe.

    When an existing timeline has clips with relative target_url values (written by
    a previous create-type call where the media was co-located), adding an image
    overlay whose image is outside the otio_dir must:
      - succeed (ok=True)
      - preserve the existing relative clip target_url values unchanged
      - store the new image_path as absolute
    """

    def test_relative_clip_refs_preserved_after_adding_external_image(self) -> None:
        """Existing relative clip target_urls are preserved in load->save round-trip."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            proj = tmp / "proj"
            media = tmp / "media"
            proj.mkdir()
            media.mkdir()

            # Input timeline with a clip that has a relative target_url
            tl = _make_v1_timeline_with_relative_url()
            inp = proj / "in.otio"
            out = proj / "out.otio"
            _write_timeline(tl, inp)

            # External image (outside proj/ -> will be stored as absolute)
            img = media / "watermark.png"
            _write_dummy_png(img)

            opts = _default_opts(str(img))
            result = add_overlay(str(inp), str(out), opts)

            # Step 1: must succeed
            assert result["ok"] is True, (
                f"external image overlay must succeed; got: {result.get('error')}"
            )

            # Step 2: load the saved output timeline
            saved_tl = _read_timeline(out)

            # Step 3: verify the existing clip's relative target_url is unchanged
            v1 = next(
                (t for t in saved_tl.tracks if t.kind == otio.schema.TrackKind.Video),
                None,
            )
            assert v1 is not None
            clips = [item for item in v1 if isinstance(item, otio.schema.Clip)]
            assert len(clips) == 1, f"Expected 1 clip, got {len(clips)}"

            original_url = "media/clip0.mp4"
            saved_url = clips[0].media_reference.target_url  # type: ignore[union-attr]
            assert saved_url == original_url, (
                f"Relative clip target_url must be preserved; "
                f"expected {original_url!r}, got {saved_url!r}"
            )

            # Step 4: the new image marker must use absolute path
            markers = _get_image_overlay_markers(saved_tl)
            assert len(markers) == 1
            stored_image = markers[0].metadata["clipwright"]["image_path"]
            assert Path(str(stored_image)).is_absolute(), (
                f"External image must be stored as absolute; got: {stored_image!r}"
            )

    def test_relative_and_absolute_refs_coexist_without_corruption(self) -> None:
        """Timeline has both relative (clip) and absolute (image marker) refs after call.

        After the round-trip: clip.target_url is relative; marker image_path is absolute.
        The two refs must be distinct (no aliasing or corruption).
        """
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            proj = tmp / "proj"
            ext = tmp / "ext"
            proj.mkdir()
            ext.mkdir()

            tl = _make_v1_timeline_with_relative_url()
            inp = proj / "in.otio"
            out = proj / "out.otio"
            _write_timeline(tl, inp)

            img = ext / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(str(img))
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True

            saved_tl = _read_timeline(out)
            v1 = next(
                t for t in saved_tl.tracks if t.kind == otio.schema.TrackKind.Video
            )
            clips = [item for item in v1 if isinstance(item, otio.schema.Clip)]
            markers = _get_image_overlay_markers(saved_tl)

            clip_url = clips[0].media_reference.target_url  # type: ignore[union-attr]
            image_path = str(markers[0].metadata["clipwright"]["image_path"])

            # Clip uses relative URL; image uses absolute path
            assert not Path(clip_url).is_absolute(), (
                f"clip target_url must still be relative; got: {clip_url!r}"
            )
            assert Path(image_path).is_absolute(), (
                f"image marker path must be absolute; got: {image_path!r}"
            )
            # They must not alias each other
            assert clip_url != image_path, (
                "clip target_url and image marker path must not be equal"
            )
