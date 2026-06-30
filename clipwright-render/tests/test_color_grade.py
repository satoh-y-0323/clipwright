"""test_color_grade.py — Red-phase tests for WB/lut3d filter stages in clipwright-render.

Target functions (NOT YET IMPLEMENTED; all tests are expected to FAIL with
ImportError, AttributeError, or AssertionError):
  - _RenderWhiteBalance         — reader model mirroring WhiteBalanceParams (§6.1)
  - _RenderColorGrade           — aggregate {eq, white_balance, lut} (§6.1 / ADR-CO-8)
  - _validate_color_grade(color, otio_dir) -> _RenderColorGrade | None  (§6.1)
  - _append_wb_filter(parts, label, wb) -> str                          (§6.3 / FR-7)
  - _append_lut3d_filter(parts, label, lut_path) -> str                 (§6.3 / FR-8)
  - build_plan(..., color=color_with_white_balance_and_lut)             (§6.2 / FR-9)

Requirements: architecture-report-20260630-231945.md §5.2 / §6 / D4 / D6
FR-7/8/9 (WB + lut3d stages), NFR-2/4/5/6 (security/numeric/escape/yuv420p),
AC-4/5/6/8 (LUT pathpolicy / ordering / numeric lock / backward compat).
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode

from clipwright_render.plan import (
    ProbeInfo,
    build_plan,
    resolve_kept_ranges,
)
from clipwright_render.schemas import RenderOptions, SubtitleOptions

# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_color_eq.py / test_plan.py — no cross-import
# between test files per project convention)
# ---------------------------------------------------------------------------

FPS = 30.0

# Base color directive matching v0.2.x shape (eq only; no white_balance / lut).
# Consumed by _validate_color_eq (existing), which ignores unknown keys.
_COLOR_DICT_BASE: dict[str, Any] = {
    "tool": "clipwright-color",
    "version": "0.3.0",
    "kind": "color",
    "target_luma": 128.0,
    "measured": {
        "yavg": 96.4,
        "ymin": 12.0,
        "ymax": 230.0,
        "sampled_frames": 12,
    },
    "eq": {
        "brightness": 0.2,
        "contrast": 1.0,
        "saturation": 1.0,
        "gamma": 1.0,
    },
}

# Typical WB correction values (warm-biased scene: positive r, small g, negative b).
_WB_PARAMS: dict[str, float] = {"r": 0.12, "g": -0.05, "b": -0.08}

# Color dict with white_balance present (no lut); used in most ordering tests.
_COLOR_DICT_WB: dict[str, Any] = {
    **_COLOR_DICT_BASE,
    "white_balance": _WB_PARAMS,
}


def _rt(seconds: float, rate: float = FPS) -> otio.opentime.RationalTime:
    return otio.opentime.RationalTime(seconds * rate, rate)


def _tr(start: float, duration: float, rate: float = FPS) -> otio.opentime.TimeRange:
    return otio.opentime.TimeRange(
        start_time=_rt(start, rate),
        duration=_rt(duration, rate),
    )


def _make_clip(
    source: str,
    start: float,
    duration: float,
    rate: float = FPS,
) -> otio.schema.Clip:
    clip = otio.schema.Clip()
    clip.media_reference = otio.schema.ExternalReference(target_url=source)
    clip.source_range = _tr(start, duration, rate)
    return clip


def _make_timeline(clips: list[Any]) -> otio.schema.Timeline:
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    for c in clips:
        track.append(c)
    tl = otio.schema.Timeline()
    tl.tracks.append(track)
    return tl


def _make_probe(
    has_video: bool = True,
    audio_count: int = 1,
    bit_rate: int | None = 8_000_000,
    width: int | None = 1920,
    height: int | None = 1080,
    fps: float | None = 30.0,
) -> ProbeInfo:
    return ProbeInfo(
        has_video=has_video,
        audio_count=audio_count,
        bit_rate=bit_rate,
        width=width,
        height=height,
        fps=fps,
    )


def _single_source_plan_grade(
    color: dict[str, Any] | None = None,
    options: RenderOptions | None = None,
) -> Any:
    """Build a single-source RenderPlan with an optional color grade directive."""
    tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 5.0)])
    ranges = resolve_kept_ranges(tl)
    probe = _make_probe()
    return build_plan(  # type: ignore[call-arg]
        ranges,
        probe,
        options or RenderOptions(),
        color=color,
    )


def _multi_source_plan_grade(
    color: dict[str, Any] | None = None,
    options: RenderOptions | None = None,
) -> Any:
    """Build a multi-source RenderPlan with an optional color grade directive."""
    clips = [
        _make_clip("/src/a.mp4", 0.0, 3.0),
        _make_clip("/src/b.mp4", 0.0, 2.0),
    ]
    tl = _make_timeline(clips)
    ranges = resolve_kept_ranges(tl)
    source_probes = {
        "/src/a.mp4": _make_probe(width=1920, height=1080, fps=30.0),
        "/src/b.mp4": _make_probe(width=1920, height=1080, fps=30.0),
    }
    probe_info = source_probes["/src/a.mp4"]
    return build_plan(  # type: ignore[call-arg]
        ranges,
        probe_info,
        options or RenderOptions(),
        color=color,
        source_probes=source_probes,
    )


# ===========================================================================
# Aspect CG-1: Reader models — _RenderWhiteBalance / _RenderColorGrade (§6.1)
# ===========================================================================


class TestColorGradeSchema:
    """_RenderWhiteBalance and _RenderColorGrade reader models (§6.1 / ADR-CO-8)."""

    def test_wb_model_importable(self) -> None:
        """_RenderWhiteBalance can be imported from clipwright_render.plan."""
        from clipwright_render.plan import _RenderWhiteBalance  # type: ignore[attr-defined]

        assert _RenderWhiteBalance is not None

    def test_wb_model_r_g_b_fields(self) -> None:
        """_RenderWhiteBalance accepts r, g, b float fields in [-1, 1]."""
        from clipwright_render.plan import _RenderWhiteBalance  # type: ignore[attr-defined]

        wb = _RenderWhiteBalance(r=0.12, g=-0.05, b=-0.08)
        assert wb.r == pytest.approx(0.12)
        assert wb.g == pytest.approx(-0.05)
        assert wb.b == pytest.approx(-0.08)

    def test_wb_model_defaults_all_zero(self) -> None:
        """_RenderWhiteBalance default values are 0.0 (neutral white balance)."""
        from clipwright_render.plan import _RenderWhiteBalance  # type: ignore[attr-defined]

        wb = _RenderWhiteBalance()
        assert wb.r == 0.0
        assert wb.g == 0.0
        assert wb.b == 0.0

    def test_wb_model_rejects_out_of_range(self) -> None:
        """_RenderWhiteBalance rejects r/g/b outside [-1.0, 1.0]."""
        from pydantic import ValidationError

        from clipwright_render.plan import _RenderWhiteBalance  # type: ignore[attr-defined]

        with pytest.raises(ValidationError):
            _RenderWhiteBalance(r=2.0, g=0.0, b=0.0)

    def test_wb_model_rejects_extra_keys(self) -> None:
        """_RenderWhiteBalance rejects unknown fields (extra: forbid / CWE-20)."""
        from pydantic import ValidationError

        from clipwright_render.plan import _RenderWhiteBalance  # type: ignore[attr-defined]

        with pytest.raises(ValidationError):
            _RenderWhiteBalance(r=0.0, g=0.0, b=0.0, unknown=0.5)  # type: ignore[call-arg]

    def test_wb_model_rejects_inf(self) -> None:
        """_RenderWhiteBalance rejects inf values (allow_inf_nan=False / CWE-20)."""
        import math

        from pydantic import ValidationError

        from clipwright_render.plan import _RenderWhiteBalance  # type: ignore[attr-defined]

        with pytest.raises(ValidationError):
            _RenderWhiteBalance(r=math.inf, g=0.0, b=0.0)

    def test_color_grade_importable(self) -> None:
        """_RenderColorGrade can be imported from clipwright_render.plan."""
        from clipwright_render.plan import _RenderColorGrade  # type: ignore[attr-defined]

        assert _RenderColorGrade is not None

    def test_color_grade_has_eq_wb_lut_fields(self) -> None:
        """_RenderColorGrade has eq, white_balance, lut fields (ADR-CO-8)."""
        from clipwright_render.plan import _RenderColorGrade  # type: ignore[attr-defined]

        grade = _RenderColorGrade(eq=None, white_balance=None, lut=None)
        assert grade.eq is None
        assert grade.white_balance is None
        assert grade.lut is None

    def test_color_grade_stores_wb_object(self) -> None:
        """_RenderColorGrade stores a _RenderWhiteBalance in white_balance field."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _RenderColorGrade,
            _RenderWhiteBalance,
        )

        wb = _RenderWhiteBalance(r=0.1, g=0.0, b=-0.1)
        grade = _RenderColorGrade(eq=None, white_balance=wb, lut=None)
        assert grade.white_balance is wb

    def test_color_grade_stores_lut_path_string(self) -> None:
        """_RenderColorGrade stores a resolved path string in lut field."""
        from clipwright_render.plan import _RenderColorGrade  # type: ignore[attr-defined]

        grade = _RenderColorGrade(
            eq=None, white_balance=None, lut="/resolved/film.cube"
        )
        assert grade.lut == "/resolved/film.cube"


# ===========================================================================
# Aspect CG-2: _validate_color_grade orchestration (§6.1)
# ===========================================================================


class TestValidateColorGrade:
    """_validate_color_grade orchestrates eq + WB + LUT validation (§6.1)."""

    def _validate(
        self,
        color: dict[str, Any],
        otio_dir: Path | None = None,
    ) -> Any:
        from clipwright_render.plan import _validate_color_grade  # type: ignore[attr-defined]

        return _validate_color_grade(color, otio_dir or Path("."))

    def test_empty_dict_returns_grade_with_all_none_fields(self) -> None:
        """_validate_color_grade({}) returns _RenderColorGrade with all fields None."""
        grade = self._validate({})
        assert grade is not None
        assert grade.eq is None
        assert grade.white_balance is None
        assert grade.lut is None

    def test_missing_white_balance_key_leaves_wb_none(self) -> None:
        """color dict without white_balance key -> grade.white_balance is None."""
        color: dict[str, Any] = {
            "eq": {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "gamma": 1.0}
        }
        grade = self._validate(color)
        assert grade is not None
        assert grade.white_balance is None

    def test_missing_lut_key_leaves_lut_none(self) -> None:
        """color dict without lut key -> grade.lut is None."""
        grade = self._validate({"white_balance": _WB_PARAMS})
        assert grade is not None
        assert grade.lut is None

    def test_white_balance_present_populates_wb_fields(self) -> None:
        """white_balance key present -> grade.white_balance reflects r/g/b values."""
        grade = self._validate({"white_balance": _WB_PARAMS})
        assert grade is not None
        assert grade.white_balance is not None
        assert grade.white_balance.r == pytest.approx(0.12)
        assert grade.white_balance.g == pytest.approx(-0.05)
        assert grade.white_balance.b == pytest.approx(-0.08)

    def test_eq_key_present_populates_eq(self) -> None:
        """eq key present -> grade.eq reflects the values."""
        color: dict[str, Any] = {
            "eq": {"brightness": 0.2, "contrast": 1.0, "saturation": 1.0, "gamma": 1.0}
        }
        grade = self._validate(color)
        assert grade is not None
        assert grade.eq is not None
        assert grade.eq.brightness == pytest.approx(0.2)

    def test_invalid_wb_r_out_of_range_raises_invalid_input(self) -> None:
        """white_balance.r=5.0 (out of [-1, 1]) -> INVALID_INPUT."""
        color: dict[str, Any] = {"white_balance": {"r": 5.0, "g": 0.0, "b": 0.0}}
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate(color)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_wb_error_message_no_input_value(self) -> None:
        """INVALID_INPUT message for WB must not expose the raw value (CWE-209)."""
        color: dict[str, Any] = {"white_balance": {"r": 5.0, "g": 0.0, "b": 0.0}}
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate(color)
        assert "5.0" not in exc_info.value.message
        assert exc_info.value.__cause__ is None

    def test_valid_lut_path_populates_lut(self, tmp_path: Path) -> None:
        """Valid absolute .cube path -> grade.lut is set (resolved path)."""
        cube = tmp_path / "film.cube"
        cube.write_text("LUT_3D_SIZE 2\n")
        grade = self._validate({"lut": str(cube)}, otio_dir=tmp_path)
        assert grade is not None
        assert grade.lut is not None

    def test_full_grade_dict_populates_all_fields(self, tmp_path: Path) -> None:
        """Full color dict (eq + white_balance + lut) -> all three grade fields set."""
        cube = tmp_path / "film.cube"
        cube.write_text("LUT_3D_SIZE 2\n")
        color: dict[str, Any] = {
            **_COLOR_DICT_WB,
            "lut": str(cube),
        }
        grade = self._validate(color, otio_dir=tmp_path)
        assert grade is not None
        assert grade.eq is not None
        assert grade.white_balance is not None
        assert grade.lut is not None


