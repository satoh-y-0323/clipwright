"""Tests for clipwright-text add_text() core logic — TDD Red phase.

All tests in this module verify the contract of add_text() as defined in:
  - architecture-report-20260617-230606.md §3.4 / §3.5
  - requirements-report-20260617-230230.md AC-1-2 ~ AC-1-9

These tests are written before the implementation exists (Red phase).
They are expected to fail with ImportError / NotImplementedError until
the developer implements clipwright_text.text.add_text().

Covered contracts:
  - AC-1-2  Value range violations -> INVALID_INPUT + hint (parametrize)
  - AC-1-3  text empty / newline / control chars -> INVALID_INPUT
  - AC-1-4  x / y control chars -> INVALID_INPUT
  - AC-1-5  font_color / box_color allowlist -> INVALID_INPUT / pass
  - AC-1-6  Normal annotation: text_overlay marker added, name=text_0,
            metadata["clipwright"] fields, marked_range rate
  - AC-1-7  Accumulation: two distinct calls -> text_0 / text_1
  - AC-1-8  Idempotent no-op: exact duplicate -> applied=0 + warning,
            marker count unchanged
  - AC-1-9  Non-destructive: input OTIO marker count unchanged after call
  - AC-1-10 Boundary: output outside timeline dir -> PATH_NOT_ALLOWED
  - AC-1-11 Boundary: non-.otio output -> INVALID_INPUT
  - AC-1-12 Boundary: output == timeline -> INVALID_INPUT
  - AC-1-13 Boundary: no video track -> UNSUPPORTED_OPERATION
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import opentimelineio as otio
import pytest

from clipwright_text.text import add_text
from clipwright_text.schemas import AddTextOptions

# ---------------------------------------------------------------------------
# Fixture helpers (conftest.py is owned by test-text-schema task; helpers are
# defined inline here to remain self-contained for the Red phase)
# ---------------------------------------------------------------------------

_RATE = 24.0


def _make_clip(name: str, duration_sec: float = 10.0, rate: float = _RATE) -> otio.schema.Clip:
    ref = otio.schema.ExternalReference(target_url=f"file:///media/{name}.mp4")
    sr = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, rate),
        duration=otio.opentime.RationalTime(duration_sec * rate, rate),
    )
    return otio.schema.Clip(name=name, media_reference=ref, source_range=sr)


def _make_v1_timeline(n_clips: int = 1, rate: float = _RATE) -> otio.schema.Timeline:
    """Return a timeline with a V1 video track containing n_clips clips."""
    tl = otio.schema.Timeline(name="test_tl")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(v1)
    tl.tracks.append(a1)
    for i in range(n_clips):
        v1.append(_make_clip(f"clip{i}", rate=rate))
    return tl


def _make_audio_only_timeline() -> otio.schema.Timeline:
    """Return a timeline with no video track."""
    tl = otio.schema.Timeline(name="audio_only")
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(a1)
    a1.append(_make_clip("audio_clip"))
    return tl


def _write_timeline(tl: otio.schema.Timeline, path: Path) -> None:
    otio.adapters.write_to_file(tl, str(path))


def _read_timeline(path: Path) -> otio.schema.Timeline:
    return otio.adapters.read_from_file(str(path))  # type: ignore[no-any-return]


def _get_text_overlay_markers(tl: otio.schema.Timeline) -> list[otio.schema.Marker]:
    """Return all text_overlay markers from the V1 track."""
    for track in tl.tracks:
        if track.kind == otio.schema.TrackKind.Video:
            return [
                m
                for m in track.markers
                if m.metadata.get("clipwright", {}).get("kind") == "text_overlay"
            ]
    return []


def _default_opts(**overrides: object) -> AddTextOptions:
    """Return an AddTextOptions with valid defaults, allowing field overrides."""
    base: dict[str, object] = {
        "text": "Hello World",
        "start_sec": 1.0,
        "duration_sec": 3.0,
    }
    base.update(overrides)
    return AddTextOptions(**base)  # type: ignore[arg-type]


# ===========================================================================
# AC-1-2  Value range violations -> INVALID_INPUT + hint
# ===========================================================================


class TestValueRangeViolations:
    """Each out-of-range field must return INVALID_INPUT with a non-empty hint."""

    @pytest.mark.parametrize(
        "field,value",
        [
            ("start_sec", -0.001),
            ("start_sec", -100.0),
            ("duration_sec", 0.0),
            ("duration_sec", -1.0),
            ("font_size", 0),
            ("font_size", -1),
            ("fade_in_sec", -0.001),
            ("fade_out_sec", -0.001),
        ],
    )
    def test_invalid_range_returns_invalid_input(
        self, field: str, value: object
    ) -> None:
        """Out-of-range field must return INVALID_INPUT with hint."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            opts = _default_opts(**{field: value})
            result = add_text(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT for {field}={value}, got {error.get('code')!r}"
            )
            assert error.get("hint"), f"hint must be non-empty for {field}={value}"

    def test_fade_sum_exceeds_duration(self) -> None:
        """fade_in_sec + fade_out_sec > duration_sec must return INVALID_INPUT."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            # fade_in=1.5 + fade_out=1.5 = 3.0 > duration=2.0
            opts = _default_opts(duration_sec=2.0, fade_in_sec=1.5, fade_out_sec=1.5)
            result = add_text(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT"
            assert error.get("hint")

    def test_fade_sum_exactly_equals_duration_ok(self) -> None:
        """fade_in_sec + fade_out_sec == duration_sec must be accepted (boundary)."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            # fade_in=1.0 + fade_out=1.0 == duration=2.0 -> should pass
            opts = _default_opts(duration_sec=2.0, fade_in_sec=1.0, fade_out_sec=1.0)
            result = add_text(str(inp), str(out), opts)

            assert result["ok"] is True, (
                f"fade_in+fade_out==duration should be OK, got: {result.get('error')}"
            )


