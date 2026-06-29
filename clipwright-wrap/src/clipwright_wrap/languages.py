"""languages.py — canonical language-class sets for caption wrapping.

Single source of truth shared by schemas.py (pattern), wrap.py (segmentation
routing and joiner selection). Adding a language here updates the MCP input
schema and the runtime routing together (no drift).
"""

from __future__ import annotations

# CJK / Thai: budoux phrase-boundary segmentation (subprocess). joiner="".
CJK_LANGUAGES: tuple[str, ...] = ("ja", "zh-hans", "zh-hant", "th")

# Space-delimited Latin-script: whitespace word split (in-process). joiner=" ".
SPACE_DELIMITED_LANGUAGES: tuple[str, ...] = ("en", "es", "fr", "de", "it", "pt", "nl")

# Regex alternation for the MCP input-schema pattern. No bare "zh", so the
# alternation has no prefix-collision; longest token is "zh-hant" (7 chars).
LANGUAGE_PATTERN: str = (
    "^(" + "|".join(CJK_LANGUAGES + SPACE_DELIMITED_LANGUAGES) + ")$"
)


def is_cjk(language: str) -> bool:
    """True when *language* uses budoux phrase-boundary segmentation."""
    return language in CJK_LANGUAGES


def is_space_delimited(language: str) -> bool:
    """True when *language* uses whitespace word segmentation (joiner=' ')."""
    return language in SPACE_DELIMITED_LANGUAGES
