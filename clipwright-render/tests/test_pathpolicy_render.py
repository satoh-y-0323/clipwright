"""test_pathpolicy_render.py — Green tests for ADR-PP-1 path-policy unification.

render.py delegates to clipwright.pathpolicy.check_media_ref for boundary
validation of media / subtitle / image-overlay references (ADR-PP-1).

Decision logic (ADR-PP-1):
  - Absolute ref to existing real file → allowed regardless of timeline dir boundary.
  - Relative ref: must resolve within otio_dir tree (CWE-22 guard, unchanged).
  - Absolute ref × symlink → PATH_NOT_ALLOWED (ADR-PP-2).
  - Absolute ref × non-existent → rejected.
  - output == source → PATH_NOT_ALLOWED (unchanged, DC-AM-002).

Test IDs and status:
  PP-1a  Absolute media ref, existing real file, outside timeline dir → ok=True      [GREEN]
  PP-1b  Multi-source: second source absolute + outside, existing → ok=True          [GREEN]
  PP-1c  Absolute subtitle ref, existing .srt, outside timeline dir → ok=True        [GREEN]
  PP-1d  Absolute image overlay ref, existing .png, outside timeline dir → ok=True   [GREEN]
  PP-2a  Relative ref within timeline dir → ok=True (unchanged allow)                [GREEN]
  PP-3a  Relative ../ traversal outside timeline dir → PATH_NOT_ALLOWED              [GREEN]
  PP-4a  Absolute non-existent ref → rejected (not ok=True)                          [GREEN]
  PP-5a  output == absolute external source → PATH_NOT_ALLOWED                       [GREEN]
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_render.schemas import RenderOptions

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

FPS = 30.0

# Minimal valid PNG header (8-byte PNG signature + 4-byte padding).
# Used to satisfy _verify_image_magic() in render.py without a real PNG file.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 4


# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_render.py — no cross-file import to avoid
# coupling the test suites)
# ---------------------------------------------------------------------------


def _rt(seconds: float, rate: float = FPS) -> otio.opentime.RationalTime:
    return otio.opentime.RationalTime(seconds * rate, rate)


def _tr(start: float, duration: float, rate: float = FPS) -> otio.opentime.TimeRange:
    return otio.opentime.TimeRange(
        start_time=_rt(start, rate),
        duration=_rt(duration, rate),
    )


def _make_clip(source: str, start: float, duration: float) -> otio.schema.Clip:
    clip = otio.schema.Clip()
    clip.media_reference = otio.schema.ExternalReference(target_url=source)
    clip.source_range = _tr(start, duration)
    return clip


def _make_timeline(clips: list[otio.schema.Clip]) -> otio.schema.Timeline:
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    for clip in clips:
        track.append(clip)
    tl = otio.schema.Timeline()
    tl.tracks.append(track)
    return tl


def _write_timeline(path: Path, clips: list[otio.schema.Clip]) -> None:
    tl = _make_timeline(clips)
    otio.adapters.write_to_file(tl, str(path))


def _make_media_info(
    path: str = "/fake/source.mp4",
    *,
    has_video: bool = True,
    audio_streams: int = 1,
    bit_rate: int | None = 8_000_000,
    fps_rate: float | None = None,
) -> MediaInfo:
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(
            StreamInfo(
                index=0, codec_type="video", codec_name="h264", width=1920, height=1080
            )
        )
    for i in range(audio_streams):
        streams.append(
            StreamInfo(index=len(streams), codec_type="audio", codec_name="aac")
        )
    duration = (
        RationalTimeModel(value=10.0 * fps_rate, rate=fps_rate)
        if fps_rate is not None
        else None
    )
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=duration,
        streams=streams,
        bit_rate=bit_rate,
    )


def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
    """Stub for process.run — always succeeds without calling ffmpeg."""
    return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


def _fake_resolve_tool(name: str, env_var: str | None = None) -> str:
    return f"/usr/bin/{name}"


# ---------------------------------------------------------------------------
# PP-1a  Absolute media ref, existing real file, outside timeline dir → ok=True
# GREEN: render.py delegates to pathpolicy.check_media_ref (ADR-PP-1).
# ---------------------------------------------------------------------------


class TestAbsoluteExternalMediaRefAllowed:
    """PP-1a/PP-1b: Absolute media references to existing real files outside the
    timeline directory are allowed under ADR-PP-1.

    Green because render.py delegates to pathpolicy.check_media_ref which applies
    the absolute escape hatch: existing real files are accepted regardless of boundary.
    """

    def test_absolute_media_outside_timeline_dir_passes(self, tmp_path: Path) -> None:
        """PP-1a: Single absolute external media ref (existing real file) → ok=True.

        Setup: timeline in project/, source in outside/ (absolute path, file exists,
        no symlinks).  render.py delegates to pathpolicy.check_media_ref which
        applies the absolute escape hatch (ADR-PP-1).
        """
        from clipwright_render.render import render_timeline

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        outside_source = str(outside_dir / "clip.mp4")
        Path(outside_source).touch()

        tl_path = project_dir / "tl.otio"
        _write_timeline(tl_path, [_make_clip(outside_source, 0.0, 5.0)])
        output = str(project_dir / "out.mp4")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=outside_source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=_fake_resolve_tool,
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        # ADR-PP-1: absolute external ref to existing real file must not be blocked.
        assert result["ok"] is True, (
            f"Expected ok=True (ADR-PP-1 absolute external ref allowed),"
            f" got: {result.get('error')}"
        )

    def test_multi_source_second_outside_timeline_dir_passes(
        self, tmp_path: Path
    ) -> None:
        """PP-1b: Multi-source timeline where second source is absolute + outside → ok=True.

        First source is inside the timeline dir (standard case).  Second source is
        absolute and outside, but exists and has no symlinks.  Both are accepted
        because render.py delegates to pathpolicy.check_media_ref (ADR-PP-1).
        """
        from clipwright_render.render import render_timeline

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        src0 = str(project_dir / "clip0.mp4")
        src1 = str(outside_dir / "clip1.mp4")
        Path(src0).touch()
        Path(src1).touch()

        tl_path = project_dir / "tl.otio"
        _write_timeline(
            tl_path,
            [_make_clip(src0, 0.0, 3.0), _make_clip(src1, 0.0, 2.0)],
        )
        output = str(project_dir / "out.mp4")

        def _fake_inspect(path: str) -> MediaInfo:
            # fps_rate=30.0 is required so that build_plan's multi-source
            # _resolve_target_spec can derive target fps (fps=None raises INVALID_INPUT).
            return _make_media_info(path=path, fps_rate=30.0)

        with (
            patch("clipwright_render.render.inspect_media", side_effect=_fake_inspect),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=_fake_resolve_tool,
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, (
            f"Expected ok=True (ADR-PP-1 multi-source absolute external allowed),"
            f" got: {result.get('error')}"
        )


# ---------------------------------------------------------------------------
# PP-1c  Absolute subtitle ref, existing .srt, outside timeline dir → ok=True
# GREEN: render.py delegates to pathpolicy.check_media_ref (ADR-PP-1).
# ---------------------------------------------------------------------------


class TestAbsoluteExternalSubtitleRefAllowed:
    """PP-1c: Absolute subtitle references to existing .srt files outside the timeline
    directory are allowed under ADR-PP-1 (unified policy).

    Green because render.py delegates to pathpolicy.check_media_ref for subtitle
    boundary validation.
    """

    def test_absolute_subtitle_outside_timeline_dir_passes(
        self, tmp_path: Path
    ) -> None:
        """PP-1c: Absolute .srt subtitle outside timeline dir → ok=True.

        Setup: source in project/ (inside), subtitle .srt in outside/ (outside, exists).
        render.py delegates to pathpolicy.check_media_ref which applies the absolute
        escape hatch for subtitle references (ADR-PP-1).
        """
        from clipwright_render.render import render_timeline
        from clipwright_render.schemas import (  # type: ignore[attr-defined]
            RenderOptions,
            SubtitleOptions,
        )

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        src = str(project_dir / "clip.mp4")
        Path(src).touch()

        # Subtitle is in the outside directory (absolute path, file exists)
        subtitle_path = str(outside_dir / "subs.srt")
        Path(subtitle_path).touch()

        tl_path = project_dir / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 5.0)])
        output = str(project_dir / "out.mp4")

        options = RenderOptions(
            subtitle=SubtitleOptions(path=subtitle_path)  # type: ignore[call-arg]
        )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=_fake_resolve_tool,
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=options,
                dry_run=True,
            )

            # ADR-PP-1: absolute external .srt must not be blocked.
            assert result["ok"] is True, (
                f"Expected ok=True (ADR-PP-1 absolute external subtitle allowed),"
                f" got: {result.get('error')}"
            )


# ---------------------------------------------------------------------------
# PP-1d  Absolute image overlay ref, existing .png, outside timeline dir → ok=True
# GREEN: render.py delegates to pathpolicy.check_media_ref (ADR-PP-1).
# ---------------------------------------------------------------------------


def _make_timeline_with_image_overlay(
    clips: list[otio.schema.Clip],
    image_path_in_marker: str,
) -> otio.schema.Timeline:
    """Build a single-video-track Timeline with one image_overlay marker.

    image_path_in_marker: relative or absolute path stored in the marker metadata.
    When relative, plan.py will reconstruct it as absolute using the timeline path.
    """
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    for clip in clips:
        track.append(clip)

    rate = FPS
    marker = otio.schema.Marker(
        name="image_0",
        marked_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(1.0 * rate, rate),
            duration=otio.opentime.RationalTime(3.0 * rate, rate),
        ),
        metadata={
            "clipwright": {
                "kind": "image_overlay",
                "tool": "clipwright-overlay",
                "version": "0.1.0",
                "image_path": image_path_in_marker,
                "start_sec": 1.0,
                "duration_sec": 3.0,
                "x": "(W-w)/2",
                "y": "(H-h)/2",
                "scale": 1.0,
                "opacity": 1.0,
                "fade_in_sec": 0.0,
                "fade_out_sec": 0.0,
            }
        },
    )
    track.markers.append(marker)

    tl = otio.schema.Timeline()
    tl.tracks.append(track)
    return tl


class TestAbsoluteExternalImageOverlayAllowed:
    """PP-1d: Absolute image overlay references to existing .png files outside the
    timeline directory are allowed under ADR-PP-1 (unified policy).

    Green because render.py delegates to pathpolicy.check_media_ref for image
    overlay boundary validation.
    """

    def test_absolute_image_outside_timeline_dir_passes(self, tmp_path: Path) -> None:
        """PP-1d: Absolute image overlay ref to existing .png outside timeline dir → ok=True.

        The image_overlay marker stores a relative path "../outside/logo.png"
        (relative to timeline dir).  plan.py reconstructs this to an absolute path
        under tmp_path/outside/.  render.py delegates to pathpolicy.check_media_ref
        which applies the absolute escape hatch (ADR-PP-1).

        Note: The image boundary check in render.py fires in the 6b (execute) path,
        so dry_run=False is required.  ffmpeg calls are fully mocked.
        """
        from clipwright_render.render import render_timeline

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        # Source inside timeline dir
        src = str(project_dir / "clip.mp4")
        Path(src).touch()

        # Image outside timeline dir (absolute when reconstructed by plan.py)
        image_file = outside_dir / "logo.png"
        image_file.write_bytes(_PNG_MAGIC)

        # Relative path "../outside/logo.png" from project_dir → resolves to outside_dir/logo.png
        image_path_in_marker = "../outside/logo.png"

        tl = _make_timeline_with_image_overlay(
            [_make_clip(src, 0.0, 5.0)],
            image_path_in_marker=image_path_in_marker,
        )
        tl_path = project_dir / "tl.otio"
        otio.adapters.write_to_file(tl, str(tl_path))

        # Pre-create output file so the post-run existence check passes
        output = str(project_dir / "out.mp4")
        Path(output).touch()

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=_fake_resolve_tool,
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
                dry_run=False,
            )

        # ADR-PP-1: absolute external image must not be blocked.
        assert result["ok"] is True, (
            f"Expected ok=True (ADR-PP-1 absolute external image overlay allowed),"
            f" got: {result.get('error')}"
        )


# ---------------------------------------------------------------------------
# PP-2a  Relative ref within timeline dir → passes (unchanged allow)
# GREEN: both old and new policies allow relative refs within the timeline dir.
# ---------------------------------------------------------------------------


class TestRelativeRefWithinTimeline:
    """PP-2a: Relative media references within the timeline directory must continue
    to be allowed (behavior preserved across old and new policy).
    """

    def test_relative_media_ref_within_timeline_dir_passes(
        self, tmp_path: Path
    ) -> None:
        """PP-2a: Absolute source path within timeline dir → ok=True.

        When the OTIO stores an absolute path pointing inside the timeline directory,
        both old policy (within-boundary allow) and new policy (absolute + exists → allow)
        accept it.  Verifies that the allow path is preserved.
        """
        from clipwright_render.render import render_timeline

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        inside_source = str(project_dir / "clip.mp4")
        Path(inside_source).touch()

        tl_path = project_dir / "tl.otio"
        _write_timeline(tl_path, [_make_clip(inside_source, 0.0, 5.0)])
        output = str(project_dir / "out.mp4")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=inside_source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=_fake_resolve_tool,
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        # Must still be allowed (regression guard)
        assert result["ok"] is True, (
            f"Within-timeline-dir source unexpectedly blocked: {result.get('error')}"
        )


# ---------------------------------------------------------------------------
# PP-3a  Relative ../ traversal outside timeline dir → PATH_NOT_ALLOWED
# GREEN: both old and new policies block relative path traversal outside the
#        timeline directory (CWE-22 guard unchanged).
# ---------------------------------------------------------------------------


class TestRelativeTraversalBlocked:
    """PP-3a: Relative path traversal (../) that resolves outside the timeline
    directory must remain blocked under both old and new policy (CWE-22 guard).
    """

    def test_relative_dotdot_traversal_blocked(self, tmp_path: Path) -> None:
        """PP-3a: OTIO with relative source path going above timeline dir → PATH_NOT_ALLOWED.

        The OTIO stores source as "../outside/clip.mp4" (relative).  Under
        ADR-PP-1, relative refs must still resolve within the otio_dir tree.
        Traversal outside is rejected with PATH_NOT_ALLOWED by both old and new policy.
        """
        from clipwright_render.render import render_timeline

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        outside_source = outside_dir / "clip.mp4"
        outside_source.touch()

        # Store a RELATIVE path that traverses above the project dir
        relative_traversal = "../outside/clip.mp4"
        tl_path = project_dir / "tl.otio"
        _write_timeline(tl_path, [_make_clip(relative_traversal, 0.0, 5.0)])
        output = str(project_dir / "out.mp4")

        result = render_timeline(
            timeline=str(tl_path),
            output=output,
            options=RenderOptions(),
        )

        # Relative traversal outside boundary must remain blocked (CWE-22)
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED


# ---------------------------------------------------------------------------
# PP-4a  Absolute non-existent ref → rejected
# GREEN: non-existent absolute refs are rejected under both policies.
#        Old: PATH_NOT_ALLOWED (boundary check fires before existence for outside refs).
#        New: PATH_NOT_ALLOWED (check_media_ref raises for non-file absolute refs).
# ---------------------------------------------------------------------------


class TestAbsoluteNonExistentRefRejected:
    """PP-4a: Absolute references to non-existent files must be rejected under
    both old and new policy (rejected, just for different reasons).
    """

    def test_absolute_nonexistent_outside_rejected(self, tmp_path: Path) -> None:
        """PP-4a: Absolute path to non-existent file outside timeline dir → rejected.

        Old policy: PATH_NOT_ALLOWED (boundary check fails before existence check).
        New policy: PATH_NOT_ALLOWED (check_media_ref raises for non-file absolute ref).
        Both policies reject this: error must be non-ok.
        """
        from clipwright_render.render import render_timeline

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        # File does NOT exist
        missing_source = str(outside_dir / "nonexistent.mp4")

        tl_path = project_dir / "tl.otio"
        _write_timeline(tl_path, [_make_clip(missing_source, 0.0, 5.0)])
        output = str(project_dir / "out.mp4")

        result = render_timeline(
            timeline=str(tl_path),
            output=output,
            options=RenderOptions(),
        )

        # Must be rejected under both old and new policy
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# PP-5a  output == absolute external source → PATH_NOT_ALLOWED (unchanged)
# GREEN: output-equals-source is always rejected regardless of boundary policy.
# ---------------------------------------------------------------------------


class TestOutputEqualsSourceStillBlocked:
    """PP-5a: output == source must remain blocked under both old and new policy
    (non-destructive principle, DC-AM-002).

    Even if ADR-PP-1 allows absolute external sources, writing to the same file
    as a source must always be rejected.
    """

    def test_output_equals_absolute_external_source_blocked(
        self, tmp_path: Path
    ) -> None:
        """PP-5a: output == absolute external source (outside timeline dir) → PATH_NOT_ALLOWED.

        Under the new policy, the absolute source itself would be allowed.  But output ==
        source remains invalid regardless (DC-AM-002 / non-destructive).
        """
        from clipwright_render.render import render_timeline

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        outside_source = str(outside_dir / "clip.mp4")
        Path(outside_source).touch()

        tl_path = project_dir / "tl.otio"
        _write_timeline(tl_path, [_make_clip(outside_source, 0.0, 5.0)])

        # output == source (same absolute path)
        result = render_timeline(
            timeline=str(tl_path),
            output=outside_source,  # output == source → must always be rejected
            options=RenderOptions(),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED
