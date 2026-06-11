"""test_operations.py — Red phase tests for operations.py.

This test file expects ImportError because operations.py is not yet implemented (Red).
Failures due to unimplemented features are the expected behaviour.

Target (§6 / §13.1 / §13.5):
- AddClipOp / AddGapOp / AddMarkerOp (Pydantic discriminated union, discriminator="op")
- Operation type alias
- apply_operations(timeline, ops, validate_only):
    - track uses flat index (0=V1, 1=A1)
    - Out-of-range index → TRACK_NOT_FOUND (§13.1 DC-AS-003 / §13.5 DC-AS-001 re)
    - AddMarkerOp attaches a marker to the track (no clip required; §13.5 DC-GP-001 re)
    - all-or-nothing (§13.1 DC-AM-004)
    - validate_only=True validates only (does not apply or save; applied_count=0)
    - Unknown op is rejected by Pydantic (UNSUPPORTED_OPERATION equivalent)
"""

from __future__ import annotations

from pathlib import Path

import pytest

# --- Import (operations.py not yet implemented → ImportError expected → Red) ---
from clipwright.operations import (
    AddClipOp,
    AddGapOp,
    AddMarkerOp,
    Operation,
    apply_operations,
)

# ===========================================================================
# AddClipOp (discriminated union member)
# ===========================================================================


class TestAddClipOp:
    """Type contract for AddClipOp."""

    def test_construct_minimal(self) -> None:
        """Can be constructed with required fields only."""
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
        assert op.track == 0  # default

    def test_track_default_is_zero(self) -> None:
        """Default value of track is 0 (V1)."""
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
        """name is optional and defaults to None."""
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
        """metadata is optional and defaults to None."""
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
        """The op field is fixed to 'add_clip' (discriminator)."""
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
# AddGapOp (discriminated union member)
# ===========================================================================


class TestAddGapOp:
    """Type contract for AddGapOp."""

    def test_construct(self) -> None:
        """Can be constructed with required fields only."""
        from clipwright.schemas import RationalTimeModel

        op = AddGapOp(
            op="add_gap",
            duration=RationalTimeModel(value=30.0, rate=30.0),
        )
        assert op.op == "add_gap"
        assert op.track == 0

    def test_track_field(self) -> None:
        """track can be specified."""
        from clipwright.schemas import RationalTimeModel

        op = AddGapOp(
            op="add_gap",
            track=1,
            duration=RationalTimeModel(value=15.0, rate=30.0),
        )
        assert op.track == 1

    def test_duration_preserved(self) -> None:
        """duration is preserved."""
        from clipwright.schemas import RationalTimeModel

        op = AddGapOp(
            op="add_gap",
            duration=RationalTimeModel(value=60.0, rate=30.0),
        )
        assert op.duration.value == 60.0
        assert op.duration.rate == 30.0


# ===========================================================================
# AddMarkerOp (discriminated union member)
# ===========================================================================


class TestAddMarkerOp:
    """Type contract for AddMarkerOp."""

    def test_construct(self) -> None:
        """Can be constructed with required fields only."""
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
        """color is optional and defaults to None."""
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
        """metadata is optional and defaults to None."""
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
# Operation type alias (discriminated union integration test)
# ===========================================================================


class TestOperationUnion:
    """Parse contract for the Operation discriminated union."""

    def test_parse_add_clip(self) -> None:
        """A dict with op='add_clip' is parsed as AddClipOp."""
        from pydantic import TypeAdapter

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
        """A dict with op='add_gap' is parsed as AddGapOp."""
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
        """A dict with op='add_marker' is parsed as AddMarkerOp."""
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
        """An unknown op value raises a Pydantic ValidationError
        (UNSUPPORTED_OPERATION equivalent)."""
        from pydantic import TypeAdapter, ValidationError

        adapter: TypeAdapter[Operation] = TypeAdapter(Operation)
        data = {
            "op": "delete_clip",  # unknown op
            "track": 0,
        }
        with pytest.raises(ValidationError):
            adapter.validate_python(data)


# ===========================================================================
# apply_operations — success path
# ===========================================================================


