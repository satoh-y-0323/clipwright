"""test_integration.py — clipwright-render 実バイナリ end-to-end テスト（DC-GP-002）。

本テストはモックを一切使わず、実 ffmpeg/ffprobe バイナリを使って
clipwright_render のオーケストレーション関数を end-to-end で検証する。

カバレッジ上の位置づけ:
  - モックベースの test_render.py では「ffmpeg が正しく実行されて1本の動画が
    生成される」「元素材・OTIO ファイルが不変」という受入条件2/3を検証できない。
  - 本テストは実バイナリを動かすことで、それらをはじめて確認する。

実行条件:
  - env CLIPWRIGHT_FFMPEG / CLIPWRIGHT_FFPROBE が設定済み、または PATH に
    ffmpeg/ffprobe があること。未設定時は pytest.skip する（モックは使わない）。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest

from clipwright_render.render import clipwright_render
from clipwright_render.schemas import RenderOptions

# ===========================================================================
# ヘルパー
# ===========================================================================


def _sha256(path: Path) -> str:
    """ファイルの SHA-256 ハッシュを返す。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _probe_duration(ffprobe: str, path: Path) -> float:
    """ffprobe で動画の duration（秒）を取得する。"""
    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"ffprobe failed: {result.stderr}"
    data: dict[str, Any] = json.loads(result.stdout)
    return float(data["format"]["duration"])


def _make_test_video(ffmpeg: str, output: Path, duration: float = 5.0) -> None:
    """lavfi testsrc + sine を使って短い映像+音声の動画を生成する（CFR・固定解像度）。

    Args:
        ffmpeg: ffmpeg 実行ファイルのパス。
        output: 生成するファイルのパス（.mp4）。
        duration: 動画の尺（秒）。
    """
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration}:size=320x240:rate=25",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duration}",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        "-shortest",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"素材生成に失敗しました: {result.stderr[:300]}"


def _build_two_segment_timeline(
    source_path: Path,
    otio_path: Path,
    clip1_start: float,
    clip1_duration: float,
    clip2_start: float,
    clip2_duration: float,
    rate: float = 25.0,
) -> otio.schema.Timeline:
    """同一ソースを参照する 2 区間の OTIO Timeline を構築して保存し返す。

    Args:
        source_path: ソースメディアファイルのパス。
        otio_path: 保存先の .otio ファイルパス。
        clip1_start: 第1クリップの source_range start（秒）。
        clip1_duration: 第1クリップの duration（秒）。
        clip2_start: 第2クリップの source_range start（秒）。
        clip2_duration: 第2クリップの duration（秒）。
        rate: RationalTime のレート（デフォルト 25 fps）。

    Returns:
        保存した Timeline オブジェクト。
    """
    ref = otio.schema.ExternalReference(target_url=str(source_path))

    def make_clip(start_sec: float, dur_sec: float) -> otio.schema.Clip:
        return otio.schema.Clip(
            name=f"clip_{start_sec}",
            media_reference=ref,
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(start_sec * rate, rate),
                duration=otio.opentime.RationalTime(dur_sec * rate, rate),
            ),
        )

    track = otio.schema.Track(name="video", kind=otio.schema.TrackKind.Video)
    track.append(make_clip(clip1_start, clip1_duration))
    track.append(make_clip(clip2_start, clip2_duration))

    timeline = otio.schema.Timeline(name="integration_test")
    timeline.tracks.append(track)

    otio.adapters.write_to_file(timeline, str(otio_path))
    return timeline


# ===========================================================================
# テスト
# ===========================================================================


@pytest.mark.integration
def test_render_two_segments_produces_single_output(
    tmp_path: Path,
    require_ffmpeg: str,
    require_ffprobe: str,
) -> None:
    """受入2の検証: 実 ffmpeg で 2 区間連結し出力 1 本が生成される。

    モックのみでは未検証の受入条件2（ffmpeg が正しく起動して出力ファイルを生成する）を
    実バイナリで確認する。

    観点:
      - clipwright_render を dry_run=False で実行すると ok=True が返る。
      - 出力ファイルが 1 本だけ生成される（artifacts に記録される）。
      - ffprobe で出力ファイルを読めること（有効な動画ファイルである）。
    """
    source = tmp_path / "source.mp4"
    _make_test_video(require_ffmpeg, source, duration=6.0)

    otio_path = tmp_path / "timeline.otio"
    _build_two_segment_timeline(
        source_path=source,
        otio_path=otio_path,
        clip1_start=0.0,
        clip1_duration=2.0,
        clip2_start=3.0,
        clip2_duration=2.0,
    )

    output = tmp_path / "output.mp4"
    result = clipwright_render(
        timeline=str(otio_path),
        output=str(output),
        options=RenderOptions(),
        dry_run=False,
    )

    assert result["ok"] is True, f"render が失敗しました: {result}"
    assert output.exists(), "出力ファイルが生成されていません"
    assert output.stat().st_size > 0, "出力ファイルのサイズが 0 です"

    # ffprobe で読めることを確認（有効な動画ファイル）
    duration = _probe_duration(require_ffprobe, output)
    assert duration > 0.0, "出力動画の尺が 0 秒です"

    # artifacts に出力パスが記録されている
    artifacts = result.get("artifacts", [])
    assert len(artifacts) == 1, f"artifacts の件数が不正です: {artifacts}"
    assert Path(artifacts[0]["path"]).resolve() == output.resolve()


