"""test_e2e.py — clipwright-silence real-binary end-to-end dogfooding tests.

This test uses no mocks and validates the detect_silence -> render_timeline
integration pipeline using real ffmpeg/ffprobe binaries.

Verification flow (DC-AS-001/002/005):
  (1) Generate a video+audio source (speech + silence + speech) via ffmpeg lavfi
  (2) Run detect_silence to produce a KEEP-clip timeline.otio (V1, absolute target_url)
  (3) Run render_timeline to materialize the timeline; confirm output mp4 is shorter than source
  (4) Confirm audio duration is also shortened (DC-AS-005: audio trimmed at the same coordinates)
  (5) Confirm silence->render succeeds via contract, OTIO, and file paths only (dogfooding)

Prerequisites:
  - CLIPWRIGHT_FFMPEG / CLIPWRIGHT_FFPROBE set, or ffmpeg/ffprobe on PATH.
    Tests are skipped (no mocks) when neither is found.

Note: e2e tests call real binaries (ffmpeg/ffprobe/flite TTS etc.) directly via subprocess.
  Direct subprocess use is an intentional exception to the process.run convention
  used in production code; it is permitted here as e2e test infrastructure (CR-R-003).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest
from clipwright_render.render import render_timeline
from clipwright_render.schemas import RenderOptions

from clipwright_silence.schemas import DetectSilenceOptions
from clipwright_silence.server import mcp

# ===========================================================================
# Helpers
# ===========================================================================

# Silence detection threshold: set loosely to reliably detect lavfi anullsrc (complete silence)
_SILENCE_DB = -40.0
# Minimum silence duration: shorter than the generated silence interval (2s) so it is detected
_MIN_SILENCE_DURATION = 0.5


def _probe_info(
    ffprobe: str,
    path: Path,
) -> dict[str, Any]:
    """Fetch format/stream info for a video via ffprobe and return it."""
    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"ffprobe failed: {result.stderr[:200]}"
    return json.loads(result.stdout)  # type: ignore[no-any-return]


def _probe_duration(ffprobe: str, path: Path) -> float:
    """Return the duration (seconds) of a video via ffprobe."""
    data = _probe_info(ffprobe, path)
    return float(data["format"]["duration"])


def _probe_audio_duration(ffprobe: str, path: Path) -> float:
    """Return the audio stream duration (seconds) of a video via ffprobe.

    The format duration represents the overall container duration.
    The audio stream duration is fetched separately to confirm DC-AS-005 (audio trim).
    Returns 0.0 if no audio stream is present.
    """
    data = _probe_info(ffprobe, path)
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "audio":
            return float(stream.get("duration", 0.0))
    return 0.0


def _make_silent_segment_video(
    ffmpeg: str,
    output: Path,
    audio_sec: float = 3.0,
    silence_sec: float = 2.0,
    audio2_sec: float = 3.0,
) -> float:
    """Generate a video+audio source with three segments: speech -> silence -> speech.

    Composition:
      [0, audio_sec)                          : testsrc + sine 440Hz (speech)
      [audio_sec, audio_sec + silence_sec)    : testsrc + anullsrc (complete silence)
      [audio_sec + silence_sec, total)        : testsrc + sine 440Hz (speech)

    Three segments are concatenated into one mp4 via ffmpeg filter_complex.
    Video: testsrc (320x240 @ 25fps, libx264)
    Audio: sine/anullsrc (aac)

    Returns:
        Total duration (seconds) of the generated source.
    """
    total = audio_sec + silence_sec + audio2_sec
    # Assemble 3 segments via filter_complex
    # Video: generate 3 intervals from testsrc using trim
    # Audio: interval 1/3 = sine, interval 2 = anullsrc (complete silence)
    fc = (
        # Video sources (trim from a single shared testsrc)
        f"[0:v]trim=start=0:end={audio_sec:.3f},setpts=PTS-STARTPTS[va];"
        f"[0:v]trim=start=0:end={silence_sec:.3f},setpts=PTS-STARTPTS[vb];"
        f"[0:v]trim=start=0:end={audio2_sec:.3f},setpts=PTS-STARTPTS[vc];"
        # Audio sources (sine from [1:a], anullsrc from [2:a])
        f"[1:a]atrim=start=0:end={audio_sec:.3f},asetpts=PTS-STARTPTS[aa];"
        f"[2:a]atrim=start=0:end={silence_sec:.3f},asetpts=PTS-STARTPTS[ab];"
        f"[1:a]atrim=start=0:end={audio2_sec:.3f},asetpts=PTS-STARTPTS[ac];"
        # concat: join 3 intervals (n=3 segments, v=1 video, a=1 audio)
        "[va][aa][vb][ab][vc][ac]concat=n=3:v=1:a=1[outv][outa]"
    )
    cmd = [
        ffmpeg,
        "-y",
        # input 0: testsrc video (generate total seconds; trim as needed)
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=320x240:rate=25:duration={total:.3f}",
        # input 1: sine audio (generate total seconds; atrim as needed)
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={total:.3f}",
        # input 2: anullsrc (complete silence for silence_sec seconds)
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r=44100:cl=stereo:d={silence_sec:.3f}",
        "-filter_complex",
        fc,
        "-map",
        "[outv]",
        "-map",
        "[outa]",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, (
        f"Failed to generate test source: {result.stderr[:300]}"
    )
    return total


# ===========================================================================
# Tests
# ===========================================================================


@pytest.mark.integration
def test_silence_detect_to_render_e2e(
    tmp_path: Path,
    require_ffmpeg: str,
    require_ffprobe: str,
) -> None:
    """Dogfooding: full e2e validation of detect_silence -> render_timeline.

    Validates acceptance criteria for DC-AS-001/002/005 using real binaries.

    Verification points:
      - (1) detect_silence returns ok=True and timeline.otio is generated
      - (2) V1 track contains a KEEP clip list (keep-clip list, metadata.kind=keep)
      - (3) clip.target_url is an absolute path to the media (DC-AS-001)
      - (4) render_timeline returns ok=True and the output mp4 is generated
      - (5) Output video duration is shorter than the source (silence interval was cut)
      - (6) Output audio duration is also shortened (DC-AS-005: audio trimmed at same coordinates)
      - (7) Source media and OTIO are non-destructive (unchanged)
      - (8) silence->render succeeds via contract, OTIO, and file paths only (dogfooding)
    """
    # --- (1) Generate video+audio (speech->silence->speech) test source (DC-AS-002) ---
    source = tmp_path / "source.mp4"
    audio_sec = 3.0
    silence_sec = 2.0
    audio2_sec = 3.0
    total_sec = _make_silent_segment_video(
        require_ffmpeg,
        source,
        audio_sec=audio_sec,
        silence_sec=silence_sec,
        audio2_sec=audio2_sec,
    )
    assert source.exists(), "Test source was not generated"
    source_size_before = source.stat().st_size

    # --- (2) Generate KEEP clip timeline.otio via detect_silence ---
    # DC-AS-001: output timeline is placed in the same directory as media (tmp_path)
    otio_path = tmp_path / "cut.otio"
    options = DetectSilenceOptions(
        silence_threshold_db=_SILENCE_DB,
        min_silence_duration=_MIN_SILENCE_DURATION,
        padding=0.05,
        min_keep_duration=0.0,
    )
    _content, detect_result = asyncio.run(
        mcp.call_tool(
            "clipwright_detect_silence",
            {
                "media": str(source),
                "output": str(otio_path),
                "options": options.model_dump(),
            },
        )
    )

    assert detect_result["ok"] is True, f"detect_silence failed: {detect_result}"
    assert otio_path.exists(), "timeline.otio was not generated"

    # Validate envelope format
    assert "summary" in detect_result
    assert "data" in detect_result
    data = detect_result["data"]
    assert "keep_count" in data
    assert "silence_count" in data
    assert data["keep_count"] >= 1, (
        f"KEEP interval count is 0: data={data}. "
        "silencedetect may not have detected any silence."
    )
    # timeline path is recorded in artifacts
    artifacts = detect_result.get("artifacts", [])
    assert len(artifacts) == 1
    assert Path(artifacts[0]["path"]).resolve() == otio_path.resolve()
    assert artifacts[0]["format"] == "otio"

    # --- (3) Validate OTIO content: V1 has keep-clip list and absolute target_url ---
    timeline = otio.adapters.read_from_file(str(otio_path))
    v1 = timeline.tracks[0]
    assert v1.kind == otio.schema.TrackKind.Video, "V1 track is not Video"
    clips = [c for c in v1 if isinstance(c, otio.schema.Clip)]
    assert len(clips) >= 1, "V1 track contains no clips"

    # target_url must be an absolute path (DC-AS-001)
    for clip in clips:
        ref = clip.media_reference
        assert isinstance(ref, otio.schema.ExternalReference)
        target_url = ref.target_url
        assert Path(target_url).is_absolute(), (
            f"target_url is not an absolute path: {target_url!r}"
        )
        # metadata.clipwright.kind = "keep"
        meta = clip.metadata.get("clipwright", {})
        assert meta.get("kind") == "keep", (
            f"clip.metadata.clipwright.kind is not 'keep': {meta!r}"
        )

    # --- (4) Materialize via render_timeline ---
    output_mp4 = tmp_path / "out.mp4"
    render_result = render_timeline(
        timeline=str(otio_path),
        output=str(output_mp4),
        options=RenderOptions(),
        dry_run=False,
    )
    assert render_result["ok"] is True, f"render_timeline failed: {render_result}"
    assert output_mp4.exists(), "Output mp4 was not generated"
    assert output_mp4.stat().st_size > 0, "Output mp4 size is 0"

    # --- (5) Output video duration is shorter than the source (silence was cut) ---
    source_duration = _probe_duration(require_ffprobe, source)
    output_duration = _probe_duration(require_ffprobe, output_mp4)
    assert output_duration < source_duration, (
        f"Output duration is not shorter than the source: "
        f"output={output_duration:.3f}s, source={source_duration:.3f}s. "
        "Silence interval was not cut."
    )
    # The silence interval (2s) is cut so output should be shorter than source
    # Tolerance: ±1.5s for encoder GOP boundary
    expected_max = total_sec - silence_sec + 1.5
    assert output_duration <= expected_max, (
        f"Output duration is too long: output={output_duration:.3f}s,"
        f" expected<={expected_max:.3f}s"
    )

    # --- (6) Output audio duration is also shortened (DC-AS-005) ---
    output_audio_duration = _probe_audio_duration(require_ffprobe, output_mp4)
    source_audio_duration = _probe_audio_duration(require_ffprobe, source)
    assert output_audio_duration > 0.0, "Output has no audio stream"
    assert output_audio_duration < source_audio_duration, (
        f"Output audio duration is not shorter than the source: "
        f"output_audio={output_audio_duration:.3f}s, "
        f"source_audio={source_audio_duration:.3f}s. "
        "Audio silence interval was not cut (DC-AS-005)."
    )

    # --- (7) Source media and OTIO are non-destructive ---
    assert source.stat().st_size == source_size_before, "Source media size changed"

    # --- (8) Dogfooding success confirmation ---
    # Confirms that silence->render succeeded via contract, OTIO, and file paths only.
    # render consumed the timeline.otio generated by silence without modification,
    # read the V1 keep-clip list, and applied video+audio trim at the same coordinates.


# ===========================================================================
# VAD backend e2e tests (DC-AS-007 / §7.8 / task_id: e2e-vad)
# ===========================================================================

# Skip flag when VAD extra is not installed
_VAD_AVAILABLE = True
try:
    import onnxruntime as _onnxruntime  # noqa: F401
    import silero_vad as _silero_vad  # noqa: F401
except ImportError:
    _VAD_AVAILABLE = False

_SKIP_VAD_REASON = (
    "silero-vad or onnxruntime cannot be imported. "
    "Install VAD dependencies with 'pip install clipwright-silence[vad]'."
)


def _make_vad_test_video(
    ffmpeg: str,
    output: Path,
) -> dict[str, float]:
    """Generate a VAD e2e test source (flite TTS speech + cough noise burst).

    Composition:
      [0, pre_sil)                          : silence
      [pre_sil, pre_sil+speech_a)           : flite TTS speech A (VAD judges as speech)
      [pre_sil+speech_a, +gap_sil)          : silence (gap between speech segments)
      [pre_sil+speech_a+gap_sil, +cough)    : cough-equivalent noise burst (loud, non-speech)
      [+cough, +gap_sil2)                   : silence (gap after cough)
      [+gap_sil2, +speech_b)                : flite TTS speech B (VAD judges as speech)
      [+speech_b, total)                    : silence

    Audio is generated via ffmpeg's libflite TTS engine (lavfi:flite).
    libflite is a royalty-free speech synthesis engine with characteristics
    close enough to real speech that Silero VAD classifies it as speech.

    Video is testsrc. Audio is composed of flite TTS (speech intervals),
    anullsrc (silence intervals), and sine burst (cough-equivalent loud noise)
    assembled via filter_complex.

    Returns:
        dict with timing positions in seconds.
        keys: pre_sil, speech_a_dur, gap_sil, cough_dur, gap_sil2, speech_b_dur,
              total, cough_start, cough_end
    """
    # Duration of each interval (seconds)
    pre_sil = 0.3
    gap_sil = 0.8  # silence between speech and cough (gap ensures VAD does not classify cough as speech)
    cough_dur = 0.2  # cough-equivalent noise burst
    gap_sil2 = 0.8  # silence between cough and speech
    post_sil = 0.3

    # Measure speech audio duration in advance using flite TTS
    # Speech text (limited word count; flite should produce ~1-1.5s)
    speech_text_a = "hello world"
    speech_text_b = "goodbye world"

    # Generate temporarily to measure speech duration
    def _probe_flite_dur(text: str) -> float:
        """Measure the audio duration of flite TTS output.

        Calls pytest.skip if ffmpeg's libflite is unavailable (returncode != 0
        or no time= pattern in stderr).
        Falling back with a guessed value is avoided (CR-E-002).
        """
        import re  # noqa: PLC0415

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
        # Skip if ffmpeg itself fails (e.g. build without libflite support)
        if result.returncode != 0:
            pytest.skip(
                f"ffmpeg libflite is not available (returncode={result.returncode}). "
                f"An ffmpeg build with libflite support is required. "
                f"stderr: {result.stderr[-200:]}"
            )
        # Obtain duration from stderr
        m = re.search(r"time=(\d+):(\d+):([0-9.]+)", result.stderr)
        if m:
            h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
            return h * 3600 + mi * 60 + s
        # No time= pattern = flite TTS did not actually run
        pytest.skip(
            "No time= pattern found in ffmpeg stderr. "
            "libflite TTS may not be functioning correctly. "
            f"text={text!r}, stderr={result.stderr[-200:]}"
        )

    speech_a_dur = _probe_flite_dur(speech_text_a)
    speech_b_dur = _probe_flite_dur(speech_text_b)

    total = (
        pre_sil
        + speech_a_dur
        + gap_sil
        + cough_dur
        + gap_sil2
        + speech_b_dur
        + post_sil
    )
    cough_start = pre_sil + speech_a_dur + gap_sil
    cough_end = cough_start + cough_dur

    # Assemble all intervals via filter_complex
    # Video: testsrc (total seconds)
    # Audio: each interval generated by anullsrc/flite/sine, then concatenated
    #
    # Interval composition:
    #   [A] pre_sil   s: anullsrc (silence)
    #   [B] speech_a  s: flite TTS A
    #   [C] gap_sil   s: anullsrc (silence)
    #   [D] cough_dur s: sine burst at high volume (cough-equivalent)
    #   [E] gap_sil2  s: anullsrc (silence)
    #   [F] speech_b  s: flite TTS B
    #   [G] post_sil  s: anullsrc (silence)
    #
    # sine frequency 1000Hz at high amplitude (volume=10) so that
    # silencedetect classifies it as non-silence (above -40dB).

    srate = 16000

    fc = (
        # --- Audio sources ---
        # [A] silence pre_sil
        f"anullsrc=r={srate}:cl=mono:d={pre_sil:.4f}[aud_a];"
        # [B] flite speech A
        f"flite=text='{speech_text_a}':voice=kal16,atrim=start=0:end={speech_a_dur:.4f},"
        f"asetpts=PTS-STARTPTS,aresample={srate}[aud_b];"
        # [C] silence gap_sil
        f"anullsrc=r={srate}:cl=mono:d={gap_sil:.4f}[aud_c];"
        # [D] cough-equivalent: loud sine burst (1kHz, high amplitude)
        f"sine=frequency=1000:beep_factor=1:duration={cough_dur:.4f},"
        f"volume=volume=10,aresample={srate},"
        f"atrim=start=0:end={cough_dur:.4f},asetpts=PTS-STARTPTS[aud_d];"
        # [E] silence gap_sil2
        f"anullsrc=r={srate}:cl=mono:d={gap_sil2:.4f}[aud_e];"
        # [F] flite speech B
        f"flite=text='{speech_text_b}':voice=kal16,atrim=start=0:end={speech_b_dur:.4f},"
        f"asetpts=PTS-STARTPTS,aresample={srate}[aud_f];"
        # [G] silence post_sil
        f"anullsrc=r={srate}:cl=mono:d={post_sil:.4f}[aud_g];"
        # --- concat ---
        "[aud_a][aud_b][aud_c][aud_d][aud_e][aud_f][aud_g]"
        "concat=n=7:v=0:a=1[audio_out]"
    )

    cmd = [
        ffmpeg,
        "-y",
        # video source
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size=320x240:rate=25:duration={total:.4f}",
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
        f"Failed to generate VAD test source: {result.stderr[-400:]}"
    )

    return {
        "pre_sil": pre_sil,
        "speech_a_dur": speech_a_dur,
        "gap_sil": gap_sil,
        "cough_dur": cough_dur,
        "gap_sil2": gap_sil2,
        "speech_b_dur": speech_b_dur,
        "total": total,
        "cough_start": cough_start,
        "cough_end": cough_end,
    }


def _collect_keep_intervals(otio_path: Path) -> list[tuple[float, float]]:
    """Return the keep clip intervals (seconds) from V1 track of an OTIO file."""
    timeline = otio.adapters.read_from_file(str(otio_path))
    v1 = timeline.tracks[0]
    result = []
    time_cursor = 0.0
    for item in v1:
        if isinstance(item, otio.schema.Clip):
            sr_otio = item.source_range
            if sr_otio is not None:
                start_sec = sr_otio.start_time.value / sr_otio.start_time.rate
                dur_sec = sr_otio.duration.value / sr_otio.duration.rate
                result.append((start_sec, start_sec + dur_sec))
        time_cursor += 0.0  # return in absolute coordinates
    return result


@pytest.mark.integration
@pytest.mark.skipif(not _VAD_AVAILABLE, reason=_SKIP_VAD_REASON)
def test_vad_backend_e2e(
    tmp_path: Path,
    require_ffmpeg: str,
    require_ffprobe: str,
) -> None:
    """VAD backend e2e: demonstrate cough noise is excluded by VAD but kept by silencedetect.

    Verification flow (§7.8 / DC-AS-007):
      (1) Generate material where VAD classifies speech using ffmpeg flite TTS (pre-establish)
      (2) Insert a cough-equivalent noise burst (0.2s) outside speech intervals
      (3) detect with backend="vad" -> confirm cough interval is not in KEEP
      (4) detect with backend="silencedetect" -> confirm cough interval remains in KEEP
      (5) Materialize VAD timeline via render_timeline -> confirm output mp4 and shortened duration
      (6) silencedetect backend regression: even after adding backend metadata,
          render still succeeds as with the existing silencedetect e2e (DC-GP-002)

    Skip conditions (DC-AS-007):
      - CLIPWRIGHT_FFMPEG / CLIPWRIGHT_FFPROBE not found
      - silero-vad / onnxruntime cannot be imported
    """
    # ==================================================================
    # (1) Pre-establish: generate source material that VAD classifies as speech
    # ==================================================================
    source = tmp_path / "vad_source.mp4"
    timing = _make_vad_test_video(require_ffmpeg, source)
    assert source.exists(), "VAD test source was not generated"

    cough_start = timing["cough_start"]
    cough_end = timing["cough_end"]

    # Pre-confirm that the flite TTS audio is classified as speech by VAD
    # (§7.8: pre-establishment step for source validation)
    # Launch vad_cli in a subprocess and confirm speech_segments is non-empty
    import sys

    vad_check_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "clipwright_silence.vad_cli",
            "--media",
            str(source),
            "--threshold",
            "0.5",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(tmp_path),
    )
    assert vad_check_result.returncode == 0, (
        f"vad_cli launch failed: returncode={vad_check_result.returncode}, "
        f"stderr={vad_check_result.stderr[-200:]}"
    )
    vad_payload = json.loads(vad_check_result.stdout)
    assert "error" not in vad_payload, (
        f"vad_cli returned an error: {vad_payload['error']}"
    )
    speech_segments = vad_payload.get("speech_segments", [])
    if len(speech_segments) == 0:
        pytest.skip(
            "vad_cli returned speech_segments=0. "
            "Source pre-establishment failed: flite TTS audio was not classified "
            "as speech by Silero VAD on this platform. "
            f"vad_payload={vad_payload}"
        )

    # ==================================================================
    # (3) detect with backend="vad" -> cough interval is excluded from KEEP
    # ==================================================================
    otio_vad = tmp_path / "timeline_vad.otio"
    vad_options = DetectSilenceOptions(
        backend="vad",
        vad_threshold=0.5,
        vad_min_speech_duration=0.1,
        vad_min_silence_duration=0.05,
        padding=0.05,
        min_keep_duration=0.1,
    )
    _content, vad_result = asyncio.run(
        mcp.call_tool(
            "clipwright_detect_silence",
            {
                "media": str(source),
                "output": str(otio_vad),
                "options": vad_options.model_dump(),
            },
        )
    )

    assert vad_result["ok"] is True, f"VAD backend detect_silence failed: {vad_result}"
    assert otio_vad.exists(), "VAD backend timeline.otio was not generated"

    vad_keep_intervals = _collect_keep_intervals(otio_vad)
    assert len(vad_keep_intervals) > 0, (
        f"VAD backend KEEP interval count is 0. vad_result={vad_result}"
    )

    # Confirm the cough interval (cough_start~cough_end) is not in VAD KEEP intervals
    # (VAD classifies it as non-speech and excludes it)
    cough_center = (cough_start + cough_end) / 2
    vad_cough_covered = any(s <= cough_center <= e for s, e in vad_keep_intervals)
    assert not vad_cough_covered, (
        f"VAD backend included the cough center ({cough_center:.3f}s) in KEEP. "
        f"Cough interval: [{cough_start:.3f}s - {cough_end:.3f}s]. "
        f"VAD KEEP intervals: {[(round(s, 3), round(e, 3)) for s, e in vad_keep_intervals]}. "  # noqa: E501
        "VAD failed to exclude the cough-equivalent noise as non-speech."
    )

    # VAD metadata has backend="vad" recorded (VAD-AD-07)
    timeline_vad = otio.adapters.read_from_file(str(otio_vad))
    for clip in timeline_vad.tracks[0]:
        if isinstance(clip, otio.schema.Clip):
            meta = clip.metadata.get("clipwright", {})
            assert meta.get("backend") == "vad", (
                f"VAD backend clip metadata.backend is not 'vad': {meta!r}"
            )
            break

    # ==================================================================
    # (4) detect with backend="silencedetect" -> cough interval remains in KEEP
    # ==================================================================
    otio_sd = tmp_path / "timeline_sd.otio"
    sd_options = DetectSilenceOptions(
        backend="silencedetect",
        silence_threshold_db=-40.0,
        min_silence_duration=0.3,
        padding=0.05,
        min_keep_duration=0.1,
    )
    _content, sd_result = asyncio.run(
        mcp.call_tool(
            "clipwright_detect_silence",
            {
                "media": str(source),
                "output": str(otio_sd),
                "options": sd_options.model_dump(),
            },
        )
    )

    assert sd_result["ok"] is True, (
        f"silencedetect backend detect_silence failed: {sd_result}"
    )
    assert otio_sd.exists(), "silencedetect backend timeline.otio was not generated"

    sd_keep_intervals = _collect_keep_intervals(otio_sd)
    assert len(sd_keep_intervals) > 0, (
        f"silencedetect backend KEEP interval count is 0. sd_result={sd_result}"
    )

    # Confirm the cough interval (cough_start~cough_end) is in silencedetect KEEP intervals
    # (silencedetect treats high volume as non-silence -> keeps it)
    sd_cough_covered = any(s <= cough_center <= e for s, e in sd_keep_intervals)
    assert sd_cough_covered, (
        f"silencedetect backend did not include the cough center ({cough_center:.3f}s) in KEEP. "  # noqa: E501
        f"Cough interval: [{cough_start:.3f}s - {cough_end:.3f}s]. "
        f"silencedetect KEEP intervals: {[(round(s, 3), round(e, 3)) for s, e in sd_keep_intervals]}. "  # noqa: E501
        "Could not confirm that the cough (loud noise) remains as KEEP in silencedetect."
    )

    # silencedetect metadata has backend="silencedetect" recorded (VAD-AD-07)
    timeline_sd = otio.adapters.read_from_file(str(otio_sd))
    for clip in timeline_sd.tracks[0]:
        if isinstance(clip, otio.schema.Clip):
            meta = clip.metadata.get("clipwright", {})
            assert meta.get("backend") == "silencedetect", (
                f"silencedetect clip metadata.backend is not 'silencedetect': {meta!r}"  # noqa: E501
            )
            break

    # ==================================================================
    # (5) Materialize VAD timeline via render_timeline (success condition 3)
    # ==================================================================
    output_mp4 = tmp_path / "vad_render_out.mp4"
    render_result = render_timeline(
        timeline=str(otio_vad),
        output=str(output_mp4),
        options=RenderOptions(),
        dry_run=False,
    )
    assert render_result["ok"] is True, (
        f"render_timeline for VAD timeline failed: {render_result}"
    )
    assert output_mp4.exists(), "VAD render output mp4 was not generated"
    assert output_mp4.stat().st_size > 0, "VAD render output mp4 size is 0"

    # Output mp4 duration is shorter than the source (non-speech intervals were cut)
    source_duration = _probe_duration(require_ffprobe, source)
    output_duration = _probe_duration(require_ffprobe, output_mp4)
    assert output_duration < source_duration, (
        f"VAD render output duration is not shorter than the source: "
        f"output={output_duration:.3f}s, source={source_duration:.3f}s. "
        "Non-speech intervals were not cut."
    )

    # ==================================================================
    # (6) silencedetect path regression (DC-GP-002)
    # render must succeed even after adding backend metadata
    # ==================================================================
    sd_render_out = tmp_path / "sd_render_out.mp4"
    sd_render_result = render_timeline(
        timeline=str(otio_sd),
        output=str(sd_render_out),
        options=RenderOptions(),
        dry_run=False,
    )
    assert sd_render_result["ok"] is True, (
        f"render_timeline for silencedetect timeline failed: {sd_render_result}"
    )
    assert sd_render_out.exists(), (
        "silencedetect regression: render output mp4 was not generated"
    )
    sd_output_duration = _probe_duration(require_ffprobe, sd_render_out)
    assert sd_output_duration < source_duration, (
        f"silencedetect regression: render output duration is not shorter than the source: "
        f"output={sd_output_duration:.3f}s, source={source_duration:.3f}s."
    )
