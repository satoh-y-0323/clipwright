"""test_envelope_typed.py — Typed ToolResult contract tests (Red phase).

Covers:
- ok_result returns a ToolResult instance (not a plain dict)
- error_result returns a ToolResult instance with ok=False and error populated
- to_tool_result(dict) -> ToolResult conversion (new function, not yet implemented)
- Artifact extra="ignore" model_config (M-002)
- Wire-compatibility: model_dump(mode="json") key set
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from clipwright.schemas import Artifact, ToolError, ToolResult

# ---------------------------------------------------------------------------
# to_tool_result import — expected to fail until implemented (Red)
# ---------------------------------------------------------------------------
try:
    from clipwright.envelope import to_tool_result  # type: ignore[attr-defined]

    _TO_TOOL_RESULT_AVAILABLE = True
except ImportError:
    _TO_TOOL_RESULT_AVAILABLE = False


# ===========================================================================
# ok_result returns ToolResult instance
# ===========================================================================


class TestOkResultTyped:
    """ok_result must return a ToolResult instance, not a plain dict."""

    def test_returns_tool_result_instance(self) -> None:
        """ok_result() returns a ToolResult Pydantic model instance."""
        from clipwright.envelope import ok_result

        result = ok_result("Processing complete")
        assert isinstance(result, ToolResult), (
            f"Expected ToolResult instance, got {type(result)}"
        )

    def test_ok_is_true(self) -> None:
        """ok_result() produces ok=True on the ToolResult."""
        from clipwright.envelope import ok_result

        result = ok_result("Done")
        assert result.ok is True  # type: ignore[union-attr]

    def test_error_is_none(self) -> None:
        """ok_result() produces error=None on the ToolResult."""
        from clipwright.envelope import ok_result

        result = ok_result("Done")
        assert result.error is None  # type: ignore[union-attr]

    def test_summary_stored(self) -> None:
        """ok_result() stores summary in the ToolResult."""
        from clipwright.envelope import ok_result

        result = ok_result("Inspected 3 clips")
        assert result.summary == "Inspected 3 clips"  # type: ignore[union-attr]

    def test_defaults_empty(self) -> None:
        """data / artifacts / warnings default to empty values."""
        from clipwright.envelope import ok_result

        result = ok_result("ok")
        assert result.data == {}  # type: ignore[union-attr]
        assert result.artifacts == []  # type: ignore[union-attr]
        assert result.warnings == []  # type: ignore[union-attr]

    def test_data_included(self) -> None:
        """data argument is stored in the ToolResult."""
        from clipwright.envelope import ok_result

        result = ok_result("ok", data={"clip_count": 5})
        assert result.data["clip_count"] == 5  # type: ignore[union-attr,index]

    def test_artifacts_included(self) -> None:
        """artifacts argument is stored in the ToolResult."""
        from clipwright.envelope import ok_result

        art = {"role": "timeline", "path": "/out/t.otio", "format": "otio"}
        result = ok_result("ok", artifacts=[art])
        # artifacts must be list[Artifact], not list[dict]
        assert len(result.artifacts) == 1  # type: ignore[union-attr]
        assert isinstance(result.artifacts[0], Artifact)  # type: ignore[union-attr]

    def test_warnings_included(self) -> None:
        """warnings argument is stored in the ToolResult."""
        from clipwright.envelope import ok_result

        result = ok_result("ok", warnings=["VFR detected"])
        assert "VFR detected" in result.warnings  # type: ignore[union-attr]


# ===========================================================================
# error_result returns ToolResult instance
# ===========================================================================


class TestErrorResultTyped:
    """error_result must return a ToolResult instance with ok=False."""

    def test_returns_tool_result_instance(self) -> None:
        """error_result() returns a ToolResult Pydantic model instance."""
        from clipwright.envelope import error_result

        result = error_result("FILE_NOT_FOUND", "File not found", "Check the path")
        assert isinstance(result, ToolResult), (
            f"Expected ToolResult instance, got {type(result)}"
        )

    def test_ok_is_false(self) -> None:
        """error_result() produces ok=False on the ToolResult."""
        from clipwright.envelope import error_result

        result = error_result("INVALID_INPUT", "Bad input", "Fix it")
        assert result.ok is False  # type: ignore[union-attr]

    def test_error_code_matches(self) -> None:
        """error_result() stores code in error.code."""
        from clipwright.envelope import error_result

        result = error_result(
            "DEPENDENCY_MISSING", "ffprobe not found", "Install FFmpeg"
        )
        assert result.error is not None  # type: ignore[union-attr]
        assert result.error.code == "DEPENDENCY_MISSING"  # type: ignore[union-attr]

    def test_error_message_matches(self) -> None:
        """error_result() stores message in error.message."""
        from clipwright.envelope import error_result

        result = error_result("OTIO_ERROR", "Failed to parse OTIO file", "hint")
        assert result.error is not None  # type: ignore[union-attr]
        assert result.error.message == "Failed to parse OTIO file"  # type: ignore[union-attr]

    def test_error_hint_matches(self) -> None:
        """error_result() stores hint in error.hint."""
        from clipwright.envelope import error_result

        result = error_result(
            "DEPENDENCY_MISSING", "ffprobe not found", "winget install Gyan.FFmpeg"
        )
        assert result.error is not None  # type: ignore[union-attr]
        assert result.error.hint == "winget install Gyan.FFmpeg"  # type: ignore[union-attr]

    def test_error_is_tool_error_instance(self) -> None:
        """error_result().error is a ToolError instance."""
        from clipwright.envelope import error_result

        result = error_result("PROBE_FAILED", "Parse failed", "Check ffprobe")
        assert isinstance(result.error, ToolError)  # type: ignore[union-attr]


# ===========================================================================
# to_tool_result — new function (not yet implemented → Red)
# ===========================================================================

_SKIP_TO_TOOL_RESULT = pytest.mark.skipif(
    not _TO_TOOL_RESULT_AVAILABLE,
    reason="to_tool_result not yet implemented — expected Red",
)


@pytest.mark.xfail(
    not _TO_TOOL_RESULT_AVAILABLE,
    reason="to_tool_result() not yet implemented (Red — feature not implemented)",
    strict=True,
)
class TestToToolResult:
    """to_tool_result(dict) -> ToolResult conversion contract."""

    def test_success_dict_returns_tool_result(self) -> None:
        """Success dict is converted to a ToolResult instance."""
        d: dict[str, Any] = {
            "ok": True,
            "summary": "Done",
            "data": {"count": 3},
            "artifacts": [
                {"role": "timeline", "path": "/out/t.otio", "format": "otio"}
            ],
            "warnings": [],
        }
        result = to_tool_result(d)
        assert isinstance(result, ToolResult)

    def test_success_dict_ok_is_true(self) -> None:
        """Converted success ToolResult has ok=True."""
        d: dict[str, Any] = {
            "ok": True,
            "summary": "Media inspected",
            "data": {},
            "artifacts": [],
            "warnings": [],
        }
        result = to_tool_result(d)
        assert result.ok is True

    def test_success_dict_artifacts_coerced_to_artifact_list(self) -> None:
        """Artifacts dicts are coerced to list[Artifact] via model_validate."""
        d: dict[str, Any] = {
            "ok": True,
            "summary": "ok",
            "data": {},
            "artifacts": [{"role": "output", "path": "/out/v.mp4", "format": "mp4"}],
            "warnings": [],
        }
        result = to_tool_result(d)
        assert len(result.artifacts) == 1
        assert isinstance(result.artifacts[0], Artifact)
        assert result.artifacts[0].role == "output"

    def test_failure_dict_returns_tool_result(self) -> None:
        """Failure dict is converted to a ToolResult instance with ok=False."""
        d: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": "FILE_NOT_FOUND",
                "message": "File not found",
                "hint": "Check the path",
            },
        }
        result = to_tool_result(d)
        assert isinstance(result, ToolResult)
        assert result.ok is False

    def test_failure_dict_error_is_tool_error(self) -> None:
        """Failure dict produces a ToolError in result.error."""
        d: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": "INVALID_INPUT",
                "message": "bad input",
                "hint": "fix it",
            },
        }
        result = to_tool_result(d)
        assert isinstance(result.error, ToolError)
        assert result.error.code == "INVALID_INPUT"

    def test_failure_dict_error_code_message_hint(self) -> None:
        """Failure dict error fields are preserved in the ToolError."""
        d: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": "PROBE_FAILED",
                "message": "ffprobe parse error",
                "hint": "Check ffprobe installation",
            },
        }
        result = to_tool_result(d)
        assert result.error is not None
        assert result.error.code == "PROBE_FAILED"
        assert result.error.message == "ffprobe parse error"
        assert result.error.hint == "Check ffprobe installation"


# ===========================================================================
# Artifact extra="ignore" (M-002)
# ===========================================================================


class TestArtifactExtraIgnore:
    """Artifact model_config must have extra='ignore' so unknown keys are silently dropped."""

    def test_extra_key_is_ignored_via_model_validate(self) -> None:
        """A dict with extra keys must not raise ValidationError (M-002)."""
        d: dict[str, Any] = {
            "role": "timeline",
            "path": "/out/t.otio",
            "format": "otio",
            "unknown_extra_key": "should be ignored",
        }
        # Before extra="ignore" is added, Pydantic v2 raises ValidationError.
        # After the fix this must NOT raise.
        artifact = Artifact.model_validate(d)
        assert artifact.role == "timeline"
        assert not hasattr(artifact, "unknown_extra_key")

    @pytest.mark.xfail(
        reason=(
            "Artifact.model_config extra='ignore' not yet set — "
            "extra keys raise ValidationError (Red)"
        ),
        strict=True,
    )
    def test_extra_key_causes_validation_error_before_fix(self) -> None:
        """Demonstrate that without extra='ignore' the dict would fail validation.

        This xfail test acts as a regression guard: once extra='ignore' is added,
        this test will xpass and the test above will pass.
        """
        d: dict[str, Any] = {
            "role": "timeline",
            "path": "/out/t.otio",
            "format": "otio",
            "unknown_extra_key": "should be ignored",
        }
        with pytest.raises(ValidationError):
            Artifact.model_validate(d)

    @pytest.mark.xfail(
        reason="to_tool_result not yet implemented (Red — feature not implemented)",
        strict=True,
    )
    def test_extra_key_ignored_via_to_tool_result(self) -> None:
        """Extra keys in artifacts list items are ignored when going through to_tool_result."""
        d: dict[str, Any] = {
            "ok": True,
            "summary": "ok",
            "data": {},
            "artifacts": [
                {
                    "role": "output",
                    "path": "/out/v.mp4",
                    "format": "mp4",
                    "extra_field": "ignored",
                }
            ],
            "warnings": [],
        }
        result = to_tool_result(d)
        assert len(result.artifacts) == 1
        assert result.artifacts[0].format == "mp4"


# ===========================================================================
# Wire compatibility: model_dump(mode="json") key set
# ===========================================================================


class TestWireCompatibility:
    """ok_result().model_dump(mode='json') must include the standard envelope keys."""

    def test_model_dump_contains_required_keys(self) -> None:
        """Success ToolResult serialises to a dict containing standard envelope keys."""
        from clipwright.envelope import ok_result

        result = ok_result("Done")
        dumped = result.model_dump(mode="json")  # type: ignore[union-attr]
        required_keys = {"ok", "summary", "data", "artifacts", "warnings"}
        assert required_keys.issubset(set(dumped.keys())), (
            f"Missing keys: {required_keys - set(dumped.keys())}"
        )

    def test_model_dump_ok_is_true(self) -> None:
        """ok key in model_dump result is True."""
        from clipwright.envelope import ok_result

        result = ok_result("Done")
        dumped = result.model_dump(mode="json")  # type: ignore[union-attr]
        assert dumped["ok"] is True

    def test_new_tool_result_unified_model_has_ok_bool(self) -> None:
        """New unified ToolResult allows ok=False (no Literal[True] constraint)."""
        # This tests that the NEW ToolResult definition accepts ok=False.
        # Current ToolResult has ok: Literal[True], so this raises ValidationError.
        # After the redefinition to ok: bool, this must succeed.
        try:
            result = ToolResult(
                ok=False, error=ToolError(code="X", message="m", hint="h")
            )
            assert result.ok is False
        except (ValidationError, TypeError) as exc:
            pytest.fail(
                f"ToolResult with ok=False must be valid after redefinition, got: {exc}"
            )

    def test_new_tool_result_error_field_exists(self) -> None:
        """New unified ToolResult must have an error field (not present in old model)."""
        result = ToolResult(ok=True, summary="ok")
        # error field must exist and default to None
        assert hasattr(result, "error"), "ToolResult must have an 'error' field"
        assert result.error is None

    def test_new_tool_result_summary_is_optional(self) -> None:
        """New unified ToolResult allows summary=None (for error-only results)."""
        # Old ToolResult required summary: str (no default).
        # New definition: summary: str | None = None
        try:
            result = ToolResult(
                ok=False, error=ToolError(code="X", message="m", hint="h")
            )
            assert result.summary is None
        except (ValidationError, TypeError) as exc:
            pytest.fail(
                f"ToolResult with summary=None must be valid after redefinition, got: {exc}"
            )
