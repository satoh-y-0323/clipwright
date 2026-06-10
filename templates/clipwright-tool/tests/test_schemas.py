"""test_schemas.py — __Action__Options の検証。

契約面（schemas）は実質 100% を目標にカバーする（CONVENTIONS §テストカバレッジ）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from clipwright___TOOL__.schemas import __Action__Options


def test_defaults() -> None:
    opts = __Action__Options()
    assert opts.example_threshold == 0.5


def test_accepts_valid_value() -> None:
    opts = __Action__Options(example_threshold=1.5)
    assert opts.example_threshold == 1.5


def test_rejects_non_positive() -> None:
    """gt=0 制約: 0 以下は ValidationError（→ server 境界で INVALID_INPUT）。"""
    with pytest.raises(ValidationError):
        __Action__Options(example_threshold=0)
