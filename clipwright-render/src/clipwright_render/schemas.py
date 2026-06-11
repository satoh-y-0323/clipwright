"""schemas.py — clipwright-render 固有の Pydantic スキーマ。

共通型（MediaRef / TimeRange / Artifact / ToolResult 等）は clipwright.schemas で
一元定義されているため、このモジュールでは再定義しない。
必要な場合は `from clipwright.schemas import ...` で参照すること。
"""

from __future__ import annotations

import re
from typing import Annotated, Self

from pydantic import BaseModel, Field, field_validator, model_validator

# filtergraph/force_style の区切り文字および libass FontName の解釈リスク文字:
# , : ' [ ] ; \ = # （ADR-S2-r2/DC-AM-004）
# # を追加: libass 一部バージョンで FontName 値の # が誤解釈されるリスク（SR-NEW）。
_FONT_NAME_FORBIDDEN_CHARS_RE = re.compile(r"[,:'\\[\];=#]")

# font_size の実用上限（libass が拒否しない上限値として設定・実質無制限の意図）
_FONT_SIZE_MAX: int = 1_000_000_000

# margin_v の実用上限（8K 解像度の最大縦幅を超える値を拒否するための基準）
_MARGIN_V_MAX: int = 10_000


class SubtitleOptions(BaseModel):
    """字幕焼き込みオプション（ADR-S2-r2 / ADR-S6-r2 / ADR-S6-r3）。

    RenderOptions.subtitle に渡すことで clipwright-render が字幕を映像に焼き込む。
    subtitle=None（省略）のとき字幕処理は行わない（後方互換・ADR-S8）。

    全スタイル系フィールドは Optional。未指定時は libass の既定値を使う。
    """

    # allow_inf_nan=False を model_config に追加（BGM/Denoise モデルと整合・SR-V-001）。
    # フィールドレベルの allow_inf_nan=False は model_config 設定で冗長になるが、
    # outline フィールドは明示的にフィールドレベルでも保持する（多重防御・ADR-S2-r2）。
    model_config = {
        "extra": "forbid",
        "arbitrary_types_allowed": False,
        "allow_inf_nan": False,
    }

    path: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "字幕ファイルのパス（必須）。空文字は不可（DC-AM-005）。"
                " シングルクォート（'）は不可（ffmpeg filtergraph クォート構文破綻防止・CR-E-001）。"  # noqa: E501
                " 拡張子は .srt / .vtt / .ass のみ受理（render.py で検証）。"
                " render.py が絶対パスに解決してから plan.py に渡す（ADR-S5-r2）。"
            ),
        ),
    ]

    font_name: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "フォントファミリー名。日本語フォント名（CJK 文字列）も許可する（ADR-S2-r2）。"  # noqa: E501
                " ただし filtergraph/force_style の区切り文字 `, : ' [ ] ; \\ = #` は禁止（DC-AM-004/SR-NEW）。"  # noqa: E501
                " 前後の空白は libass の FontName 認識に影響するため、意図した通りに指定すること（S-L-5）。"  # noqa: E501
            ),
        ),
    ] = None

    fonts_dir: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "フォント探索ディレクトリのパス。ffmpeg subtitles フィルタの fontsdir= に渡す。"  # noqa: E501
                " シングルクォート（'）は不可（ffmpeg filtergraph クォート構文破綻防止・CR-E-001）。"  # noqa: E501
                " 未指定のとき fontsdir オプションは省略する。"
            ),
        ),
    ] = None

    font_size: Annotated[
        int | None,
        Field(
            default=None,
            gt=0,
            le=_FONT_SIZE_MAX,
            description=(
                "フォントサイズ（ポイント）。1 以上の整数。未指定は libass 既定値。"
                f" 上限は {_FONT_SIZE_MAX}（libass が拒否しない実用上限・実質無制限の意図）。"  # noqa: E501
            ),
        ),
    ] = None

    font_color: Annotated[
        str | None,
        Field(
            default=None,
            pattern=r"^#[0-9a-fA-F]{6}$",
            description=(
                "文字色を #RRGGBB 形式で指定する。"
                " 内部で ASS の PrimaryColour（&H00BBGGRR）に変換する（DC-AM-002）。"
                " 未指定は libass 既定値。"
            ),
        ),
    ] = None

    outline: Annotated[
        float | None,
        Field(
            default=None,
            ge=0.0,
            le=100.0,
            allow_inf_nan=False,
            description=(
                "アウトライン幅。0.0 以上の実数。"
                " `0.0` を指定すると force_style に `Outline=0`（縁取りなし）を付与する。"  # noqa: E501
                " `None` のとき libass 既定値（縁取りあり）を使用する（省略 = libass 任せ）。"  # noqa: E501
            ),
        ),
    ] = None

    alignment: Annotated[
        int | None,
        Field(
            default=None,
            ge=1,
            le=9,
            description=(
                "字幕の表示位置（ASS v4+ numpad 配置）。"
                " 1=左下, 2=中下, 3=右下, 4=左中, 5=中央,"
                " 6=右中, 7=左上, 8=中上, 9=右上。"  # noqa: E501
                " 1〜9 の整数のみ有効（DC-AM-001）。"
            ),
        ),
    ] = None

    margin_v: Annotated[
        int | None,
        Field(
            default=None,
            ge=0,
            le=_MARGIN_V_MAX,
            description=(
                "垂直方向のマージン（ピクセル）。0 以上の整数。未指定は libass 既定値。"
                f" 上限は {_MARGIN_V_MAX}（8K 解像度の最大縦幅を超える値を拒否するための基準）。"  # noqa: E501
            ),
        ),
    ] = None

    @field_validator("font_name")
    @classmethod
    def _validate_font_name_no_forbidden_chars(cls, v: str | None) -> str | None:
        """font_name に filtergraph/force_style の区切り文字が含まれないことを検証する。

        禁止文字: , : ' [ ] ; \\ = #（ADR-S2-r2/DC-AM-004/SR-NEW）。
        日本語フォント名（CJK 等 Unicode）は許可する。
        # を禁止する理由: libass の一部バージョンで FontName 値の # が
        # 色表記と誤解釈されるリスクがあるため防御的に禁止する（SR-NEW）。
        """
        if v is None:
            return v
        if _FONT_NAME_FORBIDDEN_CHARS_RE.search(v):
            raise ValueError(
                "font_name に filtergraph/force_style の区切り文字"
                " (, : ' [ ] ; \\ = #) を含めることはできません（DC-AM-004/SR-NEW）。"
            )
        return v

    @field_validator("path")
    @classmethod
    def _validate_path_no_single_quote(cls, v: str) -> str:
        """path にシングルクォートが含まれないことを検証する。

        ffmpeg の filtergraph 構文では filename='{path}' の形式でパスを囲む。
        パスにシングルクォートが含まれると ffmpeg パーサーが構文エラーを起こすため、
        allow-list 方式でシングルクォートを禁止する（CR-E-001）。
        `'` のエスケープは ffmpeg filtergraph の仕様上壊れやすいため遮断を選択する。
        """
        if "'" in v:
            raise ValueError(
                "path にシングルクォート（'）を含めることはできません"
                "（ffmpeg filtergraph クォート構文破綻防止・CR-E-001）。"
            )
        return v

    @field_validator("fonts_dir")
    @classmethod
    def _validate_fonts_dir_no_single_quote(cls, v: str | None) -> str | None:
        """fonts_dir にシングルクォートが含まれないことを検証する。

        path と同様の理由（fontsdir='{dir}' 構文破綻防止・CR-E-001）。
        """
        if v is None:
            return v
        if "'" in v:
            raise ValueError(
                "fonts_dir にシングルクォート（'）を含めることはできません"
                "（ffmpeg filtergraph クォート構文破綻防止・CR-E-001）。"
            )
        return v


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
            max_length=64,
            pattern=r"^[a-zA-Z0-9_\-]+$",
            description=(
                "出力映像コーデック。例: libx264 / libx265 / copy。未指定はソース踏襲。"
                " 英数字・アンダースコア・ハイフンのみ使用可（最大64文字）。"
            ),
        ),
    ] = None

    audio_codec: Annotated[
        str | None,
        Field(
            default=None,
            max_length=64,
            pattern=r"^[a-zA-Z0-9_\-]+$",
            description=(
                "出力音声コーデック。例: aac / opus / mp3。未指定はソース踏襲。"
                " 英数字・アンダースコア・ハイフンのみ使用可（最大64文字）。"
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

    subtitle: Annotated[
        SubtitleOptions | None,
        Field(
            default=None,
            description=(
                "字幕焼き込みオプション。指定時は映像に字幕を hardsub で焼き込む（ADR-S1）。"  # noqa: E501
                " None（省略）のとき字幕処理は行わない（後方互換・ADR-S8）。"
            ),
        ),
    ] = None

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
