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
