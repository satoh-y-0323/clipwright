"""test_server.py — Tests for server.py (FastMCP 4 tools).

Test perspectives:
  - ToolResult / ToolErrorResult envelope contract (§6.3/§6.4) for success and failure
  - MCP annotations match the §7 table
  - read_timeline: mutually exclusive project_dir / timeline_path; marker truncation 50
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ---- Import with availability flag (xfail guard when server.py is absent)

try:
    from clipwright.server import (
        clipwright_init_project,
        clipwright_inspect_media,
        clipwright_read_timeline,
        clipwright_write_timeline,
        mcp,
    )

    _SERVER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _SERVER_AVAILABLE = False

# Mark all tests as XFAIL when server.py is not available
pytestmark = pytest.mark.xfail(
    not _SERVER_AVAILABLE,
    reason="server.py is not available",
    strict=True,
)


# ===========================================================================
# Helpers
# ===========================================================================


def _assert_tool_result(result: Any) -> None:
    """Verify the ToolResult envelope contract (§6.3)."""
    d = result.model_dump() if hasattr(result, "model_dump") else result
    assert d.get("ok") is True, "ok must be True on success"
    assert "summary" in d, "summary key is required"
    assert isinstance(d["summary"], str), "summary must be str"
    assert len(d["summary"]) > 0, "summary must not be empty"
    assert "data" in d, "data key is required"
    assert isinstance(d["data"], dict), "data must be dict"
    assert "artifacts" in d, "artifacts key is required"
    assert isinstance(d["artifacts"], list), "artifacts must be list"
    assert "warnings" in d, "warnings key is required"
    assert isinstance(d["warnings"], list), "warnings must be list"


def _assert_tool_error_result(result: Any, expected_code: str) -> None:
    """Verify the ToolResult failure envelope contract (§6.4)."""
    d = result.model_dump() if hasattr(result, "model_dump") else result
    assert d.get("ok") is False, "ok must be False on failure"
    assert "error" in d, "error key is required"
    error = d["error"]
    assert isinstance(error, dict), "error must be dict"
    assert "code" in error, "error.code is required"
    assert "message" in error, "error.message is required"
    assert "hint" in error, "error.hint is required"
    assert isinstance(error["hint"], str) and len(error["hint"]) > 0, (
        "hint must be a non-empty string (actionable content)"
    )
    assert error["code"] == expected_code, (
        f"error.code must be {expected_code} (actual: {error['code']})"
    )


def _assert_no_path_leak(
    message: str, hint: str, project_dir: str, tmp_path: Path
) -> None:
    """Verify that neither message nor hint leaks filesystem details (CWE-209).

    Each condition is asserted individually (never OR-joined) so that a single
    regression cannot be masked by another still-passing condition.
    """
    assert project_dir not in message, (
        f"message must not contain the project path: {message!r}"
    )
    assert project_dir not in hint, f"hint must not contain the project path: {hint!r}"
    assert str(tmp_path) not in message, (
        f"message must not contain the temp root path: {message!r}"
    )
    assert str(tmp_path) not in hint, (
        f"hint must not contain the temp root path: {hint!r}"
    )
    assert "timeline.otio" not in message, (
        f"message must not contain the timeline file name: {message!r}"
    )
    assert "timeline.otio" not in hint, (
        f"hint must not contain the timeline file name: {hint!r}"
    )


def _collect_schema_string_choices(node: Any) -> set[str]:
    """Recursively collect string values constrained by ``enum`` / ``const``.

    Walks the whole JSON Schema fragment so the assertion does not depend on
    where Pydantic decides to place the constraint (e.g. directly on the
    property, inside ``anyOf``, or expanded into ``const`` alternatives).
    """
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "enum" and isinstance(value, list):
                found.update(v for v in value if isinstance(v, str))
            elif key == "const" and isinstance(value, str):
                found.add(value)
            else:
                found |= _collect_schema_string_choices(value)
    elif isinstance(node, list):
        for item in node:
            found |= _collect_schema_string_choices(item)
    return found


def _collect_schema_types(node: Any) -> set[str]:
    """Recursively collect every ``type`` keyword value in a schema fragment."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "type" and isinstance(value, str):
                found.add(value)
            else:
                found |= _collect_schema_types(value)
    elif isinstance(node, list):
        for item in node:
            found |= _collect_schema_types(item)
    return found


# ===========================================================================
# MCP annotations tests (§7 table / README adopted package notation)
# ===========================================================================


class TestMcpAnnotations:
    """Verify that FastMCP ToolAnnotations are set as per the §7 table.

    Uses ToolAnnotations fields from the README "annotations notation (adopted)".
    Retrieves the registered tool definition from mcp._tool_manager (or _tools).
    """

    def _get_tool_annotations(self, tool_name: str) -> dict[str, Any]:
        """Get annotations for a tool from the mcp object.

        Uses the FastMCP public API (mcp._tool_manager.get_tool).
        """
        tool = mcp._tool_manager.get_tool(tool_name)  # type: ignore[attr-defined]
        assert tool is not None, f"Tool {tool_name} must be registered in mcp"
        return tool.annotations or {}

    def test_clipwright_init_project_annotations(self) -> None:
        """init_project: readOnly:false / destructive:false
        / idempotent:false / openWorld:false."""
        ann = self._get_tool_annotations("clipwright_init_project")
        assert ann.readOnlyHint is False, "init_project is not read-only"
        assert ann.destructiveHint is False, (
            "init_project is non-destructive (no user data deletion)"
        )
        assert ann.idempotentHint is False, (
            "init_project is not idempotent (PROJECT_EXISTS on re-run)"
        )
        assert ann.openWorldHint is False, (
            "init_project does not access external resources"
        )

    def test_clipwright_inspect_media_annotations(self) -> None:
        """inspect_media: readOnly:true / destructive:false / idempotent:true."""
        ann = self._get_tool_annotations("clipwright_inspect_media")
        assert ann.readOnlyHint is True, "inspect_media is read-only"
        assert ann.destructiveHint is False, "inspect_media is non-destructive"
        assert ann.idempotentHint is True, (
            "inspect_media is idempotent (same input → same result)"
        )
        assert ann.openWorldHint is False, (
            "inspect_media does not access external resources"
        )

    def test_clipwright_read_timeline_annotations(self) -> None:
        """read_timeline: readOnly:true / destructive:false / idempotent:true."""
        ann = self._get_tool_annotations("clipwright_read_timeline")
        assert ann.readOnlyHint is True, "read_timeline is read-only"
        assert ann.destructiveHint is False, "read_timeline is non-destructive"
        assert ann.idempotentHint is True, "read_timeline is idempotent"

    def test_clipwright_write_timeline_annotations(self) -> None:
        """write_timeline: readOnly:false / destructive:false / idempotent:false."""
        ann = self._get_tool_annotations("clipwright_write_timeline")
        assert ann.readOnlyHint is False, "write_timeline writes"
        assert ann.destructiveHint is False, (
            "write_timeline is non-destructive (append semantics)"
        )
        assert ann.idempotentHint is False, "write_timeline is not idempotent"


# ===========================================================================
# clipwright_init_project tests
# ===========================================================================


class TestInitProject:
    """Verify the envelope contract for the clipwright_init_project tool."""

    def test_success_returns_tool_result(self, tmp_path: Path) -> None:
        """Success path: creates a project and returns ToolResult form."""
        project_dir = str(tmp_path / "my_project")
        result = clipwright_init_project(project_dir=project_dir, name="test project")
        _assert_tool_result(result)

    def test_success_creates_manifest(self, tmp_path: Path) -> None:
        """Success path: clipwright.json is generated."""
        project_dir = str(tmp_path / "proj")
        clipwright_init_project(project_dir=project_dir, name="test")
        assert (tmp_path / "proj" / "clipwright.json").exists()

    def test_success_creates_timeline(self, tmp_path: Path) -> None:
        """Success path: timeline.otio is generated."""
        project_dir = str(tmp_path / "proj")
        clipwright_init_project(project_dir=project_dir, name="test")
        assert (tmp_path / "proj" / "timeline.otio").exists()

    def test_success_artifacts_contain_manifest_and_timeline(
        self, tmp_path: Path
    ) -> None:
        """Success path: artifacts contain paths to the manifest and timeline."""
        project_dir = str(tmp_path / "proj")
        result = clipwright_init_project(project_dir=project_dir, name="test")
        _assert_tool_result(result)
        artifact_paths = [
            a["path"] if isinstance(a, dict) else a.path for a in result["artifacts"]
        ]
        assert any("clipwright.json" in p for p in artifact_paths), (
            "artifacts must contain clipwright.json"
        )
        assert any("timeline.otio" in p for p in artifact_paths), (
            "artifacts must contain timeline.otio"
        )

    def test_duplicate_project_returns_error(self, tmp_path: Path) -> None:
        """Error path: re-init of an existing project with force=False
        returns an error envelope."""
        project_dir = str(tmp_path / "proj")
        clipwright_init_project(project_dir=project_dir, name="test")
        # Second call
        result = clipwright_init_project(project_dir=project_dir, name="test")
        _assert_tool_error_result(result, "PROJECT_EXISTS")

    def test_force_reinit_returns_tool_result(self, tmp_path: Path) -> None:
        """Success path: force=True re-init of an existing project does not error."""
        project_dir = str(tmp_path / "proj")
        clipwright_init_project(project_dir=project_dir, name="test")
        result = clipwright_init_project(
            project_dir=project_dir, name="test", force=True
        )
        _assert_tool_result(result)

    def test_force_does_not_overwrite_existing_timeline(self, tmp_path: Path) -> None:
        """Success path: force=True does not overwrite the existing timeline.otio
        (non-destructive §13.2 DC-AM-007)."""
        project_dir = str(tmp_path / "proj")
        clipwright_init_project(project_dir=project_dir, name="test")
        # Record mtime of timeline.otio after writing sentinel content
        timeline_path = tmp_path / "proj" / "timeline.otio"
        original_mtime = timeline_path.stat().st_mtime

        clipwright_init_project(project_dir=project_dir, name="test2", force=True)
        # mtime must not change (file not overwritten)
        assert timeline_path.stat().st_mtime == original_mtime, (
            "force=True must not change the mtime of the existing timeline.otio"
        )


