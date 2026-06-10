"""test___TOOL__.py — オーケストレーション層 __ACTION__ のテスト。

ハッピーパス1つと代表的なエラー（FILE_NOT_FOUND / 非破壊 / 拡張子）を検証する。
実ツールでは検出ロジック・OSS subprocess 境界のテストを追加する。
"""

from __future__ import annotations

import json
from pathlib import Path

from clipwright___TOOL__.__TOOL__ import __ACTION__
from clipwright___TOOL__.schemas import __Action__Options


def test_happy_path_writes_artifact(sample_input: Path, tmp_path: Path) -> None:
    output = tmp_path / "out.json"
    result = __ACTION__(
        input=str(sample_input),
        output=str(output),
        options=__Action__Options(),
    )
    assert result["ok"] is True
    assert output.exists()
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["input"] == sample_input.name
    assert result["artifacts"][0]["format"] == "json"


def test_missing_input_returns_file_not_found(tmp_path: Path) -> None:
    output = tmp_path / "out.json"
    result = __ACTION__(
        input=str(tmp_path / "nope.txt"),
        output=str(output),
        options=__Action__Options(),
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "FILE_NOT_FOUND"


def test_output_equals_input_rejected(sample_input: Path) -> None:
    """非破壊（M5）: output == input は INVALID_INPUT で拒否する。"""
    result = __ACTION__(
        input=str(sample_input),
        output=str(sample_input),
        options=__Action__Options(),
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"


def test_non_json_output_rejected(sample_input: Path, tmp_path: Path) -> None:
    result = __ACTION__(
        input=str(sample_input),
        output=str(tmp_path / "out.txt"),
        options=__Action__Options(),
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_INPUT"
