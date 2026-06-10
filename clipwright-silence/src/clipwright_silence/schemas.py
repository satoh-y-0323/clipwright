"""schemas.py — clipwright-silence 固有の Pydantic スキーマ。

共通型（MediaRef / Artifact / ToolResult 等）は clipwright.schemas で
一元定義されているため、このモジュールでは再定義しない。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field


class DetectSilenceOptions(BaseModel):
    """clipwright_detect_silence のオプション（AD-2/AD-3・DC-AM-001）。

    silence_threshold_db と min_silence_duration は ffmpeg silencedetect
    フィルタへ直接渡す検出パラメータ。padding と min_keep_duration は
    plan.py の KEEP 導出ロジックで使う後処理パラメータ。
    """

    silence_threshold_db: Annotated[
        float,
        Field(
            default=-30.0,
            le=0.0,
            description=(
                "無音と判定する音量閾値（dB）。0 以下の値を指定する。"
                "例: -30.0 dB（既定）、-40.0 dB（より厳しく検出）。"
            ),
        ),
    ] = -30.0

    min_silence_duration: Annotated[
        float,
        Field(
            default=0.5,
            gt=0.0,
            description=(
                "無音と判定する最小継続時間（秒）。0 より大きい値を指定する。"
                "この秒数未満の無音は無視される。既定は 0.5 秒。"
            ),
        ),
    ] = 0.5

    padding: Annotated[
        float,
        Field(
            default=0.1,
            ge=0.0,
            description=(
                "各 KEEP 区間を前後に拡張するパディング幅（秒）。0 以上の値を指定する。"
                "拡張により隣接 KEEP が重なった場合はマージする（単語切れ防止）。"
                "既定は 0.1 秒。"
            ),
        ),
    ] = 0.1

    min_keep_duration: Annotated[
        float,
        Field(
            default=0.0,
            ge=0.0,
            description=(
                "KEEP として残す最小区間長（秒）。0 以上の値を指定する。"
                "この秒数未満の KEEP 区間はパディング・マージ後に破棄される。"
                "既定は 0.0（破棄なし・DC-AM-001 opt-in ガード）。"
            ),
        ),
    ] = 0.0
