"""test_schemas.py — Tests for SequenceClip schema (ADR-SEQ-1).

Covers schema-layer contract only:
  - model_config: extra="forbid", allow_inf_nan=False
  - media: required str
  - start_sec: float | None, default=None, ge=0 (negative rejected at schema layer)
  - end_sec:   float | None, default=None, gt=0 (zero/negative rejected at schema layer)
  - SequenceOptions is NOT defined in v0.1.0 (ADR-SEQ-1)

Range relations (start < end, end <= duration) are deferred to plan.py because
duration is unknown before probe.  Those tests belong in test_plan.py.

NOTE: The clips max_length=1000 constraint lives on the server-layer Field
annotation (clipwright_build_sequence argument), not on SequenceClip itself.
That constraint is exercised in test_server.py, not here.
"""

from __future__ import annotations

import math

import pytest
from clipwright_sequence.schemas import SequenceClip
from pydantic import ValidationError

# ===========================================================================
# SequenceClip — default construction and valid values
# ===========================================================================


class TestSequenceClipValidValues:
    """SequenceClip accepts valid combinations of (media, start_sec, end_sec)."""

    def test_media_only(self) -> None:
        """Only media is required; start_sec and end_sec default to None."""
        clip = SequenceClip(media="video.mp4")

        assert clip.media == "video.mp4"
        assert clip.start_sec is None
        assert clip.end_sec is None

    def test_all_fields_provided(self) -> None:
        """SequenceClip accepts all three fields."""
        clip = SequenceClip(media="a.mp4", start_sec=1.0, end_sec=5.0)

        assert clip.media == "a.mp4"
        assert clip.start_sec == pytest.approx(1.0)
        assert clip.end_sec == pytest.approx(5.0)

    def test_start_sec_zero_is_valid(self) -> None:
        """start_sec=0.0 is the ge=0 lower boundary and must be accepted."""
        clip = SequenceClip(media="b.mp4", start_sec=0.0, end_sec=10.0)

        assert clip.start_sec == pytest.approx(0.0)

    def test_end_sec_small_positive_is_valid(self) -> None:
        """end_sec must be strictly greater than 0 (gt=0); a tiny positive value is ok."""
        clip = SequenceClip(media="c.mp4", end_sec=0.001)

        assert clip.end_sec == pytest.approx(0.001)

    def test_float_precision_preserved(self) -> None:
        """Float values survive the Pydantic round-trip without unexpected coercion."""
        clip = SequenceClip(media="d.mp4", start_sec=1.5, end_sec=3.75)

        assert clip.start_sec == pytest.approx(1.5)
        assert clip.end_sec == pytest.approx(3.75)

    def test_media_path_with_directory(self) -> None:
        """media may be a path string (schema does not validate existence)."""
        clip = SequenceClip(media="/some/dir/video.mp4")

        assert clip.media == "/some/dir/video.mp4"

    def test_start_sec_none_default(self) -> None:
        """start_sec defaults to None when omitted."""
        clip = SequenceClip(media="e.mp4", end_sec=5.0)

        assert clip.start_sec is None

    def test_end_sec_none_default(self) -> None:
        """end_sec defaults to None when omitted."""
        clip = SequenceClip(media="f.mp4", start_sec=1.0)

        assert clip.end_sec is None


# ===========================================================================
# SequenceClip — media field: required str
# ===========================================================================


class TestSequenceClipMediaRequired:
    """media is a required field; omitting it raises ValidationError."""

    def test_media_required(self) -> None:
        """Omitting media raises ValidationError."""
        with pytest.raises(ValidationError):
            SequenceClip()  # type: ignore[call-arg]

    def test_media_must_be_str(self) -> None:
        """media accepts a str; passing a non-str type that cannot coerce is rejected."""
        # Pydantic v2 coerces int->str in lax mode; use explicit None to confirm required
        with pytest.raises(ValidationError):
            SequenceClip(media=None)  # type: ignore[arg-type]


# ===========================================================================
# SequenceClip — start_sec constraint: ge=0
# ===========================================================================


class TestSequenceClipStartSecConstraint:
    """start_sec must satisfy ge=0 (non-negative) when provided."""

    @pytest.mark.parametrize(
        "start_sec",
        [-0.001, -1.0, -100.0, -1e-9],
    )
    def test_negative_start_sec_rejected(self, start_sec: float) -> None:
        """Negative start_sec -> ValidationError (ge=0 schema constraint)."""
        with pytest.raises(ValidationError):
            SequenceClip(media="v.mp4", start_sec=start_sec)

    def test_zero_start_sec_accepted(self) -> None:
        """start_sec=0.0 is exactly the lower boundary and must pass."""
        clip = SequenceClip(media="v.mp4", start_sec=0.0)

        assert clip.start_sec == pytest.approx(0.0)

    def test_start_sec_none_accepted(self) -> None:
        """start_sec=None (omit) is always accepted regardless of ge=0."""
        clip = SequenceClip(media="v.mp4", start_sec=None)

        assert clip.start_sec is None


# ===========================================================================
# SequenceClip — end_sec constraint: gt=0
# ===========================================================================


