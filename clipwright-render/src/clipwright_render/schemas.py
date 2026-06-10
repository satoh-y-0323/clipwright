"""schemas.py — clipwright-render 固有の Pydantic スキーマ。

共通型（MediaRef / TimeRange / Artifact / ToolResult 等）は clipwright.schemas で
一元定義されているため、このモジュールでは再定義しない。
必要な場合は `from clipwright.schemas import ...` で参照すること。
"""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, Field, model_validator


class RenderOptions(BaseModel):
    """clipwright_render の変換オプション（DC-AM-004）。

    各フィールドは Optional（未指定時はソースのコーデック/解像度/fps
    等をそのまま踏襲し、ffmpeg の既定動作に委ねる）。

    解像度（width/height）はペア制約あり: 両方指定するか両方 None で
    なければならない。片方のみ指定すると scale フィルタが不完全になる
    ため ValidationError を送出する。
    """

    video_codec: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "出力映像コーデック。例: libx264 / libx265 / copy。未指定はソース踏襲。"
            ),
        ),
    ] = None

    audio_codec: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "出力音声コーデック。例: aac / opus / mp3。未指定はソース踏襲。"
            ),
        ),
    ] = None

    width: Annotated[
        int | None,
        Field(
            default=None,
            gt=0,
            description=(
                "出力映像幅（ピクセル）。height とペアで指定する。未指定はソース踏襲。"
            ),
        ),
    ] = None

    height: Annotated[
        int | None,
        Field(
            default=None,
            gt=0,
            description=(
                "出力映像高さ（ピクセル）。width とペアで指定する。未指定はソース踏襲。"
            ),
        ),
    ] = None

    fps: Annotated[
        float | None,
        Field(
            default=None,
            gt=0.0,
            description=(
                "出力フレームレート。未指定はソース踏襲（CFR 単一ソース前提）。"
            ),
        ),
    ] = None

    crf: Annotated[
        int | None,
        Field(
            default=None,
            ge=0,
            le=51,
            description=(
                "映像品質（CRF 値）。0〜51 の範囲。0 が最高品質。未指定は ffmpeg 既定。"
            ),
        ),
    ] = None

    overwrite: Annotated[
        bool,
        Field(
            default=False,
            description=(
                "True のとき既存出力ファイルを上書きする。既定は False（上書き拒否）。"
            ),
        ),
    ] = False

    @model_validator(mode="after")
    def _validate_resolution_pair(self) -> Self:
        """width と height は「両方指定」または「両方 None」でなければならない。

        片方のみ指定すると ffmpeg の scale フィルタが不完全になるため
        禁止する（DC-AM-004）。
        """
        width_set = self.width is not None
        height_set = self.height is not None
        if width_set != height_set:
            raise ValueError(
                "width と height はペアで指定するか、両方省略してください"
                "（片方のみの指定は不正です）"
            )
        return self
