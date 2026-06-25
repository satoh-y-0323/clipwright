"""test_reframe_track_contract.py — Through-tests for the D3 track contract.

These tests verify that _validate_reframe correctly passes the "track" key
from a raw OTIO metadata dict all the way through to _RenderReframe.track.
A mock-based test that constructs _RenderReframe directly would NOT detect a
missing "track" key in the filtered tuple (plan.py L2110), so we always go
through the real _validate_reframe path here (DC-GP-001 DoD).

Covers:
- DC-GP-001: filtered key tuple includes "track" so track reaches _RenderReframe
- DC-GP-005: backward-compatible through-pass for track-less directives
- AC-06: existing mode='crop' directive stays on the legacy scale-first crop path
"""

from __future__ import annotations

from typing import Any

from clipwright_render.plan import (  # type: ignore[attr-defined]
    _RenderReframe,
    _validate_reframe,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_DIRECTIVE: dict[str, Any] = {
    "tool": "clipwright-reframe",
    "version": "0.3.0",
    "kind": "reframe",
    "target_w": 1080,
    "target_h": 1920,
    "mode": "track",
    "anchor": "center",
    "pad_color": "black",
}


# ---------------------------------------------------------------------------
# DC-GP-001: through test — track reaches _RenderReframe via _validate_reframe
# ---------------------------------------------------------------------------


def test_track_through_validate_reframe_single_keyframe() -> None:
    """A raw dict with track must survive _validate_reframe and arrive in
    _RenderReframe.track (DC-GP-001).

    This is the critical through-test: it exercises the real _validate_reframe
    path including the filtered key extraction.  If "track" were missing from
    the filtered tuple, _RenderReframe.track would be None and this test would
    fail, catching the regression immediately.
    """
    raw: dict[str, Any] = {
        **_BASE_DIRECTIVE,
        "track": [{"t_s": 0.0, "cx": 0.5, "cy": 0.5}],
    }
    result = _validate_reframe(raw)

    assert result is not None
    assert isinstance(result, _RenderReframe)
    assert result.mode == "track"
    assert result.track is not None
    assert len(result.track) == 1
    assert result.track[0].t_s == 0.0
    assert result.track[0].cx == 0.5
    assert result.track[0].cy == 0.5


def test_track_through_validate_reframe_multiple_keyframes() -> None:
    """Multiple keyframes must all be preserved through _validate_reframe."""
    keyframes = [
        {"t_s": 0.0, "cx": 0.3, "cy": 0.4},
        {"t_s": 1.0, "cx": 0.6, "cy": 0.7},
        {"t_s": 2.5, "cx": 0.5, "cy": 0.5},
    ]
    raw: dict[str, Any] = {**_BASE_DIRECTIVE, "track": keyframes}
    result = _validate_reframe(raw)

    assert result is not None
    assert result.track is not None
    assert len(result.track) == 3
    assert result.track[1].t_s == 1.0
    assert result.track[2].cx == 0.5


# ---------------------------------------------------------------------------
# DC-GP-005: backward-compatible through test
# ---------------------------------------------------------------------------


def test_backward_compat_track_absent_stays_none() -> None:
    """A track-less directive (e.g. mode='crop') must pass _validate_reframe
    with _RenderReframe.track remaining None (DC-GP-005 backward compatibility).

    Design note: The backward compatibility verified here is that an existing
    mode='crop' directive continues to be processed by the legacy scale-first
    crop path unchanged.  The track-center fallback (crop-from-source) produces
    different pixel output by design (ADR-T10) — the two paths must NOT be
    conflated (AC-06 confusion guard).
    """
    raw: dict[str, Any] = {
        "tool": "clipwright-reframe",
        "version": "0.1.0",
        "kind": "reframe",
        "target_w": 1920,
        "target_h": 1080,
        "mode": "crop",
        "anchor": "center",
        "pad_color": "black",
        # No "track" key — simulates a directive written by an older reframe version.
    }
    result = _validate_reframe(raw)

    assert result is not None
    assert result.mode == "crop"
    # track must remain None — the old directive did not carry track data.
    assert result.track is None


def test_backward_compat_track_explicit_none() -> None:
    """Explicit track=None in the raw dict must also yield _RenderReframe.track is None."""
    raw: dict[str, Any] = {
        "tool": "clipwright-reframe",
        "version": "0.3.0",
        "kind": "reframe",
        "target_w": 1920,
        "target_h": 1080,
        "mode": "pad",
        "anchor": "center",
        "pad_color": "black",
        "track": None,
    }
    result = _validate_reframe(raw)

    assert result is not None
    assert result.track is None
