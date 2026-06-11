"""test_project.py — Tests for project.py.

Target (§7 / §13.2 DC-AM-007):
- init_project(project_dir, name, force=False):
    - Creates dir / sources / artifacts / outputs
    - Generates clipwright.json manifest
    - Generates empty timeline.otio with V1/A1 tracks (§13.1 DC-AS-003)
    - Raises ClipwrightError(PROJECT_EXISTS) if project already exists
    - force=True is non-destructive:
        - Only regenerates manifest and ensures directory existence
        - Does not delete or overwrite existing sources/artifacts/outputs/timeline.otio
        - Generates empty timeline only when timeline.otio is missing
- find_project(start_dir): Walks up ancestor directories looking for clipwright.json
- load_manifest / save_manifest(project_dir, manifest): round-trip serialization

Additional Red tests (review M-3 / F-03 / L-4=F-08):
- [M-3] save_manifest uses atomic write (temp → os.replace)
- [F-03] find_project raises an error when start_dir is not a directory
- [L-4/F-08] The hint on find_project not-found does not duplicate the full start_dir
"""

from __future__ import annotations

import json
import os
import unittest.mock as mock
from pathlib import Path

import pytest

# --- Import (project.py not yet implemented → ImportError expected → Red) ---
from clipwright.project import (
    find_project,
    init_project,
    load_manifest,
    save_manifest,
)

# ===========================================================================
# init_project — success path
# ===========================================================================


class TestInitProjectSuccess:
    """init_project success path (directory and file creation)."""

    def test_creates_project_dir(self, tmp_project: Path) -> None:
        """project_dir is created when it does not exist."""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        assert proj.is_dir()

    def test_creates_subdirs(self, tmp_project: Path) -> None:
        """sources / artifacts / outputs sub-directories are created."""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        assert (proj / "sources").is_dir()
        assert (proj / "artifacts").is_dir()
        assert (proj / "outputs").is_dir()

    def test_creates_manifest(self, tmp_project: Path) -> None:
        """clipwright.json manifest is generated."""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        assert (proj / "clipwright.json").is_file()

    def test_manifest_schema_version(self, tmp_project: Path) -> None:
        """Manifest contains schema_version."""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        manifest = json.loads((proj / "clipwright.json").read_text(encoding="utf-8"))
        assert "schema_version" in manifest

    def test_manifest_name(self, tmp_project: Path) -> None:
        """Manifest name field matches the argument."""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        manifest = json.loads((proj / "clipwright.json").read_text(encoding="utf-8"))
        assert manifest["name"] == "myproject"

    def test_manifest_has_clipwright_version(self, tmp_project: Path) -> None:
        """Manifest contains clipwright_version."""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        manifest = json.loads((proj / "clipwright.json").read_text(encoding="utf-8"))
        assert "clipwright_version" in manifest

    def test_manifest_has_created_at(self, tmp_project: Path) -> None:
        """Manifest contains a created_at timestamp."""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        manifest = json.loads((proj / "clipwright.json").read_text(encoding="utf-8"))
        assert "created_at" in manifest

    def test_creates_timeline_otio(self, tmp_project: Path) -> None:
        """timeline.otio is generated."""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        assert (proj / "timeline.otio").is_file()

    def test_timeline_has_v1_track(self, tmp_project: Path) -> None:
        """timeline.otio contains a V1 (Video) track (§13.1 DC-AS-003 / §13.5)."""
        import opentimelineio as otio

        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        tl = otio.adapters.read_from_file(str(proj / "timeline.otio"))
        video_tracks = [t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video]
        assert len(video_tracks) >= 1
        assert video_tracks[0].name == "V1"

    def test_timeline_has_a1_track(self, tmp_project: Path) -> None:
        """timeline.otio contains an A1 (Audio) track (§13.1 DC-AS-003 / §13.5)."""
        import opentimelineio as otio

        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        tl = otio.adapters.read_from_file(str(proj / "timeline.otio"))
        audio_tracks = [t for t in tl.tracks if t.kind == otio.schema.TrackKind.Audio]
        assert len(audio_tracks) >= 1
        assert audio_tracks[0].name == "A1"

    def test_timeline_track_order(self, tmp_project: Path) -> None:
        """Track order is [V1(Video), A1(Audio)] (§13.5 DC-AS-001 re)."""
        import opentimelineio as otio

        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        tl = otio.adapters.read_from_file(str(proj / "timeline.otio"))
        tracks = list(tl.tracks)
        assert len(tracks) == 2
        assert tracks[0].kind == otio.schema.TrackKind.Video
        assert tracks[1].kind == otio.schema.TrackKind.Audio

    def test_timeline_is_empty(self, tmp_project: Path) -> None:
        """Initial timeline.otio contains no clips or markers."""
        import opentimelineio as otio

        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        tl = otio.adapters.read_from_file(str(proj / "timeline.otio"))
        for track in tl.tracks:
            assert len(track) == 0


