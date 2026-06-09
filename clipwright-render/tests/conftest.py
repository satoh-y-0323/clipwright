"""clipwright-render テスト用共有フィクスチャ。

ffmpeg/ffprobe の探索順序:
  1. PATH (shutil.which)
  2. 環境変数 CLIPWRIGHT_FFMPEG / CLIPWRIGHT_FFPROBE

いずれも見つからない場合のみ integration テストを skip する。
core の conftest.py と同じ解決方針を維持する（DC-GP-002）。
"""

from __future__ import annotations

import os
import shutil
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
    """ffmpeg 実行ファイルのパス。見つからなければ None。"""
    return _find_binary("ffmpeg", "CLIPWRIGHT_FFMPEG")


@pytest.fixture
def require_ffprobe(ffprobe_path: str | None) -> str:
    """integration テスト用: ffprobe がなければ skip する。パスを返す。"""
    if ffprobe_path is None:
        pytest.skip(
            "ffprobe が見つかりません。"
            "PATH に ffprobe を追加するか "
            "CLIPWRIGHT_FFPROBE 環境変数にフルパスを設定してください。"
        )
    return ffprobe_path


@pytest.fixture
def require_ffmpeg(ffmpeg_path: str | None) -> str:
    """integration テスト用: ffmpeg がなければ skip する。パスを返す。"""
    if ffmpeg_path is None:
        pytest.skip(
            "ffmpeg が見つかりません。"
            "PATH に ffmpeg を追加するか "
            "CLIPWRIGHT_FFMPEG 環境変数にフルパスを設定してください。"
        )
    return ffmpeg_path
