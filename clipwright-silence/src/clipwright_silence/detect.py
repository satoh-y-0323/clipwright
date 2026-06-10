"""detect.py — clipwright-silence オーケストレーション層。

入力検証 → inspect_media → silencedetect 実行・パース → KEEP 導出 →
OTIO 構築・保存 → エンベロープ返却 の一連フローを担う。

設計判断:
- _detect_silence_intervals() が ffmpeg 実行とステドエラーパースをカプセル化し、
  将来バックエンドを差し替えられる（アダプタ抽象・AD-1）。
- _detect_vad_silence_intervals() が VAD CLI の別プロセス起動と発話→無音反転を担う。
  両者とも (無音区間リスト) を返す共通契約にし、derive_keep_ranges 以降は共通フロー。
- source_range の rate は inspect_media の MediaInfo.duration.rate を用い、
  value = 秒 × rate で構築する（DC-AS-003）。
- output は media と同一ディレクトリ配下のパスのみ許可する（DC-AS-001）。
- エラーメッセージにフルパス・ffmpeg 生 stderr を露出しない（basename のみ・M-1）。
"""

from __future__ import annotations

import json
import math
import re
import sys
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


def _fmt_sec(sec: float) -> str:
    """秒を「分秒」の人間可読表現に変換する（summary 生成用）。

    フォーマット例: 90.0 → "1分30.0秒"、45.5 → "45.5秒"
    """
    m = int(sec) // 60
    s = sec - m * 60
    return f"{m}分{s:.1f}秒" if m > 0 else f"{s:.1f}秒"


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
        # CR L-2: 対応する silence_start が無い孤立 silence_end の行は無視する。
        # silencedetect は正常時 start→end の対で出力するため孤立 end は異常出力とみなす
        # （先頭からの無音も start が出力されるので対で拾える）。
        if m_end and pending_start is not None:
            end = float(m_end.group(1))
            # SR L-3: end < start の異常区間はスキップする
            # （将来バックエンド差替え時の防御）
            if end < pending_start:
                pending_start = None
                continue
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


