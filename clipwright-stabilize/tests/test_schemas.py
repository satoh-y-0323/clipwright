"""test_schemas.py — Tests for clipwright_stabilize.schemas.

Verification points:
  - DetectShakeOptions: defaults (shakiness=5, accuracy=15, smoothing=12),
    range boundaries (ge/le end values and out-of-range rejection),
    extra forbidden, allow_inf_nan=False.
  - StabilizeDirective: required fields (version/kind/trf_path/shakiness/accuracy/smoothing),
    kind Literal["stabilize"], severity=None accepted, severity as float accepted,
    tool default "clipwright-stabilize", extra forbidden.
  - StabilizeDirective.recommendation: Literal["skip","apply"] | None,
    defaults to None, round-trip, extra="forbid" / allow_inf_nan=False maintained,
    backward compat — existing construction without recommendation still works (AC-10).

Requirements: FR-1-2 (DetectShakeOptions), FR-1-5 (StabilizeDirective),
architecture-report §3.
"""

from __future__ import annotations

import math

import pytest
from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
    DetectShakeOptions,
    StabilizeDirective,
)
from pydantic import ValidationError

# ===========================================================================
# DetectShakeOptions — defaults
# ===========================================================================


class TestDetectShakeOptionsDefaults:
    """Verify default construction and default field values (FR-1-2)."""

    def test_default_shakiness_is_5(self) -> None:
        """shakiness default must be 5."""
        opts = DetectShakeOptions()
        assert opts.shakiness == 5

    def test_default_accuracy_is_15(self) -> None:
        """accuracy default must be 15."""
        opts = DetectShakeOptions()
        assert opts.accuracy == 15

    def test_default_smoothing_is_12(self) -> None:
        """smoothing default must be 12."""
        opts = DetectShakeOptions()
        assert opts.smoothing == 12

    def test_build_with_no_args(self) -> None:
        """Construction with no arguments must succeed."""
        opts = DetectShakeOptions()
        assert opts.shakiness == 5
        assert opts.accuracy == 15
        assert opts.smoothing == 12


# ===========================================================================
# DetectShakeOptions — shakiness range (ge=1, le=10)
# ===========================================================================


class TestDetectShakeOptionsShakiness:
    """Validate shakiness boundary conditions (ge=1, le=10)."""

    def test_shakiness_1_accepted(self) -> None:
        """shakiness=1 (lower bound) must be accepted."""
        opts = DetectShakeOptions(shakiness=1)
        assert opts.shakiness == 1

    def test_shakiness_10_accepted(self) -> None:
        """shakiness=10 (upper bound) must be accepted."""
        opts = DetectShakeOptions(shakiness=10)
        assert opts.shakiness == 10

    def test_shakiness_0_rejected(self) -> None:
        """shakiness=0 (below lower bound) must be rejected."""
        with pytest.raises(ValidationError):
            DetectShakeOptions(shakiness=0)

    def test_shakiness_11_rejected(self) -> None:
        """shakiness=11 (above upper bound) must be rejected."""
        with pytest.raises(ValidationError):
            DetectShakeOptions(shakiness=11)


# ===========================================================================
# DetectShakeOptions — accuracy range (ge=1, le=15)
# ===========================================================================


class TestDetectShakeOptionsAccuracy:
    """Validate accuracy boundary conditions (ge=1, le=15)."""

    def test_accuracy_1_accepted(self) -> None:
        """accuracy=1 (lower bound) must be accepted."""
        opts = DetectShakeOptions(accuracy=1)
        assert opts.accuracy == 1

    def test_accuracy_15_accepted(self) -> None:
        """accuracy=15 (upper bound) must be accepted."""
        opts = DetectShakeOptions(accuracy=15)
        assert opts.accuracy == 15

    def test_accuracy_0_rejected(self) -> None:
        """accuracy=0 (below lower bound) must be rejected."""
        with pytest.raises(ValidationError):
            DetectShakeOptions(accuracy=0)

    def test_accuracy_16_rejected(self) -> None:
        """accuracy=16 (above upper bound) must be rejected."""
        with pytest.raises(ValidationError):
            DetectShakeOptions(accuracy=16)


# ===========================================================================
# DetectShakeOptions — smoothing range (ge=0, le=1000)
# ===========================================================================


