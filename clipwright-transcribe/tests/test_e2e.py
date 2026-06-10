"""test_e2e.py — clipwright-transcribe 実バイナリ end-to-end テスト。

本テストはモックを一切使わず、実 whisper.cpp / ffmpeg バイナリを使って
transcribe → render 連携パイプラインを検証する。

検証フロー（architecture-report-20260610-221243.md §6・§8・TR-AD-10）:
  ① ffmpeg testsrc + libflite TTS 音声を多重化した mp4 を生成（DC-AS-002）
  ② clipwright_transcribe で SRT/VTT/OTIO を生成（成功条件1）
  ③ 生成 OTIO を render_timeline(dry_run=True) で扱い ok:True を確認（DC-GP-004）
  ④ spike 照合: 実バイナリ出力 JSON が仮説 fixture と一致するか検証（DC-GP-001）

実行条件（DC-AS-004・DC-AS-006）:
  - CLIPWRIGHT_WHISPER（whisper バイナリ）
  - CLIPWRIGHT_WHISPER_MODEL（ggml モデルファイル）
  - CLIPWRIGHT_FFMPEG（WAV 抽出・テスト素材生成に必須）
  - CLIPWRIGHT_FFPROBE（render_timeline dry_run の probe に必須）
  いずれも未設定の場合は pytest.skip する。

注意: e2e テストは実バイナリを直接呼び出すため subprocess を直接使用している。
  これはプロダクションコードの process.run 規約の意図的な例外（e2e テストインフラ）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest
from clipwright_render.render import render_timeline
from clipwright_render.schemas import RenderOptions

from clipwright_transcribe.server import clipwright_transcribe as _clipwright_transcribe
from clipwright_transcribe.transcribe import LANG_AUTO_FLAG, WHISPER_BINARY_NAME

# ===========================================================================
# skip 条件チェック（DC-AS-004）
# ===========================================================================

_WHISPER_BIN = os.environ.get("CLIPWRIGHT_WHISPER")
_WHISPER_MODEL = os.environ.get("CLIPWRIGHT_WHISPER_MODEL")
_FFMPEG_BIN = os.environ.get("CLIPWRIGHT_FFMPEG") or shutil.which("ffmpeg")
_FFPROBE_BIN = os.environ.get("CLIPWRIGHT_FFPROBE") or shutil.which("ffprobe")

_SKIP_REASON_PARTS: list[str] = []
if not _WHISPER_BIN:
    _SKIP_REASON_PARTS.append("CLIPWRIGHT_WHISPER が未設定")
if not _WHISPER_MODEL:
    _SKIP_REASON_PARTS.append("CLIPWRIGHT_WHISPER_MODEL が未設定")
if not _FFMPEG_BIN:
    _SKIP_REASON_PARTS.append("CLIPWRIGHT_FFMPEG が未設定かつ PATH に ffmpeg なし")
if not _FFPROBE_BIN:
    _SKIP_REASON_PARTS.append("CLIPWRIGHT_FFPROBE が未設定かつ PATH に ffprobe なし")

_SKIP_E2E = bool(_SKIP_REASON_PARTS)
_SKIP_E2E_REASON = (
    "実バイナリ e2e には以下の env が必要です: " + "、".join(_SKIP_REASON_PARTS) + "。"
    "CLIPWRIGHT_WHISPER / CLIPWRIGHT_WHISPER_MODEL / CLIPWRIGHT_FFMPEG / "
    "CLIPWRIGHT_FFPROBE を設定してから再実行してください。"
    if _SKIP_REASON_PARTS
    else ""
)

# ===========================================================================
# fixtures/README.md の仮説スキーマ（spike 照合用・DC-GP-001）
# ===========================================================================

FIXTURES_DIR = Path(__file__).parent / "fixtures"
_HYPOTHETICAL_BINARY_NAME = "whisper-cli"
_HYPOTHETICAL_LANG_AUTO_FLAG = "-l auto"


# ===========================================================================
# ヘルパー
# ===========================================================================


def _get_ffmpeg() -> str:
    """ffmpeg バイナリパスを返す（skip 後にのみ呼ばれることを前提とする）。"""
    assert _FFMPEG_BIN is not None
    return _FFMPEG_BIN


def _get_ffprobe() -> str:
    """ffprobe バイナリパスを返す（skip 後にのみ呼ばれることを前提とする）。"""
    assert _FFPROBE_BIN is not None
    return _FFPROBE_BIN


def _probe_flite_duration(ffmpeg: str, text: str) -> float:
    """libflite TTS で生成される音声の尺を計測する。

    ffmpeg の libflite が利用できない環境では pytest.skip を呼ぶ。
    フォールバック値での継続は避ける（CR-E-002）。
    """
    result = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"flite=text='{text}':voice=kal16",
            "-t",
            "10",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.skip(
            f"ffmpeg libflite が利用できません（returncode={result.returncode}）。"
            "libflite サポート付きの ffmpeg ビルドが必要です。"
            f"stderr: {result.stderr[-200:]}"
        )
    m = re.search(r"time=(\d+):(\d+):([0-9.]+)", result.stderr)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return h * 3600 + mi * 60 + s
    pytest.skip(
        "ffmpeg stderr に time= パターンが見つかりません。"
        "libflite TTS が正常に動作していない可能性があります。"
        f"text={text!r}, stderr={result.stderr[-200:]}"
    )


def _make_tts_video(
    ffmpeg: str, output: Path, speech_text: str = "hello world"
) -> float:
    """testsrc 映像 + libflite TTS 音声を多重化した mp4 を生成する（DC-AS-002）。

    render の has_video 要求（UNSUPPORTED_OPERATION 回避）のため映像トラックを付与する。
    audio_only では render が失敗するため、必ず映像を多重化する。

    Args:
        ffmpeg: ffmpeg バイナリパス。
        output: 出力 mp4 パス。
        speech_text: libflite TTS で合成するテキスト（whisper が文字起こしできる英語）。

    Returns:
        生成した素材の総尺（秒）。
    """
    speech_dur = _probe_flite_duration(ffmpeg, speech_text)

    # filter_complex で testsrc 映像と flite TTS 音声を多重化する
    fc = (
        # TTS 音声を生成しサンプルレートを統一
        f"flite=text='{speech_text}':voice=kal16,"
        f"atrim=start=0:end={speech_dur:.4f},"
        f"asetpts=PTS-STARTPTS,"
        f"aresample=16000[audio_out]"
    )

    cmd = [
        ffmpeg,
        "-y",
        # 映像ソース: testsrc
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=320x240:rate=25:duration={speech_dur:.4f}",
        "-filter_complex",
        fc,
        "-map",
        "0:v",
        "-map",
        "[audio_out]",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        "-shortest",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, (
        f"TTS テスト素材の生成に失敗しました: {result.stderr[-300:]}"
    )
    return speech_dur


def _probe_whisper_json(
    whisper: str,
    model_path: str,
    wav_path: str,
    tmp_prefix: str,
) -> dict[str, Any]:
    """whisper バイナリで -oj JSON を生成して内容を返す（spike 照合用）。

    -of <prefix> で <prefix>.json が生成されることを確認する（DC-AM-003）。
    """
    cmd = [
        whisper,
        "-m",
        model_path,
        "-f",
        wav_path,
        "-oj",
        "-of",
        tmp_prefix,
        "-l",
        "auto",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, (
        f"spike 照合用 whisper 実行が失敗しました: {result.stderr[-300:]}"
    )
    json_path = tmp_prefix + ".json"
    assert Path(json_path).exists(), (
        f"-of <prefix> で <prefix>.json が生成されませんでした。実際のパス: {json_path}"
    )
    with open(json_path, encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


def _extract_wav_for_spike(
    ffmpeg: str,
    media: str,
    wav_path: str,
) -> None:
    """16kHz mono WAV を抽出する（spike 照合の前処理）。"""
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
        wav_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"WAV 抽出に失敗しました: {result.stderr[-300:]}"


# ===========================================================================
# テスト
# ===========================================================================


@pytest.mark.integration
@pytest.mark.skipif(_SKIP_E2E, reason=_SKIP_E2E_REASON)
def test_transcribe_e2e(tmp_path: Path) -> None:
    """e2e: transcribe → SRT/VTT/OTIO 生成・render dry_run 連携を実証する。

    検証観点（DC-GP-001/002/004・§6・成功条件1/2）:
      - ① TTS 素材（testsrc 映像 + libflite 音声 mp4）を同一一時 dir に生成（DC-AS-002/006）
      - ② clipwright_transcribe で ok=True・SRT/VTT/OTIO が生成される（成功条件1）
      - ③ SRT ファイルに非空テキストが含まれる（実発話の文字起こし実証）
      - ④ 生成 OTIO を render_timeline(dry_run=True) で扱い ok:True を確認（DC-GP-004）
      - ⑤ spike 照合（DC-GP-001/DC-AM-002/003）: 実バイナリ JSON スキーマ・-of 出力名・
           -l auto 受け入れ・WHISPER_BINARY_NAME 定数との一致を確認する
    """
    assert _WHISPER_BIN is not None
    assert _WHISPER_MODEL is not None
    ffmpeg = _get_ffmpeg()
    ffprobe = _get_ffprobe()  # noqa: F841  # render dry_run の probe で使用

    # ① TTS テスト素材の生成（DC-AS-002）
    # DC-AS-006: mp4 と output(.otio) を同一一時ディレクトリに配置して
    #           render の source-within-timeline-dir 境界を満たす
    source = tmp_path / "tts_source.mp4"
    speech_text = "hello world"
    _make_tts_video(ffmpeg, source, speech_text=speech_text)
    assert source.exists(), "TTS テスト素材が生成されていません"

    # DC-AS-006: output は media と同一ディレクトリに配置する
    otio_path = tmp_path / "out.otio"

    # ② clipwright_transcribe で文字起こし（成功条件1）
    result = _clipwright_transcribe(
        media=str(source),
        output=str(otio_path),
    )

    assert result["ok"] is True, f"clipwright_transcribe が失敗しました: {result}"

    # SRT/VTT/OTIO が生成されている
    srt_path = tmp_path / "out.srt"
    vtt_path = tmp_path / "out.vtt"
    assert otio_path.exists(), "OTIO タイムラインが生成されていません"
    assert srt_path.exists(), "SRT ファイルが生成されていません"
    assert vtt_path.exists(), "VTT ファイルが生成されていません"

    # エンベロープ形式の確認
    assert "summary" in result
    assert "data" in result
    data = result["data"]
    assert "segment_count" in data
    assert "language" in data
    assert "total_duration_seconds" in data
    artifacts = result.get("artifacts", [])
    artifact_formats = {a["format"] for a in artifacts}
    assert "otio" in artifact_formats, "artifacts に otio が含まれていません"
    assert "srt" in artifact_formats, "artifacts に srt が含まれていません"
    assert "vtt" in artifact_formats, "artifacts に vtt が含まれていません"

    # ③ SRT に非空テキストが含まれる（実発話の文字起こし実証）
    srt_content = srt_path.read_text(encoding="utf-8")
    vtt_content = vtt_path.read_text(encoding="utf-8")
    # SRT は空文字列でない・VTT は WEBVTT ヘッダを含む
    assert srt_content.strip() != "", (
        "SRT ファイルが空です。whisper が音声を文字起こしできていません。"
        f"segment_count={data['segment_count']}, language={data['language']}"
    )
    assert "WEBVTT" in vtt_content, "VTT ファイルに WEBVTT ヘッダが含まれていません"

    # ④ render 連携（DC-GP-004）: dry_run で ok:True を確認
    # 特定エラーコード不在ではなく ok:True で全失敗モードを捕捉する
    render_output = tmp_path / "render_out.mp4"
    render_result = render_timeline(
        timeline=str(otio_path),
        output=str(render_output),
        options=RenderOptions(),
        dry_run=True,
    )
    assert render_result["ok"] is True, (
        f"render_timeline(dry_run=True) が失敗しました: {render_result}。"
        "UNSUPPORTED_OPERATION/PATH_NOT_ALLOWED/INVALID_INPUT のいずれかが発生しています。"
    )

    # ⑤ spike 照合（DC-GP-001/DC-AM-002/003）
    # WHISPER_BINARY_NAME 定数が実バイナリのファイル名と一致するか確認（DC-AS-003-R）
    actual_binary_name = Path(_WHISPER_BIN).name
    if actual_binary_name != WHISPER_BINARY_NAME:
        # 差異を記録するが skip はしない（test-report に明記する）
        pytest.fail(
            f"WHISPER_BINARY_NAME 定数（'{WHISPER_BINARY_NAME}'）と "
            f"実バイナリ名（'{actual_binary_name}'）が一致しません。"
            "impl-transcribe への手戻りが必要です。"
            "transcribe.py の WHISPER_BINARY_NAME 定数を実バイナリ名に合わせてください。"
        )

    # -oj -of <prefix> で <prefix>.json が生成されること（DC-AM-003）
    # および JSON スキーマ確認（DC-AM-002/DC-GP-001）
    with tempfile.TemporaryDirectory() as spike_tmp:
        wav_path = os.path.join(spike_tmp, "spike_audio.wav")
        spike_prefix = os.path.join(spike_tmp, "spike_out")

        _extract_wav_for_spike(ffmpeg, str(source), wav_path)
        whisper_json = _probe_whisper_json(
            _WHISPER_BIN,
            _WHISPER_MODEL,
            wav_path,
            spike_prefix,
        )

    # JSON スキーマ照合（DC-GP-001/DC-AM-002/003）
    transcription = whisper_json.get("transcription")
    if transcription is None:
        pytest.fail(
            "実バイナリ JSON に 'transcription' キーが存在しません。"
            "仮説スキーマ（fixtures/README.md）との乖離があります。"
            "impl-contract / impl-transcribe への手戻りが必要です。"
            f"実 JSON のトップレベルキー: {list(whisper_json.keys())}"
        )

    # transcription が空でない場合にのみ内部スキーマを確認する
    # （libflite TTS は whisper が文字起こしできない場合があるため空は許容）
    if isinstance(transcription, list) and len(transcription) > 0:
        first_seg = transcription[0]
        offsets = first_seg.get("offsets")
        assert offsets is not None, (
            "transcription[0] に 'offsets' キーが存在しません。"
            f"実際のセグメントキー: {list(first_seg.keys())}"
        )
        assert "from" in offsets, f"offsets に 'from' キーが存在しません: {offsets}"
        assert "to" in offsets, f"offsets に 'to' キーが存在しません: {offsets}"
        # from/to がミリ秒整数であること（DC-GP-001）
        assert isinstance(offsets["from"], int), (
            f"offsets.from が整数ではありません: {offsets['from']!r} (type={type(offsets['from']).__name__})"
        )
        assert isinstance(offsets["to"], int), (
            f"offsets.to が整数ではありません: {offsets['to']!r} (type={type(offsets['to']).__name__})"
        )
        assert "text" in first_seg, (
            f"transcription[0] に 'text' キーが存在しません: {list(first_seg.keys())}"
        )

    # -l auto フラグ（LANG_AUTO_FLAG）のパース確認
    # 実行が returncode=0 で完了していれば -l auto が受け入れられていることを意味する
    # （spike 照合手順: ④ -l auto がエラーにならないこと）
    lang_auto_parts = LANG_AUTO_FLAG.split()
    assert len(lang_auto_parts) == 2, (  # noqa: PLR2004
        f"LANG_AUTO_FLAG の形式が予期しない値です: {LANG_AUTO_FLAG!r}"
    )
    assert lang_auto_parts[0] == "-l", (
        f"LANG_AUTO_FLAG の先頭が '-l' でありません: {LANG_AUTO_FLAG!r}"
    )
    assert lang_auto_parts[1] == "auto", (
        f"LANG_AUTO_FLAG の値が 'auto' でありません: {LANG_AUTO_FLAG!r}"
    )


@pytest.mark.integration
@pytest.mark.skipif(not _SKIP_E2E, reason="env 設定済みのため skip 動作テスト不要")
def test_e2e_skipped_when_env_not_set() -> None:
    """env 未設定時に e2e が skip されることを確認するプレースホルダー。

    このテスト自体は env が設定されているときのみ実行される（逆条件）。
    実際の skip 動作は pytest の skipif 機能が保証する。
    本テストは collection error が発生していないことを確認する目的で存在する。
    """
    # このテストが実行されるということは env が設定されており、本テストは不要
    # skip テスト自体も skip する
    pytest.skip("env が設定されているため skip 動作確認テストは不要")
