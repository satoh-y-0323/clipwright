"""plan.py — clipwright-render の純ロジック層。

ffmpeg/ffprobe を一切実行しない。probe 結果は ProbeInfo を引数で受ける（DC-AM-007）。
タイムライン解析・filter_complex 構築・dry_run 概算の3責務を持つ。

設計判断:
- 再エンコードは1回（ADR-1）: filter_complex で trim+concat を使うため
  フレーム精度の時刻制御が可能で、区間を重ねて再エンコードするより劣化が少ない。
- concat=n=1 一律（DC-AS-005）: 1区間でも分岐をなくすことで実装を単純化する。
  ffmpeg は n=1 を正常に処理する。
- 音声複数時は第1音声のみ採用（ADR-7）: 複数音声ストリームのマッピングは
  複雑さを大幅に増すため、本イテレーションでは第1音声のみを対象とする。
- denoise afftdn 注入（architecture-report-20260611-092647 §B-2）:
  filter_parts の順序を trim/atrim → concat → afftdn → scale に固定する。
  afftdn（audio チェーン）と scale（video チェーン）は独立ラベルで競合しない。
  has_audio=False のときは afftdn を入れず warnings に追加する。
- loudness 注入（architecture-report-20260611-114314 §3.3 ADR-L5/L5b/L6）:
  filter 注入順序は denoise の後ろに loudness を連結する（音響的正しさ）。
  audio map 終端ラベルは累積パイプ型ヘルパーで一元解決する（DC-AM-001）:
  [outa] →（denoise あり → [outa_dn]）→（track loudness あり → [outa_ln]）
  loudness 指示なしは従来と完全同一（ADR-L6・後方互換厳守）。
- 複数ソース対応（ADR-C1〜C12・architecture-report-20260611-154732 §7 v2）:
  ユニークソース数で経路分岐し、単一ソース経路の後方互換を厳守する（ADR-C3）。
  unique_sources_in_order が入力 index の単一情報源（ADR-C9-r2）。
- 解像度ペア制約（DC-AM-004）: width/height の片方のみ指定は RenderOptions の
  model_validator（schemas.py）が ValidationError で弾く。
  _build_multi_source_filter_complex の出力規格決定ロジックは
  「両方指定」または「両方 None」のみを想定する。
- BGM ミックス（ADR-B4-r2/B5-r2/B5-r3/B6-r2/B9-r3）:
  resolve_bgm で全 Audio トラックから kind=="bgm" クリップを検出する（ADR-B4-r2）。
  build_plan の bgm 引数が非 None のとき _append_bgm_pipe で BGM 段を追記する。
  has_main_audio（本編音声有無）と has_audio_output（最終出力音声有無）を分離する
  （ADR-B5-r2）。
  -stream_loop -1 は render.py が付与し、plan は atrim=0:{main_dur} のみで
  尺合わせする（ADR-B6-r2）。
  BGM index = len(input_sources)（bgm_source は input_sources 非包含・DC-AS-005）。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

import opentimelineio as otio
from clipwright.errors import ClipwrightError, ErrorCode
from pydantic import BaseModel, Field, ValidationError, model_validator

from clipwright_render.schemas import RenderOptions, SubtitleOptions

# ===========================================================================
# Denoise スキーマ（clipwright-noise には依存しない・render 内自前定義）
# ===========================================================================


class AfftdnParams(BaseModel):
    """afftdn フィルタのパラメータ検証モデル（DC-AS-006）。

    nr: ノイズ低減量（dB）。0.01〜97 の範囲。
    nf: ノイズフロア（dB）。-80〜-20 の範囲。
    nt: ノイズタイプ。"w"=ホワイトノイズ、"v"=バイナリノイズ。
    """

    nr: Annotated[float, Field(ge=0.01, le=97)]
    nf: Annotated[float, Field(ge=-80, le=-20)]
    nt: Literal["w", "v"] = "w"


# SR M-1: afftdn nt の許可値セット（モジュールレベル定数）。
# Literal["w","v"] 型制約への二重防御として _append_audio_pipe から参照する。
_VALID_NT_VALUES: frozenset[str] = frozenset({"w", "v"})


class DenoiseDirective(BaseModel):
    """timeline metadata["clipwright"]["denoise"] の検証モデル（DC-AS-006/ADR-N9）。

    render 読み込み時に Pydantic で検証し、不正な場合は INVALID_INPUT を送出する。
    backend=="afftdn" のとき params を AfftdnParams で再検証する（render.py が担う）。
    backend=="deepfilternet" のとき params は {} 固定。

    SR L-1: tool/version に max_length 制約を設ける（長大文字列混入防止）。
    SR L-3: measured_noise_floor_db は -200〜0 dB の有限値のみ許容（inf/nan 排除）。
    """

    # NR-M-1: noise 側 schemas.py（writer）と max_length を一致させる（reader が厳格だと
    # ライターが通す値を弾く非互換になる）。tool/version とも 64 に統一。
    tool: Annotated[str, Field(max_length=64)]
    version: Annotated[str, Field(max_length=64)]
    kind: Literal["denoise"]
    backend: Literal["afftdn", "deepfilternet"]
    params: dict[str, Any]
    measured_noise_floor_db: (
        Annotated[float, Field(ge=-200.0, le=0.0, allow_inf_nan=False)] | None
    ) = None


# ===========================================================================
# Loudness スキーマ（clipwright-loudness には依存しない・render 内自前定義）
# NR-M-1: loudness 側 schemas.py（writer）と max_length を一致させる（64 に統一）。
# ===========================================================================


class LoudnormTarget(BaseModel):
    """loudnorm モードのターゲット検証モデル（ADR-L1）。

    i: 統合ラウドネス目標 LUFS（-70〜-5）。
    tp: トゥルーピーク目標 dBTP（-9〜0）。
    lra: ラウドネスレンジ目標 LU（1〜50）。
    """

    i: Annotated[float, Field(ge=-70.0, le=-5.0)]
    tp: Annotated[float, Field(ge=-9.0, le=0.0)]
    lra: Annotated[float, Field(ge=1.0, le=50.0)]


class PeakTarget(BaseModel):
    """peak モードのターゲット検証モデル（ADR-L2）。

    peak_db: ピーク目標 dB（-60〜0）。
    """

    peak_db: Annotated[float, Field(ge=-60.0, le=0.0)]


class LoudnormMeasured(BaseModel):
    """loudnorm モードの測定値検証モデル（ADR-L1 linear 二段適用）。

    すべての値は有限値のみ許容（inf/nan 拒否・CWE-20）。
    """

    input_i: Annotated[float, Field(allow_inf_nan=False)]
    input_tp: Annotated[float, Field(allow_inf_nan=False)]
    input_lra: Annotated[float, Field(allow_inf_nan=False)]
    input_thresh: Annotated[float, Field(allow_inf_nan=False)]
    target_offset: Annotated[float, Field(allow_inf_nan=False)]


class PeakMeasured(BaseModel):
    """peak モードの測定値検証モデル（ADR-L2）。

    max_volume_db: 測定ピーク値 dB（-200〜0）。有限値のみ許容。
    """

    max_volume_db: Annotated[float, Field(ge=-200.0, le=0.0, allow_inf_nan=False)]


class LoudnessDirective(BaseModel):
    """timeline metadata["clipwright"]["loudness"] の検証モデル（ADR-L4/ADR-L6）。

    render 読み込み時に Pydantic で検証し、不正な場合は INVALID_INPUT を送出する。
    scope は "track" のみ対応（per_clip は①合体後に延期・DC-AS-003）。
    mode="loudnorm" のとき measured は必須（linear 適用に必要）。
    measured=None は INVALID_INPUT。

    NR-M-1: tool/version は max_length=64 に統一（reader/writer の互換維持）。

    writer 側（clipwright-loudness/schemas.py）との差異（CR-M-001 reader-strict 対応）:
      - schemas.py の LoudnessDirective は measured=None を許容する
        （U-1: 測定不能時は loudness 指示自体を OTIO に書かない設計のため）。
      - こちら（reader 側）は loudnorm+measured=None を INVALID_INPUT として弾く
        （linear 二段適用に measured_* が必須であり、measured=None の指示が
        OTIO に書かれること自体が不正状態のため・reader-strict）。
    """

    tool: Annotated[str, Field(max_length=64)]
    version: Annotated[str, Field(max_length=64)]
    kind: Literal["loudness"]
    mode: Literal["loudnorm", "peak"]
    scope: Literal["track"]
    target: LoudnormTarget | PeakTarget
    # None を型に残す理由: writer 側（schemas.py）との互換維持のため。
    # writer は peak で measured=None を許容するため reader でも受け取れる必要がある。
    # loudnorm + measured=None の不正ケースは下の model_validator が
    # reader-strict に弾く（実行時検証で制御する設計。docstring CR-M-001 参照）。
    measured: LoudnormMeasured | PeakMeasured | None = None

    @model_validator(mode="after")
    def _validate_measured_required_for_loudnorm(self) -> LoudnessDirective:
        """loudnorm モードでは measured が必須（linear 二段適用に必要）。"""
        if self.mode == "loudnorm" and self.measured is None:
            raise ValueError(
                "loudnorm モードでは measured が必須です（linear 適用に必要）。"
            )
        return self

    @model_validator(mode="after")
    def _validate_target_matches_mode(self) -> LoudnessDirective:
        """mode と target の型が一致していることを検証する。"""
        if self.mode == "loudnorm" and not isinstance(self.target, LoudnormTarget):
            raise ValueError(
                "loudnorm モードでは target に"
                " LoudnormTarget（i/tp/lra）を指定してください。"
            )
        if self.mode == "peak" and not isinstance(self.target, PeakTarget):
            raise ValueError(
                "peak モードでは target に PeakTarget（peak_db）を指定してください。"
            )
        return self


# ===========================================================================
# BGM スキーマ（clipwright-bgm には依存しない・render 内自前定義）
# ADR-B9-r2: reader-strict・未知キー forbid・allow_inf_nan=False
# NR-M-1: tool/version は max_length=64（writer 側 clipwright-bgm と一致）
# ===========================================================================


class DuckingDirective(BaseModel):
    """BGM ダッキング設定の検証モデル（ADR-B5-r3/DC-AS-006）。

    enabled: True のとき sidechaincompress を注入して BGM を本編音声でダッキングする。
    threshold: sidechaincompress の threshold パラメータ。
        ffmpeg 実許容域は 0.000976563〜1.0。
    ratio: sidechaincompress の ratio パラメータ。ffmpeg 実許容域は 1.0〜20.0。
    SR M-1: allow_inf_nan=False で OTIO 由来の inf/nan を弾く。
    """

    model_config = {"extra": "forbid", "allow_inf_nan": False}

    enabled: bool = False
    threshold: Annotated[float, Field(gt=0.0, le=1.0)]
    ratio: Annotated[float, Field(ge=1.0, le=20.0)]


class BgmDirective(BaseModel):
    """BGM クリップ metadata["clipwright"] の検証モデル（ADR-B9-r2/B9-r3）。

    render 読み込み時に Pydantic で検証し、不正な場合は INVALID_INPUT を送出する。
    reader-strict（未知キー forbid）・allow_inf_nan=False。
    fade_in_sec / fade_out_sec の既定値は 0.0（無フェード・ADR-B9-r3）。
    afade は値が > 0 のときのみ注入する。
    SR I-1: volume_db に ge=-60.0/le=20.0 を追加（writer BgmOptions と一致）。
    """

    model_config = {"extra": "forbid", "allow_inf_nan": False}

    tool: Annotated[str, Field(max_length=64)]
    version: Annotated[str, Field(max_length=64)]
    kind: Literal["bgm"]
    volume_db: Annotated[float, Field(ge=-60.0, le=20.0, allow_inf_nan=False)]
    fade_in_sec: Annotated[float, Field(ge=0)] = 0.0
    fade_out_sec: Annotated[float, Field(ge=0)] = 0.0
    ducking: DuckingDirective


# ===========================================================================
# データ型
# ===========================================================================


@dataclass
class KeptRange:
    """タイムライン上の残区間を表す値オブジェクト。

    source: メディアファイルの target_url（ソースパス）
    source_range: OTIO TimeRange（opentime で保持し秒変換は遅延）
    """

    source: str
    source_range: otio.opentime.TimeRange


@dataclass(frozen=True)
class BgmClip:
    """BGM クリップの情報を表す値オブジェクト（ADR-B4-r2）。

    source: BGM メディアファイルの target_url（ソースパス）
    source_range: BGM メディア全長（OTIO TimeRange）
    directive: BgmDirective で検証済みの BGM 指示
    """

    source: str
    source_range: otio.opentime.TimeRange
    directive: BgmDirective


@dataclass
class ProbeInfo:
    """ffprobe の probe 結果を表す値オブジェクト（DC-AM-007）。

    plan.py は本型を引数で受け取り、subprocess を一切呼ばない。
    bit_rate: None の場合は概算サイズが算出不能（ADR-3）。
    width/height/fps: 複数ソース経路の規格統一に使用（ADR-C2・後方互換のため省略可）。
    """

    has_video: bool
    audio_count: int
    bit_rate: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None


@dataclass
class RenderPlan:
    """build_plan が返す実行計画。

    filter_complex: ffmpeg -filter_complex 引数の単一文字列（インジェクション防止）。
    ffmpeg_args: ffmpeg へ渡す引数リスト（-fc 以外）。str のみ（M-1）。
    segment_count: 残区間数。
    total_duration_seconds: 出力総尺（秒）。
    estimated_size_bytes: 概算ファイルサイズ（bytes）。bit_rate None 時は None。
    warnings: dry_run 概算の注意事項。
    input_sources: 入力ソース一覧（出現順・重複排除）。ADR-C9-r2 の単一情報源。
    bgm_source: BGM ソースパス。BGM なしのとき None（ADR-B5/B7）。
    """

    filter_complex: str
    ffmpeg_args: list[str]
    segment_count: int
    total_duration_seconds: float
    estimated_size_bytes: float | None = None
    warnings: list[str] = field(default_factory=list)
    input_sources: list[str] = field(default_factory=list)
    bgm_source: str | None = None


# ===========================================================================
# ユーティリティ関数
# ===========================================================================


def unique_sources_in_order(ranges: list[KeptRange]) -> list[str]:
    """KeptRange リストからソース URL を出現順・重複排除で返す（ADR-C9-r2）。

    入力 index の割当と input_sources の単一情報源として機能する。
    同一ソースが複数のクリップに現れる場合は最初の出現位置で順序を決定する。
    """
    seen: set[str] = set()
    result: list[str] = []
    for r in ranges:
        if r.source not in seen:
            seen.add(r.source)
            result.append(r.source)
    return result


# ===========================================================================
# resolve_kept_ranges
# ===========================================================================


def resolve_kept_ranges(timeline: otio.schema.Timeline) -> list[KeptRange]:
    """先頭 video トラックの Clip を走査し、残区間リストを返す（ADR-5/DC-AS-006）。

    - Gap はスキップ（除去領域の表現のため）。
    - Transition が含まれる場合は UNSUPPORTED_OPERATION を送出する。
    - video トラックが2本以上ある場合は UNSUPPORTED_OPERATION を送出する。
    - 複数ソースを許容する（ADR-C3・DC-AS-005 旧挙動廃止）。
      各 Clip は自身の source を KeptRange に保持する。
    - Clip が0件の場合は INVALID_INPUT を送出する。

    Returns:
        KeptRange のリスト（source と source_range を opentime で保持）。
    """
    # 先頭 video トラックを取得（複数 video トラックは非対応）
    video_tracks = [t for t in timeline.tracks if t.kind == otio.schema.TrackKind.Video]
    if len(video_tracks) >= 2:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message="video トラックが2本以上含まれています。",
            hint=(
                "先頭の video トラック1本のみを持つ OTIO"
                " タイムラインを使用してください。"
            ),
        )

    if len(video_tracks) == 0:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message="video トラックが見つかりません。",
            hint="video トラックを含む OTIO タイムラインを使用してください。",
        )

    video_track = video_tracks[0]

    ranges: list[KeptRange] = []

    for item in video_track:
        if isinstance(item, otio.schema.Gap):
            # Gap は除去領域を表すためスキップ
            continue
        if isinstance(item, otio.schema.Transition):
            raise ClipwrightError(
                code=ErrorCode.UNSUPPORTED_OPERATION,
                message="Transition が含まれています。",
                hint="Transition を含まない OTIO タイムラインを使用してください。",
            )
        if isinstance(item, otio.schema.Clip):
            mr = item.media_reference
            if isinstance(mr, otio.schema.MissingReference):
                # MissingReference はタイムラインのデータ不正（参照欠落）を意味する。
                # 「サポートしていない構成」（UNSUPPORTED_OPERATION）ではなく
                # 「データが不正」（INVALID_INPUT）として扱う。
                raise ClipwrightError(
                    code=ErrorCode.INVALID_INPUT,
                    message="メディア参照が欠落しています（MissingReference）。",
                    hint="target_url を持つ ExternalReference を使用してください。",
                )
            if not isinstance(mr, otio.schema.ExternalReference):
                # 非対応構成（GeneratorReference 等）は UNSUPPORTED_OPERATION。
                raise ClipwrightError(
                    code=ErrorCode.UNSUPPORTED_OPERATION,
                    message="ExternalReference 以外のメディア参照は非対応です。",
                    hint=("target_url を持つ ExternalReference を使用してください。"),
                )
            source = mr.target_url
            source_range = item.source_range
            ranges.append(KeptRange(source=source, source_range=source_range))

    if len(ranges) == 0:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="残区間が0件です（Clip が見つかりません）。",
            hint=("少なくとも1件の Clip を含む OTIO タイムラインを使用してください。"),
        )

    return ranges


# ===========================================================================
# resolve_bgm
# ===========================================================================


def resolve_bgm(timeline: otio.schema.Timeline) -> BgmClip | None:
    """全 Audio トラックを走査し kind=="bgm" クリップを検出して BgmClip を返す。

    ADR-B4-r2 準拠。

    Audio トラック本数ではなく kind=="bgm" クリップ数で判定する（DC-AS-002）。
    A1 本編音声トラック（kind!="bgm"）が存在しても 1件の BGM クリップは正常検出する。

    Returns:
        BGM クリップが1件のとき BgmClip。0件のとき None（後方互換）。

    Raises:
        ClipwrightError(UNSUPPORTED_OPERATION): BGM クリップが2件以上のとき
            （単一 BGM のみ対応）。
        ClipwrightError(INVALID_INPUT): BGM クリップの metadata 検証失敗時。
    """
    bgm_clips: list[tuple[str, otio.opentime.TimeRange, Mapping[str, Any]]] = []

    # 全 Audio トラックを走査して kind=="bgm" クリップを収集する
    for track in timeline.tracks:
        if track.kind != otio.schema.TrackKind.Audio:
            continue
        for item in track:
            if not isinstance(item, otio.schema.Clip):
                continue
            cw_meta = item.metadata.get("clipwright")
            # OTIO の metadata 値は AnyDictionary 型（dict の subclass ではない）のため
            # Mapping プロトコルで判定する（DC-AS-002）
            if not isinstance(cw_meta, Mapping):
                continue
            if cw_meta.get("kind") != "bgm":
                continue
            mr = item.media_reference
            if not isinstance(mr, otio.schema.ExternalReference):
                continue
            source_range = item.source_range
            bgm_clips.append((mr.target_url, source_range, cw_meta))

    if len(bgm_clips) == 0:
        return None

    if len(bgm_clips) >= 2:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message="BGM クリップが2件以上含まれています（単一 BGM のみ対応）。",
            hint=(
                "timeline 内の BGM クリップを1件に絞ってください。"
                " 複数 BGM のミックスは現在未対応です。"
            ),
        )

    # 1件の場合: BgmDirective を検証して BgmClip を返す
    source, source_range, raw_meta = bgm_clips[0]
    try:
        directive = BgmDirective(**raw_meta)
    except (ValidationError, TypeError, ValueError):
        # ValueError も含む理由: 将来の model_validator 由来の raise ValueError を考慮。
        # loudness の踏襲（_validate_loudness_directive と同じ捕捉リスト）。
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="BGM クリップの metadata 検証に失敗しました。フィールド名・型・値を確認してください。",  # noqa: E501
            hint=(
                "BGM クリップの metadata['clipwright'] に kind='bgm'・volume_db・"
                "fade_in_sec・fade_out_sec・ducking が正しく設定されているか確認してください。"  # noqa: E501
            ),
        ) from None

    return BgmClip(source=source, source_range=source_range, directive=directive)


# ===========================================================================
# build_plan
# ===========================================================================


def _escape_filtergraph(path: str) -> str:
    """filtergraph の filename= / fontsdir= 用パスエスケープを行う。

    実機確認済みエスケープ規則（M2 2026-06-11 / DC-AS-005）:
    1. バックスラッシュ（\\） → \\\\
    2. コロン（:） → \\:
    この順序を守ることで Windows 絶対パス（C:\\...）が cwd 非依存で ffmpeg へ渡せる。

    例: C:\\Users\\sub.srt → C\\:\\\\Users\\\\sub.srt
    """
    return path.replace("\\", "\\\\").replace(":", "\\:")


def _rgb_to_ass_colour(hex_color: str) -> str:
    """#RRGGBB 形式の色文字列を ASS PrimaryColour 形式（&H00BBGGRR・8桁）に変換する。

    実機確認済み（M2 2026-06-11 / DC-AM-002）:
    - 8桁 &H00BBGGRR（AA=00 = 不透明）形式で不透明描画が確実。
    - 例: #FF0000（赤: R=FF,G=00,B=00）→ &H000000FF（BGR 順）。

    Args:
        hex_color: '#RRGGBB' 形式の色文字列。

    Returns:
        '&H00BBGGRR' 形式の ASS PrimaryColour 文字列（大文字）。
    """
    # 先頭の # を除去して R/G/B を取り出す
    hex_str = hex_color.lstrip("#")
    r = int(hex_str[0:2], 16)
    g = int(hex_str[2:4], 16)
    b = int(hex_str[4:6], 16)
    # ASS は BGR 順・AA=00（不透明）の 8桁
    return f"&H00{b:02X}{g:02X}{r:02X}"


def _build_force_style(subtitle: SubtitleOptions, is_ass: bool) -> str | None:
    """SubtitleOptions からフィルタグラフ用 force_style 文字列を組み立てて返す。

    ASS 入力時は force_style を不適用とし None を返す（ADR-S6-r2 / DC-AS-002）。
    スタイル系フィールドが全て None のとき None を返す（force_style= を省略する）。

    Returns:
        'FontName=...,FontSize=...' 形式の文字列。付与不要のとき None。
    """
    if is_ass:
        # ASS は内蔵スタイルを持つため force_style を適用しない（DC-AS-002）
        return None

    parts: list[str] = []
    if subtitle.font_name is not None:
        parts.append(f"FontName={subtitle.font_name}")
    if subtitle.font_size is not None:
        parts.append(f"FontSize={subtitle.font_size}")
    if subtitle.font_color is not None:
        ass_colour = _rgb_to_ass_colour(subtitle.font_color)
        parts.append(f"PrimaryColour={ass_colour}")
    if subtitle.outline is not None:
        # :g フォーマットで余分な小数点ゼロを除去する
        parts.append(f"Outline={subtitle.outline:g}")
    if subtitle.alignment is not None:
        parts.append(f"Alignment={subtitle.alignment}")
    if subtitle.margin_v is not None:
        parts.append(f"MarginV={subtitle.margin_v}")

    if not parts:
        return None
    return ",".join(parts)


def _append_subtitle_filter(
    filter_parts: list[str],
    video_map_label: str,
    subtitle: SubtitleOptions,
) -> str:
    """字幕段（subtitles フィルタ）を filter_parts に追記し新しい映像ラベルを返す。

    実機確認済み構文（M2 2026-06-11）に従う（ADR-S4-r2 / ADR-S5-r2 / ADR-S6-r2）。
    timeline_dir 引数は持たない（境界検証は render.py に一本化・DC-AS-001）。

    フィルタ形式:
    {L_v}subtitles=filename='{esc(path)}'[:fontsdir='{esc(dir)}']
                  [:force_style='{style}'][:charenc=UTF-8][outvsub]

    ASS 入力時は force_style 不適用・charenc/fontsdir は付与可（DC-AS-002）。
    SRT/VTT 入力時は charenc=UTF-8 と force_style を付与する（M2 真理値表）。

    Args:
        filter_parts: filter_complex の各セグメントリスト（破壊的に追記する）。
        video_map_label: 映像チェーン終端ラベル（'[outv]' 等）。
        subtitle: SubtitleOptions（path が絶対パスに解決済み・ADR-S5-r2）。

    Returns:
        新しい video_map_label '[outvsub]'。
    """
    path = subtitle.path
    ext = os.path.splitext(path)[1].lower()
    is_ass = ext == ".ass"

    # パスをエスケープする（実機確認済み構文: \ → \\\\ then : → \\:）
    esc_path = _escape_filtergraph(path)

    # subtitles フィルタ組み立て
    # filename= に絶対パスをシングルクォートで囲む（ADR-S5-r2）
    filter_str = f"{video_map_label}subtitles=filename='{esc_path}'"

    # fontsdir 付与（ASS/SRT/VTT 問わず指定があれば付与）
    if subtitle.fonts_dir is not None:
        esc_dir = _escape_filtergraph(subtitle.fonts_dir)
        filter_str += f":fontsdir='{esc_dir}'"

    # force_style 付与（SRT/VTT のみ・ASS は内蔵スタイル優先）
    force_style = _build_force_style(subtitle, is_ass)
    if force_style is not None:
        filter_str += f":force_style='{force_style}'"

    # charenc=UTF-8 付与（SRT/VTT のみ・ASS はエンコーディング内包）
    if not is_ass:
        filter_str += ":charenc=UTF-8"

    filter_str += "[outvsub]"
    filter_parts.append(filter_str)

    return "[outvsub]"


def _to_seconds(rt: otio.opentime.RationalTime) -> float:
    """RationalTime を秒（小数6桁）に変換する。

    OTIO の型スタブが to_seconds() を Any で定義しているため、
    明示的に float へキャストして mypy strict を通す。
    """
    return round(float(rt.to_seconds()), 6)


def _validate_denoise_directive(denoise: dict[str, Any]) -> DenoiseDirective:
    """denoise 指示 dict を DenoiseDirective で検証し、失敗時は INVALID_INPUT を送出する。

    backend=="afftdn" のとき AfftdnParams での params 再検証も行う。
    """  # noqa: E501
    try:
        directive = DenoiseDirective(**denoise)
    except (ValidationError, TypeError):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="denoise 指示の検証に失敗しました。フィールド名・型・値を確認してください。",  # noqa: E501
            hint=(
                "timeline metadata の denoise フィールドが正しい形式か確認"
                "してください。backend は 'afftdn' または 'deepfilternet'"
                " を指定してください。"
            ),
        ) from None

    if directive.backend == "afftdn":
        try:
            AfftdnParams(**directive.params)
        except (ValidationError, TypeError):
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="afftdn params の検証に失敗しました。フィールド名・型・値を確認してください。",  # noqa: E501
                hint=(
                    "params.nr は 0.01〜97、params.nf は -80〜-20 の float、"
                    "params.nt は 'w' または 'v' を指定してください。"
                ),
            ) from None

    return directive


def _validate_loudness_directive(loudness: dict[str, Any]) -> LoudnessDirective:
    """loudness 指示 dict を検証し、失敗時は INVALID_INPUT を送出する。

    mode と target 型の整合も検証する。
    セキュリティ: 入力値をエラーメッセージに含めない（SR M-1）。
    """
    try:
        # target/measured を mode に応じて手動でモデル変換してから LoudnessDirective を構築する。  # noqa: E501
        # Pydantic v2 は discriminated union なしの裸の Union[LoudnormTarget, PeakTarget] を  # noqa: E501
        # dict から変換する際、最初に適合するモデルへの変換を試みる。両モデルのフィールド名が  # noqa: E501
        # 異なるため自動変換でも誤認識はないが、mode との整合は後段の model_validator に委ねている。  # noqa: E501
        # 手動変換を先行させることで ValidationError が LoudnessDirective バリデーション前に  # noqa: E501
        # 早期検出され、エラーメッセージが target/measured の不正由来かを特定しやすくなる（L-3）。  # noqa: E501
        raw = dict(loudness)
        if isinstance(raw.get("target"), dict):
            mode = raw.get("mode")
            if mode == "loudnorm":
                raw["target"] = LoudnormTarget(**raw["target"])
            elif mode == "peak":
                raw["target"] = PeakTarget(**raw["target"])
        if isinstance(raw.get("measured"), dict):
            mode = raw.get("mode")
            if mode == "loudnorm":
                raw["measured"] = LoudnormMeasured(**raw["measured"])
            elif mode == "peak":
                raw["measured"] = PeakMeasured(**raw["measured"])
        directive = LoudnessDirective(**raw)
    except (ValidationError, TypeError, ValueError):
        # ValueError も含む理由: model_validator が raise ValueError を使うため。
        # ValidationError のみでは model_validator 内の ValueError を捕捉できない。
        # from None の理由: CWE-209 情報漏洩防止。
        # ValidationError の詳細にパス等が含まれうるため外部に露出しない。
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "loudness 指示の検証に失敗しました。"
                "フィールド名・型・値を確認してください。"
            ),
            hint=(
                "timeline metadata の loudness フィールドの形式を確認してください。"
                " mode は 'loudnorm' または 'peak'、scope は 'track'。"
                " loudnorm モードでは measured 必須。"
            ),
        ) from None
    return directive


def _append_audio_pipe(
    filter_parts: list[str],
    has_audio: bool,
    denoise_directive: DenoiseDirective | None,
    loudness_directive: LoudnessDirective | None,
) -> tuple[bool, bool]:
    """denoise afftdn / loudness フィルタを filter_parts に追記し、使用フラグを返す。

    単一ソース・複数ソース共通のヘルパー（ADR-C11-r2・重複排除）。
    [outa] を起点として累積パイプ型でラベルを繋ぐ。
    has_audio=False のとき何も追加しない（警告は build_plan 側が担う）。

    Returns:
        (use_afftdn, use_loudness)
    """
    use_afftdn = False
    use_loudness = False

    if not has_audio:
        return use_afftdn, use_loudness

    # denoise afftdn 注入
    if denoise_directive is not None and denoise_directive.backend == "afftdn":
        params = AfftdnParams(**denoise_directive.params)
        nr_str = f"{params.nr:g}"
        nf_str = f"{params.nf:g}"
        # SR M-1: Literal["w","v"] 型制約に加えて frozenset で二重防御する
        # （defense in depth: 将来 Literal 制約が外れた場合のインジェクション対策）
        nt_str = params.nt
        if nt_str not in _VALID_NT_VALUES:
            raise ClipwrightError(
                code=ErrorCode.INTERNAL,
                message="afftdn nt パラメータが不正です（内部エラー）。",
                hint="params.nt は 'w' または 'v' のみ有効です。",
            )
        filter_parts.append(
            f"[outa]afftdn=nr={nr_str}:nf={nf_str}:nt={nt_str}[outa_dn]"
        )
        use_afftdn = True

    # loudness 注入
    if loudness_directive is not None:
        loudness_input_label = "[outa_dn]" if use_afftdn else "[outa]"

        if loudness_directive.mode == "loudnorm":
            target = loudness_directive.target
            measured = loudness_directive.measured
            if not isinstance(target, LoudnormTarget) or not isinstance(
                measured, LoudnormMeasured
            ):
                raise ClipwrightError(
                    code=ErrorCode.INTERNAL,
                    message="loudnorm 指示の型整合が不正です（内部エラー）。",
                    hint="LoudnessDirective の model_validator が機能していません。",
                )
            i_str = f"{target.i:g}"
            tp_str = f"{target.tp:g}"
            lra_str = f"{target.lra:g}"
            mi_str = f"{measured.input_i:g}"
            mtp_str = f"{measured.input_tp:g}"
            mlra_str = f"{measured.input_lra:g}"
            mthresh_str = f"{measured.input_thresh:g}"
            offset_str = f"{measured.target_offset:g}"
            filter_parts.append(
                f"{loudness_input_label}loudnorm="
                f"I={i_str}:TP={tp_str}:LRA={lra_str}"
                f":measured_I={mi_str}:measured_TP={mtp_str}"
                f":measured_LRA={mlra_str}:measured_thresh={mthresh_str}"
                f":offset={offset_str}:linear=true[outa_ln]"
            )
            use_loudness = True

        elif loudness_directive.mode == "peak":
            target = loudness_directive.target
            measured = loudness_directive.measured
            if not isinstance(target, PeakTarget) or not isinstance(
                measured, PeakMeasured
            ):
                raise ClipwrightError(
                    code=ErrorCode.INTERNAL,
                    message="peak 指示の型整合が不正です（内部エラー）。",
                    hint="LoudnessDirective の model_validator が機能していません。",
                )
            gain_db = target.peak_db - measured.max_volume_db
            gain_str = f"{gain_db:g}"
            filter_parts.append(f"{loudness_input_label}volume={gain_str}dB[outa_ln]")
            use_loudness = True

    return use_afftdn, use_loudness


def _build_filter_complex(
    ranges: list[KeptRange],
    has_audio: bool,
    denoise_directive: DenoiseDirective | None,
    loudness_directive: LoudnessDirective | None,
    options: RenderOptions,
) -> tuple[str, str, str, bool, bool]:
    """filter_complex 文字列・video_map_label・audio_map_label を構築して返す（M-2）。

    責務: trim/atrim → concat → denoise afftdn → loudness → scale の
    filter_complex 文字列組み立てと、各チェーンの終端ラベル決定に集中する。
    単一ソース経路専用（後方互換維持・ADR-C3）。

    Returns:
        (filter_complex, video_map_label, audio_map_label, use_afftdn, use_loudness)
    """
    n = len(ranges)

    # 各区間の trim/atrim フィルタセグメントを生成
    video_labels: list[str] = []
    audio_labels: list[str] = []
    filter_parts: list[str] = []

    for i, r in enumerate(ranges):
        start = _to_seconds(r.source_range.start_time)
        end = round(start + _to_seconds(r.source_range.duration), 6)
        vl = f"v{i}"
        filter_parts.append(
            f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[{vl}]"
        )
        video_labels.append(f"[{vl}]")

        if has_audio:
            al = f"a{i}"
            filter_parts.append(
                f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[{al}]"
            )
            audio_labels.append(f"[{al}]")

    # concat フィルタ（ビデオ/オーディオラベルをインターリーブして入力する）
    v_count = 1
    a_count = 1 if has_audio else 0
    if has_audio:
        interleaved: list[str] = []
        for vl, al in zip(video_labels, audio_labels, strict=True):
            interleaved.append(vl)
            interleaved.append(al)
        input_labels = "".join(interleaved)
    else:
        input_labels = "".join(video_labels)

    concat_output = "[outv]" if not has_audio else "[outv][outa]"
    filter_parts.append(
        f"{input_labels}concat=n={n}:v={v_count}:a={a_count}{concat_output}"
    )

    # denoise/loudness 累積パイプ（単一/複数ソース共通ヘルパー）
    use_afftdn, use_loudness = _append_audio_pipe(
        filter_parts, has_audio, denoise_directive, loudness_directive
    )

    # width/height 指定時: scale を filter_complex 内に統合する（ADR-1 準拠）。
    # -vf と -filter_complex を同時指定すると ffmpeg がエラーを返すため、
    # concat 出力 [outv] に対して scale フィルタを連結して [outvscaled] を生成し
    # -map [outvscaled] に差し替える。
    use_scale = options.width is not None and options.height is not None
    if use_scale:
        filter_parts.append(f"[outv]scale={options.width}:{options.height}[outvscaled]")
        video_map_label = "[outvscaled]"
    else:
        video_map_label = "[outv]"

    # 字幕段注入（video_map_label 確定直後・ADR-S4-r3）。
    # subtitle=None のとき何もしない（後方互換・ADR-S8）。
    if options.subtitle is not None:
        video_map_label = _append_subtitle_filter(
            filter_parts, video_map_label, options.subtitle
        )

    filter_complex = ";".join(filter_parts)

    # 音声 map 終端ラベルを累積パイプで決定する（ADR-L5b・DC-AM-001）:
    # loudness あり → [outa_ln]、denoise のみ → [outa_dn]、なし → [outa]
    if use_loudness:
        audio_map_label = "[outa_ln]"
    elif use_afftdn:
        audio_map_label = "[outa_dn]"
    else:
        audio_map_label = "[outa]"

    return filter_complex, video_map_label, audio_map_label, use_afftdn, use_loudness


def _resolve_target_spec(
    source_probes: dict[str, ProbeInfo],
    first_source: str,
    options: RenderOptions,
) -> tuple[int, int, float]:
    """出力規格（target_w, target_h, target_fps）を決定して返す（ADR-C4-r2）。

    _build_multi_source_filter_complex から出力規格決定ロジックを分離したヘルパー。
    width/height は両方指定のとき採用、それ以外（両方 None）は先頭ソース基準。
    片方のみ指定は RenderOptions._validate_resolution_pair（DC-AM-004）で弾かれる
    ため、ここに到達する場合は「両方指定」または「両方 None」のどちらかが保証される。

    偶数丸め（ADR-C4-r2・yuv420p の偶数制約）も本関数で適用する。

    Returns:
        (target_w, target_h, target_fps) のタプル。

    Raises:
        ClipwrightError: 先頭ソースの解像度または fps が取得できない場合。
    """
    first_probe = source_probes[first_source]
    if options.width is not None and options.height is not None:
        raw_w = options.width
        raw_h = options.height
    else:
        if first_probe.width is None or first_probe.height is None:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="先頭クリップのソースから解像度を取得できません。",
                hint=(
                    "source_probes の先頭ソースに width/height を設定するか、"
                    " RenderOptions で width/height を両方指定してください。"
                ),
            )
        raw_w = first_probe.width
        raw_h = first_probe.height

    # 偶数丸め（ADR-C4-r2・yuv420p の偶数制約）
    target_w = (raw_w // 2) * 2
    target_h = (raw_h // 2) * 2

    # fps: options.fps 指定ならそれ、なければ先頭ソース fps
    if options.fps is not None:
        target_fps: float = options.fps
    else:
        if first_probe.fps is None:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="先頭クリップのソースから fps を取得できません。",
                hint=(
                    "source_probes の先頭ソースに fps を設定するか、"
                    " RenderOptions で fps を指定してください。"
                ),
            )
        target_fps = first_probe.fps

    return target_w, target_h, target_fps


def _build_clip_filters(
    ranges: list[KeptRange],
    source_index: dict[str, int],
    source_probes: dict[str, ProbeInfo],
    has_audio_overall: bool,
    target_w: int,
    target_h: int,
    target_fps: float,
) -> tuple[list[str], list[str], list[str]]:
    """各クリップの video/audio フィルタ文字列を生成して返す（ADR-C5-r2/C7-r2）。

    _build_multi_source_filter_complex からクリップフィルタ生成ロジックを分離した
    ヘルパー。各クリップの規格統一（fps/scale/pad/setsar）と音声補完（anullsrc）を
    担う。

    Returns:
        (filter_parts, video_labels, audio_labels) のタプル。
    """
    video_labels: list[str] = []
    audio_labels: list[str] = []
    filter_parts: list[str] = []

    for i, r in enumerate(ranges):
        k = source_index[r.source]
        start = _to_seconds(r.source_range.start_time)
        dur = _to_seconds(r.source_range.duration)
        end = round(start + dur, 6)
        vl = f"v{i}"
        # 各クリップ video: trim → setpts → fps → scale(decrease) → pad → setsar
        # fps は小数5桁以上で書く（ADR-C2-r2・NTSC fps 精度）
        filter_parts.append(
            f"[{k}:v]trim=start={start}:end={end},setpts=PTS-STARTPTS,"
            f"fps={target_fps:.5f},"
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,setsar=1[{vl}]"
        )
        video_labels.append(f"[{vl}]")

        if has_audio_overall:
            al = f"a{i}"
            probe = source_probes[r.source]
            if probe.audio_count >= 1:
                # 音声あり: atrim → asetpts → aformat で規格統一
                filter_parts.append(
                    f"[{k}:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,"
                    f"aformat=sample_rates=48000:channel_layouts=stereo[{al}]"
                )
            else:
                # 音声なし: anullsrc で無音補完（映像と同じ秒尺）
                filter_parts.append(
                    f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                    f"atrim=0:{dur},asetpts=PTS-STARTPTS[{al}]"
                )
            audio_labels.append(f"[{al}]")

    return filter_parts, video_labels, audio_labels


def _build_multi_source_filter_complex(
    ranges: list[KeptRange],
    source_index: dict[str, int],
    source_probes: dict[str, ProbeInfo],
    has_audio_overall: bool,
    denoise_directive: DenoiseDirective | None,
    loudness_directive: LoudnessDirective | None,
    options: RenderOptions,
    first_source: str,
) -> tuple[str, str, str, bool, bool]:
    """複数ソース経路の filter_complex を構築する（ADR-C1/C5-r2/C7-r2/C11-r2）。

    各クリップを規格統一（fps/scale/pad/setsar）してから concat する。
    has_audio_overall=True のとき音声なしソースは anullsrc で補完する（ADR-C7-r2）。
    出力ラベルを単一ソース版と統一（[outv]/[outa]・ADR-C11-r2）。

    責務の分担:
    - _resolve_target_spec: 出力規格（target_w/h/fps）の決定
    - _build_clip_filters: 各クリップの video/audio フィルタ文字列の生成
    - 本関数: concat フィルタ組み立て・_append_audio_pipe 呼び出し・戻り値決定

    Returns:
        (filter_complex, video_map_label, audio_map_label, use_afftdn, use_loudness)
    """
    n = len(ranges)

    # 出力規格の決定（ADR-C4-r2）をヘルパーに委譲
    target_w, target_h, target_fps = _resolve_target_spec(
        source_probes, first_source, options
    )

    # 各クリップの video/audio フィルタ文字列を生成
    clip_filter_parts, video_labels, audio_labels = _build_clip_filters(
        ranges,
        source_index,
        source_probes,
        has_audio_overall,
        target_w,
        target_h,
        target_fps,
    )
    # concat フィルタ・audio pipe を後続で追記するためのローカル変数として引き継ぐ
    filter_parts: list[str] = clip_filter_parts

    # concat フィルタ
    v_count = 1
    a_count = 1 if has_audio_overall else 0
    if has_audio_overall:
        interleaved: list[str] = []
        for vl, al in zip(video_labels, audio_labels, strict=True):
            interleaved.append(vl)
            interleaved.append(al)
        input_labels = "".join(interleaved)
    else:
        input_labels = "".join(video_labels)

    concat_output = "[outv]" if not has_audio_overall else "[outv][outa]"
    filter_parts.append(
        f"{input_labels}concat=n={n}:v={v_count}:a={a_count}{concat_output}"
    )

    # denoise/loudness 累積パイプ（単一/複数ソース共通ヘルパー・ADR-C11-r2）
    use_afftdn, use_loudness = _append_audio_pipe(
        filter_parts, has_audio_overall, denoise_directive, loudness_directive
    )

    # 複数ソース経路では options.width/height による後段 scale は行わない
    # （各クリップ前段で規格統一済み・ADR-C5-r2）
    video_map_label = "[outv]"

    # 字幕段注入（video_map_label 確定直後・ADR-S4-r3）。
    # subtitle=None のとき何もしない（後方互換・ADR-S8）。
    if options.subtitle is not None:
        video_map_label = _append_subtitle_filter(
            filter_parts, video_map_label, options.subtitle
        )

    filter_complex = ";".join(filter_parts)

    # 音声 map 終端ラベルを累積パイプで決定する
    if use_loudness:
        audio_map_label = "[outa_ln]"
    elif use_afftdn:
        audio_map_label = "[outa_dn]"
    else:
        audio_map_label = "[outa]"

    return filter_complex, video_map_label, audio_map_label, use_afftdn, use_loudness


def _append_bgm_pipe(
    filter_parts: list[str],
    bgm: BgmClip,
    audio_map_label: str,
    has_main_audio: bool,
    main_dur: float,
    bgm_index: int,
) -> str:
    """BGM 音声チェーンを filter_parts に追記し、新しい audio_map_label を返す。

    ADR-B5-r2/B5-r3 準拠。実機確認済み構文（test-report §5・2026-06-11）に
    厳密に従う（DC-AS-004）。

    has_main_audio=True のとき:
        本編終端ラベル L を aformat して [main_fmt] を作り、BGM と amix する。
        ducking OFF:
            [main_fmt][bgm]amix=inputs=2:normalize=0,alimiter=limit=1.0[outa_bgm]
        ducking ON:
            [main_fmt]asplit→[bgm][main_sc]sidechaincompress→amix→alimiter[outa_bgm]
    has_main_audio=False のとき:
        BGM 単独系統として
        [{bgm_index}:a]aformat...atrim,asetpts,volume,(afade)[outa_bgm]

    -stream_loop -1 は render.py が付与するため plan.py では aloop を使わない
    （ADR-B6-r2）。afade は fade_in_sec > 0 / fade_out_sec > 0 のときのみ注入する
    （ADR-B9-r3）。
    """
    d = bgm.directive
    vol_str = f"{d.volume_db:g}dB"
    dur_str = f"{main_dur:g}"

    # SR M-3: fade 秒数が本編尺を超える場合は意図しない音声出力になるため INVALID_INPUT を送出する。  # noqa: E501
    # BgmOptions には main_dur が不明のため上限制約を持てず、実行時ガードが必要。
    if d.fade_in_sec > main_dur:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="fade_in_sec が本編尺を超えています。",
            hint=f"fade は本編尺 {main_dur:.2f} 秒以下にしてください。",
        )
    if d.fade_out_sec > main_dur:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="fade_out_sec が本編尺を超えています。",
            hint=f"fade は本編尺 {main_dur:.2f} 秒以下にしてください。",
        )

    # BGM 音声チェーン共通部分: aformat → atrim → asetpts → volume → (afade)
    # afade は >0 のときのみ注入（ADR-B9-r3・DC-AM-003）
    bgm_chain = (
        f"[{bgm_index}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        f"atrim=0:{dur_str},asetpts=PTS-STARTPTS,volume={vol_str}"
    )
    if d.fade_in_sec > 0:
        bgm_chain += f",afade=t=in:st=0:d={d.fade_in_sec:g}"
    if d.fade_out_sec > 0:
        st_out = max(0.0, main_dur - d.fade_out_sec)
        bgm_chain += f",afade=t=out:st={st_out:g}:d={d.fade_out_sec:g}"

    if not has_main_audio:
        # 本編無音 + BGM 単独系統（ADR-B5-r2/DC-AS-004）: BGM を直接 [outa_bgm] へ
        filter_parts.append(f"{bgm_chain}[outa_bgm]")
    else:
        # 本編あり: BGM を [bgm] で中間ラベルに出力してから amix する
        filter_parts.append(f"{bgm_chain}[bgm]")

        # 本編終端ラベル L を aformat して [main_fmt] を生成（DC-AS-007）
        filter_parts.append(
            f"{audio_map_label}aformat=sample_rates=48000:channel_layouts=stereo[main_fmt]"
        )

        if d.ducking.enabled:
            # ducking ON: [bgm][main_sc]sidechaincompress 入力順序（DC-AS-006）
            filter_parts.append("[main_fmt]asplit[main_mix][main_sc]")
            filter_parts.append(
                f"[bgm][main_sc]sidechaincompress="
                f"threshold={d.ducking.threshold:g}:ratio={d.ducking.ratio:g}[bgm_duck]"
            )
            filter_parts.append(
                "[main_mix][bgm_duck]amix=inputs=2:normalize=0,alimiter=limit=1.0[outa_bgm]"
            )
        else:
            # ducking OFF: [main_fmt][bgm]amix→alimiter（DC-AM-001）
            filter_parts.append(
                "[main_fmt][bgm]amix=inputs=2:normalize=0,alimiter=limit=1.0[outa_bgm]"
            )

    return "[outa_bgm]"


def _build_ffmpeg_args(
    filter_complex: str,
    video_map_label: str,
    audio_map_label: str,
    has_audio: bool,
    options: RenderOptions,
    use_multi_source: bool = False,
) -> list[str]:
    """filter_complex と map ラベルから ffmpeg 引数リストを組み立てて返す（M-2）。

    filter_complex / -map / codec / fps / crf オプションを一元管理する。
    ffmpeg_args は list[str] に統一し、数値は str() で変換して格納する（M-1）。

    use_multi_source=True の場合、fps は filter_complex 内の各クリップ前段の
    fps フィルタで統一済みのため -r をスキップする（CR M-2）。
    単一ソース経路（use_multi_source=False）では従来どおり -r を追加する（後方互換）。
    """
    ffmpeg_args: list[str] = [
        "-filter_complex",
        filter_complex,
        "-map",
        video_map_label,
    ]
    if has_audio:
        ffmpeg_args += ["-map", audio_map_label]

    # RenderOptions → ffmpeg 引数への写像
    if options.video_codec is not None:
        ffmpeg_args += ["-c:v", options.video_codec]
    if options.audio_codec is not None:
        ffmpeg_args += ["-c:a", options.audio_codec]
    # width/height は filter_complex 内に統合済みのため -vf は追加しない（L-4）
    if options.fps is not None:
        if use_multi_source:
            # 複数ソース経路: filter_complex 内の fps フィルタで統一済みのため
            # -r は冗長（二重適用により意図しない再サンプリングが起きうる・CR M-2）
            pass
        else:
            # 単一ソース経路: 従来どおり -r を追加（後方互換・ADR-C3）
            ffmpeg_args += ["-r", str(options.fps)]
    if options.crf is not None:
        ffmpeg_args += ["-crf", str(options.crf)]

    return ffmpeg_args


def build_plan(
    ranges: list[KeptRange],
    probe_info: ProbeInfo,
    options: RenderOptions,
    denoise: dict[str, Any] | None = None,
    loudness: dict[str, Any] | None = None,
    source_probes: dict[str, ProbeInfo] | None = None,
    bgm: BgmClip | None = None,
) -> RenderPlan:
    """filter_complex 文字列と ffmpeg 引数配列を RenderPlan で返す（ADR-1/ADR-7）。

    本関数は薄いオーケストレーターとして、検証 → filter_complex 構築
    （_build_filter_complex or _build_multi_source_filter_complex）→
    BGM 段追記（_append_bgm_pipe）→
    ffmpeg_args 構築（_build_ffmpeg_args）→ dry_run 概算・警告生成の順で呼び出す。

    - source_probes 未指定 or ユニークソース1個 → 単一ソース経路（後方互換）。
    - ユニークソース ≥ 2 → 複数ソース経路（ADR-C3）。
    - 映像なしは UNSUPPORTED_OPERATION（DC-AS-002）。
    - 区間1件も concat=n=1 を一律使用（DC-AS-005）。
    - 音声0: a=0（-map [outv] のみ）。
    - 音声1以上: a=1、第1音声のみ採用（ADR-7）。
    - trim 座標は opentime→秒（小数6桁）に変換し数値として引数化（DC-AS-004）。
    - filter_complex は単一文字列として返す（コマンドインジェクション回避）。
    - bit_rate が None の場合は estimated_size_bytes=None + warnings 追加（ADR-3）。
    - codec 等のいずれか非 None 指定時に「概算は目安」warning（DC-AM-005）。
    - denoise: afftdn 注入（B-2・architecture-report-20260611-092647）。
      has_audio=True ＋ backend=="afftdn" → concat 後 afftdn を注入し [outa_dn] を生成。
      has_audio=False ＋ denoise → afftdn を入れず warnings に追加。
      backend=="deepfilternet" → UNSUPPORTED_OPERATION。
    - loudness: track loudness 注入（ADR-L5/L5b/L6）。
      loudnorm モード: concat 後（denoise あれば [outa_dn] 後）に
        loudnorm linear=true を注入。
      peak モード: volume フィルタを注入（target_peak - max_volume の差分ゲイン）。
      has_audio=False ＋ loudness → フィルタ非注入 + warnings 追加。
      peak + denoise 併用 → warning 追加（DC-AM-002: 測定タイミングずれ）。
      audio map 終端ラベルは累積パイプ方式で解決（DC-AM-001 ADR-L5b）:
        [outa] → (denoise → [outa_dn]) → (loudness → [outa_ln])
    - source_probes 指定時（ユニークソース ≥ 2）: 各ソースの has_video が False なら
      UNSUPPORTED_OPERATION（ADR-C12）。
    - RenderPlan.input_sources = unique_sources_in_order(ranges)（ADR-C9-r2）。
    - bgm: BgmClip が非 None のとき BGM 段を最終段に追記する（ADR-B4-r2/B5-r2/B5-r3）。
      has_main_audio（本編音声有無）と has_audio_output（最終出力音声有無）を分離する。
      BGM index = len(input_sources)（bgm_source は input_sources 非包含・DC-AS-005）。
      bgm=None は従来完全同一（後方互換・ADR-B7）。
    """
    # denoise 指示を検証する（不正ならここで INVALID_INPUT / UNSUPPORTED_OPERATION）
    denoise_directive: DenoiseDirective | None = None
    if denoise is not None:
        denoise_directive = _validate_denoise_directive(denoise)
        if denoise_directive.backend == "deepfilternet":
            raise ClipwrightError(
                code=ErrorCode.UNSUPPORTED_OPERATION,
                message="backend=deepfilternet は render での適用に未対応です。",
                hint=(
                    "backend=afftdn で再検出するか、deepfilternet の render 対応版を"
                    "お待ちください。"
                ),
            )

    # loudness 指示を検証する（不正ならここで INVALID_INPUT）
    loudness_directive: LoudnessDirective | None = None
    if loudness is not None:
        loudness_directive = _validate_loudness_directive(loudness)

    # ユニークソース一覧（ADR-C9-r2 の単一情報源）
    input_sources = unique_sources_in_order(ranges)
    n = len(ranges)

    # ソース数で経路分岐（ADR-C3）
    use_multi_source = source_probes is not None and len(input_sources) >= 2

    if use_multi_source:
        # 複数ソース経路
        # use_multi_source が True なら source_probes は非 None が保証される
        # （use_multi_source = source_probes is not None and ... の条件による）。
        # assert は -O で除去されるため if-raise で型絞り込みを行う（CR-CT-002）。
        # この防御コードは構造的に到達不能だが、mypy の型絞り込みに必要なため
        # 意図的に残している（CR L-2: 到達不能な防御コードは意図的）。
        if source_probes is None:
            raise ClipwrightError(
                code=ErrorCode.INTERNAL,
                message="source_probes が None です（内部エラー）。",
                hint="build_plan の呼び出し元を確認してください。",
            )
        # SR Info-1: source_probes のキーは render.py の _render_inner が
        # unique_sources_in_order(ranges) の結果（境界検証・存在確認・probe 済み）を
        # dict キーとして構築したものであり、外部から直接注入される経路は存在しない。
        # このキーが input_sources と一致することは render.py 側で保証されている。

        # has_video 混在チェック（ADR-C12）
        for src in input_sources:
            probe = source_probes[src]
            if not probe.has_video:
                basename = os.path.basename(src)
                raise ClipwrightError(
                    code=ErrorCode.UNSUPPORTED_OPERATION,
                    message=(
                        f"映像ストリームを持たないソースが含まれています: {basename}"
                    ),
                    hint=(
                        f"'{basename}' は映像ストリームを持ちません。"
                        " 映像ストリームを持つメディアファイルのみを使用してください。"
                    ),
                )

        # 音声有無の全体判定（ADR-C7-r2）
        has_audio_overall = any(
            source_probes[src].audio_count >= 1 for src in input_sources
        )

        # 先頭ソース（ranges の先頭クリップ）
        first_source = ranges[0].source

        # ソース → index マッピング（ADR-C1）
        source_index: dict[str, int] = {src: i for i, src in enumerate(input_sources)}

        filter_complex, video_map_label, audio_map_label, use_afftdn, use_loudness = (
            _build_multi_source_filter_complex(
                ranges,
                source_index,
                source_probes,
                has_audio_overall,
                denoise_directive,
                loudness_directive,
                options,
                first_source,
            )
        )

        has_audio = has_audio_overall

    else:
        # 単一ソース経路（後方互換・ADR-C3）
        if not probe_info.has_video:
            raise ClipwrightError(
                code=ErrorCode.UNSUPPORTED_OPERATION,
                message="映像ストリームが含まれていません。",
                hint="映像ストリームを持つメディアファイルを使用してください。",
            )

        # 音声有無: 複数音声は第1音声のみ採用（a=1 として扱う）
        has_audio = probe_info.audio_count >= 1

        filter_complex, video_map_label, audio_map_label, use_afftdn, use_loudness = (
            _build_filter_complex(
                ranges, has_audio, denoise_directive, loudness_directive, options
            )
        )

    # ---------- BGM 段の追記（ADR-B5-r2/B5-r3）----------
    # has_main_audio: 本編（concat 後）の音声有無（既存の has_audio 相当）
    # has_audio_output: 最終出力音声有無（has_main_audio or BGM あり）
    has_main_audio = has_audio
    bgm_source_out: str | None = None

    if bgm is not None:
        # BGM index = len(input_sources)（bgm_source は input_sources 非包含・DC-AS-005）  # noqa: E501
        bgm_index = len(input_sources)
        total_duration_for_bgm = sum(
            _to_seconds(r.source_range.duration) for r in ranges
        )

        # filter_complex を filter_parts リストに展開して BGM 段を追記する
        filter_parts_bgm = filter_complex.split(";")
        audio_map_label = _append_bgm_pipe(
            filter_parts_bgm,
            bgm,
            audio_map_label,
            has_main_audio,
            total_duration_for_bgm,
            bgm_index,
        )
        filter_complex = ";".join(filter_parts_bgm)
        has_audio = (
            True  # BGM があるため最終出力には音声がある（has_audio_output=True）
        )
        bgm_source_out = bgm.source

    # ---------- ffmpeg_args 構築 ----------
    ffmpeg_args = _build_ffmpeg_args(
        filter_complex,
        video_map_label,
        audio_map_label,
        has_audio,
        options,
        use_multi_source=use_multi_source,
    )

    # ---------- dry_run 概算 ----------
    total_duration = sum(_to_seconds(r.source_range.duration) for r in ranges)

    estimated_size: float | None = None
    warnings: list[str] = []

    # has_main_audio=False ＋ denoise 指示 → 本編音声なしで denoise スキップ（DC-AM-004）  # noqa: E501
    # 注: BGM の有無に関わらず、本編音声がなければ denoise は適用対象外
    if denoise_directive is not None and not has_main_audio:
        warnings.append(
            "音声なしのため denoise スキップ: afftdn フィルタは適用されませんでした。"
        )

    # has_main_audio=False ＋ loudness 指示 → 本編音声なしで loudness スキップ（DC-AM-004）  # noqa: E501
    if loudness_directive is not None and not has_main_audio:
        warnings.append(
            "音声なしのため loudness スキップ:"
            " loudnorm/volume フィルタは適用されませんでした。"
        )

    # peak + denoise 併用 → 測定タイミングずれの警告（DC-AM-002）
    # peak の max_volume は denoise 前測定値のため、denoise 後の音声に差分適用すると
    # 目標ピークからずれる可能性がある。
    if (
        loudness_directive is not None
        and loudness_directive.mode == "peak"
        and denoise_directive is not None
        and has_main_audio
    ):
        warnings.append(
            "peak モードと denoise の併用:"
            " peak の max_volume は denoise 前の測定値のため、"
            " denoise 後の音声に適用すると目標ピークがずれる可能性があります（DC-AM-002）。"  # noqa: E501
        )

    # 複数ソース（ユニークソース ≥ 2）＋ loudness → 測定値ずれ警告（ADR-C11-r2）
    if loudness_directive is not None and has_main_audio and len(input_sources) >= 2:
        warnings.append(
            "複数ソース合体に track loudness を適用しています。"
            " measured は単一メディア測定値のため、合体後トラック全体への適用は"
            " 厳密にはずれる可能性があります（per_clip loudness は未対応）。"
        )

    # dry_run 概算サイズ（ADR-C10: 先頭ソース bit_rate 基準）
    # 複数ソース時は probe_info（先頭ソース）の bit_rate を代表値とする
    if probe_info.bit_rate is not None:
        estimated_size = probe_info.bit_rate * total_duration / 8.0
        if len(input_sources) >= 2:
            warnings.append(
                "複数ソースのため概算ファイルサイズは目安です。"
                " 先頭ソースの bit_rate を代表値として使用しています。"
            )
    else:
        warnings.append(
            "bit_rate が取得できないため概算ファイルサイズを算出できません。"
        )

    # codec/解像度/fps/crf/audio_codec のいずれかが指定された場合は「概算は目安」warning
    # audio_codec も出力ビットレートを変えるため概算精度に影響する（DC-AM-005 適用）
    if (
        options.video_codec is not None
        or options.audio_codec is not None
        or options.width is not None
        or options.height is not None
        or options.fps is not None
        or options.crf is not None
    ):
        warnings.append(
            "変換オプション（codec/解像度/fps/crf）が指定されているため、"
            "概算ファイルサイズはあくまで目安です。実際のサイズは異なる場合があります。"
        )

    return RenderPlan(
        filter_complex=filter_complex,
        ffmpeg_args=ffmpeg_args,
        segment_count=n,
        total_duration_seconds=total_duration,
        estimated_size_bytes=estimated_size,
        warnings=warnings,
        input_sources=input_sources,
        bgm_source=bgm_source_out,
    )