# ===========================================================================
# Aspect CG-3: _append_wb_filter unit tests (§6.3 / FR-7 / AC-6)
# ===========================================================================


class TestAppendWbFilter:
    """_append_wb_filter appends colorbalance segment and returns new label (§6.3 / FR-7)."""

    def _append(
        self,
        parts: list[str],
        label: str,
        wb: Any,
    ) -> str:
        from clipwright_render.plan import _append_wb_filter  # type: ignore[attr-defined]

        return _append_wb_filter(parts, label, wb)

    def _make_wb(self, r: float = 0.0, g: float = 0.0, b: float = 0.0) -> Any:
        from clipwright_render.plan import _RenderWhiteBalance  # type: ignore[attr-defined]

        return _RenderWhiteBalance(r=r, g=g, b=b)

    def test_none_wb_returns_label_unchanged(self) -> None:
        """None white_balance -> label unchanged, no segment appended (no-op)."""
        parts: list[str] = []
        result = self._append(parts, "[outv]", None)
        assert result == "[outv]"
        assert parts == []

    def test_none_wb_outvscaled_returns_unchanged(self) -> None:
        """None white_balance with [outvscaled] input -> label returned unchanged."""
        parts: list[str] = []
        result = self._append(parts, "[outvscaled]", None)
        assert result == "[outvscaled]"

    def test_valid_wb_returns_outvwb_label(self) -> None:
        """Valid white_balance -> returns '[outvwb]' label."""
        parts: list[str] = []
        wb = self._make_wb(r=0.12, g=-0.05, b=-0.08)
        result = self._append(parts, "[outv]", wb)
        assert result == "[outvwb]"

    def test_valid_wb_appends_exactly_one_segment(self) -> None:
        """Valid white_balance -> exactly one segment appended to filter_parts."""
        parts: list[str] = []
        wb = self._make_wb(r=0.12, g=-0.05, b=-0.08)
        self._append(parts, "[outv]", wb)
        assert len(parts) == 1

    def test_segment_starts_with_input_label(self) -> None:
        """Appended segment starts with the input video label."""
        parts: list[str] = []
        wb = self._make_wb(r=0.12, g=-0.05, b=-0.08)
        self._append(parts, "[outvscaled]", wb)
        assert parts[0].startswith("[outvscaled]")

    def test_segment_ends_with_outvwb(self) -> None:
        """Appended segment ends with '[outvwb]'."""
        parts: list[str] = []
        wb = self._make_wb(r=0.12, g=-0.05, b=-0.08)
        self._append(parts, "[outv]", wb)
        assert parts[0].endswith("[outvwb]")

    def test_colorbalance_keyword_in_segment(self) -> None:
        """Segment contains 'colorbalance=' keyword."""
        parts: list[str] = []
        wb = self._make_wb(r=0.12, g=-0.05, b=-0.08)
        self._append(parts, "[outv]", wb)
        assert "colorbalance=" in parts[0]

    def test_rm_gm_bm_params_in_segment(self) -> None:
        """Segment contains rm=, gm=, bm= parameters."""
        parts: list[str] = []
        wb = self._make_wb(r=0.12, g=-0.05, b=-0.08)
        self._append(parts, "[outv]", wb)
        assert "rm=" in parts[0]
        assert "gm=" in parts[0]
        assert "bm=" in parts[0]

    def test_full_colorbalance_segment_format(self) -> None:
        """Full segment: [label]colorbalance=rm={r:g}:gm={g:g}:bm={b:g}[outvwb] (§6.3 / AC-6)."""
        parts: list[str] = []
        r, g, b = 0.12, -0.05, -0.08
        wb = self._make_wb(r=r, g=g, b=b)
        self._append(parts, "[outv]", wb)
        expected_seg = f"colorbalance=rm={r:g}:gm={g:g}:bm={b:g}"
        assert expected_seg in parts[0]

    def test_neutral_wb_zero_values_format(self) -> None:
        """Neutral WB (r=g=b=0.0) emits colorbalance=rm=0:gm=0:bm=0 (no exponent)."""
        parts: list[str] = []
        wb = self._make_wb(r=0.0, g=0.0, b=0.0)
        self._append(parts, "[outv]", wb)
        assert "colorbalance=rm=0:gm=0:bm=0" in parts[0]