# ===========================================================================
# clipwright_inspect_media tests
# ===========================================================================


class TestInspectMedia:
    """Verify the envelope contract for the clipwright_inspect_media tool."""

    def test_success_returns_tool_result(self, sample_media: str) -> None:
        """Success path (integration): returns a ToolResult with a MediaInfo summary."""
        result = clipwright_inspect_media(path=sample_media)
        _assert_tool_result(result)

    def test_success_data_contains_media_info(self, sample_media: str) -> None:
        """Success path (integration): data contains MediaInfo-equivalent fields."""
        result = clipwright_inspect_media(path=sample_media)
        _assert_tool_result(result)
        data = result["data"]
        assert "path" in data or "container" in data or "streams" in data, (
            "data must contain MediaInfo-equivalent fields"
        )

    def test_file_not_found_returns_error(self, tmp_path: Path) -> None:
        """Error path: passing a non-existent path returns a FILE_NOT_FOUND envelope."""
        result = clipwright_inspect_media(path=str(tmp_path / "nonexistent.mp4"))
        _assert_tool_error_result(result, "FILE_NOT_FOUND")

    def test_dependency_missing_returns_error_with_windows_hint(
        self, tmp_path: Path, sample_media: str
    ) -> None:
        """Error path: DEPENDENCY_MISSING envelope + Windows hint (winget install)
        when ffprobe is absent (§13.3 DC-GP-001/DC-GP-004).

        Mocks process.resolve_tool to reproduce the ffprobe-not-found condition.
        """
        from clipwright.errors import ClipwrightError as _CWE
        from clipwright.errors import ErrorCode as _EC

        with patch(
            "clipwright.process.resolve_tool",
            side_effect=_CWE(
                _EC.DEPENDENCY_MISSING,
                "ffprobe not found",
                "Install it with winget install Gyan.FFmpeg",
            ),
        ):
            result = clipwright_inspect_media(path=sample_media)

        _assert_tool_error_result(result, "DEPENDENCY_MISSING")
        hint = result["error"]["hint"]
        assert "winget" in hint.lower() or "winget" in hint, (
            "Windows hint must mention winget install"
        )

    def test_dependency_missing_hint_is_actionable(
        self, tmp_path: Path, sample_media: str
    ) -> None:
        """DEPENDENCY_MISSING hint mentions Gyan.FFmpeg or CLIPWRIGHT_FFPROBE."""
        from clipwright.errors import ClipwrightError, ErrorCode

        with patch(
            "clipwright.process.resolve_tool",
            side_effect=ClipwrightError(
                ErrorCode.DEPENDENCY_MISSING,
                "ffprobe not found",
                "Install with winget install Gyan.FFmpeg or set CLIPWRIGHT_FFPROBE",
            ),
        ):
            result = clipwright_inspect_media(path=sample_media)

        hint = result["error"]["hint"]
        assert "Gyan.FFmpeg" in hint or "CLIPWRIGHT_FFPROBE" in hint, (
            "hint must mention Gyan.FFmpeg or CLIPWRIGHT_FFPROBE"
        )


# ===========================================================================
# clipwright_read_timeline tests
# ===========================================================================


