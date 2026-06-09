"""operations.py — 宣言的編集オペレーション型と適用ロジック。

Pydantic の判別共用体（discriminated union）で操作を型定義し、
apply_operations が all-or-nothing でタイムラインに適用する。

この語彙が detect 系ツールの共通インターフェースになる。
（スペック §4.2 ドッグフーディング前提）
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import opentimelineio as otio
from pydantic import BaseModel, Field

from clipwright.errors import ErrorCode
from clipwright.otio_utils import add_clip, add_gap, add_marker
from clipwright.schemas import (
    MediaRef,
    OperationError,
    RationalTimeModel,
    TimeRangeModel,
    ValidationReport,
)

# ===========================================================================
# オペレーション型（判別共用体メンバー）
# ===========================================================================


class AddClipOp(BaseModel):
    """クリップをトラックに追加するオペレーション。

    track はフラット index（0=V1, 1=A1）。
    metadata: OTIO メタデータは任意キー・任意値の辞書のため Any を使用。
    将来的なサイズ・ネスト上限の検討は別タスクとする。
    """

    op: Literal["add_clip"]
    track: int = 0
    media: MediaRef
    source_range: TimeRangeModel
    name: str | None = None
    metadata: dict[str, Any] | None = None


class AddGapOp(BaseModel):
    """ギャップをトラックに追加するオペレーション。

    track はフラット index（0=V1, 1=A1）。
    """

    op: Literal["add_gap"]
    track: int = 0
    duration: RationalTimeModel


class AddMarkerOp(BaseModel):
    """マーカーをトラック自体に付与するオペレーション（§13.5 DC-GP-001 再）。

    track はフラット index（0=V1, 1=A1）。
    clip の存在を要求しない（空トラックも成功）。
    metadata: OTIO メタデータは任意キー・任意値の辞書のため Any を使用。
    将来的なサイズ・ネスト上限の検討は別タスクとする。
    """

    op: Literal["add_marker"]
    track: int = 0
    marked_range: TimeRangeModel
    name: str
    color: str | None = None
    metadata: dict[str, Any] | None = None


# 判別共用体（discriminator="op"）
Operation = Annotated[
    AddClipOp | AddGapOp | AddMarkerOp,
    Field(discriminator="op"),
]

# ===========================================================================
# apply_operations — all-or-nothing（§13.1 DC-AM-004）
# ===========================================================================


def apply_operations(
    timeline: otio.schema.Timeline,
    ops: list[AddClipOp | AddGapOp | AddMarkerOp],
    *,
    validate_only: bool,
) -> ValidationReport:
    """ops を timeline に適用する（all-or-nothing）。

    まず全 op を検証し、1件でも不正なら一切適用せず
    ValidationReport(valid=False, applied_count=0, errors=[...]) を返す。
    全件有効なときのみ全 op を適用し applied_count=len(ops) を返す。

    validate_only=True の場合は検証のみ（適用・保存しない、applied_count=0）。
    track はフラット index（0始まり）で解決する。範囲外は TRACK_NOT_FOUND。
    """
    operation_count = len(ops)
    errors: list[OperationError] = []
    track_count = len(timeline.tracks)

    # --- 検証フェーズ ---
    for i, op in enumerate(ops):
        if op.track < 0 or op.track >= track_count:
            errors.append(
                OperationError(
                    index=i,
                    code=ErrorCode.TRACK_NOT_FOUND,
                    message=(
                        f"track {op.track} が存在しません。"
                        f"既存トラック数は {track_count} です。"
                        f"track を 0..{track_count - 1} で指定してください"
                    ),
                )
            )

    if errors:
        return ValidationReport(
            valid=False,
            operation_count=operation_count,
            applied_count=0,
            errors=errors,
        )

    # validate_only の場合は検証のみで返す
    if validate_only:
        return ValidationReport(
            valid=True,
            operation_count=operation_count,
            applied_count=0,
            errors=[],
        )

    # --- 適用フェーズ ---
    for op in ops:
        track = timeline.tracks[op.track]
        if isinstance(op, AddClipOp):
            add_clip(
                track,
                op.media,
                op.source_range,
                name=op.name,
                metadata=op.metadata,
            )
        elif isinstance(op, AddGapOp):
            add_gap(track, op.duration)
        elif isinstance(op, AddMarkerOp):
            add_marker(
                track,
                op.marked_range,
                op.name,
                color=op.color,
                metadata=op.metadata,
            )

    return ValidationReport(
        valid=True,
        operation_count=operation_count,
        applied_count=operation_count,
        errors=[],
    )
