"""errors.py — Error code taxonomy and ClipwrightError exception.

The library layer raises ClipwrightError on failure;
server.py converts it to error_result at the MCP boundary.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Error codes shared across all Clipwright tools (§4 + §13.1 DC-AM-002/DC-AS-003).

    Inherits str so values are JSON-serializable at API boundaries.
    Each value equals its name string.
    """

    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
    """External tool (ffmpeg/ffprobe, etc.) not found."""
    INVALID_INPUT = "INVALID_INPUT"
    """Argument validation failed."""
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    """No file exists at the given input path."""
    PATH_NOT_ALLOWED = "PATH_NOT_ALLOWED"
    """Path validation failed (e.g., path traversal attempt)."""
    SUBPROCESS_FAILED = "SUBPROCESS_FAILED"
    """External process exited with a non-zero return code."""
    SUBPROCESS_TIMEOUT = "SUBPROCESS_TIMEOUT"
    """External process timed out."""
    PROBE_FAILED = "PROBE_FAILED"
    """Failed to parse ffprobe output."""
    OTIO_ERROR = "OTIO_ERROR"
    """Failed to read, write, or parse an OTIO file."""
    PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
    """clipwright.json not found."""
    PROJECT_EXISTS = "PROJECT_EXISTS"
    """An existing project already exists at the target init location."""
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    """Unknown or unsupported operation type."""
    INTERNAL = "INTERNAL"
    """Unexpected internal error (§13.1 DC-AM-002).

    Use a generic message; expose stack traces only in hints/logs.
    The hint must include a prompt to report with reproduction steps.
    """
    TRACK_NOT_FOUND = "TRACK_NOT_FOUND"
    """The track index in operations exceeds the total track count (§13.1 DC-AS-003)."""


class ClipwrightError(Exception):
    """Exception raised by the Clipwright library layer.

    Always carries the three-part set: code / message / hint (§6.4 error contract).
    hint must describe the concrete next action for the user or AI agent.
    """

    def __init__(self, code: ErrorCode, message: str, hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
