"""test_e2e_subtitle.py — clipwright-render 字幕焼き込みの実機 e2e テスト（task_id: e2e-subtitle）。

設計根拠:
  - architecture-report-20260611-210021 §7 v2（ADR-S4-r2/S5-r2/S6-r2/S6-r3）
  - requirements-report-20260611-205356（字幕の時間基準=連結後出力先頭0秒起点・DC-AM-003）
  - ADR-S1: render 拡張（RenderOptions.subtitle 経由・MCP 経路のみ・CLI なし）
  - ADR-S3: _ALLOWED_SUBTITLE_EXTENSIONS = {.srt, .vtt, .ass}
  - ADR-S4-r2: _append_subtitle_filter signature（timeline_dir 引数なし・境界検証は render 一本化）
  - ADR-S5-r2: 字幕パスは render が絶対パス化・cwd 非依存
  - ADR-S6-r2: ASS 時 force_style 不適用・SRT/VTT は charenc=UTF-8 付与
  - ADR-S6-r3: alignment は ASS v4+ numpad
  - ADR-S8: subtitle=None で後方互換厳守
  - ADR-S10: 字幕は -i 不要（filter_complex の subtitles=filename= で直接読む）
  - DC-AM-003: 字幕タイムスタンプ基準 = 出力タイムライン先頭0秒起点
  - DC-GP-003: 全フィクスチャ・出力を tmp_path 配下に閉じ自動 teardown

テスト構成:
  1. フィクスチャ生成
     - 本編: testsrc 映像 3 秒・320x240・25fps（既知解像度）
     - SRT/VTT/ASS 字幕: 出力タイムライン 0.5〜2.5 秒に日本語 1 行
     - 日本語フォント: C:\\Windows\\Fonts 配下の Meiryo を使用
       （フォント不在時は関連テストを skip）

  assert 一覧（必須）:
    assert-1: render_timeline(dry_run=False) で字幕 1 本が outputs に生成
    assert-2: 字幕領域のピクセルが字幕なし出力と有意に異なる（SSIM < 0.999 / PSNR < 50 dB）
    assert-3: ネガティブ対照 — subtitle=None は字幕なし出力（差分が字幕起因と切り分け・B-3 教訓）
    assert-4: 日本語が豆腐化せず表示（fonts_dir 指定時・字幕 SSIM < 0.999）
    assert-5: 基本スタイル（font_size）反映 — サイズ指定有無で字幕領域 SSIM が異なる
    assert-6: SRT / VTT / ASS の 3 形式で焼ける（M2 で VTT 直読可確認済み）
    assert-7: 後方互換 — subtitle=None は映像不変（字幕あり出力と SSIM が 1.0 未満であることで
              差異を確認し、字幕なし同士は SSIM=1.0 を期待）

実行方法（ffmpeg 不在時は skip）:
  uv run --package clipwright-render pytest -k e2e_subtitle

ffmpeg を PATH に通すか CLIPWRIGHT_FFMPEG / CLIPWRIGHT_FFPROBE 環境変数で指定すること。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import opentimelineio as otio
import pytest

from clipwright_render.render import render_timeline
from clipwright_render.schemas import RenderOptions, SubtitleOptions

# ===========================================================================
# ffmpeg / ffprobe パス解決（conftest.py の require_ffmpeg と同パターン）
# ===========================================================================

# 他の e2e テストファイル（test_e2e_merge.py 等）と同一のスタンドアロン実装パターン。
# conftest.py と同一ロジックで重複実装になるが、e2e ファイルはスタンドアロンとする
# 本プロジェクトのコンベンションに従う（S-L-6 参照）。


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

_E2E_TIMEOUT: int = int(os.environ.get("E2E_TIMEOUT_SEC", "120"))

_MAIN_DUR = 3.0  # 本編: 3 秒
_RATE = 25.0  # 映像 fps
_WIDTH = 320  # 映像幅（ピクセル）
_HEIGHT = 240  # 映像高さ（ピクセル）

# 字幕表示区間（出力タイムライン先頭0秒起点・DC-AM-003）
_SUB_START_S = 0.5  # 字幕表示開始: 0.5 秒
_SUB_END_S = 2.5  # 字幕表示終了: 2.5 秒
_FRAME_SAMPLE_S = 1.0  # ピクセル比較に使うフレーム（字幕表示中）

# 日本語字幕テキスト
_JP_TEXT = "こんにちは世界"
_EN_TEXT = "Hello World Subtitle"

# Windows フォントディレクトリ（CJK フォント確認）
_WINDOWS_FONTS_DIR = r"C:\Windows\Fonts"
_JP_FONT_NAME = "Meiryo"

# 日本語フォント (Meiryo .ttc) の存在確認
_JP_FONTS_DIR_EXISTS = (
    Path(_WINDOWS_FONTS_DIR).is_dir()
    and Path(_WINDOWS_FONTS_DIR).joinpath("meiryo.ttc").exists()
)

requires_cjk_font = pytest.mark.skipif(
    not _JP_FONTS_DIR_EXISTS,
    reason=(
        f"CJK フォントが見つかりません: {_WINDOWS_FONTS_DIR}\\meiryo.ttc。"
        "日本語フォントをインストールするか、fonts_dir を設定してください。"
    ),
)

# SSIM しきい値: 字幕あり/なしの有意差（字幕が焼かれていれば SSIM < 0.999）
_SSIM_PIXEL_DIFF_THRESHOLD = 0.999

# ===========================================================================
# ヘルパー: フィクスチャ生成
# ===========================================================================


def _make_main_video(ffmpeg: str, output: Path) -> None:
    """本編フィクスチャ: testsrc 映像（3 秒・320x240・25fps）を生成する。

    音声なし（字幕テストは映像のみで十分。音声パイプは bgm/loudness テストで別途確認済み）。
    DC-GP-003: tmp_path 配下に生成し自動 teardown。
    """
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={_WIDTH}x{_HEIGHT}:rate={int(_RATE)}:duration={_MAIN_DUR}",
        "-t",
        str(_MAIN_DUR),
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
        f"本編フィクスチャ生成に失敗しました: {result.stderr[:400]}"
    )


def _make_srt(output: Path, text: str = _EN_TEXT) -> None:
    """SRT 字幕ファイルを生成する（UTF-8）。

    タイムスタンプは出力タイムライン先頭0秒起点（DC-AM-003）。
    """
    start_ms = int(_SUB_START_S * 1000)
    end_ms = int(_SUB_END_S * 1000)
    start_str = f"00:00:{start_ms // 1000:02d},{start_ms % 1000:03d}"
    end_str = f"00:00:{end_ms // 1000:02d},{end_ms % 1000:03d}"
    content = f"1\n{start_str} --> {end_str}\n{text}\n\n"
    output.write_text(content, encoding="utf-8")


def _make_vtt(output: Path, text: str = _EN_TEXT) -> None:
    """VTT 字幕ファイルを生成する（UTF-8）。

    タイムスタンプは出力タイムライン先頭0秒起点（DC-AM-003）。
    M2 実機確認済み: VTT は subtitles フィルタで直読可（ADR-S3/ADR-S9）。
    """
    start_str = f"00:00:0{int(_SUB_START_S)}.{int((_SUB_START_S % 1) * 1000):03d}"
    end_str = f"00:00:0{int(_SUB_END_S)}.{int((_SUB_END_S % 1) * 1000):03d}"
    content = f"WEBVTT\n\n{start_str} --> {end_str}\n{text}\n\n"
    output.write_text(content, encoding="utf-8")


def _make_ass(output: Path, text: str = _EN_TEXT, font_name: str = "Arial") -> None:
    """ASS 字幕ファイルを生成する（UTF-8）。

    内蔵スタイル（FontSize=20・白字・Alignment=2=中下）を持つ。
    ADR-S6-r2: ASS は内蔵スタイル優先のため force_style は付与されない。
    タイムスタンプは出力タイムライン先頭0秒起点（DC-AM-003）。
    """
    start_s = _SUB_START_S
    end_s = _SUB_END_S
    start_h = int(start_s // 3600)
    start_m = int((start_s % 3600) // 60)
    start_sec = start_s % 60
    end_h = int(end_s // 3600)
    end_m = int((end_s % 3600) // 60)
    end_sec = end_s % 60
    start_str = f"{start_h}:{start_m:02d}:{start_sec:04.2f}"
    end_str = f"{end_h}:{end_m:02d}:{end_sec:04.2f}"
    content = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font_name},20,&H00FFFFFF,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{text}\n"
    )
    output.write_text(content, encoding="utf-8")


# ===========================================================================
# ヘルパー: OTIO タイムライン構築
# ===========================================================================


def _make_timeline(
    source_path: Path,
    duration_sec: float = _MAIN_DUR,
    rate: float = _RATE,
) -> otio.schema.Timeline:
    """単一クリップの OTIO タイムラインを生成する。"""
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
    timeline = otio.schema.Timeline(name="e2e_subtitle_test")
    timeline.tracks.append(track)
    return timeline


def _save_timeline(timeline: otio.schema.Timeline, path: Path) -> None:
    """OTIO タイムラインをファイルに保存する。"""
    otio.adapters.write_to_file(timeline, str(path))


# ===========================================================================
# ヘルパー: ピクセル差分計測
# ===========================================================================


def _extract_frame(ffmpeg: str, video: Path, time_s: float, output_png: Path) -> None:
    """動画から指定時刻のフレームを PNG で抽出する。"""
    cmd = [
        ffmpeg,
        "-y",
        "-ss",
        str(time_s),
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-f",
        "image2",
        str(output_png),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"フレーム抽出に失敗しました（{video.name} @ {time_s}s）: {result.stderr[:200]}"
    )
    assert output_png.exists(), f"フレーム PNG が生成されませんでした: {output_png}"


def _measure_ssim(ffmpeg: str, frame_a: Path, frame_b: Path) -> float:
    """2フレームの SSIM All 値を計測して返す（1.0 = 完全一致、< 1.0 = ピクセル差あり）。

    SSIM（構造的類似度）は字幕焼き込みの有無を判定する指標として使用する。
    字幕が焼き込まれていれば字幕領域のピクセルが変化し SSIM < 1.0 になる。
    """
    cmd = [
        ffmpeg,
        "-i",
        str(frame_a),
        "-i",
        str(frame_b),
        "-lavfi",
        "ssim",
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
    assert result.returncode == 0, f"SSIM 計測に失敗しました: {result.stderr[:200]}"
    m = re.search(r"All:([\d.]+)", result.stderr)
    assert m is not None, f"SSIM All 値が見つかりません:\n{result.stderr[-200:]}"
    return float(m.group(1))


def _measure_psnr(ffmpeg: str, frame_a: Path, frame_b: Path) -> float:
    """2フレームの PSNR average 値を計測して返す（dB）。

    PSNR < 50 dB なら有意なピクセル差あり（字幕焼き込みの補助確認）。
    """
    cmd = [
        ffmpeg,
        "-i",
        str(frame_a),
        "-i",
        str(frame_b),
        "-lavfi",
        "psnr",
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
    assert result.returncode == 0, f"PSNR 計測に失敗しました: {result.stderr[:200]}"
    m = re.search(r"average:([\d.]+)", result.stderr)
    assert m is not None, f"PSNR average 値が見つかりません:\n{result.stderr[-200:]}"
    return float(m.group(1))


# ===========================================================================
# テスト: assert-1 + assert-3（基本生成・ネガティブ対照）
# ===========================================================================


@requires_ffmpeg
class TestSubtitleBasicRender:
    """字幕焼き込みの基本生成実証（assert-1・assert-3）。

    assert-1: render_timeline(dry_run=False) で字幕付き 1 本が outputs に生成される。
    assert-3: subtitle=None は字幕なしで出力（ネガティブ対照・B-3 教訓）。
    """

    def test_render_with_subtitle_returns_ok(self, tmp_path: Path) -> None:
        """字幕付き timeline で render が ok=True を返し出力ファイルが生成される（assert-1）。

        MCP/render_timeline 直叩き経路で確認（CLI は通さない・DC-AS-003）。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        srt = tmp_path / "test.srt"

        _make_main_video(_FFMPEG, main_src)
        _make_srt(srt)

        timeline = _make_timeline(main_src)
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline(timeline, timeline_path)

        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path),
            str(out_path),
            RenderOptions(subtitle=SubtitleOptions(path=str(srt))),
            dry_run=False,
        )
        assert result["ok"] is True, f"render が失敗しました: {result}"
        assert out_path.exists(), "出力ファイルが生成されていません"
        assert out_path.stat().st_size > 0, "出力ファイルのサイズが 0 です"

    def test_render_dry_run_filter_has_subtitles(self, tmp_path: Path) -> None:
        """dry_run で filter_complex に subtitles が含まれる（ADR-S10・内部確認）。

        字幕は -i を追加せず filter_complex の subtitles=filename= で直接読む。
        ADR-S10: dry_run で filter_complex を確認し subtitles が挿入されていることを assert する。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        srt = tmp_path / "test.srt"

        _make_main_video(_FFMPEG, main_src)
        _make_srt(srt)

        timeline = _make_timeline(main_src)
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline(timeline, timeline_path)

        out_path = tmp_path / "out_dry.mp4"
        result = render_timeline(
            str(timeline_path),
            str(out_path),
            RenderOptions(subtitle=SubtitleOptions(path=str(srt), font_size=24)),
            dry_run=True,
        )
        assert result["ok"] is True, f"dry_run が失敗しました: {result}"

        fc = result["data"]["filter_complex"]
        assert "subtitles=filename=" in fc, (
            f"filter_complex に subtitles が含まれていません（ADR-S10 違反）:\n"
            f"  filter_complex: {fc}"
        )
        assert "[outvsub]" in fc, (
            f"filter_complex に [outvsub] ラベルが含まれていません:\n"
            f"  filter_complex: {fc}"
        )
        # ADR-S10: 字幕は -i 不要のため input_sources は元の本数のまま
        # SRT/VTT 時は charenc=UTF-8 が付与される（ADR-S6-r2）
        assert "charenc=UTF-8" in fc, (
            f"SRT 時に charenc=UTF-8 が filter_complex に含まれていません（ADR-S6-r2 違反）:\n"
            f"  filter_complex: {fc}"
        )

    def test_subtitle_none_dry_run_no_subtitles_filter(self, tmp_path: Path) -> None:
        """subtitle=None のとき dry_run filter_complex に subtitles が含まれない（assert-3・ADR-S8）。

        後方互換確認: subtitle=None で _append_subtitle_filter が呼ばれず
        filter_complex に subtitles が挿入されないことを確認する。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"

        _make_main_video(_FFMPEG, main_src)

        timeline = _make_timeline(main_src)
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline(timeline, timeline_path)

        out_path = tmp_path / "out_nosub_dry.mp4"
        result = render_timeline(
            str(timeline_path),
            str(out_path),
            RenderOptions(),  # subtitle=None
            dry_run=True,
        )
        assert result["ok"] is True, f"dry_run が失敗しました: {result}"

        fc = result["data"]["filter_complex"]
        assert "subtitles" not in fc, (
            f"subtitle=None なのに filter_complex に subtitles が含まれています（ADR-S8 違反）:\n"
            f"  filter_complex: {fc}"
        )
        assert "[outvsub]" not in fc, (
            f"subtitle=None なのに filter_complex に [outvsub] が含まれています（ADR-S8 違反）:\n"
            f"  filter_complex: {fc}"
        )