# ===========================================================================
# Aspect CG-4: _append_lut3d_filter unit tests (§6.3 / FR-8 / NFR-5)
# ===========================================================================


class TestAppendLut3dFilter:
    """_append_lut3d_filter appends lut3d segment and returns new label (§6.3 / FR-8)."""

    def _append(
        self,
        parts: list[str],
        label: str,
        lut_path: str | None,
    ) -> str:
        from clipwright_render.plan import _append_lut3d_filter  # type: ignore[attr-defined]

        return _append_lut3d_filter(parts, label, lut_path)

    def test_none_lut_returns_label_unchanged(self) -> None:
        """None lut_path -> label unchanged, no segment appended (no-op)."""
        parts: list[str] = []
        result = self._append(parts, "[outveq]", None)
        assert result == "[outveq]"
        assert parts == []

    def test_valid_lut_returns_outvlut_label(self) -> None:
        """Valid lut_path -> returns '[outvlut]' label."""
        parts: list[str] = []
        result = self._append(parts, "[outveq]", "/path/to/film.cube")
        assert result == "[outvlut]"

    def test_valid_lut_appends_exactly_one_segment(self) -> None:
        """Valid lut_path -> exactly one segment appended to filter_parts."""
        parts: list[str] = []
        self._append(parts, "[outveq]", "/path/to/film.cube")
        assert len(parts) == 1

    def test_segment_starts_with_input_label(self) -> None:
        """Appended segment starts with the input video label."""
        parts: list[str] = []
        self._append(parts, "[outveq]", "/path/to/film.cube")
        assert parts[0].startswith("[outveq]")

    def test_segment_ends_with_outvlut(self) -> None:
        """Appended segment ends with '[outvlut]'."""
        parts: list[str] = []
        self._append(parts, "[outveq]", "/path/to/film.cube")
        assert parts[0].endswith("[outvlut]")

    def test_lut3d_keyword_in_segment(self) -> None:
        """Segment contains 'lut3d=' keyword."""
        parts: list[str] = []
        self._append(parts, "[outveq]", "/path/to/film.cube")
        assert "lut3d=" in parts[0]

    def test_file_param_single_quoted(self) -> None:
        """file= param uses single-quote wrap: lut3d=file='...' (NFR-5)."""
        parts: list[str] = []
        self._append(parts, "[outveq]", "/path/to/film.cube")
        assert "file='" in parts[0]
        # Body up to [outvlut] must end with closing single quote
        body = parts[0][: parts[0].index("[outvlut]")]
        assert body.endswith("'")

    def test_escape_applied_to_backslash(self) -> None:
        """_escape_filtergraph applied: backslash -> double-backslash (NFR-5)."""
        parts: list[str] = []
        path = "C:\\Users\\test\\film.cube"
        self._append(parts, "[outveq]", path)
        escaped = path.replace("\\", "\\\\").replace(":", "\\:")
        assert f"file='{escaped}'" in parts[0]

    def test_escape_applied_to_colon(self) -> None:
        """_escape_filtergraph applied: colon -> \\: (NFR-5)."""
        parts: list[str] = []
        path = "C:\\film.cube"
        self._append(parts, "[outveq]", path)
        escaped = path.replace("\\", "\\\\").replace(":", "\\:")
        assert f"file='{escaped}'" in parts[0]

    def test_posix_path_no_escaping_needed(self) -> None:
        """POSIX path without special chars appears unescaped between single quotes."""
        parts: list[str] = []
        path = "/home/user/grade/film.cube"
        self._append(parts, "[outveq]", path)
        assert f"file='{path}'" in parts[0]

    def test_none_lut_after_outvwb_input_label(self) -> None:
        """None lut with [outvwb] input label -> [outvwb] returned unchanged."""
        parts: list[str] = []
        result = self._append(parts, "[outvwb]", None)
        assert result == "[outvwb]"
        assert parts == []


