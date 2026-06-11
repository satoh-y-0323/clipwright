"""loudness.py — clipwright-loudness オーケストレーション層（設計 §3.2・ADR-L4/L7）。

フロー:
  1. 出力検証（拡張子・親dir・output==media・output==timeline・同一dir）
  2. inspect_media: 映像＋音声必須チェック
  3. timeline 解決（None → 新規生成 / path → load + 検証）
  4. measure_loudness: ラウドネス測定
  5. measured が None なら loudness 注記を書かず warning（U-1・DC-AM-003）
     measured ありなら loudness 注記を timeline-level metadata に部分更新
  6. save_timeline → ok_result 返却

設計判断:
- FILE_NOT_FOUND / message は basename のみ（DC-GP-005）。
- output は media と同一ディレクトリ（MUST・DC-AS-002）。
- source==media の比較は Path.resolve() で正規化（B-4）。
- timeline 検証: Video kind トラックがちょうど1本（B-5）。
- measured=None: loudness 注記を書かず warning を返す（U-1・DC-AM-003）。
- noise.py の _same_path / _add_full_clip / _load_and_validate_timeline 構造をミラー。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import opentimelineio as otio
from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media
from clipwright.otio_utils import (
    get_clipwright_metadata,
    load_timeline,
    new_timeline,
    save_timeline,
    set_clipwright_metadata,
)
from clipwright.schemas import RationalTimeModel
from pydantic import ValidationError

import clipwright_loudness
from clipwright_loudness.analyze import measure_loudness
from clipwright_loudness.schemas import (
    DetectLoudnessOptions,
    LoudnessDirective,
    LoudnormMeasured,
    LoudnormTarget,
    PeakMeasured,
    PeakTarget,
)


def detect_loudness(
    media: str,
    output: str,
    options: DetectLoudnessOptions,
    timeline: str | None,
) -> dict[str, Any]:
    """ラウドネス検出のパブリック API。ClipwrightError を ok=False に変換して返す。

    Args:
        media: 入力メディアファイルパス（映像＋音声必須）。
        output: 出力 OTIO タイムラインファイルパス（.otio・media と同一dir）。
        options: DetectLoudnessOptions。
        timeline: 既存タイムラインパス（None=新規生成）。

    Returns:
        ok_result または error_result のエンベロープ dict。
    """
    try:
        return _detect_loudness_inner(media, output, options, timeline)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)


def _detect_loudness_inner(
    media: str,
    output: str,
    options: DetectLoudnessOptions,
    timeline: str | None,
) -> dict[str, Any]:
    """detect_loudness の内部実装。ClipwrightError をそのまま送出する。"""
    media_path = Path(media)
    output_path = Path(output)

    # --- 1. 出力検証 ---

    if output_path.suffix.lower() != ".otio":
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"未対応の出力拡張子です: {output_path.suffix!r}",
            hint="出力ファイルの拡張子を .otio にしてください。",
        )

    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="出力先ディレクトリが存在しません。",
            hint="出力先ディレクトリを先に作成してから再実行してください。",
        )

    # output == media 禁止（非破壊・M5）
    if _same_path(output_path, media_path):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="出力パスと入力メディアパスが同一です。",
            hint="出力ファイルパスを入力メディアとは別のパスに変更してください。",
        )

    # output == timeline 禁止（非破壊）
    if timeline is not None and _same_path(output_path, Path(timeline)):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="出力パスと入力タイムラインパスが同一です。",
            hint="出力ファイルパスを入力タイムラインとは別のパスに変更してください。",
        )

    # output は media と同一ディレクトリ（MUST・DC-AS-002）
    try:
        media_resolved_dir = media_path.resolve().parent
        output_resolved_dir = output_path.resolve().parent
    except OSError:
        media_resolved_dir = media_path.absolute().parent
        output_resolved_dir = output_path.absolute().parent

    if media_resolved_dir != output_resolved_dir:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="出力ファイルはメディアファイルと同一ディレクトリに配置する必要があります。",
            hint="output パスをメディアファイルと同じディレクトリに変更してください。",
        )

    # --- 2. inspect_media: 映像＋音声必須 ---

    if not media_path.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"ファイルが見つかりません: {media_path.name}",
            hint="入力メディアファイルのパスが正しいか確認してください。",
        )

    media_info = inspect_media(media)

    has_video = any(s.codec_type == "video" for s in media_info.streams)
    has_audio = any(s.codec_type == "audio" for s in media_info.streams)

    if not has_video:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message=f"映像ストリームが見つかりません: {media_path.name}",
            hint="映像と音声の両方を含むメディアファイルを入力してください。",
        )

    if not has_audio:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message=f"音声ストリームが見つかりません: {media_path.name}",
            hint="映像と音声の両方を含むメディアファイルを入力してください。",
        )

    # duration の取得（_add_full_clip に渡す全長 clip の総尺（秒））
    duration_sec: float = 0.0
    if media_info.duration is not None:
        duration_sec = media_info.duration.value / media_info.duration.rate

    # --- 3. timeline 解決 ---

    if timeline is None:
        # 新規生成: V1 に全長 keep clip を1本追加
        tl = new_timeline(media_path.name)
        _add_full_clip(tl, media_path, duration_sec, media_info.duration)
    else:
        tl = _load_and_validate_timeline(
            timeline, media_path, duration_sec, media_info.duration
        )

    # --- 4. ラウドネス測定 ---

    kwargs: dict[str, Any] = {}
    if options.mode == "loudnorm":
        kwargs = {
            "target_i": options.target_i,
            "target_tp": options.target_tp,
            "target_lra": options.target_lra,
        }
    else:
        kwargs = {"target_peak_db": options.target_peak_db}

    analysis = measure_loudness(media_path, mode=options.mode, **kwargs)

    measured_raw: dict[str, Any] | None = analysis["measured"]
    warnings: list[str] = list(analysis["warnings"])

    # --- 5. loudness 注記を timeline-level metadata に部分更新（U-1 考慮）---

    if measured_raw is None:
        # U-1: 測定不能なら loudness 指示を書かず warning（DC-AM-003）
        # warning は analyze 側で既に追加済みだが、loudness 側でも追記する
        warnings.append(
            "ラウドネス測定値を取得できませんでした。"
            " loudness 指示は書き込みません（U-1）。"
        )
    else:
        # measured ありなら loudness 注記を書く
        if options.mode == "loudnorm":
            target: LoudnormTarget | PeakTarget = LoudnormTarget(
                i=options.target_i,
                tp=options.target_tp,
                lra=options.target_lra,
            )
            try:
                measured_obj: LoudnormMeasured | PeakMeasured | None = LoudnormMeasured(
                    **measured_raw
                )
            except ValidationError:
                # CWE-209: ValidationError 詳細を外部に露出しない
                raise ClipwrightError(
                    code=ErrorCode.INVALID_INPUT,
                    message=(
                        "loudnorm 測定値の検証に失敗しました。"
                        "フィールド型を確認してください。"
                    ),
                    hint="measure_loudness の戻り値を確認してください。",
                ) from None
        else:
            target = PeakTarget(peak_db=options.target_peak_db)
            try:
                measured_obj = PeakMeasured(**measured_raw)
            except ValidationError:
                # CWE-209: ValidationError 詳細を外部に露出しない
                raise ClipwrightError(
                    code=ErrorCode.INVALID_INPUT,
                    message=(
                        "peak 測定値の検証に失敗しました。"
                        "フィールド型を確認してください。"
                    ),
                    hint="measure_loudness の戻り値を確認してください。",
                ) from None

        directive = LoudnessDirective(
            tool="clipwright-loudness",
            version=clipwright_loudness.__version__,
            kind="loudness",
            mode=options.mode,
            scope="track",
            target=target,
            measured=measured_obj,
        )

        existing_meta = get_clipwright_metadata(tl)
        existing_meta["loudness"] = directive.model_dump()
        set_clipwright_metadata(tl, existing_meta)

    # --- 6. save_timeline → ok_result ---

    save_timeline(tl, str(output_path))

    if measured_raw is not None:
        summary = (
            f"{media_path.name} のラウドネス解析が完了しました。"
            f" mode={options.mode}, scope={options.scope}。"
            f" loudness 指示を {output_path.name} に書き込みました。"
        )
    else:
        summary = (
            f"{media_path.name} のラウドネス解析を試みましたが"
            "測定値を取得できませんでした。"
            f" mode={options.mode}, scope={options.scope}。"
            f" loudness 指示は書き込まれていません（U-1）。"
        )

    return ok_result(
        summary,
        data={
            "mode": options.mode,
            "scope": options.scope,
            "measured": measured_raw,
        },
        artifacts=[
            {"role": "timeline", "path": str(output_path), "format": "otio"},
        ],
        warnings=warnings,
    )


def _add_full_clip(
    tl: otio.schema.Timeline,
    media_path: Path,
    duration_sec: float,
    duration_rt: RationalTimeModel | None,
) -> None:
    """timeline の V1/A1 トラックに全長 keep clip を1本追加する（新規生成時）。

    target_url には media_path.resolve() の絶対パスを書く（DC-AS-002）。

    Args:
        duration_rt: Pydantic モデルの RationalTimeModel（OTIO RationalTime ではない）。
            rate 取得用。None の場合は rate=1000.0 にフォールバック。
    """
    try:
        target_url = str(media_path.resolve())
    except OSError:
        target_url = str(media_path.absolute())

    # rate の決定: duration が取れていれば使う、なければ 1000.0
    # RationalTimeModel.rate は Pydantic スキーマで float 型のみ保証されており、
    # gt=0 制約はないため、ゼロ除算は発生しないが保証されていない点に注意。
    # ただし OTIO の RationalTime(duration_sec * rate, rate) 初期化ではゼロ rate でも
    # クラッシュしないため、実害は起きない（OTIO 内部での除算は発生しない）。
    rate = duration_rt.rate if duration_rt is not None else 1000.0

    source_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, rate),
        duration=otio.opentime.RationalTime(duration_sec * rate, rate),
    )
    ref = otio.schema.ExternalReference(target_url=target_url)

    # V1（index 0）と A1（index 1）に同じ clip を追加
    for track in tl.tracks:
        clip = otio.schema.Clip(
            name=media_path.name,
            media_reference=ref,
            source_range=source_range,
        )
        track.append(clip)


def _load_and_validate_timeline(
    timeline_path: str,
    media_path: Path,
    duration_sec: float,
    duration_rt: RationalTimeModel | None,
) -> otio.schema.Timeline:
    """既存 timeline をロードして整合性を検証する（B-4 / B-5）。

    検証内容:
    - V1 clip の target_url が media_path と同一（B-4: パス正規化比較）
    - 単一 source（全 clip が同一 target_url）
    - Video kind トラックがちょうど1本（B-5）

    V1 が空の場合は全長 keep clip を追加して続行する（新規生成相当）。

    Raises:
        ClipwrightError: INVALID_INPUT / OTIO_ERROR。
    """
    tl = load_timeline(timeline_path)

    # --- Video kind トラックがちょうど1本（B-5）---
    video_tracks = [t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video]
    if len(video_tracks) != 1:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                f"タイムラインの Video トラック数が不正です: {len(video_tracks)} 本"
                "（1本のみ対応）"
            ),
            hint="Video トラックが1本の timeline を指定してください。",
        )

    v1 = video_tracks[0]

    # --- 全 clip の target_url を収集して単一 source 検証 ---
    clips = [item for item in v1 if isinstance(item, otio.schema.Clip)]

    if not clips:
        # V1 が空の場合は全長 keep clip を追加して続行する（新規生成相当）
        _add_full_clip(tl, media_path, duration_sec, duration_rt)
        return tl

    urls: set[str] = set()
    for clip in clips:
        ref = clip.media_reference
        if isinstance(ref, otio.schema.ExternalReference):
            urls.add(ref.target_url)

    # --- 境界検証: target_url が timeline 親ディレクトリ配下にあること（SR L-2）---
    # 悪意ある OTIO が任意パスを target_url に埋め込む攻撃への将来保険。
    tl_path = Path(timeline_path)
    for url in urls:
        _check_source_within_timeline_dir(tl_path, url)

    if len(urls) > 1:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message="タイムラインに複数ソースの clip が含まれています。",
            hint="単一ソース（同一メディアファイル）の timeline を指定してください。",
        )

    # --- target_url == media_path 検証（B-4: resolve() 正規化比較）---
    if urls:
        target_url = next(iter(urls))
        try:
            tl_source = Path(target_url).resolve()
            media_resolved = media_path.resolve()
        except OSError:
            tl_source = Path(target_url).absolute()
            media_resolved = media_path.absolute()

        if tl_source != media_resolved:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    f"タイムラインのソースファイルと入力メディアが一致しません。"
                    f" timeline source: {Path(target_url).name}"
                    f" / media: {media_path.name}"
                ),
                hint=(
                    "timeline を生成したときと同じメディアファイルを指定してください。"
                ),
            )

    return tl


def _check_source_within_timeline_dir(timeline_path: Path, source: str) -> None:
    """source パスが timeline 親ディレクトリ配下にあることを検証する（SR L-2）。

    OTIO target_url に任意パスが埋め込まれた悪意ある OTIO への対策。
    render.py の _check_source_within_timeline_dir と同等の境界検証を行う。

    Args:
        timeline_path: OTIO タイムラインファイルのパス。
        source: OTIO target_url から取得したメディアソースパス。

    Raises:
        ClipwrightError: INVALID_INPUT（source がタイムライン親 dir 境界外の場合）。
    """
    try:
        allowed_base = timeline_path.parent.resolve()
        source_resolved = Path(source).resolve()
        source_str = str(source_resolved)
        base_str = str(allowed_base)
        if not (
            source_str == base_str
            or source_str.startswith(base_str + "/")
            or source_str.startswith(base_str + "\\")
        ):
            raise ClipwrightError(
                # render.py と同じ PATH_NOT_ALLOWED を使う（SR-r2 L-1）
                code=ErrorCode.PATH_NOT_ALLOWED,
                message=(
                    "source ファイルがタイムラインのディレクトリ境界外を指しています。"
                ),
                hint=(
                    "OTIO タイムラインと同じディレクトリ配下の"
                    "ソースファイルを使用してください。"
                ),
            )
    except ClipwrightError:
        raise
    except OSError:
        # resolve() 失敗時は best-effort としてスキップする。
        # 後続の source==media 比較で不正なパスは INVALID_INPUT として顕在化する。
        pass


def _same_path(a: Path, b: Path) -> bool:
    """2 パスが同一実体を指すかを判定する（resolve 失敗時は文字列比較に退避）。"""
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)