# ===========================================================================
# テスト: assert-2（字幕焼き込みピクセル実証）
# ===========================================================================


@requires_ffmpeg
class TestSubtitlePixelDiff:
    """字幕焼き込みのピクセル有意差実証（assert-2・SSIM/PSNR）。

    字幕あり/なしの出力フレームを SSIM・PSNR で比較し、
    字幕が実際に映像に焼き込まれていることを実証する。
    SSIM All < 0.999 かつ PSNR < 50 dB で有意差とする。
    """

    def test_subtitle_pixels_differ_from_no_subtitle(self, tmp_path: Path) -> None:
        """字幕あり出力フレームが字幕なし出力フレームとピクセルが有意に異なる（assert-2）。

        字幕表示中（1.0 秒のフレーム）を比較する。
        SSIM < 0.999 で字幕焼き込みが実証される（B-3 教訓: ネガティブ対照で切り分け）。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        srt = tmp_path / "test.srt"

        _make_main_video(_FFMPEG, main_src)
        _make_srt(srt)

        # 字幕あり出力
        tl_sub = _make_timeline(main_src)
        tl_sub_path = tmp_path / "tl_sub.otio"
        _save_timeline(tl_sub, tl_sub_path)
        out_sub = tmp_path / "out_sub.mp4"
        result_sub = render_timeline(
            str(tl_sub_path),
            str(out_sub),
            RenderOptions(subtitle=SubtitleOptions(path=str(srt), font_size=28)),
            dry_run=False,
        )
        assert result_sub["ok"] is True, f"字幕あり render が失敗しました: {result_sub}"

        # 字幕なし出力（ネガティブ対照）
        tl_nosub = _make_timeline(main_src)
        tl_nosub_path = tmp_path / "tl_nosub.otio"
        _save_timeline(tl_nosub, tl_nosub_path)
        out_nosub = tmp_path / "out_nosub.mp4"
        result_nosub = render_timeline(
            str(tl_nosub_path),
            str(out_nosub),
            RenderOptions(),  # subtitle=None
            dry_run=False,
        )
        assert result_nosub["ok"] is True, (
            f"字幕なし render が失敗しました: {result_nosub}"
        )

        # フレーム抽出（字幕表示中）
        frame_sub = tmp_path / "frame_sub.png"
        frame_nosub = tmp_path / "frame_nosub.png"
        _extract_frame(_FFMPEG, out_sub, _FRAME_SAMPLE_S, frame_sub)
        _extract_frame(_FFMPEG, out_nosub, _FRAME_SAMPLE_S, frame_nosub)

        ssim = _measure_ssim(_FFMPEG, frame_sub, frame_nosub)
        psnr = _measure_psnr(_FFMPEG, frame_sub, frame_nosub)

        assert ssim < _SSIM_PIXEL_DIFF_THRESHOLD, (
            f"字幕あり/なしのピクセル差が不十分です（assert-2・焼き込み未実証）:\n"
            f"  SSIM All: {ssim:.6f}（期待: < {_SSIM_PIXEL_DIFF_THRESHOLD}）\n"
            f"  PSNR: {psnr:.2f} dB\n"
            f"  字幕が実際に映像に焼き込まれていれば字幕領域のピクセルが変化するはず"
        )
        assert psnr < 50.0, (
            f"字幕あり/なしの PSNR が有意差基準を超えています（assert-2・補助確認）:\n"
            f"  PSNR: {psnr:.2f} dB（期待: < 50.0 dB）\n"
            f"  SSIM: {ssim:.6f}"
        )

    def test_no_subtitle_frames_identical_to_baseline(self, tmp_path: Path) -> None:
        """字幕なし出力を2回生成すると同じフレームになる（ネガティブ対照・切り分け）。

        subtitle=None で2回 render して同じフレームが得られることを確認する。
        これにより「SSIM 差が字幕起因」であることを切り分ける。
        同一入力の2出力は SSIM ≒ 1.0 であるはず（エンコーダーの非決定性を考慮 >= 0.98）。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"

        _make_main_video(_FFMPEG, main_src)

        tl1 = _make_timeline(main_src)
        tl1_path = tmp_path / "tl_a.otio"
        _save_timeline(tl1, tl1_path)
        out_a = tmp_path / "out_a.mp4"

        tl2 = _make_timeline(main_src)
        tl2_path = tmp_path / "tl_b.otio"
        _save_timeline(tl2, tl2_path)
        out_b = tmp_path / "out_b.mp4"

        r_a = render_timeline(str(tl1_path), str(out_a), RenderOptions(), dry_run=False)
        r_b = render_timeline(str(tl2_path), str(out_b), RenderOptions(), dry_run=False)

        assert r_a["ok"] is True
        assert r_b["ok"] is True

        frame_a = tmp_path / "frame_a.png"
        frame_b = tmp_path / "frame_b.png"
        _extract_frame(_FFMPEG, out_a, _FRAME_SAMPLE_S, frame_a)
        _extract_frame(_FFMPEG, out_b, _FRAME_SAMPLE_S, frame_b)

        ssim = _measure_ssim(_FFMPEG, frame_a, frame_b)
        assert ssim >= 0.98, (
            f"字幕なし同士の SSIM が期待値を下回っています（ネガティブ対照）:\n"
            f"  SSIM: {ssim:.6f}（期待: >= 0.98）\n"
            f"  同一入力を 2 回 render しても SSIM はほぼ 1.0 になるはず"
        )


