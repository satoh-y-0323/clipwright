"""test_pathpolicy_scene.py — Regression tests: scene artifact containment.

Locks the artifact-containment behavior of clipwright-scene so that
migrating the local _check_within_boundary helper to the shared
clipwright.pathpolicy.check_within_boundary preserves identical semantics.

Verification aspects:
  (A) clipwright.pathpolicy.check_within_boundary — direct unit tests:
      A-1: target within base_dir -> no exception
      A-2: target outside base_dir -> PATH_NOT_ALLOWED
      A-3: path traversal via ../ -> PATH_NOT_ALLOWED
      A-4: kind label appears in error message and/or hint
      A-5: target in a subdirectory of base (any OS separator) -> OK
  (B) detect_scenes public API — containment enforcement:
      B-1: output in valid output_dir -> ok=True (normal path never triggers containment)
      B-2: timeline in the same directory as output -> ok=True (within boundary)
      B-3: timeline in a separate directory (outside output boundary) -> PATH_NOT_ALLOWED
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

from clipwright_scene.schemas import DetectScenesOptions

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


def _fake_run_no_scenes(cmd: list[str], timeout: float = 60.0) -> CompletedProcess[str]:
    """Simulate a successful ffmpeg scdet run that produces no scene boundaries."""
    return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


def _make_otio(path: Path) -> None:
    """Write a minimal OTIO file with one video track to path."""
    tl = otio.schema.Timeline(name="test")
    tl.tracks.append(otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video))
    otio.adapters.write_to_file(tl, str(path))


# ===========================================================================
# (A) Direct unit tests: clipwright.pathpolicy.check_within_boundary
# ===========================================================================


class TestCheckWithinBoundaryCore:
    """Direct unit tests for the core containment helper used by scene.

    These tests are stable before and after the detect.py refactor because they
    target the canonical implementation in clipwright.pathpolicy directly.
    """

    def test_a1_target_within_base_dir_ok(self, tmp_path: Path) -> None:
        """A-1: target resolves within base_dir -> no exception."""
        base_dir = tmp_path / "base"
        base_dir.mkdir()
        target = base_dir / "scene.otio"
        # Must not raise for a target that lives directly inside base_dir.
        check_within_boundary(base_dir, target, "output file")

    def test_a2_target_outside_base_dir_raises(self, tmp_path: Path) -> None:
        """A-2: target resolves outside base_dir -> PATH_NOT_ALLOWED."""
        base_dir = tmp_path / "base"
        base_dir.mkdir()
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        outside = other_dir / "scene.otio"
        with pytest.raises(ClipwrightError) as exc_info:
            check_within_boundary(base_dir, outside, "output file")
        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_a3_path_traversal_raises(self, tmp_path: Path) -> None:
        """A-3: ../ traversal that resolves outside base_dir -> PATH_NOT_ALLOWED."""
        base_dir = tmp_path / "base"
        base_dir.mkdir()
        # base/../escape.otio resolves to tmp_path/escape.otio, outside base_dir.
        traversal = base_dir / ".." / "escape.otio"
        with pytest.raises(ClipwrightError) as exc_info:
            check_within_boundary(base_dir, traversal, "timeline file")
        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_a4_kind_label_in_error(self, tmp_path: Path) -> None:
        """A-4: kind label appears in the error message or hint."""
        base_dir = tmp_path / "base"
        base_dir.mkdir()
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        outside = other_dir / "artifact.txt"
        with pytest.raises(ClipwrightError) as exc_info:
            check_within_boundary(base_dir, outside, "scene output")
        err = exc_info.value
        combined = f"{err.message} {err.hint}"
        # The kind label or a recognisable fragment must appear in the combined text.
        assert "scene output" in combined or "output" in combined.lower()

    def test_a5_subdirectory_within_base_ok(self, tmp_path: Path) -> None:
        """A-5: target in a subdirectory of base_dir -> OK (separator-aware).

        On Windows str(path) uses backslashes; _is_within in pathpolicy checks
        both '/' and '\\\\' so nested targets are always accepted regardless of OS.
        """
        base_dir = tmp_path / "base"
        sub_dir = base_dir / "sub"
        sub_dir.mkdir(parents=True)
        target = sub_dir / "scene.otio"
        # Must not raise for a target nested inside base_dir.
        check_within_boundary(base_dir, target, "output file")


# ===========================================================================
# (B) detect_scenes public API — containment enforcement
# ===========================================================================


class TestDetectScenesArtifactContainment:
    """Integration tests: detect_scenes enforces artifact containment.

    Subprocess calls and inspect_media are mocked; only the boundary-check
    behavior and envelope outcome are exercised here.
    """

    def test_b1_output_within_output_dir_succeeds(self, tmp_path: Path) -> None:
        """B-1: output in valid output_dir -> ok=True (no containment violation).

        The output path always resolves within its own parent directory, so the
        boundary check at step 1b should never fire in the normal case.
        """
        from clipwright_scene.detect import detect_scenes

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(out_dir / "scene.otio")
        media_info = _make_media_info(path=media)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.resolve_tool", return_value="/usr/bin/ffmpeg"
            ),
            patch("clipwright_scene.detect.run", side_effect=_fake_run_no_scenes),
        ):
            result = detect_scenes(media, output, DetectScenesOptions())

        assert result.ok is True
        # Confirm no PATH_NOT_ALLOWED was triggered.
        if result.error is not None:
            assert result.error.code != ErrorCode.PATH_NOT_ALLOWED

    def test_b2_timeline_in_same_dir_as_output_succeeds(self, tmp_path: Path) -> None:
        """B-2: optional timeline in the same directory as output -> ok=True.

        The timeline file is within the output_base boundary (same directory),
        so the timeline boundary check at step 5/17 must pass.
        """
        from clipwright_scene.detect import detect_scenes

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(out_dir / "scene.otio")
        # Timeline exists in the same directory as output.
        timeline_path = out_dir / "existing.otio"
        _make_otio(timeline_path)

        media_info = _make_media_info(path=media)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.resolve_tool", return_value="/usr/bin/ffmpeg"
            ),
            patch("clipwright_scene.detect.run", side_effect=_fake_run_no_scenes),
        ):
            result = detect_scenes(
                media, output, DetectScenesOptions(), timeline=str(timeline_path)
            )

        assert result.ok is True

    def test_b3_timeline_outside_output_dir_returns_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """B-3: timeline in a separate directory (outside boundary) -> PATH_NOT_ALLOWED.

        detect_scenes requires the optional timeline file to reside in the same
        directory as the output file (output_base).  A timeline located in any other
        directory violates the containment rule and must be rejected with
        PATH_NOT_ALLOWED, not silently accepted.

        This test uses a concrete directory layout without mocking the boundary check,
        so it locks the behavior at the public API level and remains stable after the
        internal _check_within_boundary is replaced by pathpolicy.check_within_boundary.
        """
        from clipwright_scene.detect import detect_scenes

        out_dir = tmp_path / "output"
        out_dir.mkdir()
        other_dir = tmp_path / "other"
        other_dir.mkdir()

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(out_dir / "scene.otio")
        # Timeline lives in a completely different directory — outside output_base.
        timeline_path = other_dir / "external.otio"
        _make_otio(timeline_path)

        media_info = _make_media_info(path=media)

        with (
            patch("clipwright_scene.detect.inspect_media", return_value=media_info),
            patch(
                "clipwright_scene.detect.resolve_tool", return_value="/usr/bin/ffmpeg"
            ),
            patch("clipwright_scene.detect.run", side_effect=_fake_run_no_scenes),
        ):
            result = detect_scenes(
                media, output, DetectScenesOptions(), timeline=str(timeline_path)
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.PATH_NOT_ALLOWED
