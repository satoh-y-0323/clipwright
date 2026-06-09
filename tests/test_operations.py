"""test_operations.py — operations.py の Red フェーズテスト。

このテストは operations.py が未実装のため ImportError で失敗する（Red）。
機能が未実装であることによる失敗が期待動作。

対象（§6 / §13.1 / §13.5）:
- AddClipOp / AddGapOp / AddMarkerOp（Pydantic 判別共用体、discriminator="op"）
- Operation 型エイリアス
- apply_operations(timeline, ops, validate_only):
    - track はフラット index（0=V1, 1=A1）
    - 範囲外 index → TRACK_NOT_FOUND（§13.1 DC-AS-003 / §13.5 DC-AS-001 再）
    - AddMarkerOp は track 自体に marker を付与（clip 不要・§13.5 DC-GP-001 再）
    - all-or-nothing（§13.1 DC-AM-004）
    - validate_only=True は検証のみ（適用・保存しない、applied_count=0）
    - 未知 op は Pydantic で弾く（UNSUPPORTED_OPERATION 相当）
"""

from __future__ import annotations

from pathlib import Path

import pytest

# --- Import（operations.py 未実装のため ImportError が発生する → Red） ---
from clipwright.operations import (
    AddClipOp,
    AddGapOp,
    AddMarkerOp,
    Operation,
    apply_operations,
)


# ===========================================================================
# AddClipOp（判別共用体メンバー）
# ===========================================================================


class TestAddClipOp:
    """AddClipOp の型契約。"""

    def test_construct_minimal(self) -> None:
        """必須フィールドで構築できる。"""
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        op = AddClipOp(
            op="add_clip",
            media=MediaRef(target_url="/v.mp4"),
            source_range=TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=90.0, rate=30.0),
            ),
        )
        assert op.op == "add_clip"
        assert op.track == 0  # デフォルト

    def test_track_default_is_zero(self) -> None:
        """track のデフォルトは 0（V1）。"""
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        op = AddClipOp(
            op="add_clip",
            media=MediaRef(target_url="/v.mp4"),
            source_range=TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=30.0, rate=30.0),
            ),
        )
        assert op.track == 0

    def test_name_optional(self) -> None:
        """name は省略可能でデフォルト None。"""
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        op = AddClipOp(
            op="add_clip",
            media=MediaRef(target_url="/v.mp4"),
            source_range=TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=30.0, rate=30.0),
            ),
        )
        assert op.name is None

    def test_metadata_optional(self) -> None:
        """metadata は省略可能でデフォルト None。"""
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        op = AddClipOp(
            op="add_clip",
            media=MediaRef(target_url="/v.mp4"),
            source_range=TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=30.0, rate=30.0),
            ),
        )
        assert op.metadata is None

    def test_discriminator_field(self) -> None:
        """op フィールドが 'add_clip' に固定されている（discriminator）。"""
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        op = AddClipOp(
            op="add_clip",
            media=MediaRef(target_url="/v.mp4"),
            source_range=TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=30.0, rate=30.0),
            ),
        )
        assert op.op == "add_clip"


# ===========================================================================
# AddGapOp（判別共用体メンバー）
# ===========================================================================


class TestAddGapOp:
    """AddGapOp の型契約。"""

    def test_construct(self) -> None:
        """必須フィールドで構築できる。"""
        from clipwright.schemas import RationalTimeModel

        op = AddGapOp(
            op="add_gap",
            duration=RationalTimeModel(value=30.0, rate=30.0),
        )
        assert op.op == "add_gap"
        assert op.track == 0

    def test_track_field(self) -> None:
        """track を指定できる。"""
        from clipwright.schemas import RationalTimeModel

        op = AddGapOp(
            op="add_gap",
            track=1,
            duration=RationalTimeModel(value=15.0, rate=30.0),
        )
        assert op.track == 1

    def test_duration_preserved(self) -> None:
        """duration が保持される。"""
        from clipwright.schemas import RationalTimeModel

        op = AddGapOp(
            op="add_gap",
            duration=RationalTimeModel(value=60.0, rate=30.0),
        )
        assert op.duration.value == 60.0
        assert op.duration.rate == 30.0


# ===========================================================================
# AddMarkerOp（判別共用体メンバー）
# ===========================================================================


