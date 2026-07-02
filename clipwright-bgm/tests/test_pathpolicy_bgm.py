"""test_pathpolicy_bgm.py — Path-boundary policy tests for add_bgm.

These tests encode the NEW path-boundary policy that replaces
_check_bgm_within_timeline_dir(L281) and _check_output_within_timeline_dir(L309):

  1. output_anywhere:  output can be placed in any directory whose parent exists,
                       provided output != any source.
  2. external_bgm:     bgm can reference any existing regular non-symlink file,
                       including files outside the timeline directory.
  3. media_ref_rule:   stored OTIO target_url follows media_ref_for_otio:
                         - bgm under output's parent dir (otio_dir) → relative POSIX
                         - bgm outside otio_dir               → absolute (no ../ traversal)
  4. output_source_collision: output == bgm → PATH_NOT_ALLOWED
                              (check_output_not_source covers bgm, not just timeline).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.otio_utils import load_timeline, save_timeline

from clipwright_bgm.bgm import add_bgm
from clipwright_bgm.schemas import BgmOptions

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_simple_timeline() -> otio.schema.Timeline:
    """Return a minimal V1+A1 timeline with no clips."""
    tl = otio.schema.Timeline(name="test_timeline")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(v1)
    tl.tracks.append(a1)
    return tl


def _write_timeline(tl: otio.schema.Timeline, path: Path) -> None:
    save_timeline(tl, str(path))


def _probe_symlink_support() -> bool:
    """Return True when the runtime environment allows symlink creation.

    Executed once at module import (collection) time so pytest.mark.skipif
    can reference the result.  Mirrors core tests/test_pathpolicy.py.
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


def _bgm_target_url(output_path: Path) -> str:
    """Return the target_url of the first kind=='bgm' clip in the saved OTIO."""
    tl = load_timeline(str(output_path))
    for track in tl.tracks:
        if track.kind == otio.schema.TrackKind.Audio:
            for item in track:
                if (
                    isinstance(item, otio.schema.Clip)
                    and item.metadata.get("clipwright", {}).get("kind") == "bgm"
                ):
                    ref = item.media_reference
                    if isinstance(ref, otio.schema.ExternalReference):
                        return ref.target_url
    return ""


# ===========================================================================
# 1. output can be placed outside the timeline directory
# ===========================================================================


class TestOutputAnywhereAllowed:
    """New policy: output file may reside in a directory other than the timeline directory."""

    def test_output_in_separate_dir_succeeds(
        self,
        tmp_path: Path,
        media_info_bgm: Any,
    ) -> None:
        """add_bgm succeeds when output is written to a directory different from the timeline."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        bgm_file = project_dir / "bgm.mp3"
        bgm_file.write_bytes(b"dummy bgm")
        tl = _make_simple_timeline()
        timeline_path = project_dir / "timeline.otio"
        _write_timeline(tl, timeline_path)
        output_path = work_dir / "output.otio"  # outside timeline dir

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is True, (
            "output outside the timeline directory must be allowed (new policy). "
            f"Got error: {result.get('error')}"
        )

    def test_output_in_nested_subdir_outside_timeline_dir_succeeds(
        self,
        tmp_path: Path,
        media_info_bgm: Any,
    ) -> None:
        """add_bgm succeeds when output is in a nested subdirectory outside the timeline directory."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        artifacts_dir = tmp_path / "artifacts" / "bgm"
        artifacts_dir.mkdir(parents=True)

        bgm_file = project_dir / "bgm.mp3"
        bgm_file.write_bytes(b"dummy bgm")
        tl = _make_simple_timeline()
        timeline_path = project_dir / "timeline.otio"
        _write_timeline(tl, timeline_path)
        output_path = artifacts_dir / "output.otio"

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is True, (
            "output in a nested dir outside timeline directory must be allowed. "
            f"Got error: {result.get('error')}"
        )


# ===========================================================================
# 2. External bgm (outside timeline directory) is allowed
# ===========================================================================