@pytest.mark.integration
def test_render_output_duration_matches_segments(
    tmp_path: Path,
    require_ffmpeg: str,
    require_ffprobe: str,
) -> None:
    """受入2の検証: 出力尺が 2 区間の Σduration に妥当（許容誤差内）。

    モックのみでは未検証の受入条件2（ffmpeg が実際に正しい尺の動画を生成する）を
    実バイナリで確認する。

    2区間合計 = 2.0 + 2.0 = 4.0 秒。ffprobe で計測した尺との誤差が 0.5 秒以内。
    """
    source = tmp_path / "source.mp4"
    _make_test_video(require_ffmpeg, source, duration=6.0)

    clip1_dur = 2.0
    clip2_dur = 2.0
    expected_total = clip1_dur + clip2_dur

    otio_path = tmp_path / "timeline.otio"
    _build_two_segment_timeline(
        source_path=source,
        otio_path=otio_path,
        clip1_start=0.0,
        clip1_duration=clip1_dur,
        clip2_start=3.0,
        clip2_duration=clip2_dur,
    )

    output = tmp_path / "output.mp4"
    result = clipwright_render(
        timeline=str(otio_path),
        output=str(output),
        options=RenderOptions(),
        dry_run=False,
    )

    assert result["ok"] is True, f"render が失敗しました: {result}"

    actual_duration = _probe_duration(require_ffprobe, output)
    tolerance = 0.5  # エンコーダーの GOP 境界による誤差を許容
    assert abs(actual_duration - expected_total) <= tolerance, (
        f"出力尺が期待範囲外です: actual={actual_duration:.3f}s,"
        f" expected≈{expected_total}s, tolerance=±{tolerance}s"
    )

    # ok_result の total_duration_seconds と一致することも確認
    reported_duration = result["data"]["total_duration_seconds"]
    assert abs(reported_duration - expected_total) <= 0.001, (
        f"報告された尺が不正です: "
        f"reported={reported_duration}, expected={expected_total}"
    )


@pytest.mark.integration
def test_render_preserves_source_and_otio(
    tmp_path: Path,
    require_ffmpeg: str,
    require_ffprobe: str,  # noqa: ARG001
) -> None:
    """受入3の検証: render 実行前後で元素材・OTIO ファイルが不変（非破壊）。

    モックのみでは未検証の受入条件3（元素材・OTIO ファイルが書き換えられない）を
    実バイナリで確認する。

    render 実行前後のファイルサイズと SHA-256 ハッシュが一致することを確認する。
    """
    source = tmp_path / "source.mp4"
    _make_test_video(require_ffmpeg, source, duration=5.0)

    otio_path = tmp_path / "timeline.otio"
    _build_two_segment_timeline(
        source_path=source,
        otio_path=otio_path,
        clip1_start=0.0,
        clip1_duration=1.5,
        clip2_start=2.0,
        clip2_duration=2.0,
    )

    # render 前の状態を記録
    source_size_before = source.stat().st_size
    source_hash_before = _sha256(source)
    otio_size_before = otio_path.stat().st_size
    otio_hash_before = _sha256(otio_path)

    output = tmp_path / "output.mp4"
    result = clipwright_render(
        timeline=str(otio_path),
        output=str(output),
        options=RenderOptions(),
        dry_run=False,
    )

    assert result["ok"] is True, f"render が失敗しました: {result}"

    # render 後の状態を検証
    assert source.stat().st_size == source_size_before, "元素材のサイズが変化しました"
    assert _sha256(source) == source_hash_before, "元素材のハッシュが変化しました"
    assert otio_path.stat().st_size == otio_size_before, (
        "OTIO ファイルのサイズが変化しました"
    )
    assert _sha256(otio_path) == otio_hash_before, (
        "OTIO ファイルのハッシュが変化しました"
    )

    # 出力は新規生成で元素材と別ファイル
    assert output.resolve() != source.resolve(), "出力と元素材が同一パスです"
    assert output.exists(), "出力ファイルが生成されていません"