class TestReadTimeline:
    """Verify the clipwright_read_timeline envelope contract,
    mutually exclusive inputs, and marker truncation."""

    def _setup_project(self, tmp_path: Path, name: str = "test") -> str:
        """Initialise a test project and return project_dir."""
        project_dir = str(tmp_path / "proj")
        clipwright_init_project(project_dir=project_dir, name=name)
        return project_dir

    # --- Success path ---

    def test_read_by_project_dir_returns_tool_result(self, tmp_path: Path) -> None:
        """Success path: returns ToolResult when specified by project_dir."""
        project_dir = self._setup_project(tmp_path)
        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(result)

    def test_read_by_timeline_path_returns_tool_result(self, tmp_path: Path) -> None:
        """Success path: returns ToolResult when specified by timeline_path."""
        project_dir = self._setup_project(tmp_path)
        timeline_path = str(Path(project_dir) / "timeline.otio")
        result = clipwright_read_timeline(timeline_path=timeline_path)
        _assert_tool_result(result)

    def test_data_contains_summary_fields(self, tmp_path: Path) -> None:
        """Success path: data contains clip_count / gap_count / marker_count
        / total_duration."""
        project_dir = self._setup_project(tmp_path)
        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(result)
        data = result["data"]
        assert "clip_count" in data, "data.clip_count is required"
        assert "gap_count" in data, "data.gap_count is required"
        assert "marker_count" in data, "data.marker_count is required"
        assert "total_duration" in data, "data.total_duration is required"

    def test_artifacts_contain_timeline_path(self, tmp_path: Path) -> None:
        """Success path: artifacts contain the timeline.otio path."""
        project_dir = self._setup_project(tmp_path)
        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(result)
        artifact_paths = [
            a["path"] if isinstance(a, dict) else a.path for a in result["artifacts"]
        ]
        assert any("timeline.otio" in p for p in artifact_paths), (
            "artifacts must contain the timeline.otio path"
        )

    # --- Mutually exclusive input validation (§13.2 DC-AS-004) ---

    def test_both_inputs_missing_returns_invalid_input(self, tmp_path: Path) -> None:
        """Error path: neither project_dir nor timeline_path specified
        → INVALID_INPUT (§13.2 DC-AS-004)."""
        result = clipwright_read_timeline()
        _assert_tool_error_result(result, "INVALID_INPUT")

    def test_both_inputs_provided_returns_invalid_input(self, tmp_path: Path) -> None:
        """Error path: both project_dir and timeline_path specified
        → INVALID_INPUT (§13.2 DC-AS-004)."""
        project_dir = self._setup_project(tmp_path)
        timeline_path = str(Path(project_dir) / "timeline.otio")
        result = clipwright_read_timeline(
            project_dir=project_dir,
            timeline_path=timeline_path,
        )
        _assert_tool_error_result(result, "INVALID_INPUT")

    def test_timeline_path_non_otio_extension_returns_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """Error path: passing a non-.otio extension to timeline_path returns
        PATH_NOT_ALLOWED (F-02 path traversal mitigation)."""
        # Create the file and verify only the extension is checked
        txt_path = tmp_path / "secrets.txt"
        txt_path.write_text("dummy")
        result = clipwright_read_timeline(timeline_path=str(txt_path))
        _assert_tool_error_result(result, "PATH_NOT_ALLOWED")

    def test_timeline_path_json_extension_returns_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """Error path: passing a .json extension to timeline_path also returns
        PATH_NOT_ALLOWED."""
        json_path = tmp_path / "data.json"
        json_path.write_text("{}")
        result = clipwright_read_timeline(timeline_path=str(json_path))
        _assert_tool_error_result(result, "PATH_NOT_ALLOWED")

    # --- marker truncation (§13.2 DC-AS-004 / §13.5 DC-AM-001) ---

    def test_markers_below_threshold_returns_markers_list(self, tmp_path: Path) -> None:
        """Success path: when marker count ≤ 50, data.markers is a list
        (§13.5 DC-AM-001).

        A new project has 0 markers, which is below the threshold.
        """
        project_dir = self._setup_project(tmp_path)
        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(result)
        data = result["data"]
        # markers key must exist as a list even with 0 markers
        assert "markers" in data, "data.markers key is required when marker count ≤ 50"
        assert isinstance(data["markers"], list), "data.markers must be list"
        # markers_truncated must be False or absent
        assert not data.get("markers_truncated", False), (
            "markers_truncated must be False or unset when marker count ≤ 50"
        )

    def test_markers_above_threshold_returns_truncated(self, tmp_path: Path) -> None:
        """AC-4: when marker count > 50, data.markers returns the first page
        (not omitted), markers_truncated=True, and markers_next_offset points
        to the next page start.

        The 'omit data.markers when > 50' behavior (old contract) is replaced
        by 'return first page with pagination keys' (new contract).
        """
        project_dir = self._setup_project(tmp_path)
        # Add 51 markers
        ops = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": float(i), "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": f"marker_{i:03d}",
            }
            for i in range(51)
        ]
        write_result = clipwright_write_timeline(
            project_dir=project_dir, operations=ops, validate_only=False
        )
        # Precondition must succeed or test itself is invalid
        assert write_result.get("ok"), (
            f"write_timeline precondition setup failed: {write_result}"
        )

        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(result)
        data = result["data"]
        # AC-4: markers key must exist even when count > 50
        assert "markers" in data, (
            "data.markers key is required (new contract: first page returned)"
        )
        # AC-4: markers list contains the first 50 items
        assert isinstance(data["markers"], list), "data.markers must be list"
        assert len(data["markers"]) == 50, (
            f"data.markers must have 50 items (first page), got {len(data['markers'])}"
        )
        # AC-4: markers_truncated=True when more items exist beyond this page
        assert data.get("markers_truncated") is True, (
            "data.markers_truncated=True when marker count > limit (50)"
        )
        # AC-4: markers_next_offset points to the next page
        assert data.get("markers_next_offset") == 50, (
            "data.markers_next_offset must be 50 (start of next page)"
        )
        # AC-8: marker_count is always the total (not page size)
        assert data["marker_count"] == 51, (
            f"marker_count must be 51 (total, not page size): {data.get('marker_count')}"
        )

    def test_markers_exactly_at_threshold_returns_list(self, tmp_path: Path) -> None:
        """Boundary: when marker count = 50, data.markers is a list (≤50 → list)."""
        project_dir = self._setup_project(tmp_path)
        ops = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": float(i), "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": f"marker_{i:03d}",
            }
            for i in range(50)
        ]
        write_result = clipwright_write_timeline(
            project_dir=project_dir, operations=ops, validate_only=False
        )
        assert write_result.get("ok"), (
            f"write_timeline precondition setup failed: {write_result}"
        )

        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(result)
        data = result["data"]
        assert "markers" in data, (
            "data.markers key is required when marker count = 50 (≤50 → list)"
        )
        assert isinstance(data["markers"], list), "data.markers must be list"
        assert not data.get("markers_truncated", False), (
            "markers_truncated must be False or unset when marker count = 50"
        )

    # --- AC-1~AC-8: Paging contract tests ---

    def _add_clips(self, project_dir: str, count: int) -> None:
        """Helper: add N clips to a timeline via write_timeline."""
        ops = [
            {
                "op": "add_clip",
                "track": 0,
                "media": {"target_url": f"file:///tmp/clip_{i:03d}.mp4"},
                "source_range": {
                    "start_time": {"value": 0.0, "rate": 30.0},
                    "duration": {"value": 10.0, "rate": 30.0},
                },
                "name": f"clip_{i:03d}",
            }
            for i in range(count)
        ]
        result = clipwright_write_timeline(
            project_dir=project_dir, operations=ops, validate_only=False
        )
        assert result.get("ok"), f"precondition: add_clip setup failed: {result}"

    def _add_markers(self, project_dir: str, count: int) -> None:
        """Helper: add N markers to a timeline via write_timeline."""
        ops = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": float(i), "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": f"marker_{i:03d}",
            }
            for i in range(count)
        ]
        result = clipwright_write_timeline(
            project_dir=project_dir, operations=ops, validate_only=False
        )
        assert result.get("ok"), f"precondition: add_marker setup failed: {result}"

    def test_ac1_clips_structure_basic_three_clips(self, tmp_path: Path) -> None:
        """AC-1: clip 3 本の timeline で data.clips が 3 件返り、
        各要素が index / name / track / start / duration / media を持つ。"""
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 3)

        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(result)
        data = result["data"]

        assert "clips" in data, "data.clips key is required"
        assert isinstance(data["clips"], list), "data.clips must be list"
        assert len(data["clips"]) == 3, (
            f"data.clips must have 3 items, got {len(data['clips'])}"
        )

        # Check all 6 required keys in each entry
        required_keys = {"index", "name", "track", "start", "duration", "media"}
        for i, clip in enumerate(data["clips"]):
            missing = required_keys - set(clip.keys())
            assert not missing, (
                f"clip[{i}] missing keys {missing}. Has: {set(clip.keys())}"
            )

    def test_ac2_clips_first_page_default_args(self, tmp_path: Path) -> None:
        """AC-2: clip 120 本・既定引数で clips が先頭 50 件、
        clips_truncated=True、clips_next_offset=50。"""
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 120)

        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(result)
        data = result["data"]

        assert isinstance(data.get("clips"), list), "data.clips must be list"
        assert len(data["clips"]) == 50, (
            f"first page must have 50 items (limit default), got {len(data['clips'])}"
        )
        assert data.get("clips_truncated") is True, (
            "clips_truncated must be True when more items exist"
        )
        assert data.get("clips_next_offset") == 50, (
            "clips_next_offset must point to next page start (50)"
        )

    def test_ac3_clips_paging_offset_and_last_page(self, tmp_path: Path) -> None:
        """AC-3: section='clips', offset=50, limit=50 で 51～100 件目。
        末尾ページでは clips_next_offset=None、clips_truncated=False。"""
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 120)

        # Page 2: offset=50, limit=50 should return clips 50-99
        result = clipwright_read_timeline(
            project_dir=project_dir,
            section="clips",
            offset=50,
            limit=50,
        )
        _assert_tool_result(result)
        data = result["data"]

        assert len(data["clips"]) == 50, (
            f"page 2 must have 50 items, got {len(data['clips'])}"
        )
        # Items should be from the 51st to 100th (index 50-99)
        assert data["clips"][0]["name"] == "clip_050", (
            "first item in page 2 should be clip_050"
        )
        assert data["clips"][49]["name"] == "clip_099", (
            "last item in page 2 should be clip_099"
        )
        assert data.get("clips_truncated") is True, (
            "clips_truncated=True (page 3 exists)"
        )
        assert data.get("clips_next_offset") == 100, "clips_next_offset=100 for page 3"

        # Page 3: offset=100, limit=50 should return clips 100-119 (20 items)
        result = clipwright_read_timeline(
            project_dir=project_dir,
            section="clips",
            offset=100,
            limit=50,
        )
        _assert_tool_result(result)
        data = result["data"]

        assert len(data["clips"]) == 20, (
            f"page 3 (last) must have 20 items, got {len(data['clips'])}"
        )
        assert data.get("clips_truncated") is False, (
            "clips_truncated=False on last page"
        )
        assert data.get("clips_next_offset") is None, (
            "clips_next_offset=None on last page"
        )

    def test_ac5_section_clips_omits_markers(self, tmp_path: Path) -> None:
        """AC-5: section='clips' 指定時は markers / markers_truncated /
        markers_next_offset キーをまとめて不在にする。"""
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 3)
        # Also add a marker for this check
        ops_marker = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": 0.0, "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": "test_marker",
            }
        ]
        clipwright_write_timeline(
            project_dir=project_dir, operations=ops_marker, validate_only=False
        )

        result = clipwright_read_timeline(project_dir=project_dir, section="clips")
        _assert_tool_result(result)
        data = result["data"]

        # clips must be present
        assert "clips" in data, "data.clips must be present when section='clips'"
        # markers keys must all be absent
        assert "markers" not in data, (
            "data.markers must be omitted when section='clips'"
        )
        assert "markers_truncated" not in data, (
            "data.markers_truncated must be omitted when section='clips'"
        )
        assert "markers_next_offset" not in data, (
            "data.markers_next_offset must be omitted when section='clips'"
        )

    def test_ac5_section_markers_omits_clips(self, tmp_path: Path) -> None:
        """AC-5: section='markers' 指定時は clips / clips_truncated /
        clips_next_offset キーをまとめて不在にする。"""
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 3)
        ops_marker = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": 0.0, "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": "test_marker",
            }
        ]
        clipwright_write_timeline(
            project_dir=project_dir, operations=ops_marker, validate_only=False
        )

        result = clipwright_read_timeline(project_dir=project_dir, section="markers")
        _assert_tool_result(result)
        data = result["data"]

        # markers must be present
        assert "markers" in data, "data.markers must be present when section='markers'"
        # clips keys must all be absent
        assert "clips" not in data, "data.clips must be omitted when section='markers'"
        assert "clips_truncated" not in data, (
            "data.clips_truncated must be omitted when section='markers'"
        )
        assert "clips_next_offset" not in data, (
            "data.clips_next_offset must be omitted when section='markers'"
        )

    def test_ac6_marker_kind_filters_results(self, tmp_path: Path) -> None:
        """AC-6: marker_kind='caption' で該当 kind のマーカーのみに絞られる。
        data.marker_count はフィルタ前の総数のまま（ADR-RD-10）。"""
        project_dir = self._setup_project(tmp_path)
        # Add markers with different kinds via write_timeline
        # (we use add_marker op which doesn't support kind; fall back to direct OTIO)
        from clipwright.otio_utils import load_timeline, save_timeline

        timeline_path = str(Path(project_dir) / "timeline.otio")
        timeline = load_timeline(timeline_path)

        # Manually add markers with kind metadata
        import opentimelineio as otio

        if timeline.video_tracks():
            v_track = timeline.video_tracks()[0]
            for i in range(10):
                kind = "caption" if i < 3 else "scene"
                marker = otio.schema.Marker(
                    name=f"marker_{i:02d}",
                    marked_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(float(i), 30.0),
                        duration=otio.opentime.RationalTime(1.0, 30.0),
                    ),
                )
                marker.metadata["clipwright"] = {"kind": kind}
                v_track.markers.append(marker)

        save_timeline(timeline, timeline_path)

        # Query by kind
        result = clipwright_read_timeline(
            project_dir=project_dir,
            section="markers",
            marker_kind="caption",
        )
        _assert_tool_result(result)
        data = result["data"]

        # Only caption markers should be in the list
        assert len(data.get("markers", [])) == 3, (
            f"filtered markers must have 3 items (caption kind), got {len(data.get('markers', []))}"
        )
        # But marker_count stays at total
        assert data.get("marker_count") == 10, (
            f"marker_count must be total (10, unfiltered), got {data.get('marker_count')}"
        )
        # summary should mention the filter
        summary = result.get("summary", "")
        assert "caption" in summary.lower() or "kind=" in summary.lower(), (
            "summary must mention the marker_kind filter"
        )

    def test_ac6_marker_kind_zero_hits_returns_empty(self, tmp_path: Path) -> None:
        """AC-6: marker_kind で 0 件ヒット時は ok=True で空リスト。
        エラーにはならない。"""
        project_dir = self._setup_project(tmp_path)
        ops_marker = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": 0.0, "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": "test_marker",
            }
        ]
        clipwright_write_timeline(
            project_dir=project_dir, operations=ops_marker, validate_only=False
        )

        # Query for a kind that doesn't exist
        result = clipwright_read_timeline(
            project_dir=project_dir,
            section="markers",
            marker_kind="nonexistent_kind",
        )
        _assert_tool_result(result)
        data = result["data"]

        assert data.get("markers") == [], "markers must be empty list when no match"
        assert data.get("markers_truncated") is False, (
            "markers_truncated=False when 0 items"
        )
        assert data.get("markers_next_offset") is None, (
            "markers_next_offset=None when 0 items"
        )

    def test_ac7_offset_with_no_section_invalid_input(self, tmp_path: Path) -> None:
        """AC-7: section=None かつ offset != 0 は INVALID_INPUT。
        error.hint が非空。error.message にパスが含まれない（CWE-209）。"""
        project_dir = self._setup_project(tmp_path)

        result = clipwright_read_timeline(project_dir=project_dir, offset=10)
        _assert_tool_error_result(result, "INVALID_INPUT")
        hint = result["error"]["hint"]
        message = result["error"]["message"]

        assert len(hint) > 0, "error.hint must be non-empty"
        # Check no path in message/hint
        assert project_dir not in message, (
            f"message must not contain project path: {message!r}"
        )
        assert project_dir not in hint, f"hint must not contain project path: {hint!r}"
        # SR-R-001: individual AND assertions (the previous single OR-joined
        # assertion could never fail on its own).
        assert str(tmp_path) not in message, (
            f"message must not contain the temp root path: {message!r}"
        )
        assert str(tmp_path) not in hint, (
            f"hint must not contain the temp root path: {hint!r}"
        )
        assert "timeline.otio" not in message, (
            f"message must not contain the timeline file name: {message!r}"
        )
        assert "timeline.otio" not in hint, (
            f"hint must not contain the timeline file name: {hint!r}"
        )

    def test_ac7_negative_offset_invalid_input(self, tmp_path: Path) -> None:
        """AC-7: offset < 0 は INVALID_INPUT。
        SR-R-001: message / hint にパスが混入しないことも固定する（CWE-209）。"""
        project_dir = self._setup_project(tmp_path)

        result = clipwright_read_timeline(
            project_dir=project_dir,
            section="clips",
            offset=-1,
        )
        _assert_tool_error_result(result, "INVALID_INPUT")
        hint = result["error"]["hint"]
        message = result["error"]["message"]
        assert len(hint) > 0, "error.hint must be non-empty"
        _assert_no_path_leak(message, hint, project_dir, tmp_path)

    def test_ac7_zero_or_negative_limit_invalid_input(self, tmp_path: Path) -> None:
        """AC-7: limit <= 0 は INVALID_INPUT。
        SR-R-001: message / hint にパスが混入しないことも固定する（CWE-209）。"""
        project_dir = self._setup_project(tmp_path)

        for bad_limit in [0, -1, -100]:
            result = clipwright_read_timeline(
                project_dir=project_dir,
                section="clips",
                limit=bad_limit,
            )
            _assert_tool_error_result(result, "INVALID_INPUT")
            hint = result["error"]["hint"]
            message = result["error"]["message"]
            assert len(hint) > 0, f"error.hint must be non-empty (limit={bad_limit})"
            _assert_no_path_leak(message, hint, project_dir, tmp_path)

    def test_ac7_offset_past_end_invalid_input(self, tmp_path: Path) -> None:
        """AC-7: offset > 0 かつ offset >= 総件数 は INVALID_INPUT。
        総件数は section により異なる。"""
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 10)

        # Try offset=10 (past end of 10 clips)
        result = clipwright_read_timeline(
            project_dir=project_dir,
            section="clips",
            offset=10,  # 0-9 have 10 items, so offset 10 is past end
        )
        _assert_tool_error_result(result, "INVALID_INPUT")
        hint = result["error"]["hint"]
        message = result["error"]["message"]

        assert len(hint) > 0, "error.hint must be non-empty"
        # Hint should be actionable (e.g., mention valid range)
        # Message should not contain path
        assert project_dir not in message, (
            f"message must not contain project path: {message!r}"
        )

    def test_ac8_small_timeline_full_return_no_truncation(self, tmp_path: Path) -> None:
        """AC-8: marker/clip 50 件以下で従来どおり全件返却。
        markers_truncated=False。既存キー不変（clip_count など）。"""
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 20)

        # Add 15 markers (below 50)
        ops_marker = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": float(i), "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": f"marker_{i:02d}",
            }
            for i in range(15)
        ]
        clipwright_write_timeline(
            project_dir=project_dir, operations=ops_marker, validate_only=False
        )

        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(result)
        data = result["data"]

        # All clips and markers should be returned
        assert len(data.get("clips", [])) == 20, "all clips must be returned"
        assert len(data.get("markers", [])) == 15, "all markers must be returned"
        assert data.get("clips_truncated") is False, "clips_truncated=False when ≤50"
        assert data.get("markers_truncated") is False, (
            "markers_truncated=False when ≤50"
        )
        assert data.get("clips_next_offset") is None, (
            "clips_next_offset=None when not truncated"
        )
        assert data.get("markers_next_offset") is None, (
            "markers_next_offset=None when not truncated"
        )
        # Existing keys must be present
        assert "clip_count" in data, "clip_count must be present (existing key)"
        assert data["clip_count"] == 20, "clip_count must be accurate"
        assert "marker_count" in data, "marker_count must be present"
        assert data["marker_count"] == 15, "marker_count must be accurate"
        assert "gap_count" in data, "gap_count must be present"
        assert "total_duration" in data, "total_duration must be present"

    def test_limit_clamp_to_500_with_warning(self, tmp_path: Path) -> None:
        """Limit > 500 は clamp + warning。data.limit は実効値をエコー。"""
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 600)

        result = clipwright_read_timeline(
            project_dir=project_dir,
            section="clips",
            limit=10000,  # Way over the max
        )
        _assert_tool_result(result)
        data = result["data"]
        warnings = result.get("warnings", [])

        # Should clamp to 500, not error
        assert len(data.get("clips", [])) == 500, (
            f"clips should be clamped to 500 items, got {len(data.get('clips', []))}"
        )
        assert data.get("limit") == 500, (
            f"data.limit should echo the clamped value (500), got {data.get('limit')}"
        )
        # Should have a warning about clamping
        assert any("clamp" in w.lower() for w in warnings), (
            f"warnings should mention clamping. Got: {warnings}"
        )

    def test_data_echoes_back_offset_limit_marker_kind(self, tmp_path: Path) -> None:
        """New: data echoes offset, limit, marker_kind for reflection."""
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 100)

        result = clipwright_read_timeline(
            project_dir=project_dir,
            section="clips",
            offset=25,
            limit=30,
        )
        _assert_tool_result(result)
        data = result["data"]

        assert data.get("offset") == 25, "data.offset should echo input"
        assert data.get("limit") == 30, "data.limit should echo input"
        # marker_kind not specified, should be None
        assert data.get("marker_kind") is None, (
            "data.marker_kind should be None when not specified"
        )

    def test_summary_contains_page_position_and_next_steps(
        self, tmp_path: Path
    ) -> None:
        """New: summary contains current page position and next action."""
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 120)

        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(result)
        summary = result.get("summary", "")

        # Should mention pagination and item counts
        assert "120" in summary or "120 clips" in summary, (
            f"summary should mention total clip count (120): {summary!r}"
        )
        # Should mention next action when truncated
        assert "offset" in summary.lower() or "50" in summary, (
            f"summary should hint at pagination/next offset: {summary!r}"
        )

    # --- CR-R-003 / SR-V-001: invalid section value response contract ---

    def test_invalid_section_value_returns_invalid_input_envelope(
        self, tmp_path: Path
    ) -> None:
        """A misspelled section value is rejected by the function body itself.

        Measured contract (regression guard for the defence-in-depth `else`
        branch): the decorated tool is a plain function, so a direct call is
        not validated by Pydantic and the value reaches the body, which returns
        the INVALID_INPUT envelope. Adding Literal to the signature must not
        remove this behaviour.
        """
        project_dir = self._setup_project(tmp_path)

        result = clipwright_read_timeline(project_dir=project_dir, section="clip")

        _assert_tool_error_result(result, "INVALID_INPUT")
        message = result["error"]["message"]
        hint = result["error"]["hint"]
        assert len(hint) > 0, "error.hint must be non-empty"
        assert "section" in message, (
            f"message must name the offending parameter: {message!r}"
        )
        _assert_no_path_leak(message, hint, project_dir, tmp_path)

    def test_valid_section_values_still_succeed(self, tmp_path: Path) -> None:
        """Both accepted section values keep working once the enum is added."""
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 3)
        self._add_markers(project_dir, 2)

        clips_result = clipwright_read_timeline(
            project_dir=project_dir, section="clips"
        )
        _assert_tool_result(clips_result)
        assert len(clips_result["data"]["clips"]) == 3, (
            "section='clips' must still return every clip"
        )

        markers_result = clipwright_read_timeline(
            project_dir=project_dir, section="markers"
        )
        _assert_tool_result(markers_result)
        assert len(markers_result["data"]["markers"]) == 2, (
            "section='markers' must still return every marker"
        )

    # --- CR-T-001: untested summary branches ---

    def test_overview_summary_when_no_clips_and_no_markers(
        self, tmp_path: Path
    ) -> None:
        """Overview of an empty timeline describes both lists as empty.

        Measured summary:
        "Timeline loaded: test (clips=0, gaps=0, markers=0).
         Showing no clips and no markers."
        """
        project_dir = self._setup_project(tmp_path)

        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(result)
        summary = result["summary"]
        data = result["data"]

        assert data["clip_count"] == 0, "precondition: timeline must have no clips"
        assert data["marker_count"] == 0, "precondition: timeline must have no markers"
        assert "Showing no clips and no markers." in summary, (
            f"summary must describe both lists as empty: {summary!r}"
        )
        assert "call again" not in summary, (
            f"empty overview must not advise further paging: {summary!r}"
        )

    def test_overview_summary_when_both_clips_and_markers_truncated(
        self, tmp_path: Path
    ) -> None:
        """Overview advises paging on both lists when both are truncated.

        Measured summary (60 clips + 60 markers, default limit 50):
        "Timeline loaded: test (clips=60, gaps=0, markers=60).
         Showing clips 0-49 of 60 and markers 0-49 of 60; call again with
         section=\"clips\" or section=\"markers\" plus offset to page further."
        """
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 60)
        self._add_markers(project_dir, 60)

        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(result)
        summary = result["summary"]
        data = result["data"]

        assert data["clips_truncated"] is True, "precondition: clips must be truncated"
        assert data["markers_truncated"] is True, (
            "precondition: markers must be truncated"
        )
        assert "Showing clips 0-49 of 60 and markers 0-49 of 60" in summary, (
            f"summary must report both page positions: {summary!r}"
        )
        assert 'section="clips"' in summary, (
            f"summary must offer the clips section: {summary!r}"
        )
        assert 'section="markers"' in summary, (
            f"summary must offer the markers section: {summary!r}"
        )
        assert 'section="clips" or section="markers"' in summary, (
            f"both sections must be joined with ' or ': {summary!r}"
        )

    def test_section_clips_summary_when_timeline_has_no_clips(
        self, tmp_path: Path
    ) -> None:
        """section='clips' on a clip-less timeline reports the empty branch.

        Measured summary:
        "Timeline loaded: test (clips=0, gaps=0, markers=0).
         Timeline has no clips."
        """
        project_dir = self._setup_project(tmp_path)

        result = clipwright_read_timeline(project_dir=project_dir, section="clips")
        _assert_tool_result(result)
        summary = result["summary"]
        data = result["data"]

        assert data["clips"] == [], "precondition: the clips page must be empty"
        assert "Timeline has no clips." in summary, (
            f"summary must report the empty-clips branch: {summary!r}"
        )

    # --- CR-E-004: validation order for compound violations ---

    def test_negative_offset_without_section_reports_offset_rule_first(
        self, tmp_path: Path
    ) -> None:
        """offset<0 must be reported before the 'offset needs section' rule.

        With section omitted AND offset negative, both rules are violated.
        The negative-offset rule is the more fundamental one, so it must win;
        otherwise the caller has to make an extra round trip.
        """
        project_dir = self._setup_project(tmp_path)

        result = clipwright_read_timeline(project_dir=project_dir, offset=-1)

        _assert_tool_error_result(result, "INVALID_INPUT")
        message = result["error"]["message"]
        hint = result["error"]["hint"]
        assert message == "offset must be zero or greater", (
            f"the negative-offset rule must be evaluated first, got {message!r}"
        )
        assert len(hint) > 0, "error.hint must be non-empty"
        _assert_no_path_leak(message, hint, project_dir, tmp_path)

    def test_positive_offset_without_section_reports_section_rule(
        self, tmp_path: Path
    ) -> None:
        """A non-negative offset without section keeps the section-required rule.

        Guards against the reordering above swallowing the original message.
        """
        project_dir = self._setup_project(tmp_path)

        result = clipwright_read_timeline(project_dir=project_dir, offset=10)

        _assert_tool_error_result(result, "INVALID_INPUT")
        message = result["error"]["message"]
        hint = result["error"]["hint"]
        assert message == "offset is only supported together with section", (
            f"positive offset without section must keep its own rule, got {message!r}"
        )
        assert len(hint) > 0, "error.hint must be non-empty"
        _assert_no_path_leak(message, hint, project_dir, tmp_path)