# ===========================================================================
# AC-1-3  text empty / newline / control characters -> INVALID_INPUT
# ===========================================================================


class TestTextValidation:
    """Invalid text values must return INVALID_INPUT."""

    @pytest.mark.parametrize(
        "bad_text",
        [
            "",           # empty string
            "Hello\nWorld",   # newline LF
            "Hello\rWorld",   # newline CR
            "Hello\x00World", # null control char
            "Hello\x7fWorld", # DEL control char
            "\x01abc",        # SOH control char
            "\x1fabc",        # US control char
        ],
    )
    def test_bad_text_returns_invalid_input(self, bad_text: str) -> None:
        """Empty or control-char-containing text must return INVALID_INPUT."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            opts = _default_opts(text=bad_text)
            result = add_text(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT for text={bad_text!r}, got {error.get('code')!r}"
            )
            assert error.get("hint")


# ===========================================================================
# AC-1-4  x / y control characters -> INVALID_INPUT
# ===========================================================================


class TestPositionExprValidation:
    """Control chars / newlines in x or y must return INVALID_INPUT."""

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("x", "(w-tw)/2\n"),
            ("x", "(w-tw)\x00/2"),
            ("x", "(w-tw)\r/2"),
            ("y", "h-th-40\n"),
            ("y", "h-th\x00-40"),
            ("y", "h-th-40\x7f"),
        ],
    )
    def test_control_char_in_position_expr_returns_invalid_input(
        self, field: str, bad_value: str
    ) -> None:
        """Newline / control char in x or y must return INVALID_INPUT."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            opts = _default_opts(**{field: bad_value})
            result = add_text(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT for {field}={bad_value!r}, "
                f"got {error.get('code')!r}"
            )
            assert error.get("hint")


# ===========================================================================
# AC-1-5  font_color / box_color allowlist
# ===========================================================================


