"""共有 pytest フィクスチャ。

ffmpeg/ffprobe の探索順序:
  1. PATH (shutil.which)
  2. 環境変数 CLIPWRIGHT_FFPROBE / CLIPWRIGHT_FFMPEG

いずれも見つからない場合のみ統合テストを skip する。[DC-AM-006]
CLIPWRIGHT_FFMPEG はテスト専用。ランタイム (media.py) は ffprobe のみ使用。[DC-AM-008]
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _find_binary(name: str, env_var: str) -> str | None:
    """バイナリを PATH → env_var の順で探す。どちらも見つからなければ None。"""
    found = shutil.which(name)
    if found:
        return found
    env_val = os.environ.get(env_var)
    if env_val and Path(env_val).is_file():
        return env_val
    return None


@pytest.fixture(scope="session")
def ffprobe_path() -> str | None:
    """ffprobe 実行ファイルのパス。見つからなければ None。"""
    return _find_binary("ffprobe", "CLIPWRIGHT_FFPROBE")


@pytest.fixture(scope="session")
def ffmpeg_path() -> str | None:
    """ffmpeg 実行ファイルのパス（テスト素材生成専用）。見つからなければ None。"""
    return _find_binary("ffmpeg", "CLIPWRIGHT_FFMPEG")


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """一時ディレクトリを返すフィクスチャ。pytest の tmp_path で自動クリーンアップ。"""
    return tmp_path


@pytest.fixture(scope="session")
def sample_media(
    ffmpeg_path: str | None,
    tmp_path_factory: pytest.TempPathFactory,
) -> str:
    """ffmpeg lavfi (testsrc + sine) で 3 秒のテスト mp4 を生成して返す。

    ffmpeg が一切見つからない場合のみ pytest.skip する（[DC-AM-006]）。
    """
    if ffmpeg_path is None:
        pytest.skip(
            "ffmpeg が見つかりません。"
            "winget install Gyan.FFmpeg で導入するか "
            "CLIPWRIGHT_FFMPEG 環境変数にフルパスを設定してください。"
        )

    out_dir = tmp_path_factory.mktemp("media")
    out_path = str(out_dir / "test_3sec.mp4")

    cmd = [
        ffmpeg_path,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=3:size=320x240:rate=30",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=3",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-shortest",
        out_path,
    ]
    result = subprocess.run(
        cmd,
        shell=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.fail(
            f"テスト素材の生成に失敗しました。\ncmd: {cmd}\nstderr: {result.stderr}"
        )
    return out_path
