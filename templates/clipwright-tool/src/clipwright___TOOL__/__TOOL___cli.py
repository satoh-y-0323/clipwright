"""__TOOL___cli.py — Small separate-process CLI wrapping external OSS (M4, reference implementation).

Not needed for pure Python tools without OSS, can be deleted.

This module is not imported from MCP server process (license independence, M4).
__TOOL__.py launches it as separate process via sys.executable -m clipwright___TOOL__.__TOOL___cli.

CLI contract:
  - stdin: JSON (input payload for this tool)
  - stdout: JSON (success result)
  - On error stdout: {"error": {"code": str, "message": str, "hint": str}}
  - main() catches all exceptions at top level, always outputs stdout JSON and returns 0.
  - stdout is JSON only. Logs, progress, traces go to stderr (prevent secret leaks, CWE-209).
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from clipwright.errors import ErrorCode

# OSS installation hint. Replace __TOOL__ with actual OSS package name.
_INSTALL_HINT = "Install dependencies with `pip install <your-oss>`."


def _force_utf8_io() -> None:
    """Pin stdin/stdout to UTF-8 so this CLI is correct regardless of host
    locale or inherited PYTHONIOENCODING (cp932 on JP Windows otherwise).

    MUST be called before the first read from stdin: TextIOWrapper.reconfigure
    raises once buffered reading has begun. Calling it at the very top of main()
    (before sys.stdin.read()) satisfies this. Safe to call even on a CLI that
    never reads stdin (reconfigure before any read is a no-op there).
    """
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


# External OSS imported at module top (separate process so no server leak).
# If not installed, set _OSS to None and main() returns DEPENDENCY_MISSING.
try:
    # import your_oss as _oss  # noqa: ERA001  TODO: Replace with actual OSS
    _OSS: Any = object()  # Template dummy. On implementation, put import result here.
except ImportError:  # pragma: no cover
    _OSS = None


def _error_output(code: str, message: str, hint: str) -> None:
    """Output error JSON to stdout. message/hint must be sanitized."""
    result: dict[str, Any] = {"error": {"code": code, "message": message, "hint": hint}}
    print(json.dumps(result, ensure_ascii=False), file=sys.stdout)


def main(argv: list[str] | None = None) -> int:  # noqa: ARG001
    """CLI entry point. Catch all exceptions, output stdout JSON, return 0."""
    _force_utf8_io()

    try:
        try:
            payload: dict[str, Any] = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, ValueError):
            _error_output(
                code=str(ErrorCode.INVALID_INPUT),
                message="Failed to parse JSON from stdin",
                hint="Pass valid JSON object to stdin.",
            )
            return 0

        if _OSS is None:
            _error_output(
                code=str(ErrorCode.DEPENDENCY_MISSING),
                message="Required OSS not installed",
                hint=_INSTALL_HINT,
            )
            return 0

        # TODO: Use payload to call OSS and assemble result.
        result: dict[str, Any] = {"result": payload}
        print(json.dumps(result, ensure_ascii=False), file=sys.stdout)
        return 0

    except Exception:
        # Catch all unexpected exceptions. str(exc) may contain internal paths, so use fixed message.
        traceback.print_exc(file=sys.stderr)
        _error_output(
            code=str(ErrorCode.INTERNAL),
            message="CLI shim encountered unexpected error",
            hint="Report with reproduction steps.",
        )
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
