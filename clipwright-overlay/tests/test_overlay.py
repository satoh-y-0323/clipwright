"""Tests for clipwright-overlay add_overlay() core logic.

All tests in this module verify the contract of add_overlay() as defined in:
  - architecture-report-20260622-013708.md §V2 (AUTHORITY, overrides v1)
  - v1 §1 ADR-OV-1 (module split / 2-layer boundary)
  - v1 §2 ADR-OV-2 (marker schema / validation order / idempotency / ErrorCode)
  - v1 §7 ADR-OV-7 (envelope)
  - V2-3 (path storage in marker via media_ref_for_otio)
  - V2-5 (x/y allowlist including single-quote prohibition)
  - V2-9 (scale (0,8] manual recheck + _MAX_IMAGE_OVERLAYS=64)
  - ADR-PP-1 (media_ref_for_otio: relative inside otio_dir, absolute outside)

Covered contracts:
  - 2-layer boundary: add_overlay never raises; _add_overlay_inner raises
    ClipwrightError which add_overlay converts via error_result
  - Happy path: valid co-located image -> ok ToolResult + marker image_0 +
    metadata["clipwright"] + RELATIVE image_path stored when inside otio_dir
  - Envelope data: {applied, overlay_count, start_sec, duration_sec};
    summary contains basename + duration + count + out.name;
    NO full path in summary/data (CWE-209); artifacts=[{role:"timeline",...}]
  - Validation order: value-range -> image_path 3-stage -> x/y control chars
  - Value ranges: start_sec<0, duration_sec<=0, opacity<0/1, fades<0,
    fade_in+fade_out>duration, scale<=0, scale>8.0 (V2-9 manual recheck)
  - image_path 3-stage (co-location removed — ADR-PP-1 / impl-overlay):
    path safety (INVALID_INPUT) -> existence (FILE_NOT_FOUND) ->
    extension allowlist (INVALID_INPUT).
    Image may be anywhere; media_ref_for_otio stores relative posix when
    inside the output OTIO's parent dir, absolute when outside.
  - x/y allowlist (V2-5): `:;[],'` or control chars -> INVALID_INPUT;
    "(W-w)/2", "(H-h)/2", "main_w-overlay_w-10" accepted
  - V1 track absence -> UNSUPPORTED_OPERATION
  - input==output -> INVALID_INPUT
  - Idempotency: identical options re-applied -> applied=0 + warning + no dup
    Comparison: image_path via media_ref_for_otio + x + y exact match;
    start/duration/scale/opacity/fades approx (<=1e-6)
  - Accumulate: two different-param calls -> image_0 and image_1
  - V2-9 cap: 65th image_overlay marker -> INVALID_INPUT
  - Rate resolution: first clip source_range.rate -> existing image_overlay
    marker rate -> fallback 1000.0 + warning

Note: This file does NOT import server.py (tested separately). No subprocess —
overlay.py is subprocess-free.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import opentimelineio as otio
import pytest
from _imgbytes import DUMMY_PNG_BYTES as _DUMMY_PNG_BYTES

from clipwright_overlay.overlay import add_overlay
from clipwright_overlay.schemas import AddOverlayOptions

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RATE = 24.0


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_clip(
    name: str, duration_sec: float = 10.0, rate: float = _RATE
) -> otio.schema.Clip:
    """Build a simple Clip with ExternalReference and source_range."""
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


def _make_no_clip_v1_timeline() -> otio.schema.Timeline:
    """Return a timeline with V1 video track but no clips (for rate fallback test)."""
    tl = otio.schema.Timeline(name="no_clip_tl")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    tl.tracks.append(v1)
    return tl


def _write_timeline(tl: otio.schema.Timeline, path: Path) -> None:
    otio.adapters.write_to_file(tl, str(path))


def _read_timeline(path: Path) -> otio.schema.Timeline:
    return otio.adapters.read_from_file(str(path))  # type: ignore[no-any-return]


def _get_image_overlay_markers(tl: otio.schema.Timeline) -> list[otio.schema.Marker]:
    """Return all image_overlay markers from the first Video track."""
    for track in tl.tracks:
        if track.kind == otio.schema.TrackKind.Video:
            return [
                m
                for m in track.markers
                if m.metadata.get("clipwright", {}).get("kind") == "image_overlay"
            ]
    return []


def _write_dummy_png(path: Path) -> None:
    """Write minimal valid PNG bytes to the given path."""
    path.write_bytes(_DUMMY_PNG_BYTES)


def _default_opts(tmp: Path, img: Path, **overrides: object) -> AddOverlayOptions:
    """Return AddOverlayOptions with valid defaults.

    fade_in_sec and fade_out_sec are intentionally pinned to 0.0 (rather than
    the schema defaults of 0.3) so that tests which do not specifically exercise
    fades are not accidentally constrained by the fade-sum <= duration_sec rule.
    Tests that need to verify fade behaviour pass explicit values via overrides.

    Args:
        tmp: Temporary directory used as output parent.
        img: Path to the co-located image file (must already exist).
        **overrides: Field overrides applied on top of the defaults above.
    """
    base: dict[str, object] = {
        "image_path": str(img),
        "start_sec": 1.0,
        "duration_sec": 3.0,
        "x": "(W-w)/2",
        "y": "(H-h)/2",
        "scale": 1.0,
        "opacity": 1.0,
        "fade_in_sec": 0.0,
        "fade_out_sec": 0.0,
    }
    base.update(overrides)
    return AddOverlayOptions(**base)  # type: ignore[arg-type]


# ===========================================================================
# 2-layer boundary
# ===========================================================================


class TestTwoLayerBoundary:
    """add_overlay must never raise; inner errors are converted to error_result."""

    def test_add_overlay_never_raises_on_error(self) -> None:
        """add_overlay converts ClipwrightError to error_result; never raises."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            # Trigger validation error: start_sec < 0
            opts = _default_opts(tmp, img, start_sec=-1.0)
            result = add_overlay(str(inp), str(out), opts)

            # Must return a dict (ToolResult), NOT raise
            assert isinstance(result, dict)
            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT"

    def test_add_overlay_returns_tool_result_type(self) -> None:
        """add_overlay must return a ToolResult dict on success."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img)
            result = add_overlay(str(inp), str(out), opts)

            assert isinstance(result, dict)
            assert "ok" in result


# ===========================================================================
# Happy path
# ===========================================================================


class TestHappyPath:
    """Valid co-located image -> ok ToolResult + image_0 marker + metadata."""

    def test_happy_path_ok_result(self) -> None:
        """Valid call returns ok=True."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img, start_sec=1.0, duration_sec=3.0)
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True, f"Expected ok=True, got: {result.get('error')}"

    def test_happy_path_marker_name_image_0(self) -> None:
        """First marker name must be 'image_0' on the V1 track."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img)
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True
            out_tl = _read_timeline(out)
            markers = _get_image_overlay_markers(out_tl)
            assert len(markers) == 1
            assert markers[0].name == "image_0", (
                f"Expected name='image_0', got {markers[0].name!r}"
            )

    def test_happy_path_metadata_fields(self) -> None:
        """marker.metadata['clipwright'] must contain all required fields."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(
                tmp,
                img,
                start_sec=2.0,
                duration_sec=4.0,
                x="(W-w)/2",
                y="(H-h)/2",
                scale=1.5,
                opacity=0.8,
                fade_in_sec=0.3,
                fade_out_sec=0.3,
            )
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True
            out_tl = _read_timeline(out)
            markers = _get_image_overlay_markers(out_tl)
            assert len(markers) == 1
            cw = markers[0].metadata.get("clipwright", {})

            assert cw.get("tool") == "clipwright-overlay"
            assert cw.get("kind") == "image_overlay"
            assert "version" in cw
            assert cw.get("start_sec") == pytest.approx(2.0)
            assert cw.get("duration_sec") == pytest.approx(4.0)
            assert cw.get("x") == "(W-w)/2"
            assert cw.get("y") == "(H-h)/2"
            assert cw.get("scale") == pytest.approx(1.5)
            assert cw.get("opacity") == pytest.approx(0.8)
            assert cw.get("fade_in_sec") == pytest.approx(0.3)
            assert cw.get("fade_out_sec") == pytest.approx(0.3)

    def test_happy_path_image_path_stored_as_relative(self) -> None:
        """image_path stored as RELATIVE posix when image is inside otio_dir.

        This exercises the 'inside otio_dir -> relative' branch of
        media_ref_for_otio (the storage rule applied after impl-overlay).
        The stored value must equal os.path.relpath(resolved, output_parent)
        converted to posix format. It must NOT be the absolute path.

        Note: when the image is OUTSIDE the output OTIO's parent directory,
        media_ref_for_otio returns an absolute path instead of a relative
        one — see test_pathpolicy_overlay.py::TestImageReferenceStorage for
        the 'outside -> absolute' branch.
        """
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img)
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True
            out_tl = _read_timeline(out)
            markers = _get_image_overlay_markers(out_tl)
            assert len(markers) == 1
            cw = markers[0].metadata.get("clipwright", {})

            stored_path = cw.get("image_path")
            assert stored_path is not None

            # Must be RELATIVE (no drive letter or leading slash)
            assert not Path(str(stored_path)).is_absolute(), (
                f"image_path must be relative, got absolute: {stored_path!r}"
            )

            # Must NOT contain full directory path
            assert str(tmp) not in str(stored_path), (
                f"image_path must not contain full dir path: {stored_path!r}"
            )

            # Must match the expected relative posix path
            output_parent = out.resolve().parent
            resolved_img = img.resolve()
            expected_rel = Path(os.path.relpath(resolved_img, output_parent)).as_posix()
            assert stored_path == expected_rel, (
                f"Expected relative posix path {expected_rel!r}, got {stored_path!r}"
            )


