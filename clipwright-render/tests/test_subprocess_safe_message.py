"""test_subprocess_safe_message.py — Tests for SR-R-001 subprocess-error redaction
(ADR-SR-1, requirements-report-20260717-163648.md FR-1).

Pins render's `_sanitize_subprocess_error` to the shared core helper
`safe_subprocess_message` (mirrors `clipwright_transcribe.transcribe`'s
`_sanitize_subprocess_error` / TR-AD-09, adapted for clipwright-render).

Regression guard (this file): `_sanitize_subprocess_error` is implemented in
`clipwright_render.render` and wired into all five seams. Before impl-render
these tests were Red — either at collection time (ImportError for the unit-test
class) or at assertion time (the render/S1/S3 integration tests observed raw,
unmasked SUBPROCESS_FAILED/TIMEOUT messages because render.py did not call the
sanitizer). They now pass and guard against a regression of FR-1.

Covers (architecture-report-20260717-163916.md §3/§7/§8):
  - Unit: `_sanitize_subprocess_error(exc) -> ClipwrightError` in isolation
    (SUBPROCESS_FAILED / SUBPROCESS_TIMEOUT masked; every other code passed
    through unchanged, `result is exc` identity preserved).
  - Pin: `clipwright_render.render` must NOT define a local
    `_SUBPROCESS_SAFE_MESSAGE` constant (DRY — reuse the core one).
  - Integration S1: full `render_timeline()` pipeline with `run()` monkeypatched
    to raise a SUBPROCESS_FAILED carrying a raw absolute path, verifying the
    error envelope's message is masked (`code`/`hint` unchanged).
  - Integration S1 (render_plan): `render_plan()` called directly with `run()`
    monkeypatched, verifying the raised ClipwrightError is masked.
  - Integration S3: `_probe()` called directly with `inspect_media()`
    monkeypatched to raise SUBPROCESS_FAILED, verifying the re-raised error
    is masked.
  - Real-binary (e2e marker): a garbage `.mp4` file fed to the real ffprobe
    binary via `_probe()`, verifying the real subprocess failure is masked
    (skipped when ffprobe cannot be resolved via PATH / CLIPWRIGHT_FFPROBE).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.process import SUBPROCESS_SAFE_MESSAGE, safe_subprocess_message
from clipwright.schemas import MediaInfo, StreamInfo

import clipwright_render.render as render_module
from clipwright_render.schemas import RenderOptions

# ---------------------------------------------------------------------------
# _sanitize_subprocess_error is now implemented in render.py. Each unit test
# below imports it locally (function scope) rather than at module scope: this
# kept the earlier Red phase clean (an ImportError failed exactly those 8 unit
# tests for the right reason without turning a module-level ImportError into a
# collection error for the S1/S3 integration tests). The function-scope import
# is retained so the shim's contract stays self-documenting.
# ---------------------------------------------------------------------------


def _sanitize(exc: ClipwrightError) -> ClipwrightError:
    """Local-import shim for clipwright_render.render._sanitize_subprocess_error.

    During the earlier Red phase this raised ImportError when the function did
    not exist yet -- the exact failure mode required for the unit tests below,
    without turning a module-level ImportError into a collection error for the
    rest of this test file.
    """
    from clipwright_render.render import _sanitize_subprocess_error

    return _sanitize_subprocess_error(exc)


# ---------------------------------------------------------------------------
# Shared OTIO / probe helpers (mirrors test_render.py's helpers; duplicated
# locally per-file rather than imported cross-module, matching this
# project's existing per-test-file helper convention).
# ---------------------------------------------------------------------------

FPS = 30.0


def _rt(seconds: float, rate: float = FPS) -> otio.opentime.RationalTime:
    return otio.opentime.RationalTime(seconds * rate, rate)


def _tr(start: float, duration: float, rate: float = FPS) -> otio.opentime.TimeRange:
    return otio.opentime.TimeRange(
        start_time=_rt(start, rate),
        duration=_rt(duration, rate),
    )


def _make_clip(source: str, start: float, duration: float) -> otio.schema.Clip:
    clip = otio.schema.Clip()
    clip.media_reference = otio.schema.ExternalReference(target_url=source)
    clip.source_range = _tr(start, duration)
    return clip


def _make_timeline(clips: list[otio.schema.Clip]) -> otio.schema.Timeline:
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    for clip in clips:
        track.append(clip)
    tl = otio.schema.Timeline()
    tl.tracks.append(track)
    return tl


def _write_timeline(path: Path, clips: list[otio.schema.Clip]) -> None:
    tl = _make_timeline(clips)
    otio.adapters.write_to_file(tl, str(path))


def _make_media_info(path: str) -> MediaInfo:
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=None,
        streams=[StreamInfo(index=0, codec_type="video", codec_name="h264")],
        bit_rate=8_000_000,
    )


def _make_exc(
    code: ErrorCode, *, path: str = "/abs/path/to/media.mp4"
) -> ClipwrightError:
    """Build a ClipwrightError simulating a raw subprocess failure message.

    The message intentionally embeds an absolute path so the no-path-leak
    assertion below is load-bearing.
    """
    return ClipwrightError(
        code=code,
        message=f"subprocess failed: {path}: exit status 1",
        hint="try again",
    )


# ===========================================================================
# Unit tests: _sanitize_subprocess_error (mirrors
# clipwright-transcribe/tests/test_subprocess_safe_message.py 1:1, adapted for
# clipwright_render.render)
# ===========================================================================


class TestSanitizeSubprocessError:
    """Verify _sanitize_subprocess_error produces the shared core helper output.

    Before impl-render, clipwright_render.render defined no
    _sanitize_subprocess_error function, so every test here failed with
    ImportError raised from _sanitize() (the local-import shim above) -- the
    correct Red signal for FR-1's unit contract. The function now exists and
    these tests pass.
    """

    def test_subprocess_failed_equals_safe_message(self) -> None:
        """SUBPROCESS_FAILED site emits a message equal to safe_subprocess_message(exc)."""
        exc = _make_exc(ErrorCode.SUBPROCESS_FAILED)
        sanitised = _sanitize(exc)
        expected = safe_subprocess_message(sanitised)
        assert sanitised.message == expected

    def test_subprocess_timeout_equals_safe_message(self) -> None:
        """SUBPROCESS_TIMEOUT site emits a message equal to safe_subprocess_message(exc)."""
        exc = _make_exc(ErrorCode.SUBPROCESS_TIMEOUT)
        sanitised = _sanitize(exc)
        expected = safe_subprocess_message(sanitised)
        assert sanitised.message == expected

    def test_subprocess_failed_contains_core_constant(self) -> None:
        """Sanitised SUBPROCESS_FAILED message starts with the core SUBPROCESS_SAFE_MESSAGE."""
        exc = _make_exc(ErrorCode.SUBPROCESS_FAILED)
        sanitised = _sanitize(exc)
        assert sanitised.message.startswith(SUBPROCESS_SAFE_MESSAGE)

    def test_subprocess_timeout_contains_core_constant(self) -> None:
        """Sanitised SUBPROCESS_TIMEOUT message starts with the core SUBPROCESS_SAFE_MESSAGE."""
        exc = _make_exc(ErrorCode.SUBPROCESS_TIMEOUT)
        sanitised = _sanitize(exc)
        assert sanitised.message.startswith(SUBPROCESS_SAFE_MESSAGE)

    def test_no_absolute_path_leak_failed(self) -> None:
        """Sanitised SUBPROCESS_FAILED message must not contain the raw absolute path."""
        abs_path = "/abs/path/to/media.mp4"
        exc = _make_exc(ErrorCode.SUBPROCESS_FAILED, path=abs_path)
        sanitised = _sanitize(exc)
        assert abs_path not in sanitised.message

    def test_no_absolute_path_leak_timeout(self) -> None:
        """Sanitised SUBPROCESS_TIMEOUT message must not contain the raw absolute path."""
        abs_path = "/abs/path/to/media.mp4"
        exc = _make_exc(ErrorCode.SUBPROCESS_TIMEOUT, path=abs_path)
        sanitised = _sanitize(exc)
        assert abs_path not in sanitised.message

    def test_other_code_unchanged(self) -> None:
        """Errors with codes other than SUBPROCESS_FAILED/TIMEOUT are returned unchanged."""
        exc = ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message="file /path/file.mp4 not found",
            hint="check the path",
        )
        result = _sanitize(exc)
        assert result is exc

    def test_hint_preserved(self) -> None:
        """hint is preserved unchanged through masking (only message is replaced)."""
        exc = _make_exc(ErrorCode.SUBPROCESS_FAILED)
        sanitised = _sanitize(exc)
        assert sanitised.hint == exc.hint

    def test_code_preserved(self) -> None:
        """code is preserved unchanged through masking."""
        exc = _make_exc(ErrorCode.SUBPROCESS_TIMEOUT)
        sanitised = _sanitize(exc)
        assert sanitised.code == exc.code


class TestNoLocalSubprocessSafeMessage:
    """Assert the local `_SUBPROCESS_SAFE_MESSAGE` copy does NOT exist in render.py.

    Pins that render.py reuses the shared core constant
    (`clipwright.process.SUBPROCESS_SAFE_MESSAGE`) rather than defining a
    redundant local copy (ADR-SR-1 §3.1 / DRY, mirrors transcribe's
    TestNoLocalSubprocessSafeMessage). This test passed throughout (render.py
    defines no such constant) and keeps passing now that impl-render added
    `_sanitize_subprocess_error` using the shared core helper.
    """

    def test_no_local_subprocess_safe_message(self) -> None:
        """clipwright_render.render must NOT define a module-level _SUBPROCESS_SAFE_MESSAGE."""
        assert not hasattr(render_module, "_SUBPROCESS_SAFE_MESSAGE"), (
            "clipwright_render.render defines a local _SUBPROCESS_SAFE_MESSAGE. "
            "Remove it and use `from clipwright.process import safe_subprocess_message`"
            " instead."
        )


# ===========================================================================
# Integration S1: render_plan() / render_timeline() with run() monkeypatched
# to raise a SUBPROCESS_FAILED carrying a raw absolute path.
#
# Before impl-render, render.py did not call _sanitize_subprocess_error
# anywhere, so both render_plan() (raised the exception verbatim) and
# render_timeline() (converted it to an error_result envelope via
# render_timeline's top-level except ClipwrightError, without masking) leaked
# the raw message. Both seams now route through the sanitizer.
# ===========================================================================


class TestS1RenderPlanInjection:
    """S1/S2 seam: run() failure surfaces through render_plan() (render.py:402)."""

    def test_render_plan_masks_subprocess_failed_message(self, tmp_path: Path) -> None:
        """render_plan() raises a masked ClipwrightError when run() fails.

        Before impl-render, render_plan did not wrap run() in a try/except, so
        the raw ClipwrightError (with the embedded absolute path) propagated
        verbatim -- the masked-message assertion below failed. render_plan now
        wraps run() (S2 seam) and re-raises through _sanitize_subprocess_error.
        """
        from clipwright_render.plan import RenderPlan

        leak_path = str(tmp_path / "leaked-media-secret.mp4")
        plan = RenderPlan(
            filter_complex="",
            ffmpeg_args=["-c:v", "libx264"],
            segment_count=1,
            total_duration_seconds=5.0,
            input_sources=[str(tmp_path / "a.mp4")],
        )
        output = str(tmp_path / "out.mp4")

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=f"Command failed with exit code 1: {leak_path}: no such file",
                hint="Check the command arguments.",
            )

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            render_module.render_plan(plan, output)

        raised = exc_info.value
        assert raised.code == ErrorCode.SUBPROCESS_FAILED
        assert raised.hint == "Check the command arguments."
        expected_message = safe_subprocess_message(raised)
        assert raised.message == expected_message, (
            "render_plan() must mask the raw run() message via "
            "_sanitize_subprocess_error rather than leak it verbatim (S2 seam, "
            "ADR-SR-1)."
        )
        assert leak_path not in raised.message

    def test_render_timeline_masks_subprocess_failed_message(
        self, tmp_path: Path
    ) -> None:
        """Full render_timeline() pipeline masks a SUBPROCESS_FAILED from the main
        ffmpeg run() call (S1 seam).

        Before impl-render, render_timeline's top-level except ClipwrightError
        converted the exception to an error_result envelope without masking, so
        the assertion on the masked message failed and the leaked absolute path
        was still present in the envelope. The main ffmpeg run() is now wrapped
        (S1 seam) and its error routed through _sanitize_subprocess_error.
        """
        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")
        leak_path = str(tmp_path / "leaked-media-secret.mp4")

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=f"Command failed with exit code 1: {leak_path}: no such file",
                hint="Check the command arguments.",
            )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
        ):
            result = render_module.render_timeline(
                timeline=str(tl_path), output=output, options=RenderOptions()
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.SUBPROCESS_FAILED
        assert result["error"]["hint"] == "Check the command arguments."
        expected_message = safe_subprocess_message(
            ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="",
                hint="",
            )
        )
        assert result["error"]["message"] == expected_message, (
            "render_timeline() must mask the raw run() message via "
            "_sanitize_subprocess_error rather than return it verbatim (S1 seam, "
            "ADR-SR-1)."
        )
        assert leak_path not in result["error"]["message"]


# ===========================================================================
# Integration S3: _probe() with inspect_media() monkeypatched to raise
# SUBPROCESS_FAILED (ffprobe seam, render.py:123-131).
# ===========================================================================


class TestS3ProbeInjection:
    """S3 seam: inspect_media() (ffprobe) failure surfaces through _probe()."""

    def test_probe_masks_subprocess_failed_message(self, tmp_path: Path) -> None:
        """_probe() re-raises a masked ClipwrightError when inspect_media() fails
        with SUBPROCESS_FAILED.

        Before impl-render, _probe's except ClipwrightError branch only
        special-cased FILE_NOT_FOUND; every other code (including
        SUBPROCESS_FAILED) fell through to a bare `raise`, so the raw message --
        including the embedded absolute path -- propagated unmodified and the
        masked-message assertion below failed. The bare `raise` now routes
        through _sanitize_subprocess_error (S3 seam).
        """
        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        leak_path = str(tmp_path / "leaked-media-secret.mp4")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=ClipwrightError(
                    code=ErrorCode.SUBPROCESS_FAILED,
                    message=f"Command failed with exit code 1: {leak_path}: eof",
                    hint="Check the command arguments, input file path, and tool version.",
                ),
            ),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            render_module._probe(source)

        raised = exc_info.value
        assert raised.code == ErrorCode.SUBPROCESS_FAILED
        assert (
            raised.hint
            == "Check the command arguments, input file path, and tool version."
        )
        expected_message = safe_subprocess_message(raised)
        assert raised.message == expected_message, (
            "_probe() must mask the raw inspect_media() message via "
            "_sanitize_subprocess_error rather than leak it verbatim (S3 seam, "
            "ADR-SR-1)."
        )
        assert leak_path not in raised.message

    def test_probe_masks_subprocess_timeout_message(self, tmp_path: Path) -> None:
        """_probe() re-raises a masked ClipwrightError when inspect_media() fails
        with SUBPROCESS_TIMEOUT.
        """
        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        leak_path = str(tmp_path / "leaked-media-secret.mp4")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=ClipwrightError(
                    code=ErrorCode.SUBPROCESS_TIMEOUT,
                    message=f"Command timed out after 30.0 seconds: {leak_path}",
                    hint="Increase the timeout value or check the size of the input file.",
                ),
            ),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            render_module._probe(source)

        raised = exc_info.value
        assert raised.code == ErrorCode.SUBPROCESS_TIMEOUT
        expected_message = safe_subprocess_message(raised)
        assert raised.message == expected_message, (
            "_probe() must mask the raw inspect_media() timeout message rather "
            "than leak it verbatim (S3 seam, ADR-SR-1)."
        )
        assert leak_path not in raised.message

    def test_probe_file_not_found_still_uses_basename_curated_message(
        self,
    ) -> None:
        """Regression guard: FILE_NOT_FOUND keeps its existing curated
        (basename-only) re-raise path and is NOT routed through
        _sanitize_subprocess_error (§8 edge-case table). This is a pre-existing
        behaviour that passed before FR-1 and keeps passing now that FR-1 has
        landed.
        """
        source = "/abs/path/to/link.mp4"
        expected_hint = "Specify a real file instead of a symbolic link."

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=ClipwrightError(
                    code=ErrorCode.FILE_NOT_FOUND,
                    message="Symbolic links are not accepted: /abs/path/to/link.mp4",
                    hint=expected_hint,
                ),
            ),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            render_module._probe(source)

        raised = exc_info.value
        assert raised.code == ErrorCode.FILE_NOT_FOUND
        assert raised.message == "Source media file not found: link.mp4"
        assert raised.hint == expected_hint


# ===========================================================================
# Real-binary masking test (e2e marker): garbage .mp4 -> real ffprobe failure
# -> _probe() must mask the message (skipped when ffprobe is unresolvable).
# ===========================================================================


def _find_binary(name: str, env_var: str) -> str | None:
    """Search for a binary in PATH first, then fall back to env_var (mirrors
    the existing pattern used across this package's e2e test files, e.g.
    test_pip_ffmpeg_execution.py)."""
    found = shutil.which(name)
    if found:
        return found
    env_val = os.environ.get(env_var)
    if env_val and Path(env_val).is_file():
        return env_val
    return None


_FFPROBE = _find_binary("ffprobe", "CLIPWRIGHT_FFPROBE")

requires_ffprobe = pytest.mark.skipif(
    _FFPROBE is None,
    reason=(
        "ffprobe not found. Add ffprobe to PATH or set the CLIPWRIGHT_FFPROBE"
        " environment variable to its full path."
    ),
)


@pytest.mark.e2e
class TestRealFfprobeMasking:
    """Real ffprobe stderr masking (no monkeypatch on run()/inspect_media()).

    Before impl-render, _probe() did not mask SUBPROCESS_FAILED messages, so the
    message did not start with SUBPROCESS_SAFE_MESSAGE (it started with "Command
    failed with exit code ..."). _probe() now masks it via S3.
    """

    @requires_ffprobe
    def test_garbage_mp4_masks_real_ffprobe_stderr(self, tmp_path: Path) -> None:
        """A garbage-content .mp4 fed to the real ffprobe binary produces a
        SUBPROCESS_FAILED whose message is masked and free of the tmp_path
        working-directory absolute path.
        """
        garbage = tmp_path / "garbage.mp4"
        garbage.write_bytes(b"this is not a real media file, just garbage bytes")
        resolved_tmp_path = str(tmp_path.resolve())

        with pytest.raises(ClipwrightError) as exc_info:
            render_module._probe(str(garbage))

        raised = exc_info.value
        assert raised.code == ErrorCode.SUBPROCESS_FAILED
        assert raised.message.startswith(SUBPROCESS_SAFE_MESSAGE), (
            "_probe() must return the masked "
            f"{SUBPROCESS_SAFE_MESSAGE!r}-prefixed message rather than the raw "
            f"ffprobe-derived message ({raised.message!r}) (S3 seam, ADR-SR-1)."
        )
        assert resolved_tmp_path not in raised.message
        assert str(garbage) not in raised.message
