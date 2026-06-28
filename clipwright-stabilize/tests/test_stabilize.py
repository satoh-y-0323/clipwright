"""test_stabilize.py — Tests for clipwright_stabilize.stabilize (detect_shake orchestration).

Mock policy (F-1):
  - Patch clipwright_stabilize.stabilize.inspect_media to inject stream presence/absence.
  - Patch clipwright_stabilize.stabilize.run_vidstabdetect to control analyze output.
  - No real ffmpeg/ffprobe binary or real libvidstab is invoked.
  - OTIO timeline I/O uses tmp_path with real save_timeline.

Verification points:
  (1) happy path: StabilizeDirective written to metadata["clipwright"]["stabilize"],
      trf_path is absolute, severity/shakiness/accuracy/smoothing in directive.
  (2) severity=None -> directive still written (differs from color measured=None skip).
  (3) output validation errors: 5 kinds (non-.otio extension, parent dir absent,
      output==media, output==timeline, media and output in different directories).
  (4) video stream absent -> UNSUPPORTED_OPERATION (audio absence is fine).
  (5) timeline=None -> new timeline + full clip; timeline=path -> load_and_validate.
  (6) multi-source timeline -> UNSUPPORTED_OPERATION.
  (7) artifacts: 2 items (timeline with role="timeline", analysis with role="analysis"
      format="trf"); both paths must exist on disk.
  (8) summary contains severity / shakiness / smoothing / trf basename.
  (9) ClipwrightError path -> error_result({ok:false, error:{code, message, hint}}).
  (10) recommendation field exposed in data / summary / warnings (AC-3/AC-4/AC-5, NF-5).

Requirements: FR-1-3, FR-1-4, FR-1-5, architecture-report §5.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest
from clipwright.errors import ErrorCode
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

# ===========================================================================
# Helpers
# ===========================================================================

FPS = 30.0
_TEST_BIT_RATE = 8_000_000
_FAKE_TRF_PATH = "/tmp/video.stabilize.trf"


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


def _fake_analyze_result(
    trf_abs: Path,
    severity: float | None = 0.35,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Return a fake run_vidstabdetect result dict."""
    trf_abs.write_bytes(b"TRF1dummy")
    return {
        "trf_path": str(trf_abs),
        "severity": severity,
        "warnings": warnings or [],
    }


def _fake_analyze_none_severity(trf_abs: Path) -> dict[str, Any]:
    """Fake analyze result with severity=None and a warning."""
    trf_abs.write_bytes(b"TRF1dummy")
    return {
        "trf_path": str(trf_abs),
        "severity": None,
        "warnings": ["Could not estimate shake severity from the .trf file."],
    }


# ===========================================================================
# (1) Happy path: directive written with expected fields
# ===========================================================================


class TestHappyPath:
    """detect_shake writes StabilizeDirective to metadata['clipwright']['stabilize']."""

    def test_directive_written_to_otio_metadata(self, tmp_path: Path) -> None:
        """StabilizeDirective must be stored at metadata['clipwright']['stabilize']."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        assert output.exists()
        tl = otio.adapters.read_from_file(str(output))
        stab_meta = tl.metadata.get("clipwright", {}).get("stabilize")
        assert stab_meta is not None, (
            "metadata['clipwright']['stabilize'] must be written on success."
        )

    def test_directive_kind_is_stabilize(self, tmp_path: Path) -> None:
        """Written directive must have kind='stabilize'."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            )
            detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        tl = otio.adapters.read_from_file(str(output))
        stab_meta = tl.metadata["clipwright"]["stabilize"]
        assert stab_meta["kind"] == "stabilize"

    def test_trf_path_is_absolute_in_directive(self, tmp_path: Path) -> None:
        """trf_path in directive must be an absolute path."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            )
            detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        tl = otio.adapters.read_from_file(str(output))
        trf_path_in_meta = tl.metadata["clipwright"]["stabilize"]["trf_path"]
        assert Path(trf_path_in_meta).is_absolute(), (
            f"trf_path must be absolute, got: {trf_path_in_meta}"
        )

    def test_directive_contains_shakiness_accuracy_smoothing(
        self, tmp_path: Path
    ) -> None:
        """Directive must contain shakiness, accuracy, and smoothing from options."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions(shakiness=7, accuracy=12, smoothing=50)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            )
            detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        tl = otio.adapters.read_from_file(str(output))
        meta = tl.metadata["clipwright"]["stabilize"]
        assert meta["shakiness"] == 7
        assert meta["accuracy"] == 12
        assert meta["smoothing"] == 50

    def test_directive_contains_severity(self, tmp_path: Path) -> None:
        """Directive must contain severity from analyze result."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(
                    trf_abs, severity=0.42
                ),
            )
            detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        tl = otio.adapters.read_from_file(str(output))
        meta = tl.metadata["clipwright"]["stabilize"]
        assert meta["severity"] == pytest.approx(0.42)


# ===========================================================================
# (2) severity=None -> directive still written
# ===========================================================================


class TestSeverityNoneDirectiveWritten:
    """severity=None must not prevent directive from being written (differs from color)."""

    def test_directive_written_when_severity_none(self, tmp_path: Path) -> None:
        """Even with severity=None, directive must be written to metadata."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_none_severity(
                    trf_abs
                ),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        tl = otio.adapters.read_from_file(str(output))
        stab_meta = tl.metadata.get("clipwright", {}).get("stabilize")
        assert stab_meta is not None, (
            "StabilizeDirective must be written even when severity=None."
        )
        assert stab_meta["severity"] is None


