"""project.py — Project directory and manifest management.

Project layout:
  <project_dir>/
    clipwright.json   — manifest
    timeline.otio     — OTIO timeline (empty timeline with V1/A1 tracks)
    sources/          — input media storage
    artifacts/        — intermediate output storage
    outputs/          — final output storage
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clipwright import __version__
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.otio_utils import new_timeline, save_timeline

# Manifest filename
_MANIFEST_FILENAME = "clipwright.json"

# Manifest schema version (used for future migration detection)
_SCHEMA_VERSION = "1.0"

# Subdirectory list (created/re-created unconditionally by init_project)
_SUBDIRS = ("sources", "artifacts", "outputs")


# ===========================================================================
# Internal helpers
# ===========================================================================


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomically write text to a file (temp → os.replace).

    Creates a temp file in the same directory as the destination, then replaces it
    with os.replace to prevent file corruption on interrupted writes.
    Using the same directory avoids cross-device moves.
    """
    dir_path = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_path, str(path))
    except Exception:
        # Broad catch only to clean up the temp file; always re-raise (NL-2).
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


# ===========================================================================
# init_project
# ===========================================================================


def init_project(
    project_dir: str,
    name: str,
    *,
    force: bool = False,
) -> None:
    """Initialise a project directory.

    Creates project_dir if it does not exist.
    Creates the sources / artifacts / outputs subdirectories.
    Generates a clipwright.json manifest and an empty timeline.otio.

    Calling with force=False on an existing project (clipwright.json present) raises
    ClipwrightError(PROJECT_EXISTS).

    force=True behaviour (§13.2 DC-AM-007 — non-destructive):
      - Regenerates the manifest (reflects changes to name, etc.)
      - Ensures subdirectories exist (recreates them if missing)
      - Does not overwrite existing sources / artifacts / outputs / timeline.otio
      - Generates an empty timeline only if timeline.otio is absent

    Threat model:
      This function can create and initialise a directory at any path.
      It assumes a trusted caller (local MCP client, developer script, etc.) and
      provides no sandbox against malicious external input.
      The caller is responsible for validating project_dir before calling this function.
    """
    proj = Path(project_dir)
    manifest_path = proj / _MANIFEST_FILENAME
    timeline_path = proj / "timeline.otio"

    # Check for existing project
    if manifest_path.exists() and not force:
        raise ClipwrightError(
            code=ErrorCode.PROJECT_EXISTS,
            message=f"Project already exists: {project_dir}",
            hint=(
                "Pass force=True to reinitialise an existing project."
                " force=True is non-destructive"
                " (preserves existing sources/artifacts/outputs/timeline.otio)."
            ),
        )

    # Create directory (safe even if it already exists)
    proj.mkdir(parents=True, exist_ok=True)

    # Ensure subdirectories exist
    for subdir in _SUBDIRS:
        (proj / subdir).mkdir(exist_ok=True)

    # Generate manifest (regenerated when force=True)
    manifest: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "name": name,
        "clipwright_version": __version__,
        "created_at": datetime.now(UTC).isoformat(),
        "settings": {},
    }
    _atomic_write_text(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2),
    )

    # Generate timeline.otio (skipped when force=True and the file already exists)
    if not timeline_path.exists():
        timeline = new_timeline(name)
        save_timeline(timeline, str(timeline_path))


# ===========================================================================
# find_project
# ===========================================================================


def find_project(start_dir: str) -> str:
    """Walk up from start_dir to find clipwright.json.

    Returns the path (str) of the directory containing clipwright.json.
    Raises ClipwrightError(INVALID_INPUT) if start_dir is not a directory.
    Raises ClipwrightError(PROJECT_NOT_FOUND) if the root is reached without finding it.
    """
    start_path = Path(start_dir)
    if not start_path.is_dir():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"start_dir must be a directory: {start_dir}",
            hint=(
                "Specify a path to an existing directory."
                f" The given path '{start_path.name}' is not a directory."
            ),
        )

    current = start_path.resolve()

    while True:
        if (current / _MANIFEST_FILENAME).exists():
            return str(current)

        parent = current.parent
        if parent == current:
            # Reached the filesystem root
            break
        current = parent

    raise ClipwrightError(
        code=ErrorCode.PROJECT_NOT_FOUND,
        message=f"clipwright.json not found (search started from: {start_path.name})",
        hint="Initialise a project with init_project, then try again.",
    )


# ===========================================================================
# load_manifest / save_manifest
# ===========================================================================


def load_manifest(project_dir: str) -> dict[str, Any]:
    """Load the manifest (clipwright.json) from a project directory.

    Raises ClipwrightError(PROJECT_NOT_FOUND) if clipwright.json does not exist.
    Returns a dict (the top-level JSON object).
    """
    manifest_path = Path(project_dir) / _MANIFEST_FILENAME
    if not manifest_path.exists():
        raise ClipwrightError(
            code=ErrorCode.PROJECT_NOT_FOUND,
            message=f"clipwright.json not found: {project_dir}",
            hint="Initialise a project with init_project.",
        )
    return json.loads(manifest_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def save_manifest(project_dir: str, manifest: dict[str, Any]) -> None:
    """Atomically write the manifest to the project directory.

    Uses the temp → os.replace pattern to prevent clipwright.json corruption
    on interrupted writes (M-3).
    manifest must be a dict containing only JSON-serialisable types.
    """
    manifest_path = Path(project_dir) / _MANIFEST_FILENAME
    _atomic_write_text(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2),
    )
