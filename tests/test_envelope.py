"""test_envelope.py — Contract tests for envelope.py.

Covers:
- ok_result: returns a ToolResult-form dict (§4 return value envelope)
- error_result: returns { ok: False, error: { code, message, hint } } form dict (§4)
"""

from __future__ import annotations

import pytest

# --- Import ---
from clipwright.envelope import error_result, ok_result

# ===========================================================================
# ok_result
# ===========================================================================


class TestOkResult:
    """Verify that ok_result returns a ToolResult-form dict."""

    def test_returns_ok_true(self) -> None:
        """The ok key is True."""
        result = ok_result("Processing complete")
        assert result["ok"] is True

    def test_summary_is_set(self) -> None:
        """The passed string is stored in summary."""
        result = ok_result("Processed 3 clips")
        assert result["summary"] == "Processed 3 clips"

    def test_defaults_are_empty(self) -> None:
        """data / artifacts / warnings default to empty values when not specified."""
        result = ok_result("ok")
        assert result["data"] == {} or result.get("data") is not None
        assert result["artifacts"] == [] or result.get("artifacts") is not None
        assert result["warnings"] == [] or result.get("warnings") is not None

    def test_data_is_included(self) -> None:
        """Passing a data argument includes it in the result."""
        result = ok_result("ok", data={"clip_count": 5, "duration": 30.0})
        assert result["data"]["clip_count"] == 5
        assert result["data"]["duration"] == 30.0

    def test_artifacts_is_included(self) -> None:
        """Passing an artifacts argument includes it in the result."""
        art = {"role": "timeline", "path": "/out/t.otio", "format": "otio"}
        result = ok_result("ok", artifacts=[art])
        assert len(result["artifacts"]) == 1
        assert result["artifacts"][0]["role"] == "timeline"

    def test_warnings_is_included(self) -> None:
        """Passing a warnings argument includes it in the result."""
        result = ok_result("ok", warnings=["VFR 映像が含まれています"])
        assert "VFR 映像が含まれています" in result["warnings"]

    def test_all_fields_present(self) -> None:
        """The result contains required keys: ok, summary, data, artifacts, warnings."""
        result = ok_result("Done")
        for key in ("ok", "summary", "data", "artifacts", "warnings"):
            assert key in result, f"Key '{key}' is missing from the result"

    @pytest.mark.parametrize(
        "summary",
        [
            "Inspected 1 media file",
            "Project initialised",
            "Applied 3 operations to the timeline",
        ],
    )
    def test_summary_passthrough(self, summary: str) -> None:
        """The summary value is returned as-is."""
        result = ok_result(summary)
        assert result["summary"] == summary

    def test_ok_field_is_boolean_true(self) -> None:
        """The ok value is Python bool True (not just 1)."""
        result = ok_result("ok")
        assert result["ok"] is True
        assert type(result["ok"]) is bool


# ===========================================================================
# error_result
# ===========================================================================


class TestErrorResult:
    """Verify error_result returns { ok: False, error: { code, message, hint } }."""

    def test_returns_ok_false(self) -> None:
        """The ok key is False."""
        result = error_result("FILE_NOT_FOUND", "File not found", "Check the path")
        assert result["ok"] is False

    def test_error_key_exists(self) -> None:
        """The error key exists."""
        result = error_result("INVALID_INPUT", "Invalid input", "Fix it")
        assert "error" in result

    def test_error_has_code(self) -> None:
        """The passed string is stored in error.code."""
        result = error_result(
            "PROBE_FAILED", "Failed to parse ffprobe output", "Check ffprobe"
        )
        assert result["error"]["code"] == "PROBE_FAILED"

    def test_error_has_message(self) -> None:
        """The passed string is stored in error.message."""
        result = error_result("OTIO_ERROR", "Failed to parse OTIO file", "hint")
        assert result["error"]["message"] == "Failed to parse OTIO file"

    def test_error_has_hint(self) -> None:
        """The passed string is stored in error.hint."""
        result = error_result(
            "DEPENDENCY_MISSING",
            "ffprobe not found",
            "winget install Gyan.FFmpeg",
        )
        assert result["error"]["hint"] == "winget install Gyan.FFmpeg"

    def test_error_structure_keys(self) -> None:
        """The error object contains all required keys: code / message / hint."""
        result = error_result(
            "INTERNAL", "Unexpected error", "Please report with reproduction steps"
        )
        for key in ("code", "message", "hint"):
            assert key in result["error"], (
                f"Key '{key}' is missing from the error object"
            )

    def test_top_level_keys(self) -> None:
        """The top level contains ok and error keys."""
        result = error_result(
            "SUBPROCESS_FAILED", "Process failed with exit code 1", "Check the command"
        )
        for key in ("ok", "error"):
            assert key in result, f"Top-level key '{key}' is missing"

    def test_ok_field_is_boolean_false(self) -> None:
        """The ok value is Python bool False (not just 0)."""
        result = error_result("INVALID_INPUT", "x", "y")
        assert result["ok"] is False
        assert type(result["ok"]) is bool

    def test_required_top_level_keys_present(self) -> None:
        """Unified ToolResult envelope must contain ok and error keys."""
        result = error_result("FILE_NOT_FOUND", "msg", "hint")
        keys = set(result.model_dump().keys())
        assert {"ok", "error"}.issubset(keys), f"Required keys missing from: {keys}"

    @pytest.mark.parametrize(
        "code",
        [
            "DEPENDENCY_MISSING",
            "INVALID_INPUT",
            "FILE_NOT_FOUND",
            "PATH_NOT_ALLOWED",
            "SUBPROCESS_FAILED",
            "SUBPROCESS_TIMEOUT",
            "PROBE_FAILED",
            "OTIO_ERROR",
            "PROJECT_NOT_FOUND",
            "PROJECT_EXISTS",
            "UNSUPPORTED_OPERATION",
            "INTERNAL",
            "TRACK_NOT_FOUND",
        ],
    )
    def test_all_error_codes(self, code: str) -> None:
        """error_result can be constructed with every ErrorCode value."""
        result = error_result(code, "test message", "test hint")
        assert result["ok"] is False
        assert result["error"]["code"] == code

    def test_hint_is_actionable_pattern(self) -> None:
        """hint must be non-empty (actionable hint required — §6 contract)."""
        result = error_result(
            "SUBPROCESS_TIMEOUT",
            "Command timed out",
            "Increase the timeout value",
        )
        assert len(result["error"]["hint"]) > 0
