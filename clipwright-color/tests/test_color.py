"""test_color.py — Tests for clipwright_color.color (detect_color orchestration).

Mock policy:
  - Patch clipwright_color.color.inspect_media to inject stream presence/absence.
  - Patch clipwright_color.color.measure_brightness to control measured output.
  - No real ffmpeg/ffprobe binary is invoked. OTIO timeline I/O uses tmp_path.

Verification points:
  (1) happy path: directive written to metadata["clipwright"]["color"] with correct brightness
  (2) measured=None -> directive NOT written + warning; timeline still saved (U-1)
  (3) output validation errors (non-.otio, missing parent, output==media,
      output==timeline, output not same dir as media)
  (4) no video stream -> UNSUPPORTED_OPERATION
  (5) audio absent is fine (no error)
  (6) brightness clamp at extremes (target=255,yavg=0 -> +1.0; target=0,yavg=255 -> -1.0)
  (7) summary contains measured_luma/target_luma/brightness

Requirements: FR-2 (processing), FR-5 (metadata key), architecture-report §5.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest
from clipwright.errors import ErrorCode
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_color.schemas import (
    DetectColorOptions,  # type: ignore[import-not-found]
)

# ===========================================================================
# Helpers
# ===========================================================================

FPS = 30.0
_TEST_BIT_RATE = 8_000_000


def _make_media_info(
    path: str,
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
    has_video: bool = True,
    has_audio: bool = True,
) -> MediaInfo:
    """Construct a MediaInfo for testing."""
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(StreamInfo(index=0, codec_type="video", codec_name="h264"))
    if has_audio:
        streams.append(
            StreamInfo(index=len(streams), codec_type="audio", codec_name="aac")
        )
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=duration_sec * rate, rate=rate),
        streams=streams,
        bit_rate=_TEST_BIT_RATE,
    )


def _fake_measured(
    yavg: float = 96.4,
    ymin: float = 9.0,
    ymax: float = 242.0,
    sampled_frames: int = 12,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Return a fake measure_brightness result dict."""
    return {
        "measured": {
            "yavg": yavg,
            "ymin": ymin,
            "ymax": ymax,
            "sampled_frames": sampled_frames,
        },
        "warnings": warnings or [],
    }


_FAKE_MEASURED_NONE: dict[str, Any] = {
    "measured": None,
    "warnings": [
        "Could not retrieve signalstats YAVG values. color directive will not be written (U-1)."
    ],
}


# ===========================================================================
# (1) Happy path: directive written with correct derived brightness
# ===========================================================================


class TestHappyPath:
    """detect_color writes ColorDirective to metadata['clipwright']['color'] (FR-5)."""

    def test_directive_written_to_otio_metadata(self, tmp_path: Path) -> None:
        """Color directive must be stored at metadata['clipwright']['color']."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(target_luma=128.0)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=96.4),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        assert output.exists()
        tl = otio.adapters.read_from_file(str(output))
        color_meta = tl.metadata.get("clipwright", {}).get("color")
        assert color_meta is not None, (
            "metadata['clipwright']['color'] must be written on success."
        )

    def test_directive_kind_is_color(self, tmp_path: Path) -> None:
        """Written directive must have kind='color'."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(target_luma=128.0)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=96.4),
            )
            detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        tl = otio.adapters.read_from_file(str(output))
        color_meta = tl.metadata["clipwright"]["color"]
        assert color_meta["kind"] == "color"

    def test_derived_brightness_in_directive(self, tmp_path: Path) -> None:
        """brightness = clamp((target_luma - yavg) / 255.0, -1, 1) must be correct."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        # target=178, yavg=128 -> (178-128)/255 = 50/255 ≈ 0.196
        opts = DetectColorOptions(target_luma=178.0)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=128.0),
            )
            detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        tl = otio.adapters.read_from_file(str(output))
        color_meta = tl.metadata["clipwright"]["color"]
        eq = color_meta["eq"]
        expected_brightness = (178.0 - 128.0) / 255.0
        assert eq["brightness"] == pytest.approx(expected_brightness, abs=0.001)


# ===========================================================================
# (2) measured=None -> directive NOT written + warning; timeline still saved (U-1)
# ===========================================================================


class TestMeasuredNone:
    """When measure_brightness returns None, directive is skipped but timeline is saved (U-1)."""

    def test_directive_not_written_when_measured_none(self, tmp_path: Path) -> None:
        """When measured=None, 'color' key must NOT appear in metadata (U-1)."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _FAKE_MEASURED_NONE,
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        tl = otio.adapters.read_from_file(str(output))
        color_meta = tl.metadata.get("clipwright", {}).get("color")
        assert color_meta is None, (
            "U-1: 'color' directive must not be written when measured=None."
        )

    def test_timeline_saved_when_measured_none(self, tmp_path: Path) -> None:
        """Timeline file must still be written even when measured=None (U-1)."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _FAKE_MEASURED_NONE,
            )
            detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert output.exists(), "Timeline must be saved even when measured=None."

    def test_warning_emitted_when_measured_none(self, tmp_path: Path) -> None:
        """A warning must be returned in the result when measured=None (U-1)."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _FAKE_MEASURED_NONE,
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert len(result.get("warnings", [])) > 0, (
            "U-1: a warning must be returned when measured=None."
        )


