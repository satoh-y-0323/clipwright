"""test_schemas.py — Validation of __Action__Options.

Contract surface (schemas) targets ~100% coverage (CONVENTIONS §test coverage).
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
    """gt=0 constraint: values <= 0 raise ValidationError (→ INVALID_INPUT at server boundary)."""
    with pytest.raises(ValidationError):
        __Action__Options(example_threshold=0)
