"""conftest.py — clipwright-bgm テスト用共有フィクスチャ。

フィクスチャ一覧:
  - tmp_timeline_dir: tmp_path 配下に timeline/bgm ファイル用 dir を生成
  - bgm_audio_file: 許可拡張子 .mp3 のダミー BGM ファイル
  - timeline_otio_path: .otio ファイルパス（ファイルは存在しない）
  - output_otio_path: 出力 .otio ファイルパス（ファイルは存在しない）
  - simple_timeline: V1/A1 の2トラックのみを持つ OTIO Timeline
  - media_info_bgm: BGM 用 MediaInfo（duration=30.0s・rate=48000・audio ストリームのみ）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

# ---------------------------------------------------------------------------
# ディレクトリ・ファイルパス
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_timeline_dir(tmp_path: Path) -> Path:
    """timeline・bgm ファイルを同一 dir に置くための一時ディレクトリを返す。"""
    d = tmp_path / "project"
    d.mkdir()
    return d


@pytest.fixture
def bgm_audio_file(tmp_timeline_dir: Path) -> Path:
    """許可拡張子 .mp3 のダミー BGM ファイルを生成して返す。"""
    path = tmp_timeline_dir / "bgm.mp3"
    path.write_bytes(b"dummy bgm audio")
    return path


@pytest.fixture
def timeline_otio_path(tmp_timeline_dir: Path) -> Path:
    """入力 .otio タイムラインファイルパス（ファイルは存在しない・書き込み前）。"""
    return tmp_timeline_dir / "timeline.otio"


@pytest.fixture
def output_otio_path(tmp_timeline_dir: Path) -> Path:
    """出力 .otio タイムラインファイルパス（ファイルは存在しない・書き込み前）。"""
    return tmp_timeline_dir / "output.otio"


# ---------------------------------------------------------------------------
# OTIO Timeline
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_timeline() -> otio.schema.Timeline:
    """V1(Video) + A1(Audio) の 2 トラック構成 Timeline を返す。

    add_bgm の正常系テスト入力として使う。クリップは空（再呼び出し検出テストでは
    kind=='bgm' クリップを手動で追加する）。
    """
    tl = otio.schema.Timeline(name="test_timeline")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(v1)
    tl.tracks.append(a1)
    return tl


# ---------------------------------------------------------------------------
# MediaInfo モック値
# ---------------------------------------------------------------------------

BGM_DURATION_SEC = 30.0
BGM_RATE = 48000.0


@pytest.fixture
def media_info_bgm() -> MediaInfo:
    """BGM ファイル用 MediaInfo（audio ストリーム 1 本・30 秒）。

    inspect_media のモック戻り値として使う。
    duration は RationalTimeModel(value=30.0 * 48000, rate=48000) で表現する。
    """
    return MediaInfo(
        path="bgm.mp3",
        container="mp3",
        duration=RationalTimeModel(value=BGM_DURATION_SEC * BGM_RATE, rate=BGM_RATE),
        streams=[
            StreamInfo(
                index=0,
                codec_type="audio",
                codec_name="mp3",
                sample_rate=48000,
                channels=2,
            )
        ],
        bit_rate=320_000,
    )


def make_bgm_timeline(
    timeline_dir: Path,
    bgm_path: Path,
    bgm_duration_sec: float = BGM_DURATION_SEC,
    bgm_rate: float = BGM_RATE,
) -> otio.schema.Timeline:
    """BGM クリップを持つ timeline（add_bgm 後の想定構造）を構築して返すヘルパー。

    実際の add_bgm を呼ばず OTIO を手動組み立てする。
    unit テスト内で「期待する構造」と比較する用途に使う。
    """
    tl = otio.schema.Timeline(name="test_timeline")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    a2 = otio.schema.Track(name="A2", kind=otio.schema.TrackKind.Audio)

    source_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, bgm_rate),
        duration=otio.opentime.RationalTime(bgm_duration_sec * bgm_rate, bgm_rate),
    )
    ref = otio.schema.ExternalReference(target_url=str(bgm_path))
    bgm_metadata: dict[str, Any] = {
        "clipwright": {
            "tool": "clipwright-bgm",
            "version": "0.1.0",
            "kind": "bgm",
            "volume_db": -6.0,
            "fade_in_sec": 0.0,
            "fade_out_sec": 0.0,
            "ducking": {
                "enabled": False,
                "threshold": 0.05,
                "ratio": 4.0,
            },
        }
    }
    bgm_clip = otio.schema.Clip(
        name=bgm_path.name,
        media_reference=ref,
        source_range=source_range,
        metadata=bgm_metadata,
    )
    a2.append(bgm_clip)

    tl.tracks.append(v1)
    tl.tracks.append(a1)
    tl.tracks.append(a2)
    return tl
