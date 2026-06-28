"""test_recommend.py — Tests for clipwright_stabilize.analyze.recommend.

Verification points:
  (1) recommend(None) -> "apply" (AC-5: safe-default when severity is unknown)
  (2) calm fixture severity (low-motion, ≈0.060) -> "skip" (AC-3)
  (3) shaky fixture severity (high-motion, ≈0.106) -> "apply" (AC-4)
  (4) recommend always returns a known Literal: "skip" or "apply"

Threshold design:
  Tests express behaviour by fixture property, not by hardcoding the threshold
  value.  Severity values are measured at runtime via _estimate_severity from
  committed TRF1 fixtures (calm.stabilize.trf / shaky.stabilize.trf).
  The implementation owns _SEVERITY_APPLY_THRESHOLD; tests only assert that
  the ordering property (calm=skip, shaky=apply) holds.

W1 prerequisite:
  These tests assume W1 has established that calm severity < shaky severity
  and both are non-None (test_analyze.py::TestEstimateSeverityFixtures).

Requirements: AC-3, AC-4, AC-5.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_SHAKY_TRF = _FIXTURES_DIR / "shaky.stabilize.trf"
_CALM_TRF = _FIXTURES_DIR / "calm.stabilize.trf"


# ===========================================================================
# (1) recommend(None) -> "apply" (AC-5: safe-default)
# ===========================================================================


class TestRecommendNone:
    """recommend(None) must return 'apply' as the safe-default (AC-5)."""

    def test_recommend_none_returns_apply(self) -> None:
        """recommend(None) must return 'apply' when severity is unavailable (AC-5).

        Severity may be None when the .trf binary cannot be parsed (best-effort
        estimation).  The safe-default is to recommend stabilization rather than
        silently skipping it, because a missed stabilization is more harmful than
        a no-op apply on stable footage.
        """
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            recommend,
        )

        result = recommend(None)
        assert result == "apply", (
            f"recommend(None) must return 'apply' as safe-default, got {result!r}"
        )


# ===========================================================================
# (2) calm fixture severity -> "skip" (AC-3)
# ===========================================================================


class TestRecommendCalmFixture:
    """Calm fixture severity (low-motion) must map to 'skip' (AC-3)."""

    def test_calm_fixture_severity_gives_skip(self) -> None:
        """_estimate_severity(calm.trf) then recommend must return 'skip' (AC-3).

        The calm fixture was recorded from a low-motion scene (severity ≈ 0.060).
        Its severity must be below _SEVERITY_APPLY_THRESHOLD so that stabilization
        is not recommended.  The exact threshold value is an implementation detail;
        this test asserts only the ordering property.
        """
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
            recommend,
        )

        calm_severity = _estimate_severity(_CALM_TRF)
        assert calm_severity is not None, (
            "Prerequisite: calm.stabilize.trf must return non-None severity "
            "(W1 regression guard). Run test_analyze.py::TestEstimateSeverityFixtures first."
        )

        result = recommend(calm_severity)
        assert result == "skip", (
            f"calm fixture severity={calm_severity:.4f} must map to 'skip', "
            f"got {result!r}.  Verify _SEVERITY_APPLY_THRESHOLD > {calm_severity:.4f}."
        )


# ===========================================================================
# (3) shaky fixture severity -> "apply" (AC-4)
# ===========================================================================


class TestRecommendShakyFixture:
    """Shaky fixture severity (high-motion) must map to 'apply' (AC-4)."""

    def test_shaky_fixture_severity_gives_apply(self) -> None:
        """_estimate_severity(shaky.trf) then recommend must return 'apply' (AC-4).

        The shaky fixture was recorded from a high-motion scene (severity ≈ 0.106).
        Its severity must be at or above _SEVERITY_APPLY_THRESHOLD so that
        stabilization is recommended.  The exact threshold is an implementation
        detail; this test asserts only the ordering property.
        """
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            _estimate_severity,
            recommend,
        )

        shaky_severity = _estimate_severity(_SHAKY_TRF)
        assert shaky_severity is not None, (
            "Prerequisite: shaky.stabilize.trf must return non-None severity "
            "(W1 regression guard). Run test_analyze.py::TestEstimateSeverityFixtures first."
        )

        result = recommend(shaky_severity)
        assert result == "apply", (
            f"shaky fixture severity={shaky_severity:.4f} must map to 'apply', "
            f"got {result!r}.  Verify _SEVERITY_APPLY_THRESHOLD <= {shaky_severity:.4f}."
        )


# ===========================================================================
# (4) recommend always returns a known Literal
# ===========================================================================


class TestRecommendReturnType:
    """recommend must always return exactly 'skip' or 'apply' for any input."""

    @pytest.mark.parametrize(
        "severity",
        [0.0, 0.01, 0.05, 0.1, 0.5, 0.9, 1.0],
    )
    def test_recommend_float_returns_valid_literal(self, severity: float) -> None:
        """Any float in [0.0, 1.0] must return exactly 'skip' or 'apply'."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            recommend,
        )

        result = recommend(severity)
        assert result in ("skip", "apply"), (
            f"recommend({severity}) must return 'skip' or 'apply', got {result!r}"
        )

    def test_recommend_none_returns_valid_literal(self) -> None:
        """None input must also return a valid Literal ('skip' or 'apply')."""
        from clipwright_stabilize.analyze import (  # type: ignore[import-not-found]
            recommend,
        )

        result = recommend(None)
        assert result in ("skip", "apply"), (
            f"recommend(None) must return 'skip' or 'apply', got {result!r}"
        )
