"""clipwright-wrap テスト用共有フィクスチャ。

spike-budoux が生成した tests/fixtures/budoux_sample.json をロードし、
実 budoux 文節分割セグメントを使った貪欲行詰めテストの入力として提供する。
フィクスチャは spike 確定済み（全4言語ロード成功・parse() -> list[str] API 確認済み）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def budoux_sample_json() -> dict[str, Any]:
    """spike-budoux が生成した budoux_sample.json を dict として返す。

    ステータス: 確定（spike-budoux で全4言語ロード成功・実 budoux 出力）。
    fixtures/README.md の確定事項を参照すること。
    """
    path = FIXTURES_DIR / "budoux_sample.json"
    with path.open(encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
    return data


@pytest.fixture(scope="session")
def budoux_segments_ja(budoux_sample_json: dict[str, Any]) -> list[list[str]]:
    """budoux_sample.json の日本語セグメント列（4サンプル分）を返す。

    各要素は 1 cue 分の文節トークンリスト（list[str]）。
    - [0]: ["今日は", "いい", "天気です。"]
    - [1]: ["今日は", "とても", "いい", "天気なので", "公園に", "散歩に", "行きました。"]
    - [2]: 長文（9文節）
    - [3]: 英数字混じり（6文節）
    """
    segments: list[list[str]] = [list(s) for s in budoux_sample_json["segments"]]
    return segments