# ===========================================================================
# (3) Output validation errors
# ===========================================================================


class TestOutputValidation:
    """Output path validation (architecture-report §5, step 1)."""

    def test_non_otio_extension_rejected(self, tmp_path: Path) -> None:
        """output with non-.otio extension must return INVALID_INPUT."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.json"
        opts = DetectColorOptions()

        result = detect_color(
            media=str(media), output=str(output), options=opts, timeline=None
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value

    def test_missing_parent_directory_rejected(self, tmp_path: Path) -> None:
        """output whose parent directory does not exist must return INVALID_INPUT."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "nonexistent" / "out.otio"
        opts = DetectColorOptions()

        result = detect_color(
            media=str(media), output=str(output), options=opts, timeline=None
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value

    def test_output_equals_media_rejected(self, tmp_path: Path) -> None:
        """output == media (same path) must return PATH_NOT_ALLOWED."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        # media must have .otio extension to avoid triggering the extension check first
        media = tmp_path / "video.otio"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()

        result = detect_color(
            media=str(media), output=str(media), options=opts, timeline=None
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED.value

    def test_output_equals_timeline_rejected(self, tmp_path: Path) -> None:
        """output == timeline (same path) must return PATH_NOT_ALLOWED."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        timeline_file = tmp_path / "existing.otio"
        timeline_file.write_bytes(b"dummy otio")
        opts = DetectColorOptions()

        result = detect_color(
            media=str(media),
            output=str(timeline_file),
            options=opts,
            timeline=str(timeline_file),
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED.value

    def test_output_in_different_directory_now_allowed(self, tmp_path: Path) -> None:
        """DC-AS-004: output in a different directory from media must now return ok=True.

        Updated from old policy (INVALID_INPUT) to new policy (ok=True).
        Previously: pre-migration code returned INVALID_INPUT (same-dir block L117-131).
        """
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media_dir = tmp_path / "media_dir"
        media_dir.mkdir()
        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")
        other_dir = tmp_path / "other_dir"
        other_dir.mkdir()
        output = other_dir / "out.otio"
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=100.0),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )
        assert result["ok"] is True, (
            "DC-AS-004: output in different dir from media must be allowed."
            f" error={result.get('error')}"
        )


# ===========================================================================
# (4) No video stream -> UNSUPPORTED_OPERATION
# ===========================================================================


class TestVideoRequired:
    """Video stream is required; audio is not (FR-2, architecture-report §5 step 2)."""

    def test_no_video_stream_returns_unsupported(self, tmp_path: Path) -> None:
        """When inspect_media returns no video stream, UNSUPPORTED_OPERATION must be returned."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "audio_only.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p), has_video=False, has_audio=True),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.UNSUPPORTED_OPERATION.value


# ===========================================================================
# (5) Audio absent is fine (no error)
# ===========================================================================


class TestAudioNotRequired:
    """Audio stream absence must not cause an error (FR-2 Constraint)."""

    def test_no_audio_stream_is_accepted(self, tmp_path: Path) -> None:
        """When audio is absent but video is present, detect_color must succeed."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video_only.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p), has_video=True, has_audio=False),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=100.0),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True, (
            "Audio absence must not be an error (color requires video only)."
        )


