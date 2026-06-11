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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

import opentimelineio as otio
from clipwright.errors import ClipwrightError, ErrorCode
from pydantic import BaseModel, Field, ValidationError, model_validator

from clipwright_render.schemas import RenderOptions

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


@dataclass
class ProbeInfo:
    """ffprobe の probe 結果を表す値オブジェクト（DC-AM-007）。

    plan.py は本型を引数で受け取り、subprocess を一切呼ばない。
    bit_rate: None の場合は概算サイズが算出不能（ADR-3）。
    """

    has_video: bool
    audio_count: int
    bit_rate: int | None = None


@dataclass
class RenderPlan:
    """build_plan が返す実行計画。

    filter_complex: ffmpeg -filter_complex 引数の単一文字列（インジェクション防止）。
    ffmpeg_args: ffmpeg へ渡す引数リスト（-fc 以外）。str のみ（M-1）。
    segment_count: 残区間数。
    total_duration_seconds: 出力総尺（秒）。
    estimated_size_bytes: 概算ファイルサイズ（bytes）。bit_rate None 時は None。
    warnings: dry_run 概算の注意事項。
    """

    filter_complex: str
    ffmpeg_args: list[str]
    segment_count: int
    total_duration_seconds: float
    estimated_size_bytes: float | None = None
    warnings: list[str] = field(default_factory=list)


# ===========================================================================
# resolve_kept_ranges
# ===========================================================================


def resolve_kept_ranges(timeline: otio.schema.Timeline) -> list[KeptRange]:
    """先頭 video トラックの Clip を走査し、残区間リストを返す（ADR-5/DC-AS-006）。

    - Gap はスキップ（除去領域の表現のため）。
    - Transition が含まれる場合は UNSUPPORTED_OPERATION を送出する。
    - video トラックが2本以上ある場合は UNSUPPORTED_OPERATION を送出する。
    - すべての Clip が同一 target_url を持つことを検証する（単一ソース前提）。
      不一致の場合は UNSUPPORTED_OPERATION を送出する。
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
    first_source: str | None = None

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
            # 単一ソース検証（DC-AS-007）
            if first_source is None:
                first_source = source
            elif source != first_source:
                raise ClipwrightError(
                    code=ErrorCode.UNSUPPORTED_OPERATION,
                    message="複数の source が検出されました（単一ソースのみ対応）。",
                    hint="単一ソースファイルのみを使用してください。",
                )
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
# build_plan
# ===========================================================================


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

    # denoise afftdn 注入（B-2・architecture-report-20260611-092647）
    # 順序: trim/atrim → concat → afftdn（has_audio ＋ afftdn のときのみ）→ scale
    # afftdn（audio チェーン）と scale（video チェーン）は独立ラベルで競合しない。
    # has_audio=False ＋ denoise の場合は afftdn を入れず warnings 追加は build_plan 側で行う。  # noqa: E501
    use_afftdn = False
    if (
        denoise_directive is not None
        and denoise_directive.backend == "afftdn"
        and has_audio
    ):
        params = AfftdnParams(**denoise_directive.params)
        # ロケール非依存フォーマット: g 形式で数値を書く（余分な0を省略）
        nr_str = f"{params.nr:g}"
        nf_str = f"{params.nf:g}"
        nt_str = params.nt
        filter_parts.append(
            f"[outa]afftdn=nr={nr_str}:nf={nf_str}:nt={nt_str}[outa_dn]"
        )
        use_afftdn = True

    # loudness 注入（ADR-L5/L5b）
    # 注入順序: trim/atrim → concat → denoise afftdn（あれば）
    #   → track loudness（あれば）→ scale
    # audio map 終端ラベルは累積パイプ方式で解決する（DC-AM-001）。
    # [outa] →（denoise → [outa_dn]）→（track loudness → [outa_ln]）
    use_loudness = False
    if loudness_directive is not None and has_audio:
        # 累積パイプ: denoise あり → [outa_dn] を起点、なし → [outa] を起点
        loudness_input_label = "[outa_dn]" if use_afftdn else "[outa]"

        if loudness_directive.mode == "loudnorm":
            # linear 二段適用: measured_* を数値ロケール非依存フォーマットで渡す
            target = loudness_directive.target
            measured = loudness_directive.measured
            # assert の代わりに if-raise を使う: assert は -O 最適化で除去されるが
            # if-raise は除去されない（CR-CT-002 最適化耐性 + mypy 型絞り込み両立）。
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
            # peak: max_volume との差分ゲインを volume フィルタで当てる（ADR-L2）
            target = loudness_directive.target
            measured = loudness_directive.measured
            # assert の代わりに if-raise を使う（CR-CT-002 最適化耐性 + mypy 型絞り込み両立）。  # noqa: E501
            if not isinstance(target, PeakTarget) or not isinstance(  # noqa: E501
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


def _build_ffmpeg_args(
    filter_complex: str,
    video_map_label: str,
    audio_map_label: str,
    has_audio: bool,
    options: RenderOptions,
) -> list[str]:
    """filter_complex と map ラベルから ffmpeg 引数リストを組み立てて返す（M-2）。

    filter_complex / -map / codec / fps / crf オプションを一元管理する。
    ffmpeg_args は list[str] に統一し、数値は str() で変換して格納する（M-1）。
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
) -> RenderPlan:
    """filter_complex 文字列と ffmpeg 引数配列を RenderPlan で返す（ADR-1/ADR-7）。

    本関数は薄いオーケストレーターとして、検証 → filter_complex 構築
    （_build_filter_complex）→ ffmpeg_args 構築（_build_ffmpeg_args）→
    dry_run 概算・警告生成の順で呼び出す（M-2 責務分離）。

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
    """
    if not probe_info.has_video:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message="映像ストリームが含まれていません。",
            hint="映像ストリームを持つメディアファイルを使用してください。",
        )

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

    # 音声有無: 複数音声は第1音声のみ採用（a=1 として扱う）
    has_audio = probe_info.audio_count >= 1
    n = len(ranges)

    # ---------- filter_complex 構築 ----------
    filter_complex, video_map_label, audio_map_label, use_afftdn, use_loudness = (
        _build_filter_complex(
            ranges, has_audio, denoise_directive, loudness_directive, options
        )
    )

    # ---------- ffmpeg_args 構築 ----------
    ffmpeg_args = _build_ffmpeg_args(
        filter_complex, video_map_label, audio_map_label, has_audio, options
    )

    # ---------- dry_run 概算 ----------
    total_duration = sum(_to_seconds(r.source_range.duration) for r in ranges)

    estimated_size: float | None = None
    warnings: list[str] = []

    # has_audio=False ＋ denoise 指示 → 音声なしで denoise スキップ（DC-AS-005）
    if denoise_directive is not None and not has_audio:
        warnings.append(
            "音声なしのため denoise スキップ: afftdn フィルタは適用されませんでした。"
        )

    # has_audio=False ＋ loudness 指示 → 音声なしで loudness スキップ
    if loudness_directive is not None and not has_audio:
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
        and has_audio
    ):
        warnings.append(
            "peak モードと denoise の併用:"
            " peak の max_volume は denoise 前の測定値のため、"
            " denoise 後の音声に適用すると目標ピークがずれる可能性があります（DC-AM-002）。"  # noqa: E501
        )

    if probe_info.bit_rate is not None:
        estimated_size = probe_info.bit_rate * total_duration / 8.0
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
    )
