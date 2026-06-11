"""test_e2e_merge.py — 複数ソース連結の実 e2e テスト（task_id: e2e-merge）。

設計根拠:
  - architecture-report-20260611-154732 §7 v2
  - ADR-C5-r2: 規格統一フィルタ（fps/scale/pad/setsar）で各クリップを前処理
  - ADR-C7-r2: 音声規格統一（aformat=48000/stereo）を必須化、anullsrc で無音補完
  - ADR-C3: ユニークソース数で経路分岐し、単一ソース経路の後方互換を維持
  - ADR-C9-r2: input_sources を plan.py の単一情報源として -i 並びに使用
  - DC-AS-002/AM-005/GP-003: 異 sample_rate/channel_layout でも連結が成立することを実証

テスト構成:
  1. フィクスチャ生成（ffmpeg lavfi で規格不一致の3種）
     - 横動画: 640x480・30fps・sine 44100Hz mono
     - 縦動画: 360x640・25fps・sine 48000Hz stereo
     - 音声なしクリップ: 640x480・testsrc・音声なし
  2. 複数ソース e2e: 3本を timeline.otio で連結して render_timeline(dry_run=False)
     - assert1: 出力ファイルが生成される
     - assert2: 出力尺 ≒ 各 source_range duration 秒の合計（±2 フレーム許容）
     - assert3: 出力解像度 = 先頭クリップ基準（640x480 偶数丸め）
     - assert4: 出力に音声1本・異規格でも連結が成立する（DC-AS-002/AM-005/GP-003）
  3. ネガティブ対照: 単一ソースのみの timeline は従来どおり出力される

実行方法（ffmpeg 不在時は skip）:
  uv run --package clipwright-render pytest -k e2e_merge

ffmpeg を PATH に通すか CLIPWRIGHT_FFMPEG/CLIPWRIGHT_FFPROBE 環境変数で指定すること。
"""

from __future__ import annotations

import json
import os
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

# e2e テスト全体の subprocess タイムアウト秒数。
# CI 環境変数 E2E_TIMEOUT_SEC で上書き可能にする。
_E2E_TIMEOUT: int = int(os.environ.get("E2E_TIMEOUT_SEC", "120"))

# 各フィクスチャの尺（秒）。短くして実行時間を抑える。
_DUR_LANDSCAPE = 3.0  # 横動画（640x480・30fps・44100Hz mono）
_DUR_PORTRAIT = 3.0  # 縦動画（360x640・25fps・48000Hz stereo）
_DUR_NOAUDIO = 3.0  # 音声なし（640x480・testsrc）

# 先頭クリップ規格（横動画が先頭 → 出力規格はこれが基準）
_FIRST_W = 640
_FIRST_H = 480
_FIRST_FPS = 30.0

