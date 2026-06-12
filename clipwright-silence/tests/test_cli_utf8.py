"""test_cli_utf8.py — UTF-8 I/O symmetry guard for vad_cli (post DRY refactor).

After the DRY refactor (CR L-2 / SR I-1 / CR L-3), the UTF-8 helper lives in core
as clipwright.cli_io.force_utf8_io(). vad_cli imports and re-exposes it as
vad_cli.force_utf8_io. The deterministic reconfigure/guard unit tests now live in
the core suite (tests/test_cli_io.py) and are NOT duplicated here.

This file keeps a thin presence/symmetry check parallel to wrap_cli's, closing
the symmetry gap that CR L-3 flagged: both CLIs are verified to source the same
shared helper. (vad_cli reads no stdin and emits numeric JSON only, so a
subprocess round-trip is not meaningful here — see ADR-3 / DC-AM-003.)
"""

from __future__ import annotations


def test_force_utf8_io_sources_shared_helper() -> None:
    """Verify vad_cli re-exposes the shared core helper (not a local copy).

    Symmetry/presence check (CR L-3): mirrors wrap_cli's check so the two CLIs
    have symmetric coverage. vad_cli must expose force_utf8_io and it must BE the
    core shared helper object (clipwright.cli_io.force_utf8_io), proving the
    duplication was removed rather than renamed.

    Pre-fix: FAILS — vad_cli has no force_utf8_io (only the old private copy).
    Post-fix: PASSES.
    """
    from clipwright import cli_io

    from clipwright_silence import vad_cli

    assert hasattr(vad_cli, "force_utf8_io"), (
        "vad_cli does not expose force_utf8_io (expected import from clipwright.cli_io)"
    )
    assert vad_cli.force_utf8_io is cli_io.force_utf8_io, (
        "vad_cli.force_utf8_io is not the shared clipwright.cli_io.force_utf8_io; "
        "it appears to be a local duplicate rather than the core helper"
    )
