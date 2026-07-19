"""test___TOOL__.py — Test orchestration layer __ACTION__.

Validates happy path and representative errors (FILE_NOT_FOUND / non-destructive / extension).
Real tools add detection logic and OSS subprocess boundary tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clipwright___TOOL__.__TOOL__ import __ACTION__
from clipwright___TOOL__.schemas import __Action__Options

# ---------------------------------------------------------------------------
# Symlink probe (mirrors clipwright-bgm/tests/test_pathpolicy_bgm.py L50-88).
# File-local duplication is the established convention for this repo (no
# shared conftest helper); see architecture-report-20260720-082027.md ADR-PB-4.
# ---------------------------------------------------------------------------


def _probe_symlink_support() -> bool:
    """Return True when the runtime environment allows symlink creation.

    Executed once at module import (collection) time so pytest.mark.skipif
    can reference the result.
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


def test_happy_path_writes_artifact(sample_input: Path, tmp_path: Path) -> None:
    output = tmp_path / "out.json"
    result = __ACTION__(
        input=str(sample_input),
        output=str(output),
        options=__Action__Options(),
    )
    assert result["ok"] is True
    assert output.exists()
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["input"] == sample_input.name
    assert result["artifacts"][0]["format"] == "json"


def test_missing_input_returns_file_not_found(tmp_path: Path) -> None:
    output = tmp_path / "out.json"
    result = __ACTION__(
        input=str(tmp_path / "nope.txt"),
        output=str(output),
        options=__Action__Options(),
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "FILE_NOT_FOUND"


def test_output_equals_input_rejected(sample_input: Path) -> None:
    """Non-destructive (M5): output == input rejected as PATH_NOT_ALLOWED (check_output_not_source)."""
    result = __ACTION__(
        input=str(sample_input),
        output=str(sample_input),
        options=__Action__Options(),
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "PATH_NOT_ALLOWED"


def test_non_json_output_rejected(sample_input: Path, tmp_path: Path) -> None:
    result = __ACTION__(
        input=str(sample_input),
        output=str(tmp_path / "out.txt"),
        options=__Action__Options(),
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"


@_skip_no_symlinks
def test_symlink_input_rejected(sample_input: Path, tmp_path: Path) -> None:
    """Symlinked input must be rejected with PATH_NOT_ALLOWED (ADR-PP-2, CWE-59)."""
    linked_input = tmp_path / f"linked{sample_input.suffix}"
    _try_symlink(linked_input, sample_input)
    output = tmp_path / "out.json"

    result = __ACTION__(
        input=str(linked_input),
        output=str(output),
        options=__Action__Options(),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "PATH_NOT_ALLOWED"
    assert str(tmp_path) not in result["error"]["message"]
