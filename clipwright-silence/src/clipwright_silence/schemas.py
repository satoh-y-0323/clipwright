"""schemas.py — clipwright-silence 固有の Pydantic スキーマ。

共通型（MediaRef / Artifact / ToolResult 等）は clipwright.schemas で
一元定義されているため、このモジュールでは再定義しない。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class DetectSilenceOptions(BaseModel):
    """clipwright_detect_silence のオプション（AD-2/AD-3・DC-AM-001）。

    silence_threshold_db と min_silence_duration は ffmpeg silencedetect
    フィルタへ直接渡す検出パラメータ。padding と min_keep_duration は
    plan.py の KEEP 導出ロジックで使う後処理パラメータ。
    vad_* フィールドは backend="vad" 時のみ有効（VAD-AD-05）。
    """

    silence_threshold_db: Annotated[
        float,
        Field(
            default=-30.0,
            le=0.0,
            description=(
                "silencedetect backend 専用。VAD 使用時は vad_* を使う。"
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
                "silencedetect backend 専用。VAD 使用時は vad_* を使う。"
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

    backend: Annotated[
        Literal["silencedetect", "vad"],
        Field(
            default="silencedetect",
            description=(
                "使用する検出バックエンド。"
                '"silencedetect"（既定）は ffmpeg silencedetect フィルタを使用。'
                '"vad" は Silero VAD（ONNX）を使用。VAD-AD-01 後方互換 opt-in。'
            ),
        ),
    ] = "silencedetect"

    vad_threshold: Annotated[
        float,
        Field(
            default=0.5,
            ge=0.0,
            le=1.0,
            description=(
                "VAD backend 時のみ有効。"
                "発話確率しきい値（0.0–1.0）。この値以上を発話区間とみなす。"
                "既定は 0.5。"
            ),
        ),
    ] = 0.5

    vad_min_speech_duration: Annotated[
        float,
        Field(
            default=0.25,
            gt=0.0,
            description=(
                "VAD backend 時のみ有効。"
                "発話と判定する最小継続時間（秒）。0 より大きい値を指定する。"
                "既定は 0.25 秒。"
            ),
        ),
    ] = 0.25

    vad_min_silence_duration: Annotated[
        float,
        Field(
            default=0.1,
            gt=0.0,
            description=(
                "VAD backend 時のみ有効。"
                "発話区間間の最小無音長（秒）。0 より大きい値を指定する。"
                "この秒数未満の無音は発話区間に吸収される。既定は 0.1 秒。"
            ),
        ),
    ] = 0.1
