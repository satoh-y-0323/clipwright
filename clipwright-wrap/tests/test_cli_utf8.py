"""test_cli_utf8.py — UTF-8 I/O guards for wrap_cli (post DRY refactor).

After the DRY refactor (CR L-2 / SR I-1), the UTF-8 helper lives in core as
clipwright.cli_io.force_utf8_io(). wrap_cli imports and re-exposes it as
wrap_cli.force_utf8_io. The deterministic reconfigure/guard unit tests now live
in tests/test_cli_io.py (core); they are NOT duplicated here.

This file keeps:
  (1) A thin presence/symmetry check that wrap_cli sources the shared helper.
  (2) The subprocess round-trip POSITIVE GUARDS (both env variants) — these PASS
      pre- and post-fix and guard against crashes / invalid JSON.
  (3) The SR I-2 TRUE REGRESSION GUARD: a Japanese payload that is NOT
      byte-identity-preserving under a cp932 decode->encode cycle (it contains a
      character outside cp932). With PYTHONIOENCODING=cp932 (PYTHONUTF8 removed),
      a child that did not pin UTF-8 would crash or corrupt the text; with the
      fix it round-trips intact.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

import pytest


def test_force_utf8_io_sources_shared_helper() -> None:
    """Verify wrap_cli re-exposes the shared core helper (not a local copy).

    Symmetry/presence check (CR L-2 / SR I-1): after the refactor, wrap_cli must
    expose force_utf8_io and it must BE the core shared helper object
    (clipwright.cli_io.force_utf8_io), proving the duplication was removed rather
    than merely renamed.

    Pre-fix: FAILS — wrap_cli has no force_utf8_io (only the old private copy).
    Post-fix: PASSES.
    """
    from clipwright import cli_io

    from clipwright_wrap import wrap_cli

    assert hasattr(wrap_cli, "force_utf8_io"), (
        "wrap_cli does not expose force_utf8_io (expected import from clipwright.cli_io)"
    )
    assert wrap_cli.force_utf8_io is cli_io.force_utf8_io, (
        "wrap_cli.force_utf8_io is not the shared clipwright.cli_io.force_utf8_io; "
        "it appears to be a local duplicate rather than the core helper"
    )


class TestWrapCliUtf8Roundtrip:
    """Subprocess invocation guards for wrap_cli with Japanese text.

    NOTE on the byte-identity caveat (see security report I-2): a payload like
    "こんにちは世界" is byte-identity-preserving under a cp932 decode->encode cycle
    (its UTF-8 bytes are also a valid cp932 sequence whose round-trip yields the
    same bytes), so such a payload cannot detect the encoding defect — it PASSES
    pre- and post-fix and is only a positive guard. The SR I-2 regression guard
    below uses a payload that breaks this property to make the guard real.
    """

    @pytest.mark.parametrize(
        "env_variant",
        [
            "cp932_forced",  # PYTHONIOENCODING=cp932, PYTHONUTF8 unset
            "bare_unset",  # Both unset (matches success criterion ②)
        ],
    )
    def test_japanese_text_roundtrip(self, env_variant: str) -> None:
        """Invoke wrap_cli as subprocess with Japanese text and verify round-trip.

        Positive guard (NOT a Red): verifies wrap_cli does not crash, produces
        valid JSON, and the concatenated segments match the input. It PASSES both
        pre- and post-fix because "こんにちは世界" is byte-identity-preserving under a
        cp932 cycle. The deterministic behavioral checks for the helper itself
        live in core's tests/test_cli_io.py.
        """
        original_text = "こんにちは世界"
        input_payload: dict[str, Any] = {
            "language": "ja",
            "texts": [original_text],
        }
        input_json = json.dumps(input_payload, ensure_ascii=False)

        env = {**os.environ}

        if env_variant == "cp932_forced":
            env["PYTHONIOENCODING"] = "cp932"
            env.pop("PYTHONUTF8", None)
            input_data: bytes | str = input_json.encode("utf-8")
            use_text = False
        elif env_variant == "bare_unset":
            env.pop("PYTHONIOENCODING", None)
            env.pop("PYTHONUTF8", None)
            input_data = input_json
            use_text = True
        else:
            pytest.fail(f"Unknown env_variant: {env_variant}")

        cmd = [sys.executable, "-m", "clipwright_wrap.wrap_cli"]
        try:
            if use_text:
                proc = subprocess.run(
                    cmd,
                    input=input_data,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    env=env,
                    timeout=10,
                )
                stdout_text = proc.stdout
            else:
                proc = subprocess.run(
                    cmd,
                    input=input_data,
                    capture_output=True,
                    env=env,
                    timeout=10,
                )
                stdout_text = proc.stdout.decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            pytest.fail(f"wrap_cli subprocess timed out (env_variant={env_variant})")

        try:
            output_payload: dict[str, Any] = json.loads(stdout_text)
        except json.JSONDecodeError as e:
            pytest.fail(
                f"Failed to parse wrap_cli stdout (env_variant={env_variant}): {e}"
            )

        assert "segments" in output_payload, (
            f"No segments in output (env_variant={env_variant})"
        )

        segments = output_payload["segments"]
        assert len(segments) > 0 and len(segments[0]) > 0, (
            f"Empty segments (env_variant={env_variant})"
        )

        reconstructed = "".join("".join(seg_list) for seg_list in segments)
        assert reconstructed == original_text, (
            f"Text round-trip failed (env_variant={env_variant}): "
            f"expected {original_text!r}, got {reconstructed!r}. "
            f"This indicates wrap_cli is not reconfiguring stdin to UTF-8."
        )

    def test_japanese_text_roundtrip_non_byte_identity_cp932_forced(self) -> None:
        """SR I-2 TRUE regression guard: a non-byte-identity payload under cp932.

        Why this payload is a genuine guard (unlike "こんにちは世界"):
          - "こんにちは世界" is byte-identity-preserving: its UTF-8 bytes happen to be
            a valid cp932 sequence whose decode->re-encode produces the same bytes,
            so a child that wrongly used cp932 would still emit the right output.
          - This payload includes characters OUTSIDE cp932 — the clapperboard emoji
            "🎬" (U+1F3AC, a non-BMP code point with no cp932 mapping) and the rare
            kanji "𠮷" (U+20BB7, also non-BMP). A child that did NOT pin UTF-8 would,
            under PYTHONIOENCODING=cp932, either crash with UnicodeEncodeError when
            writing stdout or corrupt the text — it cannot round-trip these
            characters through cp932 at all.

        The child is launched with PYTHONIOENCODING=cp932 and PYTHONUTF8 removed, so
        the ONLY thing that makes the round-trip succeed is the in-process
        force_utf8_io() pinning. Input is passed as raw UTF-8 bytes; stdout is
        decoded as strict UTF-8 (no errors="replace") so any corruption surfaces.

        This guard PASSES now because the production fix is already in the working
        tree — it exists to catch a future regression that drops the UTF-8 pinning.
        """
        # Non-BMP characters with no cp932 mapping (breaks byte-identity).
        original_text = "映画🎬𠮷野家"
        input_payload: dict[str, Any] = {
            "language": "ja",
            "texts": [original_text],
        }
        input_json = json.dumps(input_payload, ensure_ascii=False)

        env = {**os.environ}
        env["PYTHONIOENCODING"] = "cp932"
        env.pop("PYTHONUTF8", None)

        cmd = [sys.executable, "-m", "clipwright_wrap.wrap_cli"]
        try:
            proc = subprocess.run(
                cmd,
                input=input_json.encode("utf-8"),
                capture_output=True,
                env=env,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            pytest.fail("wrap_cli subprocess timed out (non-byte-identity guard)")

        # Strict UTF-8 decode: corruption from a cp932-encoded child surfaces here.
        stdout_text = proc.stdout.decode("utf-8")

        try:
            output_payload: dict[str, Any] = json.loads(stdout_text)
        except json.JSONDecodeError as e:
            stderr_text = proc.stderr.decode("utf-8", errors="replace")
            pytest.fail(
                "Failed to parse wrap_cli stdout for non-byte-identity payload: "
                f"{e}. stderr={stderr_text!r}"
            )

        assert "segments" in output_payload, (
            "No segments in output for non-byte-identity payload; "
            "wrap_cli likely failed to encode UTF-8 under PYTHONIOENCODING=cp932"
        )

        segments = output_payload["segments"]
        assert len(segments) > 0 and len(segments[0]) > 0, (
            "Empty segments for non-byte-identity payload"
        )

        reconstructed = "".join("".join(seg_list) for seg_list in segments)
        assert reconstructed == original_text, (
            f"Non-byte-identity round-trip failed: expected {original_text!r}, "
            f"got {reconstructed!r}. wrap_cli is not pinning stdin/stdout to UTF-8."
        )