# ===========================================================================
# clipwright_read_timeline inputSchema tests (CR-R-003 / SR-V-001, ADR-RD-2)
# ===========================================================================


class TestReadTimelineInputSchema:
    """Pin the generated MCP inputSchema of clipwright_read_timeline.

    ADR-RD-2 requires section to be Literal["clips", "markers"] | None so the
    allowed values are visible to the calling agent through the schema itself.
    Real stdio MCP measurement (test-report-e2e-mcp-stdio.md item 5) showed the
    enum missing, which is the regression these tests lock down.
    """

    def _property_schema(self, name: str) -> dict[str, Any]:
        tool = mcp._tool_manager.get_tool("clipwright_read_timeline")  # type: ignore[attr-defined]
        assert tool is not None, "clipwright_read_timeline must be registered in mcp"
        params = tool.parameters
        assert isinstance(params, dict), (
            f"parameters must be a dict, got {type(params)}"
        )
        properties = params.get("properties")
        assert isinstance(properties, dict), (
            f"inputSchema.properties must be a dict, got {properties!r}"
        )
        prop = properties.get(name)
        assert isinstance(prop, dict), (
            f"inputSchema.properties.{name} must be a dict, got {prop!r}"
        )
        return prop

    def test_section_property_exposes_clips_and_markers_enum(self) -> None:
        """section must constrain its string values to exactly clips/markers."""
        section_schema = self._property_schema("section")

        choices = _collect_schema_string_choices(section_schema)

        assert choices == {"clips", "markers"}, (
            "section must expose exactly the allowed values through an enum/const "
            f"constraint, got {sorted(choices)} from {section_schema!r}"
        )

    def test_section_property_still_accepts_null(self) -> None:
        """Omitting section (null) must remain valid alongside the enum."""
        section_schema = self._property_schema("section")

        assert section_schema.get("default") is None, (
            f"section default must stay null, got {section_schema!r}"
        )
        assert "null" in _collect_schema_types(section_schema), (
            f"section must still accept null: {section_schema!r}"
        )

    def test_marker_kind_property_has_no_enum(self) -> None:
        """marker_kind stays a free-form string (ADR-RD-2 scopes enum to section)."""
        assert _collect_schema_string_choices(self._property_schema("marker_kind")) == (
            set()
        ), "marker_kind must remain an unconstrained string"