# ===========================================================================
# init_project — PROJECT_EXISTS error
# ===========================================================================


class TestInitProjectExists:
    """init to an existing project raises PROJECT_EXISTS."""

    def test_raises_project_exists(self, tmp_project: Path) -> None:
        """Calling init on an existing project without force raises PROJECT_EXISTS."""
        from clipwright.errors import ClipwrightError, ErrorCode

        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        with pytest.raises(ClipwrightError) as exc_info:
            init_project(str(proj), name="myproject")
        assert exc_info.value.code == ErrorCode.PROJECT_EXISTS

    def test_error_has_hint(self, tmp_project: Path) -> None:
        """PROJECT_EXISTS error hint is non-empty."""
        from clipwright.errors import ClipwrightError

        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        with pytest.raises(ClipwrightError) as exc_info:
            init_project(str(proj), name="myproject")
        assert exc_info.value.hint


# ===========================================================================
# init_project — force=True (non-destructive semantics) DC-AM-007
# ===========================================================================


class TestInitProjectForce:
    """force=True is non-destructive (§13.2 DC-AM-007)."""

    def test_force_does_not_raise(self, tmp_project: Path) -> None:
        """force=True does not raise an exception on an existing project."""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        # Should complete without exception
        init_project(str(proj), name="myproject", force=True)

    def test_force_regenerates_manifest(self, tmp_project: Path) -> None:
        """force=True regenerates the manifest (reflects configuration changes)."""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        # Regenerate with a different name via force=True
        init_project(str(proj), name="renamed", force=True)
        manifest = json.loads((proj / "clipwright.json").read_text(encoding="utf-8"))
        assert manifest["name"] == "renamed"

    def test_force_preserves_sources_content(self, tmp_project: Path) -> None:
        """force=True does not delete files inside sources/ (non-destructive)."""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        sentinel = proj / "sources" / "keep.txt"
        sentinel.write_text("preserve me", encoding="utf-8")
        init_project(str(proj), name="myproject", force=True)
        assert sentinel.is_file()
        assert sentinel.read_text(encoding="utf-8") == "preserve me"

    def test_force_preserves_artifacts_content(self, tmp_project: Path) -> None:
        """force=True does not delete files inside artifacts/ (non-destructive)."""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        sentinel = proj / "artifacts" / "keep.json"
        sentinel.write_text("{}", encoding="utf-8")
        init_project(str(proj), name="myproject", force=True)
        assert sentinel.is_file()

    def test_force_preserves_outputs_content(self, tmp_project: Path) -> None:
        """force=True does not delete files inside outputs/ (non-destructive)."""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        sentinel = proj / "outputs" / "keep.mp4"
        sentinel.write_bytes(b"\x00\x01\x02")
        init_project(str(proj), name="myproject", force=True)
        assert sentinel.is_file()

    def test_force_preserves_existing_timeline(self, tmp_project: Path) -> None:
        """force=True does not overwrite or delete the existing timeline.otio."""

        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")

        # Record mtime after modifying timeline.otio
        timeline_path = proj / "timeline.otio"
        original_mtime = timeline_path.stat().st_mtime

        import time

        time.sleep(0.05)  # Workaround for OS mtime resolution

        init_project(str(proj), name="myproject", force=True)

        # mtime unchanged means the file was not overwritten
        new_mtime = timeline_path.stat().st_mtime
        assert new_mtime == original_mtime

    def test_force_creates_missing_timeline(self, tmp_project: Path) -> None:
        """force=True generates an empty timeline only when timeline.otio is missing."""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")

        # Manually remove timeline.otio
        timeline_path = proj / "timeline.otio"
        timeline_path.unlink()
        assert not timeline_path.exists()

        init_project(str(proj), name="myproject", force=True)
        assert timeline_path.is_file()

    def test_force_ensures_subdirs_exist(self, tmp_project: Path) -> None:
        """force=True re-creates deleted sub-directories (directory guarantee)."""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")

        # Manually remove a sub-directory
        import shutil

        shutil.rmtree(proj / "sources")

        init_project(str(proj), name="myproject", force=True)
        assert (proj / "sources").is_dir()