# 偶数丸め済み期待解像度（(v // 2) * 2・ADR-C4-r2）
_EXPECT_W = (_FIRST_W // 2) * 2  # 640
_EXPECT_H = (_FIRST_H // 2) * 2  # 480

# fps 許容誤差: ±2 フレーム相当（先頭 30fps → 1 フレーム = 1/30 ≒ 0.033 秒）
_FRAME_TOLERANCE = 2 / _FIRST_FPS  # ≒ 0.067 秒


# ===========================================================================
# ヘルパー: フィクスチャ生成
# ===========================================================================


def _make_landscape_video(
    ffmpeg: str, output: Path, duration: float = _DUR_LANDSCAPE
) -> None:
    """横動画（640x480・30fps・sine 44100Hz mono）を生成する（DC-GP-003: 音声規格統一を顕在化）。

    44100Hz mono は縦動画（48000Hz stereo）と規格不一致になるよう意図的に設定する。
    複数ソース経路の aformat 統一が機能することを顕在化させるためのフィクスチャ。
    """
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=640x480:rate=30:duration={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:sample_rate=44100:duration={duration}",
        "-t",
        str(duration),
        "-shortest",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-ac",
        "1",
        "-ar",
        "44100",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    # e2e フィクスチャ生成専用: process.run の代わりに直接呼び出しを許容（MEMORY.md 承認済み例外）
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"横動画フィクスチャ生成に失敗しました: {result.stderr[:400]}"
    )


def _make_portrait_video(
    ffmpeg: str, output: Path, duration: float = _DUR_PORTRAIT
) -> None:
    """縦動画（360x640・25fps・sine 48000Hz stereo）を生成する（DC-GP-003: 音声規格統一を顕在化）。

    48000Hz stereo は横動画（44100Hz mono）と規格不一致になるよう意図的に設定する。
    縦動画の解像度（360x640）は先頭クリップ（640x480）と異なるため、
    pad によるアスペクト保持レターボックス（ADR-C6）が機能することも顕在化させる。
    """
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=360x640:rate=25:duration={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=880:sample_rate=48000:duration={duration}",
        "-t",
        str(duration),
        "-shortest",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-ac",
        "2",
        "-ar",
        "48000",
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
        f"縦動画フィクスチャ生成に失敗しました: {result.stderr[:400]}"
    )


def _make_noaudio_video(
    ffmpeg: str, output: Path, duration: float = _DUR_NOAUDIO
) -> None:
    """音声なし動画（640x480・testsrc）を生成する（ADR-C7-r2: anullsrc 補完を顕在化）。

    音声なしクリップを含む timeline で連結し、anullsrc による無音補完が
    a/v 同期を維持して機能することを実証するためのフィクスチャ。
    """
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=640x480:rate=30:duration={duration}",
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
        f"音声なし動画フィクスチャ生成に失敗しました: {result.stderr[:400]}"
    )


# ===========================================================================
# ヘルパー: ffprobe で出力メディアを検査する
# ===========================================================================


def _probe_media(ffprobe: str, media: Path) -> dict[str, Any]:
    """ffprobe -show_streams -show_format -print_format json で出力を取得して返す。"""
    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
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
    return json.loads(result.stdout)


def _get_video_stream(probe: dict[str, Any]) -> dict[str, Any] | None:
    """probe 結果から最初の video ストリームを返す。"""
    for s in probe.get("streams", []):
        if s.get("codec_type") == "video":
            return s
    return None


def _get_audio_streams(probe: dict[str, Any]) -> list[dict[str, Any]]:
    """probe 結果からすべての audio ストリームを返す。"""
    return [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]


def _get_duration_seconds(probe: dict[str, Any]) -> float:
    """probe 結果から動画の尺を秒で返す（format.duration を使用）。"""
    duration_str = probe.get("format", {}).get("duration")
    assert duration_str is not None, "duration が取得できませんでした"
    return float(duration_str)


# ===========================================================================
# ヘルパー: OTIO タイムライン構築（複数ソース版）
# ===========================================================================


def _make_multi_source_timeline(
    clips: list[tuple[Path, float, float]],
    timeline_name: str = "e2e_merge_test",
) -> otio.schema.Timeline:
    """複数クリップ（各別ソース）の OTIO タイムラインを生成する。

    clips: [(source_path, duration_sec, rate), ...] のリスト。
    各クリップは別ソースを持ち、先頭 video トラック1本に連結する。
    """
    track = otio.schema.Track(name="video", kind=otio.schema.TrackKind.Video)

    for source_path, duration_sec, rate in clips:
        ref = otio.schema.ExternalReference(target_url=str(source_path))
        clip = otio.schema.Clip(
            name=source_path.name,
            media_reference=ref,
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0.0, rate),
                duration=otio.opentime.RationalTime(duration_sec * rate, rate),
            ),
        )
        track.append(clip)

    timeline = otio.schema.Timeline(name=timeline_name)
    timeline.tracks.append(track)
    return timeline


def _make_single_source_timeline(
    source_path: Path,
    duration_sec: float,
    rate: float,
) -> otio.schema.Timeline:
    """単一クリップ（ソース全体）の OTIO タイムラインを生成する（ネガティブ対照用）。"""
    ref = otio.schema.ExternalReference(target_url=str(source_path))
    clip = otio.schema.Clip(
        name=source_path.name,
        media_reference=ref,
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, rate),
            duration=otio.opentime.RationalTime(duration_sec * rate, rate),
        ),
    )
    track = otio.schema.Track(name="video", kind=otio.schema.TrackKind.Video)
    track.append(clip)
    timeline = otio.schema.Timeline(name="e2e_single_test")
    timeline.tracks.append(track)
    return timeline


# ===========================================================================
# テスト
# ===========================================================================


