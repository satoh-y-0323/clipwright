"""clipwright-__TOOL__ テスト用共有フィクスチャ。

雛形では一時入力ファイルを生成する最小フィクスチャだけを提供する。
実ツールでは fixtures/ に小さな実素材を置き、ここでロードする。
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def sample_input(tmp_path: Path) -> Path:
    """テスト用のダミー入力ファイルを生成して返す。"""
    path = tmp_path / "input.txt"
    path.write_text("sample", encoding="utf-8")
    return path