# ===========================================================================
# (6) Brightness clamp at extremes
# ===========================================================================


class TestBrightnessClamp:
    """brightness = clamp((target - yavg) / 255, -1, 1) boundary values (Constraint)."""

    def test_clamp_upper_bound(self, tmp_path: Path) -> None:
        """target=255, yavg=0 -> brightness must clamp to +1.0."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(target_luma=255.0)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=0.0),
            )
            detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        tl = otio.adapters.read_from_file(str(output))
        eq = tl.metadata["clipwright"]["color"]["eq"]
        assert eq["brightness"] == pytest.approx(1.0, abs=0.001), (
            "brightness must be clamped to +1.0 when (255-0)/255 > 1."
        )

    def test_clamp_lower_bound(self, tmp_path: Path) -> None:
        """target=0, yavg=255 -> brightness must clamp to -1.0."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(target_luma=0.0)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=255.0),
            )
            detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        tl = otio.adapters.read_from_file(str(output))
        eq = tl.metadata["clipwright"]["color"]["eq"]
        assert eq["brightness"] == pytest.approx(-1.0, abs=0.001), (
            "brightness must be clamped to -1.0 when (0-255)/255 < -1."
        )


# ===========================================================================
# (6b) BrightnessMeasured with ymin=None / ymax=None -> directive written normally
# ===========================================================================


class TestMeasuredYminYmaxNone:
    """Directive must be written even when ymin=None and ymax=None in measured (optional fields)."""

    def test_directive_written_when_ymin_ymax_none(self, tmp_path: Path) -> None:
        """When measured has ymin=None and ymax=None, directive must still be written (FR-5)."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(target_luma=128.0)

        # Arrange: measured with ymin=None and ymax=None (optional fields absent)
        measured_no_minmax: dict[str, Any] = {
            "measured": {
                "yavg": 100.0,
                "ymin": None,
                "ymax": None,
                "sampled_frames": 5,
            },
            "warnings": [],
        }

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: measured_no_minmax,
            )
            # Act
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        # Assert: directive must be written despite ymin/ymax being None
        assert result["ok"] is True
        tl = otio.adapters.read_from_file(str(output))
        color_meta = tl.metadata.get("clipwright", {}).get("color")
        assert color_meta is not None, (
            "metadata['clipwright']['color'] must be written when ymin/ymax are None."
        )


# ===========================================================================
# (7) summary contains measured_luma/target_luma/brightness
# ===========================================================================


class TestSummaryContent:
    """ok_result summary must include key metrics for AI decision-making (FR-2 output)."""

    def test_summary_contains_measured_luma(self, tmp_path: Path) -> None:
        """summary must reference measured_luma (YAVG value)."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(target_luma=128.0)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=96.4),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        summary = result.get("summary", "")
        # summary must reference measured luma value
        assert "96.4" in summary or "measured" in summary.lower(), (
            f"summary must reference measured_luma. Got: {summary!r}"
        )

    def test_summary_contains_target_luma(self, tmp_path: Path) -> None:
        """summary must reference target_luma."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(target_luma=200.0)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=96.4),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        summary = result.get("summary", "")
        assert "200" in summary or "target" in summary.lower(), (
            f"summary must reference target_luma. Got: {summary!r}"
        )

    def test_summary_contains_brightness(self, tmp_path: Path) -> None:
        """summary must reference the computed brightness offset."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(target_luma=128.0)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=96.4),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        summary = result.get("summary", "")
        assert "brightness" in summary.lower(), (
            f"summary must reference brightness offset. Got: {summary!r}"
        )


# ===========================================================================
# Helpers for new WB / chroma tests
# ===========================================================================


