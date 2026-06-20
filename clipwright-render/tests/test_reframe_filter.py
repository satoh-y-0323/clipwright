"""test_reframe_filter.py — Tests for render reframe filter helpers.

Target functions:
  - _append_reframe_filter(filter_parts, video_map_label, reframe) -> str
  - _RenderReframe     (Pydantic BaseModel, extra="forbid")
  - _validate_reframe  (directive dict -> _RenderReframe | None)

Architecture reference: architecture-report-20260621-004050.md §2/§3/§6/§7.4
Plan reference: plan-report-20260621-004050.md W2b (test-render-filter)
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from clipwright_render.plan import (  # type: ignore[attr-defined]
    _RenderReframe,
    _append_reframe_filter,
    _validate_reframe,
)
from clipwright.errors import ClipwrightError, ErrorCode

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_W = 1080  # target width  (9:16 vertical)
_H = 1920  # target height

# D3 directive dict — matches the both-sides contract frozen in W1
# (clipwright_reframe.schemas.ReframeDirective key set).
_D3_DICT: dict[str, Any] = {
    "tool": "clipwright-reframe",
    "version": "0.1.0",
    "kind": "reframe",
    "target_w": _W,
    "target_h": _H,
    "mode": "pad",
    "anchor": "center",
    "pad_color": "black",
}

# Input video_map_label used in filter tests (concat terminal label)
_IN_LABEL = "[outv]"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_reframe(**kwargs: Any) -> _RenderReframe:
    """Construct a _RenderReframe from _D3_DICT with optional overrides."""
    base = {
        "target_w": _W,
        "target_h": _H,
        "mode": "pad",
        "anchor": "center",
        "pad_color": "black",
    }
    base.update(kwargs)
    return _RenderReframe(**base)  # type: ignore[call-arg]


def _run_filter(
    reframe: _RenderReframe, label: str = _IN_LABEL
) -> tuple[list[str], str]:
    """Call _append_reframe_filter and return (mutated filter_parts, returned label)."""
    parts: list[str] = []
    result_label = _append_reframe_filter(parts, label, reframe)
    return parts, result_label


# ===========================================================================
# §7.4 — _RenderReframe schema (extra=forbid, allow_inf_nan=False)
# ===========================================================================


class TestRenderReframeSchema:
    """_RenderReframe field validation (architecture §7.4 / AC-03/04/05)."""

    # --- target_w/h range ---

    def test_target_w_ge_2_accepted(self) -> None:
        """target_w=2 (lower boundary) is accepted."""
        r = _RenderReframe(
            target_w=2, target_h=_H, mode="pad", anchor="center", pad_color="black"
        )  # type: ignore[call-arg]
        assert r.target_w == 2

    def test_target_h_le_7680_accepted(self) -> None:
        """target_h=7680 (upper boundary) is accepted."""
        r = _RenderReframe(
            target_w=_W, target_h=7680, mode="pad", anchor="center", pad_color="black"
        )  # type: ignore[call-arg]
        assert r.target_h == 7680

    def test_target_w_less_than_2_rejected(self) -> None:
        """target_w=0 → ValidationError (ge=2 constraint)."""
        with pytest.raises((ValidationError, ClipwrightError)):
            _RenderReframe(
                target_w=0, target_h=_H, mode="pad", anchor="center", pad_color="black"
            )  # type: ignore[call-arg]

    def test_target_w_equals_1_rejected(self) -> None:
        """target_w=1 → ValidationError (ge=2 constraint)."""
        with pytest.raises((ValidationError, ClipwrightError)):
            _RenderReframe(
                target_w=1, target_h=_H, mode="pad", anchor="center", pad_color="black"
            )  # type: ignore[call-arg]

    def test_target_h_greater_than_7680_rejected(self) -> None:
        """target_h=8000 → ValidationError (le=7680 constraint)."""
        with pytest.raises((ValidationError, ClipwrightError)):
            _RenderReframe(
                target_w=_W,
                target_h=8000,
                mode="pad",
                anchor="center",
                pad_color="black",
            )  # type: ignore[call-arg]

    # --- even validation (reader side defence-in-depth, AC-03) ---

    def test_target_w_odd_rejected(self) -> None:
        """target_w=1081 (odd) → ValidationError with 'even' in message (AC-03)."""
        with pytest.raises((ValidationError, ClipwrightError)) as exc_info:
            _RenderReframe(
                target_w=1081,
                target_h=_H,
                mode="pad",
                anchor="center",
                pad_color="black",
            )  # type: ignore[call-arg]
        err_str = str(exc_info.value)
        assert "even" in err_str.lower()

    def test_target_h_odd_rejected(self) -> None:
        """target_h=1921 (odd) → ValidationError with 'even' in message (AC-03)."""
        with pytest.raises((ValidationError, ClipwrightError)) as exc_info:
            _RenderReframe(
                target_w=_W,
                target_h=1921,
                mode="pad",
                anchor="center",
                pad_color="black",
            )  # type: ignore[call-arg]
        err_str = str(exc_info.value)
        assert "even" in err_str.lower()

    # --- mode Literal ---

    def test_mode_crop_accepted(self) -> None:
        """mode='crop' is accepted."""
        r = _make_reframe(mode="crop")
        assert r.mode == "crop"

    def test_mode_blur_pad_accepted(self) -> None:
        """mode='blur_pad' is accepted."""
        r = _make_reframe(mode="blur_pad")
        assert r.mode == "blur_pad"

    def test_mode_invalid_rejected(self) -> None:
        """mode='fit' → ValidationError (not in Literal)."""
        with pytest.raises((ValidationError, ClipwrightError)):
            _make_reframe(mode="fit")

    # --- anchor Literal (9 values) ---

    @pytest.mark.parametrize(
        "anchor",
        [
            "center",
            "top",
            "bottom",
            "left",
            "right",
            "top_left",
            "top_right",
            "bottom_left",
            "bottom_right",
        ],
    )
    def test_anchor_all_9_values_accepted(self, anchor: str) -> None:
        """All 9 anchor values are accepted."""
        r = _make_reframe(anchor=anchor)
        assert r.anchor == anchor

    def test_anchor_invalid_rejected(self) -> None:
        """anchor='middle' → ValidationError (not in Literal)."""
        with pytest.raises((ValidationError, ClipwrightError)):
            _make_reframe(anchor="middle")

    # --- pad_color allowlist (reader re-validates, AC-05 defence-in-depth) ---

    def test_pad_color_black_accepted(self) -> None:
        """pad_color='black' (named color) is accepted."""
        r = _make_reframe(pad_color="black")
        assert r.pad_color == "black"

    def test_pad_color_hex_rrggbb_accepted(self) -> None:
        """pad_color='#AABBCC' (#RRGGBB hex) is accepted."""
        r = _make_reframe(pad_color="#AABBCC")
        assert r.pad_color == "#AABBCC"

    def test_pad_color_0x_hex_accepted(self) -> None:
        """pad_color='0xAABBCC' (0xRRGGBB hex) is accepted."""
        r = _make_reframe(pad_color="0xAABBCC")
        assert r.pad_color == "0xAABBCC"

    def test_pad_color_injection_attempt_rejected(self) -> None:
        """pad_color='red;scale=1:1' → ValidationError (CWE-78 defence-in-depth)."""
        with pytest.raises((ValidationError, ClipwrightError)):
            _make_reframe(pad_color="red;scale=1:1")

    def test_pad_color_invalid_hex_rejected(self) -> None:
        """pad_color='0xZZZZZZ' → ValidationError."""
        with pytest.raises((ValidationError, ClipwrightError)):
            _make_reframe(pad_color="0xZZZZZZ")

    # --- extra=forbid ---

    def test_extra_key_rejected(self) -> None:
        """Unknown key 'unknown=1' → ValidationError (extra='forbid')."""
        with pytest.raises((ValidationError, ClipwrightError)):
            _RenderReframe(  # type: ignore[call-arg]
                target_w=_W,
                target_h=_H,
                mode="pad",
                anchor="center",
                pad_color="black",
                unknown=1,
            )


# ===========================================================================
# §7.4 — _validate_reframe (D3 directive dict → _RenderReframe)
# ===========================================================================


class TestValidateReframe:
    """_validate_reframe extracts only target_w/h/mode/anchor/pad_color from D3
    dict (tool/version/kind are ignored — extra=forbid on _RenderReframe side).
    """

    def test_d3_dict_full_round_trip(self) -> None:
        """Full D3 dict (tool/version/kind/target_w/h/mode/anchor/pad_color) is accepted."""
        result = _validate_reframe(_D3_DICT)
        assert result is not None
        assert result.target_w == _W
        assert result.target_h == _H
        assert result.mode == "pad"
        assert result.anchor == "center"
        assert result.pad_color == "black"

    def test_d3_dict_keys_do_not_include_tool_kind_in_rendered_model(self) -> None:
        """D3 dict keys tool/version/kind are stripped — _RenderReframe does not have them."""
        result = _validate_reframe(_D3_DICT)
        assert result is not None
        assert not hasattr(result, "tool")
        assert not hasattr(result, "kind")

    def test_none_input_returns_none(self) -> None:
        """_validate_reframe(None) → None (no reframe; backward compat)."""
        result = _validate_reframe(None)
        assert result is None

    # --- reader-side even validation (defence-in-depth, AC-03) ---

    def test_odd_target_w_raises_invalid_input(self) -> None:
        """D3 dict with target_w=1081 (odd) → INVALID_INPUT (AC-03 reader-side)."""
        bad = {**_D3_DICT, "target_w": 1081}
        with pytest.raises(ClipwrightError) as exc_info:
            _validate_reframe(bad)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_odd_target_h_raises_invalid_input(self) -> None:
        """D3 dict with target_h=1921 (odd) → INVALID_INPUT."""
        bad = {**_D3_DICT, "target_h": 1921}
        with pytest.raises(ClipwrightError) as exc_info:
            _validate_reframe(bad)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    # --- reader-side pad_color allowlist re-validation (AC-05 defence-in-depth) ---

    def test_injection_pad_color_raises_invalid_input(self) -> None:
        """D3 dict with pad_color='red;scale=1:1' → INVALID_INPUT (CWE-78)."""
        bad = {**_D3_DICT, "pad_color": "red;scale=1:1"}
        with pytest.raises(ClipwrightError) as exc_info:
            _validate_reframe(bad)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_invalid_mode_raises_invalid_input(self) -> None:
        """D3 dict with mode='fit' → INVALID_INPUT."""
        bad = {**_D3_DICT, "mode": "fit"}
        with pytest.raises(ClipwrightError) as exc_info:
            _validate_reframe(bad)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_invalid_anchor_raises_invalid_input(self) -> None:
        """D3 dict with anchor='middle' → INVALID_INPUT."""
        bad = {**_D3_DICT, "anchor": "middle"}
        with pytest.raises(ClipwrightError) as exc_info:
            _validate_reframe(bad)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_valid_alternate_color_accepted(self) -> None:
        """D3 dict with pad_color='white' → accepted."""
        d = {**_D3_DICT, "pad_color": "white"}
        result = _validate_reframe(d)
        assert result is not None
        assert result.pad_color == "white"

    def test_target_less_than_2_raises_invalid_input(self) -> None:
        """D3 dict with target_w=0 → INVALID_INPUT (ge=2)."""
        bad = {**_D3_DICT, "target_w": 0}
        with pytest.raises(ClipwrightError) as exc_info:
            _validate_reframe(bad)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    # --- NL-1 [SR-R-001] CWE-209: hint must not echo user-supplied integer value ---

    def test_odd_target_w_hint_does_not_echo_input_value(self) -> None:
        """Odd target_w hint must not contain the raw input value (CWE-209 lock).

        R1 changed the hint to a fixed string.  This test locks that invariant so
        that a future refactor cannot accidentally re-introduce value echoing.
        """
        odd_w = 1081
        bad = {**_D3_DICT, "target_w": odd_w}
        with pytest.raises(ClipwrightError) as exc_info:
            _validate_reframe(bad)
        err = exc_info.value
        assert err.code == ErrorCode.INVALID_INPUT
        # The raw value and its neighbour must not appear in hint or message.
        assert str(odd_w) not in err.hint, (
            f"hint must not echo input value {odd_w!r}: {err.hint!r}"
        )
        assert str(odd_w + 1) not in err.hint, (
            f"hint must not suggest v+1 ({odd_w + 1!r}): {err.hint!r}"
        )
        assert str(odd_w) not in err.message, (
            f"message must not echo input value {odd_w!r}: {err.message!r}"
        )

    def test_odd_target_h_hint_does_not_echo_input_value(self) -> None:
        """Odd target_h hint must not contain the raw input value (CWE-209 lock)."""
        odd_h = 1921
        bad = {**_D3_DICT, "target_h": odd_h}
        with pytest.raises(ClipwrightError) as exc_info:
            _validate_reframe(bad)
        err = exc_info.value
        assert err.code == ErrorCode.INVALID_INPUT
        assert str(odd_h) not in err.hint, (
            f"hint must not echo input value {odd_h!r}: {err.hint!r}"
        )
        assert str(odd_h + 1) not in err.hint, (
            f"hint must not suggest v+1 ({odd_h + 1!r}): {err.hint!r}"
        )
        assert str(odd_h) not in err.message, (
            f"message must not echo input value {odd_h!r}: {err.message!r}"
        )


# ===========================================================================
# §3.4 / §2.2 — crop mode: single segment filter string
# ===========================================================================


class TestCropModeFilter:
    """_append_reframe_filter with mode='crop' appends a single segment and
    returns '[outvrf]' (architecture §3.4 / §2.2).
    """

    def _crop_reframe(
        self, anchor: str = "center", pad_color: str = "black"
    ) -> _RenderReframe:
        return _make_reframe(mode="crop", anchor=anchor, pad_color=pad_color)

    def test_returns_outvrf_label(self) -> None:
        """crop mode → returned label is '[outvrf]'."""
        _, label = _run_filter(self._crop_reframe())
        assert label == "[outvrf]"

    def test_single_segment_appended(self) -> None:
        """crop mode → exactly one segment is appended to filter_parts."""
        parts, _ = _run_filter(self._crop_reframe())
        assert len(parts) == 1

    def test_segment_starts_with_input_label(self) -> None:
        """crop segment starts with the input video_map_label."""
        parts, _ = _run_filter(self._crop_reframe())
        assert parts[0].startswith(_IN_LABEL)

    def test_segment_ends_with_outvrf(self) -> None:
        """crop segment ends with '[outvrf]'."""
        parts, _ = _run_filter(self._crop_reframe())
        assert parts[0].endswith("[outvrf]")

    def test_crop_segment_no_semicolon(self) -> None:
        """crop segment must not contain ';' (no sub-graph stitching)."""
        parts, _ = _run_filter(self._crop_reframe())
        assert ";" not in parts[0]

    def test_scale_increase_present(self) -> None:
        """crop segment contains scale=1080:1920:force_original_aspect_ratio=increase."""
        parts, _ = _run_filter(self._crop_reframe())
        assert f"scale={_W}:{_H}:force_original_aspect_ratio=increase" in parts[0]

    def test_setsar_1_present(self) -> None:
        """crop segment contains setsar=1."""
        parts, _ = _run_filter(self._crop_reframe())
        assert "setsar=1" in parts[0]

    # --- anchor=center: ox=(iw-1080)/2, oy=(ih-1920)/2 (§2.2) ---

    def test_crop_center_ox_formula(self) -> None:
        """anchor=center → ox clamp formula contains (iw-1080)/2."""
        parts, _ = _run_filter(self._crop_reframe(anchor="center"))
        seg = parts[0]
        # The ox expression: min(max((iw-1080)/2\,0)\,iw-1080)
        assert f"(iw-{_W})/2" in seg

    def test_crop_center_oy_formula(self) -> None:
        """anchor=center → oy clamp formula contains (ih-1920)/2."""
        parts, _ = _run_filter(self._crop_reframe(anchor="center"))
        seg = parts[0]
        assert f"(ih-{_H})/2" in seg

    def test_crop_center_clamp_min_max(self) -> None:
        """anchor=center → ox/oy wrapped in min(max(...\\,0)\\,limit) clamp."""
        parts, _ = _run_filter(self._crop_reframe(anchor="center"))
        seg = parts[0]
        # Both ox and oy clamps should contain min( and max( with escaped comma
        assert "min(" in seg
        assert r"\," in seg

    # --- 9-anchor crop offset table (§2.2) ---

    @pytest.mark.parametrize(
        "anchor,expected_ox_expr,expected_oy_expr",
        [
            # center: ox=(iw-W)/2, oy=(ih-H)/2
            ("center", f"(iw-{_W})/2", f"(ih-{_H})/2"),
            # top: ox=(iw-W)/2, oy=0
            ("top", f"(iw-{_W})/2", "0"),
            # bottom: ox=(iw-W)/2, oy=ih-H
            ("bottom", f"(iw-{_W})/2", f"ih-{_H}"),
            # left: ox=0, oy=(ih-H)/2
            ("left", "0", f"(ih-{_H})/2"),
            # right: ox=iw-W, oy=(ih-H)/2
            ("right", f"iw-{_W}", f"(ih-{_H})/2"),
            # top_left: ox=0, oy=0
            ("top_left", "0", "0"),
            # top_right: ox=iw-W, oy=0
            ("top_right", f"iw-{_W}", "0"),
            # bottom_left: ox=0, oy=ih-H
            ("bottom_left", "0", f"ih-{_H}"),
            # bottom_right: ox=iw-W, oy=ih-H
            ("bottom_right", f"iw-{_W}", f"ih-{_H}"),
        ],
    )
    def test_crop_anchor_offset_all_9(
        self, anchor: str, expected_ox_expr: str, expected_oy_expr: str
    ) -> None:
        """crop offset expressions match §2.2 table for all 9 anchors."""
        parts, _ = _run_filter(self._crop_reframe(anchor=anchor))
        seg = parts[0]
        assert expected_ox_expr in seg, (
            f"anchor={anchor}: expected ox={expected_ox_expr!r} not found in {seg!r}"
        )
        assert expected_oy_expr in seg, (
            f"anchor={anchor}: expected oy={expected_oy_expr!r} not found in {seg!r}"
        )


# ===========================================================================
# §3.4 / §2.3 — pad mode: single segment filter string
# ===========================================================================


class TestPadModeFilter:
    """_append_reframe_filter with mode='pad' appends a single segment and
    returns '[outvrf]' (architecture §3.4 / §2.3).
    """

    def _pad_reframe(
        self, anchor: str = "center", pad_color: str = "black"
    ) -> _RenderReframe:
        return _make_reframe(mode="pad", anchor=anchor, pad_color=pad_color)

    def test_returns_outvrf_label(self) -> None:
        """pad mode → returned label is '[outvrf]'."""
        _, label = _run_filter(self._pad_reframe())
        assert label == "[outvrf]"

    def test_single_segment_appended(self) -> None:
        """pad mode → exactly one segment is appended to filter_parts."""
        parts, _ = _run_filter(self._pad_reframe())
        assert len(parts) == 1

    def test_scale_decrease_present(self) -> None:
        """pad segment contains scale=1080:1920:force_original_aspect_ratio=decrease."""
        parts, _ = _run_filter(self._pad_reframe())
        assert f"scale={_W}:{_H}:force_original_aspect_ratio=decrease" in parts[0]

    def test_pad_filter_present(self) -> None:
        """pad segment contains pad={W}:{H}:..."""
        parts, _ = _run_filter(self._pad_reframe())
        assert f"pad={_W}:{_H}:" in parts[0]

    def test_setsar_1_present(self) -> None:
        """pad segment contains setsar=1."""
        parts, _ = _run_filter(self._pad_reframe())
        assert "setsar=1" in parts[0]

    def test_pad_segment_no_semicolon(self) -> None:
        """pad segment must not contain ';'."""
        parts, _ = _run_filter(self._pad_reframe())
        assert ";" not in parts[0]

    def test_pad_segment_ends_with_outvrf(self) -> None:
        """pad segment ends with '[outvrf]'."""
        parts, _ = _run_filter(self._pad_reframe())
        assert parts[0].endswith("[outvrf]")

    # --- pad_color embedded (defence-in-depth AC-05) ---

    def test_pad_color_black_in_segment(self) -> None:
        """pad_color='black' is embedded in the pad filter (e.g. color=black)."""
        parts, _ = _run_filter(self._pad_reframe(pad_color="black"))
        assert "black" in parts[0]

    def test_pad_color_white_in_segment(self) -> None:
        """pad_color='white' is embedded in the pad filter."""
        parts, _ = _run_filter(self._pad_reframe(pad_color="white"))
        assert "white" in parts[0]

    def test_pad_color_hex_in_segment(self) -> None:
        """pad_color='#AABBCC' is embedded in the pad filter."""
        parts, _ = _run_filter(self._pad_reframe(pad_color="#AABBCC"))
        assert "#AABBCC" in parts[0]

    # --- anchor=center: ox=(ow-iw)/2, oy=(oh-ih)/2 (§2.3 output coords) ---

    def test_pad_center_ox_formula(self) -> None:
        """anchor=center → ox expression contains (ow-iw)/2."""
        parts, _ = _run_filter(self._pad_reframe(anchor="center"))
        assert "(ow-iw)/2" in parts[0]

    def test_pad_center_oy_formula(self) -> None:
        """anchor=center → oy expression contains (oh-ih)/2."""
        parts, _ = _run_filter(self._pad_reframe(anchor="center"))
        assert "(oh-ih)/2" in parts[0]

    def test_pad_center_clamp_min_max(self) -> None:
        """anchor=center → ox/oy wrapped in min(max(...\\,0)\\,limit) clamp."""
        parts, _ = _run_filter(self._pad_reframe(anchor="center"))
        seg = parts[0]
        assert "min(" in seg
        assert r"\," in seg

    # --- 9-anchor pad offset table (§2.3) ---

    @pytest.mark.parametrize(
        "anchor,expected_ox_expr,expected_oy_expr",
        [
            # center: ox=(ow-iw)/2, oy=(oh-ih)/2
            ("center", "(ow-iw)/2", "(oh-ih)/2"),
            # top: ox=(ow-iw)/2, oy=0
            ("top", "(ow-iw)/2", "0"),
            # bottom: ox=(ow-iw)/2, oy=oh-ih
            ("bottom", "(ow-iw)/2", "oh-ih"),
            # left: ox=0, oy=(oh-ih)/2
            ("left", "0", "(oh-ih)/2"),
            # right: ox=ow-iw, oy=(oh-ih)/2
            ("right", "ow-iw", "(oh-ih)/2"),
            # top_left: ox=0, oy=0
            ("top_left", "0", "0"),
            # top_right: ox=ow-iw, oy=0
            ("top_right", "ow-iw", "0"),
            # bottom_left: ox=0, oy=oh-ih
            ("bottom_left", "0", "oh-ih"),
            # bottom_right: ox=ow-iw, oy=oh-ih
            ("bottom_right", "ow-iw", "oh-ih"),
        ],
    )
    def test_pad_anchor_offset_all_9(
        self, anchor: str, expected_ox_expr: str, expected_oy_expr: str
    ) -> None:
        """pad offset expressions match §2.3 table for all 9 anchors (output coords)."""
        parts, _ = _run_filter(self._pad_reframe(anchor=anchor))
        seg = parts[0]
        assert expected_ox_expr in seg, (
            f"anchor={anchor}: expected ox={expected_ox_expr!r} not found in {seg!r}"
        )
        assert expected_oy_expr in seg, (
            f"anchor={anchor}: expected oy={expected_oy_expr!r} not found in {seg!r}"
        )


# ===========================================================================
# §3.3 — blur_pad mode: 4 segments
# ===========================================================================


class TestBlurPadModeFilter:
    """_append_reframe_filter with mode='blur_pad' appends exactly 4 segments
    and returns '[outvrf]' (architecture §3.3 / FR-3.3).
    """

    def _blur_pad_reframe(self, anchor: str = "center") -> _RenderReframe:
        return _make_reframe(mode="blur_pad", anchor=anchor)

    def test_returns_outvrf_label(self) -> None:
        """blur_pad mode → returned label is '[outvrf]'."""
        _, label = _run_filter(self._blur_pad_reframe())
        assert label == "[outvrf]"

    def test_four_segments_appended(self) -> None:
        """blur_pad mode → exactly 4 segments appended (split / bg / fg / overlay)."""
        parts, _ = _run_filter(self._blur_pad_reframe())
        assert len(parts) == 4

    def test_no_segment_contains_semicolon(self) -> None:
        """No segment must contain ';' (individual append rule; §3.1)."""
        parts, _ = _run_filter(self._blur_pad_reframe())
        for seg in parts:
            assert ";" not in seg, f"Segment contains ';': {seg!r}"

    # --- segment 0: split ---

    def test_segment_0_is_split(self) -> None:
        """Segment 0 is '{L}split=2[reframe_bg][reframe_fg]' (§3.3)."""
        parts, _ = _run_filter(self._blur_pad_reframe())
        seg0 = parts[0]
        assert seg0.startswith(_IN_LABEL)
        assert "split=2" in seg0
        assert "[reframe_bg]" in seg0
        assert "[reframe_fg]" in seg0

    # --- segment 1: background (scale increase + center crop + boxblur) ---

    def test_segment_1_is_background(self) -> None:
        """Segment 1 starts with [reframe_bg] and contains scale increase, crop, boxblur."""
        parts, _ = _run_filter(self._blur_pad_reframe())
        seg1 = parts[1]
        assert seg1.startswith("[reframe_bg]")
        assert f"scale={_W}:{_H}:force_original_aspect_ratio=increase" in seg1
        assert f"crop={_W}:{_H}" in seg1
        assert "boxblur=" in seg1
        assert "[reframe_bgb]" in seg1

    def test_segment_1_boxblur_values(self) -> None:
        """Segment 1 boxblur parameters are 20:2 (architecture §3.3)."""
        parts, _ = _run_filter(self._blur_pad_reframe())
        assert "boxblur=20:2" in parts[1]

    def test_segment_1_background_crop_no_offset(self) -> None:
        """Segment 1 background crop has no explicit ox/oy (center-fixed; FR-3.3/AC-15)."""
        parts, _ = _run_filter(self._blur_pad_reframe())
        seg1 = parts[1]
        # The crop for blur_pad background is crop=W:H (no ox:oy — center default)
        # Expect crop=W:H followed immediately by ',' or '['
        assert f"crop={_W}:{_H}" in seg1
        # Ensure no clamp expression follows the blur_pad background crop
        # (ox/oy should be absent for background — center implicit)
        crop_idx = seg1.index(f"crop={_W}:{_H}")
        after_crop = seg1[crop_idx + len(f"crop={_W}:{_H}") :]
        # Should start with comma (next filter) or bracket (label), not ':' (offset)
        assert after_crop.startswith(",") or after_crop.startswith("["), (
            f"Background crop for blur_pad has unexpected offset: {after_crop!r}"
        )

    # --- segment 2: foreground (scale decrease only) ---

    def test_segment_2_is_foreground(self) -> None:
        """Segment 2 starts with [reframe_fg] and contains scale decrease."""
        parts, _ = _run_filter(self._blur_pad_reframe())
        seg2 = parts[2]
        assert seg2.startswith("[reframe_fg]")
        assert f"scale={_W}:{_H}:force_original_aspect_ratio=decrease" in seg2
        assert "[reframe_fgs]" in seg2

    # --- segment 3: overlay + setsar ---

    def test_segment_3_is_overlay(self) -> None:
        """Segment 3 starts with [reframe_bgb][reframe_fgs] and contains overlay."""
        parts, _ = _run_filter(self._blur_pad_reframe())
        seg3 = parts[3]
        assert seg3.startswith("[reframe_bgb][reframe_fgs]")
        assert "overlay=" in seg3
        assert "setsar=1" in seg3
        assert seg3.endswith("[outvrf]")

    def test_segment_3_overlay_center_formula(self) -> None:
        """Segment 3 overlay position is (W-w)/2:(H-h)/2 (center; §3.3)."""
        parts, _ = _run_filter(self._blur_pad_reframe())
        seg3 = parts[3]
        assert f"({_W}-w)/2" in seg3
        assert f"({_H}-h)/2" in seg3

    # --- intermediate labels use reframe_ prefix (§3.2 non-collision) ---

    def test_intermediate_labels_use_reframe_prefix(self) -> None:
        """All intermediate labels in blur_pad segments use 'reframe_' prefix (§3.2)."""
        parts, _ = _run_filter(self._blur_pad_reframe())
        all_text = " ".join(parts)
        # All intermediate labels: [reframe_bg] [reframe_fg] [reframe_bgb] [reframe_fgs]
        for label in ("[reframe_bg]", "[reframe_fg]", "[reframe_bgb]", "[reframe_fgs]"):
            assert label in all_text, f"Expected intermediate label {label!r} not found"

    def test_no_outv_label_collision(self) -> None:
        """blur_pad must not generate new [outv*] labels that collide with existing ones.

        The input video_map_label ([outv]) legitimately appears as a consumed input
        in segment 0.  What must NOT appear are newly *generated* labels that clash
        with the conventional [outv] namespace used by other render pipeline stages.
        We therefore use a neutral input label ([outvx]) so that any [outv]-prefixed
        label found in the output can only have been created by _append_reframe_filter,
        and assert that none of the known collision candidates appear there (§3.2).
        """
        neutral_label = "[outvx]"
        parts: list[str] = []
        _append_reframe_filter(parts, neutral_label, self._blur_pad_reframe())
        # Exclude the first token of seg0 (the consumed input label) from inspection.
        # Join everything except the leading neutral_label occurrence in seg0.
        seg0_tail = parts[0][len(neutral_label) :]
        generated_text = seg0_tail + " " + " ".join(parts[1:])
        for collision in ("[outv]", "[outveq]", "[outvsub]", "[outvscaled]"):
            assert collision not in generated_text, (
                f"Label collision: {collision!r} generated by _append_reframe_filter"
            )

    # --- anchor is ignored for background crop (AC-15 unit-side guarantee) ---

    def test_blur_pad_anchor_bottom_same_background_as_center(self) -> None:
        """anchor='bottom' blur_pad → background crop identical to anchor='center' (AC-15)."""
        parts_center, _ = _run_filter(self._blur_pad_reframe(anchor="center"))
        parts_bottom, _ = _run_filter(self._blur_pad_reframe(anchor="bottom"))
        # Segment 1 (background) must be identical regardless of anchor
        assert parts_center[1] == parts_bottom[1], (
            "Background crop segment must be center-fixed regardless of anchor"
        )

    def test_blur_pad_anchor_top_right_same_background(self) -> None:
        """anchor='top_right' blur_pad → background crop same as 'center'."""
        parts_center, _ = _run_filter(self._blur_pad_reframe(anchor="center"))
        parts_top_right, _ = _run_filter(self._blur_pad_reframe(anchor="top_right"))
        assert parts_center[1] == parts_top_right[1]

    # --- blur_pad ignores pad_color (§3.3 — background is blurred video) ---

    def test_blur_pad_pad_color_not_in_filter(self) -> None:
        """blur_pad → pad_color is NOT embedded in the filter (background is blurred; §3.3)."""
        parts, _ = _run_filter(_make_reframe(mode="blur_pad", pad_color="white"))
        all_text = " ".join(parts)
        # 'white' must not appear — background is blurred video, not colored pad
        assert "white" not in all_text

    def test_blur_pad_no_pad_filter_keyword(self) -> None:
        """blur_pad segments must not contain 'pad=' keyword (no padding)."""
        parts, _ = _run_filter(self._blur_pad_reframe())
        all_text = " ".join(parts)
        assert "pad=" not in all_text


# ===========================================================================
# §6 — Shared: terminal label always '[outvrf]', input label is preserved
# ===========================================================================


class TestAppendReframeFilterShared:
    """Shared invariants across all modes (architecture §6)."""

    @pytest.mark.parametrize("mode", ["crop", "pad", "blur_pad"])
    def test_terminal_label_always_outvrf(self, mode: str) -> None:
        """All modes return '[outvrf]' as the terminal label."""
        reframe = _make_reframe(mode=mode)
        _, label = _run_filter(reframe)
        assert label == "[outvrf]"

    @pytest.mark.parametrize("mode", ["crop", "pad", "blur_pad"])
    def test_first_segment_starts_with_input_label(self, mode: str) -> None:
        """First segment starts with the video_map_label passed in."""
        custom_label = "[outvscaled]"
        reframe = _make_reframe(mode=mode)
        parts: list[str] = []
        _append_reframe_filter(parts, custom_label, reframe)
        assert parts[0].startswith(custom_label), (
            f"mode={mode}: first segment should start with {custom_label!r}, "
            f"got {parts[0]!r}"
        )

    @pytest.mark.parametrize("mode", ["crop", "pad", "blur_pad"])
    def test_setsar_1_in_last_segment(self, mode: str) -> None:
        """setsar=1 appears in the last segment for all modes."""
        parts, _ = _run_filter(_make_reframe(mode=mode))
        assert "setsar=1" in parts[-1], (
            f"mode={mode}: setsar=1 not in last segment {parts[-1]!r}"
        )

    @pytest.mark.parametrize("mode", ["crop", "pad", "blur_pad"])
    def test_last_segment_ends_with_outvrf(self, mode: str) -> None:
        """Last segment ends with '[outvrf]' for all modes."""
        parts, _ = _run_filter(_make_reframe(mode=mode))
        assert parts[-1].endswith("[outvrf]")


# ===========================================================================
# SR M-1 — writer/reader pad_color allowlist parity
# ===========================================================================


from clipwright_render.plan import _RF_NAMED_COLORS, _RF_HEX_COLOR_RE  # noqa: E402
from clipwright_reframe.schemas import _NAMED_COLORS, _HEX_COLOR_RE  # noqa: E402


class TestWriterReaderPadColorParity:
    """Verify that clipwright-render's pad_color constants match clipwright-reframe's.

    The reader (plan.py) and writer (schemas.py) maintain independent copies of
    the named-color allowlist and hex-color regex.  This test ensures they stay
    in sync so that a color accepted by the writer is never rejected by the reader
    (and vice versa).  (SR M-1 CI guard.)
    """

    def test_named_color_allowlists_identical(self) -> None:
        """_RF_NAMED_COLORS (reader) == _NAMED_COLORS (writer)."""
        assert _RF_NAMED_COLORS == _NAMED_COLORS, (
            f"Named color allowlist mismatch.\n"
            f"  render (reader): {sorted(_RF_NAMED_COLORS)}\n"
            f"  reframe (writer): {sorted(_NAMED_COLORS)}"
        )

    def test_hex_color_patterns_identical(self) -> None:
        """_RF_HEX_COLOR_RE.pattern (reader) == _HEX_COLOR_RE.pattern (writer)."""
        assert _RF_HEX_COLOR_RE.pattern == _HEX_COLOR_RE.pattern, (
            f"Hex color regex mismatch.\n"
            f"  render (reader): {_RF_HEX_COLOR_RE.pattern!r}\n"
            f"  reframe (writer): {_HEX_COLOR_RE.pattern!r}"
        )
