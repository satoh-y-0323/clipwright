"""clipwright-noise テスト用共有フィクスチャ。"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_media(tmp_path: Path) -> Path:
    """テスト用のダミーメディアファイルを生成して返す（mp4 拡張子のスタブ）。"""
    path = tmp_path / "video.mp4"
    path.write_bytes(b"dummy media")
    return path