# ===========================================================================
# Envelope data
# ===========================================================================


class TestEnvelopeData:
    """Result envelope must conform to the contract (ADR-OV-7 / CWE-209)."""

    def test_envelope_data_fields(self) -> None:
        """data must contain applied, overlay_count, start_sec, duration_sec."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img, start_sec=1.0, duration_sec=3.0)
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True
            data = result.get("data") or {}
            assert data.get("applied") == 1
            assert data.get("overlay_count") == 1
            assert data.get("start_sec") == pytest.approx(1.0)
            assert data.get("duration_sec") == pytest.approx(3.0)

    def test_envelope_summary_contains_basename_and_count(self) -> None:
        """summary must contain image basename, duration, count, and out.name."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img, start_sec=1.0, duration_sec=3.0)
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True
            summary = result.get("summary") or ""
            assert "logo.png" in summary, (
                f"summary must contain basename, got: {summary!r}"
            )
            assert "3" in summary or "3.0" in summary, (
                f"summary must contain duration, got: {summary!r}"
            )
            assert "1" in summary, f"summary must contain count, got: {summary!r}"
            assert "out.otio" in summary, (
                f"summary must contain out.name, got: {summary!r}"
            )

    def test_envelope_summary_no_full_path(self) -> None:
        """summary must NOT contain the full directory path (CWE-209)."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img)
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True
            summary = result.get("summary") or ""
            # The full absolute dir path must not appear
            assert str(tmp) not in summary, (
                f"summary must not contain full dir path: {summary!r}"
            )

    def test_envelope_data_no_full_path(self) -> None:
        """data must NOT contain the full directory path (CWE-209)."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img)
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True
            import json

            data_str = json.dumps(result.get("data") or {})
            assert str(tmp) not in data_str, (
                f"data must not contain full dir path: {data_str!r}"
            )

    def test_envelope_artifacts_timeline_entry(self) -> None:
        """artifacts must contain one entry with role='timeline' and format='otio'."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img)
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True
            artifacts = result.get("artifacts") or []
            tl_artifact = next(
                (
                    a
                    for a in artifacts
                    if (
                        a.get("role")
                        if isinstance(a, dict)
                        else getattr(a, "role", None)
                    )
                    == "timeline"
                ),
                None,
            )
            assert tl_artifact is not None, "artifacts must contain 'timeline' entry"
            artifact_path = (
                tl_artifact.get("path")
                if isinstance(tl_artifact, dict)
                else getattr(tl_artifact, "path", None)
            )
            artifact_format = (
                tl_artifact.get("format")
                if isinstance(tl_artifact, dict)
                else getattr(tl_artifact, "format", None)
            )
            assert artifact_format == "otio"
            # path must be the resolved absolute path
            assert str(out.resolve()) == str(artifact_path)


# ===========================================================================
# Validation order
# ===========================================================================


class TestValidationOrder:
    """Validation order: value-range -> image_path 4-stage -> x/y control chars."""

    def test_value_range_checked_before_image_path(self) -> None:
        """Value-range violation must be caught before image_path validation."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            # Use a non-existent image path — would be FILE_NOT_FOUND if reached
            nonexistent_img = tmp / "nonexistent.png"

            opts = _default_opts(tmp, nonexistent_img, start_sec=-1.0)
            result = add_overlay(str(inp), str(out), opts)

            # Must get INVALID_INPUT from value-range, not FILE_NOT_FOUND
            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT (value-range), got {error.get('code')!r}"
            )

    def test_path_safety_checked_before_existence(self) -> None:
        """Path safety must fire before existence check.

        Co-location restriction removed (ADR-PP-1 / impl-overlay).
        New order: path safety -> existence -> extension.
        An image path with a single-quote (path safety violation) that does
        NOT exist should yield INVALID_INPUT (safety), not FILE_NOT_FOUND.
        """
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            # Non-existent path with single-quote (path safety violation)
            bad_path = str(tmp / "non_existent'.png")

            opts = AddOverlayOptions(
                image_path=bad_path,
                start_sec=1.0,
                duration_sec=3.0,
            )
            result = add_overlay(str(inp), str(out), opts)

            # Must get INVALID_INPUT from path safety, not FILE_NOT_FOUND
            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT (path safety before existence), "
                f"got {error.get('code')!r}"
            )

    def test_existence_checked_before_extension(self) -> None:
        """Existence check must fire before extension check."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            # Non-existent file with bad extension — existence must fire first
            nonexistent_gif = tmp / "nonexistent.gif"

            opts = _default_opts(tmp, nonexistent_gif)
            result = add_overlay(str(inp), str(out), opts)

            # Must be FILE_NOT_FOUND, not INVALID_INPUT from extension
            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "FILE_NOT_FOUND", (
                f"Expected FILE_NOT_FOUND (existence before extension), "
                f"got {error.get('code')!r}"
            )

    def test_extension_checked_before_path_safety(self) -> None:
        """Extension check must fire before path safety check."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            # Write a .gif file (bad extension) — no single-quote but bad ext
            bad_ext_file = tmp / "image.gif"
            bad_ext_file.write_bytes(b"GIF89a")

            opts = _default_opts(tmp, bad_ext_file)
            result = add_overlay(str(inp), str(out), opts)

            # Must be INVALID_INPUT from extension, not path safety
            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT (extension check), got {error.get('code')!r}"
            )