@requires_ffmpeg
@requires_ffprobe
class TestMultiSourceMergeE2E:
    """複数ソース連結の実 e2e テスト（ADR-C5-r2/C7-r2/C3 実証）。"""

    def test_render_returns_ok(self, tmp_path: Path) -> None:
        """複数ソースの timeline で render_timeline(dry_run=False) が ok=True を返す（assert1）。

        横動画（44100 mono）・縦動画（48000 stereo）・音声なし の3ソースを連結し、
        render が成功することを確認する（最小 assert）。
        """
        assert _FFMPEG is not None
        landscape = tmp_path / "landscape.mp4"
        portrait = tmp_path / "portrait.mp4"
        noaudio = tmp_path / "noaudio.mp4"

        _make_landscape_video(_FFMPEG, landscape)
        _make_portrait_video(_FFMPEG, portrait)
        _make_noaudio_video(_FFMPEG, noaudio)

        timeline = _make_multi_source_timeline(
            [
                (landscape, _DUR_LANDSCAPE, _FIRST_FPS),
                (portrait, _DUR_PORTRAIT, 25.0),
                (noaudio, _DUR_NOAUDIO, _FIRST_FPS),
            ]
        )
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render が失敗しました: {result}"
        assert out_path.exists(), "出力ファイルが生成されていません"
        assert out_path.stat().st_size > 0, "出力ファイルのサイズが 0 です"

    def test_output_duration_equals_sum_of_sources(self, tmp_path: Path) -> None:
        """出力尺 ≒ 各 source_range duration 秒の合計（assert2・±2 フレーム許容）。

        ADR-C5-r2: fps 変換は秒尺を保存する設計（trim で切った秒区間は不変）。
        フレーム丸めによる ±2 フレーム相当の誤差は許容する（_FRAME_TOLERANCE = 2/30 ≒ 0.067 秒）。
        """
        assert _FFMPEG is not None
        assert _FFPROBE is not None

        landscape = tmp_path / "landscape.mp4"
        portrait = tmp_path / "portrait.mp4"
        noaudio = tmp_path / "noaudio.mp4"

        _make_landscape_video(_FFMPEG, landscape)
        _make_portrait_video(_FFMPEG, portrait)
        _make_noaudio_video(_FFMPEG, noaudio)

        expected_total = _DUR_LANDSCAPE + _DUR_PORTRAIT + _DUR_NOAUDIO

        timeline = _make_multi_source_timeline(
            [
                (landscape, _DUR_LANDSCAPE, _FIRST_FPS),
                (portrait, _DUR_PORTRAIT, 25.0),
                (noaudio, _DUR_NOAUDIO, _FIRST_FPS),
            ]
        )
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render が失敗しました: {result}"

        probe = _probe_media(_FFPROBE, out_path)
        actual_duration = _get_duration_seconds(probe)

        diff = abs(actual_duration - expected_total)
        assert diff <= _FRAME_TOLERANCE, (
            f"出力尺が期待値と乖離しています（assert2）:\n"
            f"  期待尺合計: {expected_total:.3f} 秒\n"
            f"  実出力尺: {actual_duration:.3f} 秒\n"
            f"  差分: {diff:.4f} 秒（許容: ±{_FRAME_TOLERANCE:.4f} 秒 = ±2 フレーム）"
        )

    def test_output_resolution_matches_first_clip(self, tmp_path: Path) -> None:
        """出力解像度 = 先頭クリップ基準（640x480）で縦動画が pad 収容される（assert3）。

        ADR-C4-r2: options.width/height 未指定 → 先頭クリップソースの解像度が基準。
        ADR-C6: force_original_aspect_ratio=decrease + pad で縦動画がアスペクト保持+黒帯収容。
        出力 width=640・height=480 を ffprobe で確認する。
        """
        assert _FFMPEG is not None
        assert _FFPROBE is not None

        landscape = tmp_path / "landscape.mp4"
        portrait = tmp_path / "portrait.mp4"
        noaudio = tmp_path / "noaudio.mp4"

        _make_landscape_video(_FFMPEG, landscape)
        _make_portrait_video(_FFMPEG, portrait)
        _make_noaudio_video(_FFMPEG, noaudio)

        timeline = _make_multi_source_timeline(
            [
                (landscape, _DUR_LANDSCAPE, _FIRST_FPS),
                (portrait, _DUR_PORTRAIT, 25.0),
                (noaudio, _DUR_NOAUDIO, _FIRST_FPS),
            ]
        )
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render が失敗しました: {result}"

        probe = _probe_media(_FFPROBE, out_path)
        video_stream = _get_video_stream(probe)
        assert video_stream is not None, "出力に video ストリームが存在しません"

        actual_w = int(video_stream["width"])
        actual_h = int(video_stream["height"])

        assert actual_w == _EXPECT_W, (
            f"出力 width が期待値と異なります（assert3）:\n"
            f"  期待: {_EXPECT_W}px\n"
            f"  実測: {actual_w}px\n"
            f"  縦動画（360x640）は pad でアスペクト保持・黒帯収容されるはず"
        )
        assert actual_h == _EXPECT_H, (
            f"出力 height が期待値と異なります（assert3）:\n"
            f"  期待: {_EXPECT_H}px\n"
            f"  実測: {actual_h}px"
        )

    def test_audio_stream_present_and_sync(self, tmp_path: Path) -> None:
        """異規格（44100Hz mono・48000Hz stereo・無音）でも連結が成立し音声1本が出力される（assert4）。

        DC-AS-002/AM-005/GP-003 実証:
        - 出力に audio ストリームが1本存在する（anullsrc 補完の効果）。
        - 音声が途切れず a/v 同期が保たれる（音声/映像の尺が一致する）。
        - aformat=48000/stereo によって規格統一が完了し concat が成立している。
        """
        assert _FFMPEG is not None
        assert _FFPROBE is not None

        landscape = tmp_path / "landscape.mp4"
        portrait = tmp_path / "portrait.mp4"
        noaudio = tmp_path / "noaudio.mp4"

        _make_landscape_video(_FFMPEG, landscape)
        _make_portrait_video(_FFMPEG, portrait)
        _make_noaudio_video(_FFMPEG, noaudio)

        expected_total = _DUR_LANDSCAPE + _DUR_PORTRAIT + _DUR_NOAUDIO

        timeline = _make_multi_source_timeline(
            [
                (landscape, _DUR_LANDSCAPE, _FIRST_FPS),
                (portrait, _DUR_PORTRAIT, 25.0),
                (noaudio, _DUR_NOAUDIO, _FIRST_FPS),
            ]
        )
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render が失敗しました: {result}"

        probe = _probe_media(_FFPROBE, out_path)
        audio_streams = _get_audio_streams(probe)

        # 音声ストリームが1本存在する（anullsrc 補完で音声なしクリップも繋がれる）
        assert len(audio_streams) == 1, (
            f"出力の音声ストリーム数が期待値と異なります（assert4）:\n"
            f"  期待: 1本\n"
            f"  実測: {len(audio_streams)} 本\n"
            f"  音声なしクリップは anullsrc で補完されて連結されるはず（ADR-C7-r2）"
        )

        # 音声尺が映像尺と一致する（a/v 同期確認）
        audio_duration_str = audio_streams[0].get("duration")
        if audio_duration_str is not None:
            audio_duration = float(audio_duration_str)
            diff_av = abs(audio_duration - expected_total)
            # a/v 同期: ±4 フレーム相当（音声エンコーダのパディング等を含む）
            av_tolerance = 4 / _FIRST_FPS
            assert diff_av <= av_tolerance, (
                f"音声と映像の尺が乖離しています（a/v 同期 assert4）:\n"
                f"  期待総尺: {expected_total:.3f} 秒\n"
                f"  音声尺: {audio_duration:.3f} 秒\n"
                f"  差分: {diff_av:.4f} 秒（許容: ±{av_tolerance:.4f} 秒 = ±4 フレーム）"
            )


