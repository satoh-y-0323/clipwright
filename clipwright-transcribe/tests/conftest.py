"""clipwright-transcribe テスト用共有フィクスチャ。

spike-whisper が生成した tests/fixtures/whisper_sample.json をロードし、
normalize_segments テストの土台として提供する。
フィクスチャが「仮説」か「確定」かは fixtures/README.md を参照すること。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def whisper_sample_json() -> dict[str, Any]:
    """spike-whisper が生成した whisper_sample.json を dict として返す。

    ステータス: 仮説（CLIPWRIGHT_WHISPER env 未設定のため実バイナリ未確認）。
    fixtures/README.md の「確定/仮説」の別を参照すること。
    """
    path = FIXTURES_DIR / "whisper_sample.json"
    with path.open(encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
    return data