# ===========================================================================
# Value range violations
# ===========================================================================


class TestValueRangeViolations:
    """Out-of-range fields must return INVALID_INPUT with a non-empty hint."""

    @pytest.mark.parametrize(
        "field,value",
        [
            ("start_sec", -0.001),
            ("start_sec", -100.0),
            ("duration_sec", 0.0),
            ("duration_sec", -1.0),
            ("opacity", -0.001),
            ("opacity", 1.001),
            ("fade_in_sec", -0.001),
            ("fade_out_sec", -0.001),
        ],
    )
    def test_invalid_range_returns_invalid_input(
        self, field: str, value: object
    ) -> None:
        """Out-of-range field must return INVALID_INPUT with non-empty hint."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img, **{field: value})
            result = add_overlay(str(inp), str(out), opts)

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
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(
                tmp, img, duration_sec=2.0, fade_in_sec=1.5, fade_out_sec=1.5
            )
            result = add_overlay(str(inp), str(out), opts)

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
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(
                tmp, img, duration_sec=2.0, fade_in_sec=1.0, fade_out_sec=1.0
            )
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True, (
                f"fade_in+fade_out==duration must be OK, got: {result.get('error')}"
            )

    def test_scale_zero_returns_invalid_input(self) -> None:
        """scale=0 must return INVALID_INPUT via defense-in-depth recheck (V2-9).

        Uses model_construct to bypass schema Field(gt=0) so the manual recheck
        in overlay.py _validate_overlay_fields is exercised (scale check fires
        before image_path validation, so no real image file is required).
        """
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            # Bypass Pydantic Field(gt=0) to reach overlay.py manual recheck
            opts = AddOverlayOptions.model_construct(
                image_path=str(tmp / "logo.png"),
                start_sec=1.0,
                duration_sec=3.0,
                x="(W-w)/2",
                y="(H-h)/2",
                scale=0.0,
                opacity=1.0,
                fade_in_sec=0.0,
                fade_out_sec=0.0,
            )
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT for scale=0.0, got {error.get('code')!r}"
            )
            assert error.get("hint"), "hint must be non-empty for scale=0"

    def test_scale_negative_returns_invalid_input(self) -> None:
        """scale<0 must return INVALID_INPUT via defense-in-depth recheck (V2-9).

        Uses model_construct to bypass schema Field(gt=0) so the manual recheck
        in overlay.py _validate_overlay_fields is exercised.
        """
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            # Bypass Pydantic Field(gt=0) to reach overlay.py manual recheck
            opts = AddOverlayOptions.model_construct(
                image_path=str(tmp / "logo.png"),
                start_sec=1.0,
                duration_sec=3.0,
                x="(W-w)/2",
                y="(H-h)/2",
                scale=-0.5,
                opacity=1.0,
                fade_in_sec=0.0,
                fade_out_sec=0.0,
            )
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT for scale=-0.5, got {error.get('code')!r}"
            )
            assert error.get("hint"), "hint must be non-empty for scale<0"

    def test_scale_exceeds_8_returns_invalid_input(self) -> None:
        """scale>8.0 must return INVALID_INPUT with precise hint (V2-9 manual recheck).

        Uses model_construct to bypass schema Field(le=8.0) so the manual recheck
        in overlay.py _validate_overlay_fields is exercised.
        """
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            # Bypass Pydantic Field(le=8.0) to reach overlay.py manual recheck
            opts = AddOverlayOptions.model_construct(
                image_path=str(tmp / "logo.png"),
                start_sec=1.0,
                duration_sec=3.0,
                x="(W-w)/2",
                y="(H-h)/2",
                scale=8.001,
                opacity=1.0,
                fade_in_sec=0.0,
                fade_out_sec=0.0,
            )
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT for scale=8.001, got {error.get('code')!r}"
            )
            # V2-9: manual recheck owns the precise hint mentioning 8.0
            assert error.get("hint"), "hint must be non-empty for scale>8.0"

    def test_scale_exactly_8_is_valid(self) -> None:
        """scale == 8.0 must be accepted (upper boundary of (0, 8])."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img, scale=8.0)
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True, (
                f"scale=8.0 must be valid, got: {result.get('error')}"
            )


