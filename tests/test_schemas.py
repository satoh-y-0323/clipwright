"""test_schemas.py — Contract tests for schemas.py.

Covers:
- RationalTimeModel / TimeRangeModel
- MediaRef / Artifact / ToolResult / ToolError
- StreamInfo / MediaInfo
- OperationError / ValidationReport (§13.1 DC-AM-003)
- to_otio_time / from_otio_time (§13.1 DC-GP-005)
"""

from __future__ import annotations

import pytest

# --- Import ---
from clipwright.schemas import (
    Artifact,
    MediaInfo,
    MediaRef,
    OperationError,
    RationalTimeModel,
    StreamInfo,
    TimeRangeModel,
    ToolError,
    ToolResult,
    ValidationReport,
    from_otio_time,
    full_media_range,
    to_otio_time,
)

# ===========================================================================
# RationalTimeModel
# ===========================================================================


class TestRationalTimeModel:
    """Basic contract for RationalTimeModel."""

    def test_construct_basic(self) -> None:
        """Can hold value and rate."""
        rt = RationalTimeModel(value=30.0, rate=30.0)
        assert rt.value == 30.0
        assert rt.rate == 30.0

    def test_rate_preserved(self) -> None:
        """rate is stored as float (not lost as a bare seconds float)."""
        rt = RationalTimeModel(value=1.0, rate=24.0)
        assert rt.rate == 24.0

    @pytest.mark.parametrize(
        "value, rate",
        [
            (0.0, 30.0),
            (1.0, 24.0),
            (90.0, 29.97),
            (1000.0, 1000.0),
        ],
    )
    def test_various_rates(self, value: float, rate: float) -> None:
        """Can be constructed with representative fps/rate values."""
        rt = RationalTimeModel(value=value, rate=rate)
        assert rt.value == value
        assert rt.rate == rate

    def test_json_roundtrip(self) -> None:
        """rate is preserved through JSON serialisation and deserialisation."""
        rt = RationalTimeModel(value=10.0, rate=24.0)
        restored = RationalTimeModel.model_validate_json(rt.model_dump_json())
        assert restored.value == rt.value
        assert restored.rate == rt.rate


# ===========================================================================
# TimeRangeModel
# ===========================================================================


class TestTimeRangeModel:
    """Basic contract for TimeRangeModel."""

    def test_construct(self) -> None:
        """Can hold start_time and duration."""
        start = RationalTimeModel(value=0.0, rate=30.0)
        dur = RationalTimeModel(value=90.0, rate=30.0)
        tr = TimeRangeModel(start_time=start, duration=dur)
        assert tr.start_time.rate == 30.0
        assert tr.duration.value == 90.0

    def test_json_roundtrip(self) -> None:
        """Values are preserved through JSON serialisation and deserialisation."""
        start = RationalTimeModel(value=30.0, rate=30.0)
        dur = RationalTimeModel(value=60.0, rate=30.0)
        tr = TimeRangeModel(start_time=start, duration=dur)
        restored = TimeRangeModel.model_validate_json(tr.model_dump_json())
        assert restored.start_time.value == 30.0
        assert restored.duration.value == 60.0


# ===========================================================================
# MediaRef
# ===========================================================================


class TestMediaRef:
    """Basic contract for MediaRef."""

    def test_minimal(self) -> None:
        """Can be constructed with target_url only."""
        ref = MediaRef(target_url="/path/to/video.mp4")
        assert ref.target_url == "/path/to/video.mp4"
        assert ref.name is None
        assert ref.available_range is None

    def test_with_all_fields(self) -> None:
        """All fields can be specified."""
        rng = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=90.0, rate=30.0),
        )
        ref = MediaRef(
            target_url="/path/to/video.mp4", name="clip1", available_range=rng
        )
        assert ref.name == "clip1"
        assert ref.available_range is not None
        assert ref.available_range.duration.value == 90.0


# ===========================================================================
# Artifact
# ===========================================================================


