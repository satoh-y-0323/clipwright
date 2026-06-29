"""schemas.py — clipwright-transcribe-specific Pydantic schemas.

Common types (MediaRef / Artifact / ToolResult, etc.) are defined in clipwright.schemas;
this module does not redefine them.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class TranscribeOptions(BaseModel):
    """Options for clipwright_transcribe (TR-AD-06).

    language: whisper language code (None = auto-detect). model_path: path to the ggml
    model file (None falls back to env CLIPWRIGHT_WHISPER_MODEL).
    initial_prompt: context hint to improve whisper recognition accuracy.

    extra="forbid" rejects unknown fields so that AI-side typos raise an explicit
    validation error instead of being silently ignored (SR L-1: explicit allowlist).
    """

    model_config = ConfigDict(extra="forbid")

    language: Annotated[
        str | None,
        Field(
            default=None,
            max_length=10,
            pattern=r"^[a-zA-Z]{2,}$|^auto$",
            description=(
                'Transcription language code (e.g. "ja", "en"). '
                "None (default) lets whisper auto-detect the language. "
                "ISO 639-1 compatible: 2 or more ASCII letters, or 'auto'. "
                "Anything else is rejected."
            ),
        ),
    ] = None

    model_path: Annotated[
        str | None,
        Field(
            default=None,
            max_length=4096,
            description=(
                "Path to the whisper.cpp ggml model file"
                " (max 4096 chars, equivalent to OS path length limit)."
                " None (default) uses the CLIPWRIGHT_WHISPER_MODEL"
                " environment variable."
                " If neither is set or the file does not exist, an error is raised."
            ),
        ),
    ] = None

    initial_prompt: Annotated[
        str | None,
        Field(
            default=None,
            max_length=2048,
            description=(
                "Context hint passed to whisper (proper nouns, technical terms, etc.)."
                " None (default) means no prompt. Used to tune recognition accuracy."
                " Maximum 2048 characters (equivalent to whisper.cpp context length)."
            ),
        ),
    ] = None

    word_timestamps: Annotated[
        bool,
        Field(
            default=False,
            description=(
                "When True, extract per-word timing from whisper -ojf tokens[] and emit"
                " a <stem>.words.vtt artifact with inline WebVTT timestamps (ADR-K2 /"
                " F-T-01). Default False preserves the existing segment output exactly"
                " (AC-2). The whisper command is identical for False and True;"
                " -ojf is used unconditionally so the segment JSON is unchanged."
            ),
        ),
    ] = False
