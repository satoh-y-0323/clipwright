"""Tests for ExtractFramesOptions schema validation."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from clipwright_frames.schemas import ExtractFramesOptions

# ===========================================================================
# Default construction
# ===========================================================================


class TestExtractFramesOptionsDefaults:
    """Model must be constructable with all fields omitted, and each field must
    carry the correct default value as specified in architecture §7."""

    def test_build_with_no_args(self) -> None:
        """ExtractFramesOptions() must succeed without any arguments."""
        opts = ExtractFramesOptions()

        assert opts.mode == "interval"
        assert opts.interval_sec == pytest.approx(10.0)
        assert opts.scene_timeline is None
        assert opts.timestamps == []
        assert opts.format == "jpeg"
        assert opts.quality == 2
        assert opts.max_width is None

    def test_default_mode(self) -> None:
        """Default mode must be 'interval'."""
        opts = ExtractFramesOptions()
        assert opts.mode == "interval"

    def test_default_interval_sec(self) -> None:
        """Default interval_sec must be 10.0."""
        opts = ExtractFramesOptions()
        assert opts.interval_sec == pytest.approx(10.0)

    def test_default_scene_timeline(self) -> None:
        """Default scene_timeline must be None."""
        opts = ExtractFramesOptions()
        assert opts.scene_timeline is None

    def test_default_timestamps(self) -> None:
        """Default timestamps must be an empty list."""
        opts = ExtractFramesOptions()
        assert opts.timestamps == []

    def test_default_format(self) -> None:
        """Default format must be 'jpeg'."""
        opts = ExtractFramesOptions()
        assert opts.format == "jpeg"

    def test_default_quality(self) -> None:
        """Default quality must be 2."""
        opts = ExtractFramesOptions()
        assert opts.quality == 2

    def test_default_max_width(self) -> None:
        """Default max_width must be None."""
        opts = ExtractFramesOptions()
        assert opts.max_width is None


# ===========================================================================
# timestamps: default_factory (instances must not share the list)
# ===========================================================================


class TestTimestampsDefaultFactory:
    """timestamps must use default_factory=list so instances do not share a list."""

    def test_timestamps_not_shared_between_instances(self) -> None:
        """Mutating timestamps on one instance must not affect another instance."""
        a = ExtractFramesOptions()
        b = ExtractFramesOptions()
        a.timestamps.append(1.0)

        assert b.timestamps == [], (
            "timestamps must use default_factory=list; "
            "instances are sharing the same list object."
        )

    def test_timestamps_is_independent_list(self) -> None:
        """Two default instances must return different list objects for timestamps."""
        a = ExtractFramesOptions()
        b = ExtractFramesOptions()
        assert a.timestamps is not b.timestamps


# ===========================================================================
# mode: valid values accepted
# ===========================================================================


@pytest.mark.parametrize("mode", ["interval", "scene", "timestamps"])
def test_valid_mode_accepted(mode: str) -> None:
    """Values in Literal['interval', 'scene', 'timestamps'] must be accepted."""
    opts = ExtractFramesOptions(mode=mode)
    assert opts.mode == mode


# ===========================================================================
# mode: invalid values rejected
# ===========================================================================


@pytest.mark.parametrize(
    "invalid_mode",
    ["Interval", "INTERVAL", "keyframe", "auto", "", "none", "Scene", "Timestamps"],
)
def test_invalid_mode_rejected(invalid_mode: str) -> None:
    """Values outside Literal['interval', 'scene', 'timestamps'] must raise ValidationError."""
    with pytest.raises(ValidationError):
        ExtractFramesOptions(mode=invalid_mode)  # type: ignore[arg-type]


# ===========================================================================
# format: valid values accepted
# ===========================================================================


@pytest.mark.parametrize("fmt", ["jpeg", "png"])
def test_valid_format_accepted(fmt: str) -> None:
    """Values in Literal['jpeg', 'png'] must be accepted."""
    opts = ExtractFramesOptions(format=fmt)
    assert opts.format == fmt


# ===========================================================================
# format: invalid values rejected
# ===========================================================================


@pytest.mark.parametrize(
    "invalid_format",
    ["jpg", "JPEG", "PNG", "webp", "gif", "bmp", "tiff", ""],
)
def test_invalid_format_rejected(invalid_format: str) -> None:
    """Values outside Literal['jpeg', 'png'] must raise ValidationError."""
    with pytest.raises(ValidationError):
        ExtractFramesOptions(format=invalid_format)  # type: ignore[arg-type]


# ===========================================================================
# interval_sec: valid values accepted (gt=0.0)
# ===========================================================================


@pytest.mark.parametrize("interval", [0.001, 0.1, 1.0, 10.0, 60.0, 3600.0])
def test_valid_interval_sec_accepted(interval: float) -> None:
    """Values > 0.0 must be accepted as interval_sec."""
    opts = ExtractFramesOptions(interval_sec=interval)
    assert opts.interval_sec == pytest.approx(interval)


# ===========================================================================
# interval_sec: constraint violations (gt=0.0 means 0 and negatives rejected)
# ===========================================================================


@pytest.mark.parametrize("invalid_interval", [0.0, -0.001, -1.0, -10.0, -100.0])
def test_invalid_interval_sec_rejected(invalid_interval: float) -> None:
    """interval_sec <= 0.0 must raise ValidationError (constraint: gt=0.0)."""
    with pytest.raises(ValidationError):
        ExtractFramesOptions(interval_sec=invalid_interval)


# ===========================================================================
# quality: valid values accepted (ge=1, le=31)
# ===========================================================================


@pytest.mark.parametrize("quality", [1, 2, 10, 20, 30, 31])
def test_valid_quality_accepted(quality: int) -> None:
    """Values in [1, 31] must be accepted as quality."""
    opts = ExtractFramesOptions(quality=quality)
    assert opts.quality == quality


# ===========================================================================
# quality: constraint violations
# ===========================================================================


def test_quality_zero_rejected() -> None:
    """quality=0 must raise ValidationError (constraint: ge=1)."""
    with pytest.raises(ValidationError):
        ExtractFramesOptions(quality=0)


def test_quality_negative_rejected() -> None:
    """quality < 0 must raise ValidationError (constraint: ge=1)."""
    with pytest.raises(ValidationError):
        ExtractFramesOptions(quality=-1)


def test_quality_32_rejected() -> None:
    """quality=32 must raise ValidationError (constraint: le=31)."""
    with pytest.raises(ValidationError):
        ExtractFramesOptions(quality=32)


def test_quality_above_31_rejected() -> None:
    """quality > 31 must raise ValidationError (constraint: le=31)."""
    with pytest.raises(ValidationError):
        ExtractFramesOptions(quality=100)


# ===========================================================================
# max_width: valid values accepted (gt=0 when specified; None is allowed)
# ===========================================================================


def test_max_width_none_accepted() -> None:
    """max_width=None must be accepted (no resize)."""
    opts = ExtractFramesOptions(max_width=None)
    assert opts.max_width is None


@pytest.mark.parametrize("width", [1, 2, 100, 320, 640, 1280, 1920, 3840])
def test_valid_max_width_accepted(width: int) -> None:
    """Positive integer values must be accepted as max_width."""
    opts = ExtractFramesOptions(max_width=width)
    assert opts.max_width == width


# ===========================================================================
# max_width: constraint violations (gt=0)
# ===========================================================================


def test_max_width_zero_rejected() -> None:
    """max_width=0 must raise ValidationError (constraint: gt=0)."""
    with pytest.raises(ValidationError):
        ExtractFramesOptions(max_width=0)


def test_max_width_negative_rejected() -> None:
    """max_width < 0 must raise ValidationError (constraint: gt=0)."""
    with pytest.raises(ValidationError):
        ExtractFramesOptions(max_width=-1)


def test_max_width_large_negative_rejected() -> None:
    """max_width=-100 must raise ValidationError (constraint: gt=0)."""
    with pytest.raises(ValidationError):
        ExtractFramesOptions(max_width=-100)


# ===========================================================================
# model_config: extra="forbid"
# ===========================================================================


class TestExtraForbid:
    """Unknown fields must be rejected (model_config extra='forbid')."""

    def test_unknown_field_rejected(self) -> None:
        """Passing an unknown keyword must raise ValidationError."""
        with pytest.raises(ValidationError):
            ExtractFramesOptions(unknown_field=123)  # type: ignore[call-arg]

    def test_multiple_unknown_fields_rejected(self) -> None:
        """Multiple unknown keywords must raise ValidationError."""
        with pytest.raises(ValidationError):
            ExtractFramesOptions(foo="bar", baz=42)  # type: ignore[call-arg]

    def test_typo_field_rejected(self) -> None:
        """A typo in a valid field name must be rejected as unknown."""
        with pytest.raises(ValidationError):
            ExtractFramesOptions(intervall_sec=5.0)  # type: ignore[call-arg]


# ===========================================================================
# model_config: allow_inf_nan=False
# ===========================================================================


class TestNanInfRejected:
    """NaN and Inf must be rejected for float fields (allow_inf_nan=False)."""

    def test_nan_interval_sec_rejected(self) -> None:
        """interval_sec=NaN must raise ValidationError."""
        with pytest.raises(ValidationError):
            ExtractFramesOptions(interval_sec=float("nan"))

    def test_inf_interval_sec_rejected(self) -> None:
        """interval_sec=Inf must raise ValidationError."""
        with pytest.raises(ValidationError):
            ExtractFramesOptions(interval_sec=float("inf"))

    def test_neg_inf_interval_sec_rejected(self) -> None:
        """interval_sec=-Inf must raise ValidationError."""
        with pytest.raises(ValidationError):
            ExtractFramesOptions(interval_sec=float("-inf"))

    def test_nan_in_timestamps_rejected(self) -> None:
        """timestamps containing NaN must raise ValidationError."""
        with pytest.raises(ValidationError):
            ExtractFramesOptions(timestamps=[1.0, float("nan")])

    def test_inf_in_timestamps_rejected(self) -> None:
        """timestamps containing Inf must raise ValidationError."""
        with pytest.raises(ValidationError):
            ExtractFramesOptions(timestamps=[1.0, float("inf")])

    def test_math_nan_interval_sec_rejected(self) -> None:
        """interval_sec=math.nan must raise ValidationError."""
        with pytest.raises(ValidationError):
            ExtractFramesOptions(interval_sec=math.nan)

    def test_math_inf_interval_sec_rejected(self) -> None:
        """interval_sec=math.inf must raise ValidationError."""
        with pytest.raises(ValidationError):
            ExtractFramesOptions(interval_sec=math.inf)


# ===========================================================================
# All fields specified together (valid)
# ===========================================================================


def test_all_fields_specified_accepted() -> None:
    """Model must accept all fields explicitly set to valid values simultaneously."""
    opts = ExtractFramesOptions(
        mode="scene",
        interval_sec=5.0,
        scene_timeline="/path/to/timeline.otio",
        timestamps=[1.0, 2.5, 5.0],
        format="png",
        quality=15,
        max_width=1280,
    )
    assert opts.mode == "scene"
    assert opts.interval_sec == pytest.approx(5.0)
    assert opts.scene_timeline == "/path/to/timeline.otio"
    assert opts.timestamps == [1.0, 2.5, 5.0]
    assert opts.format == "png"
    assert opts.quality == 15
    assert opts.max_width == 1280


def test_boundary_values_accepted() -> None:
    """Boundary values (interval_sec=0.001, quality=1/31, max_width=1) must be accepted."""
    opts = ExtractFramesOptions(
        interval_sec=0.001,
        quality=1,
        max_width=1,
    )
    assert opts.interval_sec == pytest.approx(0.001)
    assert opts.quality == 1
    assert opts.max_width == 1

    opts2 = ExtractFramesOptions(quality=31)
    assert opts2.quality == 31


# ===========================================================================
# Field existence in model_fields
# ===========================================================================


class TestFieldExistence:
    """All required fields must be registered in model_fields."""

    EXPECTED_FIELDS = {
        "mode",
        "interval_sec",
        "scene_timeline",
        "timestamps",
        "format",
        "quality",
        "max_width",
        "scene_sample",
    }

    def test_all_expected_fields_exist(self) -> None:
        """model_fields must contain exactly the 8 specified fields."""
        actual = set(ExtractFramesOptions.model_fields.keys())
        missing = self.EXPECTED_FIELDS - actual
        extra = actual - self.EXPECTED_FIELDS
        assert not missing, f"Missing fields: {missing}"
        assert not extra, f"Unexpected extra fields: {extra}"

    def test_mode_field_exists(self) -> None:
        """model_fields must contain 'mode'."""
        assert "mode" in ExtractFramesOptions.model_fields

    def test_interval_sec_field_exists(self) -> None:
        """model_fields must contain 'interval_sec'."""
        assert "interval_sec" in ExtractFramesOptions.model_fields

    def test_scene_timeline_field_exists(self) -> None:
        """model_fields must contain 'scene_timeline'."""
        assert "scene_timeline" in ExtractFramesOptions.model_fields

    def test_timestamps_field_exists(self) -> None:
        """model_fields must contain 'timestamps'."""
        assert "timestamps" in ExtractFramesOptions.model_fields

    def test_format_field_exists(self) -> None:
        """model_fields must contain 'format'."""
        assert "format" in ExtractFramesOptions.model_fields

    def test_quality_field_exists(self) -> None:
        """model_fields must contain 'quality'."""
        assert "quality" in ExtractFramesOptions.model_fields

    def test_max_width_field_exists(self) -> None:
        """model_fields must contain 'max_width'."""
        assert "max_width" in ExtractFramesOptions.model_fields

    def test_scene_sample_field_exists(self) -> None:
        """model_fields must contain 'scene_sample'."""
        assert "scene_sample" in ExtractFramesOptions.model_fields


# ===========================================================================
# Field descriptions (MCP schema AI-friendliness)
# ===========================================================================


class TestFieldDescriptions:
    """Field descriptions must be non-empty (MCP schema needs them for AI agents)."""

    def test_mode_description_is_non_empty(self) -> None:
        """mode field must have a non-empty description."""
        field_info = ExtractFramesOptions.model_fields["mode"]
        assert field_info.description, "mode field must have a description"

    def test_interval_sec_description_is_non_empty(self) -> None:
        """interval_sec field must have a non-empty description."""
        field_info = ExtractFramesOptions.model_fields["interval_sec"]
        assert field_info.description, "interval_sec field must have a description"

    def test_format_description_is_non_empty(self) -> None:
        """format field must have a non-empty description."""
        field_info = ExtractFramesOptions.model_fields["format"]
        assert field_info.description, "format field must have a description"

    def test_quality_description_mentions_jpeg(self) -> None:
        """quality description must mention 'jpeg' to indicate jpeg-only validity."""
        field_info = ExtractFramesOptions.model_fields["quality"]
        description = field_info.description or ""
        assert "jpeg" in description.lower(), (
            "quality description must mention 'jpeg' since -q:v is only valid for jpeg. "
            "Architecture §7 notes 'quality は jpeg のみ有効である旨を明記'."
        )

    def test_timestamps_description_is_non_empty(self) -> None:
        """timestamps field must have a non-empty description."""
        field_info = ExtractFramesOptions.model_fields["timestamps"]
        assert field_info.description, "timestamps field must have a description"

    def test_max_width_description_is_non_empty(self) -> None:
        """max_width field must have a non-empty description."""
        field_info = ExtractFramesOptions.model_fields["max_width"]
        assert field_info.description, "max_width field must have a description"


# ===========================================================================
# No redefinition of core types
# ===========================================================================


def test_extract_frames_options_does_not_redefine_core_types() -> None:
    """schemas.py must not redefine core common types (MediaRef/Artifact/ToolResult)."""
    from clipwright.schemas import Artifact, MediaRef, ToolResult  # noqa: F401

    import clipwright_frames.schemas as frames_schemas

    assert not hasattr(frames_schemas, "MediaRef"), (
        "schemas.py redefines MediaRef from core"
    )
    assert not hasattr(frames_schemas, "Artifact"), (
        "schemas.py redefines Artifact from core"
    )
    assert not hasattr(frames_schemas, "ToolResult"), (
        "schemas.py redefines ToolResult from core"
    )


# ===========================================================================
# scene_timeline: accepts valid path strings
# ===========================================================================


def test_scene_timeline_accepts_string_path() -> None:
    """scene_timeline must accept a string path value."""
    opts = ExtractFramesOptions(scene_timeline="/some/path/timeline.otio")
    assert opts.scene_timeline == "/some/path/timeline.otio"


def test_scene_timeline_accepts_none() -> None:
    """scene_timeline=None must be accepted (default case)."""
    opts = ExtractFramesOptions(scene_timeline=None)
    assert opts.scene_timeline is None


# ===========================================================================
# timestamps: accepts a list of valid floats
# ===========================================================================


def test_timestamps_accepts_valid_list() -> None:
    """timestamps must accept a list of valid float values."""
    opts = ExtractFramesOptions(timestamps=[0.0, 1.5, 10.0, 30.0])
    assert opts.timestamps == [0.0, 1.5, 10.0, 30.0]


def test_timestamps_accepts_empty_list() -> None:
    """timestamps must accept an empty list."""
    opts = ExtractFramesOptions(timestamps=[])
    assert opts.timestamps == []


# ===========================================================================
# scene_sample: Literal["midpoint","start","boundary"], default "midpoint"
# ===========================================================================


class TestSceneSample:
    """scene_sample must be Literal['midpoint','start','boundary'] with default 'midpoint'."""

    def test_default_scene_sample_is_midpoint(self) -> None:
        """Default scene_sample must be 'midpoint' when no value is supplied."""
        opts = ExtractFramesOptions()
        assert opts.scene_sample == "midpoint"

    @pytest.mark.parametrize("sample", ["midpoint", "start", "boundary"])
    def test_valid_scene_sample_accepted(self, sample: str) -> None:
        """Values in Literal['midpoint', 'start', 'boundary'] must be accepted."""
        opts = ExtractFramesOptions(scene_sample=sample)
        assert opts.scene_sample == sample

    @pytest.mark.parametrize(
        "invalid_sample",
        ["invalid", "center", "end", "", "MidPoint", "BOUNDARY", "Start"],
    )
    def test_invalid_scene_sample_rejected(self, invalid_sample: str) -> None:
        """Values outside Literal['midpoint', 'start', 'boundary'] must raise ValidationError.

        extra='forbid' rejects unknown fields; Literal rejects out-of-range values.
        """
        with pytest.raises(ValidationError):
            ExtractFramesOptions(scene_sample=invalid_sample)  # type: ignore[call-arg]

    def test_scene_sample_description_is_non_empty(self) -> None:
        """scene_sample field must have a non-empty description for AI consumers."""
        field_info = ExtractFramesOptions.model_fields.get("scene_sample")
        assert field_info is not None, "scene_sample field must exist in model_fields"
        assert field_info.description, "scene_sample field must have a description"
