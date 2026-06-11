"""noise.py — clipwright-noise オーケストレーション層（設計 §1.1）。

フロー:
  1. 出力検証（拡張子・親dir・output==media・output==timeline・同一dir）
  2. inspect_media: 映像＋音声必須チェック（ADR-N8）
  3. timeline 解決（None → 新規生成 / path → load + 検証）
  4. measure_noise: astats でノイズフロア測定 → params 算出
  5. denoise 指示を timeline-level metadata に部分更新
  6. save_timeline → ok_result 返却

設計判断:
- FILE_NOT_FOUND / SUBPROCESS_FAILED の message は basename のみ（DC-GP-005）。
- output は media と同一ディレクトリ（MUST・DC-AS-002）。
- source==media の比較は Path.resolve() で正規化（DC-AS-003 / B-4）。
- timeline 検証: Video kind トラックがちょうど1本（B-5）。
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

import clipwright_noise
from clipwright_noise.analyze import measure_noise
from clipwright_noise.schemas import DenoiseDirective, DetectNoiseOptions


def detect_noise(
    media: str,
    output: str,
    options: DetectNoiseOptions,
    timeline: str | None,
) -> dict[str, Any]:
    """ノイズ検出のパブリック API。ClipwrightError を ok=False に変換して返す。

    Args:
        media: 入力メディアファイルパス（映像＋音声必須）。
        output: 出力 OTIO タイムラインファイルパス（.otio・media と同一dir）。
        options: DetectNoiseOptions。
        timeline: 既存タイムラインパス（None=新規生成）。

    Returns:
        ok_result または error_result のエンベロープ dict。
    """
    try:
        return _detect_noise_inner(media, output, options, timeline)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)


def _detect_noise_inner(
    media: str,
    output: str,
    options: DetectNoiseOptions,
    timeline: str | None,
) -> dict[str, Any]:
    """detect_noise の内部実装。ClipwrightError をそのまま送出する。"""
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

    # --- 2. inspect_media: 映像＋音声必須（ADR-N8 / DC-AS-003）---

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

    # --- 4. ノイズ解析 ---

    analysis = measure_noise(
        media_path=media_path,
        strength=options.strength,
        backend=options.backend,
    )

    params: dict[str, Any] = analysis["params"]
    measured: float | None = analysis["measured_noise_floor_db"]
    warnings: list[str] = list(analysis["warnings"])

    # --- 5. denoise 指示を timeline-level metadata に部分更新 ---

    directive = DenoiseDirective(
        tool="clipwright-noise",
        version=clipwright_noise.__version__,
        kind="denoise",
        backend=options.backend,
        params=params,
        measured_noise_floor_db=measured,
    )

    existing_meta = get_clipwright_metadata(tl)
    existing_meta["denoise"] = directive.model_dump()
    set_clipwright_metadata(tl, existing_meta)

    # deepfilternet 選択時は render 未対応 warning を追加（DC-GP-003）
    if options.backend == "deepfilternet":
        warnings.append(
            "backend=deepfilternet が選択されました。"
            "render 適用は未対応です（初版 afftdn のみ）。"
            " afftdn で再検出するか将来版をお待ちください。"
        )

    # --- 6. save_timeline → ok_result ---

    save_timeline(tl, str(output_path))

    summary = (
        f"{media_path.name} のノイズ解析が完了しました。"
        f" backend={options.backend}, strength={options.strength}。"
        f" denoise 指示を {output_path.name} に書き込みました。"
        + (
            f" 測定ノイズフロア: {measured:.1f} dB。"
            if measured is not None
            else " ノイズフロア測定不能のため既定値を使用しました。"
        )
    )

    return ok_result(
        summary,
        data={
            "backend": options.backend,
            "strength": options.strength,
            "measured_noise_floor_db": measured,
            "params": params,
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
    """
    try:
        target_url = str(media_path.resolve())
    except OSError:
        target_url = str(media_path.absolute())

    # rate の決定: duration が取れていれば使う、なければ 1000.0
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
    """既存 timeline をロードして整合性を検証する（DC-AM-003 / DC-AM-004 / B-4 / B-5）。

    検証内容:
    - V1 clip の target_url が media_path と同一（B-4: パス正規化比較）
    - 単一 source（全 clip が同一 target_url）
    - Video kind トラックがちょうど1本（B-5）

    V1 が空の場合は全長 keep clip を追加して続行する（render が INVALID_INPUT に
    ならない renderable な timeline にするため・新規生成相当の扱い）。

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

    # --- 全 clip の target_url を収集して単一 source 検証（DC-AM-004）---
    clips = [item for item in v1 if isinstance(item, otio.schema.Clip)]

    if not clips:
        # V1 が空の場合は全長 keep clip を追加して続行する（新規生成相当）。
        # render の resolve_kept_ranges が「Clip が0件」を INVALID_INPUT で弾くため、
        # クリップを追加しておくことで renderable な timeline にする。
        _add_full_clip(tl, media_path, duration_sec, duration_rt)
        return tl

    urls: set[str] = set()
    for clip in clips:
        ref = clip.media_reference
        if isinstance(ref, otio.schema.ExternalReference):
            urls.add(ref.target_url)

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
            # OSError 時は best-effort: absolute() で比較
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


def _same_path(a: Path, b: Path) -> bool:
    """2 パスが同一実体を指すかを判定する（resolve 失敗時は文字列比較に退避）。"""
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)
