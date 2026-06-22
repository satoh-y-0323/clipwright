"""Shared _whisper_run factory for test_transcribe.py and test_server.py.

Placed in tests/_whisper_run.py (an importable local module) to avoid the
sys.path fragility of `from conftest import ...`.  Both test files import via
relative package import: `from ._whisper_run import _whisper_run`.

This module intentionally does NOT re-export Segment so that callers retain
their own imports and remain self-contained.
"""

from __future__ import annotations

from clipwright_transcribe.captions import Segment
from clipwright_transcribe.transcribe import WhisperRun


def _whisper_run(
    segments: list[Segment],
    language: str | None = "en",
    device: str = "cpu",
    detail: str = "cpu",
    wall: float = 1.0,
) -> WhisperRun:
    """Build a WhisperRun for use as a _run_whisper mock return value."""
    return WhisperRun(segments, language, {"device": device, "detail": detail}, wall)