class TestExternalBgmAllowed:
    """New policy: bgm can be any existing regular non-symlink file regardless of directory."""

    def test_bgm_in_external_dir_succeeds(
        self,
        tmp_path: Path,
        media_info_bgm: Any,
    ) -> None:
        """add_bgm succeeds when bgm is a real file in a directory outside the timeline directory."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        music_dir = tmp_path / "music"
        music_dir.mkdir()

        bgm_file = music_dir / "bgm.mp3"  # outside timeline dir
        bgm_file.write_bytes(b"dummy bgm")
        tl = _make_simple_timeline()
        timeline_path = project_dir / "timeline.otio"
        _write_timeline(tl, timeline_path)
        output_path = project_dir / "output.otio"

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is True, (
            "bgm file outside the timeline directory must be allowed (new policy). "
            f"Got error: {result.get('error')}"
        )

    def test_bgm_absent_external_returns_file_not_found(
        self,
        tmp_path: Path,
    ) -> None:
        """Missing external bgm must return FILE_NOT_FOUND (FILE_NOT_FOUND takes precedence).

        This test verifies that the error-precedence ordering is correct:
        existence check comes before any boundary check.
        """
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        music_dir = tmp_path / "music"
        music_dir.mkdir()

        tl = _make_simple_timeline()
        timeline_path = project_dir / "timeline.otio"
        _write_timeline(tl, timeline_path)
        output_path = project_dir / "output.otio"
        nonexistent_bgm = music_dir / "ghost.mp3"  # does not exist

        result = add_bgm(
            timeline=str(timeline_path),
            bgm=str(nonexistent_bgm),
            output=str(output_path),
            options=BgmOptions(volume_db=-6.0),
        )

        # FILE_NOT_FOUND must be returned regardless of bgm location
        assert result["ok"] is False
        assert result["error"]["code"] == "FILE_NOT_FOUND", (
            "Missing bgm must return FILE_NOT_FOUND before any path-boundary check."
        )


# ===========================================================================
# 3. Stored OTIO reference follows media_ref_for_otio rule
# ===========================================================================


class TestMediaRefForOtioRule:
    """New policy: stored OTIO target_url follows media_ref_for_otio:
    - bgm under output's parent dir (otio_dir) → relative POSIX path (no backslash, no ../)
    - bgm outside otio_dir                     → absolute path (no ../ traversal)
    """

    def test_bgm_colocated_with_output_stores_relative_posix_ref(
        self,
        tmp_path: Path,
        media_info_bgm: Any,
    ) -> None:
        """When bgm is under the output's parent dir, stored target_url must be a relative POSIX path."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        # All three files in the same dir (bgm under output's parent = project_dir)
        bgm_file = project_dir / "bgm.mp3"
        bgm_file.write_bytes(b"dummy bgm")
        tl = _make_simple_timeline()
        timeline_path = project_dir / "timeline.otio"
        _write_timeline(tl, timeline_path)
        output_path = project_dir / "output.otio"

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is True, f"Expected success, got: {result.get('error')}"
        target_url = _bgm_target_url(output_path)
        assert target_url, "BGM clip must have a target_url"
        assert not Path(target_url).is_absolute(), (
            f"BGM in otio_dir must be stored as relative ref, got: {target_url!r}"
        )
        assert "\\" not in target_url, (
            f"OTIO target_url must use POSIX separators (no backslash), got: {target_url!r}"
        )

    def test_external_bgm_outside_otio_dir_stores_absolute_posix_ref(
        self,
        tmp_path: Path,
        media_info_bgm: Any,
    ) -> None:
        """When bgm is outside the output's parent dir, stored target_url must be absolute with no ../ traversal."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        music_dir = tmp_path / "music"
        music_dir.mkdir()

        # bgm outside project_dir (= output parent dir)
        bgm_file = music_dir / "bgm.mp3"
        bgm_file.write_bytes(b"dummy bgm")
        tl = _make_simple_timeline()
        timeline_path = project_dir / "timeline.otio"
        _write_timeline(tl, timeline_path)
        output_path = project_dir / "output.otio"

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is True, f"Expected success, got: {result.get('error')}"
        target_url = _bgm_target_url(output_path)
        assert target_url, "BGM clip must have a target_url"
        # Must be absolute (bgm outside otio_dir must not produce ../ reference)
        assert Path(target_url).is_absolute(), (
            f"External bgm ref must be absolute (no relative traversal), got: {target_url!r}"
        )
        # Must not contain relative traversal components
        assert ".." not in target_url, (
            f"External bgm ref must not contain '..', got: {target_url!r}"
        )
        # Must use POSIX separators (ADR convention for OTIO target_url)
        assert "\\" not in target_url, (
            f"OTIO target_url must use forward slashes, got: {target_url!r}"
        )

    def test_bgm_inside_output_subdir_stores_relative_posix_ref(
        self,
        tmp_path: Path,
        media_info_bgm: Any,
    ) -> None:
        """bgm in a subdir of output's parent dir → relative POSIX ref (no backslash, no ../)."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        work_dir = tmp_path / "work"
        music_subdir = work_dir / "music"
        music_subdir.mkdir(parents=True)

        # bgm under work/music/ — output is work/output.otio, so otio_dir = work/
        # bgm IS under otio_dir → should be stored as relative "music/bgm.mp3"
        bgm_file = music_subdir / "bgm.mp3"
        bgm_file.write_bytes(b"dummy bgm")
        tl = _make_simple_timeline()
        timeline_path = project_dir / "timeline.otio"  # timeline in separate dir
        _write_timeline(tl, timeline_path)
        output_path = work_dir / "output.otio"

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is True, f"Expected success, got: {result.get('error')}"
        target_url = _bgm_target_url(output_path)
        assert target_url, "BGM clip must have a target_url"
        assert not Path(target_url).is_absolute(), (
            f"BGM under otio_dir subdir must have relative ref, got: {target_url!r}"
        )
        assert ".." not in target_url, (
            f"Relative ref must not contain '..' traversal, got: {target_url!r}"
        )
        assert "\\" not in target_url, (
            f"OTIO target_url must use forward slashes, got: {target_url!r}"
        )


