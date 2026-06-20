"""test_schemas.py — Schema tests for ReframeOptions and ReframeDirective.

Tests cover:
- AC-03: even-pixel enforcement (target_w / target_h)
- AC-04: range constraints (ge=2, le=7680)
- AC-05: pad_color allowlist (filtergraph injection prevention)
- Literal validation for mode / anchor
- D3: directive dict shape freeze (both-sides contract)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from clipwright_reframe.schemas import ReframeDirective, ReframeOptions

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EVEN_W = 1920
_EVEN_H = 1080


def _make_options(**overrides: object) -> ReframeOptions:
    """Build a minimal valid ReframeOptions, applying overrides."""
    defaults: dict[str, object] = {"target_w": _EVEN_W, "target_h": _EVEN_H}
    defaults.update(overrides)
    return ReframeOptions(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AC-03: even-pixel enforcement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("odd_value", [3, 101, 1081, 1279, 7679])
def test_target_w_odd_raises(odd_value: int) -> None:
    """Odd target_w must raise ValidationError with 'even' in the message (AC-03)."""
    with pytest.raises(ValidationError) as exc_info:
        _make_options(target_w=odd_value)
    errors = exc_info.value.errors()
    messages = " ".join(e.get("msg", "") for e in errors)
    assert "even" in messages.lower(), (
        f"Expected 'even' in error messages for target_w={odd_value}, got: {messages}"
    )


@pytest.mark.parametrize("odd_value", [3, 101, 1081, 1079, 7679])
def test_target_h_odd_raises(odd_value: int) -> None:
    """Odd target_h must raise ValidationError with 'even' in the message (AC-03)."""
    with pytest.raises(ValidationError) as exc_info:
        _make_options(target_h=odd_value)
    errors = exc_info.value.errors()
    messages = " ".join(e.get("msg", "") for e in errors)
    assert "even" in messages.lower(), (
        f"Expected 'even' in error messages for target_h={odd_value}, got: {messages}"
    )


@pytest.mark.parametrize("even_value", [2, 4, 100, 1080, 1280, 1920, 7680])
def test_target_w_even_accepts(even_value: int) -> None:
    """Even target_w values within range must be accepted."""
    opts = _make_options(target_w=even_value)
    assert opts.target_w == even_value


@pytest.mark.parametrize("even_value", [2, 4, 100, 1080, 1920, 2160, 7680])
def test_target_h_even_accepts(even_value: int) -> None:
    """Even target_h values within range must be accepted."""
    opts = _make_options(target_h=even_value)
    assert opts.target_h == even_value


# ---------------------------------------------------------------------------
# AC-04: range constraints (ge=2, le=7680)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_w", [0, 1, -2, -1920])
def test_target_w_below_minimum_raises(bad_w: int) -> None:
    """target_w < 2 must raise ValidationError (AC-04)."""
    with pytest.raises(ValidationError):
        _make_options(target_w=bad_w)


@pytest.mark.parametrize("bad_h", [0, 1, -2, -1080])
def test_target_h_below_minimum_raises(bad_h: int) -> None:
    """target_h < 2 must raise ValidationError (AC-04)."""
    with pytest.raises(ValidationError):
        _make_options(target_h=bad_h)


@pytest.mark.parametrize("bad_w", [7681, 7682, 8000, 10000])
def test_target_w_above_maximum_raises(bad_w: int) -> None:
    """target_w > 7680 must raise ValidationError (AC-04), including off-by-one 7681."""
    with pytest.raises(ValidationError):
        _make_options(target_w=bad_w)


@pytest.mark.parametrize("bad_h", [7681, 7682, 8000, 10000])
def test_target_h_above_maximum_raises(bad_h: int) -> None:
    """target_h > 7680 must raise ValidationError (AC-04), including off-by-one 7681."""
    with pytest.raises(ValidationError):
        _make_options(target_h=bad_h)


def test_target_w_boundary_min_accepts() -> None:
    """target_w == 2 (lower boundary) must be accepted (AC-04)."""
    opts = _make_options(target_w=2)
    assert opts.target_w == 2


def test_target_h_boundary_min_accepts() -> None:
    """target_h == 2 (lower boundary) must be accepted (AC-04)."""
    opts = _make_options(target_h=2)
    assert opts.target_h == 2


def test_target_w_below_boundary_min_raises() -> None:
    """target_w == 1 (one below lower boundary) must raise ValidationError (AC-04)."""
    with pytest.raises(ValidationError):
        _make_options(target_w=1)


def test_target_h_below_boundary_min_raises() -> None:
    """target_h == 1 (one below lower boundary) must raise ValidationError (AC-04)."""
    with pytest.raises(ValidationError):
        _make_options(target_h=1)


def test_target_w_boundary_max_accepts() -> None:
    """target_w == 7680 (upper boundary) must be accepted (AC-04)."""
    opts = _make_options(target_w=7680)
    assert opts.target_w == 7680


def test_target_h_boundary_max_accepts() -> None:
    """target_h == 7680 (upper boundary) must be accepted (AC-04)."""
    opts = _make_options(target_h=7680)
    assert opts.target_h == 7680


# ---------------------------------------------------------------------------
# mode Literal validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("valid_mode", ["crop", "pad", "blur_pad"])
def test_mode_valid_values_accept(valid_mode: str) -> None:
    """All three valid mode literals must be accepted."""
    opts = _make_options(mode=valid_mode)
    assert opts.mode == valid_mode


@pytest.mark.parametrize("bad_mode", ["scale", "stretch", "fit", "CROP", "Pad", ""])
def test_mode_invalid_raises(bad_mode: str) -> None:
    """Values outside Literal['crop','pad','blur_pad'] must raise ValidationError."""
    with pytest.raises(ValidationError):
        _make_options(mode=bad_mode)


def test_mode_default_is_pad() -> None:
    """mode must default to 'pad' when omitted."""
    opts = _make_options()
    assert opts.mode == "pad"


# ---------------------------------------------------------------------------
# anchor Literal validation
# ---------------------------------------------------------------------------

_VALID_ANCHORS = [
    "center",
    "top",
    "bottom",
    "left",
    "right",
    "top_left",
    "top_right",
    "bottom_left",
    "bottom_right",
]


@pytest.mark.parametrize("valid_anchor", _VALID_ANCHORS)
def test_anchor_valid_values_accept(valid_anchor: str) -> None:
    """All nine valid anchor literals must be accepted."""
    opts = _make_options(anchor=valid_anchor)
    assert opts.anchor == valid_anchor


@pytest.mark.parametrize(
    "bad_anchor",
    ["middle", "Center", "TOP", "upperleft", "top-left", ""],
)
def test_anchor_invalid_raises(bad_anchor: str) -> None:
    """Values outside the nine-value Literal must raise ValidationError."""
    with pytest.raises(ValidationError):
        _make_options(anchor=bad_anchor)


def test_anchor_default_is_center() -> None:
    """anchor must default to 'center' when omitted."""
    opts = _make_options()
    assert opts.anchor == "center"


# ---------------------------------------------------------------------------
# AC-05: pad_color allowlist (filtergraph injection prevention)
# ---------------------------------------------------------------------------

# Accepted: named colors and hex formats (#RRGGBB / 0xRRGGBB)
_VALID_PAD_COLORS = [
    "black",
    "white",
    "red",
    "green",
    "blue",
    "gray",
    "grey",
    "yellow",
    "cyan",
    "magenta",
    "#000000",
    "#ffffff",
    "#FF0000",
    "#aabbcc",
    "0x000000",
    "0xFFFFFF",
    "0xAaBbCc",
]

# Rejected: injection attempts and invalid formats
_INVALID_PAD_COLORS = [
    "red;scale=1:1",  # semicolon injection
    "black,scale=1:1",  # comma injection (filtergraph separator)
    "black[out]",  # bracket injection
    "0xZZZZZZ",  # invalid hex digits
    "#GGHHII",  # invalid hex digits
    "0xRRGGBB",  # literal placeholder (invalid)
    "#12345",  # too short
    "#1234567",  # too long (7 chars after #)
    "0x12345",  # too short for 0x format
    "0x1234567",  # too long for 0x format
    "color=black",  # key=value injection
    "black\\nwhite",  # backslash-n
    "",  # empty string
    "   ",  # whitespace only
]


@pytest.mark.parametrize("valid_color", _VALID_PAD_COLORS)
def test_pad_color_valid_accepts(valid_color: str) -> None:
    """Valid CSS color names and #RRGGBB / 0xRRGGBB hex formats must be accepted (AC-05)."""
    opts = _make_options(pad_color=valid_color)
    assert opts.pad_color == valid_color