class TestAddMarkerOp:
    """AddMarkerOp の型契約。"""

    def test_construct(self) -> None:
        """必須フィールドで構築できる。"""
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        op = AddMarkerOp(
            op="add_marker",
            marked_range=TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=1.0, rate=30.0),
            ),
            name="chapter1",
        )
        assert op.op == "add_marker"
        assert op.name == "chapter1"

    def test_color_optional(self) -> None:
        """color は省略可能でデフォルト None。"""
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        op = AddMarkerOp(
            op="add_marker",
            marked_range=TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=1.0, rate=30.0),
            ),
            name="cue",
        )
        assert op.color is None

    def test_metadata_optional(self) -> None:
        """metadata は省略可能でデフォルト None。"""
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        op = AddMarkerOp(
            op="add_marker",
            marked_range=TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=1.0, rate=30.0),
            ),
            name="cue",
        )
        assert op.metadata is None


# ===========================================================================
# Operation 型エイリアス（判別共用体の統合テスト）
# ===========================================================================


class TestOperationUnion:
    """Operation 判別共用体の parse 契約。"""

    def test_parse_add_clip(self) -> None:
        """op='add_clip' を持つ dict は AddClipOp として parse される。"""
        from pydantic import TypeAdapter

        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        adapter: TypeAdapter[Operation] = TypeAdapter(Operation)
        data = {
            "op": "add_clip",
            "track": 0,
            "media": {"target_url": "/v.mp4"},
            "source_range": {
                "start_time": {"value": 0.0, "rate": 30.0},
                "duration": {"value": 30.0, "rate": 30.0},
            },
        }
        op = adapter.validate_python(data)
        assert isinstance(op, AddClipOp)

    def test_parse_add_gap(self) -> None:
        """op='add_gap' を持つ dict は AddGapOp として parse される。"""
        from pydantic import TypeAdapter

        adapter: TypeAdapter[Operation] = TypeAdapter(Operation)
        data = {
            "op": "add_gap",
            "track": 0,
            "duration": {"value": 30.0, "rate": 30.0},
        }
        op = adapter.validate_python(data)
        assert isinstance(op, AddGapOp)

    def test_parse_add_marker(self) -> None:
        """op='add_marker' を持つ dict は AddMarkerOp として parse される。"""
        from pydantic import TypeAdapter

        adapter: TypeAdapter[Operation] = TypeAdapter(Operation)
        data = {
            "op": "add_marker",
            "track": 0,
            "marked_range": {
                "start_time": {"value": 0.0, "rate": 30.0},
                "duration": {"value": 1.0, "rate": 30.0},
            },
            "name": "cue",
        }
        op = adapter.validate_python(data)
        assert isinstance(op, AddMarkerOp)

    def test_unknown_op_raises(self) -> None:
        """未知の op 値は Pydantic の ValidationError になる（UNSUPPORTED_OPERATION 相当）。"""
        from pydantic import TypeAdapter, ValidationError

        adapter: TypeAdapter[Operation] = TypeAdapter(Operation)
        data = {
            "op": "delete_clip",  # 未知の op
            "track": 0,
        }
        with pytest.raises(ValidationError):
            adapter.validate_python(data)


# ===========================================================================
# apply_operations — 正常系
# ===========================================================================


