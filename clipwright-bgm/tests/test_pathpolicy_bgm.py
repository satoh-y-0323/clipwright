"""test_pathpolicy_bgm.py — Red-phase path-boundary migration tests for add_bgm.

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

All assertions that verify NEW behaviour will FAIL (Red phase) because the
implementation still uses the old boundary helpers.

Failure modes per test
~~~~~~~~~~~~~~~~~~~~~~
output_anywhere tests:
  _check_output_within_timeline_dir raises PATH_NOT_ALLOWED → ok=False
  → assert result["ok"] is True fails  (correct Red: feature not yet implemented)

external_bgm tests:
  _check_bgm_within_timeline_dir raises PATH_NOT_ALLOWED → ok=False
  → assert result["ok"] is True fails  (correct Red: feature not yet implemented)

media_ref_rule / co-location:
  Case A (bgm and output in the same dir): all old boundary checks pass,
  but current code stores str(bgm_path) = absolute; new code must store relative.
  → assert not Path(target_url).is_absolute() fails  (correct Red: rule not yet applied)

  Case B (bgm outside output dir): _check_bgm_within_timeline_dir fails first.
  → assert result["ok"] is True fails  (correct Red: boundary check not yet updated)

output_source_collision / output == bgm:
  Old code reaches output.exists() before checking output != bgm;
  bgm_file already exists → INVALID_INPUT ("Output file already exists").
  → assert error code == PATH_NOT_ALLOWED fails  (correct Red: check_output_not_source
    not yet extended to include bgm in sources list)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
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
    """New policy: output file may reside in a directory other than the timeline directory.

    Red: _check_output_within_timeline_dir currently enforces co-location and
    returns PATH_NOT_ALLOWED for output outside the timeline directory.
    """

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

        # RED: _check_output_within_timeline_dir currently returns PATH_NOT_ALLOWED
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

        # RED: _check_output_within_timeline_dir currently returns PATH_NOT_ALLOWED
        assert result["ok"] is True, (
            "output in a nested dir outside timeline directory must be allowed. "
            f"Got error: {result.get('error')}"
        )


# ===========================================================================
# 2. External bgm (outside timeline directory) is allowed
# ===========================================================================


class TestExternalBgmAllowed:
    """New policy: bgm can be any existing regular non-symlink file regardless of directory.

    Red: _check_bgm_within_timeline_dir currently enforces co-location and
    returns PATH_NOT_ALLOWED for bgm outside the timeline directory.
    """

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

        # RED: _check_bgm_within_timeline_dir currently returns PATH_NOT_ALLOWED
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

    Case A (bgm and output co-located): all OLD boundary checks pass,
    but current code stores str(bgm_path) which is absolute.
    Red failure: assertion `not Path(target_url).is_absolute()` fails.

    Case B (bgm outside output dir / outside timeline dir): OLD boundary check fails first.
    Red failure: assertion `result["ok"] is True` fails.
    """

    def test_bgm_colocated_with_output_stores_relative_posix_ref(
        self,
        tmp_path: Path,
        media_info_bgm: Any,
    ) -> None:
        """When bgm is under the output's parent dir, stored target_url must be a relative POSIX path.

        Red: current code stores str(bgm_path) which is absolute even when bgm is co-located.
        """
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
        # RED: current code stores absolute; new code must store relative POSIX
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
        """When bgm is outside the output's parent dir, stored target_url must be absolute with no ../ traversal.

        Red: _check_bgm_within_timeline_dir currently fails before reaching the storage step.
        """
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

        # RED: _check_bgm_within_timeline_dir currently returns PATH_NOT_ALLOWED
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
        """bgm in a subdir of output's parent dir → relative POSIX ref (no backslash, no ../).

        Red: _check_bgm_within_timeline_dir currently fails (bgm is outside timeline dir).
        """
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

        # RED: _check_bgm_within_timeline_dir fails (bgm is outside timeline dir = project_dir)
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

    Red: current code only compares output vs timeline (_same_path check).
    When output == bgm, the code reaches output.exists() and returns
    INVALID_INPUT ("Output file already exists") instead of PATH_NOT_ALLOWED.
    """

    def test_output_same_as_bgm_returns_path_not_allowed(
        self,
        tmp_path: Path,
    ) -> None:
        """When output path equals bgm path, PATH_NOT_ALLOWED must be returned.

        Red: current code returns INVALID_INPUT (output already exists) rather than
        PATH_NOT_ALLOWED (check_output_not_source does not yet cover bgm).
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
            output=str(
                bgm_file
            ),  # output == bgm — must be rejected as PATH_NOT_ALLOWED
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False
        # RED: current code returns INVALID_INPUT ("Output file already exists"),
        # not PATH_NOT_ALLOWED (check_output_not_source does not yet include bgm).
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