class TestDetectShakeOptionsSmoothing:
    """Validate smoothing boundary conditions (ge=0, le=1000)."""

    def test_smoothing_0_accepted(self) -> None:
        """smoothing=0 (lower bound) must be accepted."""
        opts = DetectShakeOptions(smoothing=0)
        assert opts.smoothing == 0

    def test_smoothing_1000_accepted(self) -> None:
        """smoothing=1000 (upper bound) must be accepted."""
        opts = DetectShakeOptions(smoothing=1000)
        assert opts.smoothing == 1000

    def test_smoothing_negative_rejected(self) -> None:
        """smoothing=-1 (below lower bound) must be rejected."""
        with pytest.raises(ValidationError):
            DetectShakeOptions(smoothing=-1)

    def test_smoothing_1001_rejected(self) -> None:
        """smoothing=1001 (above upper bound) must be rejected."""
        with pytest.raises(ValidationError):
            DetectShakeOptions(smoothing=1001)


# ===========================================================================
# DetectShakeOptions — extra forbidden and allow_inf_nan=False
# ===========================================================================


class TestDetectShakeOptionsConstraints:
    """Extra fields must be forbidden; inf/nan must be rejected (allow_inf_nan=False)."""

    def test_extra_field_rejected(self) -> None:
        """Unknown field must raise ValidationError (extra: forbid)."""
        with pytest.raises(ValidationError):
            DetectShakeOptions(unknown_field=True)  # type: ignore[call-arg]

    def test_inf_shakiness_rejected(self) -> None:
        """inf shakiness must be rejected (allow_inf_nan=False)."""
        with pytest.raises(ValidationError):
            DetectShakeOptions(shakiness=math.inf)  # type: ignore[arg-type]

    def test_nan_smoothing_rejected(self) -> None:
        """nan smoothing must be rejected (allow_inf_nan=False)."""
        with pytest.raises(ValidationError):
            DetectShakeOptions(smoothing=math.nan)  # type: ignore[arg-type]


# ===========================================================================
# StabilizeDirective — required fields
# ===========================================================================


class TestStabilizeDirectiveRequired:
    """StabilizeDirective must require version, kind, trf_path, shakiness, accuracy, smoothing."""

    def _valid_kwargs(self) -> dict[str, object]:
        return {
            "version": "0.1.0",
            "kind": "stabilize",
            "trf_path": "/tmp/video.stabilize.trf",
            "shakiness": 5,
            "accuracy": 15,
            "smoothing": 30,
        }

    def test_valid_directive_constructed(self) -> None:
        """A fully specified StabilizeDirective must be accepted."""
        d = StabilizeDirective(**self._valid_kwargs())  # type: ignore[arg-type]
        assert d.kind == "stabilize"
        assert d.tool == "clipwright-stabilize"

    def test_version_required(self) -> None:
        """Missing version must raise ValidationError."""
        kwargs = self._valid_kwargs()
        del kwargs["version"]
        with pytest.raises(ValidationError):
            StabilizeDirective(**kwargs)  # type: ignore[arg-type]

    def test_kind_required(self) -> None:
        """Missing kind must raise ValidationError."""
        kwargs = self._valid_kwargs()
        del kwargs["kind"]
        with pytest.raises(ValidationError):
            StabilizeDirective(**kwargs)  # type: ignore[arg-type]

    def test_trf_path_required(self) -> None:
        """Missing trf_path must raise ValidationError."""
        kwargs = self._valid_kwargs()
        del kwargs["trf_path"]
        with pytest.raises(ValidationError):
            StabilizeDirective(**kwargs)  # type: ignore[arg-type]

    def test_shakiness_required(self) -> None:
        """Missing shakiness must raise ValidationError."""
        kwargs = self._valid_kwargs()
        del kwargs["shakiness"]
        with pytest.raises(ValidationError):
            StabilizeDirective(**kwargs)  # type: ignore[arg-type]

    def test_accuracy_required(self) -> None:
        """Missing accuracy must raise ValidationError."""
        kwargs = self._valid_kwargs()
        del kwargs["accuracy"]
        with pytest.raises(ValidationError):
            StabilizeDirective(**kwargs)  # type: ignore[arg-type]

    def test_smoothing_required(self) -> None:
        """Missing smoothing must raise ValidationError."""
        kwargs = self._valid_kwargs()
        del kwargs["smoothing"]
        with pytest.raises(ValidationError):
            StabilizeDirective(**kwargs)  # type: ignore[arg-type]


# ===========================================================================
# StabilizeDirective — kind Literal["stabilize"]
# ===========================================================================


