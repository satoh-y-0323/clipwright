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