# ===========================================================================
# (3) Output validation errors (5 kinds)
# ===========================================================================


class TestOutputValidation:
    """Output path validation (architecture-report §5 step 1)."""

    def test_non_otio_extension_rejected(self, tmp_path: Path) -> None:
        """output with non-.otio extension must return INVALID_INPUT."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.json"
        opts = DetectShakeOptions()

        result = detect_shake(
            media=str(media), output=str(output), options=opts, timeline=None
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value

    def test_missing_parent_directory_rejected(self, tmp_path: Path) -> None:
        """output whose parent directory does not exist must return INVALID_INPUT."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "nonexistent" / "out.otio"
        opts = DetectShakeOptions()

        result = detect_shake(
            media=str(media), output=str(output), options=opts, timeline=None
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value

    def test_output_equals_media_rejected(self, tmp_path: Path) -> None:
        """output == media (same path) must return PATH_NOT_ALLOWED."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        # media must have .otio extension to avoid triggering extension check first
        media = tmp_path / "video.otio"
        media.write_bytes(b"dummy")
        opts = DetectShakeOptions()

        result = detect_shake(
            media=str(media), output=str(media), options=opts, timeline=None
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED.value

    def test_output_equals_timeline_rejected(self, tmp_path: Path) -> None:
        """output == timeline (same path) must return PATH_NOT_ALLOWED."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        timeline_file = tmp_path / "existing.otio"
        timeline_file.write_bytes(b"dummy otio")
        opts = DetectShakeOptions()

        result = detect_shake(
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
        Previously: pre-migration code returned INVALID_INPUT (same-dir block L110-125).
        """
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media_dir = tmp_path / "media_dir"
        media_dir.mkdir()
        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")
        other_dir = tmp_path / "other_dir"
        other_dir.mkdir()
        output = other_dir / "out.otio"
        trf_abs = other_dir / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )
        assert result["ok"] is True, (
            "DC-AS-004: output in different dir from media must be allowed."
            f" error={result.get('error')}"
        )


# ===========================================================================
# (4) Video stream required; audio absence is acceptable
# ===========================================================================


class TestVideoRequired:
    """Video stream is required; audio is not (FR-1-3 step 2, architecture-report §5)."""

    def test_no_video_stream_returns_unsupported(self, tmp_path: Path) -> None:
        """When inspect_media returns no video stream, UNSUPPORTED_OPERATION must be returned."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "audio_only.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p), has_video=False, has_audio=True),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.UNSUPPORTED_OPERATION.value

    def test_no_audio_stream_is_accepted(self, tmp_path: Path) -> None:
        """When audio is absent but video is present, detect_shake must succeed."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video_only.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video_only.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p), has_video=True, has_audio=False),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True, (
            "Audio absence must not be an error (stabilize requires video only)."
        )


# ===========================================================================
# (5) timeline=None -> new timeline; timeline=path -> load_and_validate
# ===========================================================================


class TestTimelineResolution:
    """Timeline creation or loading (FR-1-3 step 3, architecture-report §5)."""

    def test_timeline_none_creates_new_timeline(self, tmp_path: Path) -> None:
        """timeline=None must result in a new timeline being created."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        # Output OTIO must have been created with the media as source
        assert output.exists()
        tl = otio.adapters.read_from_file(str(output))
        assert tl is not None


# ===========================================================================
# (6) Multi-source timeline -> UNSUPPORTED_OPERATION
# ===========================================================================