class TestColorAllowlist:
    """Color values outside the allowlist must return INVALID_INPUT."""

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            # space in color
            ("font_color", "white "),
            ("font_color", " white"),
            ("box_color", "black @0.5"),
            # single quotes
            ("font_color", "'white'"),
            ("box_color", "'black@0.5'"),
            # colon (filtergraph separator)
            ("font_color", "white:100"),
            ("box_color", "black:50"),
            # comma (filtergraph option separator)
            ("font_color", "white,red"),
            ("box_color", "black,0.5"),
        ],
    )
    def test_invalid_color_returns_invalid_input(
        self, field: str, bad_value: str
    ) -> None:
        """Color value outside allowlist must return INVALID_INPUT with hint."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            opts = _default_opts(**{field: bad_value})
            result = add_text(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT for {field}={bad_value!r}, "
                f"got {error.get('code')!r}"
            )
            assert error.get("hint")

    @pytest.mark.parametrize(
        "field,valid_value",
        [
            ("font_color", "white"),
            ("font_color", "#FFCC00"),
            ("font_color", "#ffcc00"),
            ("font_color", "black@0.5"),
            ("box_color", "black@0.5"),
            ("box_color", "white"),
            ("box_color", "#000000"),
        ],
    )
    def test_valid_color_is_accepted(self, field: str, valid_value: str) -> None:
        """Valid color values (named, #RRGGBB, name@alpha) must be accepted."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            opts = _default_opts(**{field: valid_value})
            result = add_text(str(inp), str(out), opts)

            assert result["ok"] is True, (
                f"Valid color {field}={valid_value!r} must be accepted, "
                f"got error: {result.get('error')}"
            )


# ===========================================================================
# AC-1-6  Normal annotation: marker added, name, metadata, marked_range
# ===========================================================================


class TestNormalAnnotation:
    """Successful add_text must append exactly one text_overlay marker."""

    def test_marker_added_to_v1_track(self) -> None:
        """One text_overlay marker must be added to the V1 track."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            opts = _default_opts(text="Hello", start_sec=1.0, duration_sec=3.0)
            result = add_text(str(inp), str(out), opts)

            assert result["ok"] is True, f"Expected ok=True, got: {result.get('error')}"
            out_tl = _read_timeline(out)
            markers = _get_text_overlay_markers(out_tl)
            assert len(markers) == 1, f"Expected 1 text_overlay marker, got {len(markers)}"

    def test_marker_name_is_text_0(self) -> None:
        """First marker name must be 'text_0'."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            opts = _default_opts()
            result = add_text(str(inp), str(out), opts)

            assert result["ok"] is True
            out_tl = _read_timeline(out)
            markers = _get_text_overlay_markers(out_tl)
            assert markers[0].name == "text_0", (
                f"Expected name='text_0', got {markers[0].name!r}"
            )

    def test_metadata_clipwright_fields(self) -> None:
        """marker.metadata['clipwright'] must contain tool/version/kind + all AddTextOptions fields."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            opts = _default_opts(
                text="Test",
                start_sec=2.0,
                duration_sec=4.0,
                x="(w-tw)/2",
                y="h-th-40",
                font_size=48,
                font_color="white",
                box=False,
                box_color="black@0.5",
                fade_in_sec=0.3,
                fade_out_sec=0.3,
                font_path=None,
            )
            result = add_text(str(inp), str(out), opts)

            assert result["ok"] is True
            out_tl = _read_timeline(out)
            markers = _get_text_overlay_markers(out_tl)
            assert len(markers) == 1
            cw = markers[0].metadata.get("clipwright", {})

            # Required system fields
            assert cw.get("tool") == "clipwright-text"
            assert cw.get("kind") == "text_overlay"
            assert "version" in cw, "version must be present in metadata"

            # All AddTextOptions fields must be stored
            assert cw.get("text") == "Test"
            assert cw.get("start_sec") == pytest.approx(2.0)
            assert cw.get("duration_sec") == pytest.approx(4.0)
            assert cw.get("x") == "(w-tw)/2"
            assert cw.get("y") == "h-th-40"
            assert cw.get("font_size") == 48
            assert cw.get("font_color") == "white"
            assert cw.get("box") is False
            assert cw.get("box_color") == "black@0.5"
            assert cw.get("fade_in_sec") == pytest.approx(0.3)
            assert cw.get("fade_out_sec") == pytest.approx(0.3)
            assert "font_path" in cw

    def test_marked_range_reflects_rate(self) -> None:
        """marked_range start/duration must use the timeline's rate (not a float)."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            rate = 24.0
            tl = _make_v1_timeline(rate=rate)
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            opts = _default_opts(start_sec=2.0, duration_sec=3.0)
            result = add_text(str(inp), str(out), opts)

            assert result["ok"] is True
            out_tl = _read_timeline(out)
            markers = _get_text_overlay_markers(out_tl)
            assert len(markers) == 1
            mr = markers[0].marked_range

            # Rate must match the timeline rate
            assert mr.start_time.rate == pytest.approx(rate)
            assert mr.duration.rate == pytest.approx(rate)

            # Values in seconds must match
            assert mr.start_time.to_seconds() == pytest.approx(2.0, abs=1e-4)
            assert mr.duration.to_seconds() == pytest.approx(3.0, abs=1e-4)

    def test_result_envelope_shape(self) -> None:
        """Success result must have ok/summary/data{applied,overlay_count}/artifacts."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            opts = _default_opts()
            result = add_text(str(inp), str(out), opts)

            assert result["ok"] is True
            assert result.get("summary"), "summary must be non-empty"

            data = result.get("data") or {}
            assert data.get("applied") == 1
            assert data.get("overlay_count") == 1

            artifacts = result.get("artifacts") or []
            assert len(artifacts) >= 1
            tl_artifact = next(
                (
                    a
                    for a in artifacts
                    if (
                        a.get("role")
                        if isinstance(a, dict)
                        else getattr(a, "role", None)
                    ) == "timeline"
                ),
                None,
            )
            assert tl_artifact is not None, "artifacts must contain a 'timeline' entry"


# ===========================================================================
# AC-1-7  Accumulation: two distinct calls -> text_0 / text_1
# ===========================================================================


class TestAccumulation:
    """Two distinct add_text calls must accumulate text_0 and text_1."""

    def test_two_distinct_overlays_accumulate(self) -> None:
        """Calling add_text twice with different text produces text_0 and text_1."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            mid = tmp / "mid.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            opts1 = _default_opts(text="First", start_sec=1.0, duration_sec=2.0)
            r1 = add_text(str(inp), str(mid), opts1)
            assert r1["ok"] is True, f"First call failed: {r1.get('error')}"

            opts2 = _default_opts(text="Second", start_sec=4.0, duration_sec=2.0)
            r2 = add_text(str(mid), str(out), opts2)
            assert r2["ok"] is True, f"Second call failed: {r2.get('error')}"

            out_tl = _read_timeline(out)
            markers = _get_text_overlay_markers(out_tl)
            assert len(markers) == 2, f"Expected 2 markers, got {len(markers)}"

            names = {m.name for m in markers}
            assert names == {"text_0", "text_1"}, (
                f"Expected names {{text_0, text_1}}, got {names}"
            )

            data = r2.get("data") or {}
            assert data.get("overlay_count") == 2


# ===========================================================================
# AC-1-8  Idempotent no-op: exact duplicate -> applied=0 + warning
# ===========================================================================


class TestIdempotentNoop:
    """Exact duplicate call must produce applied=0, warning, and no new marker."""

    def test_duplicate_call_returns_applied_zero(self) -> None:
        """Calling add_text twice with identical params must return applied=0."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            mid = tmp / "mid.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            opts = _default_opts(text="Same", start_sec=1.0, duration_sec=3.0)

            r1 = add_text(str(inp), str(mid), opts)
            assert r1["ok"] is True

            r2 = add_text(str(mid), str(out), opts)
            assert r2["ok"] is True, f"no-op call must still return ok=True: {r2.get('error')}"

            data = r2.get("data") or {}
            assert data.get("applied") == 0, (
                f"Duplicate call must return applied=0, got {data.get('applied')!r}"
            )

    def test_duplicate_call_emits_warning(self) -> None:
        """Duplicate call must include a non-empty warning."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            mid = tmp / "mid.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            opts = _default_opts(text="Same", start_sec=1.0, duration_sec=3.0)
            add_text(str(inp), str(mid), opts)
            r2 = add_text(str(mid), str(out), opts)

            warnings = r2.get("warnings") or []
            assert len(warnings) > 0, "Duplicate call must emit at least one warning"
            assert any(warnings), "warning entries must be non-empty strings"

    def test_duplicate_call_marker_count_unchanged(self) -> None:
        """After no-op, the output must have the same marker count as input."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            mid = tmp / "mid.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            opts = _default_opts(text="Same", start_sec=1.0, duration_sec=3.0)
            add_text(str(inp), str(mid), opts)

            mid_tl = _read_timeline(mid)
            count_before = len(_get_text_overlay_markers(mid_tl))

            add_text(str(mid), str(out), opts)
            out_tl = _read_timeline(out)
            count_after = len(_get_text_overlay_markers(out_tl))

            assert count_after == count_before, (
                f"No-op must not change marker count: "
                f"before={count_before}, after={count_after}"
            )


# ===========================================================================
# AC-1-9  Non-destructive: input OTIO must not be modified
# ===========================================================================


class TestNonDestructive:
    """Input OTIO file bytes and marker count must be unchanged after add_text."""

    def test_input_bytes_unchanged(self) -> None:
        """Input file bytes must be identical before and after add_text."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            before = inp.read_bytes()
            opts = _default_opts()
            add_text(str(inp), str(out), opts)
            after = inp.read_bytes()

            assert before == after, "Input OTIO file must not be modified by add_text"

    def test_input_marker_count_unchanged(self) -> None:
        """Input timeline marker count must not change after add_text."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            inp_tl_before = _read_timeline(inp)
            count_before = len(_get_text_overlay_markers(inp_tl_before))

            opts = _default_opts()
            add_text(str(inp), str(out), opts)

            inp_tl_after = _read_timeline(inp)
            count_after = len(_get_text_overlay_markers(inp_tl_after))

            assert count_before == count_after, (
                "Input timeline marker count must not change"
            )


# ===========================================================================
# AC-1-10  Boundary: output outside timeline directory -> PATH_NOT_ALLOWED
# ===========================================================================


class TestOutputBoundary:
    """Output outside the timeline directory must return PATH_NOT_ALLOWED."""

    def test_output_outside_timeline_dir_path_not_allowed(self) -> None:
        """Output in a sibling directory must return PATH_NOT_ALLOWED."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            proj = tmp / "proj"
            elsewhere = tmp / "elsewhere"
            proj.mkdir()
            elsewhere.mkdir()

            tl = _make_v1_timeline()
            inp = proj / "in.otio"
            out = elsewhere / "out.otio"
            _write_timeline(tl, inp)

            opts = _default_opts()
            result = add_text(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "PATH_NOT_ALLOWED", (
                f"Expected PATH_NOT_ALLOWED, got {error.get('code')!r}"
            )
            assert error.get("hint")


# ===========================================================================
# AC-1-11  Boundary: non-.otio output extension -> INVALID_INPUT
# ===========================================================================


class TestOutputExtension:
    """Non-.otio output extension must return INVALID_INPUT."""

    @pytest.mark.parametrize("bad_ext", [".mp4", ".json", ".txt", ""])
    def test_non_otio_output_returns_invalid_input(self, bad_ext: str) -> None:
        """Output path with non-.otio extension must return INVALID_INPUT."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / f"out{bad_ext}" if bad_ext else tmp / "out_no_ext"
            _write_timeline(tl, inp)

            opts = _default_opts()
            result = add_text(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT for extension {bad_ext!r}, "
                f"got {error.get('code')!r}"
            )
            assert error.get("hint")


# ===========================================================================
# AC-1-12  Boundary: output == timeline -> INVALID_INPUT
# ===========================================================================


class TestOutputEqualsTimeline:
    """output path identical to timeline path must return INVALID_INPUT."""

    def test_output_equals_timeline_returns_invalid_input(self) -> None:
        """Passing the same path for timeline and output must return INVALID_INPUT."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            _write_timeline(tl, inp)

            opts = _default_opts()
            result = add_text(str(inp), str(inp), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT when output==timeline, "
                f"got {error.get('code')!r}"
            )
            assert error.get("hint")


# ===========================================================================
# AC-1-13  Boundary: no video track -> UNSUPPORTED_OPERATION
# ===========================================================================


class TestNoVideoTrack:
    """Timeline with no video track must return UNSUPPORTED_OPERATION."""

    def test_audio_only_timeline_returns_unsupported(self) -> None:
        """Audio-only timeline (no V1) must return UNSUPPORTED_OPERATION."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_audio_only_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            opts = _default_opts()
            result = add_text(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "UNSUPPORTED_OPERATION", (
                f"Expected UNSUPPORTED_OPERATION for audio-only timeline, "
                f"got {error.get('code')!r}"
            )
            assert error.get("hint")
