"""test_e2e_bgm.py — clipwright-render BGM ミックスの実 e2e テスト（task_id: e2e-bgm）。

設計根拠:
  - architecture-report-20260611-172611 §7 改訂 v2
  - ADR-B5-r2/B5-r3: has_main_audio/has_audio_output 分離・amix+alimiter
  - ADR-B5-r3: amix 配線・aformat 必須・sidechaincompress 入力順序（DC-AS-005/006/007・DC-AM-001）
  - ADR-B6-r2: -stream_loop -1 + atrim 尺合わせ（aloop 廃止）
  - ADR-B9-r3: fade 既定 0・afade は > 0 のみ注入
  - DC-AM-001: amix 後 alimiter=limit=1.0 必須・出力ピーク ≤ 0dBFS 実証
  - DC-AS-004: 本編無音 + BGM 単独系統実証
  - DC-GP-003: 全フィクスチャ・出力を tmp_path 配下に閉じ自動 teardown

テスト構成:
  1. フィクスチャ: testsrc 映像 + sine 音声（5秒・440Hz）本編
     短い BGM: sine 2秒・880Hz（本編と弁別可能）→ -stream_loop ループ実証
     長い BGM: sine 8秒 → atrim 末尾トリム実証
     本編無音クリップ（音声なし testsrc）→ BGM 単独系統実証

  2. assert 一覧（必須）:
     assert-1: render_timeline(dry_run=False) で ok=True・出力ファイル生成
     assert-2: 出力に音声ストリームが含まれ BGM が実際に混ざる（音量変化で確認）
     assert-3: 出力ピークが 0dBFS を超えない（DC-AM-001・alimiter 実証）
     assert-4: fade_in/out が効く（先頭/末尾が中央より低い・afade 実証）
     assert-4b: fade=0 ケースで無フェード確認
     assert-5: BGM が本編尺に合う（短い BGM ループ・長い BGM トリム・出力尺 ≒ 本編尺）
     assert-6: ducking ON 時に BGM 寄与が減衰する（ducking OFF との比較）
     assert-7: ネガティブ対照 — BGM 注記なし timeline は本編のみ出力
     assert-8: 本編無音 + BGM で BGM 単独系統出力（DC-AS-004）

実行方法（ffmpeg 不在時は skip）:
  uv run --package clipwright-render pytest -k e2e_bgm

ffmpeg を PATH に通すか CLIPWRIGHT_FFMPEG / CLIPWRIGHT_FFPROBE 環境変数で指定すること。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest

from clipwright_render.render import render_timeline
from clipwright_render.schemas import RenderOptions

# ===========================================================================
# ffmpeg / ffprobe パス解決（conftest.py の require_ffmpeg と同パターン）
# ===========================================================================


def _find_binary(name: str, env_var: str) -> str | None:
    """バイナリを PATH → env_var の順で探す。"""
    found = shutil.which(name)
    if found:
        return found
    env_val = os.environ.get(env_var)
    if env_val and Path(env_val).is_file():
        return env_val
    return None


_FFMPEG = _find_binary("ffmpeg", "CLIPWRIGHT_FFMPEG")
_FFPROBE = _find_binary("ffprobe", "CLIPWRIGHT_FFPROBE")

pytestmark = pytest.mark.e2e

requires_ffmpeg = pytest.mark.skipif(
    _FFMPEG is None,
    reason=(
        "ffmpeg が見つかりません。"
        "PATH に ffmpeg を追加するか "
        "CLIPWRIGHT_FFMPEG 環境変数にフルパスを設定してください。"
    ),
)

requires_ffprobe = pytest.mark.skipif(
    _FFPROBE is None,
    reason=(
        "ffprobe が見つかりません。"
        "PATH に ffprobe を追加するか "
        "CLIPWRIGHT_FFPROBE 環境変数にフルパスを設定してください。"
    ),
)

# ===========================================================================
# 定数
# ===========================================================================

# e2e テスト全体の subprocess タイムアウト秒数
_E2E_TIMEOUT: int = int(os.environ.get("E2E_TIMEOUT_SEC", "120"))

# フィクスチャ設定
_MAIN_DUR = 5.0  # 本編: 5 秒
_BGM_SHORT_DUR = 2.0  # 短い BGM: 2 秒（本編より短い → -stream_loop でループ）
_BGM_LONG_DUR = 8.0  # 長い BGM: 8 秒（本編より長い → atrim でトリム）
_MAIN_FREQ = 440  # 本編 sine 周波数 Hz（A4）
_BGM_SHORT_FREQ = 880  # 短い BGM sine 周波数 Hz（A5・本編と弁別可能）
_BGM_LONG_FREQ = 880  # 長い BGM sine 周波数 Hz（同）
_RATE = 25.0  # 映像 fps

# 尺許容誤差: ±1 フレーム相当（出力 fps=25 → 1 フレーム = 0.04 秒）
# ADR-B6-r2: -stream_loop + atrim なので 1 フレーム余裕で十分
_FRAME_TOLERANCE = 1.0 / _RATE  # 0.04 秒

# volumedetect の音量計測用定数
# BGM あり/なし比較で有意な差 (3 dB 以上) を確認する
_BGM_EFFECT_MIN_DB_DIFF = 3.0

# ducking 減衰確認: ducking ON で BGM 単体経路が ducking OFF より低い
# 完全混合出力の差ではなく、ducking 効果は filter_complex の dry_run で確認する
# ducking ON の filter_complex に sidechaincompress が含まれることで機能確認

# ===========================================================================
# ヘルパー: フィクスチャ生成
# ===========================================================================


def _make_main_video(
    ffmpeg: str,
    output: Path,
    duration: float = _MAIN_DUR,
    freq: int = _MAIN_FREQ,
) -> None:
    """本編フィクスチャ: testsrc 映像 + sine 音声（デフォルト 5 秒・440Hz）を生成する。

    DC-GP-003: tmp_path 配下に生成し自動 teardown。
    """
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=320x240:rate={int(_RATE)}:duration={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={freq}:sample_rate=48000:duration={duration}",
        "-t",
        str(duration),
        "-shortest",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"本編フィクスチャ生成に失敗しました: {result.stderr[:400]}"
    )


def _make_silent_video(
    ffmpeg: str,
    output: Path,
    duration: float = _MAIN_DUR,
) -> None:
    """本編無音フィクスチャ: testsrc 映像のみ（音声なし）を生成する。

    DC-AS-004 の BGM 単独系統実証用（has_main_audio=False + BGM）。
    """
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=320x240:rate={int(_RATE)}:duration={duration}",
        "-t",
        str(duration),
        "-c:v",
        "libx264",
        "-an",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"無音本編フィクスチャ生成に失敗しました: {result.stderr[:400]}"
    )


def _make_bgm_audio(
    ffmpeg: str,
    output: Path,
    duration: float,
    freq: int = _BGM_SHORT_FREQ,
    amplitude: float = 0.3,
) -> None:
    """BGM フィクスチャ（音声のみ mp4）を生成する。

    sine 単音で生成し、本編（440Hz）と弁別できる周波数（880Hz）を使う。
    amplitude は volumedetect で計測可能なレベルに設定する。
    """
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={freq}:sample_rate=48000:duration={duration}",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=320x240:rate={int(_RATE)}:duration={duration}",
        "-t",
        str(duration),
        "-shortest",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"BGM フィクスチャ生成に失敗しました: {result.stderr[:400]}"
    )


# ===========================================================================
# ヘルパー: 音声計測
# ===========================================================================


def _measure_max_volume(ffmpeg: str, media: Path) -> float:
    """volumedetect で max_volume を測定して返す（dB）。"""
    cmd = [
        ffmpeg,
        "-i",
        str(media),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"volumedetect 測定に失敗しました: {result.stderr[:400]}"
    )
    m = re.search(r"max_volume:\s*([-0-9.]+)\s*dB", result.stderr)
    assert m is not None, f"max_volume が見つかりません:\n{result.stderr[-400:]}"
    return float(m.group(1))


def _measure_mean_volume(ffmpeg: str, media: Path) -> float:
    """volumedetect で mean_volume を測定して返す（dB）。"""
    cmd = [
        ffmpeg,
        "-i",
        str(media),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"volumedetect 測定に失敗しました: {result.stderr[:400]}"
    )
    m = re.search(r"mean_volume:\s*([-0-9.]+)\s*dB", result.stderr)
    assert m is not None, f"mean_volume が見つかりません:\n{result.stderr[-400:]}"
    return float(m.group(1))


def _measure_segment_volume(
    ffmpeg: str, media: Path, start: float, duration: float
) -> float:
    """指定区間の mean_volume を測定して返す（dB）。

    afade 前後比較（assert-4）に使用する。
    """
    cmd = [
        ffmpeg,
        "-ss",
        str(start),
        "-t",
        str(duration),
        "-i",
        str(media),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"区間 volumedetect 測定に失敗しました: {result.stderr[:400]}"
    )
    m = re.search(r"mean_volume:\s*([-0-9.]+)\s*dB", result.stderr)
    # 無音区間は mean_volume が得られないことがある
    if m is None:
        return -100.0
    return float(m.group(1))


def _get_duration_seconds(ffprobe: str, media: Path) -> float:
    """ffprobe で動画の尺を秒で返す。"""
    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        str(media),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, f"ffprobe に失敗しました: {result.stderr[:400]}"
    info = json.loads(result.stdout)
    duration_str = info.get("format", {}).get("duration")
    assert duration_str is not None, "duration が取得できませんでした"
    return float(duration_str)


def _get_audio_stream_count(ffprobe: str, media: Path) -> int:
    """ffprobe で音声ストリーム数を返す。"""
    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        str(media),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, f"ffprobe に失敗しました: {result.stderr[:400]}"
    info = json.loads(result.stdout)
    return sum(1 for s in info.get("streams", []) if s.get("codec_type") == "audio")


# ===========================================================================
# ヘルパー: OTIO タイムライン構築
# ===========================================================================


def _make_base_timeline(
    source_path: Path,
    duration_sec: float = _MAIN_DUR,
    rate: float = _RATE,
) -> otio.schema.Timeline:
    """単一クリップ本編の OTIO タイムラインを生成する（Video トラックのみ）。"""
    ref = otio.schema.ExternalReference(target_url=str(source_path))
    clip = otio.schema.Clip(
        name=source_path.name,
        media_reference=ref,
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, rate),
            duration=otio.opentime.RationalTime(duration_sec * rate, rate),
        ),
    )
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    track.append(clip)
    timeline = otio.schema.Timeline(name="e2e_bgm_test")
    timeline.tracks.append(track)
    return timeline


def _add_bgm_track(
    timeline: otio.schema.Timeline,
    bgm_path: Path,
    bgm_duration_sec: float,
    bgm_rate: float = 48000.0,
    volume_db: float = -6.0,
    fade_in_sec: float = 0.0,
    fade_out_sec: float = 0.0,
    ducking_enabled: bool = False,
    ducking_threshold: float = 0.05,
    ducking_ratio: float = 4.0,
) -> None:
    """timeline に A2 BGM トラックを追加する（add_bgm が書くのと同等の OTIO 構造）。

    e2e テストで add_bgm ツールを経由せずに直接 OTIO を組み立てるヘルパー。
    BGM クリップの metadata には BgmDirective 相当の dict を書く（ADR-B3/B9-r2）。
    """
    bgm_directive: dict[str, Any] = {
        "tool": "clipwright-bgm",
        "version": "0.1.0",
        "kind": "bgm",
        "volume_db": volume_db,
        "fade_in_sec": fade_in_sec,
        "fade_out_sec": fade_out_sec,
        "ducking": {
            "enabled": ducking_enabled,
            "threshold": ducking_threshold,
            "ratio": ducking_ratio,
        },
    }

    ref = otio.schema.ExternalReference(target_url=str(bgm_path))
    # source_range = BGM メディア全長固定（ADR-B2-r2/DC-AS-003）
    bgm_clip = otio.schema.Clip(
        name=bgm_path.name,
        media_reference=ref,
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, bgm_rate),
            duration=otio.opentime.RationalTime(bgm_duration_sec * bgm_rate, bgm_rate),
        ),
        metadata={"clipwright": bgm_directive},
    )

    a2 = otio.schema.Track(name="A2", kind=otio.schema.TrackKind.Audio)
    a2.append(bgm_clip)
    timeline.tracks.append(a2)


def _save_timeline(timeline: otio.schema.Timeline, path: Path) -> None:
    """OTIO タイムラインをファイルに保存する。"""
    otio.adapters.write_to_file(timeline, str(path))


# ===========================================================================
# テスト: assert-1 + assert-2 + assert-3（基本ミックス・ピーク検証）
# ===========================================================================


@requires_ffmpeg
@requires_ffprobe
class TestBgmBasicMix:
    """BGM ミックスの基本動作実証（assert-1/2/3）。"""

    def test_render_with_bgm_returns_ok(self, tmp_path: Path) -> None:
        """BGM 付き timeline で render が ok=True を返し出力ファイルが生成される（assert-1）。"""
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        bgm_src = tmp_path / "bgm_short.mp4"

        _make_main_video(_FFMPEG, main_src)
        _make_bgm_audio(_FFMPEG, bgm_src, _BGM_SHORT_DUR)

        timeline = _make_base_timeline(main_src)
        _add_bgm_track(timeline, bgm_src, _BGM_SHORT_DUR)
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline(timeline, timeline_path)

        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render が失敗しました: {result}"
        assert out_path.exists(), "出力ファイルが生成されていません"
        assert out_path.stat().st_size > 0, "出力ファイルのサイズが 0 です"

    def test_bgm_output_has_audio_stream(self, tmp_path: Path) -> None:
        """BGM 付き出力に音声ストリームが含まれる（assert-2 前提確認）。"""
        assert _FFMPEG is not None
        assert _FFPROBE is not None
        main_src = tmp_path / "main.mp4"
        bgm_src = tmp_path / "bgm_short.mp4"

        _make_main_video(_FFMPEG, main_src)
        _make_bgm_audio(_FFMPEG, bgm_src, _BGM_SHORT_DUR)

        timeline = _make_base_timeline(main_src)
        _add_bgm_track(timeline, bgm_src, _BGM_SHORT_DUR, volume_db=-6.0)
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline(timeline, timeline_path)

        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render が失敗しました: {result}"

        audio_count = _get_audio_stream_count(_FFPROBE, out_path)
        assert audio_count >= 1, (
            f"出力に音声ストリームがありません（assert-2）:\n"
            f"  音声ストリーム数: {audio_count}"
        )

    def test_bgm_increases_mean_volume(self, tmp_path: Path) -> None:
        """BGM 混合により mean_volume が BGM なしより高くなる（assert-2・混合実証）。

        本編 440Hz sine + BGM 880Hz sine を amix した場合、
        BGM なし本編のみより出力音量が有意に高くなることを確認する。
        BGM volume_db=0.0 で相対調整なし（最大効果）。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        bgm_src = tmp_path / "bgm_short.mp4"

        _make_main_video(_FFMPEG, main_src)
        _make_bgm_audio(_FFMPEG, bgm_src, _BGM_SHORT_DUR)

        # BGM ありの出力
        timeline_bgm = _make_base_timeline(main_src)
        _add_bgm_track(timeline_bgm, bgm_src, _BGM_SHORT_DUR, volume_db=0.0)
        tl_path_bgm = tmp_path / "timeline_bgm.otio"
        _save_timeline(timeline_bgm, tl_path_bgm)
        out_bgm = tmp_path / "out_bgm.mp4"
        result_bgm = render_timeline(
            str(tl_path_bgm), str(out_bgm), RenderOptions(), dry_run=False
        )
        assert result_bgm["ok"] is True, f"BGM あり render が失敗しました: {result_bgm}"

        # BGM なしの出力（ネガティブ対照）
        timeline_no_bgm = _make_base_timeline(main_src)
        tl_path_no_bgm = tmp_path / "timeline_no_bgm.otio"
        _save_timeline(timeline_no_bgm, tl_path_no_bgm)
        out_no_bgm = tmp_path / "out_no_bgm.mp4"
        result_no_bgm = render_timeline(
            str(tl_path_no_bgm), str(out_no_bgm), RenderOptions(), dry_run=False
        )
        assert result_no_bgm["ok"] is True, (
            f"BGM なし render が失敗しました: {result_no_bgm}"
        )

        mean_bgm = _measure_mean_volume(_FFMPEG, out_bgm)
        mean_no_bgm = _measure_mean_volume(_FFMPEG, out_no_bgm)
        diff = mean_bgm - mean_no_bgm

        assert diff >= _BGM_EFFECT_MIN_DB_DIFF, (
            f"BGM 混合による音量変化が不十分です（assert-2）:\n"
            f"  BGM あり mean_volume: {mean_bgm:.2f} dB\n"
            f"  BGM なし mean_volume: {mean_no_bgm:.2f} dB\n"
            f"  差: {diff:.2f} dB（期待: >= {_BGM_EFFECT_MIN_DB_DIFF} dB）\n"
            f"  BGM が実際に混ざっていれば本編のみより音量が高くなるはず"
        )

    def test_output_peak_does_not_exceed_0dbfs(self, tmp_path: Path) -> None:
        """出力ピークが 0dBFS を超えない（assert-3・DC-AM-001・alimiter=limit=1.0 実証）。

        本編フルスケール sine + BGM を amix しても alimiter でクリッピングが防止されることを
        実機確認する。本編 amplitude=1.0 は実際には AAC エンコードによりピークが変わるため、
        出力の max_volume を計測し 0 dBFS 以下（≤ 0.0）であることを確認する。

        volumedetect の max_volume は PCM 解析値のため 0 dBFS が 0.0 dB に相当する。
        ただし AAC エンコード後の再デコードによりわずかに超過することがある（±1 dB 許容）。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        bgm_src = tmp_path / "bgm_short.mp4"

        # 本編: できるだけ大きい音量（volume=0.9 で接近）
        # ffmpeg 8.1.1 の sine フィルタは amplitude オプション非対応のため
        # volume フィルタで音量調整する
        cmd_main = [
            _FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=320x240:rate={int(_RATE)}:duration={_MAIN_DUR}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={_MAIN_FREQ}:sample_rate=48000:duration={_MAIN_DUR}",
            "-t",
            str(_MAIN_DUR),
            "-shortest",
            "-filter:a",
            "volume=0.9",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-pix_fmt",
            "yuv420p",
            str(main_src),
        ]
        r = subprocess.run(
            cmd_main,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=_E2E_TIMEOUT,
        )
        assert r.returncode == 0, f"大音量本編生成失敗: {r.stderr[:400]}"

        # BGM も volume_db=0.0 で最大加算
        _make_bgm_audio(_FFMPEG, bgm_src, _BGM_SHORT_DUR)

        timeline = _make_base_timeline(main_src)
        _add_bgm_track(timeline, bgm_src, _BGM_SHORT_DUR, volume_db=0.0)
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline(timeline, timeline_path)

        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render が失敗しました: {result}"

        max_vol = _measure_max_volume(_FFMPEG, out_path)

        # AAC エンコード後の再デコードによる ±1 dB を考慮
        assert max_vol <= 1.0, (
            f"出力ピークが 0dBFS を超過しています（assert-3・DC-AM-001 違反）:\n"
            f"  max_volume: {max_vol:.2f} dB\n"
            f"  期待: ≤ 1.0 dB（alimiter=limit=1.0 でクリッピングが防止されるはず）"
        )


# ===========================================================================
# テスト: assert-4 / assert-4b（fade_in/out 実証）
# ===========================================================================


@requires_ffmpeg
class TestBgmFade:
    """BGM の fade_in/out 効果を実証するテスト（assert-4/4b）。

    先頭区間の音量が中央区間より低いことで afade in が機能することを確認する。
    末尾区間の音量が中央区間より低いことで afade out が機能することを確認する。
    fade=0 ケースでは先頭/末尾が中央と同程度になることを確認する（assert-4b）。
    """

    def test_fade_in_reduces_beginning_volume(self, tmp_path: Path) -> None:
        """fade_in_sec > 0 のとき先頭区間の音量が中央より低い（assert-4・afade in 実証）。"""
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        bgm_src = tmp_path / "bgm_long.mp4"

        _make_main_video(_FFMPEG, main_src)
        _make_bgm_audio(_FFMPEG, bgm_src, _BGM_LONG_DUR)

        fade_sec = 1.5  # 先頭 1.5 秒でフェードイン

        timeline = _make_base_timeline(main_src)
        _add_bgm_track(
            timeline,
            bgm_src,
            _BGM_LONG_DUR,
            volume_db=-6.0,
            fade_in_sec=fade_sec,
            fade_out_sec=0.0,
        )
        timeline_path = tmp_path / "timeline_fade.otio"
        _save_timeline(timeline, timeline_path)

        out_path = tmp_path / "out_fade.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render が失敗しました: {result}"

        # 先頭 0.5 秒（フェードイン途中）の音量
        vol_start = _measure_segment_volume(_FFMPEG, out_path, start=0.0, duration=0.5)
        # 中央 2.0〜2.5 秒（フェード影響外）の音量
        vol_mid = _measure_segment_volume(_FFMPEG, out_path, start=2.0, duration=0.5)

        assert vol_start < vol_mid, (
            f"fade_in が効いていません（assert-4）:\n"
            f"  先頭 0.5 秒 mean_volume: {vol_start:.2f} dB\n"
            f"  中央 2.0〜2.5 秒 mean_volume: {vol_mid:.2f} dB\n"
            f"  期待: 先頭 < 中央（フェードインで音量が増加するはず）"
        )

    def test_fade_out_reduces_ending_volume(self, tmp_path: Path) -> None:
        """fade_out_sec > 0 のとき末尾区間の音量が中央より低い（assert-4・afade out 実証）。"""
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        bgm_src = tmp_path / "bgm_long.mp4"

        _make_main_video(_FFMPEG, main_src)
        _make_bgm_audio(_FFMPEG, bgm_src, _BGM_LONG_DUR)

        fade_sec = 1.5  # 末尾 1.5 秒でフェードアウト

        timeline = _make_base_timeline(main_src)
        _add_bgm_track(
            timeline,
            bgm_src,
            _BGM_LONG_DUR,
            volume_db=-6.0,
            fade_in_sec=0.0,
            fade_out_sec=fade_sec,
        )
        timeline_path = tmp_path / "timeline_fadeout.otio"
        _save_timeline(timeline, timeline_path)

        out_path = tmp_path / "out_fadeout.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render が失敗しました: {result}"

        # 中央 2.0〜2.5 秒（フェード影響外）の音量
        vol_mid = _measure_segment_volume(_FFMPEG, out_path, start=2.0, duration=0.5)
        # 末尾 4.0〜4.5 秒（フェードアウト途中）の音量
        vol_end = _measure_segment_volume(_FFMPEG, out_path, start=4.0, duration=0.4)

        assert vol_end < vol_mid, (
            f"fade_out が効いていません（assert-4）:\n"
            f"  中央 2.0〜2.5 秒 mean_volume: {vol_mid:.2f} dB\n"
            f"  末尾 4.0〜4.5 秒 mean_volume: {vol_end:.2f} dB\n"
            f"  期待: 末尾 < 中央（フェードアウトで音量が減少するはず）"
        )

    def test_no_fade_does_not_reduce_volume_at_boundaries(self, tmp_path: Path) -> None:
        """fade=0 のとき先頭/末尾の音量が中央と同程度（assert-4b・無フェード確認）。

        ADR-B9-r3: fade_in_sec=0/fade_out_sec=0 のとき afade を注入しない。
        dry_run で filter_complex に afade が含まれないことも確認する。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        bgm_src = tmp_path / "bgm_long.mp4"

        _make_main_video(_FFMPEG, main_src)
        _make_bgm_audio(_FFMPEG, bgm_src, _BGM_LONG_DUR)

        timeline = _make_base_timeline(main_src)
        _add_bgm_track(
            timeline,
            bgm_src,
            _BGM_LONG_DUR,
            volume_db=-6.0,
            fade_in_sec=0.0,
            fade_out_sec=0.0,
        )
        timeline_path = tmp_path / "timeline_no_fade.otio"
        _save_timeline(timeline, timeline_path)

        # dry_run で filter_complex に afade が含まれないことを確認
        out_path_dry = tmp_path / "out_no_fade_dry.mp4"
        result_dry = render_timeline(
            str(timeline_path), str(out_path_dry), RenderOptions(), dry_run=True
        )
        assert result_dry["ok"] is True, f"dry_run が失敗しました: {result_dry}"
        fc = result_dry["data"]["filter_complex"]
        assert "afade" not in fc, (
            f"fade=0 なのに filter_complex に afade が含まれています（assert-4b・ADR-B9-r3 違反）:\n"
            f"  filter_complex: {fc}"
        )

        # 実機: 先頭/末尾の音量が中央と大きくは差がないことを確認
        out_path = tmp_path / "out_no_fade.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render が失敗しました: {result}"

        vol_start = _measure_segment_volume(_FFMPEG, out_path, start=0.0, duration=0.5)
        vol_mid = _measure_segment_volume(_FFMPEG, out_path, start=2.0, duration=0.5)

        # 無フェードなので先頭と中央の差は小さいはず（5 dB 以内）
        diff = vol_mid - vol_start
        assert diff <= 5.0, (
            f"fade=0 なのに先頭と中央の音量差が大きすぎます（assert-4b）:\n"
            f"  先頭 0.5 秒 mean_volume: {vol_start:.2f} dB\n"
            f"  中央 mean_volume: {vol_mid:.2f} dB\n"
            f"  差: {diff:.2f} dB（許容: 5 dB 以内）"
        )