@pytest.mark.parametrize("bad_color", _INVALID_PAD_COLORS)
def test_pad_color_invalid_raises(bad_color: str) -> None:
    """Injection attempts and invalid formats must raise ValidationError (AC-05)."""
    with pytest.raises(ValidationError):
        _make_options(pad_color=bad_color)


def test_pad_color_default_is_black() -> None:
    """pad_color must default to 'black' when omitted."""
    opts = _make_options()
    assert opts.pad_color == "black"


# ---------------------------------------------------------------------------
# extra fields rejected
# ---------------------------------------------------------------------------


def test_reframe_options_extra_field_raises() -> None:
    """Unknown fields must raise ValidationError (extra='forbid')."""
    with pytest.raises(ValidationError):
        ReframeOptions(target_w=1920, target_h=1080, unknown_field="oops")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# D3: ReframeDirective dict shape freeze (both-sides contract)
#
# The key set here MUST match the keys consumed by render's _RenderReframe reader.
# Both sides extract exactly these keys from the OTIO metadata dict.
# Any change to this set is a breaking change to the D3 contract and must be
# coordinated with the render side (_RenderReframe in plan.py).
# ---------------------------------------------------------------------------

# D3 contract: canonical key set for the directive dict
_D3_REQUIRED_KEYS = frozenset(
    {"tool", "version", "kind", "target_w", "target_h", "mode", "anchor", "pad_color"}
)