class TestApplyOperationsSuccess:
    """apply_operations の正常系テスト。"""

    def test_apply_single_add_clip(self, tmp_path: Path) -> None:
        """add_clip 1 件を適用すると ValidationReport(valid=True, applied_count=1)。"""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import (
            MediaRef,
            RationalTimeModel,
            TimeRangeModel,
            ValidationReport,
        )

        tl = new_timeline("single_clip")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [
            AddClipOp(
                op="add_clip",
                track=0,
                media=MediaRef(target_url="/v.mp4"),
                source_range=TimeRangeModel(
                    start_time=RationalTimeModel(value=0.0, rate=30.0),
                    duration=RationalTimeModel(value=90.0, rate=30.0),
                ),
            )
        ]
        report = apply_operations(tl, ops, validate_only=False)
        assert isinstance(report, ValidationReport)
        assert report.valid is True
        assert report.applied_count == 1
        assert report.errors == []

    def test_apply_single_add_gap(self, tmp_path: Path) -> None:
        """add_gap 1 件を適用すると applied_count=1。"""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel, ValidationReport

        tl = new_timeline("single_gap")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [AddGapOp(op="add_gap", track=0, duration=RationalTimeModel(value=30.0, rate=30.0))]
        report = apply_operations(tl, ops, validate_only=False)
        assert isinstance(report, ValidationReport)
        assert report.valid is True
        assert report.applied_count == 1

    def test_apply_multiple_ops(self, tmp_path: Path) -> None:
        """複数 op を適用すると applied_count が op 数と一致する。"""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import (
            MediaRef,
            RationalTimeModel,
            TimeRangeModel,
            ValidationReport,
        )

        tl = new_timeline("multi_ops")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops: list[Operation] = [
            AddClipOp(
                op="add_clip",
                track=0,
                media=MediaRef(target_url="/v.mp4"),
                source_range=TimeRangeModel(
                    start_time=RationalTimeModel(value=0.0, rate=30.0),
                    duration=RationalTimeModel(value=90.0, rate=30.0),
                ),
            ),
            AddGapOp(
                op="add_gap",
                track=1,
                duration=RationalTimeModel(value=30.0, rate=30.0),
            ),
            AddMarkerOp(
                op="add_marker",
                track=0,
                marked_range=TimeRangeModel(
                    start_time=RationalTimeModel(value=0.0, rate=30.0),
                    duration=RationalTimeModel(value=1.0, rate=30.0),
                ),
                name="start",
            ),
        ]
        report = apply_operations(tl, ops, validate_only=False)
        assert report.valid is True
        assert report.applied_count == 3

    def test_apply_marker_to_empty_track_succeeds(self, tmp_path: Path) -> None:
        """空トラックへの add_marker は成功する（§13.5 DC-GP-001 再）。"""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel, TimeRangeModel, ValidationReport

        tl = new_timeline("empty_marker")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [
            AddMarkerOp(
                op="add_marker",
                track=0,
                marked_range=TimeRangeModel(
                    start_time=RationalTimeModel(value=0.0, rate=30.0),
                    duration=RationalTimeModel(value=1.0, rate=30.0),
                ),
                name="empty_track_cue",
            )
        ]
        report = apply_operations(tl, ops, validate_only=False)
        assert isinstance(report, ValidationReport)
        assert report.valid is True
        assert report.applied_count == 1

    def test_operation_count_equals_ops_length(self, tmp_path: Path) -> None:
        """report.operation_count は ops リストの件数に一致する。"""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("op_count")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [
            AddGapOp(op="add_gap", track=0, duration=RationalTimeModel(value=10.0, rate=30.0)),
            AddGapOp(op="add_gap", track=1, duration=RationalTimeModel(value=10.0, rate=30.0)),
        ]
        report = apply_operations(tl, ops, validate_only=False)
        assert report.operation_count == 2


# ===========================================================================
# apply_operations — track 範囲外 → TRACK_NOT_FOUND（§13.1/§13.5）
# ===========================================================================


class TestApplyOperationsTrackNotFound:
    """apply_operations の TRACK_NOT_FOUND 検証テスト（§13.1 DC-AS-003）。"""

    def test_out_of_range_track_returns_invalid(self, tmp_path: Path) -> None:
        """範囲外 track index は valid=False の ValidationReport を返す。"""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel, ValidationReport

        tl = new_timeline("out_of_range")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [
            # track=99 は存在しない（V1=0, A1=1 の 2 本しかない）
            AddGapOp(
                op="add_gap",
                track=99,
                duration=RationalTimeModel(value=30.0, rate=30.0),
            )
        ]
        report = apply_operations(tl, ops, validate_only=False)
        assert isinstance(report, ValidationReport)
        assert report.valid is False

    def test_out_of_range_track_applied_count_is_zero(self, tmp_path: Path) -> None:
        """all-or-nothing: 範囲外 track があれば applied_count=0（§13.1 DC-AM-004）。"""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("no_apply")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [
            AddGapOp(
                op="add_gap",
                track=99,
                duration=RationalTimeModel(value=30.0, rate=30.0),
            )
        ]
        report = apply_operations(tl, ops, validate_only=False)
        assert report.applied_count == 0

    def test_error_contains_track_not_found_code(self, tmp_path: Path) -> None:
        """エラーの code に TRACK_NOT_FOUND が含まれる（§13.1 DC-AS-003）。"""
        from clipwright.errors import ErrorCode
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("error_code")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [
            AddGapOp(
                op="add_gap",
                track=99,
                duration=RationalTimeModel(value=30.0, rate=30.0),
            )
        ]
        report = apply_operations(tl, ops, validate_only=False)
        assert len(report.errors) >= 1
        codes = [e.code for e in report.errors]
        assert ErrorCode.TRACK_NOT_FOUND in codes

    def test_error_index_points_to_failing_op(self, tmp_path: Path) -> None:
        """OperationError.index は失敗した op の位置（0 始まり）を示す。"""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("error_index")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops: list[Operation] = [
            # 0 番は有効
            AddClipOp(
                op="add_clip",
                track=0,
                media=MediaRef(target_url="/v.mp4"),
                source_range=TimeRangeModel(
                    start_time=RationalTimeModel(value=0.0, rate=30.0),
                    duration=RationalTimeModel(value=30.0, rate=30.0),
                ),
            ),
            # 1 番は範囲外 track
            AddGapOp(
                op="add_gap",
                track=99,
                duration=RationalTimeModel(value=30.0, rate=30.0),
            ),
        ]
        report = apply_operations(tl, ops, validate_only=False)
        assert report.valid is False
        error_indices = [e.index for e in report.errors]
        assert 1 in error_indices


