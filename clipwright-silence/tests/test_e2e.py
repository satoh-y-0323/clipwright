"""test_e2e.py — clipwright-silence 実バイナリ end-to-end ドッグフーディングテスト。

本テストはモックを一切使わず、実 ffmpeg/ffprobe バイナリを使って
detect_silence → render_timeline の連携パイプラインを検証する。

検証フロー（architecture-report-20260610-141050.md / DC-AS-001/002/005）:
  ① ffmpeg lavfi で映像＋音声素材（有音区間＋無音区間＋有音区間）を生成
  ② detect_silence で KEEP clip 列の timeline.otio を生成（V1・target_url 絶対パス）
  ③ render_timeline で timeline.otio を実体化し出力 mp4 の尺が元素材より短いことを確認
  ④ 出力音声の尺も短縮されていることを確認（DC-AS-005: 音声も同座標 trim）
  ⑤ silence→render が規約・OTIO・ファイルパスのみで成立（ドッグフーディング成功）

実行条件:
  - CLIPWRIGHT_FFMPEG / CLIPWRIGHT_FFPROBE が設定済み、または PATH に
    ffmpeg/ffprobe があること。未設定時は pytest.skip する（モックは使わない）。

注意: e2e テストは実バイナリ（ffmpeg/ffprobe/flite TTS 等）を直接呼び出すため、
  subprocess を直接使用している。これはプロダクションコードの process.run 規約
  の意図的な例外であり、e2e テストインフラとして許容される（CR-R-003）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest
from clipwright_render.render import render_timeline
from clipwright_render.schemas import RenderOptions

from clipwright_silence.detect import detect_silence
from clipwright_silence.schemas import DetectSilenceOptions

# ===========================================================================
# ヘルパー
# ===========================================================================

# 無音検出閾値: lavfi anullsrc（完全無音）を確実に検出するため緩めに設定
_SILENCE_DB = -40.0
# 最小無音継続長: 生成する無音区間（2 秒）より短く設定して確実に検出
_MIN_SILENCE_DURATION = 0.5


def _probe_info(
    ffprobe: str,
    path: Path,
) -> dict[str, Any]:
    """ffprobe で動画の format/stream 情報を取得して返す。"""
    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"ffprobe failed: {result.stderr[:200]}"
    return json.loads(result.stdout)  # type: ignore[no-any-return]


def _probe_duration(ffprobe: str, path: Path) -> float:
    """ffprobe で動画の duration（秒）を取得する。"""
    data = _probe_info(ffprobe, path)
    return float(data["format"]["duration"])


def _probe_audio_duration(ffprobe: str, path: Path) -> float:
    """ffprobe で動画の音声ストリーム duration（秒）を取得する。

    フォーマットの duration が動画全体を表すため、音声ストリームの
    duration を別途取得して DC-AS-005（音声 trim）を確認する。
    音声ストリームが無い場合は 0.0 を返す。
    """
    data = _probe_info(ffprobe, path)
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "audio":
            return float(stream.get("duration", 0.0))
    return 0.0


def _make_silent_segment_video(
    ffmpeg: str,
    output: Path,
    audio_sec: float = 3.0,
    silence_sec: float = 2.0,
    audio2_sec: float = 3.0,
) -> float:
    """有音→無音→有音の 3 区間を持つ映像＋音声素材を生成する。

    构成:
      [0, audio_sec)           : testsrc + sine 440Hz（有音）
      [audio_sec, audio_sec + silence_sec) : testsrc + anullsrc（完全無音）
      [audio_sec + silence_sec, total)     : testsrc + sine 440Hz（有音）

    ffmpeg filter_complex で 3 区間を concat して 1 本の mp4 に多重化する。
    映像: testsrc（320x240 @ 25fps・libx264）
    音声: sine/anullsrc（aac）

    Returns:
        生成した素材の総尺（秒）。
    """
    total = audio_sec + silence_sec + audio2_sec
    # filter_complex で 3 セグメントを組み立てる
    # 映像: testsrc を3区間分生成して concat
    # 音声: 区間1/3=sine、区間2=anullsrc（完全無音）
    fc = (
        # 映像ソース（共通 testsrc を trim で切り出す）
        f"[0:v]trim=start=0:end={audio_sec:.3f},setpts=PTS-STARTPTS[va];"
        f"[0:v]trim=start=0:end={silence_sec:.3f},setpts=PTS-STARTPTS[vb];"
        f"[0:v]trim=start=0:end={audio2_sec:.3f},setpts=PTS-STARTPTS[vc];"
        # 音声ソース（sine は [1:a]、anullsrc は [2:a]）
        f"[1:a]atrim=start=0:end={audio_sec:.3f},asetpts=PTS-STARTPTS[aa];"
        f"[2:a]atrim=start=0:end={silence_sec:.3f},asetpts=PTS-STARTPTS[ab];"
        f"[1:a]atrim=start=0:end={audio2_sec:.3f},asetpts=PTS-STARTPTS[ac];"
        # concat: 3 区間を連結（n=3 セグメント、v=1 映像、a=1 音声）
        "[va][aa][vb][ab][vc][ac]concat=n=3:v=1:a=1[outv][outa]"
    )
    cmd = [
        ffmpeg,
        "-y",
        # 入力0: testsrc 映像（total 秒分を生成して trim で切り出す）
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=320x240:rate=25:duration={total:.3f}",
        # 入力1: sine 音声（total 秒分を生成して atrim で切り出す）
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={total:.3f}",
        # 入力2: anullsrc（完全無音・silence_sec 秒分）
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r=44100:cl=stereo:d={silence_sec:.3f}",
        "-filter_complex",
        fc,
        "-map",
        "[outv]",
        "-map",
        "[outa]",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, (
        f"テスト素材の生成に失敗しました: {result.stderr[:300]}"
    )
    return total


# ===========================================================================
# テスト
# ===========================================================================


@pytest.mark.integration
def test_silence_detect_to_render_e2e(
    tmp_path: Path,
    require_ffmpeg: str,
    require_ffprobe: str,
) -> None:
    """ドッグフーディング: detect_silence → render_timeline の完全 e2e 検証。

    DC-AS-001/002/005 の受入基準を実バイナリで確認する。

    検証観点:
      - ① detect_silence が ok=True を返し timeline.otio が生成される
      - ② V1 トラックに KEEP clip 列が含まれる（keep-clip 列・metadata.kind=keep）
      - ③ clip の target_url が media の絶対パスである（DC-AS-001）
      - ④ render_timeline が ok=True を返し出力 mp4 が生成される
      - ⑤ 出力の映像尺が元素材より短い（無音区間がカットされた）
      - ⑥ 出力の音声尺も短縮されている（DC-AS-005: 音声も同座標 trim）
      - ⑦ 元素材・OTIO が非破壊（不変）
      - ⑧ silence→render が規約・OTIO・ファイルパスのみで成立（ドッグフーディング）
    """
    # --- ① 映像＋音声（有音→無音→有音）テスト素材の生成（DC-AS-002） ---
    source = tmp_path / "source.mp4"
    audio_sec = 3.0
    silence_sec = 2.0
    audio2_sec = 3.0
    total_sec = _make_silent_segment_video(
        require_ffmpeg,
        source,
        audio_sec=audio_sec,
        silence_sec=silence_sec,
        audio2_sec=audio2_sec,
    )
    assert source.exists(), "テスト素材が生成されていません"
    source_size_before = source.stat().st_size

    # --- ② detect_silence で KEEP clip 列の timeline.otio を生成 ---
    # DC-AS-001: output timeline は media と同一ディレクトリ（tmp_path）に配置
    otio_path = tmp_path / "cut.otio"
    options = DetectSilenceOptions(
        silence_threshold_db=_SILENCE_DB,
        min_silence_duration=_MIN_SILENCE_DURATION,
        padding=0.05,
        min_keep_duration=0.0,
    )
    detect_result = detect_silence(
        media=str(source),
        output=str(otio_path),
        options=options,
    )

    assert detect_result["ok"] is True, (
        f"detect_silence が失敗しました: {detect_result}"
    )
    assert otio_path.exists(), "timeline.otio が生成されていません"

    # エンベロープ形式の確認
    assert "summary" in detect_result
    assert "data" in detect_result
    data = detect_result["data"]
    assert "keep_count" in data
    assert "silence_count" in data
    assert data["keep_count"] >= 1, (
        f"KEEP 区間が 0 です: data={data}。"
        "silencedetect が無音を検出できていない可能性があります。"
    )
    # artifacts に timeline パスが記録されている
    artifacts = detect_result.get("artifacts", [])
    assert len(artifacts) == 1
    assert Path(artifacts[0]["path"]).resolve() == otio_path.resolve()
    assert artifacts[0]["format"] == "otio"

    # --- ③ OTIO の内容確認: V1 に keep-clip 列・target_url 絶対パス ---
    timeline = otio.adapters.read_from_file(str(otio_path))
    v1 = timeline.tracks[0]
    assert v1.kind == otio.schema.TrackKind.Video, "V1 トラックが Video でありません"
    clips = [c for c in v1 if isinstance(c, otio.schema.Clip)]
    assert len(clips) >= 1, "V1 トラックに clip が含まれていません"

    # target_url が絶対パスであること（DC-AS-001）
    for clip in clips:
        ref = clip.media_reference
        assert isinstance(ref, otio.schema.ExternalReference)
        target_url = ref.target_url
        assert Path(target_url).is_absolute(), (
            f"target_url が絶対パスではありません: {target_url!r}"
        )
        # metadata.clipwright.kind = "keep"
        meta = clip.metadata.get("clipwright", {})
        assert meta.get("kind") == "keep", (
            f"clip.metadata.clipwright.kind が 'keep' でありません: {meta!r}"
        )

    # --- ④ render_timeline で実体化 ---
    output_mp4 = tmp_path / "out.mp4"
    render_result = render_timeline(
        timeline=str(otio_path),
        output=str(output_mp4),
        options=RenderOptions(),
        dry_run=False,
    )
    assert render_result["ok"] is True, (
        f"render_timeline が失敗しました: {render_result}"
    )
    assert output_mp4.exists(), "出力 mp4 が生成されていません"
    assert output_mp4.stat().st_size > 0, "出力 mp4 のサイズが 0 です"

    # --- ⑤ 出力の映像尺が元素材より短い（無音カット確認） ---
    source_duration = _probe_duration(require_ffprobe, source)
    output_duration = _probe_duration(require_ffprobe, output_mp4)
    assert output_duration < source_duration, (
        f"出力尺が元素材以上です: output={output_duration:.3f}s,"
        f" source={source_duration:.3f}s。"
        "無音区間がカットされていません。"
    )
    # 無音区間（2 秒）がカットされているので、出力は元素材より短いはず
    # 許容誤差: エンコーダー GOP 境界による ±1.5 秒
    expected_max = total_sec - silence_sec + 1.5
    assert output_duration <= expected_max, (
        f"出力尺が期待より長すぎます: output={output_duration:.3f}s,"
        f" expected<={expected_max:.3f}s"
    )

    # --- ⑥ 出力音声の尺も短縮されている（DC-AS-005） ---
    output_audio_duration = _probe_audio_duration(require_ffprobe, output_mp4)
    source_audio_duration = _probe_audio_duration(require_ffprobe, source)
    assert output_audio_duration > 0.0, "出力に音声ストリームがありません"
    assert output_audio_duration < source_audio_duration, (
        f"出力音声尺が元素材以上です: output_audio={output_audio_duration:.3f}s,"
        f" source_audio={source_audio_duration:.3f}s。"
        "音声の無音区間がカットされていません（DC-AS-005）。"
    )

    # --- ⑦ 元素材・OTIO が非破壊 ---
    assert source.stat().st_size == source_size_before, "元素材のサイズが変化しました"

    # --- ⑧ ドッグフーディング成功の確認 ---
    # silence→render が規約・OTIO・ファイルパスのみで成立したことを確認。
    # render は silence が生成した timeline.otio を無変更で消費し、
    # V1 keep-clip 列を読んで同座標の映像・音声 trim を実施した。


# ===========================================================================
# VAD backend e2e テスト（DC-AS-007 / §7.8 / task_id: e2e-vad）
# ===========================================================================

# VAD extra 未インストール時のスキップフラグ
_VAD_AVAILABLE = True
try:
    import onnxruntime as _onnxruntime  # noqa: F401
    import silero_vad as _silero_vad  # noqa: F401
except ImportError:
    _VAD_AVAILABLE = False

_SKIP_VAD_REASON = (
    "silero-vad または onnxruntime が import できません。"
    "'pip install clipwright-silence[vad]' で VAD 依存を導入してください。"
)


def _make_vad_test_video(
    ffmpeg: str,
    output: Path,
) -> dict[str, float]:
    """VAD e2e 用テスト素材（flite TTS 発話 + 咳払いノイズバースト）を生成する。

    構成:
      [0, pre_sil)                          : 無音
      [pre_sil, pre_sil+speech_a)           : flite TTS 発話 A（VAD が speech 判定）
      [pre_sil+speech_a, +gap_sil)          : 無音（発話間の間隔）
      [pre_sil+speech_a+gap_sil, +cough)    : 咳払い相当ノイズバースト（大音量・非発話）
      [+cough, +gap_sil2)                   : 無音（咳払い後の間隔）
      [+gap_sil2, +speech_b)                : flite TTS 発話 B（VAD が speech 判定）
      [+speech_b, total)                    : 無音

    音声生成に ffmpeg の libflite TTS エンジン（lavfi:flite）を使用する。
    libflite は権利フリーの音声合成エンジンで、Silero VAD が発話と判定する
    実音声に近い特性を持つ。

    映像は testsrc。音声は flite TTS（発話区間）と anullsrc（無音区間）、
    sine burst（咳払い相当・大音量ノイズ）を filter_complex で結合する。

    Returns:
        各区間の秒数位置を格納した dict。
        keys: pre_sil, speech_a_dur, gap_sil, cough_dur, gap_sil2, speech_b_dur,
              total, cough_start, cough_end
    """
    # 各区間の長さ（秒）
    pre_sil = 0.3
    gap_sil = 0.8  # 発話-咳払い間の無音（VAD が咳払いを発話と判定しないよう間隔を確保）
    cough_dur = 0.2  # 咳払い相当ノイズバースト
    gap_sil2 = 0.8  # 咳払い-発話間の無音
    post_sil = 0.3

    # flite TTS で発話音声の長さを事前に計測
    # 発話テキスト（単語数を絞り、flite が ~1-1.5 秒に収まるよう設定）
    speech_text_a = "hello world"
    speech_text_b = "goodbye world"

    # 発話尺を計測するため一時生成
    def _probe_flite_dur(text: str) -> float:
        """flite TTS の音声尺を計測する。

        ffmpeg の libflite が利用できない環境（returncode != 0 または
        time= パターン非マッチ）の場合は pytest.skip を呼び出す。
        フォールバック値での継続は避ける（CR-E-002）。
        """
        import re  # noqa: PLC0415

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
        # ffmpeg 自体が失敗した場合（libflite 非サポートのビルド等）はスキップ
        if result.returncode != 0:
            pytest.skip(
                f"ffmpeg libflite が利用できません（returncode={result.returncode}）。"
                f"libflite サポート付きの ffmpeg ビルドが必要です。"
                f"stderr: {result.stderr[-200:]}"
            )
        # stderr から duration を取得
        m = re.search(r"time=(\d+):(\d+):([0-9.]+)", result.stderr)
        if m:
            h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            return h * 3600 + mi * 60 + s
        # time= パターン非マッチ = flite TTS が実際に動作していない
        pytest.skip(
            "ffmpeg stderr に time= パターンが見つかりません。"
            "libflite TTS が正常に動作していない可能性があります。"
            f"text={text!r}, stderr={result.stderr[-200:]}"
        )

    speech_a_dur = _probe_flite_dur(speech_text_a)
    speech_b_dur = _probe_flite_dur(speech_text_b)

    total = (
        pre_sil
        + speech_a_dur
        + gap_sil
        + cough_dur
        + gap_sil2
        + speech_b_dur
        + post_sil
    )
    cough_start = pre_sil + speech_a_dur + gap_sil
    cough_end = cough_start + cough_dur

    # filter_complex で全区間を組み立てる
    # 映像: testsrc（total 秒）
    # 音声: 各区間を anullsrc/flite/sine で生成して concat
    #
    # 区間構成:
    #   [A] pre_sil   秒: anullsrc（無音）
    #   [B] speech_a  秒: flite TTS A
    #   [C] gap_sil   秒: anullsrc（無音）
    #   [D] cough_dur 秒: sine burst 大音量（咳払い相当）
    #   [E] gap_sil2  秒: anullsrc（無音）
    #   [F] speech_b  秒: flite TTS B
    #   [G] post_sil  秒: anullsrc（無音）
    #
    # sine の周波数を 1000Hz・振幅大（volume=10）にして silencedetect が
    # 有音として検出できるようにする（-40dB を超えるレベル）。

    srate = 16000

    fc = (
        # --- 音声ソース ---
        # [A] 無音 pre_sil
        f"anullsrc=r={srate}:cl=mono:d={pre_sil:.4f}[aud_a];"
        # [B] flite 発話 A
        f"flite=text='{speech_text_a}':voice=kal16,atrim=start=0:end={speech_a_dur:.4f},"
        f"asetpts=PTS-STARTPTS,aresample={srate}[aud_b];"
        # [C] 無音 gap_sil
        f"anullsrc=r={srate}:cl=mono:d={gap_sil:.4f}[aud_c];"
        # [D] 咳払い相当: 大音量 sine burst（1kHz, amplitude 高）
        f"sine=frequency=1000:beep_factor=1:duration={cough_dur:.4f},"
        f"volume=volume=10,aresample={srate},"
        f"atrim=start=0:end={cough_dur:.4f},asetpts=PTS-STARTPTS[aud_d];"
        # [E] 無音 gap_sil2
        f"anullsrc=r={srate}:cl=mono:d={gap_sil2:.4f}[aud_e];"
        # [F] flite 発話 B
        f"flite=text='{speech_text_b}':voice=kal16,atrim=start=0:end={speech_b_dur:.4f},"
        f"asetpts=PTS-STARTPTS,aresample={srate}[aud_f];"
        # [G] 無音 post_sil
        f"anullsrc=r={srate}:cl=mono:d={post_sil:.4f}[aud_g];"
        # --- concat ---
        "[aud_a][aud_b][aud_c][aud_d][aud_e][aud_f][aud_g]"
        "concat=n=7:v=0:a=1[audio_out]"
    )

    cmd = [
        ffmpeg,
        "-y",
        # 映像ソース
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=320x240:rate=25:duration={total:.4f}",
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
        f"VAD テスト素材の生成に失敗しました: {result.stderr[-400:]}"
    )

    return {
        "pre_sil": pre_sil,
        "speech_a_dur": speech_a_dur,
        "gap_sil": gap_sil,
        "cough_dur": cough_dur,
        "gap_sil2": gap_sil2,
        "speech_b_dur": speech_b_dur,
        "total": total,
        "cough_start": cough_start,
        "cough_end": cough_end,
    }


def _collect_keep_intervals(otio_path: Path) -> list[tuple[float, float]]:
    """OTIO ファイルから V1 トラックの keep clip 区間（秒）を返す。"""
    timeline = otio.adapters.read_from_file(str(otio_path))
    v1 = timeline.tracks[0]
    result = []
    time_cursor = 0.0
    for item in v1:
        if isinstance(item, otio.schema.Clip):
            sr_otio = item.source_range
            if sr_otio is not None:
                start_sec = sr_otio.start_time.value / sr_otio.start_time.rate
                dur_sec = sr_otio.duration.value / sr_otio.duration.rate
                result.append((start_sec, start_sec + dur_sec))
        time_cursor += 0.0  # 絶対座標で返す
    return result


@pytest.mark.integration
@pytest.mark.skipif(not _VAD_AVAILABLE, reason=_SKIP_VAD_REASON)
def test_vad_backend_e2e(
    tmp_path: Path,
    require_ffmpeg: str,
    require_ffprobe: str,
) -> None:
    """VAD backend e2e: 咳払いノイズが VAD では除去・silencedetect では残ることを実証。

    検証フロー（architecture-report §7.8 / DC-AS-007）:
      ① VAD が speech 判定する素材を ffmpeg flite TTS で生成（先行確立）
      ② 発話区間外に咳払い相当ノイズバースト（0.2s）を挿入した mp4 を生成
      ③ backend="vad" で detect → 咳払い区間が KEEP から除かれていることを確認
      ④ backend="silencedetect" で detect → 咳払い区間が KEEP に残ることを対比確認
      ⑤ VAD timeline を render_timeline で実体化 → 出力 mp4 生成・尺短縮を確認
      ⑥ silencedetect backend の非回帰: backend metadata が追加されても
         既存 silencedetect e2e と同様に render まで通ること（DC-GP-002）

    skip 条件（DC-AS-007）:
      - CLIPWRIGHT_FFMPEG / CLIPWRIGHT_FFPROBE 不在
      - silero-vad / onnxruntime が import 不可
    """
    # ==================================================================
    # ① 先行確立: VAD が speech 判定する素材の生成
    # ==================================================================
    source = tmp_path / "vad_source.mp4"
    timing = _make_vad_test_video(require_ffmpeg, source)
    assert source.exists(), "VAD テスト素材が生成されていません"

    cough_start = timing["cough_start"]
    cough_end = timing["cough_end"]

    # flite TTS 音声が実際に VAD で speech 判定されることを先行確認
    # （§7.8: 素材確立の先行ステップ）
    # vad_cli を別プロセス起動して speech_segments が非空であることを確認
    import sys

    vad_check_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "clipwright_silence.vad_cli",
            "--media",
            str(source),
            "--threshold",
            "0.5",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(tmp_path),
    )
    assert vad_check_result.returncode == 0, (
        f"vad_cli 起動に失敗しました: returncode={vad_check_result.returncode}, "
        f"stderr={vad_check_result.stderr[-200:]}"
    )
    vad_payload = json.loads(vad_check_result.stdout)
    assert "error" not in vad_payload, (
        f"vad_cli がエラーを返しました: {vad_payload['error']}"
    )
    speech_segments = vad_payload.get("speech_segments", [])
    assert len(speech_segments) > 0, (
        "vad_cli が speech_segments=0 を返しました。"
        "素材確立に失敗しています。"
        "flite TTS で生成した音声が VAD に発話と判定されませんでした。"
        f"vad_payload={vad_payload}"
    )

    # ==================================================================
    # ③ backend="vad" で detect → 咳払い区間が KEEP から除かれる
    # ==================================================================
    otio_vad = tmp_path / "timeline_vad.otio"
    vad_options = DetectSilenceOptions(
        backend="vad",
        vad_threshold=0.5,
        vad_min_speech_duration=0.1,
        vad_min_silence_duration=0.05,
        padding=0.05,
        min_keep_duration=0.1,
    )
    vad_result = detect_silence(
        media=str(source),
        output=str(otio_vad),
        options=vad_options,
    )

    assert vad_result["ok"] is True, (
        f"VAD backend detect_silence が失敗しました: {vad_result}"
    )
    assert otio_vad.exists(), "VAD backend の timeline.otio が生成されていません"

    vad_keep_intervals = _collect_keep_intervals(otio_vad)
    assert len(vad_keep_intervals) > 0, (
        f"VAD backend の KEEP 区間が 0 です。vad_result={vad_result}"
    )

    # 咳払い区間（cough_start〜cough_end）が VAD の KEEP 区間に含まれていないことを確認
    # （VAD は非発話と判定して除去する）
    cough_center = (cough_start + cough_end) / 2
    vad_cough_covered = any(s <= cough_center <= e for s, e in vad_keep_intervals)
    assert not vad_cough_covered, (
        f"VAD backend が咳払い中心点({cough_center:.3f}s)を KEEP に含めています。"
        f"咳払い区間: [{cough_start:.3f}s - {cough_end:.3f}s]。"
        f"VAD KEEP 区間: {[(round(s, 3), round(e, 3)) for s, e in vad_keep_intervals]}。"  # noqa: E501
        "VAD が咳払い相当のノイズを非発話として除去できていません。"
    )

    # VAD metadata に backend="vad" が記録されている（VAD-AD-07）
    timeline_vad = otio.adapters.read_from_file(str(otio_vad))
    for clip in timeline_vad.tracks[0]:
        if isinstance(clip, otio.schema.Clip):
            meta = clip.metadata.get("clipwright", {})
            assert meta.get("backend") == "vad", (
                f"VAD backend clip の metadata.backend が 'vad' でありません: {meta!r}"
            )
            break

    # ==================================================================
    # ④ backend="silencedetect" で detect → 咳払い区間が KEEP に残る
    # ==================================================================
    otio_sd = tmp_path / "timeline_sd.otio"
    sd_options = DetectSilenceOptions(
        backend="silencedetect",
        silence_threshold_db=-40.0,
        min_silence_duration=0.3,
        padding=0.05,
        min_keep_duration=0.1,
    )
    sd_result = detect_silence(
        media=str(source),
        output=str(otio_sd),
        options=sd_options,
    )

    assert sd_result["ok"] is True, (
        f"silencedetect backend detect_silence が失敗しました: {sd_result}"
    )
    assert otio_sd.exists(), (
        "silencedetect backend の timeline.otio が生成されていません"
    )  # noqa: E501

    sd_keep_intervals = _collect_keep_intervals(otio_sd)
    assert len(sd_keep_intervals) > 0, (
        f"silencedetect backend の KEEP 区間が 0 です。sd_result={sd_result}"
    )

    # 咳払い区間（cough_start〜cough_end）が silencedetect の KEEP 区間に含まれることを確認  # noqa: E501
    # （silencedetect は音量で非無音 → KEEP として残す）
    sd_cough_covered = any(s <= cough_center <= e for s, e in sd_keep_intervals)
    assert sd_cough_covered, (
        f"silencedetect backend が咳払い中心点({cough_center:.3f}s)を KEEP に含めていません。"  # noqa: E501
        f"咳払い区間: [{cough_start:.3f}s - {cough_end:.3f}s]。"
        f"silencedetect KEEP 区間: {[(round(s, 3), round(e, 3)) for s, e in sd_keep_intervals]}。"  # noqa: E501
        "咳払い（大音量ノイズ）が有音として KEEP に残ることを確認できていません。"
    )

    # silencedetect metadata に backend="silencedetect" が記録されている（VAD-AD-07）
    timeline_sd = otio.adapters.read_from_file(str(otio_sd))
    for clip in timeline_sd.tracks[0]:
        if isinstance(clip, otio.schema.Clip):
            meta = clip.metadata.get("clipwright", {})
            assert meta.get("backend") == "silencedetect", (
                f"silencedetect clip の metadata.backend が 'silencedetect' でありません: {meta!r}"  # noqa: E501
            )
            break

    # ==================================================================
    # ⑤ VAD timeline を render_timeline で実体化（成功条件3）
    # ==================================================================
    output_mp4 = tmp_path / "vad_render_out.mp4"
    render_result = render_timeline(
        timeline=str(otio_vad),
        output=str(output_mp4),
        options=RenderOptions(),
        dry_run=False,
    )
    assert render_result["ok"] is True, (
        f"VAD timeline の render_timeline が失敗しました: {render_result}"
    )
    assert output_mp4.exists(), "VAD render の出力 mp4 が生成されていません"
    assert output_mp4.stat().st_size > 0, "VAD render の出力 mp4 サイズが 0 です"

    # 出力 mp4 の尺が元素材より短い（非発話区間がカットされた）
    source_duration = _probe_duration(require_ffprobe, source)
    output_duration = _probe_duration(require_ffprobe, output_mp4)
    assert output_duration < source_duration, (
        f"VAD render 出力尺が元素材以上です: output={output_duration:.3f}s,"
        f" source={source_duration:.3f}s。"
        "非発話区間がカットされていません。"
    )

    # ==================================================================
    # ⑥ silencedetect 経路の非回帰（DC-GP-002）
    # backend metadata 追加後も render まで通ること
    # ==================================================================
    sd_render_out = tmp_path / "sd_render_out.mp4"
    sd_render_result = render_timeline(
        timeline=str(otio_sd),
        output=str(sd_render_out),
        options=RenderOptions(),
        dry_run=False,
    )
    assert sd_render_result["ok"] is True, (
        f"silencedetect timeline の render_timeline が失敗しました: {sd_render_result}"
    )
    assert sd_render_out.exists(), (
        "silencedetect 非回帰: render 出力 mp4 が生成されていません"
    )
    sd_output_duration = _probe_duration(require_ffprobe, sd_render_out)
    assert sd_output_duration < source_duration, (
        f"silencedetect 非回帰: render 出力尺が元素材以上です:"
        f" output={sd_output_duration:.3f}s, source={source_duration:.3f}s。"
    )