# ===========================================================================
# image_path 4-stage validation
# ===========================================================================


class TestImagePathValidation:
    """image_path must pass all 3 stages: path safety, existence, extension.

    Co-location restriction removed (ADR-PP-1 / impl-overlay): images may be
    placed anywhere relative to the output OTIO directory.
    """

    def test_image_outside_output_parent_is_accepted(self) -> None:
        """Image outside output parent tree must now be accepted (ADR-PP-1).

        Co-location restriction removed: image outside the output OTIO's parent
        directory is allowed and stored as an absolute path via media_ref_for_otio.
        """
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            proj = tmp / "proj"
            elsewhere = tmp / "elsewhere"
            proj.mkdir()
            elsewhere.mkdir()

            tl = _make_v1_timeline()
            inp = proj / "in.otio"
            out = proj / "out.otio"
            _write_timeline(tl, inp)
            # Image exists but is outside output parent dir -> now allowed
            outside_img = elsewhere / "logo.png"
            _write_dummy_png(outside_img)

            opts = _default_opts(proj, outside_img)
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True, (
                f"Image outside output parent must now be accepted (ADR-PP-1), "
                f"got error: {result.get('error')}"
            )

    def test_colocation_recursive_subdir_allowed(self) -> None:
        """Image in recursive subdir under output parent must be allowed."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            subdir = tmp / "images" / "logos"
            subdir.mkdir(parents=True)

            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = subdir / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img)
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True, (
                f"Recursive subdir image must be allowed, got: {result.get('error')}"
            )

    def test_existence_nonexistent_file_not_found(self) -> None:
        """Non-existent image within output parent -> FILE_NOT_FOUND with basename only."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            nonexistent = tmp / "missing_logo.png"

            opts = _default_opts(tmp, nonexistent)
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "FILE_NOT_FOUND", (
                f"Expected FILE_NOT_FOUND, got {error.get('code')!r}"
            )
            # CWE-209: message must contain basename ONLY
            msg = error.get("message") or ""
            assert "missing_logo.png" in msg, f"message must contain basename: {msg!r}"
            assert str(tmp) not in msg, (
                f"message must NOT contain directory path: {msg!r}"
            )

    @pytest.mark.parametrize("ext", [".gif", ".bmp"])
    def test_extension_not_in_allowlist_invalid_input(self, ext: str) -> None:
        """Extensions not in allowlist (.gif, .bmp) -> INVALID_INPUT."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            bad_file = tmp / f"image{ext}"
            bad_file.write_bytes(b"FAKE_IMAGE_BYTES")

            opts = _default_opts(tmp, bad_file)
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT for {ext}, got {error.get('code')!r}"
            )
            assert error.get("hint")

    @pytest.mark.parametrize("ext", [".png", ".jpg", ".jpeg", ".webp"])
    def test_extension_in_allowlist_accepted(self, ext: str) -> None:
        """Extensions in allowlist (.png, .jpg, .jpeg, .webp) must be accepted."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            good_file = tmp / f"image{ext}"
            _write_dummy_png(good_file)

            opts = _default_opts(tmp, good_file)
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True, (
                f"Extension {ext} must be allowed, got: {result.get('error')}"
            )

    def test_extension_case_insensitive(self) -> None:
        """Extension check must be case-insensitive (e.g., .PNG accepted)."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            upper_ext = tmp / "IMAGE.PNG"
            _write_dummy_png(upper_ext)

            opts = _default_opts(tmp, upper_ext)
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True, (
                f".PNG (uppercase) must be accepted, got: {result.get('error')}"
            )

    def test_path_safety_single_quote_invalid_input(self) -> None:
        """image_path containing single-quote -> INVALID_INPUT."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            # Pass a string with a single quote in the path
            bad_path = str(tmp / "logo'.png")
            opts = AddOverlayOptions(
                image_path=bad_path,
                start_sec=1.0,
                duration_sec=3.0,
            )
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT for single-quote in image_path, "
                f"got {error.get('code')!r}"
            )
            assert error.get("hint")

    def test_path_safety_control_char_invalid_input(self) -> None:
        """image_path containing control char -> INVALID_INPUT."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            bad_path = str(tmp) + "/logo\x00.png"
            opts = AddOverlayOptions(
                image_path=bad_path,
                start_sec=1.0,
                duration_sec=3.0,
            )
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT for control char in image_path, "
                f"got {error.get('code')!r}"
            )

    def test_sibling_dir_image_accepted_and_stored_as_absolute(self) -> None:
        """Image in sibling dir must be accepted and stored as absolute (ADR-PP-1).

        Co-location restriction removed: sibling-dir images that would previously
        yield a '..' relative path are now allowed. media_ref_for_otio stores the
        absolute path instead of a '../'-prefixed relative path (no traversal stored).
        """
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            proj = tmp / "proj"
            sibling = tmp / "sibling"
            proj.mkdir()
            sibling.mkdir()

            tl = _make_v1_timeline()
            inp = proj / "in.otio"
            out = proj / "out.otio"
            _write_timeline(tl, inp)
            # Image is in a sibling dir (outside proj/)
            sibling_img = sibling / "logo.png"
            _write_dummy_png(sibling_img)

            opts = _default_opts(proj, sibling_img)
            result = add_overlay(str(inp), str(out), opts)

            # Sibling dir image must now be accepted (ADR-PP-1)
            assert result["ok"] is True, (
                f"Sibling dir image must be accepted (ADR-PP-1), "
                f"got error: {result.get('error')}"
            )


# ===========================================================================
# x/y allowlist (V2-5)
# ===========================================================================


class TestXYAllowlist:
    """x/y allowlist: forbids `: ; [ ] , '` and control chars."""

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("x", "(W-w):2"),  # colon
            ("x", "(W;w)/2"),  # semicolon
            ("x", "[W-w]/2"),  # open bracket
            ("x", "(W-w])/2"),  # close bracket
            ("x", "(W-w)/2,0"),  # comma
            ("x", "(W-w)'/2"),  # single-quote
            ("x", "(W-w)\x00/2"),  # NUL control char
            ("x", "(W-w)\x1f/2"),  # control char US
            ("x", "(W-w)/2\x7f"),  # DEL
            ("y", "(H-h):2"),  # colon in y
            ("y", "(H-h)'/2"),  # single-quote in y
            ("y", "(H-h)\n/2"),  # newline in y
        ],
    )
    def test_invalid_xy_returns_invalid_input(self, field: str, bad_value: str) -> None:
        """x/y with forbidden chars must return INVALID_INPUT."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img, **{field: bad_value})
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT for {field}={bad_value!r}, "
                f"got {error.get('code')!r}"
            )
            assert error.get("hint")

    @pytest.mark.parametrize(
        "field,good_value",
        [
            ("x", "(W-w)/2"),
            ("x", "(H-h)/2"),
            ("x", "main_w-overlay_w-10"),
            ("x", "0"),
            ("x", "100"),
            ("y", "(H-h)/2"),
            ("y", "(W-w)/2"),
            ("y", "main_h-overlay_h-10"),
            ("y", "0"),
        ],
    )
    def test_valid_xy_accepted(self, field: str, good_value: str) -> None:
        """Valid x/y expressions must be accepted."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img, **{field: good_value})
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True, (
                f"Valid {field}={good_value!r} must be accepted, "
                f"got error: {result.get('error')}"
            )


# ===========================================================================
# V1 track absence
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
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img)
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "UNSUPPORTED_OPERATION", (
                f"Expected UNSUPPORTED_OPERATION for audio-only timeline, "
                f"got {error.get('code')!r}"
            )
            assert error.get("hint")


# ===========================================================================
# input == output
# ===========================================================================


class TestInputEqualsOutput:
    """output path identical to timeline path must return PATH_NOT_ALLOWED."""

    def test_output_equals_input_returns_invalid_input(self) -> None:
        """Same path for timeline and output must return PATH_NOT_ALLOWED (check_output_not_source)."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img)
            result = add_overlay(str(inp), str(inp), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            # check_output_not_source raises PATH_NOT_ALLOWED (CR-M-5 unified code)
            assert error.get("code") == "PATH_NOT_ALLOWED", (
                f"Expected PATH_NOT_ALLOWED when output==timeline, "
                f"got {error.get('code')!r}"
            )
            assert error.get("hint")


# ===========================================================================
# Idempotency
# ===========================================================================


class TestIdempotency:
    """Identical options re-applied -> applied=0 + warning + no duplicate marker."""

    def test_idempotent_call_returns_applied_zero(self) -> None:
        """Identical re-application must return applied=0."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            mid = tmp / "mid.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img, start_sec=1.0, duration_sec=3.0)
            r1 = add_overlay(str(inp), str(mid), opts)
            assert r1["ok"] is True, f"First call failed: {r1.get('error')}"

            # Second call with identical params (using mid as input, out as output)
            opts2 = _default_opts(tmp, img, start_sec=1.0, duration_sec=3.0)
            r2 = add_overlay(str(mid), str(out), opts2)

            assert r2["ok"] is True, (
                f"Idempotent call must still return ok=True: {r2.get('error')}"
            )
            data = r2.get("data") or {}
            assert data.get("applied") == 0, (
                f"Duplicate call must return applied=0, got {data.get('applied')!r}"
            )

    def test_idempotent_call_emits_warning(self) -> None:
        """Duplicate call must include a non-empty warning message."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            mid = tmp / "mid.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img)
            add_overlay(str(inp), str(mid), opts)
            r2 = add_overlay(str(mid), str(out), opts)

            warnings = r2.get("warnings") or []
            assert len(warnings) > 0, "Duplicate call must emit at least one warning"
            assert any(w for w in warnings), "warning entries must be non-empty"

    def test_idempotent_call_no_duplicate_marker(self) -> None:
        """After idempotent no-op, output must have same marker count as input."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            mid = tmp / "mid.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img)
            add_overlay(str(inp), str(mid), opts)

            mid_tl = _read_timeline(mid)
            count_before = len(_get_image_overlay_markers(mid_tl))

            add_overlay(str(mid), str(out), opts)
            out_tl = _read_timeline(out)
            count_after = len(_get_image_overlay_markers(out_tl))

            assert count_after == count_before, (
                f"No-op must not change marker count: "
                f"before={count_before}, after={count_after}"
            )

    def test_idempotent_call_output_timeline_written(self) -> None:
        """Output timeline file must be written even for no-op (idempotency)."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            mid = tmp / "mid.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img)
            add_overlay(str(inp), str(mid), opts)
            r2 = add_overlay(str(mid), str(out), opts)

            assert r2["ok"] is True
            assert out.exists(), "Output timeline must be written even for no-op"

    def test_idempotency_comparison_uses_relative_path(self) -> None:
        """Idempotency comparison must use the stored relative path (V2-3).

        Two calls with the same image (same absolute path) must be detected
        as duplicate based on the relative path string comparison.
        """
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            mid = tmp / "mid.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img, x="(W-w)/2", y="(H-h)/2")
            r1 = add_overlay(str(inp), str(mid), opts)
            assert r1["ok"] is True

            # Same image path, same x/y, same timings -> must be duplicate
            r2 = add_overlay(str(mid), str(out), opts)
            assert r2["ok"] is True
            data = r2.get("data") or {}
            assert data.get("applied") == 0, (
                "Same image+x+y+timings must be detected as duplicate"
            )


