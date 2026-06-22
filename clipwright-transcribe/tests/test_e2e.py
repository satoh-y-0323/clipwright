"""test_e2e.py — clipwright-transcribe real-binary end-to-end tests.

No mocks are used; real whisper.cpp / ffmpeg binaries drive the
transcribe -> render pipeline.

Verification flow (§6/§8, TR-AD-10):
  ① Generate an mp4 from ffmpeg testsrc + libflite TTS audio (DC-AS-002)
  ② Run clipwright_transcribe; expect SRT/VTT/OTIO to be produced (success
     condition 1)
  ③ Verify that the generated OTIO is handled by render_timeline(dry_run=True)
     with ok:True (DC-GP-004)
  ④ spike validation: confirm that the real binary JSON output matches the
     hypothetical fixture (DC-GP-001)

Run prerequisites (DC-AS-004, DC-AS-006):
  - CLIPWRIGHT_WHISPER (whisper binary)
  - CLIPWRIGHT_WHISPER_MODEL (ggml model file)
  - CLIPWRIGHT_FFMPEG (required for WAV extraction and test media generation)
  - CLIPWRIGHT_FFPROBE (required for render_timeline dry_run probe)
  All tests are skipped when any of these env vars is absent.

Note: e2e tests call subprocess directly to invoke real binaries. This is an
  intentional exception to the production code process.run discipline (e2e test
  infrastructure).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest
from clipwright_render.render import render_timeline
from clipwright_render.schemas import RenderOptions

from clipwright_transcribe.server import mcp
from clipwright_transcribe.transcribe import LANG_AUTO_FLAG, WHISPER_BINARY_NAME

# ===========================================================================
# Skip condition check (DC-AS-004)
# ===========================================================================

_WHISPER_BIN = os.environ.get("CLIPWRIGHT_WHISPER")
_WHISPER_MODEL = os.environ.get("CLIPWRIGHT_WHISPER_MODEL")
_FFMPEG_BIN = os.environ.get("CLIPWRIGHT_FFMPEG") or shutil.which("ffmpeg")
_FFPROBE_BIN = os.environ.get("CLIPWRIGHT_FFPROBE") or shutil.which("ffprobe")

_SKIP_REASON_PARTS: list[str] = []
if not _WHISPER_BIN:
    _SKIP_REASON_PARTS.append("CLIPWRIGHT_WHISPER is not set")
if not _WHISPER_MODEL:
    _SKIP_REASON_PARTS.append("CLIPWRIGHT_WHISPER_MODEL is not set")
if not _FFMPEG_BIN:
    _SKIP_REASON_PARTS.append("CLIPWRIGHT_FFMPEG is not set and ffmpeg not on PATH")
if not _FFPROBE_BIN:
    _SKIP_REASON_PARTS.append("CLIPWRIGHT_FFPROBE is not set and ffprobe not on PATH")

_SKIP_E2E = bool(_SKIP_REASON_PARTS)
_SKIP_E2E_REASON = (
    "Real-binary e2e requires the following env vars: "
    + ", ".join(_SKIP_REASON_PARTS)
    + ". "
    "Set CLIPWRIGHT_WHISPER / CLIPWRIGHT_WHISPER_MODEL / CLIPWRIGHT_FFMPEG / "
    "CLIPWRIGHT_FFPROBE and run again."
    if _SKIP_REASON_PARTS
    else ""
)

# ===========================================================================
# Hypothetical schema from fixtures/README.md (for spike validation; DC-GP-001)
# ===========================================================================

FIXTURES_DIR = Path(__file__).parent / "fixtures"
_HYPOTHETICAL_BINARY_NAME = "whisper-cli"
_HYPOTHETICAL_LANG_AUTO_FLAG = ["-l", "auto"]


# ===========================================================================
# Helpers
# ===========================================================================


def _get_ffmpeg() -> str:
    """Return the ffmpeg binary path (assumed to be called only after skip checks)."""
    assert _FFMPEG_BIN is not None
    return _FFMPEG_BIN


def _get_ffprobe() -> str:
    """Return the ffprobe binary path (assumed to be called only after skip checks)."""
    assert _FFPROBE_BIN is not None
    return _FFPROBE_BIN


def _probe_flite_duration(ffmpeg: str, text: str) -> float:
    """Measure the duration of audio synthesised by libflite TTS.

    Calls pytest.skip when libflite is not available in the ffmpeg build.
    Falling back with a hardcoded value is explicitly avoided (CR-E-002).
    """
    # SR H-1: Guard against lavfi filter injection before expanding text into the
    # filter string. Only ASCII alphanumerics and spaces are permitted (intentional
    # e2e infrastructure restriction; special chars such as ':' '\\'' '[' ']' would
    # cause filter injection).
    if not re.fullmatch(r"[A-Za-z0-9 ]+", text):
        pytest.skip(
            f"speech_text contains lavfi special characters; skipping: {text!r}"
        )
    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"flite=text='{text}':voice=kal16",
                "-t",
                "10",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("ffmpeg libflite duration probe timed out (30 s).")
    if result.returncode != 0:
        pytest.skip(
            f"ffmpeg libflite is not available (returncode={result.returncode}). "
            "A libflite-enabled ffmpeg build is required. "
            f"stderr: {result.stderr[-200:]}"
        )
    m = re.search(r"time=(\d+):(\d+):([0-9.]+)", result.stderr)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return h * 3600 + mi * 60 + s
    pytest.skip(
        "time= pattern not found in ffmpeg stderr. "
        "libflite TTS may not be functioning correctly. "
        f"text={text!r}, stderr={result.stderr[-200:]}"
    )


def _make_tts_video(
    ffmpeg: str, output: Path, speech_text: str = "hello world"
) -> float:
    """Generate an mp4 that multiplexes testsrc video and libflite TTS audio
    (DC-AS-002).

    A video track is included to satisfy render's has_video requirement
    (avoids UNSUPPORTED_OPERATION). Audio-only sources cause render to fail.

    Args:
        ffmpeg: Path to the ffmpeg binary.
        output: Output mp4 path.
        speech_text: Text synthesised by libflite TTS (English; whisper-readable).

    Returns:
        Total duration of the generated media in seconds.
    """
    # SR H-1: Same guard as _probe_flite_duration (double-check for safety margin).
    if not re.fullmatch(r"[A-Za-z0-9 ]+", speech_text):
        pytest.skip(
            f"speech_text contains lavfi special characters; skipping: {speech_text!r}"
        )
    speech_dur = _probe_flite_duration(ffmpeg, speech_text)

    # filter_complex multiplexes testsrc video with flite TTS audio.
    fc = (
        # Generate TTS audio and normalise sample rate
        f"flite=text='{speech_text}':voice=kal16,"
        f"atrim=start=0:end={speech_dur:.4f},"
        f"asetpts=PTS-STARTPTS,"
        f"aresample=16000[audio_out]"
    )

    cmd = [
        ffmpeg,
        "-y",
        # Video source: testsrc
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=320x240:rate=25:duration={speech_dur:.4f}",
        "-filter_complex",
        fc,
        "-map",
        "0:v",
        "-map",
        "[audio_out]",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        "-shortest",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, (
        f"TTS test media generation failed: {result.stderr[-300:]}"
    )
    return speech_dur


def _probe_whisper_json(
    whisper: str,
    model_path: str,
    wav_path: str,
    tmp_prefix: str,
) -> dict[str, Any]:
    """Run whisper with -oj and return the generated JSON (for spike validation).

    Confirms that -of <prefix> produces <prefix>.json (DC-AM-003).
    """
    cmd = [
        whisper,
        "-m",
        model_path,
        "-f",
        wav_path,
        "-oj",
        "-of",
        tmp_prefix,
        *LANG_AUTO_FLAG,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, (
        f"spike validation whisper run failed: {result.stderr[-300:]}"
    )
    json_path = tmp_prefix + ".json"
    assert Path(json_path).exists(), (
        f"-of <prefix> did not produce <prefix>.json. Actual path: {json_path}"
    )
    with open(json_path, encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


def _extract_wav_for_spike(
    ffmpeg: str,
    media: str,
    wav_path: str,
) -> None:
    """Extract a 16 kHz mono WAV (preprocessing for spike validation)."""
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
        "16000",
        "-ac",
        "1",
        "-y",
        wav_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"WAV extraction failed: {result.stderr[-300:]}"


# ===========================================================================
# Tests
# ===========================================================================


@pytest.mark.integration
@pytest.mark.skipif(_SKIP_E2E, reason=_SKIP_E2E_REASON)
def test_transcribe_e2e(tmp_path: Path) -> None:
    """e2e: transcribe -> SRT/VTT/OTIO generation and render dry_run integration.

    Verification points (DC-GP-001/002/004, §6, success conditions 1/2):
      - ① Generate TTS media (testsrc video + libflite audio mp4) in the same temp dir
           (DC-AS-002/006)
      - ② clipwright_transcribe produces ok=True, SRT/VTT/OTIO (success condition 1)
      - ③ SRT file contains non-empty text (proves real speech was transcribed)
      - ④ Generated OTIO is handled by render_timeline(dry_run=True) with ok:True
           (DC-GP-004)
      - ⑤ spike validation (DC-GP-001/DC-AM-002/003): confirm real binary JSON schema,
           -of output name, -l auto acceptance, and WHISPER_BINARY_NAME constant match
    """
    assert _WHISPER_BIN is not None
    assert _WHISPER_MODEL is not None
    ffmpeg = _get_ffmpeg()
    ffprobe = _get_ffprobe()  # noqa: F841  # used by render dry_run probe

    # ① Generate TTS test media (DC-AS-002)
    # DC-AS-006: place mp4 and output (.otio) in the same temp directory to satisfy
    #            render's source-within-timeline-dir boundary constraint.
    source = tmp_path / "tts_source.mp4"
    speech_text = "hello world"
    _make_tts_video(ffmpeg, source, speech_text=speech_text)
    assert source.exists(), "TTS test media was not generated"

    # DC-AS-006: output must be in the same directory as media
    otio_path = tmp_path / "out.otio"

    # ② Transcribe (success condition 1)
    _content, result = asyncio.run(
        mcp.call_tool(
            "clipwright_transcribe",
            {
                "media": str(source),
                "output": str(otio_path),
            },
        )
    )

    assert result["ok"] is True, f"clipwright_transcribe failed: {result}"

    # SRT/VTT/OTIO must exist
    srt_path = tmp_path / "out.srt"
    vtt_path = tmp_path / "out.vtt"
    assert otio_path.exists(), "OTIO timeline was not generated"
    assert srt_path.exists(), "SRT file was not generated"
    assert vtt_path.exists(), "VTT file was not generated"

    # Verify envelope shape
    assert "summary" in result
    assert "data" in result
    data = result["data"]
    assert "segment_count" in data
    assert "language" in data
    assert "total_duration_seconds" in data
    artifacts = result.get("artifacts", [])
    artifact_formats = {a["format"] for a in artifacts}
    assert "otio" in artifact_formats, "artifacts does not contain otio"
    assert "srt" in artifact_formats, "artifacts does not contain srt"
    assert "vtt" in artifact_formats, "artifacts does not contain vtt"

    # ③ SRT contains non-empty text (proves real speech was transcribed)
    srt_content = srt_path.read_text(encoding="utf-8")
    vtt_content = vtt_path.read_text(encoding="utf-8")
    # SRT must be non-empty; VTT must contain the WEBVTT header
    assert srt_content.strip() != "", (
        "SRT file is empty. whisper could not transcribe the audio. "
        f"segment_count={data['segment_count']}, language={data['language']}"
    )
    assert "WEBVTT" in vtt_content, "VTT file does not contain the WEBVTT header"

    # ④ render integration (DC-GP-004): dry_run must return ok:True
    # Use ok:True to catch all failure modes rather than checking for absent error codes.
    render_output = tmp_path / "render_out.mp4"
    render_result = render_timeline(
        timeline=str(otio_path),
        output=str(render_output),
        options=RenderOptions(),
        dry_run=True,
    )
    assert render_result["ok"] is True, (
        f"render_timeline(dry_run=True) failed: {render_result}. "
        "One of UNSUPPORTED_OPERATION/PATH_NOT_ALLOWED/INVALID_INPUT may have occurred."
    )

    # ⑤ spike validation (DC-GP-001/DC-AM-002/003)
    # Confirm that the WHISPER_BINARY_NAME constant matches the actual binary name
    # (DC-AS-003-R).
    # Compare stems only: on Windows the resolved binary has a .exe suffix while
    # WHISPER_BINARY_NAME is the extension-less cross-platform name (PATHEXT handles
    # the .exe lookup when using PATH; CLIPWRIGHT_WHISPER may be a full path with .exe).
    actual_binary_stem = Path(_WHISPER_BIN).stem
    expected_binary_stem = Path(WHISPER_BINARY_NAME).stem
    if actual_binary_stem != expected_binary_stem:
        # Record the discrepancy but do not skip.
        pytest.fail(
            f"WHISPER_BINARY_NAME constant stem ('{expected_binary_stem}') does not "
            f"match the actual binary stem ('{actual_binary_stem}'). "
            "A fix in impl-transcribe is required. "
            "Update the WHISPER_BINARY_NAME constant in transcribe.py to match the "
            "actual binary name."
        )

    # Confirm that -oj -of <prefix> produces <prefix>.json (DC-AM-003)
    # and validate the JSON schema (DC-AM-002/DC-GP-001).
    with tempfile.TemporaryDirectory() as spike_tmp:
        wav_path = os.path.join(spike_tmp, "spike_audio.wav")
        spike_prefix = os.path.join(spike_tmp, "spike_out")

        _extract_wav_for_spike(ffmpeg, str(source), wav_path)
        whisper_json = _probe_whisper_json(
            _WHISPER_BIN,
            _WHISPER_MODEL,
            wav_path,
            spike_prefix,
        )

    # JSON schema validation (DC-GP-001/DC-AM-002/003)
    transcription = whisper_json.get("transcription")
    if transcription is None:
        pytest.fail(
            "Real binary JSON does not contain a 'transcription' key. "
            "The hypothetical schema (fixtures/README.md) has diverged. "
            "A fix in impl-contract / impl-transcribe is required. "
            f"Top-level keys in actual JSON: {list(whisper_json.keys())}"
        )

    # Only validate internal schema when transcription is non-empty
    # (libflite TTS may produce audio that whisper cannot transcribe; empty is
    # acceptable).
    if isinstance(transcription, list) and len(transcription) > 0:
        first_seg = transcription[0]
        offsets = first_seg.get("offsets")
        assert offsets is not None, (
            "transcription[0] does not contain an 'offsets' key. "
            f"Actual segment keys: {list(first_seg.keys())}"
        )
        assert "from" in offsets, f"offsets does not contain a 'from' key: {offsets}"
        assert "to" in offsets, f"offsets does not contain a 'to' key: {offsets}"
        # from/to must be integer milliseconds (DC-GP-001)
        assert isinstance(offsets["from"], int), (
            f"offsets.from is not an integer: {offsets['from']!r} "
            f"(type={type(offsets['from']).__name__})"
        )
        assert isinstance(offsets["to"], int), (
            f"offsets.to is not an integer: {offsets['to']!r} "
            f"(type={type(offsets['to']).__name__})"
        )
        assert "text" in first_seg, (
            f"transcription[0] does not contain a 'text' key: {list(first_seg.keys())}"
        )

    # Verify -l auto flag (LANG_AUTO_FLAG) parsing.
    # returncode=0 means -l auto was accepted by the binary.
    # (spike validation step ④: -l auto must not cause an error)
    # CR M-4: LANG_AUTO_FLAG is list[str]; .split() is not needed (DC-AM-002).
    assert len(LANG_AUTO_FLAG) == 2, (  # noqa: PLR2004
        f"LANG_AUTO_FLAG has an unexpected shape: {LANG_AUTO_FLAG!r}"
    )
    assert LANG_AUTO_FLAG[0] == "-l", (
        f"LANG_AUTO_FLAG[0] is not '-l': {LANG_AUTO_FLAG!r}"
    )
    assert LANG_AUTO_FLAG[1] == "auto", (
        f"LANG_AUTO_FLAG[1] is not 'auto': {LANG_AUTO_FLAG!r}"
    )
    # Confirm match with hypothetical constant (DC-AM-002 constant isolation
    # validation).
    assert LANG_AUTO_FLAG == _HYPOTHETICAL_LANG_AUTO_FLAG, (
        f"LANG_AUTO_FLAG does not match the hypothetical constant: "
        f"{LANG_AUTO_FLAG!r} != {_HYPOTHETICAL_LANG_AUTO_FLAG!r}"
    )


@pytest.mark.integration
@pytest.mark.skipif(_SKIP_E2E, reason=_SKIP_E2E_REASON)
def test_transcribe_backend_e2e(tmp_path: Path) -> None:
    """e2e: backend/realtime surface verification with a real whisper-cli (CPU build).

    Verification points (AC-4 / ADR-7'(f)):
      - ① Generate TTS media (same pattern as test_transcribe_e2e)
      - ② Run clipwright_transcribe and obtain ok=True result
      - ③ data["backend"]["device"] is in the known value set {"cuda","metal","cpu","unknown"}
      - ④ On a CPU build, device must be "cpu" or "unknown" (both accepted).
           "cpu" is the expected outcome because the real stderr emits
           "whisper_backend_init_gpu: no GPU found" on a CPU build (F2).
      - ⑤ data["whisper_wall_seconds"] > 0 (real whisper always takes non-zero wall time)
      - ⑥ data["realtime_factor"] > 0 and is not None
           (real transcription never hits the wall<=0 guard)
      - ⑦ data["backend"]["detail"] contains no path-like token (CWE-209 live check).
           A path-like token is defined as a whitespace-separated token that contains
           "/" or "\\" (backslash). The real stderr line 1 exposes the model absolute
           path (F4); if sanitisation is broken this assert catches it.
      - ⑧ use-gpu trap regression guard (F3): even though real stderr emits
           "use gpu = 1" / "gpu_device = 0" on a CPU build, device must be "cpu" or
           "unknown" — NOT "cuda" or "metal". This is verified by the same
           device-value check in ④ (e2e cannot observe raw stderr directly; the device
           result is the observable proxy for the trap being avoided).
           See ADR-1'(F3): "use gpu" / "gpu_device" lines are request params, not
           backend-init result lines, and must be excluded before pattern matching.
    """
    assert _WHISPER_BIN is not None
    assert _WHISPER_MODEL is not None
    ffmpeg = _get_ffmpeg()

    # ① Generate TTS test media (same pattern as test_transcribe_e2e)
    source = tmp_path / "backend_tts_source.mp4"
    speech_text = "hello backend"
    _make_tts_video(ffmpeg, source, speech_text=speech_text)
    assert source.exists(), "TTS test media was not generated for backend e2e"

    otio_path = tmp_path / "backend_out.otio"

    # ② Transcribe with real whisper-cli
    _content, result = asyncio.run(
        mcp.call_tool(
            "clipwright_transcribe",
            {
                "media": str(source),
                "output": str(otio_path),
            },
        )
    )

    assert result["ok"] is True, f"clipwright_transcribe failed: {result}"
    assert "data" in result, "result has no 'data' key"
    data = result["data"]

    # ③ device is in the known value set
    _KNOWN_DEVICES = {"cuda", "metal", "cpu", "unknown"}
    assert "backend" in data, (
        "data has no 'backend' key. "
        "The backend/realtime surface is not yet implemented (expected Red failure)."
    )
    backend = data["backend"]
    assert isinstance(backend, dict), f"data['backend'] is not a dict: {backend!r}"
    assert "device" in backend, f"backend has no 'device' key: {backend!r}"
    device = backend["device"]
    assert device in _KNOWN_DEVICES, (
        f"data['backend']['device']={device!r} is not in {_KNOWN_DEVICES}. "
        "Unexpected device value returned."
    )

    # ④ On a CPU build, device must be "cpu" or "unknown" (NOT "cuda" / "metal").
    # "cpu" is expected because the real stderr emits
    # "whisper_backend_init_gpu: no GPU found" (F2 confirmed on this machine).
    # ⑧ use-gpu trap regression guard: the same check ensures that "use gpu = 1" in
    # real stderr does NOT cause device to be misdetected as "cuda" or "metal" (F3).
    _CPU_BUILD_ALLOWED = {"cpu", "unknown"}
    assert device in _CPU_BUILD_ALLOWED, (
        f"CPU build produced device={device!r}. "
        "Expected 'cpu' (or 'unknown' as fallback). "
        "If 'cuda' or 'metal': the use-gpu trap (F3) may have caused misdetection. "
        "Check that 'use gpu = 1' / 'gpu_device' lines are excluded before matching."
    )

    # ⑤ whisper_wall_seconds > 0 (real transcription always takes non-zero time)
    assert "whisper_wall_seconds" in data, (
        "data has no 'whisper_wall_seconds' key. "
        "The backend/realtime surface is not yet implemented (expected Red failure)."
    )
    wall = data["whisper_wall_seconds"]
    assert isinstance(wall, (int, float)), (
        f"whisper_wall_seconds is not numeric: {wall!r}"
    )
    assert wall > 0, (
        f"whisper_wall_seconds={wall} is not positive. "
        "Real whisper-cli always takes non-zero wall time."
    )

    # ⑥ realtime_factor > 0 and not None
    assert "realtime_factor" in data, (
        "data has no 'realtime_factor' key. "
        "The backend/realtime surface is not yet implemented (expected Red failure)."
    )
    rtf = data["realtime_factor"]
    assert rtf is not None, (
        "realtime_factor is None. "
        "Real whisper-cli always takes positive wall time so realtime_factor "
        "should never be None (wall<=0 guard should not be triggered)."
    )
    assert isinstance(rtf, (int, float)), f"realtime_factor is not numeric: {rtf!r}"
    assert rtf > 0, (
        f"realtime_factor={rtf} is not positive. "
        "Real whisper-cli always takes non-zero wall time."
    )

    # ⑦ detail contains no path-like token (CWE-209 live check; F4)
    # A path-like token is a whitespace-separated token containing "/" or "\\".
    # The real stderr line 1 exposes the model absolute path (F4); sanitisation
    # must prevent it from leaking into data["backend"]["detail"].
    assert "detail" in backend, f"backend has no 'detail' key: {backend!r}"
    detail = backend["detail"]
    assert isinstance(detail, str), f"backend['detail'] is not a str: {detail!r}"
    tokens_with_path_sep = [tok for tok in detail.split() if "/" in tok or "\\" in tok]
    assert not tokens_with_path_sep, (
        f"backend['detail'] contains path-like token(s): {tokens_with_path_sep!r}. "
        f"Full detail value: {detail!r}. "
        "CWE-209: model absolute path must not leak into data['backend']['detail']. "
        "Check _sanitize_detail() and _DEVICE_DETAIL_LABELS in transcribe.py."
    )


@pytest.mark.integration
@pytest.mark.skipif(not _SKIP_E2E, reason="env is set; skip behaviour test not needed")
def test_e2e_skipped_when_env_not_set() -> None:
    """Placeholder confirming that e2e is skipped when env vars are absent.

    This test itself runs only when env vars ARE set (inverse condition).
    The actual skip behaviour is guaranteed by pytest's skipif mechanism.
    This test exists to confirm that no collection errors occur.
    """
    # Reaching this test means env is set; the skip-check test is therefore unnecessary.
    pytest.skip("env is set; skip behaviour confirmation test is not needed")
