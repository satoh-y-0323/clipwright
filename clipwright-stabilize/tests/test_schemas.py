"""test_schemas.py — Tests for clipwright_stabilize.schemas.

Verification points:
  - DetectShakeOptions: defaults (shakiness=5, accuracy=15, smoothing=30),
    range boundaries (ge/le end values and out-of-range rejection),
    extra forbidden, allow_inf_nan=False.
  - StabilizeDirective: required fields (version/kind/trf_path/shakiness/accuracy/smoothing),
    kind Literal["stabilize"], severity=None accepted, severity as float accepted,
    tool default "clipwright-stabilize", extra forbidden.

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

    def test_default_smoothing_is_30(self) -> None:
        """smoothing default must be 30."""
        opts = DetectShakeOptions()
        assert opts.smoothing == 30

    def test_build_with_no_args(self) -> None:
        """Construction with no arguments must succeed."""
        opts = DetectShakeOptions()
        assert opts.shakiness == 5
        assert opts.accuracy == 15
        assert opts.smoothing == 30


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