def _fake_measured_with_chroma(
    yavg: float = 128.0,
    uavg: float | None = None,
    vavg: float | None = None,
    sampled_frames: int = 10,
) -> dict[str, Any]:
    """Return a fake measure_brightness result that optionally includes chroma values.

    When uavg/vavg are provided, the measured dict includes them so that the
    implementation (once written) can derive white_balance.  When absent, the
    dict mirrors the existing _fake_measured() structure so that the schema
    validation of BrightnessMeasured passes with the v0.2.x fields only.
    """
    measured: dict[str, Any] = {
        "yavg": yavg,
        "ymin": None,
        "ymax": None,
        "sampled_frames": sampled_frames,
    }
    if uavg is not None:
        measured["uavg"] = uavg
    if vavg is not None:
        measured["vavg"] = vavg
    return {"measured": measured, "warnings": []}


# ===========================================================================
# Auto WB derivation (§4.2, AC-2, FR-2)
# ===========================================================================


class TestAutoWhiteBalance:
    """Auto WB derivation from uavg/vavg using BT.601 gray-world inverse-cast (§4.2)."""

    def test_wb_formula_with_known_chroma(self, tmp_path: Path) -> None:
        """Given known uavg/vavg, white_balance must match the §4.2 formula.

        uavg=148, vavg=138  →  dU=20, dV=10
        r = -1.402 * 10 / 255.0  ≈ -0.054980
        g = (0.344 * 20 + 0.714 * 10) / 255.0  ≈  0.054980
        b = -1.772 * 20 / 255.0  ≈ -0.138980

        RED: BrightnessMeasured has no uavg/vavg fields yet →
        ValidationError inside _detect_color_inner → ok=False (not ok=True).
        """
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(target_luma=128.0)

        uavg, vavg = 148.0, 138.0
        dU = uavg - 128.0
        dV = vavg - 128.0
        expected_r = -1.402 * dV / 255.0
        expected_g = (0.344 * dU + 0.714 * dV) / 255.0
        expected_b = -1.772 * dU / 255.0

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured_with_chroma(
                    yavg=128.0, uavg=uavg, vavg=vavg
                ),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True, (
            "Auto WB: detect_color must succeed when uavg/vavg are present."
            f" error={result.get('error')}"
        )
        tl = otio.adapters.read_from_file(str(output))
        color_meta = tl.metadata["clipwright"]["color"]
        wb = color_meta.get("white_balance")
        assert wb is not None, (
            "§4.2: white_balance must be written when uavg/vavg are present."
        )
        assert wb["r"] == pytest.approx(expected_r, abs=1e-4), (
            f"r mismatch: expected {expected_r:.6f}, got {wb['r']}"
        )
        assert wb["g"] == pytest.approx(expected_g, abs=1e-4), (
            f"g mismatch: expected {expected_g:.6f}, got {wb['g']}"
        )
        assert wb["b"] == pytest.approx(expected_b, abs=1e-4), (
            f"b mismatch: expected {expected_b:.6f}, got {wb['b']}"
        )

    def test_wb_clamp_applied_at_extremes(self, tmp_path: Path) -> None:
        """WB shifts must be clamped to [-1,1] for extreme uavg/vavg values (§4.2).

        uavg=255, vavg=255 → dU=127, dV=127
        b = -1.772*127/255 ≈ -0.882 (no clamp needed, within range)
        Use uavg=0, vavg=0 → dU=-128, dV=-128
        r = -1.402*(-128)/255 ≈ +0.703 (within range but tests direction)
        """
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(target_luma=128.0)

        # uavg=0, vavg=0 → extreme cast toward blue-green
        # r = -1.402*(-128)/255 ≈ 0.703  (clamped to [−1,1] = 0.703)
        # g = (0.344*(−128)+0.714*(−128))/255 = (−43.9−91.4)/255 ≈ −0.531
        # b = -1.772*(−128)/255 ≈ 0.890

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured_with_chroma(
                    yavg=128.0, uavg=0.0, vavg=0.0
                ),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True, f"error={result.get('error')}"
        tl = otio.adapters.read_from_file(str(output))
        wb = tl.metadata["clipwright"]["color"].get("white_balance")
        assert wb is not None, "white_balance must be written"
        assert -1.0 <= wb["r"] <= 1.0, "r must be clamped to [-1,1]"
        assert -1.0 <= wb["g"] <= 1.0, "g must be clamped to [-1,1]"
        assert -1.0 <= wb["b"] <= 1.0, "b must be clamped to [-1,1]"


