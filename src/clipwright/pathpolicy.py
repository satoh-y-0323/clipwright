"""pathpolicy.py — Cross-tool path safety primitives.

Centralises source validation, output-vs-source collision detection,
OTIO media reference construction, and containment guards.
All functions follow ADR-PP-1 (absolute escape hatch) and ADR-PP-2
(islink-before-resolve ordering, covering all path components).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from clipwright.errors import ClipwrightError, ErrorCode

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _has_symlink_component(path: Path) -> bool:
    """Return True if any component of *path* is a symlink.

    Walks from the leaf up to the filesystem root, calling is_symlink() on
    each step.  Must be called before resolve() to prevent CWE-59 bypass
    (ADR-PP-2): resolve() follows symlinks, so is_symlink() on the resolved
    path always returns False, masking the original symlink.
    """
    current = path
    while True:
        if current.is_symlink():
            return True
        parent = current.parent
        if parent == current:
            # Reached the filesystem root
            break
        current = parent
    return False


def _normalize_sep(path_str: str) -> Path:
    """Return a Path from *path_str*, normalising backslashes to forward slashes.

    Enables consistent behaviour on Windows-style path strings passed from
    AI agents or Windows-origin OTIO files, on any host OS.
    """
    return Path(path_str.replace("\\", "/"))


def _canon(p: Path) -> str:
    """Return a canonical string for path equality comparison.

    Three-stage fallback: resolve() -> absolute() -> str (SR L-1).
    Guards against network paths, long paths, and other OS conditions
    where resolve() may raise OSError.
    """
    try:
        return str(p.resolve())
    except OSError:
        try:
            return str(p.absolute())
        except OSError:
            return str(p)


def _is_within(target_str: str, base_str: str) -> bool:
    """Return True when *target_str* names a path under the *base_str* tree.

    Checks both / and \\ as directory separators so that cross-platform path
    strings stored in OTIO files are handled correctly.
    """
    return (
        target_str == base_str
        or target_str.startswith(base_str + "/")
        or target_str.startswith(base_str + "\\")
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_source_file(path: str) -> None:
    """Validate a tool's media-input argument: exists + regular file + no symlinks.

    Raises ClipwrightError(FILE_NOT_FOUND) when the path does not exist or
    is not a regular file.  Raises ClipwrightError(PATH_NOT_ALLOWED) when any
    path component (leaf or intermediate directory) is a symlink.

    ADR-PP-2: the islink check over all path components runs before resolve()
    so that symlinks are never silently followed.

    Promotion of media._validate_existing_file into the shared core API.

    Args:
        path: Input media file path string (forward or backslash separators).

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED when the path contains a symlink
            component.  FILE_NOT_FOUND when the path does not point to an
            existing regular file.
    """
    p = _normalize_sep(path)

    # ADR-PP-2: islink check must precede resolve()
    if _has_symlink_component(p):
        raise ClipwrightError(
            code=ErrorCode.PATH_NOT_ALLOWED,
            message=f"Symbolic links are not accepted: {path}",
            hint="Specify the path to a real file, not a symbolic link.",
        )

    if not p.is_file():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"File not found: {path}",
            hint="Check that the path is correct and the file exists.",
        )


def check_output_not_source(output: Path, sources: Iterable[str]) -> None:
    """Raise PATH_NOT_ALLOWED when output resolves equal to any source path.

    Uses a resolve() -> absolute() -> str three-stage canonicalisation for
    each source, consolidating render._check_path_not_allowed and extending
    it to support multiple sources and Windows-style backslash separators.

    Args:
        output: Intended output file path.
        sources: Iterable of source file path strings.  May include
            Windows-style backslash separators.

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED when output equals any source.
    """
    out_canon = _canon(output)
    for source in sources:
        src_path = _normalize_sep(source)
        if out_canon == _canon(src_path):
            raise ClipwrightError(
                code=ErrorCode.PATH_NOT_ALLOWED,
                message="Output path and input source path are the same.",
                hint=(
                    "Change the output file path to be different from the"
                    " input source file."
                ),
            )


def media_ref_for_otio(source: str | Path, otio_dir: Path) -> str:
    """Return the target_url to embed in an OTIO saved at *otio_dir*.

    Returns a relative POSIX path when *source* is under the *otio_dir* tree
    (so the OTIO file can be moved as a unit), and the absolute path string
    when *source* is outside the tree (external reference).

    Backslash separators in *source* are normalised before comparison so that
    Windows-origin path strings are handled correctly on all host OSes.

    Args:
        source: Source media file path (str or Path; may use backslashes).
        otio_dir: Directory where the OTIO file will be saved.

    Returns:
        Relative POSIX path string when source is under otio_dir, otherwise
        the absolute path string.
    """
    src_path = _normalize_sep(source) if isinstance(source, str) else source

    try:
        src_abs = src_path.resolve()
        dir_abs = otio_dir.resolve()
        rel = src_abs.relative_to(dir_abs)
        return rel.as_posix()
    except (ValueError, OSError):
        pass

    # Source is outside otio_dir: return absolute path with POSIX separators
    # (OTIO target_url must use forward slashes on all platforms).
    try:
        return src_path.resolve().as_posix()
    except OSError:
        try:
            return src_path.absolute().as_posix()
        except OSError:
            return src_path.as_posix()


def check_media_ref(ref: str, otio_dir: Path, kind: str) -> None:
    """Validate an OTIO media/subtitle/image reference at read/materialise time.

    Decision logic (ADR-PP-1):
    - Relative ref: must resolve within the otio_dir tree (CWE-22 guard).
    - Absolute ref: allowed iff it resolves to an existing regular file with
      no symlink in any path component (ADR-PP-2).
    - All other cases: PATH_NOT_ALLOWED.

    Consolidates render._check_within_timeline_dir with the absolute escape
    hatch unified across media/subtitle/image references (ADR-PP-1).

    Args:
        ref: Reference string from the OTIO target_url field.
        otio_dir: Directory containing the OTIO file.
        kind: Type label ('media', 'subtitle', 'image') used in error messages.

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED when the reference is unsafe,
            out-of-boundary, non-existent, or a symlink.
    """
    ref_path = _normalize_sep(ref)

    if ref_path.is_absolute():
        # Absolute escape hatch (ADR-PP-1): existing regular file, no symlinks.
        # ADR-PP-2: islink check must precede resolve().
        if _has_symlink_component(ref_path):
            raise ClipwrightError(
                code=ErrorCode.PATH_NOT_ALLOWED,
                message=f"Symbolic links are not accepted for {kind} reference.",
                hint=(f"Specify the path to a real {kind} file, not a symbolic link."),
            )
        if not ref_path.is_file():
            raise ClipwrightError(
                code=ErrorCode.PATH_NOT_ALLOWED,
                message=(
                    f"Referenced {kind} file does not exist"
                    f" or is not a regular file: {ref_path.name}"
                ),
                hint=(
                    f"Check that the {kind} file path is correct and the file exists."
                ),
            )
    else:
        # Relative ref: must resolve within the otio_dir tree (CWE-22 guard).
        try:
            target_resolved = str((otio_dir / ref_path).resolve())
            base_resolved = str(otio_dir.resolve())
            if not _is_within(target_resolved, base_resolved):
                raise ClipwrightError(
                    code=ErrorCode.PATH_NOT_ALLOWED,
                    message=(f"{kind} reference points outside the project boundary."),
                    hint=(
                        f"Use a {kind} file located under the same"
                        " directory as the OTIO timeline."
                    ),
                )
        except ClipwrightError:
            raise
        except OSError:
            # Fallback: absolute()-based comparison (SR L-1)
            try:
                abs_target = str((otio_dir / ref_path).absolute())
                abs_base = str(otio_dir.absolute())
                if not _is_within(abs_target, abs_base):
                    raise ClipwrightError(
                        code=ErrorCode.PATH_NOT_ALLOWED,
                        message=(
                            f"{kind} reference points outside the project boundary."
                        ),
                        hint=(
                            f"Use a {kind} file located under the same"
                            " directory as the OTIO timeline."
                        ),
                    )
            except ClipwrightError:
                raise
            except OSError:
                # Skip only when absolute() also fails (truly unresolvable path)
                pass


def check_within_boundary(base_dir: Path, target: Path, kind: str) -> None:
    """Containment guard for detect/extract output artifacts.

    Target must be under base_dir.  Path traversal attempts (e.g. ../) are
    caught via resolve().  Behaviour-preserving consolidation of
    scene/frames._check_within_boundary.

    Args:
        base_dir: Allowed boundary directory.
        target: Path to validate (need not exist yet).
        kind: Type label ('frame', 'scene', 'thumbnail') for error messages.

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED when target is outside base_dir.
    """
    try:
        base_resolved = str(base_dir.resolve())
        target_resolved = str(target.resolve())
        if not _is_within(target_resolved, base_resolved):
            raise ClipwrightError(
                code=ErrorCode.PATH_NOT_ALLOWED,
                message=f"{kind} output path is outside the allowed boundary.",
                hint=(
                    f"Place the {kind} output inside the designated"
                    " artifacts directory."
                ),
            )
    except ClipwrightError:
        raise
    except OSError:
        # Fallback: absolute()-based comparison (SR L-1)
        try:
            base_abs = str(base_dir.absolute())
            target_abs = str(target.absolute())
            if not _is_within(target_abs, base_abs):
                raise ClipwrightError(
                    code=ErrorCode.PATH_NOT_ALLOWED,
                    message=f"{kind} output path is outside the allowed boundary.",
                    hint=(
                        f"Place the {kind} output inside the designated"
                        " artifacts directory."
                    ),
                )
        except ClipwrightError:
            raise
        except OSError:
            pass
