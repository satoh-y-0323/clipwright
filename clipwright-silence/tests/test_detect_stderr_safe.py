"""test_detect_stderr_safe.py — Tests for stderr/path sanitisation in detect.py.

Covers two sanitisation seams:

Seam 1 (silencedetect path — SR L-1 [SR-R-001], already fixed):
  detect_silence() catches ClipwrightError and forwards exc.message verbatim
  via error_result(exc.code, exc.message, exc.hint) (detect.py:308-309).

  On the silencedetect path, _detect_silence_intervals() calls
  clipwright.process.run() (detect.py:155). When ffmpeg exits non-zero, core
  run() raises ClipwrightError(SUBPROCESS_FAILED) whose message is built from
  the raw ffmpeg stderr (stderr_summary, process.py:120-124), which can embed
  absolute input paths.

  The fix (detect.py:156-168) intercepts SUBPROCESS_FAILED/TIMEOUT and replaces
  the raw message with SUBPROCESS_SAFE_MESSAGE from clipwright.process (SR-NEW:
  DRY consolidation from the previously-duplicated detect.py/vad_cli.py constants).

Seam 2 (VAD path — SR L-1 [SR-R-001] symmetry, NEW in this follow-up):
  _detect_vad_silence_intervals() calls core run() directly (detect.py:212) to
  spawn the VAD CLI subprocess. If run() raises SUBPROCESS_FAILED/TIMEOUT at
  that seam (e.g., catastrophic interpreter crash before vad_cli.main() starts),
  the raw message (potentially containing the vad_cli module path) is forwarded
  to the envelope unfiltered — non-symmetric with the silencedetect path.

  This seam must be sanitised symmetrically: detect.py:212 needs a try/except
  guard that replaces the message with SUBPROCESS_SAFE_MESSAGE, matching the
  silencedetect seam at detect.py:156-168.

  This FAILS today: VAD run() forwards the raw message verbatim.

CR L-2: import now targets clipwright.process.SUBPROCESS_SAFE_MESSAGE (the
  consolidated public constant) rather than the private clipwright_silence.vad_cli
  SUBPROCESS_SAFE_MESSAGE. The test verifies the canonical source so a
  future value change in the core constant is caught here.

Mocking policy (no real ffmpeg/vad_cli run):
  - Patch clipwright_silence.detect.inspect_media to supply MediaInfo so
    execution reaches the run() calls.
  - Patch clipwright_silence.detect.resolve_tool to avoid PATH dependency
    (silencedetect path only).
  - Patch clipwright_silence.detect.run to raise ClipwrightError(SUBPROCESS_FAILED)
    with a message embedding an absolute path, mirroring what core run() produces.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from clipwright.errors import ClipwrightError, ErrorCode

# CR L-2: Import the consolidated PUBLIC constant from clipwright.process
# (the canonical source after DRY consolidation of detect.py:44 / vad_cli.py:39).
# This fails today because process.py does not yet export SUBPROCESS_SAFE_MESSAGE.
from clipwright.process import SUBPROCESS_SAFE_MESSAGE
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_silence.schemas import DetectSilenceOptions

# Absolute path that must NOT leak into the error envelope. Kept distinct from
# tmp_path so a substring match unambiguously proves the raw stderr leaked.
_LEAKED_ABS_PATH = "/abs/secret/path/to/input.mp4"

# Raw ffmpeg stderr summary as core run() would embed it on a non-zero exit
# (process.py:120-124). The absolute input path is part of the message.
_RAW_SUBPROCESS_MESSAGE = f"ffmpeg failed: {_LEAKED_ABS_PATH} No such file or directory"


def _make_media_info(
    path: str,
    *,
    duration_sec: float = 10.0,
    rate: float = 30.0,
) -> MediaInfo:
    """Construct a MediaInfo with one video + one audio stream and a duration.

    Used as the inspect_media mock return value so detect_silence reaches the
    silencedetect run() call.
    """
    streams = [
        StreamInfo(index=0, codec_type="video", codec_name="h264"),
        StreamInfo(index=1, codec_type="audio", codec_name="aac"),
    ]
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=duration_sec * rate, rate=rate),
        streams=streams,
        bit_rate=8_000_000,
    )


def _opts() -> DetectSilenceOptions:
    """Default silencedetect options (backend defaults to silencedetect)."""
    return DetectSilenceOptions(
        silence_threshold_db=-30.0,
        min_silence_duration=0.5,
        padding=0.0,
        min_keep_duration=0.0,
    )


def _opts_vad() -> DetectSilenceOptions:
    """DetectSilenceOptions with backend='vad' to exercise the VAD branch."""
    return DetectSilenceOptions(
        silence_threshold_db=-30.0,
        min_silence_duration=0.5,
        padding=0.0,
        min_keep_duration=0.0,
        backend="vad",
    )


def _fake_run_subprocess_failed(cmd: list[str], **kwargs: Any) -> Any:
    """Mock run() that raises SUBPROCESS_FAILED with a path-embedding message.

    Mirrors core process.run(): on a non-zero ffmpeg exit it raises
    ClipwrightError(SUBPROCESS_FAILED) whose message is built from raw ffmpeg
    stderr (which can contain absolute input paths).
    """
    raise ClipwrightError(
        code=ErrorCode.SUBPROCESS_FAILED,
        message=_RAW_SUBPROCESS_MESSAGE,
        hint="Check that ffmpeg can read the input file.",
    )


class TestSilencedetectStderrSafe:
    """SR L-1: the silencedetect SUBPROCESS_FAILED path must not leak raw stderr.

    The VAD branch already substitutes SUBPROCESS_SAFE_MESSAGE; the
    silencedetect branch must do the same so absolute input paths from core
    ffmpeg stderr never reach the MCP error envelope.
    """

    def test_subprocess_failed_does_not_leak_absolute_path(
        self, tmp_path: Path
    ) -> None:
        """The error envelope message must not contain the leaked absolute path."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(media)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_run_subprocess_failed,
            ),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.SUBPROCESS_FAILED
        error_msg = result["error"]["message"]
        # The raw ffmpeg stderr (with absolute input path) must NOT be forwarded.
        assert _LEAKED_ABS_PATH not in error_msg, (
            "Raw ffmpeg stderr (absolute path) leaked into the error envelope; "
            "the silencedetect branch must substitute a generic message like "
            "the VAD branch does."
        )

    def test_subprocess_failed_uses_sanitised_message(self, tmp_path: Path) -> None:
        """The error message must mirror the VAD branch's SUBPROCESS_SAFE_MESSAGE."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(media)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_run_subprocess_failed,
            ),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.SUBPROCESS_FAILED
        error_msg = result["error"]["message"]
        # Mirror the VAD branch: the sanitised generic message must be present.
        assert SUBPROCESS_SAFE_MESSAGE in error_msg, (
            "The silencedetect branch must substitute the sanitised "
            f"{SUBPROCESS_SAFE_MESSAGE!r} message (as the VAD branch does) "
            "instead of forwarding raw ffmpeg stderr."
        )


# ===========================================================================
# SR L-1 [SR-R-001] — VAD path symmetry (NEW — deterministic-Red today)
# ===========================================================================

# Absolute path for the VAD path leak test; kept distinct from _LEAKED_ABS_PATH
# to avoid false positives between the two test classes.
_VAD_LEAKED_ABS_PATH = "/abs/secret/path/to/input.mp4"

# Raw message that core run() would embed when the VAD CLI subprocess exits
# non-zero before vad_cli.main() starts (catastrophic spawn failure).
_VAD_RAW_SUBPROCESS_MESSAGE = (
    f"Command failed with exit code 1: vad_cli spawn error {_VAD_LEAKED_ABS_PATH}"
)


class TestVadPathStderrSafe:
    """SR L-1 [SR-R-001] symmetry: the VAD run() seam (detect.py:212) must also
    sanitise SUBPROCESS_FAILED/TIMEOUT before forwarding to the envelope.

    The silencedetect path has had the sanitisation seam at detect.py:156-168
    since the SR L-1 fix.  The VAD path calls core run() directly at detect.py:212
    to spawn vad_cli as a subprocess.  If that run() raises SUBPROCESS_FAILED or
    SUBPROCESS_TIMEOUT (e.g., vad_cli import crashes, interpreter OOM, etc.),
    the raw message is forwarded to the envelope unfiltered — non-symmetric.

    This class verifies that detect.py:212 gains a matching try/except guard so
    that the envelope never sees raw stderr/paths from the VAD spawn.

    Red classification: deterministic-Red today — the VAD run() at detect.py:212
    has no sanitisation seam, so the raw path leaks.
    """

    def test_vad_subprocess_failed_does_not_leak_absolute_path(
        self, tmp_path: Path
    ) -> None:
        """VAD run() SUBPROCESS_FAILED must not forward the absolute path to the envelope.

        Arrange:
          - inspect_media mock returns valid MediaInfo (video + audio + duration).
          - clipwright_silence.detect.run mock raises ClipwrightError(SUBPROCESS_FAILED)
            with a message embedding _VAD_LEAKED_ABS_PATH, matching what core run()
            produces from raw subprocess stderr.
          - detect_silence is called with backend='vad' to reach the VAD branch.
        Act:   detect_silence(media, output, opts_vad).
        Assert: result["error"]["message"] does NOT contain _VAD_LEAKED_ABS_PATH.

        This FAILS today: the VAD run() at detect.py:212 has no sanitisation seam.
        """
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(media)

        def _fake_vad_run_subprocess_failed(cmd: list[str], **kwargs: Any) -> Any:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=_VAD_RAW_SUBPROCESS_MESSAGE,
                hint="Check that the Python interpreter can spawn vad_cli.",
            )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_vad_run_subprocess_failed,
            ),
        ):
            result = detect_silence(media, output, _opts_vad())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.SUBPROCESS_FAILED
        error_msg = result["error"]["message"]
        assert _VAD_LEAKED_ABS_PATH not in error_msg, (
            f"Absolute path {_VAD_LEAKED_ABS_PATH!r} leaked into the VAD branch "
            "error envelope; detect.py:212 must have a sanitisation seam symmetric "
            "with the silencedetect path at detect.py:156-168."
        )

    def test_vad_subprocess_failed_uses_sanitised_message(self, tmp_path: Path) -> None:
        """VAD run() SUBPROCESS_FAILED envelope message must contain SUBPROCESS_SAFE_MESSAGE.

        Arrange:
          - Same mocking as test_vad_subprocess_failed_does_not_leak_absolute_path.
        Act:   detect_silence(media, output, opts_vad).
        Assert: result["error"]["message"] contains SUBPROCESS_SAFE_MESSAGE.

        This FAILS today: the raw message (not the safe message) is forwarded.
        """
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(media)

        def _fake_vad_run_subprocess_failed(cmd: list[str], **kwargs: Any) -> Any:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=_VAD_RAW_SUBPROCESS_MESSAGE,
                hint="Check that the Python interpreter can spawn vad_cli.",
            )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_vad_run_subprocess_failed,
            ),
        ):
            result = detect_silence(media, output, _opts_vad())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.SUBPROCESS_FAILED
        error_msg = result["error"]["message"]
        assert SUBPROCESS_SAFE_MESSAGE in error_msg, (
            "The VAD branch must substitute the sanitised "
            f"{SUBPROCESS_SAFE_MESSAGE!r} message when core run() raises "
            "SUBPROCESS_FAILED at the vad_cli spawn seam (detect.py:212)."
        )
