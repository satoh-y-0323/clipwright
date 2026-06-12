"""test_template_subprocess_message.py — Red test for round-4 SR I-1 [SR-NEW]
template fix.

Asserts that the template's module-level ``_SUBPROCESS_SAFE_MESSAGE`` literal value
equals the canonical core constant ``SUBPROCESS_SAFE_MESSAGE`` from
``clipwright.process``.

Design:
  - The template file (templates/clipwright-tool/src/clipwright___TOOL__/__TOOL__.py)
    is a self-contained scaffold that MUST NOT import core (spec §7). Therefore
    it keeps its own local copy of the constant.
  - This test reads the template as plain text, extracts the assigned literal via
    regex, and asserts the extracted value equals the imported core constant.
  - Red today because the template has ``_SUBPROCESS_SAFE_MESSAGE = "Internal subprocess
    failed"`` (capital "I"), while core has ``"internal subprocess failed"``
    (lowercase).
  - Do NOT import the template module: the path contains placeholder tokens
    (``__TOOL__``) that are not importable.

Red classification: deterministic-Red (string value mismatch, capital vs lowercase "i").
"""

from __future__ import annotations

import re
from pathlib import Path

from clipwright.process import SUBPROCESS_SAFE_MESSAGE

# Resolve the template file path relative to the repository root.
# __file__ is tests/test_template_subprocess_message.py; repo root is one level up.
_REPO_ROOT = Path(__file__).parent.parent
_TEMPLATE_PATH = (
    _REPO_ROOT
    / "templates"
    / "clipwright-tool"
    / "src"
    / "clipwright___TOOL__"
    / "__TOOL__.py"
)

# Regex to match: _SUBPROCESS_SAFE_MESSAGE = "some value"
# Captures the string literal between the double quotes.
_ASSIGNMENT_RE = re.compile(
    r'^_SUBPROCESS_SAFE_MESSAGE\s*=\s*"([^"]*)"',
    re.MULTILINE,
)


def _extract_template_message_value() -> str:
    """Read the template source and extract the _SUBPROCESS_SAFE_MESSAGE literal.

    Reads the template as UTF-8 text and applies a regex to find the module-level
    assignment. Raises AssertionError if the assignment line is not found.

    Returns:
        The string literal value assigned to ``_SUBPROCESS_SAFE_MESSAGE`` in the
        template source.
    """
    source = _TEMPLATE_PATH.read_text(encoding="utf-8")
    match = _ASSIGNMENT_RE.search(source)
    assert match is not None, (
        f"Could not find '_SUBPROCESS_SAFE_MESSAGE = \"...\"' in {_TEMPLATE_PATH}. "
        "The template source may have changed; update the regex in this test."
    )
    return match.group(1)


class TestTemplateSubprocessSafeMessage:
    """Assert the template's _SUBPROCESS_SAFE_MESSAGE literal matches the core
    constant."""

    def test_template_path_exists(self) -> None:
        """The template source file must be readable at the expected path.

        Arrange: build path from repo root (deterministic, not cwd-dependent).
        Act:     check existence.
        Assert:  file exists.
        """
        assert _TEMPLATE_PATH.exists(), (
            f"Template file not found at {_TEMPLATE_PATH}. "
            "If the template was moved, update _TEMPLATE_PATH in this test."
        )

    def test_template_message_assignment_present(self) -> None:
        """The template source must contain a _SUBPROCESS_SAFE_MESSAGE assignment.

        Arrange: read template source text.
        Act:     apply assignment regex.
        Assert:  a match is found (the constant is defined in the template).
        """
        source = _TEMPLATE_PATH.read_text(encoding="utf-8")
        match = _ASSIGNMENT_RE.search(source)
        assert match is not None, (
            "No '_SUBPROCESS_SAFE_MESSAGE = \"...\"' assignment found in template. "
            "The assignment must be present (self-contained per spec §7)."
        )

    def test_template_message_equals_core_constant(self) -> None:
        """Template _SUBPROCESS_SAFE_MESSAGE literal must equal core
        SUBPROCESS_SAFE_MESSAGE.

        This is the cross-package matching-key assertion (SR I-1 [SR-NEW]).
        The template must stay self-contained (no core import), but its local
        constant value must match the canonical core value to prevent divergence.

        RED TODAY because: template has "Internal subprocess failed" (capital "I"),
        but core SUBPROCESS_SAFE_MESSAGE == "internal subprocess failed" (lowercase).

        Arrange: read the template literal via regex; import core constant.
        Act:     compare string values directly.
        Assert:  template_value == SUBPROCESS_SAFE_MESSAGE.
        """
        template_value = _extract_template_message_value()
        assert template_value == SUBPROCESS_SAFE_MESSAGE, (
            f"Template _SUBPROCESS_SAFE_MESSAGE = {template_value!r} "
            f"does not match core SUBPROCESS_SAFE_MESSAGE = "
            f"{SUBPROCESS_SAFE_MESSAGE!r}. "
            "Fix the template value to match core (lowercase 'i')."
        )
