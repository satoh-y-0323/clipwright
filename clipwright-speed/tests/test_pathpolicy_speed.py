"""test_pathpolicy_speed.py — Path-boundary policy tests for speed output-placement.

Policy (impl-transform): the co-location constraint is removed from set_speed.
After impl-transform, output may be placed in any directory provided:
  - parent directory exists
  - output extension is .otio
  - output path does not resolve to the same file as the input timeline

Test groups:
  A. output in different directory from timeline → ok=True (new policy)
  B. output == timeline → PATH_NOT_ALLOWED
  C. DC-AM-003: mixed relative/absolute media refs preserved after round-trip
     when output resides outside the timeline directory
  D. preserved checks: .otio extension, parent dir existence, missing timeline
     (regression guards; expected Green)
"""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio
import pytest

from clipwright_speed.schemas import SetSpeedOptions
from clipwright_speed.speed import set_speed

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RATE = 24.0
_DURATION_SEC = 5.0


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
    tl = otio.schema.Timeline(name="speed_tl")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(v1)
    tl.tracks.append(a1)
    for i in range(n_clips):
        v1.append(_make_clip(f"clip{i}"))
    return tl


def _write_timeline(tl: otio.schema.Timeline, path: Path) -> None:
    otio.adapters.write_to_file(tl, str(path))


def _default_opts(speed: float = 2.0) -> SetSpeedOptions:
    return SetSpeedOptions(speed=speed)


# ---------------------------------------------------------------------------
# Symlink availability detection (for pytest.mark.skipif at collection time)
# ---------------------------------------------------------------------------
#
# Mirrors the canonical pattern in tests/test_pathpolicy.py (core package):
# probe symlink creation once at collection time so tests that require a
# symlink SKIP on hosts without the privilege (e.g. local Windows without
# Developer Mode) instead of failing, while still running on CI (3 OS).


def _probe_symlink_support() -> bool:
    """Return True when the runtime environment allows symlink creation."""
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


# ===========================================================================
# A. output in different directory from timeline → ok=True (new policy)
# ===========================================================================


class TestOutputOutsideTimelineDir:
    """After impl-transform, output may live outside the timeline directory."""

    def test_output_in_sibling_dir_allowed(self, tmp_path: Path) -> None:
        """Output in a sibling directory must succeed (removed co-location constraint)."""
        proj_dir = tmp_path / "project"
        work_dir = tmp_path / "work"
        proj_dir.mkdir()
        work_dir.mkdir()

        tl = _make_v1_timeline()
        tl_path = proj_dir / "timeline.otio"
        _write_timeline(tl, tl_path)

        output = work_dir / "out.otio"
        result = set_speed(str(tl_path), str(output), _default_opts())

        # New policy: output outside the timeline directory is allowed.
        assert result["ok"] is True, (
            f"Output in sibling dir must be allowed; got: {result.get('error')}"
        )

    def test_output_in_parent_dir_allowed(self, tmp_path: Path) -> None:
        """Output placed in the parent directory of the timeline must succeed."""
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()

        tl = _make_v1_timeline()
        tl_path = proj_dir / "timeline.otio"
        _write_timeline(tl, tl_path)

        # Output in the parent of proj_dir (outside proj_dir/)
        output = tmp_path / "out.otio"
        result = set_speed(str(tl_path), str(output), _default_opts())

        assert result["ok"] is True, (
            f"Output in parent dir must be allowed; got: {result.get('error')}"
        )

    def test_output_in_deeply_nested_external_dir_allowed(self, tmp_path: Path) -> None:
        """Output deeply nested under an unrelated directory must succeed."""
        src_dir = tmp_path / "src" / "footage"
        out_dir = tmp_path / "artifacts" / "speed" / "v1"
        src_dir.mkdir(parents=True)
        out_dir.mkdir(parents=True)

        tl = _make_v1_timeline()
        tl_path = src_dir / "timeline.otio"
        _write_timeline(tl, tl_path)

        output = out_dir / "fast.otio"
        result = set_speed(str(tl_path), str(output), _default_opts())

        assert result["ok"] is True, (
            f"Output in unrelated nested dir must be allowed; got: {result.get('error')}"
        )


# ===========================================================================
# B. output == timeline → PATH_NOT_ALLOWED
# ===========================================================================