# ===========================================================================
# Aspect CG-5: AC-5 filter ordering — single-source path (D4 / FR-9)
# ===========================================================================


class TestGradeOrderingSingleSource:
    """colorbalance -> eq -> lut3d -> subtitles order in single-source path (AC-5 / FR-9)."""

    def test_colorbalance_before_eq_before_lut3d(self, tmp_path: Path) -> None:
        """colorbalance= pos < eq= pos < lut3d=file=' pos in single-source filter_complex."""
        cube = tmp_path / "film.cube"
        cube.write_text("LUT_3D_SIZE 2\n")
        color: dict[str, Any] = {**_COLOR_DICT_WB, "lut": str(cube)}
        plan = _single_source_plan_grade(color=color)
        fc = plan.filter_complex

        wb_pos = fc.find("colorbalance=")
        eq_pos = fc.find("eq=")
        lut_pos = fc.find("lut3d=file='")

        assert wb_pos != -1, f"colorbalance= not found in filter_complex: {fc!r}"
        assert eq_pos != -1, f"eq= not found in filter_complex: {fc!r}"
        assert lut_pos != -1, f"lut3d=file=' not found in filter_complex: {fc!r}"
        assert wb_pos < eq_pos, (
            f"colorbalance (pos={wb_pos}) must precede eq (pos={eq_pos})"
        )
        assert eq_pos < lut_pos, f"eq (pos={eq_pos}) must precede lut3d (pos={lut_pos})"

    def test_lut3d_before_subtitles(self, tmp_path: Path) -> None:
        """lut3d=file=' pos < subtitles= pos when subtitle option is active (AC-5)."""
        cube = tmp_path / "film.cube"
        cube.write_text("LUT_3D_SIZE 2\n")
        with tempfile.NamedTemporaryFile(suffix=".vtt", mode="w", delete=False) as f:
            f.write("WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHello\n")
            sub_path = f.name

        color: dict[str, Any] = {**_COLOR_DICT_WB, "lut": str(cube)}
        options = RenderOptions(subtitle=SubtitleOptions(path=sub_path))
        plan = _single_source_plan_grade(color=color, options=options)
        fc = plan.filter_complex

        lut_pos = fc.find("lut3d=file='")
        sub_pos = fc.find("subtitles=")

        assert lut_pos != -1, f"lut3d=file=' not found in filter_complex: {fc!r}"
        assert sub_pos != -1, f"subtitles= not found in filter_complex: {fc!r}"
        assert lut_pos < sub_pos, (
            f"lut3d (pos={lut_pos}) must precede subtitles (pos={sub_pos})"
        )

    def test_outvwb_label_present_single_source(self) -> None:
        """[outvwb] label present in filter_complex when WB is active."""
        plan = _single_source_plan_grade(color=_COLOR_DICT_WB)
        assert "[outvwb]" in plan.filter_complex

    def test_outvlut_label_present_single_source(self, tmp_path: Path) -> None:
        """[outvlut] label present in filter_complex when lut is active."""
        cube = tmp_path / "film.cube"
        cube.write_text("LUT_3D_SIZE 2\n")
        color: dict[str, Any] = {**_COLOR_DICT_WB, "lut": str(cube)}
        plan = _single_source_plan_grade(color=color)
        assert "[outvlut]" in plan.filter_complex

    def test_outvwb_before_outveq_single_source(self) -> None:
        """[outvwb] position < [outveq] position in single-source filter_complex."""
        plan = _single_source_plan_grade(color=_COLOR_DICT_WB)
        fc = plan.filter_complex
        wb_label_pos = fc.find("[outvwb]")
        eq_label_pos = fc.find("[outveq]")
        assert wb_label_pos != -1, "[outvwb] not found in filter_complex"
        assert eq_label_pos != -1, "[outveq] not found in filter_complex"
        assert wb_label_pos < eq_label_pos, (
            f"[outvwb] (pos={wb_label_pos}) must precede [outveq] (pos={eq_label_pos})"
        )

    def test_outveq_before_outvlut_single_source(self, tmp_path: Path) -> None:
        """[outveq] position < [outvlut] position in single-source filter_complex."""
        cube = tmp_path / "film.cube"
        cube.write_text("LUT_3D_SIZE 2\n")
        color: dict[str, Any] = {**_COLOR_DICT_WB, "lut": str(cube)}
        plan = _single_source_plan_grade(color=color)
        fc = plan.filter_complex
        eq_pos = fc.find("[outveq]")
        lut_pos = fc.find("[outvlut]")
        assert eq_pos != -1, "[outveq] not found in filter_complex"
        assert lut_pos != -1, "[outvlut] not found in filter_complex"
        assert eq_pos < lut_pos