class TestSequenceClipEndSecConstraint:
    """end_sec must satisfy gt=0 (strictly positive) when provided."""

    def test_zero_end_sec_rejected(self) -> None:
        """end_sec=0.0 violates gt=0 and must be rejected."""
        with pytest.raises(ValidationError):
            SequenceClip(media="v.mp4", end_sec=0.0)

    @pytest.mark.parametrize(
        "end_sec",
        [-0.001, -1.0, -100.0, -1e-9],
    )
    def test_negative_end_sec_rejected(self, end_sec: float) -> None:
        """Negative end_sec -> ValidationError (gt=0 schema constraint)."""
        with pytest.raises(ValidationError):
            SequenceClip(media="v.mp4", end_sec=end_sec)

    def test_positive_end_sec_accepted(self) -> None:
        """Any positive end_sec must pass the gt=0 constraint."""
        clip = SequenceClip(media="v.mp4", end_sec=0.5)

        assert clip.end_sec == pytest.approx(0.5)

    def test_end_sec_none_accepted(self) -> None:
        """end_sec=None (omit) is always accepted regardless of gt=0."""
        clip = SequenceClip(media="v.mp4", end_sec=None)

        assert clip.end_sec is None


# ===========================================================================
# SequenceClip — allow_inf_nan=False
# ===========================================================================


class TestSequenceClipInfNan:
    """SequenceClip rejects inf and nan for all float fields (allow_inf_nan=False)."""

    @pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
    def test_start_sec_inf_nan_rejected(self, value: float) -> None:
        """inf/nan for start_sec -> ValidationError."""
        with pytest.raises(ValidationError):
            SequenceClip(media="v.mp4", start_sec=value)

    @pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
    def test_end_sec_inf_nan_rejected(self, value: float) -> None:
        """inf/nan for end_sec -> ValidationError."""
        with pytest.raises(ValidationError):
            SequenceClip(media="v.mp4", end_sec=value)


# ===========================================================================
# SequenceClip — extra="forbid"
# ===========================================================================


class TestSequenceClipExtraForbid:
    """SequenceClip must reject unknown fields (extra='forbid')."""

    def test_unknown_field_rejected(self) -> None:
        """An unrecognised keyword argument raises ValidationError."""
        with pytest.raises(ValidationError):
            SequenceClip(media="v.mp4", unknown_field="value")  # type: ignore[call-arg]

    def test_typo_field_rejected(self) -> None:
        """A typo such as 'start' instead of 'start_sec' must not silently pass."""
        with pytest.raises(ValidationError):
            SequenceClip(media="v.mp4", start=1.0)  # type: ignore[call-arg]

    def test_end_typo_field_rejected(self) -> None:
        """A typo such as 'end' instead of 'end_sec' must not silently pass."""
        with pytest.raises(ValidationError):
            SequenceClip(media="v.mp4", end=5.0)  # type: ignore[call-arg]


# ===========================================================================
# SequenceClip — Field(description=...) present on all fields
# ===========================================================================


class TestSequenceClipFieldDescriptions:
    """All SequenceClip fields must carry a non-empty description (NFR-1 self-describing API)."""

    def test_media_has_description(self) -> None:
        field_info = SequenceClip.model_fields["media"]
        assert field_info.description, "media field must have a non-empty description"

    def test_start_sec_has_description(self) -> None:
        field_info = SequenceClip.model_fields["start_sec"]
        assert field_info.description, (
            "start_sec field must have a non-empty description"
        )

    def test_end_sec_has_description(self) -> None:
        field_info = SequenceClip.model_fields["end_sec"]
        assert field_info.description, "end_sec field must have a non-empty description"


# ===========================================================================
# SequenceOptions — must NOT be defined (ADR-SEQ-1)
# ===========================================================================


class TestSequenceOptionsNotDefined:
    """SequenceOptions is deliberately absent in v0.1.0 (ADR-SEQ-1).

    sequence v0.1.0 has no adjustable parameters; an empty model would be
    dead schema.  SequenceOptions will be introduced when concrete options
    (e.g. transition hints) are added.
    """

    def test_sequence_options_not_importable(self) -> None:
        """Importing SequenceOptions from clipwright_sequence.schemas must fail."""
        import clipwright_sequence.schemas as seq_schemas

        assert not hasattr(seq_schemas, "SequenceOptions"), (
            "SequenceOptions must NOT be defined in schemas.py (ADR-SEQ-1). "
            "Introduce it only when concrete options exist."
        )


# ===========================================================================
# No redefinition of core types
# ===========================================================================


def test_sequence_schemas_does_not_redefine_core_types() -> None:
    """clipwright_sequence.schemas must not redefine core common types."""
    # core types must be importable
    import clipwright_sequence.schemas as seq_schemas
    from clipwright.schemas import Artifact, MediaRef, ToolResult  # noqa: F401

    assert not hasattr(seq_schemas, "MediaRef"), (
        "schemas.py must not redefine MediaRef from core"
    )
    assert not hasattr(seq_schemas, "Artifact"), (
        "schemas.py must not redefine Artifact from core"
    )
    assert not hasattr(seq_schemas, "ToolResult"), (
        "schemas.py must not redefine ToolResult from core"
    )
