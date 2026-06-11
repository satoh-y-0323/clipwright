"""schemas.py — clipwright-loudness 固有の Pydantic スキーマ。

共通型（MediaRef / Artifact / ToolResult 等）は clipwright.schemas で
一元定義されているため、このモジュールでは再定義しない。

DetectLoudnessOptions: clipwright_detect_loudness の入力オプション。
LoudnessDirective: timeline-level metadata["clipwright"]["loudness"]
    に書く指示スキーマ。
LoudnormTarget / PeakTarget: mode 別の正規化目標値。
LoudnormMeasured / PeakMeasured: mode 別の測定値。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


class DetectLoudnessOptions(BaseModel):
    """clipwright_detect_loudness のオプション（設計 §3・ADR-L1/L2）。

    mode: loudnorm（EBU R128 LUFS 正規化）または peak（ピーク dB 正規化）。
    scope: track のみ（per_clip は DC-AS-003 で延期）。
    target_i/target_tp/target_lra: loudnorm target 値（上書き可能）。
    target_peak_db: peak target 値（上書き可能）。
    """

    mode: Annotated[
        Literal["loudnorm", "peak"],
        Field(
            default="loudnorm",
            description=(
                "ラウドネス正規化モード。"
                '"loudnorm"（既定）は EBU R128 LUFS 正規化（ffmpeg loudnorm）。'
                '"peak" はピーク dB 正規化（ffmpeg volumedetect）。'
            ),
        ),
    ] = "loudnorm"

    scope: Annotated[
        Literal["track"],
        Field(
            default="track",
            description=(
                "処理スコープ。"
                '"track"（既定）はタイムライン全体を1回測定する。'
                "per_clip は DC-AS-003 で延期。"
            ),
        ),
    ] = "track"

    # loudnorm target パラメータ（設計: I=-14/TP=-1/LRA=11）
    target_i: Annotated[
        float,
        Field(
            default=-14.0,
            ge=-70.0,
            le=-5.0,
            description=(
                "loudnorm 統合ラウドネス目標値（LUFS）。範囲 [-70, -5]。既定 -14。"
            ),
        ),
    ] = -14.0

    target_tp: Annotated[
        float,
        Field(
            default=-1.0,
            ge=-9.0,
            le=0.0,
            description=(
                "loudnorm トゥルーピーク目標値（dBTP）。範囲 [-9, 0]。既定 -1。"
            ),
        ),
    ] = -1.0

    target_lra: Annotated[
        float,
        Field(
            default=11.0,
            ge=1.0,
            le=50.0,
            description="loudnorm LRA 目標値（LU）。範囲 [1, 50]。既定 11。",
        ),
    ] = 11.0

    # peak target パラメータ
    target_peak_db: Annotated[
        float,
        Field(
            default=-1.0,
            ge=-60.0,
            le=0.0,
            description="peak モードのピーク目標値（dB）。範囲 [-60, 0]。既定 -1。",
        ),
    ] = -1.0


class LoudnormTarget(BaseModel):
    """loudnorm モードの正規化目標値（ADR-L4）。

    render 側 plan.py でも独立して再定義する
    （NR-M-1 教訓: clipwright_loudness 非依存）。
    """

    i: Annotated[
        float,
        Field(
            default=-14.0,
            ge=-70.0,
            le=-5.0,
            description="統合ラウドネス目標値（LUFS）。範囲 [-70, -5]。",
        ),
    ] = -14.0

    tp: Annotated[
        float,
        Field(
            default=-1.0,
            ge=-9.0,
            le=0.0,
            description="トゥルーピーク目標値（dBTP）。範囲 [-9, 0]。",
        ),
    ] = -1.0

    lra: Annotated[
        float,
        Field(
            default=11.0,
            ge=1.0,
            le=50.0,
            description="LRA 目標値（LU）。範囲 [1, 50]。",
        ),
    ] = 11.0


class PeakTarget(BaseModel):
    """peak モードの正規化目標値（ADR-L4）。"""

    peak_db: Annotated[
        float,
        Field(
            default=-1.0,
            ge=-60.0,
            le=0.0,
            allow_inf_nan=False,
            description="ピーク目標値（dB）。範囲 [-60, 0]。inf/nan 不可。",
        ),
    ] = -1.0


class LoudnormMeasured(BaseModel):
    """loudnorm フィルタが出力する測定値（ADR-L1）。

    ffmpeg loudnorm=print_format=json の stderr 末尾 JSON から取得する 5 値。
    無音素材で "-inf" が返る場合があり、そのときは allow_inf_nan=False により
    ValidationError になるため呼び出し側は measured=None として扱う（U-1）。
    """

    input_i: Annotated[
        float,
        Field(
            allow_inf_nan=False,
            description="入力統合ラウドネス（LUFS）。inf/nan 不可。",
        ),
    ]

    input_tp: Annotated[
        float,
        Field(
            allow_inf_nan=False,
            description="入力トゥルーピーク（dBTP）。inf/nan 不可。",
        ),
    ]

    input_lra: Annotated[
        float,
        Field(
            allow_inf_nan=False,
            description="入力 LRA（LU）。inf/nan 不可。",
        ),
    ]

    input_thresh: Annotated[
        float,
        Field(
            allow_inf_nan=False,
            description="入力閾値（LUFS）。inf/nan 不可。",
        ),
    ]

    target_offset: Annotated[
        float,
        Field(
            allow_inf_nan=False,
            description="目標オフセット（LU）。inf/nan 不可。",
        ),
    ]


class PeakMeasured(BaseModel):
    """volumedetect フィルタが出力する測定値（ADR-L2）。

    ffmpeg volumedetect の stderr から "max_volume: -X.X dB" を抽出した値。
    """

    max_volume_db: Annotated[
        float,
        Field(
            ge=-200.0,
            le=0.0,
            allow_inf_nan=False,
            description="最大音量（dB）。範囲 [-200, 0]。inf/nan 不可。",
        ),
    ]


class LoudnessDirective(BaseModel):
    """timeline-level metadata に書く loudness 指示スキーマ（設計 §3.2・ADR-L4）。

    loudness が生成し render が検証読込する。
    scope は track のみ（per_clip は DC-AS-003 で延期）。
    target は mode で discriminate（LoudnormTarget または PeakTarget）。
    measured は mode 別の測定値または None（U-1: 測定不能時）。
    """

    tool: Annotated[str, Field(max_length=64)]
    version: Annotated[str, Field(max_length=64)]
    kind: Literal["loudness"]
    mode: Literal["loudnorm", "peak"]
    scope: Literal["track"]
    target: LoudnormTarget | PeakTarget
    measured: LoudnormMeasured | PeakMeasured | None = None

    @model_validator(mode="after")
    def _validate_target_mode_consistency(self) -> LoudnessDirective:
        """target の型が mode に対応していることを検証する。

        mode=loudnorm → LoudnormTarget、mode=peak → PeakTarget でなければならない。
        """
        if self.mode == "loudnorm" and not isinstance(self.target, LoudnormTarget):
            raise ValueError(
                "mode=loudnorm の場合 target は LoudnormTarget でなければなりません。"
            )
        if self.mode == "peak" and not isinstance(self.target, PeakTarget):
            raise ValueError(
                "mode=peak の場合 target は PeakTarget でなければなりません。"
            )
        return self