# ===========================================================================
# Aspect CG-6: AC-5 filter ordering — multi-source path (D4 / FR-9)
# ===========================================================================


class TestGradeOrderingMultiSource:
    """colorbalance -> eq -> lut3d -> subtitles order in multi-source path (AC-5 / FR-9)."""

    def test_colorbalance_before_eq_before_lut3d(self, tmp_path: Path) -> None:
        """colorbalance < eq < lut3d ordering in multi-source filter_complex (AC-5)."""
        cube = tmp_path / "film.cube"
        cube.write_text("LUT_3D_SIZE 2\n")
        color: dict[str, Any] = {**_COLOR_DICT_WB, "lut": str(cube)}
        plan = _multi_source_plan_grade(color=color)
        fc = plan.filter_complex

        wb_pos = fc.find("colorbalance=")
        eq_pos = fc.find("eq=")
        lut_pos = fc.find("lut3d=file='")

        assert wb_pos != -1, (
            f"colorbalance= not found in multi-source filter_complex: {fc!r}"
        )
        assert eq_pos != -1, f"eq= not found in multi-source filter_complex: {fc!r}"
        assert lut_pos != -1, (
            f"lut3d=file=' not found in multi-source filter_complex: {fc!r}"
        )
        assert wb_pos < eq_pos, (
            f"colorbalance (pos={wb_pos}) must precede eq (pos={eq_pos})"
        )
        assert eq_pos < lut_pos, f"eq (pos={eq_pos}) must precede lut3d (pos={lut_pos})"

    def test_outvwb_label_present_multi_source(self) -> None:
        """[outvwb] label present in multi-source filter_complex when WB is active."""
        plan = _multi_source_plan_grade(color=_COLOR_DICT_WB)
        assert "[outvwb]" in plan.filter_complex

    def test_outvlut_label_present_multi_source(self, tmp_path: Path) -> None:
        """[outvlut] label present in multi-source filter_complex when lut is active."""
        cube = tmp_path / "film.cube"
        cube.write_text("LUT_3D_SIZE 2\n")
        color: dict[str, Any] = {**_COLOR_DICT_WB, "lut": str(cube)}
        plan = _multi_source_plan_grade(color=color)
        assert "[outvlut]" in plan.filter_complex

    def test_lut3d_before_subtitles_multi_source(self, tmp_path: Path) -> None:
        """lut3d < subtitles ordering in multi-source filter_complex with subtitle active (AC-5)."""
        cube = tmp_path / "film.cube"
        cube.write_text("LUT_3D_SIZE 2\n")
        with tempfile.NamedTemporaryFile(suffix=".vtt", mode="w", delete=False) as f:
            f.write("WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHello\n")
            sub_path = f.name

        color: dict[str, Any] = {**_COLOR_DICT_WB, "lut": str(cube)}
        options = RenderOptions(subtitle=SubtitleOptions(path=sub_path))
        plan = _multi_source_plan_grade(color=color, options=options)
        fc = plan.filter_complex

        lut_pos = fc.find("lut3d=file='")
        sub_pos = fc.find("subtitles=")
        assert lut_pos != -1, (
            f"lut3d=file=' not found in multi-source filter_complex: {fc!r}"
        )
        assert sub_pos != -1, (
            f"subtitles= not found in multi-source filter_complex: {fc!r}"
        )
        assert lut_pos < sub_pos

    def test_outvwb_before_outveq_multi_source(self) -> None:
        """[outvwb] position < [outveq] position in multi-source filter_complex."""
        plan = _multi_source_plan_grade(color=_COLOR_DICT_WB)
        fc = plan.filter_complex
        wb_label_pos = fc.find("[outvwb]")
        eq_label_pos = fc.find("[outveq]")
        assert wb_label_pos != -1, "[outvwb] not found in multi-source filter_complex"
        assert eq_label_pos != -1, "[outveq] not found in multi-source filter_complex"
        assert wb_label_pos < eq_label_pos


# ===========================================================================
# Aspect CG-7: AC-6 WB numeric format lock (SR-INJ-002 / NFR-4)
# ===========================================================================


