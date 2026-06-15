"""test_schemas.py — Red tests for DetectScenesOptions.

Encodes the DetectScenesOptions specification from architecture §3 Schema Design
as executable test observations. All tests are expected to fail with ImportError
until clipwright_scene/schemas.py is implemented.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from clipwright_scene.schemas import DetectScenesOptions


# ===========================================================================
# Default construction
# ===========================================================================


class TestDetectScenesOptionsDefaults:
    """Model must be constructable with all fields omitted, and each field must
    carry the correct default value as specified in architecture §3."""

    def test_build_with_no_args(self) -> None:
        """DetectScenesOptions() must succeed without any arguments."""
        # Arrange / Act
        opts = DetectScenesOptions()

        # Assert
        assert opts.threshold == pytest.approx(0.3)
        assert opts.min_scene_duration == pytest.approx(1.0)
        assert opts.backend == "ffmpeg"

    def test_default_threshold(self) -> None:
        """Default threshold must be 0.3."""
        opts = DetectScenesOptions()
        assert opts.threshold == pytest.approx(0.3)

    def test_default_min_scene_duration(self) -> None:
        """Default min_scene_duration must be 1.0."""
        opts = DetectScenesOptions()
        assert opts.min_scene_duration == pytest.approx(1.0)

    def test_default_backend(self) -> None:
        """Default backend must be 'ffmpeg'."""
        opts = DetectScenesOptions()
        assert opts.backend == "ffmpeg"


# ===========================================================================
# threshold: valid value acceptance (0.0 <= threshold <= 1.0)
# ===========================================================================


@pytest.mark.parametrize(
    "threshold",
    [0.0, 0.1, 0.3, 0.5, 1.0],
)
def test_valid_threshold_accepted(threshold: float) -> None:
    """Values in [0.0, 1.0] must be accepted as threshold."""
    opts = DetectScenesOptions(threshold=threshold)
    assert opts.threshold == pytest.approx(threshold)


# ===========================================================================
# threshold: constraint violations
# ===========================================================================


@pytest.mark.parametrize(
    "threshold",
    [1.001, 1.5, 2.0, 10.0],
)
def test_threshold_above_1_rejected(threshold: float) -> None:
    """threshold > 1.0 must raise ValidationError (constraint: le=1.0)."""
    with pytest.raises(ValidationError):
        DetectScenesOptions(threshold=threshold)


@pytest.mark.parametrize(
    "threshold",
    [-0.001, -0.1, -1.0, -10.0],
)
def test_threshold_below_0_rejected(threshold: float) -> None:
    """threshold < 0.0 must raise ValidationError (constraint: ge=0.0)."""
    with pytest.raises(ValidationError):
        DetectScenesOptions(threshold=threshold)


# ===========================================================================
# min_scene_duration: valid value acceptance (ge=0.0)
# ===========================================================================


@pytest.mark.parametrize(
    "duration",
    [0.0, 0.5, 1.0, 5.0],
)
def test_valid_min_scene_duration_accepted(duration: float) -> None:
    """Values >= 0.0 must be accepted as min_scene_duration."""
    opts = DetectScenesOptions(min_scene_duration=duration)
    assert opts.min_scene_duration == pytest.approx(duration)


# ===========================================================================
# min_scene_duration: constraint violations
# ===========================================================================


@pytest.mark.parametrize(
    "duration",
    [-0.001, -0.5, -1.0, -5.0],
)
def test_negative_min_scene_duration_rejected(duration: float) -> None:
    """min_scene_duration < 0.0 must raise ValidationError (constraint: ge=0.0)."""
    with pytest.raises(ValidationError):
        DetectScenesOptions(min_scene_duration=duration)


# ===========================================================================
# backend: valid value acceptance
# ===========================================================================


def test_backend_ffmpeg_accepted() -> None:
    """backend='ffmpeg' must be accepted."""
    opts = DetectScenesOptions(backend="ffmpeg")
    assert opts.backend == "ffmpeg"


def test_backend_pyscenedetect_accepted() -> None:
    """backend='pyscenedetect' must be accepted."""
    opts = DetectScenesOptions(backend="pyscenedetect")
    assert opts.backend == "pyscenedetect"


# ===========================================================================
# backend: invalid values rejected
# ===========================================================================


@pytest.mark.parametrize(
    "invalid_backend",
    ["opencv", "auto", "FFmpeg", "PySceneDetect", "", "none", "scdet"],
)
def test_invalid_backend_rejected(invalid_backend: str) -> None:
    """Values outside Literal['ffmpeg', 'pyscenedetect'] must raise ValidationError."""
    with pytest.raises(ValidationError):
        DetectScenesOptions(backend=invalid_backend)  # type: ignore[arg-type]


# ===========================================================================
# All fields specified together
# ===========================================================================


def test_all_fields_specified_accepted() -> None:
    """Model must accept all fields explicitly set to valid values simultaneously."""
    opts = DetectScenesOptions(
        threshold=0.5,
        min_scene_duration=2.0,
        backend="pyscenedetect",
    )
    assert opts.threshold == pytest.approx(0.5)
    assert opts.min_scene_duration == pytest.approx(2.0)
    assert opts.backend == "pyscenedetect"


def test_all_fields_at_boundary_values() -> None:
    """Boundary values (0.0 / 1.0 / 0.0) must all be accepted together."""
    opts = DetectScenesOptions(
        threshold=0.0,
        min_scene_duration=0.0,
        backend="ffmpeg",
    )
    assert opts.threshold == pytest.approx(0.0)
    assert opts.min_scene_duration == pytest.approx(0.0)
    assert opts.backend == "ffmpeg"


# ===========================================================================
# Field description: AI-friendliness check
# ===========================================================================


class TestFieldDescriptions:
    """Field descriptions must support AI agent usage (architecture §3 AI-friendly docs)."""

    def test_threshold_description_mentions_ai_or_sensitive(self) -> None:
        """threshold description must contain 'AI' or 'sensitive' so agents can
        infer directional semantics (architecture §3 / ADR-3)."""
        field_info = DetectScenesOptions.model_fields["threshold"]
        description = field_info.description or ""
        assert "AI" in description or "sensitive" in description, (
            "threshold description must contain 'AI' or 'sensitive'. "
            "Architecture §3 requires AI-friendly directional guidance for the threshold field."
        )

    def test_threshold_description_is_non_empty(self) -> None:
        """threshold field must have a non-empty description."""
        field_info = DetectScenesOptions.model_fields["threshold"]
        assert field_info.description, "threshold field must have a description"

    def test_min_scene_duration_description_is_non_empty(self) -> None:
        """min_scene_duration field must have a non-empty description."""
        field_info = DetectScenesOptions.model_fields["min_scene_duration"]
        assert field_info.description, "min_scene_duration field must have a description"

    def test_backend_description_is_non_empty(self) -> None:
        """backend field must have a non-empty description."""
        field_info = DetectScenesOptions.model_fields["backend"]
        assert field_info.description, "backend field must have a description"


# ===========================================================================
# Field existence in model_fields
# ===========================================================================


class TestFieldExistence:
    """All required fields must be registered in model_fields."""

    def test_threshold_field_exists(self) -> None:
        """model_fields must contain 'threshold'."""
        assert "threshold" in DetectScenesOptions.model_fields

    def test_min_scene_duration_field_exists(self) -> None:
        """model_fields must contain 'min_scene_duration'."""
        assert "min_scene_duration" in DetectScenesOptions.model_fields

    def test_backend_field_exists(self) -> None:
        """model_fields must contain 'backend'."""
        assert "backend" in DetectScenesOptions.model_fields

    def test_no_extra_unexpected_fields(self) -> None:
        """model_fields must contain exactly the three specified fields
        (threshold, min_scene_duration, backend) and no others."""
        expected = {"threshold", "min_scene_duration", "backend"}
        actual = set(DetectScenesOptions.model_fields.keys())
        assert actual == expected, (
            f"Unexpected fields in model_fields: {actual - expected}. "
            f"Missing fields: {expected - actual}."
        )


# ===========================================================================
# No redefinition of core types
# ===========================================================================


def test_detect_scenes_options_does_not_redefine_core_types() -> None:
    """schemas.py must not redefine core common types (MediaRef/Artifact/ToolResult)."""
    # Core common types must be importable from the shared package
    from clipwright.schemas import Artifact, MediaRef, ToolResult  # noqa: F401

    # clipwright_scene.schemas must not redeclare any of these names
    import clipwright_scene.schemas as scene_schemas

    assert not hasattr(scene_schemas, "MediaRef"), (
        "schemas.py redefines MediaRef from core"
    )
    assert not hasattr(scene_schemas, "Artifact"), (
        "schemas.py redefines Artifact from core"
    )
    assert not hasattr(scene_schemas, "ToolResult"), (
        "schemas.py redefines ToolResult from core"
    )