# ===========================================================================
# Accumulation
# ===========================================================================


class TestAccumulation:
    """Two different-param calls must accumulate image_0 and image_1."""

    def test_two_distinct_overlays_accumulate(self) -> None:
        """Two calls with different images produce image_0 and image_1."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            mid = tmp / "mid.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img1 = tmp / "logo1.png"
            img2 = tmp / "logo2.png"
            _write_dummy_png(img1)
            _write_dummy_png(img2)

            opts1 = _default_opts(tmp, img1, start_sec=1.0, duration_sec=2.0)
            r1 = add_overlay(str(inp), str(mid), opts1)
            assert r1["ok"] is True, f"First call failed: {r1.get('error')}"

            opts2 = _default_opts(tmp, img2, start_sec=4.0, duration_sec=2.0)
            r2 = add_overlay(str(mid), str(out), opts2)
            assert r2["ok"] is True, f"Second call failed: {r2.get('error')}"

            out_tl = _read_timeline(out)
            markers = _get_image_overlay_markers(out_tl)
            assert len(markers) == 2, f"Expected 2 markers, got {len(markers)}"

            names = {m.name for m in markers}
            assert names == {"image_0", "image_1"}, (
                f"Expected {{image_0, image_1}}, got {names}"
            )

            data = r2.get("data") or {}
            assert data.get("overlay_count") == 2


# ===========================================================================
# V2-9 cap: _MAX_IMAGE_OVERLAYS=64
# ===========================================================================


class TestMaxOverlaysCap:
    """Adding the 65th image_overlay marker must return INVALID_INPUT."""

    def test_65th_overlay_returns_invalid_input(self) -> None:
        """When 64 image_overlay markers are already present, adding one more must fail."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            # Build a timeline with 64 existing image_overlay markers directly
            tl = _make_v1_timeline()
            v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
            for n in range(64):
                marker = otio.schema.Marker(
                    name=f"image_{n}",
                    marked_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(float(n), _RATE),
                        duration=otio.opentime.RationalTime(_RATE, _RATE),
                    ),
                    metadata={
                        "clipwright": {
                            "tool": "clipwright-overlay",
                            "version": "0.1.0",
                            "kind": "image_overlay",
                            "image_path": "logo.png",
                            "start_sec": float(n),
                            "duration_sec": 1.0,
                            "x": "(W-w)/2",
                            "y": "(H-h)/2",
                            "scale": 1.0,
                            "opacity": 1.0,
                            "fade_in_sec": 0.0,
                            "fade_out_sec": 0.0,
                        }
                    },
                )
                v1.markers.append(marker)

            inp = tmp / "in_64.otio"
            out = tmp / "out_65.otio"
            _write_timeline(tl, inp)

            opts = _default_opts(tmp, img, start_sec=1.0, duration_sec=1.0)
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT when adding 65th overlay, "
                f"got {error.get('code')!r}"
            )
            assert error.get("hint"), "hint must be non-empty for cap violation"


