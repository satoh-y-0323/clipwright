"""test_server.py — Red tests for server.py (FastMCP 4 tools).

server.py is not yet implemented, so all tests are expected to fail
as Red due to unimplemented features.
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

# ---- Import unimplemented module
# (Red phase: ModuleNotFoundError or ImportError is expected)
# server.py does not exist yet, so the import below will fail.
# To structure the tests, the import is done directly without try/except or
# pytestmark, so ImportError/AttributeError will appear per test class/function
# as an explicit failure.

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

# Mark all tests as XFAIL until server.py is implemented
pytestmark = pytest.mark.xfail(
    not _SERVER_AVAILABLE,
    reason="server.py is not yet implemented (Red — feature not implemented)",
    strict=True,
)


# ===========================================================================
# Helpers
# ===========================================================================


def _assert_tool_result(result: Any) -> None:
    """Verify the ToolResult envelope contract (§6.3)."""
    assert isinstance(result, dict), "Return value must be a dict"
    assert result.get("ok") is True, "ok must be True on success"
    assert "summary" in result, "summary key is required"
    assert isinstance(result["summary"], str), "summary must be str"
    assert len(result["summary"]) > 0, "summary must not be empty"
    assert "data" in result, "data key is required"
    assert isinstance(result["data"], dict), "data must be dict"
    assert "artifacts" in result, "artifacts key is required"
    assert isinstance(result["artifacts"], list), "artifacts must be list"
    assert "warnings" in result, "warnings key is required"
    assert isinstance(result["warnings"], list), "warnings must be list"


def _assert_tool_error_result(result: Any, expected_code: str) -> None:
    """Verify the ToolErrorResult envelope contract (§6.4)."""
    assert isinstance(result, dict), "Return value must be a dict"
    assert result.get("ok") is False, "ok must be False on failure"
    assert "error" in result, "error key is required"
    error = result["error"]
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
        """Success path: when marker count > 50, data.markers is omitted and
        markers_truncated=true is returned (§13.5 DC-AM-001).

        Adds 51 markers via write_timeline before calling read_timeline.
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
        # Skip if precondition setup failed
        if write_result.get("ok") is not True:
            pytest.skip(f"write_timeline precondition setup failed: {write_result}")

        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(result)
        data = result["data"]
        assert data.get("markers_truncated") is True, (
            "data.markers_truncated=True is required when marker count > 50"
        )
        assert "marker_count" in data, (
            "data.marker_count is required when marker count > 50"
        )
        assert data["marker_count"] == 51, (
            f"marker_count must be 51 (actual: {data.get('marker_count')})"
        )
        assert "markers" not in data or data.get("markers") is None, (
            "data.markers must be omitted or None when marker count > 50"
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
        if write_result.get("ok") is not True:
            pytest.skip(f"write_timeline precondition setup failed: {write_result}")

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
    """F-06 fix: pin that the except blocks in read_timeline / write_timeline
    do not include {exc} content (internal paths etc.) in message.

    After L-3, otio_utils.load_timeline converts to ClipwrightError, so normal
    OTIO file errors go through the ClipwrightError path. However, even when a
    non-OTIO exception reaches the except Exception as exc path in server.py,
    a generic message must be returned without exposing the {exc} content.
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
        """F-06: read_timeline also returns a generic message when a
        non-OTIO exception occurs (no {exc} content).

        Pins that the except Exception as exc path in server.py returns a
        generic message.
        """
        project_dir = self._setup_project(tmp_path)

        # Mock load_timeline to raise a non-OTIO exception (RuntimeError)
        sensitive_detail = "C:\\Users\\satoh\\AppData\\internal_db_connection_string"
        with patch(
            "clipwright.server.load_timeline",
            side_effect=RuntimeError(f"internal error: {sensitive_detail}"),
        ):
            result = clipwright_read_timeline(project_dir=project_dir)

        _assert_tool_error_result(result, "OTIO_ERROR")
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
        """F-06: write_timeline also returns a generic message when a
        non-OTIO exception occurs (no {exc} content).

        Pins that the except Exception path in write_timeline returns a
        generic message.
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

        _assert_tool_error_result(result, "OTIO_ERROR")
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
        """F-06: read_timeline OTIO error message is a fixed generic string.

        Message has a fixed format and does not include variable exception detail.
        """
        project_dir = self._setup_project(tmp_path)

        with patch(
            "clipwright.server.load_timeline",
            side_effect=RuntimeError("unexpected internal detail xyz"),
        ):
            result = clipwright_read_timeline(project_dir=project_dir)

        _assert_tool_error_result(result, "OTIO_ERROR")
        message = result["error"]["message"]
        hint = result["error"]["hint"]
        # Variable exception detail must not be in message
        assert "unexpected internal detail xyz" not in message, (
            f"message contains raw exception message: {message!r}"
        )
        # hint must be non-empty (actionable content)
        assert len(hint) > 0, "hint must be non-empty"

    def test_write_timeline_error_hint_is_actionable(self, tmp_path: Path) -> None:
        """F-06: write_timeline OTIO error hint is a fixed string."""
        project_dir = self._setup_project(tmp_path)

        with patch(
            "clipwright.server.load_timeline",
            side_effect=RuntimeError("unexpected detail abc"),
        ):
            result = clipwright_write_timeline(
                project_dir=project_dir, operations=[], validate_only=False
            )

        _assert_tool_error_result(result, "OTIO_ERROR")
        hint = result["error"]["hint"]
        message = result["error"]["message"]
        # Raw exception message must not be in message
        assert "unexpected detail abc" not in message, (
            f"message contains raw exception message: {message!r}"
        )
        assert len(hint) > 0, "hint must be an actionable string"