# ===========================================================================
# FR-4: measured present but uavg/vavg None → white_balance omitted + warning
# ===========================================================================


class TestWbChromaAbsent:
    """FR-4: measured present but chroma absent → white_balance omitted + WB warning."""

    def test_wb_warning_emitted_when_chroma_not_measurable(
        self, tmp_path: Path
    ) -> None:
        """FR-4: A warning about WB failure must appear when uavg/vavg are absent.

        RED: current implementation emits no WB-specific warning when chroma absent.
        """
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(target_luma=128.0)

        # measured has yavg but no uavg/vavg (chroma measurement did not succeed)
        measured_no_chroma: dict[str, Any] = {
            "measured": {
                "yavg": 128.0,
                "ymin": None,
                "ymax": None,
                "sampled_frames": 10,
                # uavg and vavg deliberately absent
            },
            "warnings": [],
        }

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: measured_no_chroma,
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True, (
            "FR-4: detect_color must succeed even when chroma measurement absent."
            f" error={result.get('error')}"
        )
        # A warning describing the WB failure must be present (FR-4)
        wb_keywords = ("white", "wb", "chroma", "balance", "uavg", "vavg")
        wb_warnings = [
            w
            for w in result.get("warnings", [])
            if any(kw in w.lower() for kw in wb_keywords)
        ]
        assert len(wb_warnings) > 0, (
            "FR-4: A warning about WB measurement failure must be present when chroma absent."
            f" Got warnings: {result.get('warnings', [])}"
        )

    def test_white_balance_absent_when_chroma_not_measurable(
        self, tmp_path: Path
    ) -> None:
        """FR-4: white_balance key must be absent (None) when uavg/vavg are not available."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(target_luma=178.0)

        measured_no_chroma: dict[str, Any] = {
            "measured": {
                "yavg": 128.0,
                "ymin": None,
                "ymax": None,
                "sampled_frames": 10,
            },
            "warnings": [],
        }

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: measured_no_chroma,
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        tl = otio.adapters.read_from_file(str(output))
        color_meta = tl.metadata["clipwright"]["color"]
        assert color_meta is not None, "Directive must still be written when chroma absent"
        # white_balance must be None (absent), not a neutral WhiteBalanceParams object
        assert color_meta.get("white_balance") is None, (
            "FR-4: white_balance must be None when uavg/vavg not measurable."
        )
        # brightness+eq must still be present
        expected_brightness = (178.0 - 128.0) / 255.0
        assert color_meta["eq"]["brightness"] == pytest.approx(
            expected_brightness, abs=1e-4
        )


# ===========================================================================
# Caller override (§4.3, AC-3): temperature/tint → WB from axes only
# ===========================================================================


class TestCallerWbOverride:
    """§4.3 / AC-3: temperature/tint caller override discards auto measurement."""

    def test_temperature_tint_override_yields_axis_mapping(
        self, tmp_path: Path
    ) -> None:
        """temperature=0.3, tint=0.1 → r=+0.3, b=-0.3, g=-0.1; auto WB discarded.

        RED: DetectColorOptions has no temperature/tint fields yet → ValidationError.
        """
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        # RED: temperature/tint fields do not exist on DetectColorOptions yet
        opts = DetectColorOptions(  # type: ignore[call-arg]
            target_luma=128.0,
            temperature=0.3,
            tint=0.1,
        )

        # Even with uavg/vavg present (would yield different auto WB),
        # the caller override must take precedence.
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured_with_chroma(
                    yavg=128.0, uavg=148.0, vavg=138.0
                ),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True, f"error={result.get('error')}"
        tl = otio.adapters.read_from_file(str(output))
        wb = tl.metadata["clipwright"]["color"].get("white_balance")
        assert wb is not None
        # temperature=0.3 → r=+0.3, b=-0.3; tint=0.1 → g=-0.1
        assert wb["r"] == pytest.approx(0.3, abs=1e-4), (
            f"r must equal +temperature (0.3), got {wb['r']}"
        )
        assert wb["b"] == pytest.approx(-0.3, abs=1e-4), (
            f"b must equal -temperature (-0.3), got {wb['b']}"
        )
        assert wb["g"] == pytest.approx(-0.1, abs=1e-4), (
            f"g must equal -tint (-0.1), got {wb['g']}"
        )

    def test_temperature_only_zero_g_axis(self, tmp_path: Path) -> None:
        """temperature=0.5, tint omitted → r=+0.5, b=-0.5, g=0.0 (tint defaults to 0).

        RED: DetectColorOptions has no temperature field yet → ValidationError.
        """
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(  # type: ignore[call-arg]
            target_luma=128.0,
            temperature=0.5,
        )

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=128.0),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        wb = tl = otio.adapters.read_from_file(str(output))  # noqa: F841
        wb = tl.metadata["clipwright"]["color"].get("white_balance")
        assert wb is not None
        assert wb["r"] == pytest.approx(0.5, abs=1e-4)
        assert wb["b"] == pytest.approx(-0.5, abs=1e-4)
        assert wb["g"] == pytest.approx(0.0, abs=1e-4)


# ===========================================================================
# eq population (FR-1): saturation/contrast/gamma options populate EqParams
# ===========================================================================


class TestEqPopulation:
    """FR-1: saturation/contrast/gamma caller options populate EqParams fields."""

    def test_saturation_option_writes_to_eq(self, tmp_path: Path) -> None:
        """saturation=1.5 must be written into eq.saturation.

        RED: DetectColorOptions has no saturation field yet → ValidationError.
        """
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(  # type: ignore[call-arg]
            target_luma=128.0,
            saturation=1.5,
        )

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=128.0),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        tl = otio.adapters.read_from_file(str(output))
        eq = tl.metadata["clipwright"]["color"]["eq"]
        assert eq["saturation"] == pytest.approx(1.5, abs=1e-4), (
            f"eq.saturation must equal caller-supplied 1.5, got {eq['saturation']}"
        )

    def test_contrast_option_writes_to_eq(self, tmp_path: Path) -> None:
        """contrast=0.8 must be written into eq.contrast.

        RED: DetectColorOptions has no contrast field yet → ValidationError.
        """
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(  # type: ignore[call-arg]
            target_luma=128.0,
            contrast=0.8,
        )

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=128.0),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        tl = otio.adapters.read_from_file(str(output))
        eq = tl.metadata["clipwright"]["color"]["eq"]
        assert eq["contrast"] == pytest.approx(0.8, abs=1e-4), (
            f"eq.contrast must equal caller-supplied 0.8, got {eq['contrast']}"
        )

    def test_gamma_option_writes_to_eq(self, tmp_path: Path) -> None:
        """gamma=2.2 must be written into eq.gamma.

        RED: DetectColorOptions has no gamma field yet → ValidationError.
        """
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(  # type: ignore[call-arg]
            target_luma=128.0,
            gamma=2.2,
        )

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=128.0),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        tl = otio.adapters.read_from_file(str(output))
        eq = tl.metadata["clipwright"]["color"]["eq"]
        assert eq["gamma"] == pytest.approx(2.2, abs=1e-4), (
            f"eq.gamma must equal caller-supplied 2.2, got {eq['gamma']}"
        )

    def test_absent_options_use_neutral_defaults(self, tmp_path: Path) -> None:
        """When eq options not supplied, neutral defaults must hold (contrast=1, sat=1, gamma=1)."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(target_luma=128.0)  # no saturation/contrast/gamma

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=128.0),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        tl = otio.adapters.read_from_file(str(output))
        eq = tl.metadata["clipwright"]["color"]["eq"]
        assert eq["contrast"] == pytest.approx(1.0, abs=1e-4)
        assert eq["saturation"] == pytest.approx(1.0, abs=1e-4)
        assert eq["gamma"] == pytest.approx(1.0, abs=1e-4)

    def test_data_block_returns_actual_eq_values_not_hardcoded(
        self, tmp_path: Path
    ) -> None:
        """data block must return actual saturation/contrast/gamma, not hardcoded 1.0.

        RED: (a) DetectColorOptions has no saturation/contrast/gamma fields yet, and
             (b) current ok_result always writes hardcoded 1.0 for these fields.
        """
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(  # type: ignore[call-arg]
            target_luma=128.0,
            saturation=1.8,
            contrast=1.2,
            gamma=0.5,
        )

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=128.0),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        data = result.get("data", {})
        assert data.get("saturation") == pytest.approx(1.8, abs=1e-4), (
            "data.saturation must be actual value 1.8, not hardcoded 1.0."
            f" Got: {data.get('saturation')}"
        )
        assert data.get("contrast") == pytest.approx(1.2, abs=1e-4), (
            "data.contrast must be actual value 1.2, not hardcoded 1.0."
            f" Got: {data.get('contrast')}"
        )
        assert data.get("gamma") == pytest.approx(0.5, abs=1e-4), (
            "data.gamma must be actual value 0.5, not hardcoded 1.0."
            f" Got: {data.get('gamma')}"
        )