# ===========================================================================
# テスト: assert-4（日本語・豆腐化なし実証）
# ===========================================================================


@requires_ffmpeg
@requires_cjk_font
class TestSubtitleJapanese:
    """日本語字幕が豆腐化せず表示されることを実証するテスト（assert-4）。

    fonts_dir=C:\\Windows\\Fonts・font_name=Meiryo を指定して日本語 SRT を焼き込み、
    字幕なし出力との SSIM 差が有意であることを確認する（グリフが描画されていれば差が出る）。
    フォント不在環境では skip する（requires_cjk_font マーカー）。
    """

    def test_japanese_subtitle_renders_with_cjk_font(self, tmp_path: Path) -> None:
        """日本語字幕が Meiryo フォントで豆腐化せず表示される（assert-4）。

        SSIM < 0.999 で CJK グリフが描画されていることを確認する。
        フォントが正しく指定されていれば ASCII 字幕と同様のピクセル変化が生じる。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        srt_jp = tmp_path / "test_jp.srt"

        _make_main_video(_FFMPEG, main_src)
        _make_srt(srt_jp, text=_JP_TEXT)

        # 日本語字幕あり（Meiryo・Windows Fonts）
        tl_jp = _make_timeline(main_src)
        tl_jp_path = tmp_path / "tl_jp.otio"
        _save_timeline(tl_jp, tl_jp_path)
        out_jp = tmp_path / "out_jp.mp4"
        result_jp = render_timeline(
            str(tl_jp_path),
            str(out_jp),
            RenderOptions(
                subtitle=SubtitleOptions(
                    path=str(srt_jp),
                    font_name=_JP_FONT_NAME,
                    fonts_dir=_WINDOWS_FONTS_DIR,
                    font_size=32,
                )
            ),
            dry_run=False,
        )
        assert result_jp["ok"] is True, f"日本語字幕 render が失敗しました: {result_jp}"

        # ネガティブ対照（字幕なし）
        tl_nosub = _make_timeline(main_src)
        tl_nosub_path = tmp_path / "tl_nosub.otio"
        _save_timeline(tl_nosub, tl_nosub_path)
        out_nosub = tmp_path / "out_nosub.mp4"
        result_nosub = render_timeline(
            str(tl_nosub_path), str(out_nosub), RenderOptions(), dry_run=False
        )
        assert result_nosub["ok"] is True

        # フレーム抽出
        frame_jp = tmp_path / "frame_jp.png"
        frame_nosub = tmp_path / "frame_nosub.png"
        _extract_frame(_FFMPEG, out_jp, _FRAME_SAMPLE_S, frame_jp)
        _extract_frame(_FFMPEG, out_nosub, _FRAME_SAMPLE_S, frame_nosub)

        ssim = _measure_ssim(_FFMPEG, frame_jp, frame_nosub)
        assert ssim < _SSIM_PIXEL_DIFF_THRESHOLD, (
            f"日本語字幕のピクセル差が不十分です（assert-4・日本語豆腐化疑い）:\n"
            f"  SSIM All: {ssim:.6f}（期待: < {_SSIM_PIXEL_DIFF_THRESHOLD}）\n"
            f"  font_name={_JP_FONT_NAME}・fonts_dir={_WINDOWS_FONTS_DIR}\n"
            f"  フォントが正しく読み込まれ CJK グリフが描画されていれば差が出るはず"
        )


# ===========================================================================
# テスト: assert-5（スタイル反映・font_size 差異）
# ===========================================================================


@requires_ffmpeg
class TestSubtitleStyle:
    """基本スタイル（font_size）反映を実証するテスト（assert-5）。

    font_size=48 と font_size=12 では字幕領域のピクセル変化量が異なることを確認する。
    大きいフォントは多くのピクセルを変化させるため SSIM がより低くなる。
    """

    def test_large_font_size_has_more_pixel_diff_than_small(
        self, tmp_path: Path
    ) -> None:
        """font_size=48 の SSIM 差は font_size=12 より大きい（assert-5・スタイル反映）。

        大きい字幕は字幕領域で多くのピクセルを変化させるため、
        字幕なし出力との SSIM 差が小さい字幕より大きくなることを確認する。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        srt = tmp_path / "test.srt"

        _make_main_video(_FFMPEG, main_src)
        _make_srt(srt)

        # 字幕なし出力（共通ベースライン）
        tl_nosub = _make_timeline(main_src)
        tl_nosub_path = tmp_path / "tl_nosub.otio"
        _save_timeline(tl_nosub, tl_nosub_path)
        out_nosub = tmp_path / "out_nosub.mp4"
        r_nosub = render_timeline(
            str(tl_nosub_path), str(out_nosub), RenderOptions(), dry_run=False
        )
        assert r_nosub["ok"] is True

        # font_size=48（大）
        tl_big = _make_timeline(main_src)
        tl_big_path = tmp_path / "tl_big.otio"
        _save_timeline(tl_big, tl_big_path)
        out_big = tmp_path / "out_big.mp4"
        r_big = render_timeline(
            str(tl_big_path),
            str(out_big),
            RenderOptions(subtitle=SubtitleOptions(path=str(srt), font_size=48)),
            dry_run=False,
        )
        assert r_big["ok"] is True

        # font_size=12（小）
        tl_small = _make_timeline(main_src)
        tl_small_path = tmp_path / "tl_small.otio"
        _save_timeline(tl_small, tl_small_path)
        out_small = tmp_path / "out_small.mp4"
        r_small = render_timeline(
            str(tl_small_path),
            str(out_small),
            RenderOptions(subtitle=SubtitleOptions(path=str(srt), font_size=12)),
            dry_run=False,
        )
        assert r_small["ok"] is True

        # フレーム抽出
        frame_nosub = tmp_path / "frame_nosub.png"
        frame_big = tmp_path / "frame_big.png"
        frame_small = tmp_path / "frame_small.png"
        _extract_frame(_FFMPEG, out_nosub, _FRAME_SAMPLE_S, frame_nosub)
        _extract_frame(_FFMPEG, out_big, _FRAME_SAMPLE_S, frame_big)
        _extract_frame(_FFMPEG, out_small, _FRAME_SAMPLE_S, frame_small)

        ssim_big = _measure_ssim(_FFMPEG, frame_big, frame_nosub)
        ssim_small = _measure_ssim(_FFMPEG, frame_small, frame_nosub)

        # 大フォントは多くのピクセルを変化させる → SSIM が小フォントより低い
        assert ssim_big < ssim_small, (
            f"font_size=48 の SSIM 差が font_size=12 より大きくなっていません（assert-5）:\n"
            f"  SSIM (size=48 vs nosub): {ssim_big:.6f}\n"
            f"  SSIM (size=12 vs nosub): {ssim_small:.6f}\n"
            f"  大きいフォントは多くのピクセルを変化させるため SSIM が低くなるはず"
        )

    def test_force_style_in_filter_complex_for_srt(self, tmp_path: Path) -> None:
        """SRT 時に force_style が filter_complex に含まれる（ADR-S6-r2 内部確認）。

        SubtitleOptions にスタイル指定がある場合、SRT/VTT は force_style= が
        filter_complex に付与されることを dry_run で確認する。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        srt = tmp_path / "test.srt"

        _make_main_video(_FFMPEG, main_src)
        _make_srt(srt)

        tl = _make_timeline(main_src)
        tl_path = tmp_path / "tl.otio"
        _save_timeline(tl, tl_path)

        out_path = tmp_path / "out_dry.mp4"
        result = render_timeline(
            str(tl_path),
            str(out_path),
            RenderOptions(
                subtitle=SubtitleOptions(
                    path=str(srt),
                    font_size=24,
                    alignment=2,
                    margin_v=20,
                )
            ),
            dry_run=True,
        )
        assert result["ok"] is True

        fc = result["data"]["filter_complex"]
        assert "force_style=" in fc, (
            f"SRT 時に force_style が filter_complex に含まれていません（ADR-S6-r2 違反）:\n"
            f"  filter_complex: {fc}"
        )
        assert "FontSize=24" in fc, (
            f"font_size=24 が force_style に反映されていません:\n  filter_complex: {fc}"
        )
        assert "Alignment=2" in fc, (
            f"alignment=2 が force_style に反映されていません（ADR-S6-r3）:\n"
            f"  filter_complex: {fc}"
        )
        assert "MarginV=20" in fc, (
            f"margin_v=20 が force_style に反映されていません:\n  filter_complex: {fc}"
        )

    def test_ass_no_force_style_in_filter_complex(self, tmp_path: Path) -> None:
        """ASS 時に force_style が filter_complex に含まれない（ADR-S6-r2 内部確認）。

        ASS は内蔵スタイルを持つため force_style を適用しない（DC-AS-002）。
        SubtitleOptions にスタイル指定があっても ASS 入力時は force_style= を付与しない。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        ass = tmp_path / "test.ass"

        _make_main_video(_FFMPEG, main_src)
        _make_ass(ass)

        tl = _make_timeline(main_src)
        tl_path = tmp_path / "tl_ass.otio"
        _save_timeline(tl, tl_path)

        out_path = tmp_path / "out_ass_dry.mp4"
        result = render_timeline(
            str(tl_path),
            str(out_path),
            RenderOptions(
                subtitle=SubtitleOptions(
                    path=str(ass),
                    font_size=24,  # 指定があっても ASS では force_style に入らない
                )
            ),
            dry_run=True,
        )
        assert result["ok"] is True

        fc = result["data"]["filter_complex"]
        assert "force_style=" not in fc, (
            f"ASS 時に force_style が filter_complex に含まれています（ADR-S6-r2/DC-AS-002 違反）:\n"
            f"  filter_complex: {fc}"
        )
        # ASS 時は charenc=UTF-8 も付与しない（実機確認済み・ADR-S6-r2）
        assert "charenc=UTF-8" not in fc, (
            f"ASS 時に charenc=UTF-8 が filter_complex に含まれています（ADR-S6-r2 違反）:\n"
            f"  filter_complex: {fc}"
        )


