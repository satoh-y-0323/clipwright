"""test_pathpolicy.py — Unit tests for clipwright.pathpolicy.

Target module: clipwright/pathpolicy.py.

Test groups:
  A. validate_source_file: exists / missing / leaf-symlink / intermediate-dir-symlink /
     basename-only error (SR-L-1)
  B. check_output_not_source: same-path / different / multi-source / separator-variants /
     resolve→absolute→str fallback
  C. media_ref_for_otio: within-tree / outside / nested / backslash-input
  D. check_media_ref: relative-within / relative-outside / abs-ok / abs-missing /
     abs-symlink / mixed (DC-AM-003) / nonexistent-relative-ok (CR-L-8) /
     double-OSError-warning (CR-L-7 / SR-L-3)
  E. check_within_boundary: within / outside / kind label propagated /
     double-OSError-warning (CR-L-7 / SR-L-3)
  F. ADR-PP-2 ordering: islink-before-resolve for leaf and intermediate symlinks
  G. check_media_ref relative symlink (SR-M-2): within-boundary leaf /
     outside-boundary / intermediate-dir symlink
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clipwright.errors import ClipwrightError, ErrorCode

# clipwright.pathpolicy is implemented and available (ADR-PP-1 / ADR-PP-2).
from clipwright.pathpolicy import (
    check_media_ref,
    check_output_not_source,
    check_timeline_source_matches,
    check_within_boundary,
    media_ref_for_otio,
    validate_source_file,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_file(path: Path, content: bytes = b"dummy") -> Path:
    """Create a regular file with dummy content and return its Path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _try_symlink(link: Path, target: Path) -> None:
    """Create a symlink; skip the test if the OS refuses (Windows privilege)."""
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip(
            "Cannot create symlinks on this system (requires elevated privileges)"
        )


# ---------------------------------------------------------------------------
# Symlink availability detection (for pytest.mark.skipif at collection time)
# ---------------------------------------------------------------------------