def _detect_vad_silence_intervals(
    source: str,
    options: DetectSilenceOptions,
    total_duration_sec: float,
) -> tuple[list[tuple[float, float]], int]:
    """VAD CLI を別プロセス起動して無音区間リストと speech_count を返す。

    VAD-AD-02/04: VAD CLI が返す発話区間を total_duration_sec に対して反転し
    無音区間にする。speech_count は VAD summary 生成のみに使用し、
    共通フローには渡さない（§7.5）。

    Args:
        source: 入力メディアファイルの絶対パス。
        options: DetectSilenceOptions（vad_* フィールドを参照）。
        total_duration_sec: 素材の総尺（秒）。反転に使用。

    Returns:
        (無音区間リスト, speech_count) のタプル。

    Raises:
        ClipwrightError: VAD CLI の error JSON を対応 ErrorCode にマップして送出。
                         run() が非ゼロ終了した場合は SUBPROCESS_FAILED。
    """
    timeout = float(max(60, math.ceil(total_duration_sec * 4)))
    cmd = [
        sys.executable,
        "-m",
        "clipwright_silence.vad_cli",
        "--media",
        source,
        "--threshold",
        f"{options.vad_threshold}",
        "--min-speech",
        f"{options.vad_min_speech_duration}",
        "--min-silence",
        f"{options.vad_min_silence_duration}",
    ]
    result = run(cmd, timeout=timeout)

    payload: dict[str, Any] = json.loads(result.stdout)

    # エラー JSON の場合は ErrorCode にマップして ClipwrightError を送出（§7.1）
    if "error" in payload:
        err = payload["error"]
        raw_code: str = err.get("code", "INTERNAL")
        message: str = err.get("message", "VAD CLI でエラーが発生しました")
        hint: str = err.get("hint", "再現条件を添えて報告してください。")

        # 既知の ErrorCode にマップ。不明なコードは SUBPROCESS_FAILED にフォールバック
        try:
            error_code = ErrorCode(raw_code)
        except ValueError:
            error_code = ErrorCode.SUBPROCESS_FAILED

        raise ClipwrightError(code=error_code, message=message, hint=hint)

    # 発話区間の前処理（§7.4）: クリップ・退化除去
    raw_segments: list[dict[str, Any]] = payload.get("speech_segments", [])
    total = total_duration_sec
    speech_segments: list[tuple[float, float]] = []
    for seg in raw_segments:
        # dict 形式 {"start": ..., "end": ...} または list 形式 [start, end] を許容
        if isinstance(seg, (list, tuple)):
            start, end = float(seg[0]), float(seg[1])
        else:
            start, end = float(seg["start"]), float(seg["end"])
        # start < 0 → 0 でクリップ、end > total → total でクリップ
        start = max(0.0, start)
        end = min(total, end)
        # 退化区間（start >= end）を除去
        if start >= end:
            continue
        speech_segments.append((start, end))

    speech_count = len(speech_segments)

    # 発話区間 → 無音区間へ反転（VAD-AD-04）
    # 発話区間を昇順でソートしてから [0, total] の補集合を取る
    sorted_speech = sorted(speech_segments, key=lambda iv: iv[0])
    silence_intervals: list[tuple[float, float]] = []
    cursor = 0.0
    for s_start, s_end in sorted_speech:
        if s_start > cursor:
            silence_intervals.append((cursor, s_start))
        cursor = max(cursor, s_end)
    if cursor < total:
        silence_intervals.append((cursor, total))

    return silence_intervals, speech_count


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

    # inspect_media は FILE_NOT_FOUND（symlink 拒否含む）/ PROBE_FAILED 等を送出する。
    # SR L-2: FILE_NOT_FOUND の message は basename のみに差し替える
    # （フルパス露出を防ぐ・render._probe の M-1 対応と同方針）。
    try:
        media_info = inspect_media(media)
    except ClipwrightError as exc:
        if exc.code == ErrorCode.FILE_NOT_FOUND:
            raise ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"ファイルが見つかりません: {media_path.name}",
                hint=exc.hint,
            ) from exc
        raise

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

    # --- 3. 検出実行（backend で分岐）---

    abs_media = str(media_path.resolve())

    # speech_count は VAD summary 用途のみ。共通フロー（無音区間リスト）は両経路共通
    speech_count: int | None = None

    if options.backend == "vad":
        # VAD 経路: sys.executable -m clipwright_silence.vad_cli で別プロセス起動
        # resolve_tool は使わない（sys.executable -m で同 venv を保証・VAD-AD-02）
        silence_intervals, speech_count = _detect_vad_silence_intervals(
            abs_media, options, total_duration_sec
        )
    else:
        # silencedetect 経路（既存・backend="silencedetect"）
        ffmpeg = resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")
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
                "backend": options.backend,  # VAD-AD-07
            },
        )

    save_timeline(timeline, output)

    # --- 6. エンベロープ返却 ---

    silence_count = len(silence_intervals)
    keep_count = len(keep_ranges)
    total_silence_seconds = sum(e - s for s, e in silence_intervals)
    total_keep_seconds = sum(e - s for s, e in keep_ranges)

    # summary を backend で出し分け（VAD-AD-08・§7.5）
    if options.backend == "vad" and speech_count is not None:
        _silence_fmt = _fmt_sec(total_silence_seconds)
        _keep_fmt = _fmt_sec(total_keep_seconds)
        summary = (
            f"発話 {speech_count} 区間を検出。"
            f"非発話 {silence_count} 区間（合計 {_silence_fmt}）を除去。"
            f"残す {keep_count} 区間（合計 {_keep_fmt}）の"
            f"{output_path.name} を生成しました。"
        )
    else:
        _silence_fmt = _fmt_sec(total_silence_seconds)
        _keep_fmt = _fmt_sec(total_keep_seconds)
        summary = (
            f"総尺 {_fmt_sec(total_duration_sec)} の素材から"
            f"無音 {silence_count} 区間（合計 {_silence_fmt}）を検出。"
            f"残す {keep_count} 区間（合計 {_keep_fmt}）の"
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
