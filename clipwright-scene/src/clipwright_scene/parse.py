"""parse.py — Pure parsing logic for shot boundary detection outputs.

No subprocess calls or I/O side effects. All functions accept text and return
lists of SceneBoundary objects.

Covers:
  §4 FFmpeg scdet filter stderr parsing
  §5 PySceneDetect CSV parsing
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class SceneBoundary:
    """A detected shot boundary with timestamp, confidence, and index."""

    timestamp_sec: float
    confidence: float  # 0.0–1.0 normalized
    scene_index: int   # 0-based


# Matches both "[scdet @ 0x...]" and bare "scdet @ 0x..." formats.
# Captures pts_time and score values from the scdet filter output line.
_SCDET_PATTERN = re.compile(
    r"pts_time=(\d+(?:\.\d+)?)\s+score=(\d+(?:\.\d+)?)"
)


def parse_scdet_stderr(
    stderr: str | None,
    total_duration_sec: float,  # noqa: ARG001 — reserved for future clipping
) -> list[SceneBoundary]:
    """Parse FFmpeg scdet filter stderr and return sorted SceneBoundary list.

    Args:
        stderr: Raw stderr text from an ffmpeg run with ``-vf scdet``.
            Accepts None gracefully (returns empty list).
        total_duration_sec: Duration of the source media in seconds.
            Currently unused; reserved for future boundary clipping.

    Returns:
        List of SceneBoundary sorted by timestamp_sec ascending.
        confidence = min(score / 100.0, 1.0).
    """
    if not stderr:
        return []

    boundaries: list[SceneBoundary] = []
    for match in _SCDET_PATTERN.finditer(stderr):
        pts_time = float(match.group(1))
        score = float(match.group(2))
        confidence = min(score / 100.0, 1.0)
        boundaries.append(
            SceneBoundary(
                timestamp_sec=pts_time,
                confidence=confidence,
                scene_index=0,  # re-assigned below
            )
        )

    boundaries.sort(key=lambda b: b.timestamp_sec)
    for idx, boundary in enumerate(boundaries):
        boundary.scene_index = idx

    return boundaries


def parse_pyscenedetect_csv(csv_text: str) -> list[SceneBoundary]:
    """Parse PySceneDetect list-scenes CSV output into SceneBoundary objects.

    The CSV must contain a header row with the column name
    ``Start Time (seconds)``.  All other rows are treated as scene data.
    Malformed rows (too few columns, non-numeric values) are silently skipped.

    Args:
        csv_text: Full CSV text as produced by ``scenedetect list-scenes``.

    Returns:
        List of SceneBoundary sorted by timestamp_sec ascending.
        confidence is always 1.0 (PySceneDetect reports binary cut points).
        scene_index is 0-based (CSV Scene Number is 1-based).
    """
    if not csv_text or not csv_text.strip():
        return []

    lines = csv_text.splitlines()

    # Locate header row and resolve the column index for "Start Time (seconds)".
    header_idx: int | None = None
    start_time_col: int | None = None
    for line_idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # Detect the header line by presence of the required column name.
        if "Start Time (seconds)" in stripped:
            columns = [c.strip() for c in stripped.split(",")]
            try:
                start_time_col = columns.index("Start Time (seconds)")
            except ValueError:
                continue
            header_idx = line_idx
            break

    if header_idx is None or start_time_col is None:
        return []

    boundaries: list[SceneBoundary] = []
    scene_index = 0
    for line in lines[header_idx + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        columns = [c.strip() for c in stripped.split(",")]
        try:
            timestamp_sec = float(columns[start_time_col])
        except (ValueError, IndexError):
            continue
        boundaries.append(
            SceneBoundary(
                timestamp_sec=timestamp_sec,
                confidence=1.0,
                scene_index=scene_index,
            )
        )
        scene_index += 1

    boundaries.sort(key=lambda b: b.timestamp_sec)
    # Re-assign scene_index after sorting to keep it consistent with order.
    for idx, boundary in enumerate(boundaries):
        boundary.scene_index = idx

    return boundaries


def merge_close_boundaries(
    boundaries: list[SceneBoundary],
    min_duration_sec: float,
) -> list[SceneBoundary]:
    """Merge shot boundaries that are closer than min_duration_sec apart.

    When two or more boundaries fall within min_duration_sec of each other,
    only the one with the highest confidence is retained.  Ties are broken by
    keeping the earlier boundary.

    Args:
        boundaries: List of SceneBoundary objects (may be unsorted).
        min_duration_sec: Minimum allowed gap between retained boundaries.
            Pass 0.0 to disable merging entirely (all boundaries returned).

    Returns:
        List of SceneBoundary sorted by timestamp_sec ascending,
        with scene_index reassigned from 0.
    """
    if not boundaries:
        return []

    if min_duration_sec == 0.0:
        result = sorted(boundaries, key=lambda b: b.timestamp_sec)
        for idx, boundary in enumerate(result):
            boundary.scene_index = idx
        return result

    sorted_b = sorted(boundaries, key=lambda b: b.timestamp_sec)

    # Group consecutive boundaries that are all within min_duration_sec of the
    # previous *accepted* boundary using a cluster-scan approach:
    # Walk through sorted boundaries; accumulate a cluster while the gap from
    # the cluster start is less than min_duration_sec, then keep the winner.
    retained: list[SceneBoundary] = []
    cluster: list[SceneBoundary] = [sorted_b[0]]

    for current in sorted_b[1:]:
        # Check gap against the first element of the current cluster.
        gap = current.timestamp_sec - cluster[0].timestamp_sec
        if gap < min_duration_sec:
            cluster.append(current)
        else:
            # Flush cluster: keep boundary with highest confidence.
            best = max(cluster, key=lambda b: b.confidence)
            retained.append(best)
            cluster = [current]

    # Flush the final cluster.
    best = max(cluster, key=lambda b: b.confidence)
    retained.append(best)

    retained.sort(key=lambda b: b.timestamp_sec)
    for idx, boundary in enumerate(retained):
        boundary.scene_index = idx

    return retained