@requires_ffmpeg
@requires_ffprobe
class TestSingleSourceNegativeControl:
    """ネガティブ対照: 単一ソースは複数ソース経路に入らず従来の出力を返す（ADR-C3 実証）。

    複数ソース時の規格統一（pad/aformat）が「合体起因」であることを切り分ける。
    単一ソースでは fps 統一・pad・aformat が入らず、ソースの規格がそのまま出力に反映される。
    """

    def test_single_source_render_returns_ok(self, tmp_path: Path) -> None:
        """単一ソースの timeline で render_timeline が ok=True を返す（後方互換確認）。"""
        assert _FFMPEG is not None

        landscape = tmp_path / "landscape.mp4"
        _make_landscape_video(_FFMPEG, landscape)

        timeline = _make_single_source_timeline(landscape, _DUR_LANDSCAPE, _FIRST_FPS)
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out_single.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, (
            f"単一ソース render が失敗しました（後方互換 assert）: {result}"
        )
        assert out_path.exists(), "単一ソース出力ファイルが生成されていません"

    def test_single_source_output_keeps_original_resolution(
        self, tmp_path: Path
    ) -> None:
        """単一ソースでは pad・scale なしで元の解像度（640x480）が出力される（ADR-C3 切り分け）。

        単一ソース経路では _build_filter_complex が使われ、options.width/height 未指定時は
        scale を入れない（trim/concat のみ）。元の 640x480 がそのまま出力されることを確認する。
        これにより、複数ソース時の 640x480 出力が「合体時の規格統一」によるものと切り分けられる。
        """
        assert _FFMPEG is not None
        assert _FFPROBE is not None

        landscape = tmp_path / "landscape.mp4"
        _make_landscape_video(_FFMPEG, landscape)

        timeline = _make_single_source_timeline(landscape, _DUR_LANDSCAPE, _FIRST_FPS)
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out_single.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render が失敗しました: {result}"

        probe = _probe_media(_FFPROBE, out_path)
        video_stream = _get_video_stream(probe)
        assert video_stream is not None, "出力に video ストリームが存在しません"

        actual_w = int(video_stream["width"])
        actual_h = int(video_stream["height"])

        # 単一ソース経路: scale なし → 元の 640x480 がそのまま出力される
        assert actual_w == 640, (
            f"単一ソース出力 width が元の解像度と異なります（ADR-C3 切り分け）:\n"
            f"  期待: 640px（scale なし・元ソース解像度）\n"
            f"  実測: {actual_w}px"
        )
        assert actual_h == 480, (
            f"単一ソース出力 height が元の解像度と異なります（ADR-C3 切り分け）:\n"
            f"  期待: 480px（scale なし・元ソース解像度）\n"
            f"  実測: {actual_h}px"
        )

    def test_single_source_no_pad_filter(self, tmp_path: Path) -> None:
        """単一ソースでは dry_run の filter_complex に pad が含まれない（ADR-C3 内部確認）。

        複数ソース経路は _build_multi_source_filter_complex（pad あり）を使い、
        単一ソース経路は _build_filter_complex（pad なし・trim/concat のみ）を使う。
        dry_run で filter_complex を取得し、pad が入っていないことを assert する。
        """
        assert _FFMPEG is not None

        landscape = tmp_path / "landscape.mp4"
        _make_landscape_video(_FFMPEG, landscape)

        timeline = _make_single_source_timeline(landscape, _DUR_LANDSCAPE, _FIRST_FPS)
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out_single_dry.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=True
        )
        assert result["ok"] is True, f"dry_run が失敗しました: {result}"

        fc = result["data"]["filter_complex"]
        assert "pad=" not in fc, (
            f"単一ソースの filter_complex に pad が含まれています（ADR-C3 切り分け失敗）:\n"
            f"  filter_complex: {fc}"
        )
        assert "aformat=" not in fc, (
            f"単一ソースの filter_complex に aformat が含まれています（ADR-C3 切り分け失敗）:\n"
            f"  filter_complex: {fc}"
        )

    def test_multi_source_has_pad_and_aformat_in_filter(self, tmp_path: Path) -> None:
        """複数ソースでは dry_run の filter_complex に pad・aformat が含まれる（ADR-C5-r2/C7-r2 内部確認）。

        ネガティブ対照と組み合わせることで、規格統一フィルタが
        「複数ソース時のみ」挿入されることを filter_complex レベルで確認する。
        """
        assert _FFMPEG is not None

        landscape = tmp_path / "landscape.mp4"
        portrait = tmp_path / "portrait.mp4"
        noaudio = tmp_path / "noaudio.mp4"

        _make_landscape_video(_FFMPEG, landscape)
        _make_portrait_video(_FFMPEG, portrait)
        _make_noaudio_video(_FFMPEG, noaudio)

        timeline = _make_multi_source_timeline(
            [
                (landscape, _DUR_LANDSCAPE, _FIRST_FPS),
                (portrait, _DUR_PORTRAIT, 25.0),
                (noaudio, _DUR_NOAUDIO, _FIRST_FPS),
            ]
        )
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out_multi_dry.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=True
        )
        assert result["ok"] is True, f"dry_run が失敗しました: {result}"

        fc = result["data"]["filter_complex"]

        assert "pad=" in fc, (
            f"複数ソースの filter_complex に pad が含まれていません（ADR-C5-r2 違反）:\n"
            f"  filter_complex: {fc}"
        )
        assert "aformat=" in fc, (
            f"複数ソースの filter_complex に aformat が含まれていません（ADR-C7-r2 違反）:\n"
            f"  filter_complex: {fc}"
        )
        assert "anullsrc" in fc, (
            f"複数ソースの filter_complex に anullsrc が含まれていません（ADR-C7-r2 違反）:\n"
            f"  filter_complex: {fc}"
        )
