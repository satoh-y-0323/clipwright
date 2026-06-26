"""test_pathpolicy_frames.py — Regression tests: frames artifact containment.

Locks the artifact-containment behavior of clipwright-frames so that
migrating the local _check_within_boundary helper to the shared
clipwright.pathpolicy.check_within_boundary preserves identical semantics.

Verification aspects:
  (A) clipwright.pathpolicy.check_within_boundary — direct unit tests:
      A-1: target within base_dir -> no exception
      A-2: target outside base_dir -> PATH_NOT_ALLOWED
      A-3: path traversal via ../ -> PATH_NOT_ALLOWED
      A-4: kind label appears in error message and/or hint
      A-5: target in a subdirectory of base (any OS separator) -> OK
  (B) extract_frames public API — containment enforcement:
      B-1: normal output_dir -> ok=True (frames.otio/.json always within out_dir)
      B-2: scene_timeline outside output_dir -> ok=True (read-only input, not boundary-checked)
      B-3: artifact paths in ok=True envelope resolve within output_dir (regression guard)
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.pathpolicy import check_within_boundary
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_frames.schemas import ExtractFramesOptions

# ===========================================================================
# Helpers
# ===========================================================================

FPS = 30.0


def _make_media_info(
    path: str = "/fake/video.mp4",
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
) -> MediaInfo:
    """Minimal MediaInfo with one video and one audio stream."""
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=duration_sec * rate, rate=rate),
        streams=[
            StreamInfo(index=0, codec_type="video", codec_name="h264"),
            StreamInfo(index=1, codec_type="audio", codec_name="aac"),
        ],
        bit_rate=8_000_000,
    )


def _opts(**kwargs: object) -> ExtractFramesOptions:
    """Build ExtractFramesOptions with sensible defaults."""
    defaults: dict[str, object] = {"mode": "interval", "interval_sec": 2.0}
    defaults.update(kwargs)
    return ExtractFramesOptions.model_validate(defaults)


def _fake_run(cmd: list[str], timeout: float = 60.0) -> CompletedProcess[str]:
    """Simulate a successful ffmpeg run that produces no output files."""
    return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


def _make_scene_timeline(path: Path) -> None:
    """Write a minimal OTIO file with one scene_boundary marker at 2.0s."""
    tl = otio.schema.Timeline(name="scene_test")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    tl.tracks.append(v1)
    rt = otio.opentime.RationalTime(2.0 * FPS, FPS)
    dur = otio.opentime.RationalTime(0.0, FPS)
    marker = otio.schema.Marker(
        name="scene_boundary",
        marked_range=otio.opentime.TimeRange(start_time=rt, duration=dur),
    )
    marker.metadata["clipwright"] = {"kind": "scene_boundary"}
    v1.markers.append(marker)
    otio.adapters.write_to_file(tl, str(path))


# ===========================================================================
# (A) Direct unit tests: clipwright.pathpolicy.check_within_boundary
# ===========================================================================


class TestCheckWithinBoundaryCore:
    """Direct unit tests for the core containment helper used by frames.

    These tests are stable before and after the extract.py refactor because they
    target the canonical implementation in clipwright.pathpolicy directly.
    The same helper backs both scene and frames artifact containment.
    """

    def test_a1_target_within_base_dir_ok(self, tmp_path: Path) -> None:
        """A-1: target resolves within base_dir -> no exception."""
        base_dir = tmp_path / "out"
        base_dir.mkdir()
        target = base_dir / "frames.otio"
        # Must not raise for a target that lives directly inside base_dir.
        check_within_boundary(base_dir, target, "frame")

    def test_a2_target_outside_base_dir_raises(self, tmp_path: Path) -> None:
        """A-2: target resolves outside base_dir -> PATH_NOT_ALLOWED."""
        base_dir = tmp_path / "out"
        base_dir.mkdir()
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        outside = other_dir / "frames.otio"
        with pytest.raises(ClipwrightError) as exc_info:
            check_within_boundary(base_dir, outside, "frame")
        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_a3_path_traversal_raises(self, tmp_path: Path) -> None:
        """A-3: ../ traversal that resolves outside base_dir -> PATH_NOT_ALLOWED."""
        base_dir = tmp_path / "out"
        base_dir.mkdir()
        # out/../escape.txt resolves to tmp_path/escape.txt, outside base_dir.
        traversal = base_dir / ".." / "escape.txt"
        with pytest.raises(ClipwrightError) as exc_info:
            check_within_boundary(base_dir, traversal, "frame")
        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_a4_kind_label_in_error(self, tmp_path: Path) -> None:
        """A-4: kind label appears in the error message or hint."""
        base_dir = tmp_path / "out"
        base_dir.mkdir()
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        outside = other_dir / "frames.json"
        with pytest.raises(ClipwrightError) as exc_info:
            check_within_boundary(base_dir, outside, "frame output")
        err = exc_info.value
        combined = f"{err.message} {err.hint}"
        assert "frame output" in combined or "frame" in combined.lower()

    def test_a5_subdirectory_within_base_ok(self, tmp_path: Path) -> None:
        """A-5: target in a subdirectory of base_dir -> OK (separator-aware).

        On Windows str(path) uses backslashes; _is_within in pathpolicy checks
        both '/' and '\\\\' so nested targets are always accepted regardless of OS.
        """
        base_dir = tmp_path / "out"
        sub_dir = base_dir / "sub"
        sub_dir.mkdir(parents=True)
        target = sub_dir / "frame_00001.jpg"
        # Must not raise for a target nested inside base_dir.
        check_within_boundary(base_dir, target, "frame")


# ===========================================================================
# (B) extract_frames public API — containment enforcement
# ===========================================================================


class TestExtractFramesArtifactContainment:
    """Integration tests: extract_frames enforces artifact containment.

    Subprocess calls and inspect_media are mocked; only the boundary-check
    behavior and envelope outcome are exercised here.
    """

    def test_b1_normal_output_dir_succeeds(self, tmp_path: Path) -> None:
        """B-1: normal output_dir -> ok=True.

        frames.otio and frames.json are always written directly inside output_dir,
        so the boundary check (step 6 in extract.py) never fires in the normal case.
        """
        from clipwright_frames.extract import extract_frames

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch(
                "clipwright_frames.extract.resolve_tool", return_value="/usr/bin/ffmpeg"
            ),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
        ):
            result = extract_frames(media, str(out_dir), _opts())

        assert result.ok is True
        # Confirm PATH_NOT_ALLOWED was never triggered.
        if result.error is not None:
            assert result.error.code != ErrorCode.PATH_NOT_ALLOWED

    def test_b2_scene_timeline_outside_output_dir_succeeds(
        self, tmp_path: Path
    ) -> None:
        """B-2: scene_timeline outside output_dir -> ok=True.

        scene_timeline is a read-only input consumed before the output artifacts
        are written.  Only the output artifacts (frames.otio/.json) are subject to
        the containment check — read-only inputs are explicitly exempt (SR M-1).
        """
        from clipwright_frames.extract import extract_frames

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        timelines_dir = tmp_path / "timelines"
        timelines_dir.mkdir()

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        # scene_timeline lives in a completely separate directory from out_dir.
        scene_otio_path = timelines_dir / "scenes.otio"
        _make_scene_timeline(scene_otio_path)

        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch(
                "clipwright_frames.extract.resolve_tool", return_value="/usr/bin/ffmpeg"
            ),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
        ):
            result = extract_frames(
                media,
                str(out_dir),
                _opts(
                    mode="scene",
                    scene_timeline=str(scene_otio_path),
                    scene_sample="boundary",
                ),
            )

        # scene_timeline outside output_dir must not trigger containment error.
        assert result.ok is True

    def test_b3_artifact_paths_resolve_within_output_dir(self, tmp_path: Path) -> None:
        """B-3: frames.otio and frames.json artifact paths resolve within output_dir.

        Verifies the containment guarantee at the envelope surface: every path
        listed in result.artifacts must resolve inside output_dir.
        This test does not rely on internal function names and remains stable
        after the _check_within_boundary refactor.
        """
        from clipwright_frames.extract import extract_frames

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        media_info = _make_media_info(path=media, duration_sec=10.0)

        out_dir_resolved = str(out_dir.resolve())

        with (
            patch("clipwright_frames.extract.inspect_media", return_value=media_info),
            patch(
                "clipwright_frames.extract.resolve_tool", return_value="/usr/bin/ffmpeg"
            ),
            patch("clipwright_frames.extract.run", side_effect=_fake_run),
        ):
            result = extract_frames(media, str(out_dir), _opts())

        assert result.ok is True
        assert result.artifacts, "Expected at least one artifact in the envelope"
        for artifact in result.artifacts:
            art_resolved = str(Path(artifact.path).resolve())
            assert art_resolved.startswith(out_dir_resolved), (
                f"Artifact path {art_resolved!r} escapes output_dir "
                f"{out_dir_resolved!r}."
            )
