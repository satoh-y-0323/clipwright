"""schemas.py — Clipwright 共通 Pydantic 型・時間変換ヘルパー。

契約面として最初に固定する。全ツールの入出力型はこのモジュールの語彙を共有し、
ツールごとに再定義しない。
"""

from __future__ import annotations

from typing import Any, Literal

import opentimelineio as otio
from pydantic import BaseModel

# ===========================================================================
# 時間モデル（OTIO の RationalTime / TimeRange と等価）
# ===========================================================================


class RationalTimeModel(BaseModel):
    """opentime.RationalTime と等価の Pydantic 型。

    秒の float 単独で持たず rate を必ず保持する（規約 §4.4 時間表現）。
    """

    value: float
    rate: float


class TimeRangeModel(BaseModel):
    """opentime.TimeRange と等価の Pydantic 型。"""

    start_time: RationalTimeModel
    duration: RationalTimeModel


# ===========================================================================
# メディア参照・成果物
# ===========================================================================


class MediaRef(BaseModel):
    """ローカルメディアファイルへの参照。

    バイト列は持たずパスのみを扱う（規約 §6.6 ファイル入出力）。
    """

    target_url: str
    name: str | None = None
    available_range: TimeRangeModel | None = None


class Artifact(BaseModel):
    """ツール出力ファイルへの参照。

    巨大な明細は data に詰めず artifacts のパスへ逃がす（規約 §6.3）。
    """

    role: str
    """ファイルの役割。"timeline" | "output" | "caption" | "analysis" 等。"""
    path: str
    format: str
    """ファイル形式。"otio" | "mp4" | "srt" | "json" 等。"""


# ===========================================================================
# 返り値エンベロープ型（§6.3 / §6.4）
# ===========================================================================


class ToolResult(BaseModel):
    """成功エンベロープ（§6.3）。

    ok は常に True。summary には AI が次の一手を判断できる要点を必ず書く。
    """

    ok: Literal[True] = True
    summary: str
    data: dict[str, Any] = {}
    artifacts: list[Artifact] = []
    warnings: list[str] = []


class ToolError(BaseModel):
    """エラー詳細。code / message / hint の三点セット（§6.4）。"""

    code: str
    message: str
    hint: str


class ToolErrorResult(BaseModel):
    """失敗エンベロープ（§6.4）。

    ok は常に False。error には何が起きたか（message）と次の一手（hint）を含める。
    """

    ok: Literal[False] = False
    error: ToolError


# ===========================================================================
# メディア probe 結果型
# ===========================================================================


class StreamInfo(BaseModel):
    """ffprobe が返す単一ストリームの情報。"""

    index: int
    codec_type: str
    codec_name: str | None = None
    width: int | None = None
    height: int | None = None
    sample_rate: int | None = None
    channels: int | None = None


class MediaInfo(BaseModel):
    """ffprobe が返すメディアファイル全体の情報。"""

    path: str
    container: str | None
    duration: RationalTimeModel | None
    streams: list[StreamInfo]
    bit_rate: int | None = None


# ===========================================================================
# オペレーション検証結果型（§13.1 DC-AM-003）
# ===========================================================================


class OperationError(BaseModel):
    """operations 列の単一エラー情報。

    apply_operations の ValidationReport に格納され、
    どの操作がなぜ失敗したかを示す。
    """

    index: int
    """operations 列中の位置（0 始まり）。"""
    code: str
    """ErrorCode 値の文字列表現。"""
    message: str


class ValidationReport(BaseModel):
    """apply_operations の検証・適用結果レポート（§13.1 DC-AM-003/DC-AM-004）。

    all-or-nothing セマンティクス: 1 件でも不正なら applied_count=0 で
    timeline への書き込みは一切行わない。
    """

    valid: bool
    operation_count: int
    applied_count: int
    """validate_only 時は 0。不正 op が1件でもある場合も 0。"""
    errors: list[OperationError] = []


# ===========================================================================
# OTIO 時間変換ヘルパー（§13.1 DC-GP-005 で schemas.py に配置確定）
# ===========================================================================


def to_otio_time(rt: RationalTimeModel) -> otio.opentime.RationalTime:
    """RationalTimeModel を opentime.RationalTime に変換する。

    otio_utils は本関数を import して使い、変換器自体は重複実装しない。
    """
    return otio.opentime.RationalTime(value=rt.value, rate=rt.rate)


def from_otio_time(rt: otio.opentime.RationalTime) -> RationalTimeModel:
    """opentime.RationalTime を RationalTimeModel に変換する。

    秒 float に正規化せず rate をそのまま保持する。
    """
    return RationalTimeModel(value=rt.value, rate=rt.rate)