class TestMultiSourceTimeline:
    """Multi-source timeline must be rejected with UNSUPPORTED_OPERATION (ADR-ST-2)."""

    def test_multi_source_timeline_rejected(self, tmp_path: Path) -> None:
        """A timeline with multiple distinct media sources must return UNSUPPORTED_OPERATION."""
        import opentimelineio as otio
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        # Build a multi-source timeline
        media1 = tmp_path / "video1.mp4"
        media1.write_bytes(b"dummy")
        media2 = tmp_path / "video2.mp4"
        media2.write_bytes(b"dummy")
        tl = otio.schema.Timeline()
        track = otio.schema.Track()
        tl.tracks.append(track)
        clip1 = otio.schema.Clip(
            name="clip1",
            media_reference=otio.schema.ExternalReference(target_url=str(media1)),
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 30),
                duration=otio.opentime.RationalTime(150, 30),
            ),
        )
        clip2 = otio.schema.Clip(
            name="clip2",
            media_reference=otio.schema.ExternalReference(target_url=str(media2)),
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 30),
                duration=otio.opentime.RationalTime(150, 30),
            ),
        )
        track.append(clip1)
        track.append(clip2)
        timeline_path = tmp_path / "multi.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        # Use media1 as the "primary" media
        output = tmp_path / "out.otio"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            result = detect_shake(
                media=str(media1),
                output=str(output),
                options=opts,
                timeline=str(timeline_path),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.UNSUPPORTED_OPERATION.value


# ===========================================================================
# (7) artifacts: 2 items, both paths exist
# ===========================================================================


class TestArtifacts:
    """artifacts must contain 2 items (timeline + analysis); both paths must exist."""

    def test_two_artifacts_returned(self, tmp_path: Path) -> None:
        """Result must have exactly 2 artifacts: timeline and analysis."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        artifacts = result.get("artifacts", [])
        assert len(artifacts) == 2, (
            f"Expected 2 artifacts, got {len(artifacts)}: {artifacts}"
        )

    def test_artifact_roles(self, tmp_path: Path) -> None:
        """Artifacts must have roles 'timeline' and 'analysis'."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        artifacts = result["artifacts"]
        roles = {a["role"] for a in artifacts}
        assert "timeline" in roles, "An artifact with role='timeline' must be present"
        assert "analysis" in roles, "An artifact with role='analysis' must be present"

    def test_analysis_artifact_format_is_trf(self, tmp_path: Path) -> None:
        """The analysis artifact must have format='trf'."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        artifacts = result["artifacts"]
        analysis = next(a for a in artifacts if a["role"] == "analysis")
        assert analysis.get("format") == "trf", (
            f"analysis artifact format must be 'trf', got: {analysis.get('format')}"
        )

    def test_both_artifact_paths_exist_on_disk(self, tmp_path: Path) -> None:
        """Both artifact paths (timeline and analysis) must exist on disk."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        for artifact in result["artifacts"]:
            p = Path(artifact["path"])
            assert p.exists(), f"Artifact path must exist on disk: {p}"


# ===========================================================================
# (8) summary contains key metrics
# ===========================================================================


class TestSummaryContent:
    """ok_result summary must include severity / shakiness / smoothing / trf basename."""

    def test_summary_contains_severity(self, tmp_path: Path) -> None:
        """summary must reference severity."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(
                    trf_abs, severity=0.25
                ),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        summary = result.get("summary", "")
        # summary must contain some reference to severity (value or the word)
        assert "severity" in summary.lower() or "0.25" in summary, (
            f"summary must reference severity, got: {summary}"
        )

    def test_summary_contains_trf_basename(self, tmp_path: Path) -> None:
        """summary must reference the trf basename."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "myvideo.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "myvideo.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        summary = result.get("summary", "")
        assert "myvideo.stabilize.trf" in summary, (
            f"summary must contain trf basename, got: {summary}"
        )

    def test_summary_contains_smoothing(self, tmp_path: Path) -> None:
        """summary must reference smoothing value."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions(smoothing=42)

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        summary = result.get("summary", "")
        assert "42" in summary or "smoothing" in summary.lower(), (
            f"summary must reference smoothing=42, got: {summary}"
        )


# ===========================================================================
# (9) ClipwrightError path -> error_result
# ===========================================================================


class TestErrorResult:
    """ClipwrightError must be caught and returned as error_result (architecture-report §5)."""

    def test_clipwright_error_gives_error_result(self, tmp_path: Path) -> None:
        """ClipwrightError raised by run_vidstabdetect must yield ok=False result."""
        from clipwright.errors import ClipwrightError, ErrorCode
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectShakeOptions()

        def _fail(media_path: Path, output_path: Path, options: Any) -> Any:
            raise ClipwrightError(
                code=ErrorCode.UNSUPPORTED_OPERATION,
                message="This ffmpeg build does not support vidstabdetect.",
                hint="Install an ffmpeg build with libvidstab.",
            )

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                _fail,
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is False
        assert result.get("error") is not None
        assert result["error"]["code"] == ErrorCode.UNSUPPORTED_OPERATION.value
        assert result["error"].get("message") is not None
        assert result["error"].get("hint") is not None


# ===========================================================================
# (10) recommendation field in data, summary, and warnings (AC-3/AC-4/AC-5, NF-5)
# ===========================================================================


class TestRecommendationContract:
    """detect_shake must expose recommendation in data, summary, and warnings.

    Mock policy matches the rest of this module (F-1): inspect_media and
    run_vidstabdetect are patched; no real ffmpeg or libvidstab is invoked.

    AC-3/AC-4: recommendation reflects severity (skip for low / apply for high).
    AC-5:      recommendation defaults to 'apply' when severity=None.
    NF-5:      envelope contract {ok, summary, data, artifacts, warnings} is
               maintained; no existing key is removed.
    """

    def test_data_recommendation_field_present(self, tmp_path: Path) -> None:
        """data must expose 'recommendation' key with value 'skip' or 'apply' (NF-5)."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(
                    trf_abs, severity=0.35
                ),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        data = result.get("data", {})
        assert "recommendation" in data, (
            f"data must expose 'recommendation' key, got keys: {sorted(data.keys())}"
        )
        assert data["recommendation"] in ("skip", "apply"), (
            f"data['recommendation'] must be 'skip' or 'apply', "
            f"got {data['recommendation']!r}"
        )

    def test_summary_contains_recommendation_keyword(self, tmp_path: Path) -> None:
        """summary must contain the word 'recommendation' (not merely 'apply' by accident).

        The current summary already contains 'apply' in 'apply with clipwright-render';
        this test checks that the implementation explicitly surfaces 'recommendation'
        as a named concept in the summary text.
        """
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(
                    trf_abs, severity=0.35
                ),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        summary = result.get("summary", "").lower()
        assert "recommendation" in summary, (
            "summary must explicitly include the word 'recommendation'. "
            f"Got: {result.get('summary', '')}"
        )

    def test_summary_mentions_agent_final_decision(self, tmp_path: Path) -> None:
        """summary must state that the calling agent makes the final decision."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(
                    trf_abs, severity=0.35
                ),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        # summary must communicate that the AI caller owns the final decision.
        # Accept any of the natural English phrasings the implementation may choose.
        summary_lower = result.get("summary", "").lower()
        ai_decision_keywords = [
            "agent",
            "caller",
            "override",
            "final decision",
            "you may",
            "discretion",
            "advisory",
        ]
        assert any(kw in summary_lower for kw in ai_decision_keywords), (
            "summary must mention that the calling agent makes the final decision "
            f"(checked for: {ai_decision_keywords}). "
            f"Got: {result.get('summary', '')}"
        )

    def test_severity_none_warning_mentions_recommendation_defaulted(
        self, tmp_path: Path
    ) -> None:
        """When severity=None, warnings must explain that recommendation defaulted to 'apply' (AC-5)."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_none_severity(
                    trf_abs
                ),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        warnings: list[str] = result.get("warnings") or []
        warning_text = " ".join(warnings).lower()
        assert "recommendation" in warning_text or "apply" in warning_text, (
            "When severity=None, warnings must mention that recommendation defaulted "
            f"to 'apply' (AC-5). Got warnings: {warnings}"
        )

    def test_severity_known_no_extra_warning(self, tmp_path: Path) -> None:
        """When severity is known, no extra recommendation warning must appear (NF-5)."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        # run_vidstabdetect returns zero warnings when severity is available.
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(
                    trf_abs, severity=0.35, warnings=[]
                ),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True
        warnings = result.get("warnings") or []
        assert len(warnings) == 0, (
            "When severity is known, detect_shake must not append extra warnings. "
            f"Got: {warnings}"
        )

    def test_envelope_contract_maintained(self, tmp_path: Path) -> None:
        """detect_shake must still return {ok, summary, data, artifacts, warnings} (NF-5)."""
        from clipwright_stabilize.schemas import (  # type: ignore[import-not-found]
            DetectShakeOptions,
        )
        from clipwright_stabilize.stabilize import (  # type: ignore[import-not-found]
            detect_shake,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        opts = DetectShakeOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_stabilize.stabilize.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            )
            result = detect_shake(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert "ok" in result, "envelope must have 'ok'"
        assert "summary" in result, "envelope must have 'summary'"
        assert "data" in result, "envelope must have 'data'"
        assert "artifacts" in result, "envelope must have 'artifacts'"
        # 'warnings' may be None/absent when empty; ok_result omits it — not checked here.