# ===========================================================================
# find_project — ancestor directory search
# ===========================================================================


class TestFindProject:
    """find_project: walks up ancestor directories looking for clipwright.json."""

    def test_find_from_project_root(self, tmp_project: Path) -> None:
        """Returns the project root when searching from the root itself."""
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        found = find_project(str(proj))
        assert Path(found) == proj

    def test_find_from_subdir(self, tmp_project: Path) -> None:
        """Walks up from a sub-directory inside the project and finds the root."""
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        subdir = proj / "sources" / "nested"
        subdir.mkdir(parents=True)
        found = find_project(str(subdir))
        assert Path(found) == proj

    def test_raises_not_found(self, tmp_project: Path) -> None:
        """Raises PROJECT_NOT_FOUND from a directory with no project."""
        from clipwright.errors import ClipwrightError, ErrorCode

        empty_dir = tmp_project / "no_project"
        empty_dir.mkdir()
        with pytest.raises(ClipwrightError) as exc_info:
            find_project(str(empty_dir))
        assert exc_info.value.code == ErrorCode.PROJECT_NOT_FOUND

    def test_returns_str(self, tmp_project: Path) -> None:
        """Return type is str (easy to embed in ToolResult)."""
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        found = find_project(str(proj))
        assert isinstance(found, str)


# ===========================================================================
# load_manifest / save_manifest — round-trip serialisation
# ===========================================================================


class TestManifestRoundtrip:
    """load_manifest / save_manifest round-trip serialisation."""

    def test_load_returns_dict(self, tmp_project: Path) -> None:
        """load_manifest returns a dict."""
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        manifest = load_manifest(str(proj))
        assert isinstance(manifest, dict)

    def test_load_contains_name(self, tmp_project: Path) -> None:
        """load_manifest return value contains name."""
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        manifest = load_manifest(str(proj))
        assert manifest["name"] == "proj"

    def test_save_and_load_roundtrip(self, tmp_project: Path) -> None:
        """Values survive a save_manifest → load_manifest round-trip."""
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        original = load_manifest(str(proj))
        original["settings"] = {"custom_key": "custom_value"}
        save_manifest(str(proj), original)
        reloaded = load_manifest(str(proj))
        assert reloaded["settings"]["custom_key"] == "custom_value"

    def test_load_raises_not_found_for_missing_manifest(
        self, tmp_project: Path
    ) -> None:
        """Raises PROJECT_NOT_FOUND for a directory that has no clipwright.json."""
        from clipwright.errors import ClipwrightError, ErrorCode

        empty_dir = tmp_project / "no_manifest"
        empty_dir.mkdir()
        with pytest.raises(ClipwrightError) as exc_info:
            load_manifest(str(empty_dir))
        assert exc_info.value.code == ErrorCode.PROJECT_NOT_FOUND

    def test_save_writes_valid_json(self, tmp_project: Path) -> None:
        """The file written by save_manifest is valid JSON."""
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        manifest = load_manifest(str(proj))
        manifest["extra"] = 42
        save_manifest(str(proj), manifest)
        raw = (proj / "clipwright.json").read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert parsed["extra"] == 42


