"""transcribe.py — clipwright-transcribe オーケストレーション層（detect.py 同型）。

入力検証 → inspect_media → モデル解決 → ffmpeg WAV 抽出 → whisper-cli 実行 →
captions で SRT/VTT 生成 → OTIO 構築・保存 → エンベロープ返却 の一連フロー。

設計判断:
- _run_whisper() が ffmpeg WAV 抽出と whisper-cli 起動・JSON 読み込みをカプセル化する
  単一アダプタ関数（TR-AD-01）。将来バックエンド差し替え（faster-whisper 等）は
  この関数のみ置換すればよい。
- whisper バイナリ名・言語自動検出フラグはモジュール定数に隔離する
  （spike-whisper 確定値・e2e 照合で差し替え可能・DC-AS-003/DC-AM-002）。
- モデル解決は resolve_tool を使わず os.path.isfile 検査（モデルは実行ファイルでない・
  DC-AS-003）。options.model_path → env CLIPWRIGHT_WHISPER_MODEL の順。
- marker の marked_range は whisper の秒値（メディア座標）をそのまま使う。
  全尺1clip かつ source_range.start_time=0 のため座標が一致する（DC-AM-001）。
- SRT/VTT のタイムコードと marker 秒値は同一秒値由来（DC-AS-005）。
- エラーは basename のみ・whisper/ffmpeg stderr 生断片はサニタイズ汎用文言に差し替え
  （TR-AD-09・VAD M-1 知見踏襲）。
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media
from clipwright.otio_utils import add_clip, add_marker, new_timeline, save_timeline
from clipwright.process import resolve_tool, run
from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

import clipwright_transcribe
from clipwright_transcribe.captions import Segment, normalize_segments, to_srt, to_vtt
from clipwright_transcribe.schemas import TranscribeOptions

# whisper.cpp の実行ファイル名（spike-whisper 確定値・最新版 whisper-cli・旧版 main）。
# env CLIPWRIGHT_WHISPER が指す実体名と一致させる（DC-AS-003-R）。e2e で照合する。
WHISPER_BINARY_NAME = "whisper-cli"
# 言語自動検出フラグ（spike 確定値・e2e 照合で差替可能（リストのまま）・DC-AM-002）
LANG_AUTO_FLAG: list[str] = ["-l", "auto"]
# marker name の最大表示文字数（超過分は省略・本文は metadata.text に全文・DC-GP-003）。
_MARKER_NAME_MAX = 40
# SUBPROCESS_FAILED/TIMEOUT 時のサニタイズ済み文言（stderr パス漏洩防止・TR-AD-09）。
_SUBPROCESS_SAFE_MESSAGE = "内部サブプロセスが失敗しました"
# whisper モデル未解決時の hint（アクション可能・TR-AD-05）。
_MODEL_MISSING_HINT = (
    "ggml モデルファイルのパスを options.model_path に指定するか、"
    "環境変数 CLIPWRIGHT_WHISPER_MODEL に設定してください。"
    "モデルは whisper.cpp の配布元から入手できます（例: ggml-base.bin）。"
)


def _fmt_sec(sec: float) -> str:
    """秒を「分秒」の人間可読表現に変換する（summary 生成用）。"""
    m = int(sec) // 60
    s = sec - m * 60
    return f"{m}分{s:.1f}秒" if m > 0 else f"{s:.1f}秒"


def _truncate_name(text: str) -> str:
    """marker name 用にテキストを先頭 _MARKER_NAME_MAX 文字へ短縮する（DC-GP-003）。

    超過時は末尾に省略記号 "…" を付ける。本文全文は metadata.text に保持する。
    """
    if len(text) <= _MARKER_NAME_MAX:
        return text
    return text[:_MARKER_NAME_MAX] + "…"


def _sanitize_subprocess_error(exc: ClipwrightError) -> ClipwrightError:
    """run() 由来の SUBPROCESS_FAILED/TIMEOUT を汎用文言に差し替える（TR-AD-09）。

    run() の message には stderr 断片・実行ファイルパスが含まれるため、
    MCP レスポンスへ漏洩させないよう固定文言に置換する。hint は維持する。
    その他のコードはそのまま返す。
    """
    if exc.code in (ErrorCode.SUBPROCESS_FAILED, ErrorCode.SUBPROCESS_TIMEOUT):
        return ClipwrightError(
            code=exc.code,
            message=f"{_SUBPROCESS_SAFE_MESSAGE}（code: {exc.code}）",
            hint=exc.hint,
        )
    return exc


def _resolve_model_path(options: TranscribeOptions) -> str:
    """whisper モデルファイルのパスを解決する（DC-AS-003）。

    解決順: options.model_path → env CLIPWRIGHT_WHISPER_MODEL。
    resolve_tool は使わず os.path.isfile で検査する（モデルは実行ファイルでない）。
    どちらも存在しなければ DEPENDENCY_MISSING を送出する。

    Args:
        options: TranscribeOptions（model_path を参照）。

    Returns:
        存在するモデルファイルの絶対/相対パス。

    Raises:
        ClipwrightError: モデルが見つからない場合（DEPENDENCY_MISSING）。
    """
    candidates: list[str] = []
    if options.model_path is not None:
        candidates.append(options.model_path)
    env_model = os.environ.get("CLIPWRIGHT_WHISPER_MODEL")
    if env_model is not None:
        candidates.append(env_model)

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    raise ClipwrightError(
        code=ErrorCode.DEPENDENCY_MISSING,
        message="whisper のモデルファイルが見つかりません",
        hint=_MODEL_MISSING_HINT,
    )


def _extract_wav(ffmpeg: str, media: str, output_path: str, timeout: float) -> None:
    """ffmpeg で 16kHz mono s16le WAV を一時ファイルに書き出す（TR-AD-01）。

    whisper.cpp は 16kHz mono WAV を要求するため変換する。
    shell=False・引数配列でのみ実行する（サブプロセス規律）。
    """
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-i",
        media,
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-y",
        output_path,
    ]
    run(cmd, timeout=timeout)


def _build_whisper_cmd(
    whisper: str,
    model_path: str,
    wav_path: str,
    prefix: str,
    options: TranscribeOptions,
) -> list[str]:
    """whisper-cli の引数配列を組み立てる（TR-AD-01・DC-AM-002/003）。

    `-oj` で JSON を `<prefix>.json` に出力させる。言語は None で自動検出
    （LANG_AUTO_FLAG）、指定時は `-l <code>`。initial_prompt は `--prompt`。
    """
    cmd = [whisper, "-m", model_path, "-f", wav_path, "-oj", "-of", prefix]
    if options.language is None:
        cmd.extend(LANG_AUTO_FLAG)
    else:
        cmd.extend(["-l", options.language])
    if options.initial_prompt is not None:
        cmd.extend(["--prompt", options.initial_prompt])
    return cmd


def _run_whisper(
    media: str,
    options: TranscribeOptions,
    total_duration_sec: float,
    model_path: str,
) -> tuple[list[Segment], str | None]:
    """ffmpeg WAV 抽出 → whisper-cli 実行 → JSON 正規化を行う単一アダプタ（TR-AD-01）。

    将来バックエンドを差し替える際はこの関数のみ置き換えればよい。
    一時ディレクトリに WAV と JSON を出力させ、元素材ディレクトリを汚さない。

    Args:
        media: 入力メディアファイルの絶対パス。
        options: TranscribeOptions。
        total_duration_sec: 素材の総尺（秒）。timeout 算出に使用。
        model_path: 解決済みモデルファイルパス。

    Returns:
        (正規化済み Segment リスト, 検出言語コード or None) のタプル。

    Raises:
        ClipwrightError: 依存不在（DEPENDENCY_MISSING）・サブプロセス失敗/timeout
            （サニタイズ済み）・JSON パース失敗（SUBPROCESS_FAILED）。
    """
    ffmpeg = resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")
    whisper = resolve_tool(WHISPER_BINARY_NAME, "CLIPWRIGHT_WHISPER")

    # timeout は total 連動。whisper は処理が重いため係数を大きくする（TR-AD-10）
    ffmpeg_timeout = float(max(60, math.ceil(total_duration_sec * 2)))
    whisper_timeout = float(max(300, math.ceil(total_duration_sec * 30)))

    with tempfile.TemporaryDirectory() as tmpdir:
        wav_path = os.path.join(tmpdir, "audio.wav")
        prefix = os.path.join(tmpdir, "transcript")

        try:
            _extract_wav(ffmpeg, media, wav_path, ffmpeg_timeout)
        except ClipwrightError as exc:
            raise _sanitize_subprocess_error(exc) from exc

        cmd = _build_whisper_cmd(whisper, model_path, wav_path, prefix, options)
        try:
            run(cmd, timeout=whisper_timeout)
        except ClipwrightError as exc:
            raise _sanitize_subprocess_error(exc) from exc

        # whisper `-oj -of <prefix>` は <prefix>.json を生成する（DC-AM-003）
        json_path = prefix + ".json"
        try:
            with open(json_path, encoding="utf-8") as f:
                whisper_json: dict[str, Any] = json.load(f)
        except (OSError, json.JSONDecodeError):
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="whisper の出力 JSON を読み込めませんでした",
                hint=(
                    "whisper.cpp のバージョン・引数を確認してください。"
                    "再現条件を添えて報告してください。"
                ),
            ) from None

        # JSON 読み込み・正規化を with ブロック内で完結させ、一時 dir が残存している
        # 間にのみデータを参照することを明示する（CR M-2）
        segments = normalize_segments(whisper_json)
        result = whisper_json.get("result")
        language = result.get("language") if isinstance(result, dict) else None

    return segments, language


def transcribe_media(
    media: str,
    output: str,
    options: TranscribeOptions,
) -> dict[str, Any]:
    """音声を文字起こしして SRT/VTT 字幕と OTIO タイムラインを生成する（TR-AD-04）。

    非破壊: 入力メディアファイルは一切書き換えない。
    出力は新規生成した timeline.otio / SRT / VTT のパスを artifacts に返す。

    Args:
        media: 入力メディアファイルパス（音声必須・映像任意）。
        output: 出力 timeline.otio ファイルパス（media と同一ディレクトリ）。
        options: TranscribeOptions。

    Returns:
        ok_result または error_result のエンベロープ dict。
    """
    try:
        return _transcribe_inner(media, output, options)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)


def _transcribe_inner(
    media: str,
    output: str,
    options: TranscribeOptions,
) -> dict[str, Any]:
    """transcribe_media の内部実装。ClipwrightError をそのまま送出する。"""
    output_path = Path(output)
    media_path = Path(media)

    # --- 1. 出力検証 ---

    if output_path.suffix.lower() != ".otio":
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                f"出力ファイルの拡張子が不正です: {output_path.suffix!r}。"
                ".otio のみ許可されています。"
            ),
            hint="出力ファイルパスの拡張子を .otio にしてください。",
        )

    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                "出力先ディレクトリが存在しません。"
                "指定 output の親ディレクトリを確認してください。"
            ),
            hint="出力先ディレクトリを先に作成してから再実行してください。",
        )

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

    # FILE_NOT_FOUND の message は basename のみに差し替える（TR-AD-09・フルパス非露出）
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

    # output が media と同一ディレクトリ配下にあることを検証（TR-AD-08）。
    # ClipwrightError はこのブロック外で伝播する。OSError は best-effort でスキップ
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
                ),
            )
    except OSError:
        # resolve 失敗（ネットワークパス等）は best-effort でスキップ
        pass

    # 音声ストリーム確認（TR-AD-03）。映像は任意（音声のみ素材を受ける）
    has_audio = any(s.codec_type == "audio" for s in media_info.streams)
    if not has_audio:
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message=f"音声ストリームが見つかりません: {media_path.name}",
            hint=(
                "文字起こしには音声ストリームが必要です。"
                "音声を含むメディアファイルを指定してください。"
            ),
        )

    # duration 確認
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
    abs_media = str(media_path.resolve())

    # --- 3. モデル解決（DC-AS-003）---

    model_path = _resolve_model_path(options)

    # --- 4. whisper 実行（アダプタ）---

    segments, detected_language = _run_whisper(
        abs_media, options, total_duration_sec, model_path
    )

    # 言語: 検出結果優先 → 明示指定 → 不明
    language = detected_language or options.language or "unknown"

    # --- 5. SRT/VTT 生成・書き込み（TR-AD-08）---

    srt_path = output_path.with_suffix(".srt")
    vtt_path = output_path.with_suffix(".vtt")
    srt_path.write_text(to_srt(segments), encoding="utf-8")
    vtt_path.write_text(to_vtt(segments), encoding="utf-8")

    # --- 6. OTIO 構築・保存（TR-AD-04/DC-AM-001/DC-AM-101）---

    timeline = new_timeline(media_path.name)
    v1 = timeline.tracks[0]  # V1（Video）トラック

    # 全尺 1 clip（source_range.start_time=0）
    full_source_range = TimeRangeModel(
        start_time=RationalTimeModel(value=0.0, rate=rate),
        duration=RationalTimeModel(value=media_info.duration.value, rate=rate),
    )
    add_clip(
        v1,
        MediaRef(target_url=abs_media),
        full_source_range,
        name=media_path.name,
        metadata={
            "tool": "clipwright-transcribe",
            "version": clipwright_transcribe.__version__,
            "kind": "transcript-source",
        },
    )

    # 各セグメントを V1 トラックの marker として付与（DC-AM-101）。
    # marked_range は whisper 秒値そのまま（メディア座標=トラック座標・DC-AM-001）
    for seg in segments:
        start_value = seg["start_sec"] * rate
        dur_value = (seg["end_sec"] - seg["start_sec"]) * rate
        marked_range = TimeRangeModel(
            start_time=RationalTimeModel(value=start_value, rate=rate),
            duration=RationalTimeModel(value=dur_value, rate=rate),
        )
        add_marker(
            item=v1,
            marked_range=marked_range,
            name=_truncate_name(seg["text"]),
            metadata={
                "tool": "clipwright-transcribe",
                "version": clipwright_transcribe.__version__,
                "kind": "caption",
                "text": seg["text"],
                "language": language,
            },
        )

    save_timeline(timeline, output)

    # --- 7. エンベロープ返却 ---

    segment_count = len(segments)
    summary = (
        f"言語 {language}・{segment_count} セグメント・"
        f"総尺 {_fmt_sec(total_duration_sec)} を文字起こし。"
        f"{srt_path.name} / {vtt_path.name} / {output_path.name} を生成しました。"
    )

    warnings: list[str] = []
    if segment_count == 0:
        warnings.append(
            "文字起こしセグメントが0件でした（無音または認識失敗の可能性）。"
            "SRT は空・VTT はヘッダのみ・marker は付与されていません。"
            "timeline には全尺1clip のみ含まれます。"
        )

    return ok_result(
        summary,
        data={
            "segment_count": segment_count,
            "language": language,
            "total_duration_seconds": total_duration_sec,
        },
        artifacts=[
            {"role": "timeline", "path": str(output_path), "format": "otio"},
            {"role": "captions", "path": str(srt_path), "format": "srt"},
            {"role": "captions", "path": str(vtt_path), "format": "vtt"},
        ],
        warnings=warnings,
    )