# ===========================================================================
# clipwright_read_timeline symlink rejection tests (ADR-PB-1 / ADR-PB-2,
# architecture-report-20260720-082027.md)
# ===========================================================================


def _probe_symlink_support() -> bool:
    """Return True when the runtime environment allows symlink creation.

    Executed once at module import (collection) time so pytest.mark.skipif
    can reference the result. Mirrors clipwright-bgm/tests/test_pathpolicy_bgm.py.
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


class TestReadTimelineSymlinkRejection:
    """ADR-PB-2 / ADR-PB-1: clipwright_read_timeline rejects a symlinked
    timeline file instead of silently following it, for both mutually
    exclusive input shapes."""

    def _setup_project(self, tmp_path: Path, name: str = "test") -> str:
        """Initialise a test project and return project_dir."""
        project_dir = str(tmp_path / "proj")
        clipwright_init_project(project_dir=project_dir, name=name)
        return project_dir

    @_skip_no_symlinks
    def test_read_timeline_timeline_path_symlink_rejected(self, tmp_path: Path) -> None:
        """ADR-PB-2: a symlink passed via timeline_path is rejected with
        PATH_NOT_ALLOWED. The pre-fix code calls Path.resolve() before
        is_file(), which silently strips the symlink component and loads
        the real target file instead of rejecting it."""
        from clipwright.otio_utils import new_timeline, save_timeline

        real_path = tmp_path / "real.otio"
        save_timeline(new_timeline("real"), str(real_path))
        link_path = tmp_path / "link.otio"
        _try_symlink(link_path, real_path)

        result = clipwright_read_timeline(timeline_path=str(link_path))
        _assert_tool_error_result(result, "PATH_NOT_ALLOWED")

    @_skip_no_symlinks
    def test_read_timeline_project_dir_symlinked_timeline_rejected(
        self, tmp_path: Path
    ) -> None:
        """ADR-PB-1: a project_dir whose timeline.otio has been replaced by
        a symlink to a real .otio file is rejected with PATH_NOT_ALLOWED.
        _resolve_project_timeline's is_file() check follows the symlink to
        a readable file, so the guard must come from load_timeline itself
        (core fix protects this project_dir path without any server change)."""
        project_dir = self._setup_project(tmp_path)
        timeline_otio = Path(project_dir) / "timeline.otio"
        real_path = tmp_path / "real_target.otio"
        timeline_otio.replace(real_path)
        _try_symlink(timeline_otio, real_path)

        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_error_result(result, "PATH_NOT_ALLOWED")


# ===========================================================================
# clipwright_write_timeline tests
# ===========================================================================


class TestWriteTimeline:
    """Verify the clipwright_write_timeline envelope contract,
    append semantics, and validate_only."""

    def _setup_project(self, tmp_path: Path, name: str = "test") -> str:
        """Initialise a test project and return project_dir."""
        project_dir = str(tmp_path / "proj")
        clipwright_init_project(project_dir=project_dir, name=name)
        return project_dir

    # --- Success path ---

    def test_empty_operations_returns_tool_result(self, tmp_path: Path) -> None:
        """Success path: returns ToolResult even with an empty operations list."""
        project_dir = self._setup_project(tmp_path)
        result = clipwright_write_timeline(
            project_dir=project_dir, operations=[], validate_only=False
        )
        _assert_tool_result(result)

    def test_data_contains_validation_report(self, tmp_path: Path) -> None:
        """Success path: data contains ValidationReport-equivalent fields
        (§13.1 DC-AM-003)."""
        project_dir = self._setup_project(tmp_path)
        result = clipwright_write_timeline(
            project_dir=project_dir, operations=[], validate_only=False
        )
        _assert_tool_result(result)
        data = result["data"]
        assert "valid" in data, "data.valid is required"
        assert "operation_count" in data, "data.operation_count is required"
        assert "applied_count" in data, "data.applied_count is required"

    def test_add_marker_operation_succeeds(self, tmp_path: Path) -> None:
        """Success path: passing an add_marker op succeeds with applied_count=1."""
        project_dir = self._setup_project(tmp_path)
        ops = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": 0.0, "rate": 30.0},
                    "duration": {"value": 30.0, "rate": 30.0},
                },
                "name": "test marker",
            }
        ]
        result = clipwright_write_timeline(
            project_dir=project_dir, operations=ops, validate_only=False
        )
        _assert_tool_result(result)
        data = result["data"]
        assert data.get("valid") is True, "valid must be True"
        assert data.get("applied_count") == 1, "applied_count must be 1"

    def test_validate_only_does_not_apply(self, tmp_path: Path) -> None:
        """Success path: validate_only=True gives applied_count=0 and
        does not write to the timeline (§13.1 DC-AM-003)."""
        project_dir = self._setup_project(tmp_path)
        timeline_path = Path(project_dir) / "timeline.otio"
        mtime_before = timeline_path.stat().st_mtime

        ops = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": 0.0, "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": "dry-run marker",
            }
        ]
        result = clipwright_write_timeline(
            project_dir=project_dir, operations=ops, validate_only=True
        )
        _assert_tool_result(result)
        data = result["data"]
        assert data.get("valid") is True, (
            "valid must be True even with validate_only=True"
        )
        assert data.get("applied_count") == 0, (
            "applied_count must be 0 with validate_only=True"
        )
        # timeline.otio mtime must not change
        assert timeline_path.stat().st_mtime == mtime_before, (
            "timeline.otio must not be updated with validate_only=True"
        )

    def test_additive_semantics_preserves_existing_content(
        self, tmp_path: Path
    ) -> None:
        """Success path: append semantics — existing content is not lost after
        a second write_timeline call (§13.2 DC-AM-001)."""
        project_dir = self._setup_project(tmp_path)

        # First call: add marker_first
        ops_1 = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": 0.0, "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": "marker_first",
            }
        ]
        result_1 = clipwright_write_timeline(
            project_dir=project_dir, operations=ops_1, validate_only=False
        )
        _assert_tool_result(result_1)

        # Second call: add marker_second
        ops_2 = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": 1.0, "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": "marker_second",
            }
        ]
        result_2 = clipwright_write_timeline(
            project_dir=project_dir, operations=ops_2, validate_only=False
        )
        _assert_tool_result(result_2)

        # Verify marker_count=2 via read_timeline
        read_result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(read_result)
        assert read_result["data"]["marker_count"] == 2, (
            "Append semantics: marker_count must be 2 after two write calls"
        )

    def test_invalid_op_returns_validation_error(self, tmp_path: Path) -> None:
        """Error path: passing an invalid op returns an ok=False
        INVALID_INPUT error envelope.

        Pydantic validation failure (unknown op type etc.) is an input schema
        violation, so ok=False / error.code=INVALID_INPUT is returned (§6.4 contract).
        all-or-nothing: no ops are applied if even one is invalid (§13.1 DC-AM-004).
        """
        project_dir = self._setup_project(tmp_path)
        bad_ops = [{"op": "unknown_op", "track": 0}]
        result = clipwright_write_timeline(
            project_dir=project_dir, operations=bad_ops, validate_only=False
        )
        _assert_tool_error_result(result, "INVALID_INPUT")

    def test_all_or_nothing_on_invalid_op(self, tmp_path: Path) -> None:
        """Error path: if any op is invalid, none are applied (§13.1 DC-AM-004)."""
        project_dir = self._setup_project(tmp_path)

        # First, add one valid marker
        ops_init = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": 0.0, "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": "marker_before",
            }
        ]
        clipwright_write_timeline(
            project_dir=project_dir, operations=ops_init, validate_only=False
        )

        # Mix of valid op + invalid op (out-of-range track)
        ops_mixed = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": 2.0, "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": "marker_good",
            },
            {
                "op": "add_marker",
                "track": 999,  # invalid: track does not exist
                "marked_range": {
                    "start_time": {"value": 3.0, "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": "marker_bad",
            },
        ]
        clipwright_write_timeline(
            project_dir=project_dir, operations=ops_mixed, validate_only=False
        )

        # all-or-nothing: marker_count before the mixed call should be preserved at 1
        read_result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(read_result)
        assert read_result["data"]["marker_count"] == 1, (
            "all-or-nothing: all ops are rolled back when an invalid op exists, "
            "so marker_count must remain 1"
        )

    def test_artifacts_contain_timeline_after_write(self, tmp_path: Path) -> None:
        """Success path: artifacts contain timeline.otio after a successful write."""
        project_dir = self._setup_project(tmp_path)
        ops = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": 0.0, "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": "m",
            }
        ]
        result = clipwright_write_timeline(
            project_dir=project_dir, operations=ops, validate_only=False
        )
        _assert_tool_result(result)
        artifact_paths = [
            a["path"] if isinstance(a, dict) else a.path for a in result["artifacts"]
        ]
        assert any("timeline.otio" in p for p in artifact_paths), (
            "artifacts must contain timeline.otio after a successful write"
        )


# ===========================================================================
# M-2: Test to pin that the duplicate resolve_tool call in
# clipwright_inspect_media is removed
# ===========================================================================


class TestInspectMediaResolveToolCallCount:
    """M-2 fix: pin via mock call count that the leading resolve_tool call
    in server.py is removed.

    Post-fix design:
      - server.py converts ClipwrightError(DEPENDENCY_MISSING) raised by
        _inspect_media directly to the envelope
      - process.resolve_tool is called exactly once inside media.py
      - server.py does not call resolve_tool directly
    """

    def test_dependency_missing_from_inspect_media_returns_error_envelope(
        self, sample_media: str
    ) -> None:
        """M-2: DEPENDENCY_MISSING raised by _inspect_media is converted to an
        error envelope by server.py (confirms the correct path for the Red check).

        Pins that after removing the leading resolve_tool from server.py,
        resolve_tool failure inside _inspect_media still propagates to the envelope.
        """
        from clipwright.errors import ClipwrightError as _CWE
        from clipwright.errors import ErrorCode as _EC

        # Patch _inspect_media directly in the server module
        with patch("clipwright.server._inspect_media") as mock_inspect:
            mock_inspect.side_effect = _CWE(
                _EC.DEPENDENCY_MISSING,
                "ffprobe not found",
                "Install with winget install Gyan.FFmpeg",
            )
            result = clipwright_inspect_media(path=sample_media)

        # DEPENDENCY_MISSING envelope is returned
        _assert_tool_error_result(result, "DEPENDENCY_MISSING")
        # hint is carried through (server.py uses ClipwrightError.hint)
        assert "winget" in result["error"]["hint"], (
            "hint must carry 'winget' through from ClipwrightError"
        )

    def test_resolve_tool_not_called_directly_from_server_on_success_path(
        self, sample_media: str
    ) -> None:
        """M-2: server.py does not call resolve_tool directly on the success path.

        When _inspect_media is mocked to return success, if server.py has a
        leading resolve_tool call, call_count >= 1. If server.py does not call
        resolve_tool directly, call_count == 0.
        """
        from clipwright.schemas import MediaInfo, RationalTimeModel

        mock_media_info = MediaInfo(
            path=sample_media,
            container="mp4",
            duration=RationalTimeModel(value=90.0, rate=30.0),
            streams=[],
        )
        with (
            patch("clipwright.process.resolve_tool") as mock_resolve,
            patch("clipwright.media.inspect_media", return_value=mock_media_info),
        ):
            result = clipwright_inspect_media(path=sample_media)

        # Valid envelope returned
        _assert_tool_result(result)
        # call_count == 0 if server.py does not call resolve_tool directly
        assert mock_resolve.call_count == 0, (
            f"server.py is calling resolve_tool directly "
            f"(call_count={mock_resolve.call_count}). "
            "Remove the leading resolve_tool call from server.py."
        )


# ===========================================================================
# F-06: exc exposure prevention tests for read_timeline / write_timeline
# ===========================================================================


class TestTimelineExcMessageNotExposed:
    """F-06 / ADR-LT-2: pin that the except blocks in read_timeline /
    write_timeline do not include {exc} content (internal paths etc.) in
    message.

    After L-3 / ADR-LT-1, otio_utils.load_timeline converts recognised
    failure modes (missing file, malformed/unparseable OTIO, non-Timeline
    schema) to ClipwrightError, so those go through the ClipwrightError
    passthrough path (exc.code/exc.message/exc.hint). Any exception outside
    that enumerated set that still reaches the except Exception fallback in
    server.py is classified as INTERNAL with a fixed generic message and hint
    (matching the clipwright_init_project pattern), never exposing {exc}
    content.
    """

    def _setup_project(self, tmp_path: Path, name: str = "test") -> str:
        """Initialise a test project and return project_dir."""
        project_dir = str(tmp_path / "proj")
        clipwright_init_project(project_dir=project_dir, name=name)
        return project_dir

    def test_read_timeline_otio_error_message_does_not_contain_exc_detail(
        self, tmp_path: Path
    ) -> None:
        """F-06: read_timeline file read failure message does not contain
        raw exception strings (internal paths etc.).

        When load_timeline raises ClipwrightError (L-3 applied), server.py
        uses only exc.message and does not embed {exc} in message.
        Confirms that internal paths are not in ClipwrightError.message.
        """
        self._setup_project(tmp_path)
        # Create a .otio file with invalid content
        bad_otio_path = tmp_path / "proj" / "bad.otio"
        bad_otio_path.write_text(
            "INVALID OTIO CONTENT - C:\\Users\\satoh\\secrets\\internal\\path.txt",
            encoding="utf-8",
        )

        result = clipwright_read_timeline(timeline_path=str(bad_otio_path))

        # ok=False with OTIO_ERROR
        _assert_tool_error_result(result, "OTIO_ERROR")
        message = result["error"]["message"]
        # Internal path strings (C:\Users\satoh etc.) must not be in message
        assert "satoh" not in message, (
            f"message contains an internal path (satoh): {message!r}"
        )
        assert "secrets" not in message, (
            f"message contains an internal path (secrets): {message!r}"
        )
        assert "internal" not in message, (
            f"message contains an internal path (internal): {message!r}"
        )

    def test_read_timeline_non_otio_exception_message_is_generic(
        self, tmp_path: Path
    ) -> None:
        """F-06 / ADR-LT-2: read_timeline also returns a generic message when
        an unexpected non-ClipwrightError exception occurs (no {exc} content).

        Pins that the except Exception fallback path in server.py classifies
        unexpected exceptions as INTERNAL (not OTIO_ERROR), matching the
        init_project INTERNAL boundary pattern.
        """
        project_dir = self._setup_project(tmp_path)

        # Mock load_timeline to raise a non-OTIO exception (RuntimeError)
        sensitive_detail = "C:\\Users\\satoh\\AppData\\internal_db_connection_string"
        with patch(
            "clipwright.server.load_timeline",
            side_effect=RuntimeError(f"internal error: {sensitive_detail}"),
        ):
            result = clipwright_read_timeline(project_dir=project_dir)

        _assert_tool_error_result(result, "INTERNAL")
        message = result["error"]["message"]
        # {exc} content must not be in message
        assert sensitive_detail not in message, (
            f"message contains RuntimeError detail ({sensitive_detail!r}): {message!r}"
        )
        assert "internal error" not in message, (
            f"message contains RuntimeError content ('internal error'): {message!r}"
        )

    def test_write_timeline_non_otio_exception_message_is_generic(
        self, tmp_path: Path
    ) -> None:
        """F-06 / ADR-LT-2: write_timeline also returns a generic message
        when an unexpected non-ClipwrightError exception occurs (no {exc}
        content).

        Pins that the except Exception fallback path in write_timeline
        classifies unexpected exceptions as INTERNAL (not OTIO_ERROR).
        """
        project_dir = self._setup_project(tmp_path)

        sensitive_detail = "C:\\Users\\satoh\\AppData\\project_file_secret.otio"
        with patch(
            "clipwright.server.load_timeline",
            side_effect=RuntimeError(f"load failed: {sensitive_detail}"),
        ):
            result = clipwright_write_timeline(
                project_dir=project_dir, operations=[], validate_only=False
            )

        _assert_tool_error_result(result, "INTERNAL")
        message = result["error"]["message"]
        # {exc} content must not be in message
        assert sensitive_detail not in message, (
            f"message contains RuntimeError detail ({sensitive_detail!r}): {message!r}"
        )
        assert "load failed" not in message, (
            f"message contains RuntimeError content ('load failed'): {message!r}"
        )

    def test_read_timeline_error_message_is_fixed_generic_string(
        self, tmp_path: Path
    ) -> None:
        """F-06 / ADR-LT-2: read_timeline's INTERNAL error message is a fixed
        generic string.

        Message has a fixed format and does not include variable exception detail.
        """
        project_dir = self._setup_project(tmp_path)

        with patch(
            "clipwright.server.load_timeline",
            side_effect=RuntimeError("unexpected internal detail xyz"),
        ):
            result = clipwright_read_timeline(project_dir=project_dir)

        _assert_tool_error_result(result, "INTERNAL")
        message = result["error"]["message"]
        hint = result["error"]["hint"]
        # Variable exception detail must not be in message
        assert "unexpected internal detail xyz" not in message, (
            f"message contains raw exception message: {message!r}"
        )
        # hint must be non-empty (actionable content)
        assert len(hint) > 0, "hint must be non-empty"

    def test_write_timeline_error_hint_is_actionable(self, tmp_path: Path) -> None:
        """F-06 / ADR-LT-2: write_timeline's INTERNAL error hint is a fixed
        actionable string."""
        project_dir = self._setup_project(tmp_path)

        with patch(
            "clipwright.server.load_timeline",
            side_effect=RuntimeError("unexpected detail abc"),
        ):
            result = clipwright_write_timeline(
                project_dir=project_dir, operations=[], validate_only=False
            )

        _assert_tool_error_result(result, "INTERNAL")
        hint = result["error"]["hint"]
        message = result["error"]["message"]
        # Raw exception message must not be in message
        assert "unexpected detail abc" not in message, (
            f"message contains raw exception message: {message!r}"
        )
        assert len(hint) > 0, "hint must be an actionable string"


# ===========================================================================
# ADR-LT-3: uninitialised project_dir pre-check for read_timeline / write_timeline
# ===========================================================================


class TestTimelineUninitialisedProjectDirPreCheck:
    """ADR-LT-3: read_timeline / write_timeline pre-check that
    <project_dir>/timeline.otio exists before calling load_timeline, so an
    uninitialised project_dir returns FILE_NOT_FOUND with a hint that names
    clipwright_init_project as the concrete next action (rather than a
    generic OTIO_ERROR from load_timeline failing on a missing file).
    """

    def test_read_timeline_project_dir_missing_timeline_returns_file_not_found(
        self, tmp_path: Path
    ) -> None:
        """An empty (uninitialised) project_dir passed to read_timeline
        returns FILE_NOT_FOUND with an init_project hint (ADR-LT-3)."""
        empty_project_dir = str(tmp_path / "uninitialised")
        Path(empty_project_dir).mkdir(parents=True)

        result = clipwright_read_timeline(project_dir=empty_project_dir)

        _assert_tool_error_result(result, "FILE_NOT_FOUND")
        hint = result["error"]["hint"]
        assert "clipwright_init_project" in hint, (
            f"hint must point to clipwright_init_project as the next action: {hint!r}"
        )

    def test_write_timeline_uninitialised_project_returns_file_not_found(
        self, tmp_path: Path
    ) -> None:
        """An empty (uninitialised) project_dir passed to write_timeline
        returns FILE_NOT_FOUND with an init_project hint (ADR-LT-3)."""
        empty_project_dir = str(tmp_path / "uninitialised")
        Path(empty_project_dir).mkdir(parents=True)

        result = clipwright_write_timeline(
            project_dir=empty_project_dir,
            operations=[
                {"op": "add_gap", "track": 0, "duration": {"value": 24.0, "rate": 24.0}}
            ],
            validate_only=False,
        )

        _assert_tool_error_result(result, "FILE_NOT_FOUND")
        hint = result["error"]["hint"]
        assert "clipwright_init_project" in hint, (
            f"hint must point to clipwright_init_project as the next action: {hint!r}"
        )


# ===========================================================================
# ADR-RD-16: lazy conversion cost contract for summarize_timeline
# ===========================================================================


class TestReadTimelineLazyCost:
    """ADR-RD-16: verify that summarize_timeline only converts clips/markers
    in the requested window, not discarding unwanted items after conversion.

    Uses monkeypatch to spy on _clip_to_dict and _marker_to_dict calls to
    ensure the conversion work is proportional to the returned data size,
    not the timeline size (P-1: per-item cost is skipped for window-external
    entries).
    """

    def _setup_project(self, tmp_path: Path, name: str = "test") -> str:
        """Initialise a test project and return project_dir."""
        project_dir = str(tmp_path / "proj")
        clipwright_init_project(project_dir=project_dir, name=name)
        return project_dir

    def _add_clips(self, project_dir: str, count: int) -> None:
        """Helper: add N clips to a timeline via write_timeline."""
        ops = [
            {
                "op": "add_clip",
                "track": 0,
                "media": {"target_url": f"file:///tmp/clip_{i:03d}.mp4"},
                "source_range": {
                    "start_time": {"value": 0.0, "rate": 30.0},
                    "duration": {"value": 10.0, "rate": 30.0},
                },
                "name": f"clip_{i:03d}",
            }
            for i in range(count)
        ]
        result = clipwright_write_timeline(
            project_dir=project_dir, operations=ops, validate_only=False
        )
        assert result.get("ok"), f"precondition: add_clip setup failed: {result}"

    def _add_markers(self, project_dir: str, count: int) -> None:
        """Helper: add N markers to a timeline via write_timeline."""
        ops = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": float(i), "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": f"marker_{i:03d}",
            }
            for i in range(count)
        ]
        result = clipwright_write_timeline(
            project_dir=project_dir, operations=ops, validate_only=False
        )
        assert result.get("ok"), f"precondition: add_marker setup failed: {result}"

    def test_t2_1_clips_limit_1_converts_one_clip_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T2-1: section='clips', limit=1 with 60 clips results in
        _clip_to_dict called exactly 1 time, not 60."""
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 60)

        import clipwright.otio_utils

        original_clip_to_dict = clipwright.otio_utils._clip_to_dict
        call_count = [0]

        def spy_clip_to_dict(*args: Any, **kwargs: Any) -> Any:
            call_count[0] += 1
            return original_clip_to_dict(*args, **kwargs)

        monkeypatch.setattr(clipwright.otio_utils, "_clip_to_dict", spy_clip_to_dict)

        result = clipwright_read_timeline(
            project_dir=project_dir, section="clips", limit=1
        )

        _assert_tool_result(result)
        data = result["data"]
        assert len(data["clips"]) == 1, f"expected 1 clip, got {len(data['clips'])}"
        assert call_count[0] == 1, (
            f"_clip_to_dict must be called 1 time, not {call_count[0]}"
        )

    def test_t2_2_clips_section_zero_markers_converted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T2-2: section='clips' does not convert any markers.

        SR-NEW: uses the same call-recording spy pattern as T2-1/T2-3
        (patch-time bound original + counter), asserted after the call,
        instead of a raise-on-call stub. The raise-on-call stub's failure
        used to be swallowed by the ADR-RD-17 (b) `except Exception` ->
        INTERNAL boundary guard, so a regression showed up only as the
        generic `_assert_tool_result` "ok must be True" message with no
        indication that `_marker_to_dict` was the culprit.
        """
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 10)
        self._add_markers(project_dir, 5)

        import clipwright.otio_utils

        original_marker_to_dict = clipwright.otio_utils._marker_to_dict
        call_count = [0]

        def spy_marker_to_dict(*args: Any, **kwargs: Any) -> Any:
            call_count[0] += 1
            return original_marker_to_dict(*args, **kwargs)

        monkeypatch.setattr(
            clipwright.otio_utils, "_marker_to_dict", spy_marker_to_dict
        )

        result = clipwright_read_timeline(
            project_dir=project_dir, section="clips", limit=50
        )

        _assert_tool_result(result)
        assert "clips" in result["data"]
        assert call_count[0] == 0, (
            "_marker_to_dict must not be called when section='clips', "
            f"was called {call_count[0]} times"
        )

    def test_t2_2_markers_section_zero_clips_converted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T2-2: section='markers' does not convert any clips.

        SR-NEW: uses the same call-recording spy pattern as T2-1/T2-3
        (patch-time bound original + counter), asserted after the call,
        instead of a raise-on-call stub. See the sibling clips-section
        test above for why the raise-on-call form under-diagnosed a
        regression through the ADR-RD-17 (b) boundary guard.
        """
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 10)
        self._add_markers(project_dir, 5)

        import clipwright.otio_utils

        original_clip_to_dict = clipwright.otio_utils._clip_to_dict
        call_count = [0]

        def spy_clip_to_dict(*args: Any, **kwargs: Any) -> Any:
            call_count[0] += 1
            return original_clip_to_dict(*args, **kwargs)

        monkeypatch.setattr(clipwright.otio_utils, "_clip_to_dict", spy_clip_to_dict)

        result = clipwright_read_timeline(
            project_dir=project_dir, section="markers", limit=50
        )

        _assert_tool_result(result)
        assert "markers" in result["data"]
        assert call_count[0] == 0, (
            "_clip_to_dict must not be called when section='markers', "
            f"was called {call_count[0]} times"
        )

    def test_t2_3_overview_call_counts_match_window_size(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T2-3: section=None (overview) calls conversion functions
        a number of times equal to min(limit, total_count)."""
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 25)
        self._add_markers(project_dir, 20)

        import clipwright.otio_utils

        original_clip_to_dict = clipwright.otio_utils._clip_to_dict
        original_marker_to_dict = clipwright.otio_utils._marker_to_dict
        clip_call_count = [0]
        marker_call_count = [0]

        def spy_clip_to_dict(*args: Any, **kwargs: Any) -> Any:
            clip_call_count[0] += 1
            return original_clip_to_dict(*args, **kwargs)

        def spy_marker_to_dict(*args: Any, **kwargs: Any) -> Any:
            marker_call_count[0] += 1
            return original_marker_to_dict(*args, **kwargs)

        monkeypatch.setattr(clipwright.otio_utils, "_clip_to_dict", spy_clip_to_dict)
        monkeypatch.setattr(
            clipwright.otio_utils, "_marker_to_dict", spy_marker_to_dict
        )

        result = clipwright_read_timeline(
            project_dir=project_dir, section=None, limit=10
        )

        _assert_tool_result(result)
        data = result["data"]
        expected_clip_count = min(10, 25)
        expected_marker_count = min(10, 20)

        assert len(data["clips"]) == expected_clip_count
        assert len(data["markers"]) == expected_marker_count
        assert clip_call_count[0] == expected_clip_count
        assert marker_call_count[0] == expected_marker_count

    def test_t2_4_marker_kind_filter_call_count_matches_results(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T2-4: _marker_to_dict is called once per matching marker
        in the window, not per total matching marker."""
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 10)

        ops = []
        for i in range(20):
            ops.append(
                {
                    "op": "add_marker",
                    "track": 0,
                    "marked_range": {
                        "start_time": {"value": float(i), "rate": 30.0},
                        "duration": {"value": 1.0, "rate": 30.0},
                    },
                    "name": f"marker_{i:03d}",
                    "metadata": {"kind": "caption" if i % 2 == 0 else "cue"},
                }
            )
        result = clipwright_write_timeline(
            project_dir=project_dir, operations=ops, validate_only=False
        )
        assert result.get("ok")

        import clipwright.otio_utils

        original_marker_to_dict = clipwright.otio_utils._marker_to_dict
        marker_call_count = [0]

        def spy_marker_to_dict(*args: Any, **kwargs: Any) -> Any:
            marker_call_count[0] += 1
            return original_marker_to_dict(*args, **kwargs)

        monkeypatch.setattr(
            clipwright.otio_utils, "_marker_to_dict", spy_marker_to_dict
        )

        result = clipwright_read_timeline(
            project_dir=project_dir,
            section="markers",
            limit=5,
            marker_kind="caption",
        )

        _assert_tool_result(result)
        data = result["data"]
        returned_count = len(data["markers"])

        assert marker_call_count[0] == returned_count

    def test_t2_5_offset_past_end_returns_invalid_input_no_conversion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T2-5: Offset past end returns INVALID_INPUT with no conversion."""
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 30)

        import clipwright.otio_utils

        original_clip_to_dict = clipwright.otio_utils._clip_to_dict
        clip_call_count = [0]

        def spy_clip_to_dict(*args: Any, **kwargs: Any) -> Any:
            clip_call_count[0] += 1
            return original_clip_to_dict(*args, **kwargs)

        monkeypatch.setattr(clipwright.otio_utils, "_clip_to_dict", spy_clip_to_dict)

        result = clipwright_read_timeline(
            project_dir=project_dir,
            section="clips",
            offset=100,
        )

        _assert_tool_error_result(result, "INVALID_INPUT")
        assert clip_call_count[0] == 0

    def test_t2_6_pagination_parity_clips_section(self, tmp_path: Path) -> None:
        """T2-6: Pagination metadata matches v0.40.0."""
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 60)

        result1 = clipwright_read_timeline(
            project_dir=project_dir, section="clips", limit=50
        )
        _assert_tool_result(result1)
        data1 = result1["data"]

        assert len(data1["clips"]) == 50
        assert data1.get("clips_truncated") is True
        assert data1.get("clips_next_offset") == 50

        result2 = clipwright_read_timeline(
            project_dir=project_dir, section="clips", offset=50, limit=50
        )
        _assert_tool_result(result2)
        data2 = result2["data"]

        assert len(data2["clips"]) == 10
        assert data2.get("clips_truncated") is False
        assert data2.get("clips_next_offset") is None

    def test_t2_6_pagination_parity_markers_with_kind_filter(
        self, tmp_path: Path
    ) -> None:
        """T2-6 extended: marker_kind filtering works with pagination."""
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 10)

        ops = []
        for i in range(60):
            ops.append(
                {
                    "op": "add_marker",
                    "track": 0,
                    "marked_range": {
                        "start_time": {"value": float(i), "rate": 30.0},
                        "duration": {"value": 1.0, "rate": 30.0},
                    },
                    "name": f"marker_{i:03d}",
                    "metadata": {"kind": "caption" if i % 2 == 0 else "cue"},
                }
            )
        result = clipwright_write_timeline(
            project_dir=project_dir, operations=ops, validate_only=False
        )
        assert result.get("ok")

        result1 = clipwright_read_timeline(
            project_dir=project_dir,
            section="markers",
            limit=20,
            marker_kind="caption",
        )
        _assert_tool_result(result1)
        data1 = result1["data"]

        assert len(data1["markers"]) == 20
        assert data1.get("markers_truncated") is True
        assert data1.get("markers_next_offset") == 20

    def test_t2_7_limit_clamp_501_to_500_with_warning(self, tmp_path: Path) -> None:
        """T2-7: limit=501 is clamped to 500 with warning."""
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 600)

        result = clipwright_read_timeline(
            project_dir=project_dir, section="clips", limit=501
        )

        _assert_tool_result(result)
        data = result["data"]
        warnings = result["warnings"]

        assert len(data["clips"]) == 500
        clamp_warning = [w for w in warnings if "limit" in w.lower() and "500" in w]
        assert len(clamp_warning) > 0

    def test_t2_8_summarize_exception_returns_internal_envelope_no_path_leak(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T2-8 (ADR-RD-17): summarize_timeline exception returns INTERNAL
        without path leaks (CWE-209).

        CR-NEW: also asserts the injected exception message itself never
        leaks into message/hint, so a regression from `except Exception:`
        to `except Exception as exc:` that mixes str(exc) into the
        envelope (ADR-RD-17 (a)/(b) CWE-209) fails this test directly,
        even though it contains no filesystem path.
        """
        project_dir = self._setup_project(tmp_path)
        self._add_clips(project_dir, 10)

        import clipwright.otio_utils

        injected_message = "Simulated summarize_timeline failure"

        def broken_summarize(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError(injected_message)

        monkeypatch.setattr(
            clipwright.otio_utils, "summarize_timeline", broken_summarize
        )

        result = clipwright_read_timeline(project_dir=project_dir)

        _assert_tool_error_result(result, "INTERNAL")
        error = result["error"]
        message = error["message"]
        hint = error["hint"]

        _assert_no_path_leak(message, hint, project_dir, tmp_path)
        # CR-NEW: individually asserted (never OR-joined) so a single
        # regression cannot be masked by the other still-passing condition.
        assert injected_message not in message, (
            f"error.message must not leak the injected exception text: {message!r}"
        )
        assert injected_message not in hint, (
            f"error.hint must not leak the injected exception text: {hint!r}"
        )