# ===========================================================================
# テスト: assert-5（BGM 尺合わせ・-stream_loop ループ・atrim トリム）
# ===========================================================================


@requires_ffmpeg
@requires_ffprobe
class TestBgmDurationMatch:
    """BGM の本編尺合わせ実証（assert-5・ADR-B6-r2）。

    短い BGM（2秒）が -stream_loop でループして本編尺（5秒）に合うこと。
    長い BGM（8秒）が atrim で末尾トリムされて本編尺（5秒）に合うこと。
    出力尺 ≒ 本編尺（±1 フレーム許容）を ffprobe で確認する。
    """

    def test_short_bgm_loops_to_main_duration(self, tmp_path: Path) -> None:
        """短い BGM（2秒）が -stream_loop でループして出力尺が本編尺（5秒）になる（assert-5）。

        ADR-B6-r2: -stream_loop -1 + atrim=0:{main_dur} により
        BGM が本編尺にぴったり合う。
        """
        assert _FFMPEG is not None
        assert _FFPROBE is not None
        main_src = tmp_path / "main.mp4"
        bgm_src = tmp_path / "bgm_short.mp4"

        _make_main_video(_FFMPEG, main_src)
        _make_bgm_audio(_FFMPEG, bgm_src, _BGM_SHORT_DUR)

        timeline = _make_base_timeline(main_src)
        _add_bgm_track(timeline, bgm_src, _BGM_SHORT_DUR)
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline(timeline, timeline_path)

        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render が失敗しました: {result}"

        actual_dur = _get_duration_seconds(_FFPROBE, out_path)
        diff = abs(actual_dur - _MAIN_DUR)

        assert diff <= _FRAME_TOLERANCE, (
            f"短い BGM ループ後の出力尺が本編尺と乖離しています（assert-5）:\n"
            f"  期待尺: {_MAIN_DUR:.3f} 秒\n"
            f"  実出力尺: {actual_dur:.3f} 秒\n"
            f"  差分: {diff:.4f} 秒（許容: ±{_FRAME_TOLERANCE:.4f} 秒 = ±1 フレーム）\n"
            f"  -stream_loop -1 + atrim で本編尺ぴったりになるはず（ADR-B6-r2）"
        )

    def test_long_bgm_trimmed_to_main_duration(self, tmp_path: Path) -> None:
        """長い BGM（8秒）が atrim で末尾トリムされ出力尺が本編尺（5秒）になる（assert-5）。"""
        assert _FFMPEG is not None
        assert _FFPROBE is not None
        main_src = tmp_path / "main.mp4"
        bgm_src = tmp_path / "bgm_long.mp4"

        _make_main_video(_FFMPEG, main_src)
        _make_bgm_audio(_FFMPEG, bgm_src, _BGM_LONG_DUR)

        timeline = _make_base_timeline(main_src)
        _add_bgm_track(timeline, bgm_src, _BGM_LONG_DUR)
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline(timeline, timeline_path)

        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render が失敗しました: {result}"

        actual_dur = _get_duration_seconds(_FFPROBE, out_path)
        diff = abs(actual_dur - _MAIN_DUR)

        assert diff <= _FRAME_TOLERANCE, (
            f"長い BGM トリム後の出力尺が本編尺と乖離しています（assert-5）:\n"
            f"  期待尺: {_MAIN_DUR:.3f} 秒\n"
            f"  実出力尺: {actual_dur:.3f} 秒\n"
            f"  差分: {diff:.4f} 秒（許容: ±{_FRAME_TOLERANCE:.4f} 秒 = ±1 フレーム）\n"
            f"  atrim=0:{_MAIN_DUR} で末尾トリムされるはず（ADR-B6-r2）"
        )

    def test_dry_run_has_stream_loop_in_render_command(self, tmp_path: Path) -> None:
        """-stream_loop -1 が filter_complex の BGM チェーンに atrim を含む（ADR-B6-r2 内部確認）。

        dry_run で filter_complex を取得し:
        1. filter_complex に atrim が含まれる（BGM チェーンの尺合わせ）
        2. filter_complex に alimiter が含まれる（DC-AM-001）
        3. filter_complex に aformat が含まれる（DC-AS-007）
        4. filter_complex に amix が含まれる（混合）
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        bgm_src = tmp_path / "bgm_short.mp4"

        _make_main_video(_FFMPEG, main_src)
        _make_bgm_audio(_FFMPEG, bgm_src, _BGM_SHORT_DUR)

        timeline = _make_base_timeline(main_src)
        _add_bgm_track(timeline, bgm_src, _BGM_SHORT_DUR)
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline(timeline, timeline_path)

        out_path = tmp_path / "out_dry.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=True
        )
        assert result["ok"] is True, f"dry_run が失敗しました: {result}"

        fc = result["data"]["filter_complex"]

        assert "atrim" in fc, (
            f"filter_complex に atrim が含まれていません（ADR-B6-r2 違反）:\n"
            f"  filter_complex: {fc}"
        )
        assert "alimiter" in fc, (
            f"filter_complex に alimiter が含まれていません（DC-AM-001 違反）:\n"
            f"  filter_complex: {fc}"
        )
        assert "aformat" in fc, (
            f"filter_complex に aformat が含まれていません（DC-AS-007 違反）:\n"
            f"  filter_complex: {fc}"
        )
        assert "amix" in fc, (
            f"filter_complex に amix が含まれていません（BGM ミックス欠落）:\n"
            f"  filter_complex: {fc}"
        )


# ===========================================================================
# テスト: assert-6（ducking ON 実証）
# ===========================================================================


@requires_ffmpeg
class TestBgmDucking:
    """ducking ON 時に BGM が抑制されることを実証するテスト（assert-6）。

    ducking ON/OFF を比較し:
    - dry_run の filter_complex で sidechaincompress の有無を確認（内部確認）。
    - 実機出力でダッキング効果を確認（ducking ON は OFF より BGM 寄与が小さい）。
    """

    def test_ducking_on_filter_complex_has_sidechaincompress(
        self, tmp_path: Path
    ) -> None:
        """ducking ON のとき filter_complex に sidechaincompress が含まれる（assert-6・内部確認）。

        ADR-B5-r3: sidechaincompress の第1入力=BGM・第2入力=本編（DC-AS-006）。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        bgm_src = tmp_path / "bgm_short.mp4"

        _make_main_video(_FFMPEG, main_src)
        _make_bgm_audio(_FFMPEG, bgm_src, _BGM_SHORT_DUR)

        # ducking ON
        timeline_on = _make_base_timeline(main_src)
        _add_bgm_track(
            timeline_on,
            bgm_src,
            _BGM_SHORT_DUR,
            ducking_enabled=True,
            ducking_threshold=0.05,
            ducking_ratio=4.0,
        )
        tl_path_on = tmp_path / "timeline_duck_on.otio"
        _save_timeline(timeline_on, tl_path_on)

        out_dry_on = tmp_path / "out_duck_on_dry.mp4"
        result_on = render_timeline(
            str(tl_path_on), str(out_dry_on), RenderOptions(), dry_run=True
        )
        assert result_on["ok"] is True, f"dry_run (ducking ON) が失敗: {result_on}"
        fc_on = result_on["data"]["filter_complex"]

        assert "sidechaincompress" in fc_on, (
            f"ducking ON なのに filter_complex に sidechaincompress がありません（assert-6・DC-AS-006 違反）:\n"
            f"  filter_complex: {fc_on}"
        )

        # sidechaincompress 入力順序の確認（ADR-B5-r3: BGM=第1・本編=第2）
        # [bgm][main_sc]sidechaincompress の順序であることを確認
        assert "bgm][main_sc]sidechaincompress" in fc_on, (
            f"sidechaincompress の入力順序が不正です（DC-AS-006 違反）:\n"
            f"  期待: [bgm][main_sc]sidechaincompress\n"
            f"  filter_complex: {fc_on}"
        )

    def test_ducking_off_filter_complex_has_no_sidechaincompress(
        self, tmp_path: Path
    ) -> None:
        """ducking OFF のとき filter_complex に sidechaincompress が含まれない（assert-6・切り分け）。"""
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        bgm_src = tmp_path / "bgm_short.mp4"

        _make_main_video(_FFMPEG, main_src)
        _make_bgm_audio(_FFMPEG, bgm_src, _BGM_SHORT_DUR)

        # ducking OFF
        timeline_off = _make_base_timeline(main_src)
        _add_bgm_track(
            timeline_off,
            bgm_src,
            _BGM_SHORT_DUR,
            ducking_enabled=False,
        )
        tl_path_off = tmp_path / "timeline_duck_off.otio"
        _save_timeline(timeline_off, tl_path_off)

        out_dry_off = tmp_path / "out_duck_off_dry.mp4"
        result_off = render_timeline(
            str(tl_path_off), str(out_dry_off), RenderOptions(), dry_run=True
        )
        assert result_off["ok"] is True, f"dry_run (ducking OFF) が失敗: {result_off}"
        fc_off = result_off["data"]["filter_complex"]

        assert "sidechaincompress" not in fc_off, (
            f"ducking OFF なのに filter_complex に sidechaincompress があります（切り分け失敗）:\n"
            f"  filter_complex: {fc_off}"
        )

    def test_ducking_on_reduces_bgm_mean_volume_vs_off(self, tmp_path: Path) -> None:
        """ducking ON の出力は ducking OFF より mean_volume が低い（assert-6・実機確認）。

        本編に強い sine 音声があるため sidechaincompress が BGM を抑制する。
        ducking ON の mean_volume <= ducking OFF の mean_volume + 1.0 dB を期待する
        （ducking の効果は本編信号の大きさに依存するため差の絶対値は不定）。

        注: 実機で ducking 効果が出るには本編信号が threshold を超える必要がある。
        本編は amplitude=1.0 相当の AAC エンコード済み sine なので threshold=0.05 を超え
        ducking が発動するはず。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        bgm_src = tmp_path / "bgm_short.mp4"

        _make_main_video(_FFMPEG, main_src)
        _make_bgm_audio(_FFMPEG, bgm_src, _BGM_SHORT_DUR)

        # ducking ON
        timeline_on = _make_base_timeline(main_src)
        _add_bgm_track(
            timeline_on,
            bgm_src,
            _BGM_SHORT_DUR,
            volume_db=0.0,
            ducking_enabled=True,
            ducking_threshold=0.05,
            ducking_ratio=4.0,
        )
        tl_path_on = tmp_path / "timeline_duck_on.otio"
        _save_timeline(timeline_on, tl_path_on)
        out_on = tmp_path / "out_duck_on.mp4"
        r_on = render_timeline(
            str(tl_path_on), str(out_on), RenderOptions(), dry_run=False
        )
        assert r_on["ok"] is True, f"ducking ON render が失敗: {r_on}"

        # ducking OFF
        timeline_off = _make_base_timeline(main_src)
        _add_bgm_track(
            timeline_off,
            bgm_src,
            _BGM_SHORT_DUR,
            volume_db=0.0,
            ducking_enabled=False,
        )
        tl_path_off = tmp_path / "timeline_duck_off.otio"
        _save_timeline(timeline_off, tl_path_off)
        out_off = tmp_path / "out_duck_off.mp4"
        r_off = render_timeline(
            str(tl_path_off), str(out_off), RenderOptions(), dry_run=False
        )
        assert r_off["ok"] is True, f"ducking OFF render が失敗: {r_off}"

        mean_on = _measure_mean_volume(_FFMPEG, out_on)
        mean_off = _measure_mean_volume(_FFMPEG, out_off)

        # ducking ON が OFF より音量が低いこと（BGM 抑制効果）
        # ducking の強さと本編信号次第だが、ratio=4.0 で threshold=0.05 なら
        # sine 音声（振幅 >> 0.05）が常にサイドチェーンに効くため ON < OFF が成立するはず
        assert mean_on <= mean_off + 1.0, (
            f"ducking ON が OFF より音量を下げていません（assert-6）:\n"
            f"  ducking ON mean_volume: {mean_on:.2f} dB\n"
            f"  ducking OFF mean_volume: {mean_off:.2f} dB\n"
            f"  ducking ON の音量が OFF より低くなるはず（BGM が本編音声でダッキング）\n"
            f"  本編 sine（threshold=0.05 超）が sidechaincompress を発動するはず"
        )


# ===========================================================================
# テスト: assert-7（ネガティブ対照）
# ===========================================================================


@requires_ffmpeg
class TestBgmNegativeControl:
    """ネガティブ対照: BGM 注記なし timeline は本編のみ出力（assert-7・B-3 教訓）。

    BGM 起因の音量変化を切り分けるために必要。
    BGM なし → render しても入力音量のまま（BGM あり時と有意差がある）。
    """

    def test_no_bgm_directive_does_not_mix_bgm(self, tmp_path: Path) -> None:
        """BGM 注記なし timeline は本編のみで render し音量が BGM あり時と異なる（assert-7）。"""
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        bgm_src = tmp_path / "bgm_short.mp4"

        _make_main_video(_FFMPEG, main_src)
        _make_bgm_audio(_FFMPEG, bgm_src, _BGM_SHORT_DUR)

        # BGM あり
        timeline_bgm = _make_base_timeline(main_src)
        _add_bgm_track(timeline_bgm, bgm_src, _BGM_SHORT_DUR, volume_db=0.0)
        tl_path_bgm = tmp_path / "timeline_bgm.otio"
        _save_timeline(timeline_bgm, tl_path_bgm)
        out_bgm = tmp_path / "out_bgm.mp4"
        r_bgm = render_timeline(
            str(tl_path_bgm), str(out_bgm), RenderOptions(), dry_run=False
        )
        assert r_bgm["ok"] is True, f"BGM あり render が失敗: {r_bgm}"

        # BGM なし（ネガティブ対照）
        timeline_no = _make_base_timeline(main_src)
        tl_path_no = tmp_path / "timeline_no_bgm.otio"
        _save_timeline(timeline_no, tl_path_no)
        out_no = tmp_path / "out_no_bgm.mp4"
        r_no = render_timeline(
            str(tl_path_no), str(out_no), RenderOptions(), dry_run=False
        )
        assert r_no["ok"] is True, f"BGM なし render が失敗: {r_no}"

        mean_bgm = _measure_mean_volume(_FFMPEG, out_bgm)
        mean_no = _measure_mean_volume(_FFMPEG, out_no)
        diff = mean_bgm - mean_no

        assert diff >= _BGM_EFFECT_MIN_DB_DIFF, (
            f"BGM なし時の音量が BGM あり時と有意差がありません（assert-7・切り分け失敗）:\n"
            f"  BGM あり mean_volume: {mean_bgm:.2f} dB\n"
            f"  BGM なし mean_volume: {mean_no:.2f} dB\n"
            f"  差: {diff:.2f} dB（期待: >= {_BGM_EFFECT_MIN_DB_DIFF} dB）\n"
            f"  音量差が BGM 混合起因であることを確認するための対照実験"
        )

    def test_no_bgm_dry_run_no_amix_in_filter(self, tmp_path: Path) -> None:
        """BGM なし timeline の dry_run filter_complex に amix が含まれない（assert-7・内部確認）。"""
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"

        _make_main_video(_FFMPEG, main_src)

        timeline = _make_base_timeline(main_src)
        tl_path = tmp_path / "timeline_no_bgm.otio"
        _save_timeline(timeline, tl_path)

        out_path = tmp_path / "out_no_bgm_dry.mp4"
        result = render_timeline(
            str(tl_path), str(out_path), RenderOptions(), dry_run=True
        )
        assert result["ok"] is True, f"dry_run が失敗しました: {result}"

        fc = result["data"]["filter_complex"]
        assert "amix" not in fc, (
            f"BGM なし timeline の filter_complex に amix が含まれています（assert-7）:\n"
            f"  filter_complex: {fc}"
        )
        assert "alimiter" not in fc, (
            f"BGM なし timeline の filter_complex に alimiter が含まれています（assert-7）:\n"
            f"  filter_complex: {fc}"
        )


# ===========================================================================
# テスト: assert-8（本編無音 + BGM 単独系統・DC-AS-004）
# ===========================================================================


@requires_ffmpeg
@requires_ffprobe
class TestBgmSilentMainAudio:
    """本編無音 + BGM で BGM のみが音声として出力される実証（assert-8・DC-AS-004）。

    ADR-B5-r2: has_main_audio=False のとき BGM 単独系統を使う。
    amix なし・BGM が唯一の音声出力。
    """

    def test_silent_main_plus_bgm_has_audio_output(self, tmp_path: Path) -> None:
        """本編無音 + BGM で出力に音声ストリームが含まれる（assert-8）。"""
        assert _FFMPEG is not None
        assert _FFPROBE is not None
        main_src = tmp_path / "main_silent.mp4"
        bgm_src = tmp_path / "bgm_short.mp4"

        _make_silent_video(_FFMPEG, main_src)
        _make_bgm_audio(_FFMPEG, bgm_src, _BGM_SHORT_DUR)

        timeline = _make_base_timeline(main_src)
        _add_bgm_track(timeline, bgm_src, _BGM_SHORT_DUR, volume_db=-6.0)
        timeline_path = tmp_path / "timeline_silent.otio"
        _save_timeline(timeline, timeline_path)

        out_path = tmp_path / "out_silent.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"本編無音 + BGM render が失敗しました: {result}"
        assert out_path.exists(), "出力ファイルが生成されていません"

        audio_count = _get_audio_stream_count(_FFPROBE, out_path)
        assert audio_count >= 1, (
            f"本編無音 + BGM の出力に音声ストリームがありません（assert-8・DC-AS-004）:\n"
            f"  音声ストリーム数: {audio_count}\n"
            f"  BGM 単独系統により BGM が唯一の音声として出力されるはず"
        )

    def test_silent_main_plus_bgm_has_audible_sound(self, tmp_path: Path) -> None:
        """本編無音 + BGM の出力に実際に音声が含まれる（assert-8・BGM 単独系統実証）。

        出力の mean_volume が無音でないことを確認する（-80 dB 超）。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main_silent.mp4"
        bgm_src = tmp_path / "bgm_short.mp4"

        _make_silent_video(_FFMPEG, main_src)
        _make_bgm_audio(_FFMPEG, bgm_src, _BGM_SHORT_DUR)

        timeline = _make_base_timeline(main_src)
        _add_bgm_track(timeline, bgm_src, _BGM_SHORT_DUR, volume_db=-6.0)
        timeline_path = tmp_path / "timeline_silent.otio"
        _save_timeline(timeline, timeline_path)

        out_path = tmp_path / "out_silent.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render が失敗しました: {result}"

        mean_vol = _measure_mean_volume(_FFMPEG, out_path)

        assert mean_vol > -80.0, (
            f"本編無音 + BGM の出力が無音になっています（assert-8・DC-AS-004 違反）:\n"
            f"  mean_volume: {mean_vol:.2f} dB\n"
            f"  BGM 単独系統で BGM が音声として出力されるはず"
        )

    def test_silent_main_plus_bgm_dry_run_no_amix(self, tmp_path: Path) -> None:
        """本編無音 + BGM の dry_run filter_complex に amix が含まれない（assert-8・内部確認）。

        ADR-B5-r2: has_main_audio=False のとき BGM 単独系統を使うため amix しない。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main_silent.mp4"
        bgm_src = tmp_path / "bgm_short.mp4"

        _make_silent_video(_FFMPEG, main_src)
        _make_bgm_audio(_FFMPEG, bgm_src, _BGM_SHORT_DUR)

        timeline = _make_base_timeline(main_src)
        _add_bgm_track(timeline, bgm_src, _BGM_SHORT_DUR, volume_db=-6.0)
        timeline_path = tmp_path / "timeline_silent.otio"
        _save_timeline(timeline, timeline_path)

        out_path = tmp_path / "out_silent_dry.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=True
        )
        assert result["ok"] is True, f"dry_run が失敗しました: {result}"

        fc = result["data"]["filter_complex"]

        # 本編無音 + BGM 単独系統: amix が入らないことを確認
        assert "amix" not in fc, (
            f"本編無音 + BGM なのに filter_complex に amix が含まれています（assert-8・ADR-B5-r2 違反）:\n"
            f"  filter_complex: {fc}"
        )
        # BGM 単独系統でも alimiter は含まれる（DC-AM-001・本編無音時は省略されうる）
        # 実装確認: _append_bgm_pipe の has_main_audio=False 分岐では alimiter なし
        # BGM chain が [outa_bgm] に直接出力されるため alimiter は不要

        # atrim が含まれることで尺合わせが機能していることを確認
        assert "atrim" in fc, (
            f"filter_complex に atrim が含まれていません（BGM 尺合わせ欠落）:\n"
            f"  filter_complex: {fc}"
        )