# ===========================================================================
# 4. output == bgm is PATH_NOT_ALLOWED (check_output_not_source must cover bgm)
# ===========================================================================


class TestOutputBgmCollision:
    """New policy: output == bgm must return PATH_NOT_ALLOWED.

    check_output_not_source(output, [str(timeline_path), str(bgm_path)]) must include bgm
    in the sources list.
    """

    def test_output_same_as_bgm_returns_path_not_allowed(
        self,
        tmp_path: Path,
    ) -> None:
        """When output path equals bgm path, PATH_NOT_ALLOWED must be returned."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        bgm_file = project_dir / "bgm.mp3"
        bgm_file.write_bytes(b"dummy bgm")
        tl = _make_simple_timeline()
        timeline_path = project_dir / "timeline.otio"
        _write_timeline(tl, timeline_path)

        result = add_bgm(
            timeline=str(timeline_path),
            bgm=str(bgm_file),
            output=str(
                bgm_file
            ),  # output == bgm — must be rejected as PATH_NOT_ALLOWED
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "PATH_NOT_ALLOWED", (
            "output == bgm must return PATH_NOT_ALLOWED (check_output_not_source). "
            f"Got: {result['error']['code']!r}"
        )

    def test_output_same_as_timeline_still_returns_invalid_input_or_path_not_allowed(
        self,
        tmp_path: Path,
        media_info_bgm: Any,
    ) -> None:
        """output == timeline must remain rejected (existing contract maintained).

        This test verifies that the refactoring does not regress the output==timeline check.
        Acceptable error codes: INVALID_INPUT or PATH_NOT_ALLOWED (both indicate rejection).
        """
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        bgm_file = project_dir / "bgm.mp3"
        bgm_file.write_bytes(b"dummy bgm")
        tl = _make_simple_timeline()
        timeline_path = project_dir / "timeline.otio"
        _write_timeline(tl, timeline_path)

        result = add_bgm(
            timeline=str(timeline_path),
            bgm=str(bgm_file),
            output=str(timeline_path),  # output == timeline
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False
        assert result["error"]["code"] in ("INVALID_INPUT", "PATH_NOT_ALLOWED"), (
            f"output == timeline must be rejected. Got: {result['error']['code']!r}"
        )


# ===========================================================================
# 5. symlink source inputs are rejected (spec5 D7, CWE-59, ADR-PP-2)
# ===========================================================================
#
# Regression guard: add_bgm delegates source validation to
# clipwright.pathpolicy.validate_source_file (islink-before-resolve) rather
# than a raw Path.exists() check, so a symlinked timeline/bgm is rejected
# with PATH_NOT_ALLOWED instead of being silently followed and accepted.


class TestSymlinkSourceRejected:
    """timeline_path / bgm_path must be rejected with PATH_NOT_ALLOWED when they
    are (or contain) a symlink component, mirroring core validate_source_file /
    ADR-PP-2 and the wrap.py reference pattern (clipwright-wrap wrap.py L164-179).
    """

    @_skip_no_symlinks
    def test_symlinked_timeline_returns_path_not_allowed(
        self,
        tmp_path: Path,
        media_info_bgm: Any,
    ) -> None:
        """A timeline path that is a symlink to a real .otio file must be rejected."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        bgm_file = project_dir / "bgm.mp3"
        bgm_file.write_bytes(b"dummy bgm")

        real_timeline_path = project_dir / "real_timeline.otio"
        tl = _make_simple_timeline()
        _write_timeline(tl, real_timeline_path)

        symlinked_timeline_path = project_dir / "timeline_link.otio"
        _try_symlink(symlinked_timeline_path, real_timeline_path)

        output_path = project_dir / "output.otio"

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(symlinked_timeline_path),
                bgm=str(bgm_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is False, (
            "A symlinked timeline must be rejected, not silently followed. "
            f"Got: {result}"
        )
        assert result["error"]["code"] == "PATH_NOT_ALLOWED", (
            "Symlinked timeline must return PATH_NOT_ALLOWED (core pathpolicy "
            f"contract, ADR-PP-2). Got: {result['error']['code']!r}"
        )
        assert str(project_dir) not in result["error"]["message"], (
            "Symlinked-timeline error message must not expose the full "
            f"directory path (CWE-209). Got: {result['error']['message']!r}"
        )

    @_skip_no_symlinks
    def test_symlinked_bgm_returns_path_not_allowed(
        self,
        tmp_path: Path,
    ) -> None:
        """A bgm path that is a symlink to a real audio file must be rejected."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        real_bgm_path = project_dir / "real_bgm.m4a"
        real_bgm_path.write_bytes(b"dummy bgm audio")

        symlinked_bgm_path = project_dir / "bgm_link.m4a"
        _try_symlink(symlinked_bgm_path, real_bgm_path)

        tl = _make_simple_timeline()
        timeline_path = project_dir / "timeline.otio"
        _write_timeline(tl, timeline_path)
        output_path = project_dir / "output.otio"

        result = add_bgm(
            timeline=str(timeline_path),
            bgm=str(symlinked_bgm_path),
            output=str(output_path),
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False, (
            "A symlinked bgm file must be rejected, not silently followed. "
            f"Got: {result}"
        )
        assert result["error"]["code"] == "PATH_NOT_ALLOWED", (
            "Symlinked bgm must return PATH_NOT_ALLOWED (core pathpolicy "
            f"contract, ADR-PP-2). Got: {result['error']['code']!r}"
        )
        assert str(project_dir) not in result["error"]["message"], (
            "Symlinked-bgm error message must not expose the full "
            f"directory path (CWE-209). Got: {result['error']['message']!r}"
        )


# ===========================================================================
# 6. Missing-source basename-only regression (spec5 D7, CWE-209)
# ===========================================================================
#
# These pin down the exact FILE_NOT_FOUND message wording that must survive
# the switch to validate_source_file (basename-only re-wrap, no full path
# exposure). They act as regression guards for the impl-bgm Green step.


class TestMissingSourceBasenameMessage:
    """FILE_NOT_FOUND messages for missing timeline/bgm must stay basename-only."""

    def test_missing_timeline_returns_file_not_found_with_basename_message(
        self,
        tmp_path: Path,
    ) -> None:
        """timeline missing → FILE_NOT_FOUND / "Timeline file not found: <basename>"."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        bgm_file = project_dir / "bgm.mp3"
        bgm_file.write_bytes(b"dummy bgm")
        missing_timeline_path = project_dir / "no_such_timeline.otio"
        output_path = project_dir / "output.otio"

        result = add_bgm(
            timeline=str(missing_timeline_path),
            bgm=str(bgm_file),
            output=str(output_path),
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "FILE_NOT_FOUND"
        assert (
            result["error"]["message"]
            == f"Timeline file not found: {missing_timeline_path.name}"
        ), f"Got: {result['error']['message']!r}"
        assert str(project_dir) not in result["error"]["message"], (
            "Timeline missing message must not expose the full directory path "
            f"(CWE-209). Got: {result['error']['message']!r}"
        )

    def test_missing_bgm_returns_file_not_found_with_basename_message(
        self,
        tmp_path: Path,
    ) -> None:
        """bgm missing → FILE_NOT_FOUND / "BGM file not found: <basename>"."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        tl = _make_simple_timeline()
        timeline_path = project_dir / "timeline.otio"
        _write_timeline(tl, timeline_path)
        missing_bgm_path = project_dir / "no_such_bgm.mp3"
        output_path = project_dir / "output.otio"

        result = add_bgm(
            timeline=str(timeline_path),
            bgm=str(missing_bgm_path),
            output=str(output_path),
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "FILE_NOT_FOUND"
        assert (
            result["error"]["message"] == f"BGM file not found: {missing_bgm_path.name}"
        ), f"Got: {result['error']['message']!r}"
        assert str(project_dir) not in result["error"]["message"], (
            "BGM missing message must not expose the full directory path "
            f"(CWE-209). Got: {result['error']['message']!r}"
        )


# ===========================================================================
# 7. FILE_NOT_FOUND re-wrap must use 'from None' (spec5 D7, CWE-209)
# ===========================================================================
#
# security-review-report-20260703-014001.md F-2: message-only assertions
# cannot detect a regression from 'raise ... from None' to 'raise ... from
# exc', which would leak the original core ClipwrightError (containing the
# full caller-supplied path) via __cause__ to a future outer handler. These
# tests directly assert __cause__ is None, mirroring the established
# frames/wrap pattern (TestInputFileNotFoundCauseIsNone).


class TestFileNotFoundCauseIsNone:
    """The FILE_NOT_FOUND re-wrap for timeline/bgm in _add_bgm_inner must use
    'raise ... from None' so __cause__ is None (CWE-209).
    """

    def test_timeline_file_not_found_cause_is_none(
        self,
        tmp_path: Path,
        mocker: Any,
    ) -> None:
        """_add_bgm_inner must raise FILE_NOT_FOUND with __cause__ == None
        for a missing timeline.

        Regression guard: changing 'from None' to 'from exc' would fail this test.
        """
        from clipwright_bgm.bgm import _add_bgm_inner

        bgm_file = tmp_path / "bgm.mp3"
        bgm_file.write_bytes(b"dummy bgm")
        timeline_path = tmp_path / "timeline.otio"
        output_path = tmp_path / "output.otio"

        # Original exception simulates core's FILE_NOT_FOUND leaking a full path.
        original_exc = ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message="File not found: C:\\secret\\full\\path\\timeline.otio",
            hint="Specify a valid path.",
        )
        mocker.patch(
            "clipwright_bgm.bgm.validate_source_file",
            side_effect=original_exc,
        )

        try:
            _add_bgm_inner(str(timeline_path), str(bgm_file), str(output_path), None)
        except ClipwrightError as exc:
            assert exc.code == ErrorCode.FILE_NOT_FOUND
            assert exc.__cause__ is None, (
                "_add_bgm_inner must use 'raise ... from None' for the "
                f"timeline FILE_NOT_FOUND re-wrap; __cause__ is {exc.__cause__!r}"
            )
            assert "C:\\secret\\full\\path\\timeline.otio" not in exc.message
        else:
            pytest.fail("Expected ClipwrightError was not raised")

    def test_bgm_file_not_found_cause_is_none(
        self,
        tmp_path: Path,
        mocker: Any,
    ) -> None:
        """_add_bgm_inner must raise FILE_NOT_FOUND with __cause__ == None
        for a missing bgm file (the timeline check must pass first, so the
        mock only raises on the second validate_source_file call).

        Regression guard: changing 'from None' to 'from exc' would fail this test.
        """
        from clipwright_bgm.bgm import _add_bgm_inner

        timeline_path = tmp_path / "timeline.otio"
        tl = _make_simple_timeline()
        _write_timeline(tl, timeline_path)
        bgm_path = tmp_path / "bgm.mp3"
        output_path = tmp_path / "output.otio"

        # Original exception simulates core's FILE_NOT_FOUND leaking a full path.
        original_exc = ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message="File not found: C:\\secret\\full\\path\\bgm.mp3",
            hint="Specify a valid path.",
        )
        mocker.patch(
            "clipwright_bgm.bgm.validate_source_file",
            side_effect=[None, original_exc],
        )

        try:
            _add_bgm_inner(str(timeline_path), str(bgm_path), str(output_path), None)
        except ClipwrightError as exc:
            assert exc.code == ErrorCode.FILE_NOT_FOUND
            assert exc.__cause__ is None, (
                "_add_bgm_inner must use 'raise ... from None' for the "
                f"bgm FILE_NOT_FOUND re-wrap; __cause__ is {exc.__cause__!r}"
            )
            assert "C:\\secret\\full\\path\\bgm.mp3" not in exc.message
        else:
            pytest.fail("Expected ClipwrightError was not raised")
