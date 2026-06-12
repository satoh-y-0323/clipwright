"""vad_cli.py — Separate-process small CLI for the Silero VAD backend.

Not imported by the MCP server process (§2.4 subprocess loose coupling).
detect.py spawns this as a separate process via
sys.executable -m clipwright_silence.vad_cli.

CLI contract (§7.1 unified):
  - main(argv) catches all exceptions at the top level, always writes stdout JSON,
    and returns 0.
  - Success: {"speech_segments": [[start_sec, end_sec], ...]}
  - Error:   {"error": {"code": str, "message": str, "hint": str}}
  - stdout is JSON only. Logs and progress go to stderr.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import sys
import tempfile
import wave
from typing import Any

from clipwright.cli_io import force_utf8_io
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.process import resolve_tool, run, safe_subprocess_message

# Fixed sample rate (§7.3)
_SAMPLE_RATE = 16000
# pip install hint string
_VAD_INSTALL_HINT = (
    "Install VAD dependencies with `pip install 'clipwright-silence[vad]'`."
)


def _error_output(code: str, message: str, hint: str) -> None:
    """Write error JSON to stdout.

    The caller must sanitize path information before passing it here.
    """
    result: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "hint": hint,
        }
    }
    print(json.dumps(result, ensure_ascii=False), file=sys.stdout)


def _extract_pcm(ffmpeg: str, media: str, output_path: str, timeout: float) -> None:
    """Write 16kHz mono s16le PCM to a temporary file using ffmpeg.

    Executed with shell=False and argument array only (subprocess discipline).
    """
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-i",
        media,
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(_SAMPLE_RATE),
        "-ac",
        "1",
        "-y",
        output_path,
    ]
    run(cmd, timeout=timeout)


def _load_audio_as_float32(
    pcm_path: str,
) -> tuple[Any, int]:
    """Read a PCM WAV file and return a float32 numpy array and sample_rate.

    Normalizes int16 -> float32 (/32768.0).

    Precondition: This function must always be called via main().
    main() lazily imports numpy and registers it in sys.modules, so the import
    inside this function is effectively a cache lookup (NF-M-2: document precondition).
    Calling directly from tests or utilities breaks the loose-coupling intent
    (numpy is kept out of the server process; only the separate vad_cli subprocess
    imports it).
    """
    import numpy as np  # See docstring (cached in sys.modules by main())

    with wave.open(pcm_path, "rb") as wf:
        n_frames = wf.getnframes()
        sample_rate = wf.getframerate()
        raw = wf.readframes(n_frames)

    audio_int16 = np.frombuffer(raw, dtype=np.int16)
    audio_float32: Any = audio_int16.astype(np.float32) / 32768.0
    return audio_float32, sample_rate


def main(argv: list[str] | None = None) -> int:
    """VAD CLI entry point.

    Catches all exceptions at the top level, writes JSON to stdout,
    and returns 0 (§7.1).

    Args:
        argv: Command-line argument list. Uses sys.argv[1:] if None.

    Returns:
        Exit code (always 0).
    """
    force_utf8_io()

    # Lazily import numpy inside main to keep the server process loosely coupled
    # (numpy is only needed inside this separate subprocess; the MCP server never
    # imports it). Pre-import here to cache in sys.modules so that
    # _load_audio_as_float32 finds it without a redundant reimport.
    # noqa: F401 = suppress lint warning for not referencing directly (NF-L-2).
    import numpy as np  # noqa: F401

    # --- Parse arguments ---
    parser = argparse.ArgumentParser(
        description="Detect speech intervals using Silero VAD and output JSON to stdout."  # noqa: E501
    )
    parser.add_argument("--media", required=True, help="Input media file path")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Speech probability threshold (0.0-1.0, default: 0.5)",
    )
    parser.add_argument(
        "--min-speech",
        type=float,
        default=0.25,
        help="Minimum speech duration (seconds, default: 0.25)",
    )
    parser.add_argument(
        "--min-silence",
        type=float,
        default=0.1,
        help="Minimum silence duration between speech intervals (seconds, default: 0.1)",  # noqa: E501
    )
    parser.add_argument(
        "--media-duration",
        type=float,
        default=None,
        help=(
            "Total media duration (seconds). Used to calculate the inner"
            " ffmpeg timeout proportional to total duration (§7.7)."
            " Uses a safe default (60s) if omitted."
        ),
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # Catch SystemExit from argparse (--help or missing required args)
        _error_output(
            code=ErrorCode.INVALID_INPUT,
            message=f"Argument parsing failed: exit code {exc.code}",
            hint="Specify --media <path> as a required argument.",
        )
        return 0

    media: str = args.media
    threshold: float = args.threshold
    min_speech_sec: float = args.min_speech
    min_silence_sec: float = args.min_silence
    media_duration: float | None = args.media_duration

    try:
        # --- Lazy import of silero_vad (keep out of server process, §2.4) ---
        try:
            import silero_vad
        except ImportError:
            # SR L-2: str(exc) may contain internal paths; use fixed message
            _error_output(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="Failed to import silero_vad or onnxruntime",
                hint=_VAD_INSTALL_HINT,
            )
            return 0

        # --- Load Silero VAD model (before ffmpeg, to catch ImportError early) ---
        # load_silero_vad raises ImportError if onnxruntime is missing (§7.3)
        model = silero_vad.load_silero_vad(onnx=True)

        # --- Resolve ffmpeg (§7.2) ---
        ffmpeg = resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")

        # --- Extract 16kHz mono s16le PCM to a temp file using ffmpeg (§7.3) ---
        # Inner timeout must always be shorter than outer (§7.7)
        # max(60, ceil(total*4)) for outer; proportional for inner
        # When --media-duration given: max(30, ceil(total * 2))
        # When omitted: safe default of 60 seconds
        if media_duration is not None:
            ffmpeg_timeout = float(max(30, math.ceil(media_duration * 2)))
        else:
            ffmpeg_timeout = 60.0

        tmp_path: str = ""
        audio_float32: Any
        sample_rate: int

        # Open with delete=False to get the name, then delete in try/finally (§7.3)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
            tmp_path = tmp_file.name
        try:
            _extract_pcm(ffmpeg, media, tmp_path, timeout=ffmpeg_timeout)
            audio_float32, sample_rate = _load_audio_as_float32(tmp_path)
        finally:
            # Ensure deletion even on exception (§7.3)
            if tmp_path and os.path.exists(tmp_path):
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)

        # get_speech_timestamps returns sample-unit values
        # min_speech_duration_ms / min_silence_duration_ms are in milliseconds
        raw_segments: list[dict[str, Any]] = silero_vad.get_speech_timestamps(
            audio_float32,
            model,
            threshold=threshold,
            sampling_rate=sample_rate,
            min_speech_duration_ms=int(min_speech_sec * 1000),
            min_silence_duration_ms=int(min_silence_sec * 1000),
            return_seconds=False,
        )

        # Convert sample units -> seconds (built in ascending order)
        speech_segments: list[list[float]] = []
        for seg in sorted(raw_segments, key=lambda s: s["start"]):
            start_sec = float(seg["start"]) / sample_rate
            end_sec = float(seg["end"]) / sample_rate
            speech_segments.append([start_sec, end_sec])

        result: dict[str, Any] = {"speech_segments": speech_segments}
        print(json.dumps(result, ensure_ascii=False), file=sys.stdout)
        return 0

    except ClipwrightError as exc:
        # Catch ClipwrightError from core run() (SUBPROCESS_FAILED/TIMEOUT) and
        # resolve_tool DEPENDENCY_MISSING here (§7.1/§7.2)
        # SR M-1: SUBPROCESS_FAILED/TIMEOUT may embed ffmpeg stderr in message;
        # replace with generic message to prevent path leakage
        if exc.code in (ErrorCode.SUBPROCESS_FAILED, ErrorCode.SUBPROCESS_TIMEOUT):
            safe_message = safe_subprocess_message(exc)
        else:
            safe_message = exc.message
        _error_output(
            code=str(exc.code),
            message=safe_message,
            hint=exc.hint,
        )
        return 0

    except ImportError:
        # ImportError propagated from load_silero_vad etc. when onnxruntime is missing
        # SR L-2: str(exc) may contain internal paths; use fixed message
        _error_output(
            code=ErrorCode.DEPENDENCY_MISSING,
            message="Failed to import silero_vad or onnxruntime",
            hint=_VAD_INSTALL_HINT,
        )
        return 0

    except Exception:
        # Catch all unexpected exceptions and return error JSON (§7.1)
        # SR NF-L-1: str(exc) may contain internal paths; use fixed message.
        # Debug details are stderr-only; do not leak into stdout JSON (MCP response).
        import traceback

        traceback.print_exc(file=sys.stderr)
        _error_output(
            code=ErrorCode.INTERNAL,
            message="An unexpected error occurred in VAD CLI",
            hint="Please report with reproduction steps.",
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
