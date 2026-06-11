"""test_e2e.py — clipwright-noise afftdn 実 e2e テスト（設計 §6.1 / v3 B-3）。

フィクスチャ生成 → detect_noise → render_timeline のフルパイプラインを
実 ffmpeg で通し、afftdn によるノイズ低減効果を volumedetect で計測する。

合格基準（§6.1）:
  out.mean_volume <= in.mean_volume - 3.0 dB

ネガティブ対照（B-3）:
  denoise なし render の mean_volume が入力比 -3.0dB を下回らない
  → -3.0dB 以上の低下が afftdn 起因であることを担保する。

前提:
  - ffmpeg は CLIPWRIGHT_FFMPEG 環境変数または PATH で解決できること。
  - ffmpeg 不在環境は @pytest.mark.e2e でスキップ。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

# -------------------------------------------------------------------
# ffmpeg 存在チェック（e2e skip 判定）
# -------------------------------------------------------------------


def _find_ffmpeg() -> str | None:
    """CLIPWRIGHT_FFMPEG または PATH で ffmpeg を解決して返す。見つからない場合は None。"""
    try:
        from clipwright.process import resolve_tool

        return resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")
    except Exception:
        return None


_FFMPEG = _find_ffmpeg()
_FFMPEG_MISSING = _FFMPEG is None

pytestmark = pytest.mark.e2e

# -------------------------------------------------------------------
# ヘルパー関数
# -------------------------------------------------------------------


def _make_fixture(tmp_path: Path) -> Path:
    """ffmpeg で映像+純ホワイトノイズ音声 2s フィクスチャを生成して返す（v3 B-3）。

    testsrc(320x240,15fps) ＋ anoisesrc(white,amplitude=0.3) を mux し
    -t 2 / -shortest で同尺2sに尺切りする。

    Note:
        e2e テストのヘルパーは「テスト用フィクスチャ生成」と「音量測定」専用であり
        プロダクションコードではない。clipwright.process.run は MCP ツール内部の
        サブプロセス規律を適用するためのラッパーであり、テストツールとして使うと
        popen 引数が合わず煩雑になる。ここでは subprocess を直接使う許容例外とする。
        timeout / capture_output / returncode 検査はすべて実施済み。  # noqa: subprocess-in-test
    """
    assert _FFMPEG is not None
    fixture = tmp_path / "fixture.mp4"
    cmd = [
        _FFMPEG,
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x240:rate=15:duration=2",
        "-f",
        "lavfi",
        "-i",
        "anoisesrc=color=white:amplitude=0.3:duration=2",
        "-t",
        "2",
        "-shortest",
        "-y",
        str(fixture),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"フィクスチャ生成失敗: {result.stderr[-400:]}"
    assert fixture.exists(), "fixture.mp4 が生成されませんでした"
    return fixture


def _get_mean_volume(path: Path) -> float:
    """ffmpeg volumedetect で音声の mean_volume (dB) を返す。

    Note:
        e2e テストの音量測定専用ヘルパー。subprocess 直呼びの許容例外
        （timeout / capture_output / returncode 検査を実施済み）。  # noqa: subprocess-in-test
    """
    assert _FFMPEG is not None
    result = subprocess.run(
        [_FFMPEG, "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    m = re.search(r"mean_volume:\s*(-?\d+\.?\d*)\s*dB", result.stderr)
    assert m is not None, (
        f"volumedetect で mean_volume が取得できませんでした。\nstderr: {result.stderr[-400:]}"
    )
    return float(m.group(1))


def _assert_ok(result: dict[str, Any], label: str) -> None:
    """ok=True でなければ pytest.fail する。"""
    if not result.get("ok"):
        pytest.fail(f"{label} が失敗しました: {result}")


# -------------------------------------------------------------------
# テスト: DC-GP-002 render 拡張反映事前確認
# -------------------------------------------------------------------


@pytest.mark.skipif(
    _FFMPEG_MISSING,
    reason="ffmpeg が見つかりません（CLIPWRIGHT_FFMPEG または PATH が必要）",
)
def test_render_processes_afftdn_directive(tmp_path: Path) -> None:
    """render が afftdn 指示入り timeline を UNSUPPORTED にならず処理し
    filter_complex に afftdn が含まれることを確認する（DC-GP-002）。"""
    from clipwright_render.render import render_timeline
    from clipwright_render.schemas import RenderOptions

    from clipwright_noise.noise import detect_noise
    from clipwright_noise.schemas import DetectNoiseOptions

    fixture = _make_fixture(tmp_path)
    timeline_path = tmp_path / "timeline.otio"

    # detect_noise で afftdn 指示を timeline に書き込む
    opts = DetectNoiseOptions(backend="afftdn", strength="medium")
    result = detect_noise(str(fixture), str(timeline_path), opts, None)
    _assert_ok(result, "detect_noise")

    # dry_run=True で render 計画を取得し filter_complex に afftdn が入ることを確認
    out_mp4 = tmp_path / "out_dryrun.mp4"
    render_opts = RenderOptions(
        video_codec="libx264", audio_codec="aac", overwrite=True
    )
    rr = render_timeline(str(timeline_path), str(out_mp4), render_opts, dry_run=True)
    _assert_ok(rr, "render_timeline (dry_run)")

    filter_complex: str = rr.get("data", {}).get("filter_complex", "")
    assert "afftdn" in filter_complex, (
        f"filter_complex に afftdn が含まれていません。filter_complex: {filter_complex!r}"
    )


# -------------------------------------------------------------------
# テスト: B-3 ネガティブ対照（denoise なし render は -3.0dB 以上低下しない）
# -------------------------------------------------------------------


@pytest.mark.skipif(
    _FFMPEG_MISSING,
    reason="ffmpeg が見つかりません（CLIPWRIGHT_FFMPEG または PATH が必要）",
)
def test_negative_control_no_denoise_within_threshold(tmp_path: Path) -> None:
    """denoise なし render の出力 mean_volume が入力比 -3.0dB を下回らないことを確認する（v3 B-3）。

    これにより「-3.0dB 以上の低下は afftdn 起因であり、
    コーデック再エンコード単独では生じない」ことを担保する。
    """
    from clipwright.media import inspect_media
    from clipwright.otio_utils import new_timeline, save_timeline
    from clipwright_render.render import render_timeline
    from clipwright_render.schemas import RenderOptions

    from clipwright_noise.noise import _add_full_clip

    fixture = _make_fixture(tmp_path)

    # denoise 指示なし timeline を生成（media と同一 tmp_path 直下）
    neg_timeline_path = tmp_path / "neg_timeline.otio"
    media_info = inspect_media(str(fixture))
    dur_sec = (
        media_info.duration.value / media_info.duration.rate
        if media_info.duration
        else 2.0
    )
    neg_tl = new_timeline(fixture.name)
    _add_full_clip(neg_tl, fixture, dur_sec, media_info.duration)
    save_timeline(neg_tl, str(neg_timeline_path))

    in_vol = _get_mean_volume(fixture)

    # denoise なしで render
    neg_out = tmp_path / "neg_out.mp4"
    rr = render_timeline(
        str(neg_timeline_path),
        str(neg_out),
        RenderOptions(video_codec="libx264", audio_codec="aac", overwrite=True),
    )
    _assert_ok(rr, "render_timeline (negative control)")
    assert neg_out.exists(), "ネガティブ対照の出力 mp4 が生成されませんでした"

    neg_vol = _get_mean_volume(neg_out)

    # ネガティブ対照: denoise なし再エンコードだけでは -3.0dB 以上は低下しない
    assert neg_vol >= in_vol - 3.0, (
        f"ネガティブ対照失敗: denoise なし再エンコードで予期外の大幅音量低下。"
        f" in={in_vol:.1f} dB, neg_out={neg_vol:.1f} dB, diff={neg_vol - in_vol:.2f} dB"
    )


# -------------------------------------------------------------------
# テスト: §6.1 本検証（afftdn による -3.0dB 以上の音量低下）
# -------------------------------------------------------------------


@pytest.mark.skipif(
    _FFMPEG_MISSING,
    reason="ffmpeg が見つかりません（CLIPWRIGHT_FFMPEG または PATH が必要）",
)
def test_afftdn_reduces_noise_by_3db(tmp_path: Path) -> None:
    """detect_noise(afftdn) → render_timeline のフルパイプラインで
    出力 mean_volume が入力比 -3.0dB 以上低下することを確認する（§6.1）。

    media / timeline.otio / out.mp4 は同一 tmp_path 直下に配置する（DC-AS-002）。
    """
    from clipwright_render.render import render_timeline
    from clipwright_render.schemas import RenderOptions

    from clipwright_noise.noise import detect_noise
    from clipwright_noise.schemas import DetectNoiseOptions

    fixture = _make_fixture(tmp_path)

    # --- 入力音量の計測 ---
    in_vol = _get_mean_volume(fixture)

    # --- detect_noise で afftdn 指示を timeline に書き込む ---
    timeline_path = tmp_path / "timeline.otio"
    opts = DetectNoiseOptions(backend="afftdn", strength="medium")
    result = detect_noise(str(fixture), str(timeline_path), opts, None)
    _assert_ok(result, "detect_noise")
    assert timeline_path.exists(), "timeline.otio が生成されませんでした"

    # --- render_timeline で afftdn を適用して出力 mp4 を生成 ---
    out_mp4 = tmp_path / "out.mp4"
    render_opts = RenderOptions(
        video_codec="libx264", audio_codec="aac", overwrite=True
    )
    rr = render_timeline(str(timeline_path), str(out_mp4), render_opts)
    _assert_ok(rr, "render_timeline")
    assert out_mp4.exists(), "out.mp4 が生成されませんでした"

    # --- 出力音量の計測 ---
    out_vol = _get_mean_volume(out_mp4)

    # --- 合格条件: out_vol <= in_vol - 3.0 ---
    assert out_vol <= in_vol - 3.0, (
        f"afftdn によるノイズ低減が不十分です。"
        f" in={in_vol:.1f} dB, out={out_vol:.1f} dB, diff={out_vol - in_vol:.2f} dB"
        f" (期待: diff <= -3.0 dB)"
    )
