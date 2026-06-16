"""plan.py — STUB: Pure planning logic for frame extraction.

All functions raise NotImplementedError; this stub exists only to verify that
test_plan.py collects and fails for the right reason (NotImplementedError, not
SyntaxError or ImportError).
"""

from __future__ import annotations

import opentimelineio as otio


def compute_interval_timestamps(duration_sec: float, interval_sec: float) -> list[float]:
    """Compute timestamps at i*interval_sec where i*interval_sec < duration_sec."""
    raise NotImplementedError


def compute_timestamps_mode(
    timestamps: list[float],
    duration_sec: float,
) -> tuple[list[float], list[float]]:
    """Partition timestamps into (kept, skipped) by range [0, duration_sec)."""
    raise NotImplementedError


def scene_marker_seconds(markers: list[otio.schema.Marker]) -> list[float]:
    """Extract sorted seconds from OTIO marker list."""
    raise NotImplementedError


def build_fps_command(
    ffmpeg: str,
    media: str,
    out_pattern: str,
    options: object,
) -> list[str]:
    """Build ffmpeg command for interval mode (fps filter)."""
    raise NotImplementedError


def build_single_frame_command(
    ffmpeg: str,
    media: str,
    ts: float,
    out_path: str,
    options: object,
) -> list[str]:
    """Build ffmpeg command for scene/timestamps mode (single frame extraction)."""
    raise NotImplementedError


def frame_filename(index: int, fmt: str) -> str:
    """Generate zero-padded frame filename: frame_%05d.{ext}."""
    raise NotImplementedError
