"""detect.py — clipwright-silence オーケストレーション層。

入力検証 → inspect_media → silencedetect 実行・パース → KEEP 導出 →
OTIO 構築・保存 → エンベロープ返却 の一連フローを担う。

設計判断:
- _detect_silence_intervals() が ffmpeg 実行とステドエラーパースをカプセル化し、
  将来バックエンドを差し替えられる（アダプタ抽象・AD-1）。
- source_range の rate は inspect_media の MediaInfo.duration.rate を用い、
  value = 秒 × rate で構築する（DC-AS-003）。
- output は media と同一ディレクトリ配下のパスのみ許可する（DC-AS-001）。
- エラーメッセージにフルパス・ffmpeg 生 stderr を露出しない（basename のみ・M-1）。
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media
from clipwright.otio_utils import add_clip, new_timeline, save_timeline
from clipwright.process import resolve_tool, run
from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

import clipwright_silence
from clipwright_silence.plan import derive_keep_ranges
from clipwright_silence.schemas import DetectSilenceOptions

# silence_start / silence_end 行を抽出する正規表現（DC-AM-003 行頭一致・`.` 小数点固定）
_RE_SILENCE_START = re.compile(r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)")
_RE_SILENCE_END = re.compile(r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)")


def _parse_silence_intervals(
    stderr: str,
    total_duration_sec: float,
) -> list[tuple[float, float]]:
    """silencedetect の stderr から無音区間リストを抽出する。

    行単位でパースし、行頭一致・`.` 小数点固定の正規表現で抽出する（DC-AM-003）。
    対になっていない末尾の silence_start は total_duration_sec で補完する（DC-AM-002）。

    Args:
        stderr: ffmpeg の標準エラー出力文字列。
        total_duration_sec: 素材の総尺（秒）。補完に使用する。

    Returns:
        無音区間のリスト。各要素は (start_sec, end_sec) の tuple。
    """
    intervals: list[tuple[float, float]] = []
    pending_start: float | None = None

    for line in stderr.splitlines():
        m_start = _RE_SILENCE_START.search(line)
        if m_start:
            pending_start = float(m_start.group(1))
            continue

        m_end = _RE_SILENCE_END.search(line)
        if m_end and pending_start is not None:
            end = float(m_end.group(1))
            intervals.append((pending_start, end))
            pending_start = None

    # 末尾 silence_end 欠落 → total_duration で補完（DC-AM-002）
    if pending_start is not None:
        intervals.append((pending_start, total_duration_sec))

    return intervals


def _detect_silence_intervals(
    ffmpeg: str,
    source: str,
    options: DetectSilenceOptions,
    total_duration_sec: float,
) -> list[tuple[float, float]]:
    """ffmpeg silencedetect を実行して無音区間リストを返す（アダプタ抽象・AD-1）。

    将来バックエンドを差し替える際はこの関数のみ置き換えればよい。

    Args:
        ffmpeg: ffmpeg 実行ファイルパス。
        source: 入力メディアファイルパス。
        options: DetectSilenceOptions。
        total_duration_sec: 素材の総尺（秒）。末尾補完に使用する。

    Returns:
        無音区間のリスト。各要素は (start_sec, end_sec)。

    Raises:
        ClipwrightError: SUBPROCESS_FAILED / SUBPROCESS_TIMEOUT（run が送出）。
    """
    # フィルタ文字列は明示フォーマットでロケール非依存（DC-AM-003）
    filter_str = (
        f"silencedetect=noise={options.silence_threshold_db:.3f}dB"
        f":d={options.min_silence_duration:.6f}"
    )
    timeout = max(60, math.ceil(total_duration_sec * 2))

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-i",
        source,
        "-af",
        filter_str,
        "-f",
        "null",
        "-",
    ]
    result = run(cmd, timeout=float(timeout))
    return _parse_silence_intervals(result.stderr, total_duration_sec)


def detect_silence(
    media: str,
    output: str,
    options: DetectSilenceOptions,
) -> dict[str, Any]:
    """無音区間を検出して KEEP 区間の OTIO タイムラインを生成する（AD-2/AD-5）。

    非破壊: 入力メディアファイルは一切書き換えない。
    出力は新規生成した timeline.otio のパスを artifacts に返す。

    フロー:
      1. 出力検証（拡張子・親ディレクトリ・output==media・出力同一ディレクトリ）
      2. inspect_media → 音声/映像ストリーム確認・duration 確認
      3. ffmpeg silencedetect 実行・stderr パース
      4. derive_keep_ranges で KEEP 導出
      5. OTIO タイムライン構築・保存
      6. エンベロープ返却

    Args:
        media: 入力メディアファイルパス。
        output: 出力 timeline.otio ファイルパス（media と同一ディレクトリ）。
        options: DetectSilenceOptions。

    Returns:
        ok_result または error_result のエンベロープ dict。
    """
    try:
        return _detect_inner(media, output, options)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)


def _detect_inner(
    media: str,
    output: str,
    options: DetectSilenceOptions,
) -> dict[str, Any]:
    """detect_silence の内部実装。ClipwrightError をそのまま送出する。"""
    output_path = Path(output)
    media_path = Path(media)

    # --- 1. 出力検証 ---

    # 拡張子は .otio のみ（AD-5）
    if output_path.suffix.lower() != ".otio":
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                f"出力ファイルの拡張子が不正です: {output_path.suffix!r}。"
                ".otio のみ許可されています。"
            ),
            hint="出力ファイルパスの拡張子を .otio にしてください。",
        )

    # 親ディレクトリ存在確認（自動作成しない・AD-5）
    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "出力先ディレクトリが存在しません。"
                "指定 output の親ディレクトリを確認してください。"
            ),
            hint="出力先ディレクトリを先に作成してから再実行してください。",
        )

    # output == media 防止（同一パス上書きを防ぐ）
    try:
        if output_path.resolve() == media_path.resolve():
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="出力パスと入力メディアパスが同一です。",
                hint="出力ファイルパスを入力メディアとは別のパスに変更してください。",
            )
    except OSError as exc:
        if str(output_path) == str(media_path):
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="出力パスと入力メディアパスが同一です。",
                hint="出力ファイルパスを入力メディアとは別のパスに変更してください。",
            ) from exc

    # --- 2. inspect_media → ストリーム・duration 確認 ---

    # inspect_media は FILE_NOT_FOUND / symlink 拒否 / PROBE_FAILED 等を送出する
    media_info = inspect_media(media)

    # output が media と同一ディレクトリ配下にあることを検証（DC-AS-001）
    # inspect_media 後に行う（存在確認済みの状態で resolve する）
    try:
        media_dir = media_path.resolve().parent
        output_dir = output_path.parent.resolve()
        if media_dir != output_dir:
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message=(
                    f"出力 timeline は入力メディアと同じディレクトリに配置してください"
                    f"（入力: {media_path.name}）。"
                ),
                hint=(
                    "output のパスを media ファイルと同じディレクトリ内に"
                    "変更してください。"
                    "（例: output = media と同じディレクトリ / timeline.otio）"
                ),
            )
    except ClipwrightError:
        raise
    except OSError:
        # resolve 失敗（ネットワークパス等）は best-effort でスキップ
        pass

    # 映像ストリーム確認（DC-AS-002）
    has_video = any(s.codec_type == "video" for s in media_info.streams)
    has_audio = any(s.codec_type == "audio" for s in media_info.streams)

    if not has_video:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message=f"映像ストリームが見つかりません: {media_path.name}",
            hint=(
                "本ツールは映像＋音声素材を対象とします。"
                "映像を含むメディアファイルを指定してください。"
            ),
        )

    if not has_audio:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message=f"音声ストリームが見つかりません: {media_path.name}",
            hint=(
                "無音検出には音声ストリームが必要です。"
                "音声を含むメディアファイルを指定してください。"
            ),
        )

    # duration 確認（DC-AS-004）
    if media_info.duration is None:
        raise ClipwrightError(
            code=ErrorCode.PROBE_FAILED,
            message=f"素材の尺を取得できませんでした: {media_path.name}",
            hint=(
                "メディアファイルが破損していないか確認してください。"
                "ffprobe で手動確認することもできます。"
            ),
        )

    total_duration_sec = media_info.duration.value / media_info.duration.rate
    rate = media_info.duration.rate

    # --- 3. ffmpeg silencedetect 実行・stderr パース ---

    ffmpeg = resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")
    abs_media = str(media_path.resolve())

    silence_intervals = _detect_silence_intervals(
        ffmpeg, abs_media, options, total_duration_sec
    )

    # --- 4. KEEP 導出 ---

    keep_ranges = derive_keep_ranges(total_duration_sec, silence_intervals, options)

    # --- 5. OTIO タイムライン構築・保存 ---

    timeline = new_timeline(media_path.name)
    v1 = timeline.tracks[0]  # V1（Video）トラック

    for start_sec, end_sec in keep_ranges:
        start_value = start_sec * rate
        dur_value = (end_sec - start_sec) * rate
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=start_value, rate=rate),
            duration=RationalTimeModel(value=dur_value, rate=rate),
        )
        media_ref = MediaRef(target_url=abs_media)
        add_clip(
            v1,
            media_ref,
            source_range,
            name="keep",
            metadata={
                "tool": "clipwright-silence",
                "version": clipwright_silence.__version__,
                "kind": "keep",
            },
        )

    save_timeline(timeline, output)

    # --- 6. エンベロープ返却 ---

    silence_count = len(silence_intervals)
    keep_count = len(keep_ranges)
    total_silence_seconds = sum(e - s for s, e in silence_intervals)
    total_keep_seconds = sum(e - s for s, e in keep_ranges)

    # 尺の人間可読表現（秒→分秒）
    def _fmt_sec(sec: float) -> str:
        m = int(sec) // 60
        s = sec - m * 60
        return f"{m}分{s:.1f}秒" if m > 0 else f"{s:.1f}秒"

    summary = (
        f"総尺 {_fmt_sec(total_duration_sec)} の素材から"
        f"無音 {silence_count} 区間（合計 {_fmt_sec(total_silence_seconds)}）を検出。"
        f"残す {keep_count} 区間（合計 {_fmt_sec(total_keep_seconds)}）の"
        f"{output_path.name} を生成しました。"
    )

    warnings: list[str] = []
    if keep_count == 0:
        warnings.append(
            "残す区間がありません（全区間が無音判定）。"
            "生成された timeline.otio の V1 トラックは空です。"
            "render に渡すと INVALID_INPUT になります。"
        )

    return ok_result(
        summary,
        data={
            "silence_count": silence_count,
            "total_silence_seconds": total_silence_seconds,
            "keep_count": keep_count,
            "total_keep_seconds": total_keep_seconds,
        },
        artifacts=[{"role": "timeline", "path": str(output_path), "format": "otio"}],
        warnings=warnings,
    )
