"""pathpolicy.py — Cross-tool path safety primitives.

Centralises source validation, output-vs-source collision detection,
OTIO media reference construction, and containment guards.
All functions follow ADR-PP-1 (absolute escape hatch) and ADR-PP-2
(islink-before-resolve ordering, covering all path components).
"""

from __future__ import annotations

import warnings
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
            message=f"Symbolic links are not accepted: {p.name}",
            hint="Specify the path to a real file, not a symbolic link.",
        )

    if not p.is_file():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"File not found: {path}",
            hint="Check that the path is correct and the file exists.",
        )


def validate_source_or_basename(
    path: str | Path,
    *,
    message: str,
    hint: str,
    error_code: ErrorCode = ErrorCode.FILE_NOT_FOUND,
) -> None:
    """Validate a source file, re-raising FILE_NOT_FOUND with caller-chosen wording.

    Delegates to validate_source_file (existence + regular-file + ADR-PP-2
    symlink-component rejection). validate_source_file's own FILE_NOT_FOUND
    message embeds the full input path (CWE-209); when that happens, this
    re-raises using the caller-supplied basename-only message/hint and
    error_code, with __cause__ dropped via `from None` so the core message
    never reaches the MCP error envelope.

    PATH_NOT_ALLOWED (symlink) is not re-wrapped: core's own message/hint
    are already basename-only and propagate unchanged.

    Consolidates the try/except validate_source_file + re-wrap idiom
    duplicated across wrap/bgm/overlay/frames/transition/speed/text call
    sites (CR-M-001). error_code defaults to FILE_NOT_FOUND; frames'
    scene_timeline call site overrides it to INVALID_INPUT to preserve its
    pre-existing contract.

    Args:
        path: Input file path (str or Path).
        message: Basename-only message substituted for core's full-path
            FILE_NOT_FOUND message. Caller must pre-format this (e.g.
            f"Timeline file not found: {inp.name}") before calling.
        hint: Hint text to accompany message.
        error_code: ErrorCode to raise instead of FILE_NOT_FOUND when the
            file is missing. Defaults to FILE_NOT_FOUND.

    Raises:
        ClipwrightError: error_code (with message/hint) when the file does
            not exist. PATH_NOT_ALLOWED (unmodified) when any path
            component is a symlink.
    """
    try:
        validate_source_file(str(path))
    except ClipwrightError as exc:
        if exc.code == ErrorCode.FILE_NOT_FOUND:
            raise ClipwrightError(code=error_code, message=message, hint=hint) from None
        raise


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

    Relative refs: boundary only; existence is checked by the caller before
    this function is invoked.

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
        # SR-M-2 / ADR-PP-2: islink check must precede resolve() to prevent
        # CWE-59 bypass.  Apply to the full joined path (otio_dir / ref_path).
        if _has_symlink_component(otio_dir / ref_path):
            raise ClipwrightError(
                code=ErrorCode.PATH_NOT_ALLOWED,
                message=f"Symbolic links are not accepted for {kind} reference.",
                hint=(f"Specify the path to a real {kind} file, not a symbolic link."),
            )
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
                # Both resolve() and absolute() failed: boundary check is skipped.
                # Emit a warning so callers are aware the guard could not run
                # (CR-L-7 / SR-L-3: silent pass may hide path injection attempts).
                warnings.warn(
                    "Boundary check skipped: path could not be resolved"
                    " (resolve() and absolute() both failed).",
                    stacklevel=2,
                )


def check_timeline_source_matches(
    target_url: str, media_path: Path, otio_dir: Path
) -> None:
    """Verify that an OTIO timeline's source reference matches the supplied media.

    Performs equality check only (single responsibility).  The caller is
    responsible for running check_media_ref() before this function so that
    security guards (symlink, boundary, existence) have already been applied.

    Relative *target_url* values are joined against *otio_dir* (not CWD) to
    reproduce the same resolution logic used when the OTIO file was written —
    this is the root fix for the CWD-based misresolution bug (spec5 timeline
    match regression).

    Canonical comparison uses _canon() with its three-stage fallback
    (resolve -> absolute -> str) so that network paths and long paths on
    Windows do not cause false mismatches.

    Args:
        target_url: The ``target_url`` string from the OTIO media reference.
        media_path: The media file Path supplied by the caller for this run.
        otio_dir: Directory containing the OTIO file; used to resolve relative
            *target_url* values.

    Raises:
        ClipwrightError: INVALID_INPUT when *target_url* does not resolve to
            the same file as *media_path*.
    """
    ref_path = _normalize_sep(target_url)
    if not ref_path.is_absolute():
        ref_path = otio_dir / ref_path
    if _canon(ref_path) != _canon(media_path):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="Timeline source file does not match input media.",
            hint=(
                "Pass the same media file that was used to create the timeline,"
                " or pass the timeline that matches this media."
            ),
        )


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
            # Both resolve() and absolute() failed: containment check is skipped.
            # Emit a warning so callers are aware the guard could not run
            # (CR-L-7 / SR-L-3: silent pass may hide path injection attempts).
            warnings.warn(
                "Containment check skipped: path could not be resolved"
                " (resolve() and absolute() both failed).",
                stacklevel=2,
            )
