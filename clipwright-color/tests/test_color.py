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
        RED: pre-migration code returns INVALID_INPUT (same-dir block L117-131).
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