def _probe_symlink_support() -> bool:
    """Return True when the runtime environment allows symlink creation.

    Executed once at module import (collection) time so pytest.mark.skipif
    can reference the result.  Uses a TemporaryDirectory that is cleaned up
    before any test runs.
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


# ===========================================================================
# A. validate_source_file
# ===========================================================================


class TestValidateSourceFile:
    """validate_source_file(path: str) -> None"""

    def test_existing_regular_file_ok(self, tmp_path: Path) -> None:
        """An existing regular file passes without raising."""
        f = _write_file(tmp_path / "video.mp4")
        # Should not raise
        validate_source_file(str(f))

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        """A path that does not exist raises FILE_NOT_FOUND."""
        nonexistent = str(tmp_path / "no_such_file.mp4")

        with pytest.raises(ClipwrightError) as exc_info:
            validate_source_file(nonexistent)

        assert exc_info.value.code == ErrorCode.FILE_NOT_FOUND

    def test_leaf_symlink_raises_path_not_allowed(self, tmp_path: Path) -> None:
        """A leaf symlink (even pointing to a real file) is rejected with PATH_NOT_ALLOWED.

        ADR-PP-2: symlink rejection covers full path components.
        """
        real_file = _write_file(tmp_path / "real.mp4")
        link = tmp_path / "link.mp4"
        _try_symlink(link, real_file)

        with pytest.raises(ClipwrightError) as exc_info:
            validate_source_file(str(link))

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_intermediate_dir_symlink_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """A symlink in an intermediate directory component is rejected.

        ADR-PP-2: all path components are checked, not just the leaf.
        """
        real_dir = tmp_path / "real_dir"
        real_dir.mkdir()
        real_file = _write_file(real_dir / "video.mp4")

        sym_dir = tmp_path / "sym_dir"
        _try_symlink(sym_dir, real_dir)

        with pytest.raises(ClipwrightError) as exc_info:
            validate_source_file(str(sym_dir / real_file.name))

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_symlink_error_message_basename_only(self, tmp_path: Path) -> None:
        """SR-L-1: symlink rejection exposes only the basename, not the full path.

        CWE-209 guard: full filesystem paths must not leak through error messages.
        The error message must contain the filename for identification but must not
        expose the full absolute path (e.g. C:\\Users\\...\\link.mp4).
        """
        real_file = _write_file(tmp_path / "real.mp4")
        link = tmp_path / "link.mp4"
        _try_symlink(link, real_file)

        with pytest.raises(ClipwrightError) as exc_info:
            validate_source_file(str(link))

        error_message = exc_info.value.message
        # Filename component must appear for identification
        assert link.name in error_message
        # Full absolute path must NOT appear (CWE-209)
        assert str(link) not in error_message


# ===========================================================================
# B. check_output_not_source
# ===========================================================================


class TestCheckOutputNotSource:
    """check_output_not_source(output: Path, sources: Iterable[str]) -> None"""

    def test_different_path_ok(self, tmp_path: Path) -> None:
        """Output path distinct from all sources does not raise."""
        src = _write_file(tmp_path / "input.mp4")
        out = tmp_path / "output.otio"

        # Should not raise
        check_output_not_source(out, [str(src)])

    def test_output_equal_to_source_raises(self, tmp_path: Path) -> None:
        """output resolving to the same path as a source raises PATH_NOT_ALLOWED."""
        src = _write_file(tmp_path / "media.mp4")
        # output == source (same absolute path)
        out = src

        with pytest.raises(ClipwrightError) as exc_info:
            check_output_not_source(out, [str(src)])

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_output_matches_one_of_multiple_sources_raises(
        self, tmp_path: Path
    ) -> None:
        """PATH_NOT_ALLOWED when output matches any one source in a multi-source list."""
        src_a = _write_file(tmp_path / "a.mp4")
        src_b = _write_file(tmp_path / "b.mp4")
        out = src_b  # output resolves to src_b

        with pytest.raises(ClipwrightError) as exc_info:
            check_output_not_source(out, [str(src_a), str(src_b)])

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_output_distinct_from_all_multiple_sources_ok(self, tmp_path: Path) -> None:
        """No exception when output differs from every source in a multi-source list."""
        src_a = _write_file(tmp_path / "a.mp4")
        src_b = _write_file(tmp_path / "b.mp4")
        out = tmp_path / "result.otio"

        check_output_not_source(out, [str(src_a), str(src_b)])

    def test_windows_backslash_source_equal_output_raises(self, tmp_path: Path) -> None:
        """Path written with backslash separators resolves equal to the output.

        Exercises the resolve→absolute→str fallback for Windows-style paths.
        """
        src = _write_file(tmp_path / "media.mp4")
        # Construct a Windows-style path string (backslash) for the same file
        win_style_src = str(src).replace("/", "\\")
        out = src

        with pytest.raises(ClipwrightError) as exc_info:
            check_output_not_source(out, [win_style_src])

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_resolve_absolute_str_fallback_distinct_path_ok(
        self, tmp_path: Path
    ) -> None:
        """resolve→absolute→str fallback: a logically different path never raises.

        Confirms the three-step canonicalisation does not produce spurious collisions.
        """
        src = _write_file(tmp_path / "src.mp4")
        # Represent the OUTPUT using forward slashes on any OS
        out = Path(str(tmp_path / "output.otio").replace("\\", "/"))

        check_output_not_source(out, [str(src)])


# ===========================================================================
# C. media_ref_for_otio
# ===========================================================================


class TestMediaRefForOtio:
    """media_ref_for_otio(source: str | Path, otio_dir: Path) -> str"""

    def test_source_within_otio_dir_returns_relative_posix(
        self, tmp_path: Path
    ) -> None:
        """Source under the otio_dir tree → relative POSIX path."""
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        src = _write_file(otio_dir / "media" / "clip.mp4")

        ref = media_ref_for_otio(str(src), otio_dir)

        # Must be a relative POSIX path (forward slashes, no leading slash)
        assert not Path(ref).is_absolute()
        assert "/" in ref or ref == Path(ref).name
        assert "\\" not in ref

    def test_source_outside_otio_dir_returns_absolute(self, tmp_path: Path) -> None:
        """Source outside the otio_dir tree → absolute path string."""
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        src = _write_file(tmp_path / "elsewhere" / "clip.mp4")

        ref = media_ref_for_otio(str(src), otio_dir)

        assert Path(ref).is_absolute()

    def test_source_in_sibling_dir_returns_absolute(self, tmp_path: Path) -> None:
        """Source in a sibling directory (not under otio_dir) → absolute."""
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        sibling = tmp_path / "raw_footage"
        src = _write_file(sibling / "take1.mp4")

        ref = media_ref_for_otio(src, otio_dir)

        assert Path(ref).is_absolute()

    def test_source_directly_in_otio_dir_returns_filename_only(
        self, tmp_path: Path
    ) -> None:
        """Source at the root of otio_dir → single filename (no subdirectory)."""
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        src = _write_file(otio_dir / "main.mp4")

        ref = media_ref_for_otio(str(src), otio_dir)

        # Relative path; no directory separator prefix expected
        assert not Path(ref).is_absolute()
        assert Path(ref).name == "main.mp4"

    def test_source_with_backslash_within_otio_dir_returns_posix(
        self, tmp_path: Path
    ) -> None:
        """Windows backslash source path within otio_dir → relative POSIX (no backslashes)."""
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        src = _write_file(otio_dir / "sub" / "clip.mp4")
        # Simulate Windows-style path string passed in
        win_src = str(src).replace("/", "\\")

        ref = media_ref_for_otio(win_src, otio_dir)

        assert not Path(ref).is_absolute()
        assert "\\" not in ref


# ===========================================================================
# D. check_media_ref
# ===========================================================================


class TestCheckMediaRef:
    """check_media_ref(ref: str, otio_dir: Path, kind: str) -> None"""

    def test_relative_ref_within_tree_ok(self, tmp_path: Path) -> None:
        """A relative reference that resolves inside the otio_dir tree is accepted."""
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        _write_file(otio_dir / "media" / "clip.mp4")

        # Should not raise
        check_media_ref("media/clip.mp4", otio_dir, kind="media")

    def test_relative_ref_outside_tree_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """A relative reference using ../ to escape the otio_dir raises PATH_NOT_ALLOWED.

        CWE-22 guard preserved (§3 ADR-PP-1).
        """
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        _write_file(tmp_path / "secret.mp4")  # outside the project tree

        with pytest.raises(ClipwrightError) as exc_info:
            check_media_ref("../secret.mp4", otio_dir, kind="media")

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_absolute_ref_existing_regular_file_ok(self, tmp_path: Path) -> None:
        """An absolute reference to an existing regular file is accepted (absolute escape hatch)."""
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        external = _write_file(tmp_path / "external" / "footage.mp4")

        # Should not raise — absolute path + real file
        check_media_ref(str(external), otio_dir, kind="media")

    def test_absolute_ref_nonexistent_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """An absolute reference to a non-existent path raises PATH_NOT_ALLOWED."""
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        ghost = str(tmp_path / "no_such.mp4")

        with pytest.raises(ClipwrightError) as exc_info:
            check_media_ref(ghost, otio_dir, kind="media")

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_absolute_ref_symlink_raises_path_not_allowed(self, tmp_path: Path) -> None:
        """An absolute reference pointing to a symlink raises PATH_NOT_ALLOWED (CWE-59)."""
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        real_file = _write_file(tmp_path / "real.mp4")
        link = tmp_path / "linked.mp4"
        _try_symlink(link, real_file)

        with pytest.raises(ClipwrightError) as exc_info:
            check_media_ref(str(link), otio_dir, kind="media")

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_kind_is_included_in_error_message(self, tmp_path: Path) -> None:
        """The 'kind' argument label (e.g. 'subtitle') appears in the error message."""
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()

        with pytest.raises(ClipwrightError) as exc_info:
            check_media_ref("../outside.srt", otio_dir, kind="subtitle")

        assert "subtitle" in exc_info.value.message or "subtitle" in exc_info.value.hint

    # DC-AM-003: mixed relative + absolute references in one OTIO are both validated

    def test_mixed_refs_both_valid_dc_am_003(self, tmp_path: Path) -> None:
        """DC-AM-003: relative and absolute references in the same OTIO are both accepted.

        Ensures check_media_ref handles mixed-ref OTIOs consistently.
        """
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        _write_file(otio_dir / "local.mp4")
        external = _write_file(tmp_path / "external" / "remote.mp4")

        # Both calls must succeed — one relative, one absolute
        check_media_ref("local.mp4", otio_dir, kind="media")
        check_media_ref(str(external), otio_dir, kind="media")

    def test_mixed_refs_invalid_absolute_dc_am_003(self, tmp_path: Path) -> None:
        """DC-AM-003: relative valid + absolute invalid → absolute raises PATH_NOT_ALLOWED.

        Validates that absolute validation is not skipped when relative refs are present.
        """
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        _write_file(otio_dir / "local.mp4")
        ghost = str(tmp_path / "ghost.mp4")  # does not exist

        # Relative is fine
        check_media_ref("local.mp4", otio_dir, kind="media")
        # Absolute non-existent raises
        with pytest.raises(ClipwrightError) as exc_info:
            check_media_ref(ghost, otio_dir, kind="media")

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_relative_ref_nonexistent_within_boundary_ok(self, tmp_path: Path) -> None:
        """CR-L-8: relative ref that does not yet exist but is within boundary is accepted.

        Existence is the caller's responsibility for relative references.
        Only boundary containment is checked (asymmetric contract vs. absolute refs).
        Regression guard: the implementation must not add an existence check to this branch.
        """
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        # Intentionally do NOT create the referenced file

        # Should not raise — boundary-only check, no existence check for relative refs
        check_media_ref("not_yet_created/clip.mp4", otio_dir, kind="media")

    def test_relative_ref_double_oserror_emits_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CR-L-7 / SR-L-3: double OSError on relative ref boundary check emits warnings.warn.

        When both resolve() and absolute() raise OSError, the boundary check cannot
        proceed.  Rather than silently passing (which may hide path injection attempts),
        a UserWarning must be emitted so callers are aware the guard was skipped.
        """
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()

        def _raise_os(self: object, *args: object, **kwargs: object) -> object:
            raise OSError("mocked unresolvable path")

        monkeypatch.setattr(Path, "resolve", _raise_os)
        monkeypatch.setattr(Path, "absolute", _raise_os)

        with pytest.warns(Warning):
            check_media_ref("some/ref.mp4", otio_dir, kind="media")


