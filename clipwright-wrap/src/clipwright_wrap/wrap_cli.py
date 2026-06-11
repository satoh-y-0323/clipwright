"""wrap_cli.py — Small CLI for BudouX phrase-boundary segmentation (separate process).

Not imported by the MCP server process (§2.4 subprocess loose coupling).
wrap.py launches this as sys.executable -m clipwright_wrap.wrap_cli in a subprocess.

CLI contract (WR-AD-02):
  - stdin: JSON {"language": "ja", "texts": ["cue1", ...]}
  - stdout: JSON {"segments": [["segment1", "segment2", ...], ...]}
  - On error stdout: {"error": {"code": str, "message": str, "hint": str}}
  - main() catches all exceptions at the top level, always outputs JSON to stdout,
    and returns 0.
  - stdout contains JSON only. Logs and progress go to stderr.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from clipwright.errors import ErrorCode

# pip install hint string
_WRAP_INSTALL_HINT = "Install clipwright-wrap with `pip install clipwright-wrap`."

# Mapping of language → parser load function (DC-AS-002: target for test monkeypatching)
# budoux is imported at module top level. Because this CLI runs in a separate process,
# there is no risk of leaking into the server process, and _PARSER_LOADERS must be
# exposed as a module constant (tests reference it directly).
# If budoux is not installed, the dict stays empty (main() returns DEPENDENCY_MISSING).
try:
    import budoux as _budoux

    _PARSER_LOADERS: dict[str, Any] = {
        "ja": _budoux.load_default_japanese_parser,
        "zh-hans": _budoux.load_default_simplified_chinese_parser,
        "zh-hant": _budoux.load_default_traditional_chinese_parser,
        "th": _budoux.load_default_thai_parser,
    }
except ImportError:
    _PARSER_LOADERS = {}


def _error_output(code: str, message: str, hint: str) -> None:
    """Output an error JSON to stdout.

    The caller must sanitise any path information before passing it here.
    """
    result: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "hint": hint,
        }
    }
    print(json.dumps(result, ensure_ascii=False), file=sys.stdout)


def main(argv: list[str] | None = None) -> int:  # noqa: ARG001
    """Entry point for wrap_cli.

    Catches all exceptions at the top level, outputs JSON to stdout,
    and returns 0 (WR-AD-02).

    Args:
        argv: Command-line argument list (unused in the current version).

    Returns:
        Exit code (always 0).
    """
    try:
        # --- Read JSON from stdin ---
        try:
            raw = sys.stdin.read()
            payload: dict[str, Any] = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            _error_output(
                code=str(ErrorCode.INVALID_INPUT),
                message="Failed to parse JSON from stdin",
                hint="Pass a valid JSON object to stdin.",
            )
            return 0

        # --- Input validation ---
        if "language" not in payload:
            _error_output(
                code=str(ErrorCode.INVALID_INPUT),
                message="Missing 'language' key",
                hint="Include a 'language' key in the stdin JSON.",
            )
            return 0

        if "texts" not in payload:
            _error_output(
                code=str(ErrorCode.INVALID_INPUT),
                message="Missing 'texts' key",
                hint="Include a 'texts' key in the stdin JSON.",
            )
            return 0

        language: str = payload["language"]
        texts = payload["texts"]

        if not isinstance(texts, list):
            _error_output(
                code=str(ErrorCode.INVALID_INPUT),
                message="'texts' must be a list",
                hint="Set 'texts' in the stdin JSON to a list of strings.",
            )
            return 0

        if not all(isinstance(t, str) for t in texts):
            _error_output(
                code=str(ErrorCode.INVALID_INPUT),
                message="Each element of 'texts' must be a string",
                hint="Set 'texts' in the stdin JSON to a list of strings.",
            )
            return 0

        # --- Get the parser loader (DC-AS-002: loaded once, outside the texts loop) ---
        # If budoux is missing (_PARSER_LOADERS empty), return DEPENDENCY_MISSING
        # CR L-2: return DEPENDENCY_MISSING + install hint instead of INVALID_INPUT
        if not _PARSER_LOADERS:
            _error_output(
                code=str(ErrorCode.DEPENDENCY_MISSING),
                message="budoux is not installed",
                hint=_WRAP_INSTALL_HINT,
            )
            return 0

        if language not in _PARSER_LOADERS:
            _error_output(
                code=str(ErrorCode.INVALID_INPUT),
                message="Unsupported language specified",
                hint=(
                    "Specify one of the following for language:"
                    " ja / zh-hans / zh-hant / th."
                ),
            )
            return 0

        # Load the parser once outside the texts loop (DC-AS-002)
        # ImportError when calling the loader is returned as DEPENDENCY_MISSING
        try:
            parser = _PARSER_LOADERS[language]()
        except ImportError:
            # SR L-2: str(exc) may contain internal paths; use a fixed message instead
            _error_output(
                code=str(ErrorCode.DEPENDENCY_MISSING),
                message="Failed to import budoux",
                hint=_WRAP_INSTALL_HINT,
            )
            return 0

        # --- Segment each cue text into phrase-boundary tokens ---
        segments: list[list[str]] = []
        for text in texts:
            seg: list[str] = parser.parse(text)
            segments.append(seg)

        result: dict[str, Any] = {"segments": segments}
        print(json.dumps(result, ensure_ascii=False), file=sys.stdout)
        return 0

    except Exception:
        # Catch all unexpected exceptions and return an error JSON (WR-AD-02)
        # SR NF-L-1: str(exc) may contain internal paths; use a fixed message instead.
        # Debug details go to stderr only; must not leak into stdout JSON.
        traceback.print_exc(file=sys.stderr)
        _error_output(
            code=str(ErrorCode.INTERNAL),
            message="An unexpected error occurred in wrap_cli",
            hint="Please report with reproduction steps.",
        )
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