# ===========================================================================
# [M-3] save_manifest — atomic write (temp → os.replace)
# ===========================================================================


class TestSaveManifestAtomic:
    """[M-3] save_manifest uses atomic write via temp → os.replace.

    Matches the same pattern as save_timeline to prevent clipwright.json
    corruption from an interrupted write.
    """

    def test_save_manifest_uses_os_replace(self, tmp_project: Path) -> None:
        """Confirms save_manifest calls os.replace via monkeypatch.

        The current implementation uses a direct write_text overwrite,
        so this test shows os.replace is NOT called (Red: feature not implemented).
        After implementation, os.replace in clipwright.project must be called once.
        """
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        manifest = load_manifest(str(proj))

        replace_calls: list[tuple[str, str]] = []

        original_replace = os.replace

        def recording_replace(src: str, dst: str) -> None:
            replace_calls.append((src, dst))
            original_replace(src, dst)

        # project.py is expected to use "import os" then call os.replace
        # patch via mock.patch.object on the os module's replace
        with mock.patch.object(os, "replace", side_effect=recording_replace):
            save_manifest(str(proj), manifest)

        # Atomic write must call os.replace at least once
        assert len(replace_calls) >= 1, (
            "save_manifest did not call os.replace. "
            "The temp → os.replace atomic write is not implemented (M-3)."
        )

    def test_save_manifest_temp_in_same_dir(self, tmp_project: Path) -> None:
        """The atomic write temp file is created in the same directory.

        os.replace is not atomic across file systems, so the temp file
        must reside in the same directory as the manifest.
        """
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        manifest = load_manifest(str(proj))

        replace_calls: list[tuple[str, str]] = []

        original_replace = os.replace

        def recording_replace(src: str, dst: str) -> None:
            replace_calls.append((src, dst))
            original_replace(src, dst)

        with mock.patch.object(os, "replace", side_effect=recording_replace):
            save_manifest(str(proj), manifest)

        assert len(replace_calls) >= 1, (
            "save_manifest did not call os.replace (M-3 not implemented)."
        )
        src_path, dst_path = replace_calls[0]
        # temp and dest must be in the same directory
        assert Path(src_path).parent == Path(dst_path).parent, (
            f"temp ({src_path}) and dest ({dst_path}) are in different directories. "
            "This may result in a cross-device atomic write."
        )

    def test_save_manifest_result_is_valid_json_after_atomic_write(
        self, tmp_project: Path
    ) -> None:
        """clipwright.json is valid JSON after the atomic write.

        This success-path test overlaps with test_save_writes_valid_json but
        is kept explicitly to pin the M-3 regression contract after implementation.
        """
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        manifest = load_manifest(str(proj))
        manifest["m3_marker"] = "atomic"
        save_manifest(str(proj), manifest)

        raw = (proj / "clipwright.json").read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert parsed["m3_marker"] == "atomic"

    def test_save_manifest_overwrites_existing_without_corruption(
        self, tmp_project: Path
    ) -> None:
        """Overwriting an existing manifest does not corrupt the file.

        After multiple save_manifest calls the last call's content is intact.
        """
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")

        for i in range(3):
            manifest = load_manifest(str(proj))
            manifest["counter"] = i
            save_manifest(str(proj), manifest)

        final = load_manifest(str(proj))
        assert final["counter"] == 2  # last value
        # Verify no corruption: parseable as JSON
        raw = (proj / "clipwright.json").read_text(encoding="utf-8")
        assert json.loads(raw) == final