class TestGradeNumericLock:
    """AC-6: WB values in filtergraph use :g formatting (no exponent; no special chars)."""

    def test_wb_values_g_format_no_exponent(self) -> None:
        """:g-formatted colorbalance= values must not contain exponent notation."""
        plan = _single_source_plan_grade(color=_COLOR_DICT_WB)
        fc = plan.filter_complex
        m = re.search(r"colorbalance=rm=([^:]+):gm=([^:]+):bm=([^\[]+)", fc)
        assert m is not None, (
            f"colorbalance= pattern not found in filter_complex: {fc!r}"
        )
        for val_str in m.groups():
            val_str = val_str.strip()
            assert re.fullmatch(r"-?[\d.]+", val_str), (
                f"Non-:g value in colorbalance parameter: {val_str!r}"
            )

    def test_wb_values_no_filtergraph_special_chars(self) -> None:
        """colorbalance= param values must not contain filtergraph delimiters."""
        plan = _single_source_plan_grade(color=_COLOR_DICT_WB)
        fc = plan.filter_complex
        m = re.search(r"colorbalance=rm=([^:]+):gm=([^:]+):bm=([^\[]+)", fc)
        assert m is not None, f"colorbalance= pattern not found: {fc!r}"
        for val_str in m.groups():
            for special in ("[", "]", ",", ";"):
                assert special not in val_str, (
                    f"Special char {special!r} leaked into WB value {val_str!r}"
                )

    def test_zero_wb_values_no_exponent_notation(self) -> None:
        """Neutral WB (all zeros) -> rm=0:gm=0:bm=0 (not 0e+00 or similar)."""
        color: dict[str, Any] = {
            **_COLOR_DICT_BASE,
            "white_balance": {"r": 0.0, "g": 0.0, "b": 0.0},
        }
        plan = _single_source_plan_grade(color=color)
        assert "colorbalance=rm=0:gm=0:bm=0" in plan.filter_complex


# ===========================================================================
# Aspect CG-8: AC-8 backward compatibility (FR-10)
# ===========================================================================


class TestGradeBackwardCompat:
    """AC-8: v0.2.x color directive -> no colorbalance/lut3d; filtergraph matches v0.16.0."""

    def test_v02x_dict_no_colorbalance_in_filter_complex(self) -> None:
        """v0.2.x color dict (eq only, no white_balance) -> no colorbalance= in filter_complex."""
        from clipwright_render.plan import _RenderColorGrade  # type: ignore[attr-defined]

        plan = _single_source_plan_grade(color=_COLOR_DICT_BASE)
        assert "colorbalance" not in plan.filter_complex, (
            "colorbalance must not appear when white_balance key is absent (AC-8)"
        )

    def test_v02x_dict_no_lut3d_in_filter_complex(self) -> None:
        """v0.2.x color dict (no lut) -> no lut3d= in filter_complex."""
        from clipwright_render.plan import _RenderColorGrade  # type: ignore[attr-defined]

        plan = _single_source_plan_grade(color=_COLOR_DICT_BASE)
        assert "lut3d" not in plan.filter_complex, (
            "lut3d must not appear when lut key is absent (AC-8)"
        )

    def test_v02x_dict_no_outvwb_label(self) -> None:
        """v0.2.x color dict -> no [outvwb] label in filter_complex."""
        from clipwright_render.plan import _RenderColorGrade  # type: ignore[attr-defined]

        plan = _single_source_plan_grade(color=_COLOR_DICT_BASE)
        assert "[outvwb]" not in plan.filter_complex

    def test_v02x_dict_no_outvlut_label(self) -> None:
        """v0.2.x color dict -> no [outvlut] label in filter_complex."""
        from clipwright_render.plan import _RenderColorGrade  # type: ignore[attr-defined]

        plan = _single_source_plan_grade(color=_COLOR_DICT_BASE)
        assert "[outvlut]" not in plan.filter_complex

    def test_explicit_none_wb_and_lut_no_colorbalance(self) -> None:
        """Explicit white_balance=None, lut=None -> strict no-op (no colorbalance/lut3d)."""
        from clipwright_render.plan import _RenderColorGrade  # type: ignore[attr-defined]

        color: dict[str, Any] = {**_COLOR_DICT_BASE, "white_balance": None, "lut": None}
        plan = _single_source_plan_grade(color=color)
        assert "colorbalance" not in plan.filter_complex
        assert "lut3d" not in plan.filter_complex

    def test_v02x_eq_stage_still_present_without_wb_lut(self) -> None:
        """v0.2.x dict: eq stage is still injected when only eq is present (no regression)."""
        from clipwright_render.plan import _validate_color_grade  # type: ignore[attr-defined]

        plan = _single_source_plan_grade(color=_COLOR_DICT_BASE)
        # eq stage must remain in the filtergraph
        assert "[outveq]" in plan.filter_complex
        assert "eq=brightness=" in plan.filter_complex


# ===========================================================================
# Aspect CG-9: §5.2 / NFR-2 LUT path security (CWE-22 / CWE-59 / CWE-209)
# ===========================================================================


