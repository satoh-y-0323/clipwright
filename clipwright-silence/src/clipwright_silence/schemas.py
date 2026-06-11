"""schemas.py — clipwright-silence specific Pydantic schemas.

Common types (MediaRef / Artifact / ToolResult, etc.) are centrally defined
in clipwright.schemas and are not redefined in this module.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class DetectSilenceOptions(BaseModel):
    """Options for clipwright_detect_silence (AD-2/AD-3, DC-AM-001).

    silence_threshold_db and min_silence_duration are detection parameters
    passed directly to the ffmpeg silencedetect filter.
    padding and min_keep_duration are post-processing parameters used by
    the KEEP derivation logic in plan.py.
    vad_* fields are only effective when backend="vad" (VAD-AD-05).
    """

    silence_threshold_db: Annotated[
        float,
        Field(
            default=-30.0,
            le=0.0,
            description=(
                "silencedetect backend only. Use vad_* when using VAD. "
                "Volume threshold (dB) for silence detection. Must be <= 0. "
                "Example: -30.0 dB (default), -40.0 dB (stricter detection)."
            ),
        ),
    ] = -30.0

    min_silence_duration: Annotated[
        float,
        Field(
            default=0.5,
            gt=0.0,
            description=(
                "silencedetect backend only. Use vad_* when using VAD. "
                "Minimum duration (seconds) to consider as silence. Must be > 0. "
                "Silences shorter than this value are ignored. Default is 0.5 seconds."
            ),
        ),
    ] = 0.5

    padding: Annotated[
        float,
        Field(
            default=0.1,
            ge=0.0,
            description=(
                "Padding width (seconds) to extend each KEEP interval on both sides."
                " Must be >= 0. If extension causes adjacent KEEPs to overlap,"
                " they are merged (prevents word cutoff). Default is 0.1 seconds."
            ),
        ),
    ] = 0.1

    min_keep_duration: Annotated[
        float,
        Field(
            default=0.0,
            ge=0.0,
            description=(
                "Minimum interval length (seconds) to retain as KEEP. Must be >= 0."
                " KEEP intervals shorter than this value are discarded after"
                " padding and merging."
                " Default is 0.0 (no discard; DC-AM-001 opt-in guard)."
            ),
        ),
    ] = 0.0

    backend: Annotated[
        Literal["silencedetect", "vad"],
        Field(
            default="silencedetect",
            description=(
                "Detection backend to use. "
                '"silencedetect" (default) uses the ffmpeg silencedetect filter. '
                '"vad" uses Silero VAD (ONNX). VAD-AD-01 backward-compatible opt-in.'
            ),
        ),
    ] = "silencedetect"

    vad_threshold: Annotated[
        float,
        Field(
            default=0.5,
            ge=0.0,
            le=1.0,
            description=(
                "VAD backend only. "
                "Speech probability threshold (0.0-1.0)."
                " Values >= this are considered speech. Default is 0.5."
            ),
        ),
    ] = 0.5

    vad_min_speech_duration: Annotated[
        float,
        Field(
            default=0.25,
            gt=0.0,
            description=(
                "VAD backend only. "
                "Minimum duration (seconds) to classify as speech. Must be > 0. "
                "Default is 0.25 seconds."
            ),
        ),
    ] = 0.25

    vad_min_silence_duration: Annotated[
        float,
        Field(
            default=0.1,
            gt=0.0,
            description=(
                "VAD backend only. "
                "Minimum silence duration (seconds) between speech intervals."
                " Must be > 0. Silences shorter than this value are absorbed"
                " into speech intervals. Default is 0.1 seconds."
            ),
        ),
    ] = 0.1