# ===========================================================================
# [F-03] find_project — is_dir() check
# ===========================================================================


class TestFindProjectValidation:
    """[F-03] find_project validates that start_dir is a directory.

    Addresses security review F-03. When a file path or non-existent path
    is passed as start_dir, the file system must not be traversed pointlessly.
    """

    def test_raises_error_when_start_dir_is_file(self, tmp_project: Path) -> None:
        """Raises an error when start_dir is a file.

        The current implementation calls Path(start_dir).resolve() without an
        is_dir() check, so passing a file starts the search from its parent
        directory — unintended behaviour.
        This test expects the appropriate error code
        (PROJECT_NOT_FOUND or INVALID_INPUT) to be returned
        (Red: is_dir() check not yet implemented).
        """
        from clipwright.errors import ClipwrightError, ErrorCode

        # Create a file and pass it as start_dir
        file_path = tmp_project / "not_a_dir.txt"
        file_path.write_text("I am a file", encoding="utf-8")

        with pytest.raises(ClipwrightError) as exc_info:
            find_project(str(file_path))

        assert exc_info.value.code in (
            ErrorCode.PROJECT_NOT_FOUND,
            ErrorCode.INVALID_INPUT,
        ), (
            f"Expected PROJECT_NOT_FOUND/INVALID_INPUT when start_dir is a file, "
            f"but got code={exc_info.value.code}."
        )

    def test_raises_invalid_input_when_start_dir_is_file(
        self, tmp_project: Path
    ) -> None:
        """INVALID_INPUT is the preferred code when start_dir is a file.

        This is the code closest to the F-03 fix intention.
        Confirmed when the implementation chooses INVALID_INPUT.
        """
        from clipwright.errors import ClipwrightError, ErrorCode

        file_path = tmp_project / "not_a_dir.txt"
        file_path.write_text("I am a file", encoding="utf-8")

        with pytest.raises(ClipwrightError) as exc_info:
            find_project(str(file_path))

        # With an is_dir() check, INVALID_INPUT is returned (implementation dependent)
        # Without the check, PROJECT_NOT_FOUND or no exception is raised (incorrect)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT, (
            "Expected INVALID_INPUT when start_dir is a file. "
            "Add an is_dir() check to find_project (F-03 not implemented)."
        )

    def test_init_project_success_not_regressed(self, tmp_project: Path) -> None:
        """init_project success path does not regress after F-03 fix.

        Adding an is_dir() check to find_project must not break
        init_project with a valid directory path.
        """
        proj = tmp_project / "valid_proj"
        # Normal path: directory → no error
        init_project(str(proj), name="valid_proj")
        found = find_project(str(proj))
        assert Path(found) == proj

    def test_find_project_with_valid_dir_not_regressed(self, tmp_project: Path) -> None:
        """The existing find_project behaviour does not regress after F-03 fix.

        A valid existing directory still returns PROJECT_NOT_FOUND.
        """
        from clipwright.errors import ClipwrightError, ErrorCode

        empty_dir = tmp_project / "empty"
        empty_dir.mkdir()

        with pytest.raises(ClipwrightError) as exc_info:
            find_project(str(empty_dir))

        assert exc_info.value.code == ErrorCode.PROJECT_NOT_FOUND


# ===========================================================================
# [L-4 / F-08] find_project — hint path duplication and oversized path mitigation
# ===========================================================================