# ===========================================================================
# apply_operations — all-or-nothing（§13.1 DC-AM-004）
# ===========================================================================


class TestApplyOperationsAllOrNothing:
    """all-or-nothing セマンティクスのテスト（§13.1 DC-AM-004）。"""

    def test_partial_invalid_applies_nothing(self, tmp_path: Path) -> None:
        """1 op でも不正なら timeline にクリップが追加されない。"""
        from clipwright.otio_utils import load_timeline, new_timeline, save_timeline, summarize_timeline
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("all_or_nothing")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops: list[Operation] = [
            # 有効な op
            AddClipOp(
                op="add_clip",
                track=0,
                media=MediaRef(target_url="/v.mp4"),
                source_range=TimeRangeModel(
                    start_time=RationalTimeModel(value=0.0, rate=30.0),
                    duration=RationalTimeModel(value=30.0, rate=30.0),
                ),
            ),
            # 無効な op（範囲外 track）
            AddGapOp(
                op="add_gap",
                track=99,
                duration=RationalTimeModel(value=30.0, rate=30.0),
            ),
        ]
        report = apply_operations(tl, ops, validate_only=False)
        assert report.valid is False
        # 有効な op も適用されていない（timeline は変更なし）
        summary = summarize_timeline(tl)
        assert summary["clip_count"] == 0

    def test_all_valid_applies_all(self, tmp_path: Path) -> None:
        """全 op が有効なら全て適用される。"""
        from clipwright.otio_utils import new_timeline, save_timeline, summarize_timeline
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("all_valid")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops: list[Operation] = [
            AddClipOp(
                op="add_clip",
                track=0,
                media=MediaRef(target_url="/v.mp4"),
                source_range=TimeRangeModel(
                    start_time=RationalTimeModel(value=0.0, rate=30.0),
                    duration=RationalTimeModel(value=30.0, rate=30.0),
                ),
            ),
            AddGapOp(
                op="add_gap",
                track=1,
                duration=RationalTimeModel(value=30.0, rate=30.0),
            ),
        ]
        report = apply_operations(tl, ops, validate_only=False)
        assert report.valid is True
        assert report.applied_count == 2
        summary = summarize_timeline(tl)
        assert summary["clip_count"] == 1
        assert summary["gap_count"] == 1


# ===========================================================================
# apply_operations — validate_only=True
# ===========================================================================


class TestApplyOperationsValidateOnly:
    """validate_only=True のテスト（適用・保存しない）。"""

    def test_validate_only_returns_valid_true(self, tmp_path: Path) -> None:
        """全 op が有効なら valid=True を返す。"""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("validate_only")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [AddGapOp(op="add_gap", track=0, duration=RationalTimeModel(value=30.0, rate=30.0))]
        report = apply_operations(tl, ops, validate_only=True)
        assert report.valid is True

    def test_validate_only_applied_count_is_zero(self, tmp_path: Path) -> None:
        """validate_only=True なら applied_count=0（適用しない・§13.1 DC-AM-003）。"""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("validate_count")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [AddGapOp(op="add_gap", track=0, duration=RationalTimeModel(value=30.0, rate=30.0))]
        report = apply_operations(tl, ops, validate_only=True)
        assert report.applied_count == 0

    def test_validate_only_does_not_modify_timeline(self, tmp_path: Path) -> None:
        """validate_only=True なら timeline に変更が加わらない。"""
        from clipwright.otio_utils import new_timeline, save_timeline, summarize_timeline
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("no_modify")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [AddGapOp(op="add_gap", track=0, duration=RationalTimeModel(value=30.0, rate=30.0))]
        apply_operations(tl, ops, validate_only=True)
        summary = summarize_timeline(tl)
        assert summary["gap_count"] == 0  # timeline は変更なし

    def test_validate_only_with_invalid_op(self, tmp_path: Path) -> None:
        """validate_only=True でも不正 op は valid=False として検出される。"""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("validate_invalid")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [
            AddGapOp(
                op="add_gap",
                track=99,
                duration=RationalTimeModel(value=30.0, rate=30.0),
            )
        ]
        report = apply_operations(tl, ops, validate_only=True)
        assert report.valid is False
        assert report.applied_count == 0

    def test_validate_only_operation_count_set(self, tmp_path: Path) -> None:
        """validate_only=True でも operation_count は ops 長に一致する。"""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("val_op_count")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [
            AddGapOp(op="add_gap", track=0, duration=RationalTimeModel(value=10.0, rate=30.0)),
            AddGapOp(op="add_gap", track=1, duration=RationalTimeModel(value=10.0, rate=30.0)),
        ]
        report = apply_operations(tl, ops, validate_only=True)
        assert report.operation_count == 2