class TestStabilizeDirectiveKind:
    """kind must be the Literal 'stabilize'."""

    def test_kind_stabilize_accepted(self) -> None:
        """kind='stabilize' must be accepted."""
        d = StabilizeDirective(
            version="0.1.0",
            kind="stabilize",
            trf_path="/tmp/video.stabilize.trf",
            shakiness=5,
            accuracy=15,
            smoothing=30,
        )
        assert d.kind == "stabilize"

    def test_kind_wrong_literal_rejected(self) -> None:
        """kind with a value other than 'stabilize' must be rejected."""
        with pytest.raises(ValidationError):
            StabilizeDirective(
                version="0.1.0",
                kind="color",  # type: ignore[arg-type]
                trf_path="/tmp/video.stabilize.trf",
                shakiness=5,
                accuracy=15,
                smoothing=30,
            )


# ===========================================================================
# StabilizeDirective — severity (None accepted / float accepted)
# ===========================================================================


class TestStabilizeDirectiveSeverity:
    """severity must accept None and float values (0.0-1.0 range)."""

    def test_severity_none_accepted(self) -> None:
        """severity=None must be accepted (default)."""
        d = StabilizeDirective(
            version="0.1.0",
            kind="stabilize",
            trf_path="/tmp/video.stabilize.trf",
            shakiness=5,
            accuracy=15,
            smoothing=30,
        )
        assert d.severity is None

    def test_severity_float_accepted(self) -> None:
        """severity as float (e.g., 0.42) must be accepted."""
        d = StabilizeDirective(
            version="0.1.0",
            kind="stabilize",
            trf_path="/tmp/video.stabilize.trf",
            severity=0.42,
            shakiness=5,
            accuracy=15,
            smoothing=30,
        )
        assert d.severity == pytest.approx(0.42)

    def test_severity_zero_accepted(self) -> None:
        """severity=0.0 must be accepted."""
        d = StabilizeDirective(
            version="0.1.0",
            kind="stabilize",
            trf_path="/tmp/video.stabilize.trf",
            severity=0.0,
            shakiness=5,
            accuracy=15,
            smoothing=30,
        )
        assert d.severity == pytest.approx(0.0)

    def test_severity_one_accepted(self) -> None:
        """severity=1.0 must be accepted."""
        d = StabilizeDirective(
            version="0.1.0",
            kind="stabilize",
            trf_path="/tmp/video.stabilize.trf",
            severity=1.0,
            shakiness=5,
            accuracy=15,
            smoothing=30,
        )
        assert d.severity == pytest.approx(1.0)

    def test_severity_negative_rejected(self) -> None:
        """severity=-0.1 (below ge=0.0) must be rejected with ValidationError."""
        with pytest.raises(ValidationError):
            StabilizeDirective(
                version="0.1.0",
                kind="stabilize",
                trf_path="/tmp/video.stabilize.trf",
                severity=-0.1,
                shakiness=5,
                accuracy=15,
                smoothing=30,
            )

    def test_severity_above_one_rejected(self) -> None:
        """severity=1.1 (above le=1.0) must be rejected with ValidationError."""
        with pytest.raises(ValidationError):
            StabilizeDirective(
                version="0.1.0",
                kind="stabilize",
                trf_path="/tmp/video.stabilize.trf",
                severity=1.1,
                shakiness=5,
                accuracy=15,
                smoothing=30,
            )


# ===========================================================================
# StabilizeDirective — tool default and extra forbidden
# ===========================================================================


class TestStabilizeDirectiveConstraints:
    """tool default and extra forbidden."""

    def test_tool_default_is_clipwright_stabilize(self) -> None:
        """tool default must be 'clipwright-stabilize'."""
        d = StabilizeDirective(
            version="0.1.0",
            kind="stabilize",
            trf_path="/tmp/video.stabilize.trf",
            shakiness=5,
            accuracy=15,
            smoothing=30,
        )
        assert d.tool == "clipwright-stabilize"

    def test_extra_field_rejected(self) -> None:
        """Unknown field must raise ValidationError (extra: forbid)."""
        with pytest.raises(ValidationError):
            StabilizeDirective(
                version="0.1.0",
                kind="stabilize",
                trf_path="/tmp/video.stabilize.trf",
                shakiness=5,
                accuracy=15,
                smoothing=30,
                unknown_field="x",  # type: ignore[call-arg]
            )


# ===========================================================================
# StabilizeDirective — recommendation field (AC-3/AC-4/AC-5, AC-10)
# ===========================================================================


