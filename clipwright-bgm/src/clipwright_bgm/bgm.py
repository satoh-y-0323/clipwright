"""bgm.py — clipwright-bgm オーケストレーション層（設計 ADR-B1/B2/B3/B8/B10）。

フロー:
  1. 入力検証（timeline 存在・bgm 存在・拡張子ホワイトリスト・境界検証・output 衝突）
  2. timeline ロード
  3. 再呼び出し検出（kind=='bgm' クリップ存在 → INVALID_INPUT・ADR-B2-r3）
  4. BGM 尺取得（core inspect_media 経由・ffprobe 直呼び禁止・ADR-B2-r2）
  5. A2 Audio トラック追加・BGM クリップ配置（BgmDirective co-locate・ADR-B3/B9-r2）
  6. save_timeline（新規出力・入力 timeline 不変・M5）
  7. ok_result 返却

設計判断:
- bgm.py から ffmpeg/ffprobe を subprocess 直呼びしない（OTIO 操作のみ）。
- エラーメッセージは絶対パス非露出・basename のみ（CWE-209・ADR-B10）。
- 再呼び出し検出は kind=='bgm' クリップ存在で判定・"A2" 名非依存（ADR-B2-r3）。
- BGM 拡張子ホワイトリストで許可外拡張子を弾く（DC-AM-007・ADR-B2-r3）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import opentimelineio as otio
from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media
from clipwright.otio_utils import load_timeline, save_timeline

import clipwright_bgm
from clipwright_bgm.schemas import BgmDirective, BgmOptions, DuckingDirective

# BGM 入力として許可する拡張子ホワイトリスト（DC-AM-007・ADR-B2-r3）
# 音声ファイルが主だが、動画も音声トラックを持ちうるため含める
_ALLOWED_BGM_EXTENSIONS: frozenset[str] = frozenset(
    {"mp3", "wav", "m4a", "aac", "flac", "ogg", "opus", "mp4", "mkv", "mov", "webm"}
)


def add_bgm(
    timeline: str,
    bgm: str,
    output: str,
    options: BgmOptions | None = None,
) -> dict[str, Any]:
    """BGM クリップを OTIO タイムラインに追加するパブリック API。

    ClipwrightError を ok=False エンベロープに変換して返す。
    BGM 尺取得は core inspect_media 経由で行い、ffprobe を直呼びしない（ADR-B2-r2）。

    Args:
        timeline: 入力 OTIO タイムラインファイルパス。
        bgm: BGM ファイルパス（音声または動画・許可拡張子ホワイトリスト参照）。
        output: 出力 OTIO タイムラインファイルパス（timeline と別ファイル必須・M5）。
        options: BGM オプション。None の場合は BgmOptions(volume_db=-6.0) を使用する。

    Returns:
        ok_result または error_result のエンベロープ dict。
    """
    try:
        return _add_bgm_inner(timeline, bgm, output, options)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)


def _add_bgm_inner(
    timeline: str,
    bgm: str,
    output: str,
    options: BgmOptions | None,
) -> dict[str, Any]:
    """add_bgm の内部実装。ClipwrightError をそのまま送出する。"""
    resolved_options = options if options is not None else BgmOptions(volume_db=-6.0)

    timeline_path = Path(timeline)
    bgm_path = Path(bgm)
    output_path = Path(output)

    # --- 1. 入力検証 ---

    # timeline 存在確認
    if not timeline_path.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"タイムラインファイルが見つかりません: {timeline_path.name}",
            hint="入力タイムラインファイルのパスが正しいか確認してください。",
        )

    # bgm 存在確認（存在確認は拡張子チェックの前に行う）
    if not bgm_path.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"BGM ファイルが見つかりません: {bgm_path.name}",
            hint="BGM ファイルのパスが正しいか確認してください。",
        )

    # BGM 拡張子ホワイトリスト検証（DC-AM-007・ADR-B2-r3）
    bgm_ext = bgm_path.suffix.lstrip(".").lower()
    if bgm_ext not in _ALLOWED_BGM_EXTENSIONS:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"許可されていない BGM ファイル形式です: .{bgm_ext}",
            hint=(
                f"BGM ファイルは次の拡張子のみ対応しています: "
                f"{', '.join(sorted(_ALLOWED_BGM_EXTENSIONS))}"
            ),
        )

    # BGM パス境界検証: bgm が timeline と同一ディレクトリ配下であること（ADR-B8）
    _check_bgm_within_timeline_dir(bgm_path, timeline_path)

    # output 衝突検証: output == 入力 timeline は禁止（非破壊・M5）
    if _same_path(output_path, timeline_path):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="出力パスと入力タイムラインパスが同一です。",
            hint="出力ファイルパスを入力タイムラインとは別のパスに変更してください。",
        )

    # output 境界検証: output が timeline と同一ディレクトリ配下であること（SR L-3）
    _check_output_within_timeline_dir(output_path, timeline_path)

    # output 衝突検証: 既存ファイルへの上書き禁止（非破壊）
    if output_path.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"出力先ファイルが既に存在します: {output_path.name}",
            hint="出力ファイルパスを既存ファイルと重複しない別のパスに変更してください。",
        )

    # --- 2. timeline ロード ---

    tl = load_timeline(str(timeline_path))

    # --- 3. 再呼び出し検出（DC-AS-002/AM-005・ADR-B2-r3）---
    # kind=='bgm' クリップが既に存在する場合は INVALID_INPUT
    # トラック名 "A2" ではなく kind ベースで判定する
    existing_bgm_clips = _collect_bgm_clips(tl)
    if existing_bgm_clips:
        # hint には既存クリップ名を展開しない
        # （OTIO 由来の制御文字混入を防ぐ・SR L-2・CWE-20）
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="タイムラインに BGM クリップが既に存在します。",
            hint=(
                "既存の BGM クリップが見つかりました。"
                "BGM クリップを持たないタイムラインを指定してください。"
            ),
        )

    # --- 4. BGM 尺取得（core inspect_media 経由・ADR-B2-r2）---
    # inspect_media の失敗は ClipwrightError をキャッチして絶対パス非露出のエラーに整形

    try:
        media_info = inspect_media(str(bgm_path))
    except ClipwrightError as exc:
        # 絶対パスを除去して basename のみのメッセージに置き換える（CWE-209・ADR-B10）
        safe_message = f"BGM ファイルの情報取得に失敗しました: {bgm_path.name}"
        raise ClipwrightError(
            code=exc.code,
            message=safe_message,
            hint=exc.hint,
        ) from None

    # duration を秒に変換
    if media_info.duration is None:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"BGM ファイルの尺を取得できませんでした: {bgm_path.name}",
            hint="有効な音声ストリームを持つ BGM ファイルを指定してください。",
        )

    bgm_duration_sec = media_info.duration.value / media_info.duration.rate
    bgm_rate = media_info.duration.rate

    # --- 5. A2 Audio トラック追加・BGM クリップ配置 ---

    # source_range = BGM メディア全長（0〜bgm_duration）固定（DC-AS-003・ADR-B2-r2）
    source_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, bgm_rate),
        duration=otio.opentime.RationalTime(bgm_duration_sec * bgm_rate, bgm_rate),
    )

    # BGM クリップ metadata に BgmDirective を構築して co-locate（ADR-B3/B9-r2）
    directive = BgmDirective(
        tool="clipwright-bgm",
        version=clipwright_bgm.__version__,
        kind="bgm",
        volume_db=resolved_options.volume_db,
        fade_in_sec=resolved_options.fade_in_sec,
        fade_out_sec=resolved_options.fade_out_sec,
        ducking=DuckingDirective(
            enabled=resolved_options.ducking.enabled,
            threshold=resolved_options.ducking.threshold,
            ratio=resolved_options.ducking.ratio,
        ),
    )

    ref = otio.schema.ExternalReference(target_url=str(bgm_path))
    bgm_clip = otio.schema.Clip(
        name=bgm_path.name,
        media_reference=ref,
        source_range=source_range,
        metadata={"clipwright": directive.model_dump()},
    )

    # A2 Audio トラックを追加して BGM クリップを配置
    a2 = otio.schema.Track(name="A2", kind=otio.schema.TrackKind.Audio)
    a2.append(bgm_clip)
    tl.tracks.append(a2)

    # --- 6. save_timeline（新規出力・入力 timeline 不変・M5）---

    save_timeline(tl, str(output_path))

    # --- 7. ok_result 返却 ---

    summary = (
        f"BGM を追加しました。"
        f" bgm={bgm_path.name}"
        f", volume_db={resolved_options.volume_db}"
        f", fade_in={resolved_options.fade_in_sec}s"
        f", fade_out={resolved_options.fade_out_sec}s"
        f", ducking={'ON' if resolved_options.ducking.enabled else 'OFF'}"
        f", bgm_duration={bgm_duration_sec:.2f}s。"
        f" 出力タイムライン: {output_path.name}"
    )

    return ok_result(
        summary,
        data={
            "bgm": bgm_path.name,
            "volume_db": resolved_options.volume_db,
            "fade_in_sec": resolved_options.fade_in_sec,
            "fade_out_sec": resolved_options.fade_out_sec,
            "ducking_enabled": resolved_options.ducking.enabled,
            "bgm_duration_sec": bgm_duration_sec,
        },
        artifacts=[
            {"role": "timeline", "path": str(output_path), "format": "otio"},
        ],
        warnings=[],
    )


def _collect_bgm_clips(tl: otio.schema.Timeline) -> list[otio.schema.Clip]:
    """timeline の全 Audio トラックから kind=='bgm' の Clip を収集して返す。

    再呼び出し検出とトラック名依存回避のため kind ベースで判定する（ADR-B2-r3）。
    """
    bgm_clips: list[otio.schema.Clip] = []
    for track in tl.tracks:
        if track.kind == otio.schema.TrackKind.Audio:
            for item in track:
                if isinstance(item, otio.schema.Clip):
                    meta = item.metadata.get("clipwright", {})
                    if meta.get("kind") == "bgm":
                        bgm_clips.append(item)
    return bgm_clips


def _check_bgm_within_timeline_dir(bgm_path: Path, timeline_path: Path) -> None:
    """BGM ファイルが timeline と同一ディレクトリ配下であることを検証する（ADR-B8）。

    境界検証: BGM パスが timeline ディレクトリの外ならば PATH_NOT_ALLOWED を送出する。
    resolve() 失敗時は absolute() でフォールバック（Windows 環境考慮）。

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED（境界外）。
    """
    try:
        bgm_resolved = bgm_path.resolve()
        timeline_dir = timeline_path.resolve().parent
    except OSError:
        bgm_resolved = bgm_path.absolute()
        timeline_dir = timeline_path.absolute().parent

    # bgm が timeline_dir 配下であるか判定
    try:
        bgm_resolved.relative_to(timeline_dir)
    except ValueError:
        raise ClipwrightError(
            code=ErrorCode.PATH_NOT_ALLOWED,
            message=(
                f"BGM ファイルがタイムラインのディレクトリ外にあります: {bgm_path.name}"
            ),
            hint="BGM ファイルをタイムラインと同一ディレクトリに配置してください。",
        ) from None


def _check_output_within_timeline_dir(output_path: Path, timeline_path: Path) -> None:
    """output ファイルが timeline と同一ディレクトリ配下であることを検証する（SR L-3）。

    境界検証: output が timeline ディレクトリ外ならば PATH_NOT_ALLOWED を送出する。
    resolve() 失敗時は absolute() でフォールバック（Windows 環境考慮）。

    Raises:
        ClipwrightError: PATH_NOT_ALLOWED（境界外）。
    """
    try:
        output_resolved = output_path.resolve()
        timeline_dir = timeline_path.resolve().parent
    except OSError:
        output_resolved = output_path.absolute()
        timeline_dir = timeline_path.absolute().parent

    # output の親ディレクトリが timeline_dir 配下（または一致）であるか判定
    # output 自身ではなくその親ディレクトリが timeline_dir 内にあれば可
    try:
        output_resolved.parent.relative_to(timeline_dir)
    except ValueError:
        raise ClipwrightError(
            code=ErrorCode.PATH_NOT_ALLOWED,
            message=(
                f"出力パスがタイムラインのディレクトリ外にあります: {output_path.name}"
            ),
            hint="出力ファイルをタイムラインと同一ディレクトリに配置してください。",
        ) from None


def _same_path(a: Path, b: Path) -> bool:
    """2 パスが同一実体を指すかを判定する（resolve 失敗時は文字列比較に退避）。"""
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)