class TestOutputEqualsSource:
    """check_output_not_source: output == timeline must return PATH_NOT_ALLOWED."""

    def test_output_equals_timeline_path_not_allowed(self, tmp_path: Path) -> None:
        """output path identical to timeline must return PATH_NOT_ALLOWED."""
        tl = _make_v1_timeline()
        tl_path = tmp_path / "timeline.otio"
        _write_timeline(tl, tl_path)

        result = set_speed(str(tl_path), str(tl_path), _default_opts())

        assert result["ok"] is False
        error = result.get("error") or {}
        assert error.get("code") == "PATH_NOT_ALLOWED", (
            f"output == timeline must return PATH_NOT_ALLOWED; "
            f"got {error.get('code')!r} (hint: {error.get('hint')!r})"
        )
        assert error.get("hint"), "hint must be non-empty"

    def test_output_equals_timeline_no_path_in_message(self, tmp_path: Path) -> None:
        """CWE-209: error message must not expose the full filesystem path."""
        tl = _make_v1_timeline()
        tl_path = tmp_path / "private" / "project.otio"
        tl_path.parent.mkdir(parents=True)
        _write_timeline(tl, tl_path)

        result = set_speed(str(tl_path), str(tl_path), _default_opts())

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
    """DC-AM-003: mixed relative/absolute media references must survive set_speed.

    set_speed is a transform tool: it loads a timeline, attaches LinearTimeWarp
    effects, and saves to a new path.  Media references (target_url strings) are
    NOT modified by set_speed; they must be written to the output file unchanged.
    """

    def test_absolute_url_preserved_after_set_speed(self, tmp_path: Path) -> None:
        """Absolute media reference in timeline survives set_speed unchanged."""
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
        v1.append(
            _make_clip("clip0", target_url=abs_url),
        )

        tl_path = proj_dir / "timeline.otio"
        _write_timeline(tl, tl_path)

        output = work_dir / "out.otio"
        result = set_speed(str(tl_path), str(output), _default_opts())

        assert result["ok"] is True, (
            f"set_speed with output outside timeline dir must succeed; "
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
        """Relative and absolute refs both survive set_speed when output is in a
        different directory from the timeline (DC-AM-003 core scenario).

        Layout:
          tmp_path/proj/timeline.otio  (relative ref 'clip0.mp4', absolute ref for clip1)
          tmp_path/work/out.otio       (output; outside proj/)
        """
        proj_dir = tmp_path / "proj"
        work_dir = tmp_path / "work"
        proj_dir.mkdir()
        work_dir.mkdir()

        rel_url = "clip0.mp4"  # relative URL stored as-is in OTIO
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

        output = work_dir / "out.otio"
        result = set_speed(str(tl_path), str(output), _default_opts())

        assert result["ok"] is True, (
            f"set_speed with mixed refs and output outside timeline dir must succeed; "
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
        """output with extension other than .otio must return INVALID_INPUT."""
        tl = _make_v1_timeline()
        tl_path = tmp_path / "timeline.otio"
        _write_timeline(tl, tl_path)

        result = set_speed(str(tl_path), str(tmp_path / "out.mp4"), _default_opts())

        assert result["ok"] is False
        assert (result.get("error") or {}).get("code") == "INVALID_INPUT"

    def test_missing_parent_dir_returns_file_not_found(self, tmp_path: Path) -> None:
        """output whose parent directory does not exist must return FILE_NOT_FOUND."""
        tl = _make_v1_timeline()
        tl_path = tmp_path / "timeline.otio"
        _write_timeline(tl, tl_path)

        output = tmp_path / "nonexistent_dir" / "out.otio"
        result = set_speed(str(tl_path), str(output), _default_opts())

        assert result["ok"] is False
        assert (result.get("error") or {}).get("code") == "FILE_NOT_FOUND"

    def test_missing_timeline_returns_file_not_found(self, tmp_path: Path) -> None:
        """Non-existent timeline must return FILE_NOT_FOUND."""
        missing = tmp_path / "does_not_exist.otio"
        output = tmp_path / "out.otio"

        result = set_speed(str(missing), str(output), _default_opts())

        assert result["ok"] is False
        assert (result.get("error") or {}).get("code") == "FILE_NOT_FOUND"


# ===========================================================================
# E. CWE-59: symlinked input timeline must be rejected
# ===========================================================================


class TestInputTimelineSymlinkRejection:
    """A symlinked input timeline is rejected with PATH_NOT_ALLOWED (CWE-59)."""

    @_skip_no_symlinks
    def test_symlinked_timeline_rejected_with_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        tl = _make_v1_timeline()
        real_path = tmp_path / "real.otio"
        _write_timeline(tl, real_path)
        link = tmp_path / "link.otio"
        _try_symlink(link, real_path)

        result = set_speed(str(link), str(tmp_path / "out.otio"), _default_opts())

        assert result["ok"] is False
        assert (result.get("error") or {}).get("code") == "PATH_NOT_ALLOWED"