class TestStabilizeDirectiveRecommendation:
    """StabilizeDirective.recommendation: Literal['skip','apply'] | None.

    Verifies that the recommendation field is correctly defined with default=None
    (backward compatibility, AC-10), accepts 'skip'/'apply', rejects unknown literals,
    and survives a model_dump() -> model_validate() round-trip.
    """

    def _base_kwargs(self) -> dict[str, object]:
        """Return minimal valid kwargs excluding recommendation."""
        return {
            "version": "0.1.0",
            "kind": "stabilize",
            "trf_path": "/tmp/video.stabilize.trf",
            "shakiness": 5,
            "accuracy": 15,
            "smoothing": 30,
        }

    def test_recommendation_defaults_to_none(self) -> None:
        """recommendation must default to None when not provided (AC-10 backward compat)."""
        d = StabilizeDirective(**self._base_kwargs())  # type: ignore[arg-type]
        # After implementation: d.recommendation is None by default.
        assert d.recommendation is None  # type: ignore[attr-defined]

    def test_recommendation_skip_accepted(self) -> None:
        """recommendation='skip' must be accepted as a valid Literal value."""
        kwargs = {**self._base_kwargs(), "recommendation": "skip"}
        d = StabilizeDirective(**kwargs)  # type: ignore[arg-type]
        assert d.recommendation == "skip"  # type: ignore[attr-defined]

    def test_recommendation_apply_accepted(self) -> None:
        """recommendation='apply' must be accepted as a valid Literal value."""
        kwargs = {**self._base_kwargs(), "recommendation": "apply"}
        d = StabilizeDirective(**kwargs)  # type: ignore[arg-type]
        assert d.recommendation == "apply"  # type: ignore[attr-defined]

    def test_recommendation_none_explicitly_accepted(self) -> None:
        """recommendation=None must be accepted explicitly (no coercion needed)."""
        kwargs = {**self._base_kwargs(), "recommendation": None}
        d = StabilizeDirective(**kwargs)  # type: ignore[arg-type]
        assert d.recommendation is None  # type: ignore[attr-defined]

    def test_recommendation_invalid_literal_rejected(self) -> None:
        """recommendation='stabilize' (not a valid Literal) must raise ValidationError.

        Note: currently passes (extra='forbid' rejects any unknown field), but for
        a different reason than after implementation (wrong Literal value).
        Included as a regression guard for the correct error path.
        """
        kwargs = {**self._base_kwargs(), "recommendation": "stabilize"}
        with pytest.raises(ValidationError):
            StabilizeDirective(**kwargs)  # type: ignore[arg-type]

    def test_recommendation_round_trip_via_model_dump(self) -> None:
        """recommendation must survive model_dump() -> model_validate() round-trip."""
        kwargs = {**self._base_kwargs(), "recommendation": "apply"}
        d = StabilizeDirective(**kwargs)  # type: ignore[arg-type]
        dumped = d.model_dump()
        assert "recommendation" in dumped, (
            "model_dump() must include 'recommendation' key"
        )
        restored = StabilizeDirective.model_validate(dumped)
        assert restored.recommendation == "apply"  # type: ignore[attr-defined]

    def test_round_trip_recommendation_none(self) -> None:
        """recommendation=None must survive model_dump() -> model_validate()."""
        d = StabilizeDirective(**self._base_kwargs())  # type: ignore[arg-type]
        dumped = d.model_dump()
        restored = StabilizeDirective.model_validate(dumped)
        assert restored.recommendation is None  # type: ignore[attr-defined]

    def test_extra_forbid_maintained_with_recommendation(self) -> None:
        """extra='forbid' must still reject truly unknown fields alongside recommendation.

        After implementation recommendation='apply' is valid, but adding an extra
        unknown_field alongside it must still raise ValidationError.
        """
        kwargs = {
            **self._base_kwargs(),
            "recommendation": "apply",
            "unknown_field": "x",
        }
        with pytest.raises(ValidationError):
            StabilizeDirective(**kwargs)  # type: ignore[arg-type]

    def test_backward_compat_existing_directive_unchanged(self) -> None:
        """Existing StabilizeDirective construction without recommendation must still work (AC-10).

        Mirrors the pattern used in TestStabilizeDirectiveRequired; must not break
        now that the recommendation field is defined with default=None.
        """
        d = StabilizeDirective(
            version="0.2.0",
            kind="stabilize",
            trf_path="/tmp/clip.stabilize.trf",
            severity=0.42,
            shakiness=7,
            accuracy=12,
            smoothing=50,
        )
        assert d.kind == "stabilize"
        assert d.tool == "clipwright-stabilize"
        assert d.severity == pytest.approx(0.42)
        # recommendation must default to None and be accessible (AC-10).
        assert d.recommendation is None  # type: ignore[attr-defined]
