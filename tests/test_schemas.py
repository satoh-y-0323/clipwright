"""test_schemas.py — schemas.py の契約面テスト（Red フェーズ）。

対象:
- RationalTimeModel / TimeRangeModel
- MediaRef / Artifact / ToolResult / ToolError / ToolErrorResult
- StreamInfo / MediaInfo
- OperationError / ValidationReport（§13.1 DC-AM-003）
- to_otio_time / from_otio_time（§13.1 DC-GP-005）

このテストは schemas.py が未実装のため ImportError で失敗する（Red）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# --- Import（schemas.py 未実装のため ImportError が発生する → Red） ---
from clipwright.schemas import (
    Artifact,
    MediaInfo,
    MediaRef,
    OperationError,
    RationalTimeModel,
    StreamInfo,
    TimeRangeModel,
    ToolError,
    ToolErrorResult,
    ToolResult,
    ValidationReport,
    from_otio_time,
    to_otio_time,
)

# ===========================================================================
# RationalTimeModel
# ===========================================================================


class TestRationalTimeModel:
    """RationalTimeModel の基本契約。"""

    def test_construct_basic(self) -> None:
        """value と rate を持てる。"""
        rt = RationalTimeModel(value=30.0, rate=30.0)
        assert rt.value == 30.0
        assert rt.rate == 30.0

    def test_rate_preserved(self) -> None:
        """rate は float として保持される（秒 float 単独で失われない）。"""
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
        """代表的な fps/rate で構築できる。"""
        rt = RationalTimeModel(value=value, rate=rate)
        assert rt.value == value
        assert rt.rate == rate

    def test_json_roundtrip(self) -> None:
        """JSON シリアライズ→復元で rate が保持される。"""
        rt = RationalTimeModel(value=10.0, rate=24.0)
        restored = RationalTimeModel.model_validate_json(rt.model_dump_json())
        assert restored.value == rt.value
        assert restored.rate == rt.rate


# ===========================================================================
# TimeRangeModel
# ===========================================================================


class TestTimeRangeModel:
    """TimeRangeModel の基本契約。"""

    def test_construct(self) -> None:
        """start_time と duration を持てる。"""
        start = RationalTimeModel(value=0.0, rate=30.0)
        dur = RationalTimeModel(value=90.0, rate=30.0)
        tr = TimeRangeModel(start_time=start, duration=dur)
        assert tr.start_time.rate == 30.0
        assert tr.duration.value == 90.0

    def test_json_roundtrip(self) -> None:
        """JSON シリアライズ→復元で値が保持される。"""
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
    """MediaRef の基本契約。"""

    def test_minimal(self) -> None:
        """target_url のみで構築できる。"""
        ref = MediaRef(target_url="/path/to/video.mp4")
        assert ref.target_url == "/path/to/video.mp4"
        assert ref.name is None
        assert ref.available_range is None

    def test_with_all_fields(self) -> None:
        """全フィールドを指定できる。"""
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
    """Artifact の基本契約。"""

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
        """代表的なロール・フォーマットで構築できる。"""
        a = Artifact(role=role, path=path, format=format_)
        assert a.role == role
        assert a.path == path
        assert a.format == format_


# ===========================================================================
# ToolResult
# ===========================================================================


class TestToolResult:
    """ToolResult の基本契約（成功エンベロープ）。"""

    def test_ok_is_true(self) -> None:
        """ok フィールドは常に True。"""
        result = ToolResult(summary="完了しました")
        assert result.ok is True

    def test_defaults(self) -> None:
        """data / artifacts / warnings のデフォルトは空。"""
        result = ToolResult(summary="ok")
        assert result.data == {}
        assert result.artifacts == []
        assert result.warnings == []

    def test_with_data_and_artifacts(self) -> None:
        """data・artifacts・warnings を設定できる。"""
        art = Artifact(role="timeline", path="/out/t.otio", format="otio")
        result = ToolResult(
            summary="メディアをインスペクトしました",
            data={"fps": 30.0},
            artifacts=[art],
            warnings=["注意事項"],
        )
        assert result.data["fps"] == 30.0
        assert len(result.artifacts) == 1
        assert result.warnings[0] == "注意事項"

    def test_ok_cannot_be_false(self) -> None:
        """ok=False を設定できない（Literal[True]）。"""
        with pytest.raises(ValidationError):
            ToolResult(ok=False, summary="should fail")  # type: ignore[arg-type]


# ===========================================================================
# ToolError / ToolErrorResult
# ===========================================================================


class TestToolError:
    """ToolError の基本契約。"""

    def test_construct(self) -> None:
        """code / message / hint を持てる。"""
        err = ToolError(
            code="FILE_NOT_FOUND",
            message="ファイルが見つかりません",
            hint="パスを確認してください",
        )
        assert err.code == "FILE_NOT_FOUND"
        assert err.message == "ファイルが見つかりません"
        assert err.hint == "パスを確認してください"


class TestToolErrorResult:
    """ToolErrorResult の基本契約（失敗エンベロープ）。"""

    def test_ok_is_false(self) -> None:
        """ok フィールドは常に False。"""
        err = ToolError(
            code="INVALID_INPUT", message="不正入力", hint="修正してください"
        )
        result = ToolErrorResult(error=err)
        assert result.ok is False

    def test_ok_cannot_be_true(self) -> None:
        """ok=True を設定できない（Literal[False]）。"""
        err = ToolError(code="INVALID_INPUT", message="x", hint="y")
        with pytest.raises(ValidationError):
            ToolErrorResult(ok=True, error=err)  # type: ignore[arg-type]

    def test_error_structure(self) -> None:
        """error フィールドに ToolError が格納される。"""
        err = ToolError(
            code="PROBE_FAILED", message="パース失敗", hint="ffprobe を確認"
        )
        result = ToolErrorResult(error=err)
        assert result.error.code == "PROBE_FAILED"


# ===========================================================================
# StreamInfo / MediaInfo
# ===========================================================================


class TestStreamInfo:
    """StreamInfo の基本契約。"""

    def test_video_stream(self) -> None:
        """映像ストリームのフィールドを持てる。"""
        s = StreamInfo(
            index=0, codec_type="video", codec_name="h264", width=1920, height=1080
        )
        assert s.codec_type == "video"
        assert s.width == 1920

    def test_audio_stream(self) -> None:
        """音声ストリームのフィールドを持てる。"""
        s = StreamInfo(
            index=1, codec_type="audio", codec_name="aac", sample_rate=44100, channels=2
        )
        assert s.codec_type == "audio"
        assert s.sample_rate == 44100
        assert s.channels == 2

    def test_optional_fields_default_none(self) -> None:
        """オプションフィールドのデフォルトは None。"""
        s = StreamInfo(index=0, codec_type="video")
        assert s.codec_name is None
        assert s.width is None
        assert s.height is None
        assert s.sample_rate is None
        assert s.channels is None


class TestMediaInfo:
    """MediaInfo の基本契約。"""

    def test_construct_minimal(self) -> None:
        """path / container / duration=None / streams=[] で構築できる。"""
        mi = MediaInfo(path="/v.mp4", container="mp4", duration=None, streams=[])
        assert mi.path == "/v.mp4"
        assert mi.container == "mp4"
        assert mi.duration is None
        assert mi.streams == []

    def test_with_duration(self) -> None:
        """duration に RationalTimeModel を持てる。"""
        dur = RationalTimeModel(value=90.0, rate=30.0)
        mi = MediaInfo(path="/v.mp4", container="mp4", duration=dur, streams=[])
        assert mi.duration is not None
        assert mi.duration.rate == 30.0

    def test_with_streams(self) -> None:
        """複数ストリームを持てる。"""
        v = StreamInfo(index=0, codec_type="video", codec_name="h264")
        a = StreamInfo(index=1, codec_type="audio", codec_name="aac")
        mi = MediaInfo(path="/v.mp4", container="mp4", duration=None, streams=[v, a])
        assert len(mi.streams) == 2


# ===========================================================================
# OperationError / ValidationReport（§13.1 DC-AM-003）
# ===========================================================================


class TestOperationError:
    """OperationError の基本契約。"""

    def test_construct(self) -> None:
        """index / code / message を持てる。"""
        oe = OperationError(
            index=2, code="TRACK_NOT_FOUND", message="track 5 が存在しません"
        )
        assert oe.index == 2
        assert oe.code == "TRACK_NOT_FOUND"
        assert oe.message == "track 5 が存在しません"

    @pytest.mark.parametrize(
        "index, code",
        [
            (0, "INVALID_INPUT"),
            (1, "TRACK_NOT_FOUND"),
            (99, "UNSUPPORTED_OPERATION"),
        ],
    )
    def test_various_indices_and_codes(self, index: int, code: str) -> None:
        """様々な index / code で構築できる。"""
        oe = OperationError(index=index, code=code, message="error")
        assert oe.index == index
        assert oe.code == code


class TestValidationReport:
    """ValidationReport の基本契約（§13.1 DC-AM-003 / DC-AM-004）。"""

    def test_valid_report(self) -> None:
        """全 op 有効の場合のレポート。"""
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
        """不正 op がある場合は valid=False・applied_count=0。"""
        err = OperationError(
            index=1, code="TRACK_NOT_FOUND", message="track 5 が存在しません"
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
        """validate_only 時は applied_count=0 で全件有効でも適用しない。"""
        report = ValidationReport(
            valid=True,
            operation_count=5,
            applied_count=0,
            errors=[],
        )
        assert report.applied_count == 0
        assert report.valid is True

    def test_errors_default_empty(self) -> None:
        """errors のデフォルトは空リスト。"""
        report = ValidationReport(valid=True, operation_count=1, applied_count=1)
        assert report.errors == []


# ===========================================================================
# to_otio_time / from_otio_time（§13.1 DC-GP-005）
# ===========================================================================


class TestOtioTimeConversion:
    """OTIO 時間変換ヘルパーの基本契約（schemas.py 配置・このタスクが所有）。"""

    def test_to_otio_time_returns_rational_time(self) -> None:
        """RationalTimeModel → opentime.RationalTime に変換できる。"""
        import opentimelineio as otio

        rt_model = RationalTimeModel(value=30.0, rate=30.0)
        rt_otio = to_otio_time(rt_model)
        assert isinstance(rt_otio, otio.opentime.RationalTime)
        assert rt_otio.value == 30.0
        assert rt_otio.rate == 30.0

    def test_from_otio_time_returns_model(self) -> None:
        """opentime.RationalTime → RationalTimeModel に変換できる。"""
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
            (1000.0, 1000.0),  # 音声のみ素材の rate
        ],
    )
    def test_roundtrip(self, value: float, rate: float) -> None:
        """RationalTimeModel → otio → RationalTimeModel で値が保持される。"""
        original = RationalTimeModel(value=value, rate=rate)
        roundtripped = from_otio_time(to_otio_time(original))
        assert roundtripped.value == original.value
        assert roundtripped.rate == original.rate

    def test_rate_is_preserved_not_converted_to_seconds(self) -> None:
        """変換後も rate は保持される（秒 float に正規化されない）。"""
        rt_model = RationalTimeModel(value=720.0, rate=24.0)
        rt_otio = to_otio_time(rt_model)
        back = from_otio_time(rt_otio)
        assert back.rate == 24.0
        # 秒 float (30.0) にはなっていないこと
        assert back.value == 720.0
