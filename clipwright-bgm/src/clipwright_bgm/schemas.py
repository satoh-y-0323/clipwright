"""schemas.py — clipwright-bgm 固有の Pydantic スキーマ。

共通型（MediaRef / Artifact / ToolResult 等）は clipwright.schemas で
一元定義されているため、このモジュールでは再定義しない。

DuckingOptions: ユーザー入力のダッキングオプション。
DuckingDirective: BGM クリップ metadata に書くダッキング指示。
BgmOptions: clipwright_add_bgm の入力オプション（ユーザー入力層）。
BgmDirective: BGM クリップ metadata["clipwright"] の指示スキーマ（writer 層・B9-r2）。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class DuckingOptions(BaseModel):
    """ユーザー入力のダッキングオプション（ADR-B9）。

    enabled=True のとき render 時に sidechaincompress で BGM を自動減衰させる。
    threshold/ratio は sidechaincompress パラメータに写像する。
    """

    model_config = {"allow_inf_nan": False}

    enabled: bool = False
    threshold: Annotated[
        float,
        Field(
            default=0.05,
            description="サイドチェーン入力のトリガー閾値（0〜1 の線形振幅）。",
        ),
    ] = 0.05
    ratio: Annotated[
        float,
        Field(
            default=4.0,
            description="圧縮比率。大きいほど強くダッキングする。",
        ),
    ] = 4.0


class BgmOptions(BaseModel):
    """clipwright_add_bgm のオプション（ユーザー入力層・ADR-B9）。

    volume_db: BGM の音量調整（dB）。-60〜20 の範囲。
    fade_in_sec: フェードイン秒数。0 のとき afade を注入しない（ADR-B9-r3）。
    fade_out_sec: フェードアウト秒数。0 のとき afade を注入しない（ADR-B9-r3）。
    ducking: ダッキングオプション（既定 OFF）。
    """

    model_config = {"allow_inf_nan": False}

    volume_db: Annotated[
        float,
        Field(
            ge=-60.0,
            le=20.0,
            description="BGM の音量調整（dB）。範囲 [-60, 20]。",
        ),
    ]
    fade_in_sec: Annotated[
        float,
        Field(
            default=0.0,
            ge=0.0,
            description="フェードイン秒数（ge=0）。0 のとき無フェード（ADR-B9-r3）。",
        ),
    ] = 0.0
    fade_out_sec: Annotated[
        float,
        Field(
            default=0.0,
            ge=0.0,
            description="フェードアウト秒数（ge=0）。0 のとき無フェード（ADR-B9-r3）。",
        ),
    ] = 0.0
    ducking: DuckingOptions = Field(default_factory=DuckingOptions)


class DuckingDirective(BaseModel):
    """BGM クリップ metadata に書くダッキング指示（writer 層・ADR-B9-r2）。

    BgmDirective.ducking として co-locate する。
    render の reader 側も同フィールド構成で読み込む。
    allow_inf_nan=False は子モデルに自動伝播しないため明示（SR L-1・M-1）。
    threshold/ratio に sidechaincompress の実許容域ベースの範囲制約を付与（CR L-6）。
    実許容域は `ffmpeg -h filter=sidechaincompress` で確認済み。
    """

    model_config = {"allow_inf_nan": False}

    enabled: bool = False
    threshold: Annotated[
        float,
        Field(
            default=0.05,
            gt=0.0,
            le=1.0,
            description=(
                "サイドチェーン入力のトリガー閾値（0〜1 の線形振幅）。"
                "ffmpeg sidechaincompress threshold 値域: (0, 1]。"
            ),
        ),
    ] = 0.05
    ratio: Annotated[
        float,
        Field(
            default=4.0,
            ge=1.0,
            le=20.0,
            description="圧縮比率（ffmpeg sidechaincompress ratio 値域: [1, 20]）。",
        ),
    ] = 4.0


class BgmDirective(BaseModel):
    """BGM クリップ metadata["clipwright"] に書く指示スキーマ（writer 層・ADR-B9-r2）。

    add_bgm が構築し .model_dump() を OTIO metadata に書く。
    render の reader 側も同フィールド・max_length=64 で定義する（NR-M-1 踏襲）。
    """

    model_config = {"allow_inf_nan": False}

    tool: Annotated[str, Field(max_length=64)]
    version: Annotated[str, Field(max_length=64)]
    kind: Literal["bgm"]
    volume_db: Annotated[float, Field(ge=-60.0, le=20.0, allow_inf_nan=False)]
    fade_in_sec: Annotated[float, Field(ge=0.0)]
    fade_out_sec: Annotated[float, Field(ge=0.0)]
    ducking: DuckingDirective