class TestApplyOperationsSuccess:
    """Success path tests for apply_operations."""

    def test_apply_single_add_clip(self, tmp_path: Path) -> None:
        """One add_clip returns ValidationReport(valid=True, applied_count=1)."""
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
        """Applying one add_gap returns applied_count=1."""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel, ValidationReport

        tl = new_timeline("single_gap")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [
            AddGapOp(
                op="add_gap", track=0, duration=RationalTimeModel(value=30.0, rate=30.0)
            )
        ]
        report = apply_operations(tl, ops, validate_only=False)
        assert isinstance(report, ValidationReport)
        assert report.valid is True
        assert report.applied_count == 1

    def test_apply_multiple_ops(self, tmp_path: Path) -> None:
        """Applying multiple ops returns applied_count matching the number of ops."""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import (
            MediaRef,
            RationalTimeModel,
            TimeRangeModel,
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
        """add_marker on an empty track succeeds (§13.5 DC-GP-001 re)."""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import (
            RationalTimeModel,
            TimeRangeModel,
            ValidationReport,
        )

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
        """report.operation_count matches the length of the ops list."""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("op_count")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [
            AddGapOp(
                op="add_gap", track=0, duration=RationalTimeModel(value=10.0, rate=30.0)
            ),
            AddGapOp(
                op="add_gap", track=1, duration=RationalTimeModel(value=10.0, rate=30.0)
            ),
        ]
        report = apply_operations(tl, ops, validate_only=False)
        assert report.operation_count == 2


# ===========================================================================
# apply_operations — out-of-range track → TRACK_NOT_FOUND (§13.1/§13.5)
# ===========================================================================


class TestApplyOperationsTrackNotFound:
    """TRACK_NOT_FOUND validation tests for apply_operations (§13.1 DC-AS-003)."""

    def test_out_of_range_track_returns_invalid(self, tmp_path: Path) -> None:
        """An out-of-range track index returns a ValidationReport with valid=False."""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel, ValidationReport

        tl = new_timeline("out_of_range")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [
            # track=99 does not exist (only V1=0 and A1=1)
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
        """all-or-nothing: out-of-range track → applied_count=0 (§13.1 DC-AM-004)."""
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
        """The error code contains TRACK_NOT_FOUND (§13.1 DC-AS-003)."""
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
        """OperationError.index points to the position (0-based) of the failing op."""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("error_index")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops: list[Operation] = [
            # index 0: valid
            AddClipOp(
                op="add_clip",
                track=0,
                media=MediaRef(target_url="/v.mp4"),
                source_range=TimeRangeModel(
                    start_time=RationalTimeModel(value=0.0, rate=30.0),
                    duration=RationalTimeModel(value=30.0, rate=30.0),
                ),
            ),
            # index 1: out-of-range track
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
# apply_operations — all-or-nothing (§13.1 DC-AM-004)
# ===========================================================================


class TestApplyOperationsAllOrNothing:
    """all-or-nothing semantics tests (§13.1 DC-AM-004)."""

    def test_partial_invalid_applies_nothing(self, tmp_path: Path) -> None:
        """If any op is invalid, no clips are added to the timeline."""
        from clipwright.otio_utils import (
            new_timeline,
            save_timeline,
            summarize_timeline,
        )
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("all_or_nothing")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops: list[Operation] = [
            # valid op
            AddClipOp(
                op="add_clip",
                track=0,
                media=MediaRef(target_url="/v.mp4"),
                source_range=TimeRangeModel(
                    start_time=RationalTimeModel(value=0.0, rate=30.0),
                    duration=RationalTimeModel(value=30.0, rate=30.0),
                ),
            ),
            # invalid op (out-of-range track)
            AddGapOp(
                op="add_gap",
                track=99,
                duration=RationalTimeModel(value=30.0, rate=30.0),
            ),
        ]
        report = apply_operations(tl, ops, validate_only=False)
        assert report.valid is False
        # The valid op is also not applied (timeline unchanged)
        summary = summarize_timeline(tl)
        assert summary["clip_count"] == 0

    def test_all_valid_applies_all(self, tmp_path: Path) -> None:
        """All ops are applied when all are valid."""
        from clipwright.otio_utils import (
            new_timeline,
            save_timeline,
            summarize_timeline,
        )
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
    """Tests for validate_only=True (validates only, does not apply or save)."""

    def test_validate_only_returns_valid_true(self, tmp_path: Path) -> None:
        """Returns valid=True when all ops are valid."""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("validate_only")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [
            AddGapOp(
                op="add_gap", track=0, duration=RationalTimeModel(value=30.0, rate=30.0)
            )
        ]
        report = apply_operations(tl, ops, validate_only=True)
        assert report.valid is True

    def test_validate_only_applied_count_is_zero(self, tmp_path: Path) -> None:
        """validate_only=True gives applied_count=0 (not applied, §13.1 DC-AM-003)."""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("validate_count")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [
            AddGapOp(
                op="add_gap", track=0, duration=RationalTimeModel(value=30.0, rate=30.0)
            )
        ]
        report = apply_operations(tl, ops, validate_only=True)
        assert report.applied_count == 0

    def test_validate_only_does_not_modify_timeline(self, tmp_path: Path) -> None:
        """validate_only=True does not modify the timeline."""
        from clipwright.otio_utils import (
            new_timeline,
            save_timeline,
            summarize_timeline,
        )
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("no_modify")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [
            AddGapOp(
                op="add_gap", track=0, duration=RationalTimeModel(value=30.0, rate=30.0)
            )
        ]
        apply_operations(tl, ops, validate_only=True)
        summary = summarize_timeline(tl)
        assert summary["gap_count"] == 0  # timeline unchanged

    def test_validate_only_with_invalid_op(self, tmp_path: Path) -> None:
        """validate_only=True still detects invalid ops as valid=False."""
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
        """validate_only=True still sets operation_count to the length of ops."""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("val_op_count")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        ops = [
            AddGapOp(
                op="add_gap", track=0, duration=RationalTimeModel(value=10.0, rate=30.0)
            ),
            AddGapOp(
                op="add_gap", track=1, duration=RationalTimeModel(value=10.0, rate=30.0)
            ),
        ]
        report = apply_operations(tl, ops, validate_only=True)
        assert report.operation_count == 2


# ===========================================================================
# apply_operations — flat track index behaviour
# ===========================================================================


class TestApplyOperationsTrackIndex:
    """Flat index tests (track=0→V1, track=1→A1) (§13.5 DC-AS-001 re)."""

    def test_track0_targets_video(self, tmp_path: Path) -> None:
        """add_clip to track=0 is appended to V1 (video track)."""
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
        # track=0 is V1 (kind=Video)
        video_track = tl.tracks[0]
        assert video_track.kind == otio.schema.TrackKind.Video
        assert len(video_track) == 1

    def test_track1_targets_audio(self, tmp_path: Path) -> None:
        """add_gap to track=1 is appended to A1 (audio track)."""
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
        # track=1 is A1 (kind=Audio)
        audio_track = tl.tracks[1]
        assert audio_track.kind == otio.schema.TrackKind.Audio
        assert len(audio_track) == 1

    def test_track2_out_of_range(self, tmp_path: Path) -> None:
        """track=2 does not exist (only V1=0 and A1=1) → TRACK_NOT_FOUND."""
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
# apply_operations — AddMarkerOp attaches to the track itself (§13.5 DC-GP-001 re)
# ===========================================================================


class TestApplyOperationsMarkerOnTrack:
    """AddMarkerOp attaches a marker to the track (item=Track) contract
    (§13.5 DC-GP-001 re)."""

    def test_marker_added_to_track_not_clip(self, tmp_path: Path) -> None:
        """AddMarkerOp marker is added to track.markers (not clip.markers)."""
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
        # marker is attached to the track itself
        video_track = tl.tracks[0]
        assert len(video_track.markers) == 1
        assert video_track.markers[0].name == "track_marker"

    def test_marker_on_empty_track_valid(self, tmp_path: Path) -> None:
        """AddMarkerOp on an empty track returns valid=True / applied_count=1
        (§13.5 DC-GP-001 re)."""
        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("marker_empty")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        assert len(tl.tracks[0]) == 0  # V1 is empty

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


# ===========================================================================
# M-5 / F-09 regression tests:
# AddClipOp.metadata / AddMarkerOp.metadata accepts dict[str, Any]
# (type change: dict | None  →  dict[str, Any] | None)
#
# [Why Red is difficult]
# This is a type annotation change only; Pydantic runtime behaviour is unchanged.
# dict values were already accepted before the type change, so writing the test
# first will pass immediately (not Red).
# [Alternative verification approach]
# 1. Runtime regression tests (this class): verify that passing a dict[str, Any]
#    value to metadata correctly builds the model and preserves the value.
# 2. mypy verification: after the developer removes `type: ignore[type-arg]` and
#    changes to `dict[str, Any] | None`, confirm that `uv run mypy` shows no errors.
#    The pre-change type (`dict` only) is confirmed to cause a mypy strict [type-arg]
#    error.
# ===========================================================================


class TestMetadataDictStrAny:
    """M-5 / F-09: regression tests for metadata accepting dict[str, Any] | None."""

    def test_add_clip_op_accepts_flat_dict(self) -> None:
        """AddClipOp metadata accepts a flat dict and the model is built correctly."""
        from typing import Any

        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        meta: dict[str, Any] = {"tool": "clipwright", "version": "1.0", "count": 3}
        op = AddClipOp(
            op="add_clip",
            media=MediaRef(target_url="/v.mp4"),
            source_range=TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=90.0, rate=30.0),
            ),
            metadata=meta,
        )
        assert op.metadata is not None
        assert op.metadata["tool"] == "clipwright"
        assert op.metadata["version"] == "1.0"
        assert op.metadata["count"] == 3

    def test_add_clip_op_accepts_nested_dict(self) -> None:
        """AddClipOp metadata accepts a nested dict and the model is built correctly."""
        from typing import Any

        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        meta: dict[str, Any] = {
            "clipwright": {
                "tool": "silence-detect",
                "confidence": 0.95,
                "flags": ["flag_a", "flag_b"],
            }
        }
        op = AddClipOp(
            op="add_clip",
            media=MediaRef(target_url="/v.mp4"),
            source_range=TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=30.0, rate=30.0),
            ),
            metadata=meta,
        )
        assert op.metadata is not None
        assert op.metadata["clipwright"]["tool"] == "silence-detect"
        assert op.metadata["clipwright"]["confidence"] == 0.95

    def test_add_clip_op_metadata_none_by_default(self) -> None:
        """AddClipOp metadata defaults to None (preserved after type change)."""
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

    def test_add_marker_op_accepts_flat_dict(self) -> None:
        """AddMarkerOp metadata accepts a flat dict and the model is built correctly."""
        from typing import Any

        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        meta: dict[str, Any] = {"kind": "chapter", "index": 1, "enabled": True}
        op = AddMarkerOp(
            op="add_marker",
            marked_range=TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=1.0, rate=30.0),
            ),
            name="chapter1",
            metadata=meta,
        )
        assert op.metadata is not None
        assert op.metadata["kind"] == "chapter"
        assert op.metadata["index"] == 1
        assert op.metadata["enabled"] is True

    def test_add_marker_op_accepts_nested_dict(self) -> None:
        """AddMarkerOp metadata accepts a nested dict and builds the model correctly."""
        from typing import Any

        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        meta: dict[str, Any] = {
            "clipwright": {
                "tool": "scene-detect",
                "version": "2.0",
            }
        }
        op = AddMarkerOp(
            op="add_marker",
            marked_range=TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=1.0, rate=30.0),
            ),
            name="scene_start",
            metadata=meta,
        )
        assert op.metadata is not None
        assert op.metadata["clipwright"]["tool"] == "scene-detect"

    def test_add_marker_op_metadata_none_by_default(self) -> None:
        """AddMarkerOp metadata defaults to None (preserved after type change)."""
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

    def test_add_clip_op_metadata_passed_to_apply_operations(
        self, tmp_path: Path
    ) -> None:
        """Passing dict metadata to AddClipOp and calling apply_operations succeeds."""
        from typing import Any

        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("meta_apply_clip")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        meta: dict[str, Any] = {"tool": "clipwright", "version": "1.0"}
        ops = [
            AddClipOp(
                op="add_clip",
                track=0,
                media=MediaRef(target_url="/v.mp4"),
                source_range=TimeRangeModel(
                    start_time=RationalTimeModel(value=0.0, rate=30.0),
                    duration=RationalTimeModel(value=30.0, rate=30.0),
                ),
                metadata=meta,
            )
        ]
        report = apply_operations(tl, ops, validate_only=False)
        assert report.valid is True
        assert report.applied_count == 1

    def test_add_marker_op_metadata_passed_to_apply_operations(
        self, tmp_path: Path
    ) -> None:
        """Passing dict metadata to AddMarkerOp then apply_operations succeeds."""
        from typing import Any

        from clipwright.otio_utils import new_timeline, save_timeline
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("meta_apply_marker")
        path = str(tmp_path / "tl.otio")
        save_timeline(tl, path)

        meta: dict[str, Any] = {"kind": "chapter", "index": 0}
        ops = [
            AddMarkerOp(
                op="add_marker",
                track=0,
                marked_range=TimeRangeModel(
                    start_time=RationalTimeModel(value=0.0, rate=30.0),
                    duration=RationalTimeModel(value=1.0, rate=30.0),
                ),
                name="start",
                metadata=meta,
            )
        ]
        report = apply_operations(tl, ops, validate_only=False)
        assert report.valid is True
        assert report.applied_count == 1