# ===========================================================================
# apply_operations — track フラット index 動作確認
# ===========================================================================


class TestApplyOperationsTrackIndex:
    """フラット index（track=0→V1, track=1→A1）の動作テスト（§13.5 DC-AS-001 再）。"""

    def test_track0_targets_video(self, tmp_path: Path) -> None:
        """track=0 への add_clip は V1（video track）に追加される。"""
        import opentimelineio as otio

        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("track0_video")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [
            AddClipOp(
                op="add_clip",
                track=0,
                media=MediaRef(target_url="/v.mp4"),
                source_range=TimeRangeModel(
                    start_time=RationalTimeModel(value=0.0, rate=30.0),
                    duration=RationalTimeModel(value=30.0, rate=30.0),
                ),
            )
        ]
        apply_operations(tl, ops, validate_only=False)
        # track=0 は V1（kind=Video）
        video_track = tl.tracks[0]
        assert video_track.kind == otio.schema.TrackKind.Video
        assert len(video_track) == 1

    def test_track1_targets_audio(self, tmp_path: Path) -> None:
        """track=1 への add_gap は A1（audio track）に追加される。"""
        import opentimelineio as otio

        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("track1_audio")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [
            AddGapOp(
                op="add_gap",
                track=1,
                duration=RationalTimeModel(value=30.0, rate=30.0),
            )
        ]
        apply_operations(tl, ops, validate_only=False)
        # track=1 は A1（kind=Audio）
        audio_track = tl.tracks[1]
        assert audio_track.kind == otio.schema.TrackKind.Audio
        assert len(audio_track) == 1

    def test_track2_out_of_range(self, tmp_path: Path) -> None:
        """track=2 は存在しない（V1=0/A1=1 の 2 本のみ）→ TRACK_NOT_FOUND。"""
        from clipwright.errors import ErrorCode
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("track2_oor")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [
            AddGapOp(
                op="add_gap",
                track=2,
                duration=RationalTimeModel(value=30.0, rate=30.0),
            )
        ]
        report = apply_operations(tl, ops, validate_only=False)
        assert report.valid is False
        codes = [e.code for e in report.errors]
        assert ErrorCode.TRACK_NOT_FOUND in codes


# ===========================================================================
# apply_operations — AddMarkerOp が track 自体に marker を付与（§13.5 DC-GP-001 再）
# ===========================================================================


class TestApplyOperationsMarkerOnTrack:
    """AddMarkerOp が track（item=Track）に marker を付与する契約（§13.5 DC-GP-001 再）。"""

    def test_marker_added_to_track_not_clip(self, tmp_path: Path) -> None:
        """AddMarkerOp の marker は track.markers に追加される（clip.markers ではない）。"""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("marker_on_track")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [
            AddMarkerOp(
                op="add_marker",
                track=0,
                marked_range=TimeRangeModel(
                    start_time=RationalTimeModel(value=0.0, rate=30.0),
                    duration=RationalTimeModel(value=1.0, rate=30.0),
                ),
                name="track_marker",
            )
        ]
        apply_operations(tl, ops, validate_only=False)
        # marker は track 自体に付与されている
        video_track = tl.tracks[0]
        assert len(video_track.markers) == 1
        assert video_track.markers[0].name == "track_marker"

    def test_marker_on_empty_track_valid(self, tmp_path: Path) -> None:
        """空トラックへの AddMarkerOp は valid=True・applied_count=1（§13.5 DC-GP-001 再）。"""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("marker_empty")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        assert len(tl.tracks[0]) == 0  # V1 は空

        ops = [
            AddMarkerOp(
                op="add_marker",
                track=0,
                marked_range=TimeRangeModel(
                    start_time=RationalTimeModel(value=0.0, rate=30.0),
                    duration=RationalTimeModel(value=1.0, rate=30.0),
                ),
                name="empty_ok",
            )
        ]
        report = apply_operations(tl, ops, validate_only=False)
        assert report.valid is True
        assert report.applied_count == 1
