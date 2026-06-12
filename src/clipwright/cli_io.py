"""cli_io.py — Shared UTF-8 I/O helper for separate-process CLIs.

DRY home for the per-CLI UTF-8 pinning helper (CR L-2 / SR I-1). wrap_cli and
vad_cli import force_utf8_io() from here instead of carrying a local copy, so the
behaviour is defined once for every separate-process CLI that depends on core.
"""

from __future__ import annotations

import sys


def force_utf8_io() -> None:
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