class TestArtifact:
    """Basic contract for Artifact."""

    @pytest.mark.parametrize(
        "role, path, format_",
        [
            ("timeline", "/out/timeline.otio", "otio"),
            ("output", "/out/output.mp4", "mp4"),
            ("caption", "/out/subs.srt", "srt"),
            ("analysis", "/out/analysis.json", "json"),
        ],
    )
    def test_construct(self, role: str, path: str, format_: str) -> None:
        """Can be constructed with representative roles and formats."""
        a = Artifact(role=role, path=path, format=format_)
        assert a.role == role
        assert a.path == path
        assert a.format == format_


# ===========================================================================
# ToolResult
# ===========================================================================


class TestToolResult:
    """Basic contract for ToolResult (success envelope)."""

    def test_ok_is_true(self) -> None:
        """The ok field can be set to True (unified model requires explicit ok)."""
        result = ToolResult(ok=True, summary="Done")
        assert result.ok is True

    def test_defaults(self) -> None:
        """data / artifacts / warnings default to empty values."""
        result = ToolResult(ok=True, summary="ok")
        assert result.data == {}
        assert result.artifacts == []
        assert result.warnings == []

    def test_with_data_and_artifacts(self) -> None:
        """data, artifacts, and warnings can be set."""
        art = Artifact(role="timeline", path="/out/t.otio", format="otio")
        result = ToolResult(
            ok=True,
            summary="Media inspected",
            data={"fps": 30.0},
            artifacts=[art],
            warnings=["Note"],
        )
        assert result.data["fps"] == 30.0
        assert len(result.artifacts) == 1
        assert result.warnings[0] == "Note"

    def test_ok_can_be_false(self) -> None:
        """Unified ToolResult accepts ok=False (not Literal[True] anymore)."""
        result = ToolResult(ok=False)
        assert result.ok is False


# ===========================================================================
# ToolError / ToolResult failure path
# ===========================================================================


class TestToolError:
    """Basic contract for ToolError."""

    def test_construct(self) -> None:
        """Can hold code / message / hint."""
        err = ToolError(
            code="FILE_NOT_FOUND",
            message="File not found",
            hint="Check the path",
        )
        assert err.code == "FILE_NOT_FOUND"
        assert err.message == "File not found"
        assert err.hint == "Check the path"


class TestToolResultFailure:
    """Basic contract for ToolResult failure path (ok=False, unified model)."""

    def test_ok_is_false(self) -> None:
        """ToolResult(ok=False) must have ok=False."""
        err = ToolError(code="INVALID_INPUT", message="Invalid input", hint="Fix it")
        result = ToolResult(ok=False, error=err)
        assert result.ok is False

    def test_error_structure(self) -> None:
        """The error field stores a ToolError."""
        err = ToolError(
            code="PROBE_FAILED", message="Parse failed", hint="Check ffprobe"
        )
        result = ToolResult(ok=False, error=err)
        assert result.error is not None
        assert result.error.code == "PROBE_FAILED"


# ===========================================================================
# StreamInfo / MediaInfo
# ===========================================================================


class TestStreamInfo:
    """Basic contract for StreamInfo."""

    def test_video_stream(self) -> None:
        """Can hold video stream fields."""
        s = StreamInfo(
            index=0, codec_type="video", codec_name="h264", width=1920, height=1080
        )
        assert s.codec_type == "video"
        assert s.width == 1920

    def test_audio_stream(self) -> None:
        """Can hold audio stream fields."""
        s = StreamInfo(
            index=1, codec_type="audio", codec_name="aac", sample_rate=44100, channels=2
        )
        assert s.codec_type == "audio"
        assert s.sample_rate == 44100
        assert s.channels == 2

    def test_optional_fields_default_none(self) -> None:
        """Optional fields default to None."""
        s = StreamInfo(index=0, codec_type="video")
        assert s.codec_name is None
        assert s.width is None
        assert s.height is None
        assert s.sample_rate is None
        assert s.channels is None

    def test_nb_frames_preserved(self) -> None:
        """ADR-5: StreamInfo.nb_frames holds the ffprobe frame count when supplied.

        architecture-report-20260709-191456.md ADR-5: new Optional[int] field for
        observability (video/audio duration mismatch diagnosis).
        """
        s = StreamInfo(index=0, codec_type="video", nb_frames=530)
        assert s.nb_frames == 530

    def test_nb_frames_defaults_to_none(self) -> None:
        """ADR-5: nb_frames is Optional with default None (backward compatible)."""
        s = StreamInfo(index=0, codec_type="video")
        assert s.nb_frames is None