# ===========================================================================
# E. check_within_boundary
# ===========================================================================


class TestCheckWithinBoundary:
    """check_within_boundary(base_dir: Path, target: Path, kind: str) -> None"""

    def test_target_within_base_dir_ok(self, tmp_path: Path) -> None:
        """A target inside base_dir does not raise."""
        base = tmp_path / "artifacts"
        base.mkdir()
        target = base / "scene_001.jpg"

        check_within_boundary(base, target, kind="frame")

    def test_target_outside_base_dir_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """A target outside base_dir raises PATH_NOT_ALLOWED."""
        base = tmp_path / "artifacts"
        base.mkdir()
        outside = tmp_path / "sensitive" / "data.bin"

        with pytest.raises(ClipwrightError) as exc_info:
            check_within_boundary(base, outside, kind="frame")

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_target_path_traversal_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """A traversal attempt (artifacts/../escape) is caught as PATH_NOT_ALLOWED."""
        base = tmp_path / "artifacts"
        base.mkdir()
        # Normalised, this escapes the boundary
        traversal = base / ".." / "escape.bin"

        with pytest.raises(ClipwrightError) as exc_info:
            check_within_boundary(base, traversal, kind="scene")

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_kind_label_propagated_in_error(self, tmp_path: Path) -> None:
        """The kind label appears in the error message or hint for diagnostics."""
        base = tmp_path / "out"
        base.mkdir()
        outside = tmp_path / "other" / "file.jpg"

        with pytest.raises(ClipwrightError) as exc_info:
            check_within_boundary(base, outside, kind="thumbnail")

        assert (
            "thumbnail" in exc_info.value.message or "thumbnail" in exc_info.value.hint
        )

    def test_double_oserror_emits_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CR-L-7 / SR-L-3: double OSError on boundary check emits warnings.warn.

        When both resolve() and absolute() raise OSError in check_within_boundary,
        the containment check cannot proceed.  A UserWarning must be emitted rather
        than silently skipping (silent pass may allow path injection to go undetected).
        """
        base = tmp_path / "artifacts"
        base.mkdir()
        target = base / "frame.jpg"

        def _raise_os(self: object, *args: object, **kwargs: object) -> object:
            raise OSError("mocked unresolvable path")

        monkeypatch.setattr(Path, "resolve", _raise_os)
        monkeypatch.setattr(Path, "absolute", _raise_os)

        with pytest.warns(Warning):
            check_within_boundary(base, target, kind="frame")


# ===========================================================================
# F. ADR-PP-2: islink-before-resolve ordering
# ===========================================================================


class TestIsLinkBeforeResolveOrdering:
    """ADR-PP-2: islink check must precede resolve() to prevent CWE-59 bypass.

    If resolve() were called first, Path.is_symlink() on the resolved path
    returns False (the target is a real file), causing the symlink to slip through.
    These tests lock the correct evaluation order.
    """

    def test_leaf_symlink_to_real_file_rejected_before_resolve(
        self, tmp_path: Path
    ) -> None:
        """Leaf symlink pointing to an existing valid file must still be rejected.

        Regression guard: if resolve() ran first, the path would look like a real
        file and is_symlink() on the resolved path would return False → bypass.
        The test DID NOT RAISE failure exposes that ordering bug.
        """
        real_file = _write_file(tmp_path / "legit.mp4")
        link = tmp_path / "evil_link.mp4"
        _try_symlink(link, real_file)

        with pytest.raises(ClipwrightError) as exc_info:
            validate_source_file(str(link))

        # Symlink must be caught (PATH_NOT_ALLOWED) before resolve() converts it
        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_intermediate_dir_symlink_rejected_before_resolve(
        self, tmp_path: Path
    ) -> None:
        """Intermediate directory symlink must be caught even when leaf file is real.

        If resolve() ran first, the full resolved path is a real file inside a real
        directory — is_symlink() on it returns False. The test DID NOT RAISE
        or wrong-code failure exposes the ordering regression.
        """
        real_dir = tmp_path / "real_dir"
        real_dir.mkdir()
        real_file = _write_file(real_dir / "clip.mp4")

        sym_dir = tmp_path / "sym_dir"
        _try_symlink(sym_dir, real_dir)

        with pytest.raises(ClipwrightError) as exc_info:
            validate_source_file(str(sym_dir / real_file.name))

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    def test_check_media_ref_leaf_symlink_rejected_before_resolve(
        self, tmp_path: Path
    ) -> None:
        """check_media_ref must also apply islink-before-resolve for absolute refs.

        Ensures the ordering rule is not scoped to validate_source_file alone.
        """
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        real_file = _write_file(tmp_path / "source.mp4")
        link = tmp_path / "link_source.mp4"
        _try_symlink(link, real_file)

        with pytest.raises(ClipwrightError) as exc_info:
            check_media_ref(str(link), otio_dir, kind="media")

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED


# ===========================================================================
# G. check_media_ref relative symlink (SR-M-2)
# ===========================================================================


class TestCheckMediaRefRelativeSymlink:
    """SR-M-2: check_media_ref relative branch must apply _has_symlink_component.

    The relative path branch currently performs boundary-containment checking only.
    It must also reject paths whose components are symlinks, using the same
    islink-before-resolve ordering as the absolute branch (ADR-PP-2).

    Tests are marked skipif when symlink creation is not supported on this system
    (WinError 1314); _try_symlink provides a runtime safety net.
    """

    @_skip_no_symlinks
    def test_relative_leaf_symlink_within_boundary_raises(self, tmp_path: Path) -> None:
        """SR-M-2: a within-boundary leaf symlink via relative ref is rejected.

        The symlink resolves inside the otio_dir tree, so the boundary check passes.
        However _has_symlink_component must fire before resolve() and reject the path.
        Regression marker: if this raises nothing, the symlink check is missing from
        the relative branch.
        """
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        real_file = _write_file(otio_dir / "real.mp4")
        link = otio_dir / "link.mp4"
        _try_symlink(link, real_file)

        with pytest.raises(ClipwrightError) as exc_info:
            check_media_ref("link.mp4", otio_dir, kind="media")

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    @_skip_no_symlinks
    def test_relative_leaf_symlink_outside_boundary_raises(
        self, tmp_path: Path
    ) -> None:
        """SR-M-2: a within-directory symlink pointing outside boundary is rejected as symlink.

        The boundary check would also catch the escape via resolve(), but the symlink
        guard must fire first (islink-before-resolve ordering contract, ADR-PP-2).
        Both mechanisms must result in PATH_NOT_ALLOWED.
        """
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        external_file = _write_file(tmp_path / "external.mp4")
        link = otio_dir / "escape.mp4"
        _try_symlink(link, external_file)

        with pytest.raises(ClipwrightError) as exc_info:
            check_media_ref("escape.mp4", otio_dir, kind="media")

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED

    @_skip_no_symlinks
    def test_relative_intermediate_dir_symlink_raises(self, tmp_path: Path) -> None:
        """SR-M-2: relative ref via a symlinked intermediate directory is rejected.

        All path components — not just the leaf — must be checked for symlinks
        (ADR-PP-2).  The symlink here is on a subdirectory, not the final file.
        """
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        real_subdir = otio_dir / "media"
        real_subdir.mkdir()
        _write_file(real_subdir / "clip.mp4")
        sym_subdir = otio_dir / "sym_media"
        _try_symlink(sym_subdir, real_subdir)

        with pytest.raises(ClipwrightError) as exc_info:
            check_media_ref("sym_media/clip.mp4", otio_dir, kind="media")

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED


# ===========================================================================
# H. check_timeline_source_matches
# ===========================================================================


class TestCheckTimelineSourceMatches:
    """check_timeline_source_matches(target_url, media_path, otio_dir) -> None

    Verifies that an OTIO ExternalReference.target_url and the tool's input
    media_path resolve to the same file.  Relative target_url is joined onto
    otio_dir (NOT the CWD) — spec5 D1 fix (ADR-D1-1).
    """

    def test_relative_target_url_matching_media_in_otio_dir_ok(
        self, tmp_path: Path
    ) -> None:
        """T1: relative target_url (basename) + media co-located in otio_dir → no exception.

        Basic match: OTIO stores a relative reference to the media file in the
        same directory as the timeline.
        """
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        media = _write_file(otio_dir / "clip.mp4")

        # Should not raise
        check_timeline_source_matches("clip.mp4", media, otio_dir)

    def test_relative_target_url_matching_cwd_independent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T2: same as T1 but CWD changed to an unrelated dir → no exception.

        Core regression guard for spec5 D1 fix (ADR-D1-1): relative target_url
        must be resolved against otio_dir, NOT the current working directory.
        A timeline stored outside CWD must still match its own media.
        """
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        media = _write_file(otio_dir / "clip.mp4")

        # Change CWD to a completely different directory
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        monkeypatch.chdir(other_dir)

        # Must not raise even though CWD != otio_dir
        check_timeline_source_matches("clip.mp4", media, otio_dir)

    def test_relative_target_url_different_basename_raises(
        self, tmp_path: Path
    ) -> None:
        """T3: relative target_url resolving to a different file raises INVALID_INPUT."""
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        media = _write_file(otio_dir / "clip.mp4")
        _write_file(otio_dir / "other.mp4")

        with pytest.raises(ClipwrightError) as exc_info:
            check_timeline_source_matches("other.mp4", media, otio_dir)

        assert exc_info.value.code == ErrorCode.INVALID_INPUT
        # SR-R-001: CWE-209 regression guard — input filenames must not leak into error message.
        assert "clip" not in exc_info.value.message and "other" not in exc_info.value.message
        # G-2: canonical error message must be the fixed sentinel string.
        assert "Timeline source file does not match input media." in exc_info.value.message

    def test_absolute_target_url_equal_to_media_ok(self, tmp_path: Path) -> None:
        """T4: absolute target_url equal to media_path → no exception."""
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        media = _write_file(tmp_path / "footage" / "clip.mp4")

        # Absolute target_url string of the same media file
        check_timeline_source_matches(str(media), media, otio_dir)

    def test_absolute_target_url_different_from_media_raises(
        self, tmp_path: Path
    ) -> None:
        """T5: absolute target_url pointing to a different file raises INVALID_INPUT."""
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        media = _write_file(tmp_path / "footage" / "clip.mp4")
        other = _write_file(tmp_path / "footage" / "other.mp4")

        with pytest.raises(ClipwrightError) as exc_info:
            check_timeline_source_matches(str(other), media, otio_dir)

        assert exc_info.value.code == ErrorCode.INVALID_INPUT
        # SR-R-001: CWE-209 regression guard — input filenames must not leak into error message.
        assert "clip" not in exc_info.value.message and "other" not in exc_info.value.message
        # G-2: canonical error message must be the fixed sentinel string.
        assert "Timeline source file does not match input media." in exc_info.value.message

    def test_resolve_oserror_falls_back_to_absolute_match_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T6: Path.resolve raises OSError → _canon falls back to absolute() → match succeeds.

        Exercises the resolve→absolute fallback in _canon().  When resolve()
        raises OSError for both the target_url path and media_path, _canon
        uses absolute() instead.  With an absolute target_url equal to
        media_path, both calls return the same string and no exception is raised.
        """
        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        media = _write_file(otio_dir / "clip.mp4")

        def _raise_os(self: object, *args: object, **kwargs: object) -> object:
            raise OSError("mocked unresolvable path")

        monkeypatch.setattr(Path, "resolve", _raise_os)

        # absolute target_url equal to media_path → absolute() fallback gives same string
        check_timeline_source_matches(str(media), media, otio_dir)