class TestFindProjectHintQuality:
    """[L-4/F-08] find_project not-found hint does not duplicate the full start_dir.

    Addresses code review L-4 and security review F-08.
    The hint contains only the "next step" guidance; path information belongs
    in message only. Pins that hint does not grow with a long start_dir.
    """

    def test_hint_does_not_contain_full_start_dir(self, tmp_project: Path) -> None:
        """PROJECT_NOT_FOUND hint does not contain the full start_dir path.

        The current implementation embeds start_dir in the hint, so this
        test fails (Red: L-4/F-08 not yet fixed).
        """
        from clipwright.errors import ClipwrightError

        empty_dir = tmp_project / "no_proj"
        empty_dir.mkdir()
        start_dir_str = str(empty_dir)

        with pytest.raises(ClipwrightError) as exc_info:
            find_project(start_dir_str)

        hint = exc_info.value.hint
        # hint must not contain the full path
        assert start_dir_str not in hint, (
            f"hint contains the full path '{start_dir_str}'. "
            "hint must contain only the next-step guidance, not path info (L-4/F-08)."
        )

    def test_message_contains_start_dir(self, tmp_project: Path) -> None:
        """PROJECT_NOT_FOUND message contains start_dir.

        After removing the path from hint, the message side must still carry it.
        """
        from clipwright.errors import ClipwrightError

        empty_dir = tmp_project / "no_proj2"
        empty_dir.mkdir()

        with pytest.raises(ClipwrightError) as exc_info:
            find_project(str(empty_dir))

        message = exc_info.value.message
        assert str(empty_dir) in message or empty_dir.name in message, (
            "message does not contain path information. "
            "Path should be in message, not in hint."
        )

    def test_hint_and_message_do_not_both_contain_full_path(
        self, tmp_project: Path
    ) -> None:
        """The same full path does not appear in both hint and message.

        L-4: Including identical information in both hint and message is redundant.
        """
        from clipwright.errors import ClipwrightError

        empty_dir = tmp_project / "no_proj3"
        empty_dir.mkdir()
        start_dir_str = str(empty_dir)

        with pytest.raises(ClipwrightError) as exc_info:
            find_project(start_dir_str)

        hint = exc_info.value.hint
        message = exc_info.value.message

        hint_has_full_path = start_dir_str in hint
        message_has_full_path = start_dir_str in message

        assert not (hint_has_full_path and message_has_full_path), (
            f"Both hint and message contain the full path '{start_dir_str}'. "
            "Keep the full path in message only and remove it from hint (L-4/F-08)."
        )

    def test_hint_length_bounded_with_long_path(self, tmp_project: Path) -> None:
        """hint does not grow when a very long start_dir is supplied.

        F-08: Confirms that a maliciously long path does not inflate the hint.
        hint must contain only the next-step guidance, not echo the input.

        Directory names are kept short to stay within Windows MAX_PATH,
        while the total path is made long enough via deep nesting.
        """
        from clipwright.errors import ClipwrightError

        # Use a few levels of nesting; the existing tmp_path prefix is already long
        nested = tmp_project / "a" / "b" / "c" / "no_project_here"
        nested.mkdir(parents=True)
        start_dir_str = str(nested)

        # Confirm hint does not include the full input path
        with pytest.raises(ClipwrightError) as exc_info:
            find_project(start_dir_str)

        hint = exc_info.value.hint
        # hint must not contain the full path (F-08 fails if not fixed)
        assert start_dir_str not in hint, (
            f"hint contains the input full path '{start_dir_str}'. "
            "hint should contain only the leaf directory name or no path at all (F-08)."
        )

    def test_hint_contains_actionable_guidance(self, tmp_project: Path) -> None:
        """PROJECT_NOT_FOUND hint mentions init_project.

        Confirms that next-step guidance (init_project) remains in hint
        after removing the path.
        """
        from clipwright.errors import ClipwrightError

        empty_dir = tmp_project / "no_proj4"
        empty_dir.mkdir()

        with pytest.raises(ClipwrightError) as exc_info:
            find_project(str(empty_dir))

        hint = exc_info.value.hint
        assert "init_project" in hint, (
            "hint does not mention 'init_project'. "
            "After removing the path, keep the next-step (init_project) in hint."
        )