class TestMediaInfo:
    """Basic contract for MediaInfo."""

    def test_construct_minimal(self) -> None:
        """Can be constructed with path / container / duration=None / streams=[]."""
        mi = MediaInfo(path="/v.mp4", container="mp4", duration=None, streams=[])
        assert mi.path == "/v.mp4"
        assert mi.container == "mp4"
        assert mi.duration is None
        assert mi.streams == []

    def test_with_duration(self) -> None:
        """Can hold a RationalTimeModel as duration."""
        dur = RationalTimeModel(value=90.0, rate=30.0)
        mi = MediaInfo(path="/v.mp4", container="mp4", duration=dur, streams=[])
        assert mi.duration is not None
        assert mi.duration.rate == 30.0

    def test_with_streams(self) -> None:
        """Can hold multiple streams."""
        v = StreamInfo(index=0, codec_type="video", codec_name="h264")
        a = StreamInfo(index=1, codec_type="audio", codec_name="aac")
        mi = MediaInfo(path="/v.mp4", container="mp4", duration=None, streams=[v, a])
        assert len(mi.streams) == 2


# ===========================================================================
# OperationError / ValidationReport (§13.1 DC-AM-003)
# ===========================================================================


class TestOperationError:
    """Basic contract for OperationError."""

    def test_construct(self) -> None:
        """Can hold index / code / message."""
        oe = OperationError(
            index=2, code="TRACK_NOT_FOUND", message="track 5 does not exist"
        )
        assert oe.index == 2
        assert oe.code == "TRACK_NOT_FOUND"
        assert oe.message == "track 5 does not exist"

    @pytest.mark.parametrize(
        "index, code",
        [
            (0, "INVALID_INPUT"),
            (1, "TRACK_NOT_FOUND"),
            (99, "UNSUPPORTED_OPERATION"),
        ],
    )
    def test_various_indices_and_codes(self, index: int, code: str) -> None:
        """Can be constructed with various index / code combinations."""
        oe = OperationError(index=index, code=code, message="error")
        assert oe.index == index
        assert oe.code == code


class TestValidationReport:
    """Basic contract for ValidationReport (§13.1 DC-AM-003 / DC-AM-004)."""

    def test_valid_report(self) -> None:
        """Report when all ops are valid."""
        report = ValidationReport(
            valid=True,
            operation_count=3,
            applied_count=3,
            errors=[],
        )
        assert report.valid is True
        assert report.applied_count == 3
        assert report.errors == []

    def test_invalid_report(self) -> None:
        """Report when an invalid op is present: valid=False, applied_count=0."""
        err = OperationError(
            index=1, code="TRACK_NOT_FOUND", message="track 5 does not exist"
        )
        report = ValidationReport(
            valid=False,
            operation_count=3,
            applied_count=0,
            errors=[err],
        )
        assert report.valid is False
        assert report.applied_count == 0
        assert len(report.errors) == 1

    def test_validate_only_applied_count_is_zero(self) -> None:
        """validate_only: applied_count=0 even when all ops are valid."""
        report = ValidationReport(
            valid=True,
            operation_count=5,
            applied_count=0,
            errors=[],
        )
        assert report.applied_count == 0
        assert report.valid is True

    def test_errors_default_empty(self) -> None:
        """errors defaults to an empty list."""
        report = ValidationReport(valid=True, operation_count=1, applied_count=1)
        assert report.errors == []


# ===========================================================================
# to_otio_time / from_otio_time (§13.1 DC-GP-005)
# ===========================================================================


