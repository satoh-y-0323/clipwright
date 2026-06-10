"""vad_cli.py — Silero VAD バックエンドの別プロセス小 CLI。

MCP サーバープロセスから import されない（§2.4 subprocess 疎結合）。
detect.py が sys.executable -m clipwright_silence.vad_cli として別プロセス起動する。

CLI 契約（§7.1 一本化）:
  - main(argv) は全例外をトップレベルで捕捉し、必ず stdout JSON を出して return 0。
  - 正常: {"speech_segments": [[start_sec, end_sec], ...]}
  - エラー: {"error": {"code": str, "message": str, "hint": str}}
  - stdout は JSON のみ。ログ・進捗は stderr へ。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import sys
import tempfile
import wave
from os.path import basename
from typing import Any

import numpy as np
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.process import resolve_tool, run

# サンプリングレート固定（§7.3）
_SAMPLE_RATE = 16000
# pip install ヒント文字列
_VAD_INSTALL_HINT = (
    "`pip install 'clipwright-silence[vad]'` で VAD 依存を導入してください。"
)


def _error_output(code: str, message: str, hint: str) -> None:
    """エラー JSON を stdout に出力する。フルパスは basename に変換する。"""
    result: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "hint": hint,
        }
    }
    print(json.dumps(result, ensure_ascii=False), file=sys.stdout)


def _extract_pcm(ffmpeg: str, media: str, output_path: str, timeout: float) -> None:
    """ffmpeg で 16kHz mono s16le PCM を一時ファイルに書き出す。

    shell=False・引数配列でのみ実行（サブプロセス規律）。
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
        str(_SAMPLE_RATE),
        "-ac",
        "1",
        "-y",
        output_path,
    ]
    run(cmd, timeout=timeout)


def _load_audio_as_float32(
    pcm_path: str,
) -> tuple[np.ndarray, int]:
    """PCM WAV ファイルを読み込み float32 numpy array と sample_rate を返す。

    int16 → float32 正規化（/32768.0）を行う。
    """
    with wave.open(pcm_path, "rb") as wf:
        n_frames = wf.getnframes()
        sample_rate = wf.getframerate()
        raw = wf.readframes(n_frames)

    audio_int16 = np.frombuffer(raw, dtype=np.int16)
    audio_float32: np.ndarray = audio_int16.astype(np.float32) / 32768.0
    return audio_float32, sample_rate


def main(argv: list[str] | None = None) -> int:
    """VAD CLI エントリポイント。

    全例外をトップレベルで捕捉し、stdout に JSON を出力して return 0（§7.1）。

    Args:
        argv: コマンドライン引数リスト。None の場合は sys.argv[1:] を使う。

    Returns:
        終了コード（常に 0）。
    """
    # --- 引数パース ---
    parser = argparse.ArgumentParser(
        description="Silero VAD で発話区間を検出して JSON で stdout 出力する。"
    )
    parser.add_argument("--media", required=True, help="入力メディアファイルパス")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="発話確率しきい値 (0.0–1.0, デフォルト: 0.5)",
    )
    parser.add_argument(
        "--min-speech",
        type=float,
        default=0.25,
        help="最小発話長（秒, デフォルト: 0.25）",
    )
    parser.add_argument(
        "--min-silence",
        type=float,
        default=0.1,
        help="発話間の最小無音長（秒, デフォルト: 0.1）",
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse の --help や必須引数欠落による SystemExit を捕捉する
        _error_output(
            code=ErrorCode.INVALID_INPUT,
            message=f"引数解析に失敗しました: exit code {exc.code}",
            hint="--media <path> を必須引数として指定してください。",
        )
        return 0

    media: str = args.media
    threshold: float = args.threshold
    min_speech_sec: float = args.min_speech
    min_silence_sec: float = args.min_silence

    try:
        # --- silero_vad 遅延 import（サーバープロセスへ漏らさない・§2.4）---
        try:
            import silero_vad
        except ImportError as exc:
            _error_output(
                code=ErrorCode.DEPENDENCY_MISSING,
                message=f"silero-vad または onnxruntime が見つかりません: {exc}",
                hint=_VAD_INSTALL_HINT,
            )
            return 0

        # --- silero-vad モデルロード（ffmpeg より先に行い ImportError を早期捕捉）---
        # onnxruntime 欠落時に load_silero_vad が ImportError を上げる（§7.3）
        model = silero_vad.load_silero_vad(onnx=True)

        # --- ffmpeg 解決（§7.2）---
        ffmpeg = resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")

        # --- ffmpeg で 16kHz mono s16le PCM を一時ファイルに生成（§7.3）---
        # ffmpeg 内側 timeout は外側より短く設定する（§7.7）
        ffmpeg_timeout = float(max(30, math.ceil(120.0)))  # 暫定: 120秒

        tmp_path: str = ""
        audio_float32: np.ndarray
        sample_rate: int

        # delete=False で開いて name を取得し、try/finally で確実に削除する（§7.3）
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
            tmp_path = tmp_file.name
        try:
            _extract_pcm(ffmpeg, media, tmp_path, timeout=ffmpeg_timeout)
            audio_float32, sample_rate = _load_audio_as_float32(tmp_path)
        finally:
            # 例外時も確実に削除（§7.3）
            if tmp_path and os.path.exists(tmp_path):
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)

        # get_speech_timestamps はサンプル単位で返す
        # min_speech_duration_ms / min_silence_duration_ms はミリ秒単位
        raw_segments: list[dict[str, Any]] = silero_vad.get_speech_timestamps(
            audio_float32,
            model,
            threshold=threshold,
            sampling_rate=sample_rate,
            min_speech_duration_ms=int(min_speech_sec * 1000),
            min_silence_duration_ms=int(min_silence_sec * 1000),
            return_seconds=False,
        )

        # サンプル単位 → 秒換算（昇順で組み立て）
        speech_segments: list[list[float]] = []
        for seg in sorted(raw_segments, key=lambda s: s["start"]):
            start_sec = float(seg["start"]) / sample_rate
            end_sec = float(seg["end"]) / sample_rate
            speech_segments.append([start_sec, end_sec])

        result: dict[str, Any] = {"speech_segments": speech_segments}
        print(json.dumps(result, ensure_ascii=False), file=sys.stdout)
        return 0

    except ClipwrightError as exc:
        # core run() が送出した ClipwrightError（SUBPROCESS_FAILED/TIMEOUT）と
        # resolve_tool の DEPENDENCY_MISSING をここで捕捉する（§7.1/§7.2）
        _error_output(
            code=str(exc.code),
            message=exc.message,
            hint=exc.hint,
        )
        return 0

    except ImportError as exc:
        # load_silero_vad 等で onnxruntime の ImportError が伝播した場合
        _error_output(
            code=ErrorCode.DEPENDENCY_MISSING,
            message=f"VAD 依存ライブラリのロードに失敗しました: {exc}",
            hint=_VAD_INSTALL_HINT,
        )
        return 0

    except Exception as exc:
        # 想定外の例外もすべて捕捉して error JSON を返す（§7.1）
        _error_output(
            code=ErrorCode.INTERNAL,
            message=(f"VAD CLI で予期しないエラーが発生しました: {basename(str(exc))}"),
            hint="再現条件を添えて報告してください。",
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