def test_reframe_directive_key_set_matches_d3_contract() -> None:
    """ReframeDirective must expose exactly the D3-contracted keys (both-sides contract).

    This test freezes the dict shape so that render's _RenderReframe reader
    and reframe's writer stay in sync.  Breaking this test means the D3
    contract is violated.
    """
    directive = ReframeDirective(
        version="0.1.0",
        kind="reframe",
        target_w=1920,
        target_h=1080,
    )
    actual_keys = frozenset(directive.model_dump().keys())
    assert actual_keys == _D3_REQUIRED_KEYS, (
        f"D3 contract violation: key diff = {actual_keys.symmetric_difference(_D3_REQUIRED_KEYS)}"
    )


def test_reframe_directive_tool_default() -> None:
    """tool field must default to 'clipwright-reframe'."""
    directive = ReframeDirective(
        version="0.1.0",
        kind="reframe",
        target_w=1920,
        target_h=1080,
    )
    assert directive.tool == "clipwright-reframe"


def test_reframe_directive_kind_must_be_reframe() -> None:
    """kind field is Literal['reframe'] — other values must raise ValidationError."""
    with pytest.raises(ValidationError):
        ReframeDirective(
            version="0.1.0",
            kind="crop",  # type: ignore[arg-type]
            target_w=1920,
            target_h=1080,
        )


def test_reframe_directive_generates_correct_dict() -> None:
    """ReframeDirective.model_dump() must produce the full D3 contract dict."""
    directive = ReframeDirective(
        version="0.1.0",
        kind="reframe",
        target_w=1920,
        target_h=1080,
        mode="crop",
        anchor="top_left",
        pad_color="white",
    )
    result = directive.model_dump()
    assert result == {
        "tool": "clipwright-reframe",
        "version": "0.1.0",
        "kind": "reframe",
        "target_w": 1920,
        "target_h": 1080,
        "mode": "crop",
        "anchor": "top_left",
        "pad_color": "white",
    }


def test_reframe_directive_validates_from_dict() -> None:
    """ReframeDirective.model_validate() must accept a canonical D3 dict."""
    d3_dict = {
        "tool": "clipwright-reframe",
        "version": "0.1.0",
        "kind": "reframe",
        "target_w": 1280,
        "target_h": 720,
        "mode": "blur_pad",
        "anchor": "center",
        "pad_color": "black",
    }
    directive = ReframeDirective.model_validate(d3_dict)
    assert directive.target_w == 1280
    assert directive.target_h == 720
    assert directive.mode == "blur_pad"


def test_reframe_directive_even_constraint_enforced() -> None:
    """ReframeDirective must also reject odd target_w (defence-in-depth on reader side)."""
    with pytest.raises(ValidationError):
        ReframeDirective(
            version="0.1.0",
            kind="reframe",
            target_w=1081,  # odd
            target_h=1920,
        )


def test_reframe_directive_extra_field_raises() -> None:
    """Extra fields not in D3 contract must raise ValidationError (extra='forbid')."""
    with pytest.raises(ValidationError):
        ReframeDirective(
            tool="clipwright-reframe",
            version="0.1.0",
            kind="reframe",
            target_w=1920,
            target_h=1080,
            unknown="should_fail",  # type: ignore[call-arg]
        )
