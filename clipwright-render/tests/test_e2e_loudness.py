"""test_e2e_loudness.py — loudnorm/peak の実 e2e テスト（task_id: e2e-loudnorm）。

設計根拠:
  - architecture-report-20260611-114314 §4・§9
  - ADR-L1: loudnorm linear 二段適用（detect が measured_* を取得 → render が linear=true で適用）
  - ADR-L2: peak は volumedetect で max_volume を取り volume フィルタで差分ゲインを当てる
  - DC-GP-001: フィクスチャはピンクノイズ（sine は LRA が極端値になり assert 不安定）
  - DC-AM-002: peak + denoise 併用の厳密 e2e はスコープ外（denoise なし peak のみ）
  - B-3（noise の教訓）: ネガティブ対照 — loudness 指示なしでは目標へ寄らないことを assert

テスト構成:
  1. フィクスチャ生成 (ピンクノイズ動画、約 -35 LUFS / max -21 dB)
  2. loudnorm e2e: detect_loudness → render_timeline → 出力を ebur128/loudnorm で再測定
     - assert: 出力ラウドネスが目標 I=-14 LUFS の ±2 LU 以内
  3. ネガティブ対照: loudness 指示なし render の出力が目標から外れることを assert
  4. peak e2e: detect_loudness(mode=peak) → render_timeline → volumedetect で再測定
     - assert: 出力 max_volume が目標 peak_db=-1.0 dBの ±1.5 dB 以内
  5. render 拡張反映の最小 assert: loudness 指示 timeline で render が ok=True を返す

実行方法（ffmpeg 不在時は skip）:
  uv run --package clipwright-render pytest -k e2e_loudness

ffmpeg を PATH に通すか CLIPWRIGHT_FFMPEG 環境変数で指定すること。
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
from clipwright.otio_utils import get_clipwright_metadata, set_clipwright_metadata

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

# ===========================================================================
# ヘルパー: ピンクノイズフィクスチャ生成
# ===========================================================================

_DURATION_SEC = 5.0
_RATE = 25.0
_PINK_AMPLITUDE = 0.1  # 約 -35 LUFS になるよう調整済み（実機確認済み）
# e2e テスト全体の subprocess タイムアウト秒数（CR-T-001 定数化）。
# CI 環境変数 E2E_TIMEOUT_SEC で上書き可能にすることで CI チューニングの道筋を確保する。
_E2E_TIMEOUT: int = int(os.environ.get("E2E_TIMEOUT_SEC", "120"))
# ネガティブ対照の前提条件: 入力 LUFS がこれより低いこと（L-5 pre-condition）。
# フィクスチャの振幅 0.1 が想定通り約 -35 LUFS 付近に収まることを保証する。
_PRE_CONDITION_MAX_LUFS = -25.0


def _make_pink_noise_video(
    ffmpeg: str, output: Path, duration: float = _DURATION_SEC
) -> None:
    """ピンクノイズ＋testsrc の動画を生成する（DC-GP-001）。

    ピンクノイズは LRA が安定しており loudnorm の収束 assert に適している。
    sine 単音は LRA が極端値（0.00 または inf）になり assert が不安定になるため不可。
    振幅 0.1 で約 -35 LUFS / max_volume 約 -21 dB になる（実機確認済み）。
    """
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=320x240:rate=15:duration={duration}",
        "-f",
        "lavfi",
        "-i",
        f"anoisesrc=color=pink:amplitude={_PINK_AMPLITUDE}:duration={duration}",
        "-t",
        str(duration),
        "-shortest",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    # e2e フィクスチャ/測定用ヘルパー専用: process.run の代わりに直接呼び出しを許容（MEMORY.md 承認済み例外）
    result = subprocess.run(
        cmd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_E2E_TIMEOUT,
    )
    assert result.returncode == 0, (
        f"フィクスチャ生成に失敗しました: {result.stderr[:400]}"
    )


def _measure_integrated_loudness(ffmpeg: str, media: Path) -> float:
    """loudnorm print_format=json で統合ラウドネス（input_i）を測定して返す（LUFS）。"""
    cmd = [
        ffmpeg,
        "-i",
        str(media),
        "-af",
        "loudnorm=I=-14:TP=-1:LRA=11:print_format=json",
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
    assert result.returncode == 0, f"loudnorm 測定に失敗しました: {result.stderr[:400]}"
    m = re.search(r'"input_i"\s*:\s*"([-0-9.]+)"', result.stderr)
    assert m is not None, (
        f"loudnorm JSON の input_i が見つかりません:\n{result.stderr[-600:]}"
    )
    return float(m.group(1))


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


def _measure_loudnorm_all(ffmpeg: str, media: Path) -> dict[str, float]:
    """loudnorm print_format=json の数値フィールドを返す。

    loudnorm JSON には 'normalization_type' のような文字列フィールドが混在するため、
    float 変換可能なフィールドのみ抽出する（-inf/inf は ValidationError 相当で除外）。
    必要な 5 フィールド（input_i/input_tp/input_lra/input_thresh/target_offset）が
    すべて存在することも確認する。
    """
    cmd = [
        ffmpeg,
        "-i",
        str(media),
        "-af",
        "loudnorm=I=-14:TP=-1:LRA=11:print_format=json",
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
    assert result.returncode == 0, f"loudnorm 測定に失敗しました: {result.stderr[:400]}"
    # analyze.py H-1 修正と同パターン: re.search は stderr 先頭の {} ブロックにマッチするリスクがある。
    # re.findall で全候補を収集し、末尾から required_keys を持つブロックを採用する。
    # ffmpeg は loudnorm JSON を stderr 末尾に出力するが、バージョンによって先行する {} が混在しうる。
    required_keys = [
        "input_i",
        "input_tp",
        "input_lra",
        "input_thresh",
        "target_offset",
    ]
    candidates = re.findall(r"\{[^{}]+\}", result.stderr, re.DOTALL)
    raw: dict[str, Any] | None = None
    for block in reversed(candidates):
        try:
            parsed: dict[str, Any] = json.loads(block)
        except json.JSONDecodeError:
            continue
        if all(k in parsed for k in required_keys):
            raw = parsed
            break
    assert raw is not None, f"loudnorm JSON が見つかりません:\n{result.stderr[-600:]}"
    out: dict[str, float] = {}
    for k, v in raw.items():
        try:  # noqa: SIM105
            out[k] = float(v)
        except (ValueError, TypeError):
            pass  # "dynamic" などの文字列フィールドは無視
    for key in required_keys:
        assert key in out, f"loudnorm JSON に必須フィールド '{key}' がありません: {raw}"
    return out


# ===========================================================================
# ヘルパー: OTIO タイムライン構築
# ===========================================================================


def _make_single_clip_timeline(
    source_path: Path,
    duration_sec: float = _DURATION_SEC,
    rate: float = _RATE,
) -> otio.schema.Timeline:
    """単一クリップ（ソース全体）の OTIO タイムラインを生成する。"""
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
    timeline = otio.schema.Timeline(name="e2e_test")
    timeline.tracks.append(track)
    return timeline


def _set_loudness_directive(
    timeline: otio.schema.Timeline, directive: dict[str, Any]
) -> None:
    """timeline-level metadata に loudness 指示を書き込む。"""
    meta = get_clipwright_metadata(timeline)
    meta["loudness"] = directive
    set_clipwright_metadata(timeline, meta)


# ===========================================================================
# テスト
# ===========================================================================


@requires_ffmpeg
class TestLoudnormE2E:
    """loudnorm モードの実 e2e テスト（フィクスチャ → detect → render → 再測定）。"""

    def test_loudnorm_converges_to_target_i(self, tmp_path: Path) -> None:
        """loudnorm 適用後の統合ラウドネスが目標 I=-14 LUFS の ±2 LU 以内に収束する（ADR-L1）。

        前提: ピンクノイズフィクスチャは約 -35 LUFS（目標から約 21 LU 離れた素材）。
        loudnorm linear=true による二段適用で目標へ寄ることを実測で確認する。
        許容幅 ±2 LU は実機確認から確定（実測差 ~0.66 LU）。
        """
        assert _FFMPEG is not None  # skipif で保証済みだが型チェック用
        source = tmp_path / "source.mp4"
        _make_pink_noise_video(_FFMPEG, source)

        # 入力ラウドネスを測定
        input_i = _measure_integrated_loudness(_FFMPEG, source)

        # loudnorm 測定（detect フェーズ）
        measured_raw = _measure_loudnorm_all(_FFMPEG, source)

        # LoudnessDirective を構築して timeline に書き込む
        directive: dict[str, Any] = {
            "tool": "clipwright-loudness",
            "version": "0.1.0",
            "kind": "loudness",
            "mode": "loudnorm",
            "scope": "track",
            "target": {"i": -14.0, "tp": -1.0, "lra": 11.0},
            "measured": {
                "input_i": measured_raw["input_i"],
                "input_tp": measured_raw["input_tp"],
                "input_lra": measured_raw["input_lra"],
                "input_thresh": measured_raw["input_thresh"],
                "target_offset": measured_raw["target_offset"],
            },
        }

        timeline = _make_single_clip_timeline(source)
        _set_loudness_directive(timeline, directive)
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        # render（同一 dir 制約: source / timeline.otio / out.mp4 すべて tmp_path 直下）
        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render が失敗しました: {result}"
        assert out_path.exists(), "出力ファイルが生成されていません"

        # 出力ラウドネスを再測定
        output_i = _measure_integrated_loudness(_FFMPEG, out_path)

        target_i = -14.0
        tolerance = 2.0  # ±2 LU（実機確認: 差 ~0.66 LU）
        diff = abs(output_i - target_i)

        assert diff <= tolerance, (
            f"loudnorm 収束が許容幅を超えています:\n"
            f"  入力ラウドネス: {input_i:.2f} LUFS\n"
            f"  出力ラウドネス: {output_i:.2f} LUFS\n"
            f"  目標: {target_i} LUFS\n"
            f"  差分: {diff:.2f} LU（許容: ±{tolerance} LU）"
        )

    def test_render_with_loudnorm_returns_ok(self, tmp_path: Path) -> None:
        """loudness 指示付き timeline で render が UNSUPPORTED にならず ok=True を返す（最小 assert）。"""
        assert _FFMPEG is not None
        source = tmp_path / "source.mp4"
        _make_pink_noise_video(_FFMPEG, source)

        measured_raw = _measure_loudnorm_all(_FFMPEG, source)
        directive: dict[str, Any] = {
            "tool": "clipwright-loudness",
            "version": "0.1.0",
            "kind": "loudness",
            "mode": "loudnorm",
            "scope": "track",
            "target": {"i": -14.0, "tp": -1.0, "lra": 11.0},
            "measured": {
                "input_i": measured_raw["input_i"],
                "input_tp": measured_raw["input_tp"],
                "input_lra": measured_raw["input_lra"],
                "input_thresh": measured_raw["input_thresh"],
                "target_offset": measured_raw["target_offset"],
            },
        }

        timeline = _make_single_clip_timeline(source)
        _set_loudness_directive(timeline, directive)
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )

        assert result["ok"] is True, (
            f"loudness 指示付き timeline の render が失敗しました: {result.get('error')}"
        )
        assert out_path.exists(), "出力ファイルが生成されていません"
        assert out_path.stat().st_size > 0, "出力ファイルのサイズが 0 です"


@requires_ffmpeg
class TestLoudnormNegativeControl:
    """ネガティブ対照: loudness 指示なし render の出力は目標ラウドネスへ寄らない（B-3 教訓）。

    loudnorm 効果が loudness 指示によるものであることを切り分けるために必要。
    loudness なし → render しても入力ラウドネスのまま（目標から大きく外れる）。
    """

    def test_no_loudness_directive_does_not_converge_to_target(
        self, tmp_path: Path
    ) -> None:
        """loudness 指示なし render では出力ラウドネスが目標 I=-14 から外れたまま（対照実験）。

        入力が -35 LUFS 程度のときは出力も同程度（±5 LU 以上は外れている）ことを確認。
        これにより test_loudnorm_converges_to_target_i の効果が loudnorm 指示によるものと確認できる。
        """
        assert _FFMPEG is not None
        source = tmp_path / "source.mp4"
        _make_pink_noise_video(_FFMPEG, source)

        # 入力ラウドネスを確認（-35 LUFS 程度のはず）
        input_i = _measure_integrated_loudness(_FFMPEG, source)

        # pre-condition: フィクスチャが十分に低い LUFS であることを確認する（L-5）。
        # _PINK_AMPLITUDE=0.1 が意図通り約 -35 LUFS に収まっているかを早期検出する。
        # ffmpeg バージョン変更等でフィクスチャの LUFS が変わった場合にこの assert が失敗し
        # ネガティブ対照の前提が崩れていることをすぐに検知できる。
        assert input_i <= _PRE_CONDITION_MAX_LUFS, (
            f"フィクスチャの入力 LUFS が想定より高すぎます（pre-condition 失敗）:\n"
            f"  入力 LUFS: {input_i:.2f} LUFS\n"
            f"  期待: {_PRE_CONDITION_MAX_LUFS} LUFS 以下\n"
            f"  _PINK_AMPLITUDE={_PINK_AMPLITUDE} を調整してください。"
        )

        # loudness 指示なしで timeline を作成
        timeline = _make_single_clip_timeline(source)
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out_no_loudness.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"render が失敗しました: {result}"

        # 出力ラウドネスを測定
        output_i = _measure_integrated_loudness(_FFMPEG, out_path)

        target_i = -14.0
        diff = abs(output_i - target_i)

        # 目標へ寄っていないこと（差が 5 LU 以上）を確認
        # 入力 ~-35 LUFS なので loudnorm なし render では同程度になるはず（差 ~21 LU）
        min_expected_diff = 5.0
        assert diff >= min_expected_diff, (
            f"loudness 指示なし render が意図せず目標へ収束しています（ネガティブ対照失敗）:\n"
            f"  入力ラウドネス: {input_i:.2f} LUFS\n"
            f"  出力ラウドネス: {output_i:.2f} LUFS\n"
            f"  目標: {target_i} LUFS\n"
            f"  差分: {diff:.2f} LU（期待: >{min_expected_diff} LU）"
        )


@requires_ffmpeg
class TestPeakE2E:
    """peak モードの実 e2e テスト（denoise なし・DC-AM-002）。"""

    def test_peak_max_volume_converges_to_target(self, tmp_path: Path) -> None:
        """peak 適用後の max_volume が目標 peak_db=-1.0 dB の ±1.5 dB 以内に収束する（ADR-L2）。

        detect_loudness の代わりにここで volumedetect を直接実行して measured を得る（単一 e2e）。
        denoise なし + peak の組み合わせのみ（DC-AM-002: peak + denoise 併用の厳密 assert はしない）。
        """
        assert _FFMPEG is not None
        source = tmp_path / "source.mp4"
        _make_pink_noise_video(_FFMPEG, source)

        # max_volume を測定（peak detect フェーズ）
        max_volume_db_before = _measure_max_volume(_FFMPEG, source)

        target_peak_db = -1.0
        directive: dict[str, Any] = {
            "tool": "clipwright-loudness",
            "version": "0.1.0",
            "kind": "loudness",
            "mode": "peak",
            "scope": "track",
            "target": {"peak_db": target_peak_db},
            "measured": {"max_volume_db": max_volume_db_before},
        }

        timeline = _make_single_clip_timeline(source)
        _set_loudness_directive(timeline, directive)
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out_peak.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )
        assert result["ok"] is True, f"peak render が失敗しました: {result}"
        assert out_path.exists(), "出力ファイルが生成されていません"

        # 出力の max_volume を再測定
        max_volume_db_after = _measure_max_volume(_FFMPEG, out_path)

        tolerance = 1.5  # ±1.5 dB（実機確認: 差 ~0.3 dB）
        diff = abs(max_volume_db_after - target_peak_db)

        assert diff <= tolerance, (
            f"peak 収束が許容幅を超えています:\n"
            f"  入力 max_volume: {max_volume_db_before:.1f} dB\n"
            f"  出力 max_volume: {max_volume_db_after:.1f} dB\n"
            f"  目標 peak_db: {target_peak_db} dB\n"
            f"  差分: {diff:.2f} dB（許容: ±{tolerance} dB）"
        )

    def test_peak_render_returns_ok(self, tmp_path: Path) -> None:
        """peak 指示付き timeline で render が ok=True を返す（最小 assert）。"""
        assert _FFMPEG is not None
        source = tmp_path / "source.mp4"
        _make_pink_noise_video(_FFMPEG, source)

        max_volume_db = _measure_max_volume(_FFMPEG, source)

        directive: dict[str, Any] = {
            "tool": "clipwright-loudness",
            "version": "0.1.0",
            "kind": "loudness",
            "mode": "peak",
            "scope": "track",
            "target": {"peak_db": -1.0},
            "measured": {"max_volume_db": max_volume_db},
        }

        timeline = _make_single_clip_timeline(source)
        _set_loudness_directive(timeline, directive)
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(timeline, str(timeline_path))

        out_path = tmp_path / "out_peak.mp4"
        result = render_timeline(
            str(timeline_path), str(out_path), RenderOptions(), dry_run=False
        )

        assert result["ok"] is True, (
            f"peak 指示付き timeline の render が失敗しました: {result.get('error')}"
        )