class TestLutPathSecurity:
    """§5.2 / NFR-2: .cube path re-validated at render time; untrusted OTIO rejected (AC-4)."""

    def _validate_grade(
        self,
        color: dict[str, Any],
        otio_dir: Path,
    ) -> Any:
        from clipwright_render.plan import _validate_color_grade  # type: ignore[attr-defined]

        return _validate_color_grade(color, otio_dir)

    def test_directory_traversal_lut_raises_error(self, tmp_path: Path) -> None:
        """Relative lut with '../' traversal -> rejected (CWE-22 / AC-4)."""
        color: dict[str, Any] = {**_COLOR_DICT_BASE, "lut": "../outside.cube"}
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate_grade(color, otio_dir=tmp_path)
        assert exc_info.value.code in (
            ErrorCode.INVALID_INPUT,
            ErrorCode.PATH_NOT_ALLOWED,
        )

    def test_traversal_error_no_full_path_in_message(self, tmp_path: Path) -> None:
        """Traversal error message must not contain the raw path (CWE-209 / ADR-CO-10)."""
        color: dict[str, Any] = {**_COLOR_DICT_BASE, "lut": "../outside.cube"}
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate_grade(color, otio_dir=tmp_path)
        assert str(tmp_path) not in exc_info.value.message
        assert "../outside.cube" not in exc_info.value.message

    def test_traversal_error_no_cause_chain(self, tmp_path: Path) -> None:
        """Traversal error must suppress cause chain via from None (CWE-209)."""
        color: dict[str, Any] = {**_COLOR_DICT_BASE, "lut": "../outside.cube"}
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate_grade(color, otio_dir=tmp_path)
        assert exc_info.value.__cause__ is None

    def test_nonexistent_lut_raises_error(self, tmp_path: Path) -> None:
        """Nonexistent .cube absolute path -> INVALID_INPUT or FILE_NOT_FOUND."""
        missing = tmp_path / "nonexistent.cube"
        color: dict[str, Any] = {**_COLOR_DICT_BASE, "lut": str(missing)}
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate_grade(color, otio_dir=tmp_path)
        assert exc_info.value.code in (
            ErrorCode.INVALID_INPUT,
            ErrorCode.FILE_NOT_FOUND,
        )

    def test_nonexistent_error_no_full_path_in_message(self, tmp_path: Path) -> None:
        """Nonexistent .cube error message must not contain the full path (CWE-209)."""
        missing = tmp_path / "nonexistent.cube"
        color: dict[str, Any] = {**_COLOR_DICT_BASE, "lut": str(missing)}
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate_grade(color, otio_dir=tmp_path)
        assert str(missing) not in exc_info.value.message

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="symlink creation requires elevated privileges on Windows (see MEMORY.md)",
    )
    def test_symlink_lut_raises_error(self, tmp_path: Path) -> None:
        """Symlink .cube -> PATH_NOT_ALLOWED or INVALID_INPUT (CWE-59 / AC-4 / ADR-CO-10)."""
        real_cube = tmp_path / "real.cube"
        real_cube.write_text("LUT_3D_SIZE 2\n")
        link = tmp_path / "symlink.cube"
        link.symlink_to(real_cube)
        color: dict[str, Any] = {**_COLOR_DICT_BASE, "lut": str(link)}
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate_grade(color, otio_dir=tmp_path)
        assert exc_info.value.code in (
            ErrorCode.PATH_NOT_ALLOWED,
            ErrorCode.INVALID_INPUT,
        )

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="symlink creation requires elevated privileges on Windows (see MEMORY.md)",
    )
    def test_symlink_error_no_full_path_in_message(self, tmp_path: Path) -> None:
        """Symlink error message must not contain the full path (CWE-209)."""
        real_cube = tmp_path / "real.cube"
        real_cube.write_text("LUT_3D_SIZE 2\n")
        link = tmp_path / "symlink.cube"
        link.symlink_to(real_cube)
        color: dict[str, Any] = {**_COLOR_DICT_BASE, "lut": str(link)}
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate_grade(color, otio_dir=tmp_path)
        assert str(link) not in exc_info.value.message


# ===========================================================================
# Aspect CG-10: NFR-6 yuv420p pin remains intact after lut3d stage
# ===========================================================================


class TestYuv420pPin:
    """NFR-6: -pix_fmt yuv420p must remain in ffmpeg_args when lut3d stage is active."""

    def test_yuv420p_present_with_wb_and_lut(self, tmp_path: Path) -> None:
        """With WB + lut3d active, -pix_fmt yuv420p must still appear in ffmpeg_args."""
        from clipwright_render.plan import _RenderColorGrade  # type: ignore[attr-defined]

        cube = tmp_path / "film.cube"
        cube.write_text("LUT_3D_SIZE 2\n")
        color: dict[str, Any] = {**_COLOR_DICT_WB, "lut": str(cube)}
        plan = _single_source_plan_grade(color=color)
        assert "-pix_fmt" in plan.ffmpeg_args
        idx = plan.ffmpeg_args.index("-pix_fmt")
        assert plan.ffmpeg_args[idx + 1] == "yuv420p", (
            f"-pix_fmt must be 'yuv420p', got {plan.ffmpeg_args[idx + 1]!r}"
        )

    def test_lut3d_in_filter_complex_yuv420p_in_ffmpeg_args(
        self, tmp_path: Path
    ) -> None:
        """lut3d is in filter_complex (pre-encode); yuv420p pin is in ffmpeg_args (post-filter, NFR-6)."""
        from clipwright_render.plan import _RenderColorGrade  # type: ignore[attr-defined]

        cube = tmp_path / "film.cube"
        cube.write_text("LUT_3D_SIZE 2\n")
        color: dict[str, Any] = {**_COLOR_DICT_WB, "lut": str(cube)}
        plan = _single_source_plan_grade(color=color)
        assert "lut3d=file='" in plan.filter_complex, (
            "lut3d stage must be in filter_complex, not in ffmpeg_args"
        )
        assert "-pix_fmt" in plan.ffmpeg_args, (
            "-pix_fmt yuv420p pin must be in ffmpeg_args (post-filter)"
        )