# ===========================================================================
# Backward compatibility (§6/§10): no new options → v0.2.1 directive shape
# ===========================================================================


class TestBackwardCompat:
    """§6/§10 backward compat: calling with NO new options preserves v0.2.1 shape."""

    def test_no_new_options_no_white_balance_key(self, tmp_path: Path) -> None:
        """color called without new options must not write white_balance (v0.2.1 compat)."""
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(target_luma=178.0)  # only v0.2.1 options

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=128.0),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        tl = otio.adapters.read_from_file(str(output))
        color_meta = tl.metadata["clipwright"]["color"]
        # white_balance must be absent (None) when no auto WB and no caller override
        assert color_meta.get("white_balance") is None, (
            "§6 compat: white_balance must not appear in directive when not triggered."
        )
        # lut must be absent
        assert color_meta.get("lut") is None, (
            "§6 compat: lut must not appear in directive when no lut option given."
        )
        # brightness math unchanged (v0.2.1 formula)
        expected_brightness = (178.0 - 128.0) / 255.0
        assert color_meta["eq"]["brightness"] == pytest.approx(
            expected_brightness, abs=1e-4
        )


# ===========================================================================
# NFR-9: distinct-OTIO invariant preserved even with new options
# ===========================================================================


class TestDistinctOtioNfr9:
    """NFR-9: output == timeline still rejected when new options are supplied."""

    def test_output_equals_timeline_rejected_with_new_options(
        self, tmp_path: Path
    ) -> None:
        """output == timeline must be rejected even with temperature/tint options.

        RED: DetectColorOptions has no temperature/tint fields yet → ValidationError.
        After implementation: PATH_NOT_ALLOWED is the expected outcome.
        """
        from clipwright_color.color import (
            detect_color,  # type: ignore[import-not-found]
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        timeline_file = tmp_path / "existing.otio"
        timeline_file.write_bytes(b"dummy otio")

        # temperature/tint options — fields don't exist yet (RED at construction)
        opts = DetectColorOptions(  # type: ignore[call-arg]
            target_luma=128.0,
            temperature=0.2,
            tint=0.1,
        )

        result = detect_color(
            media=str(media),
            output=str(timeline_file),
            options=opts,
            timeline=str(timeline_file),
        )
        assert result["ok"] is False
        assert result["error"]["code"] in (
            ErrorCode.INVALID_INPUT.value,
            ErrorCode.PATH_NOT_ALLOWED.value,
        ), (
            "NFR-9: output == timeline must always be rejected."
            f" Got code: {result['error']['code']}"
        )