class TestOtioTimeConversion:
    """Basic contract for OTIO time conversion helpers (in schemas.py)."""

    def test_to_otio_time_returns_rational_time(self) -> None:
        """Converts RationalTimeModel → opentime.RationalTime."""
        import opentimelineio as otio

        rt_model = RationalTimeModel(value=30.0, rate=30.0)
        rt_otio = to_otio_time(rt_model)
        assert isinstance(rt_otio, otio.opentime.RationalTime)
        assert rt_otio.value == 30.0
        assert rt_otio.rate == 30.0

    def test_from_otio_time_returns_model(self) -> None:
        """Converts opentime.RationalTime → RationalTimeModel."""
        import opentimelineio as otio

        rt_otio = otio.opentime.RationalTime(value=24.0, rate=24.0)
        rt_model = from_otio_time(rt_otio)
        assert isinstance(rt_model, RationalTimeModel)
        assert rt_model.value == 24.0
        assert rt_model.rate == 24.0

    @pytest.mark.parametrize(
        "value, rate",
        [
            (0.0, 30.0),
            (90.0, 30.0),
            (48.0, 24.0),
            (1000.0, 1000.0),  # rate for audio-only sources
        ],
    )
    def test_roundtrip(self, value: float, rate: float) -> None:
        """RationalTimeModel → otio → RationalTimeModel preserves values."""
        original = RationalTimeModel(value=value, rate=rate)
        roundtripped = from_otio_time(to_otio_time(original))
        assert roundtripped.value == original.value
        assert roundtripped.rate == original.rate

    def test_rate_is_preserved_not_converted_to_seconds(self) -> None:
        """rate is preserved after conversion (not normalised to a seconds float)."""
        rt_model = RationalTimeModel(value=720.0, rate=24.0)
        rt_otio = to_otio_time(rt_model)
        back = from_otio_time(rt_otio)
        assert back.rate == 24.0
        # Must not have been normalised to 30.0
        assert back.value == 720.0


# ===========================================================================
# full_media_range (§13.1 CR-M-001 DRY extraction; ADR-3 available_range)
# ===========================================================================


class TestFullMediaRange:
    """Contract for full_media_range: builds the whole-asset (0..duration) range."""

    def test_start_time_is_zero(self) -> None:
        """start_time is always 0, at the duration's rate."""
        dur = RationalTimeModel(value=900.0, rate=30.0)
        mi = MediaInfo(path="/v.mp4", container="mp4", duration=dur, streams=[])
        rng = full_media_range(mi)
        assert rng.start_time.value == 0.0
        assert rng.start_time.rate == 30.0

    def test_duration_matches_media_info_duration(self) -> None:
        """duration value/rate equal media_info.duration exactly (no rescaling)."""
        dur = RationalTimeModel(value=1234.5, rate=24.0)
        mi = MediaInfo(path="/v.mp4", container="mp4", duration=dur, streams=[])
        rng = full_media_range(mi)
        assert rng.duration.value == dur.value
        assert rng.duration.rate == dur.rate

    @pytest.mark.parametrize(
        "value, rate",
        [
            (0.0, 30.0),
            (90.0, 29.97),
            (48000.0, 1000.0),  # rate for audio-only sources
        ],
    )
    def test_various_rates(self, value: float, rate: float) -> None:
        """Works for representative fps/rate values, including audio-only 1000 rate."""
        dur = RationalTimeModel(value=value, rate=rate)
        mi = MediaInfo(path="/v.mp4", container="mp4", duration=dur, streams=[])
        rng = full_media_range(mi)
        assert rng.start_time.value == 0.0
        assert rng.start_time.rate == rate
        assert rng.duration.value == value
        assert rng.duration.rate == rate

    def test_raises_value_error_when_duration_is_none(self) -> None:
        """Callers must validate duration is not None before calling this helper."""
        mi = MediaInfo(path="/v.mp4", container="mp4", duration=None, streams=[])
        with pytest.raises(ValueError):
            full_media_range(mi)