# ===========================================================================
# Rate resolution
# ===========================================================================


class TestRateResolution:
    """Rate resolution: clip -> existing image_overlay marker -> fallback 1000.0+warning."""

    def test_rate_from_first_clip_source_range(self) -> None:
        """Rate must come from the first clip's source_range when available."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            rate = 30.0
            tl = _make_v1_timeline(rate=rate)
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img, start_sec=1.0, duration_sec=2.0)
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True
            out_tl = _read_timeline(out)
            markers = _get_image_overlay_markers(out_tl)
            assert len(markers) == 1
            mr = markers[0].marked_range
            assert mr.start_time.rate == pytest.approx(rate), (
                f"Expected rate={rate}, got {mr.start_time.rate}"
            )

    def test_rate_fallback_with_warning_when_no_clips(self) -> None:
        """When no clips and no existing image_overlay markers, fallback rate 1000.0
        must be used and a warning must be returned."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd)
            # Timeline with V1 track but no clips
            tl = _make_no_clip_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            img = tmp / "logo.png"
            _write_dummy_png(img)

            opts = _default_opts(tmp, img, start_sec=1.0, duration_sec=2.0)
            result = add_overlay(str(inp), str(out), opts)

            assert result["ok"] is True
            out_tl = _read_timeline(out)
            markers = _get_image_overlay_markers(out_tl)
            assert len(markers) == 1
            mr = markers[0].marked_range
            assert mr.start_time.rate == pytest.approx(1000.0), (
                f"Expected fallback rate=1000.0, got {mr.start_time.rate}"
            )

            # Warning must be present
            warnings = result.get("warnings") or []
            assert len(warnings) > 0, "Fallback rate must emit at least one warning"