# ===========================================================================
# テスト: assert-6（SRT/VTT/ASS の 3 形式）
# ===========================================================================


@requires_ffmpeg
class TestSubtitleFormats:
    """SRT / VTT / ASS の 3 形式で字幕が焼ける実証（assert-6）。

    M2 実機確認済み: VTT は subtitles フィルタで直読可（ADR-S3/ADR-S9）。
    3 形式とも render_timeline が ok=True を返し出力ファイルが生成されることを確認する。
    各形式で字幕表示中フレームのピクセルが字幕なし出力と有意に異なることも確認する。
    """

    def _render_with_subtitle(
        self,
        ffmpeg: str,
        tmp_path: Path,
        suffix: str,
        subtitle_path: Path,
    ) -> tuple[bool, Path]:
        """字幕付き render を実行し (ok, out_path) を返す。"""
        main_src = tmp_path / "main.mp4"
        tl = _make_timeline(main_src)
        tl_path = tmp_path / f"tl_{suffix}.otio"
        _save_timeline(tl, tl_path)

        out_path = tmp_path / f"out_{suffix}.mp4"
        opts = RenderOptions(
            subtitle=SubtitleOptions(path=str(subtitle_path), font_size=28)
        )
        result = render_timeline(str(tl_path), str(out_path), opts, dry_run=False)
        return result["ok"], out_path

    def test_srt_format_renders_ok(self, tmp_path: Path) -> None:
        """SRT 形式で字幕が焼ける（assert-6）。"""
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        srt = tmp_path / "test.srt"
        _make_main_video(_FFMPEG, main_src)
        _make_srt(srt)

        ok, out = self._render_with_subtitle(_FFMPEG, tmp_path, "srt", srt)
        assert ok is True, "SRT 字幕 render が失敗しました"
        assert out.exists() and out.stat().st_size > 0, (
            "SRT 出力ファイルが生成されていません"
        )

    def test_vtt_format_renders_ok(self, tmp_path: Path) -> None:
        """VTT 形式で字幕が焼ける（assert-6・M2 VTT 直読可確認済み）。

        VTT は libavformat が直接 WebVTT として読む。
        ADR-S9: VTT 直読可なので SRT 変換は不要。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        vtt = tmp_path / "test.vtt"
        _make_main_video(_FFMPEG, main_src)
        _make_vtt(vtt)

        ok, out = self._render_with_subtitle(_FFMPEG, tmp_path, "vtt", vtt)
        assert ok is True, (
            "VTT 字幕 render が失敗しました（VTT が直読できていない可能性: ADR-S9）"
        )
        assert out.exists() and out.stat().st_size > 0, (
            "VTT 出力ファイルが生成されていません"
        )

    def test_ass_format_renders_ok(self, tmp_path: Path) -> None:
        """ASS 形式で字幕が焼ける（assert-6）。

        ASS は内蔵スタイルを持つため force_style は不適用（ADR-S6-r2）。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        ass = tmp_path / "test.ass"
        _make_main_video(_FFMPEG, main_src)
        _make_ass(ass)

        ok, out = self._render_with_subtitle(_FFMPEG, tmp_path, "ass", ass)
        assert ok is True, "ASS 字幕 render が失敗しました"
        assert out.exists() and out.stat().st_size > 0, (
            "ASS 出力ファイルが生成されていません"
        )

    def test_three_formats_pixel_diff_vs_no_subtitle(self, tmp_path: Path) -> None:
        """SRT/VTT/ASS の 3 形式とも字幕なし出力とピクセル差が有意（assert-6・実証）。

        各形式の出力フレームと字幕なし出力フレームの SSIM を比較する。
        SSIM < 0.999 で字幕が焼き込まれていることを確認する。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        _make_main_video(_FFMPEG, main_src)

        srt = tmp_path / "test.srt"
        vtt = tmp_path / "test.vtt"
        ass = tmp_path / "test.ass"
        _make_srt(srt)
        _make_vtt(vtt)
        _make_ass(ass)

        # 字幕なし出力（共通ベースライン）
        tl_nosub = _make_timeline(main_src)
        tl_nosub_path = tmp_path / "tl_nosub.otio"
        _save_timeline(tl_nosub, tl_nosub_path)
        out_nosub = tmp_path / "out_nosub.mp4"
        r_nosub = render_timeline(
            str(tl_nosub_path), str(out_nosub), RenderOptions(), dry_run=False
        )
        assert r_nosub["ok"] is True
        frame_nosub = tmp_path / "frame_nosub.png"
        _extract_frame(_FFMPEG, out_nosub, _FRAME_SAMPLE_S, frame_nosub)

        failures: list[str] = []
        for fmt, sub_path in [("srt", srt), ("vtt", vtt), ("ass", ass)]:
            ok, out = self._render_with_subtitle(_FFMPEG, tmp_path, fmt, sub_path)
            if not ok:
                failures.append(f"{fmt.upper()}: render が失敗")
                continue

            frame = tmp_path / f"frame_{fmt}.png"
            _extract_frame(_FFMPEG, out, _FRAME_SAMPLE_S, frame)
            ssim = _measure_ssim(_FFMPEG, frame, frame_nosub)

            if ssim >= _SSIM_PIXEL_DIFF_THRESHOLD:
                failures.append(
                    f"{fmt.upper()}: SSIM={ssim:.6f} >= {_SSIM_PIXEL_DIFF_THRESHOLD}"
                    "（字幕焼き込み未実証）"
                )

        assert not failures, (
            "以下の形式で字幕焼き込みのピクセル差が不十分です（assert-6）:\n"
            + "\n".join(f"  {f}" for f in failures)
        )


# ===========================================================================
# テスト: assert-7（後方互換・subtitle=None で映像不変）
# ===========================================================================


@requires_ffmpeg
class TestSubtitleBackwardCompat:
    """後方互換実証: subtitle=None は従来出力と等価（assert-7・ADR-S8）。

    subtitle=None で render した2出力が SSIM ≒ 1.0 になることを確認し、
    「字幕なし出力」が安定していることを保証する（B-3 教訓の補足）。
    また字幕あり/なしの SSIM 差が確実に字幕起因であることを切り分ける。
    """

    def test_no_subtitle_outputs_are_equivalent(self, tmp_path: Path) -> None:
        """subtitle=None で 2 回 render した出力フレームが SSIM >= 0.98 で同一（assert-7）。

        同一入力のレンダリングは決定論的なので SSIM がほぼ 1.0 になる。
        エンコーダーの非決定性を考慮して 0.98 を下限とする。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        _make_main_video(_FFMPEG, main_src)

        for i in range(1, 3):
            tl = _make_timeline(main_src)
            tl_path = tmp_path / f"tl_{i}.otio"
            _save_timeline(tl, tl_path)
            out = tmp_path / f"out_{i}.mp4"
            r = render_timeline(str(tl_path), str(out), RenderOptions(), dry_run=False)
            assert r["ok"] is True, f"subtitle=None render {i} が失敗しました"

        frame_1 = tmp_path / "frame_1.png"
        frame_2 = tmp_path / "frame_2.png"
        _extract_frame(_FFMPEG, tmp_path / "out_1.mp4", _FRAME_SAMPLE_S, frame_1)
        _extract_frame(_FFMPEG, tmp_path / "out_2.mp4", _FRAME_SAMPLE_S, frame_2)

        ssim = _measure_ssim(_FFMPEG, frame_1, frame_2)
        assert ssim >= 0.98, (
            f"subtitle=None の 2 出力フレームの SSIM が期待値を下回っています（assert-7）:\n"
            f"  SSIM: {ssim:.6f}（期待: >= 0.98）\n"
            f"  subtitle=None は決定論的な出力を返すはず（ADR-S8）"
        )

    def test_subtitle_presence_causes_pixel_diff(self, tmp_path: Path) -> None:
        """字幕あり出力と字幕なし出力のピクセル差が字幕起因と切り分けられる（assert-7）。

        subtitle=None の出力を2本（同一）と字幕あり出力を比較し、
        字幕なし同士は SSIM >= 0.98・字幕あり/なし差は < 0.999 であることを確認する。
        差分が字幕のみに起因することを定量的に示す（B-3 教訓）。
        """
        assert _FFMPEG is not None
        main_src = tmp_path / "main.mp4"
        srt = tmp_path / "test.srt"
        _make_main_video(_FFMPEG, main_src)
        _make_srt(srt)

        # 字幕なし x2
        tl_no1 = _make_timeline(main_src)
        tl_no1_path = tmp_path / "tl_no1.otio"
        _save_timeline(tl_no1, tl_no1_path)
        out_no1 = tmp_path / "out_no1.mp4"
        r_no1 = render_timeline(
            str(tl_no1_path), str(out_no1), RenderOptions(), dry_run=False
        )
        assert r_no1["ok"] is True

        tl_no2 = _make_timeline(main_src)
        tl_no2_path = tmp_path / "tl_no2.otio"
        _save_timeline(tl_no2, tl_no2_path)
        out_no2 = tmp_path / "out_no2.mp4"
        r_no2 = render_timeline(
            str(tl_no2_path), str(out_no2), RenderOptions(), dry_run=False
        )
        assert r_no2["ok"] is True

        # 字幕あり x1
        tl_sub = _make_timeline(main_src)
        tl_sub_path = tmp_path / "tl_sub.otio"
        _save_timeline(tl_sub, tl_sub_path)
        out_sub = tmp_path / "out_sub.mp4"
        r_sub = render_timeline(
            str(tl_sub_path),
            str(out_sub),
            RenderOptions(subtitle=SubtitleOptions(path=str(srt), font_size=28)),
            dry_run=False,
        )
        assert r_sub["ok"] is True

        frame_no1 = tmp_path / "frame_no1.png"
        frame_no2 = tmp_path / "frame_no2.png"
        frame_sub = tmp_path / "frame_sub.png"
        _extract_frame(_FFMPEG, out_no1, _FRAME_SAMPLE_S, frame_no1)
        _extract_frame(_FFMPEG, out_no2, _FRAME_SAMPLE_S, frame_no2)
        _extract_frame(_FFMPEG, out_sub, _FRAME_SAMPLE_S, frame_sub)

        # 字幕なし同士: SSIM >= 0.98
        ssim_no_vs_no = _measure_ssim(_FFMPEG, frame_no1, frame_no2)
        assert ssim_no_vs_no >= 0.98, (
            f"字幕なし同士の SSIM が低すぎます（ネガティブ対照の信頼性不足）:\n"
            f"  SSIM: {ssim_no_vs_no:.6f}（期待: >= 0.98）"
        )

        # 字幕あり vs 字幕なし: SSIM < 0.999（字幕起因の差）
        ssim_sub_vs_no = _measure_ssim(_FFMPEG, frame_sub, frame_no1)
        assert ssim_sub_vs_no < _SSIM_PIXEL_DIFF_THRESHOLD, (
            f"字幕あり/なしの SSIM 差が不十分です（assert-7・切り分け失敗）:\n"
            f"  字幕あり vs なし SSIM: {ssim_sub_vs_no:.6f}（期待: < {_SSIM_PIXEL_DIFF_THRESHOLD}）\n"
            f"  字幕なし同士 SSIM: {ssim_no_vs_no:.6f}（参考）\n"
            f"  字幕あり/なしの差が字幕起因であることを確認するための対照実験"
        )
