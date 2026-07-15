"""
NLE Probe Spike: ffprobe timecode and channel_layout observation.

Generates test media with timecode, runs ffprobe to observe actual output format
(format.tags, streams[].tags, streams[].channel_layout), and records findings.

Requirements:
  - CLIPWRIGHT_FFMPEG / CLIPWRIGHT_FFPROBE env vars (from memory)
  - uv run python spikes/nle_probe.py
"""

import sys
import json
import subprocess
import os
import tempfile
import pathlib
import shutil
from typing import Any, Optional

# UTF-8 fix for Windows subprocess output
sys.stdout.reconfigure(encoding="utf-8")


def get_ffmpeg_path() -> str:
    """Get ffmpeg from env or PATH."""
    ffmpeg = os.environ.get("CLIPWRIGHT_FFMPEG")
    if ffmpeg and os.path.isfile(ffmpeg):
        return ffmpeg
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    raise RuntimeError("ffmpeg not found. Set CLIPWRIGHT_FFMPEG or install ffmpeg.")


def get_ffprobe_path() -> str:
    """Get ffprobe from env or PATH."""
    ffprobe = os.environ.get("CLIPWRIGHT_FFPROBE")
    if ffprobe and os.path.isfile(ffprobe):
        return ffprobe
    if shutil.which("ffprobe"):
        return "ffprobe"
    raise RuntimeError("ffprobe not found. Set CLIPWRIGHT_FFPROBE or install ffmpeg.")


def run_cmd(cmd: list[str], **kwargs) -> str:
    """Run command and return stdout."""
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\nstderr: {result.stderr}")
    return result.stdout


def generate_test_media(
    output_path: str,
    format_type: str,
    duration: float = 2.0,
    rate: float = 25.0,
    timecode: Optional[str] = None,
    audio_layout: Optional[list[int]] = None,
) -> None:
    """Generate test media file with specified parameters.

    Args:
        output_path: Output file path
        format_type: "mov", "mxf", "mp4"
        duration: Video duration in seconds
        rate: Frame rate (25.0, 30.0, 29.97, 23.976)
        timecode: Timecode string like "01:00:00:00" or "01:00:00;00" (drop-frame)
        audio_layout: List of channel counts per audio stream, e.g. [1, 1, 1, 1, 1, 1, 1, 1] for 8x1ch
    """
    ffmpeg = get_ffmpeg_path()

    # Base ffmpeg command
    cmd = [
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=duration={duration}",
    ]

    # Add audio if needed
    # For MXF, audio must be 48kHz. For others, 44.1kHz is OK.
    audio_rate = 48000 if format_type == "mxf" else 44100

    if audio_layout:
        if len(audio_layout) == 1 and audio_layout[0] == 1:
            # Single 1ch audio
            cmd.extend(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency=440:duration={duration}:sample_rate={audio_rate}",
                ]
            )
        elif len(audio_layout) == 1 and audio_layout[0] == 2:
            # Single 2ch audio (stereo)
            cmd.extend(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency=440:duration={duration}:sample_rate={audio_rate}",
                ]
            )
        else:
            # Multiple audio streams (e.g., 8x1ch)
            # Generate one sine wave, then split it into multiple streams
            cmd.extend(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency=440:duration={duration}:sample_rate={audio_rate}",
                ]
            )
    else:
        # Video only
        cmd.extend(
            [
                "-f",
                "lavfi",
                "-i",
                f"sine=duration=0.1:sample_rate={audio_rate}",  # Dummy audio to satisfy format requirements
            ]
        )

    # Frame rate handling
    if rate == 30000 / 1001:  # 29.97 (NTSC)
        rate_str = "30000/1001"
    elif rate == 24000 / 1001:  # 23.976
        rate_str = "24000/1001"
    else:
        rate_str = str(rate)

    cmd.extend(["-r", rate_str])

    # Timecode handling
    if timecode:
        cmd.extend(["-timecode", timecode])

    # Filter complex for multi-stream audio (must come before codec selection)
    if audio_layout and len(audio_layout) > 1:
        n_streams = len(audio_layout)
        filter_str = (
            f"[1:a]asplit={n_streams}{''.join(f'[a{i}]' for i in range(n_streams))}"
        )
        cmd.extend(["-filter_complex", filter_str])

    # Codec selection based on format
    if format_type == "mov":
        cmd.extend(
            [
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-pix_fmt",
                "yuv420p",
            ]
        )
        if audio_layout and len(audio_layout) > 1:
            # Map video and all audio streams
            cmd.extend(["-map", "0:v"])
            n_streams = len(audio_layout)
            for i in range(n_streams):
                cmd.extend(["-map", f"[a{i}]"])
        else:
            cmd.extend(["-map", "0:v", "-map", "1:a"])
    elif format_type == "mxf":
        cmd.extend(
            [
                "-c:v",
                "mpeg2video",
                "-c:a",
                "pcm_s16le",
                "-q:v",
                "5",
            ]
        )
        if audio_layout and len(audio_layout) > 1:
            cmd.extend(["-map", "0:v"])
            n_streams = len(audio_layout)
            for i in range(n_streams):
                cmd.extend(["-map", f"[a{i}]"])
        else:
            cmd.extend(["-map", "0:v", "-map", "1:a"])
    elif format_type == "mp4":
        cmd.extend(
            [
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-pix_fmt",
                "yuv420p",
                "-map",
                "0:v",
                "-map",
                "1:a",
            ]
        )

    cmd.append(output_path)

    print(f"Generating {format_type.upper()}: {output_path}")
    print(f"  Command: {' '.join(cmd)}")
    run_cmd(cmd)
    print(f"  ✓ Generated")


def probe_media(file_path: str) -> dict[str, Any]:
    """Run ffprobe and return JSON output."""
    ffprobe = get_ffprobe_path()

    cmd = [
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        file_path,
    ]

    output = run_cmd(cmd)
    return json.loads(output)


def extract_fixture(probe_json: dict[str, Any]) -> dict[str, Any]:
    """Extract relevant fields for fixture, keeping layout/tags/channel_layout intact."""
    fixture = {
        "format": {},
        "streams": [],
    }

    # Extract format-level tags (may contain timecode)
    if "tags" in probe_json.get("format", {}):
        fixture["format"]["tags"] = probe_json["format"]["tags"]

    # Extract relevant stream info
    for stream in probe_json.get("streams", []):
        s = {
            "index": stream.get("index"),
            "codec_type": stream.get("codec_type"),
            "codec_name": stream.get("codec_name"),
        }

        # Video-specific fields
        if stream.get("codec_type") == "video":
            s["width"] = stream.get("width")
            s["height"] = stream.get("height")
            s["avg_frame_rate"] = stream.get("avg_frame_rate")
            s["duration"] = stream.get("duration")

        # Audio-specific fields
        if stream.get("codec_type") == "audio":
            s["channels"] = stream.get("channels")
            s["channel_layout"] = stream.get("channel_layout")
            s["duration"] = stream.get("duration")

        # Tags (may contain timecode in audio/data streams)
        if "tags" in stream:
            s["tags"] = stream["tags"]

        fixture["streams"].append(s)

    return fixture


def test_rate_support(rate_value: float | str) -> bool:
    """Test if otio.opentime.from_timecode accepts a given rate."""
    try:
        import opentimelineio as otio

        result = otio.opentime.from_timecode("01:00:00:00", rate=rate_value)
        return True
    except (ValueError, TypeError):
        return False


def main():
    # Setup temp directory for generated media
    temp_dir = tempfile.mkdtemp(prefix="nle_probe_")
    print(f"Temp directory: {temp_dir}")

    try:
        results = {}
        fixtures_to_create = []

        # Test 1: MOV with timecode
        print("\n" + "=" * 60)
        print("TEST 1: MOV with timecode (01:00:00:00)")
        print("=" * 60)
        mov_path = os.path.join(temp_dir, "test_tc.mov")
        generate_test_media(mov_path, "mov", timecode="01:00:00:00", audio_layout=[2])
        probe = probe_media(mov_path)
        fixture = extract_fixture(probe)
        print(json.dumps(fixture, indent=2, ensure_ascii=False))
        fixtures_to_create.append(("mov_tc", fixture))
        results["mov_timecode"] = fixture

        # Test 2: MOV without timecode
        print("\n" + "=" * 60)
        print("TEST 2: MOV without timecode")
        print("=" * 60)
        mov_notc_path = os.path.join(temp_dir, "test_notc.mov")
        generate_test_media(mov_notc_path, "mov", audio_layout=[2])
        probe = probe_media(mov_notc_path)
        fixture = extract_fixture(probe)
        print(json.dumps(fixture, indent=2, ensure_ascii=False))
        fixtures_to_create.append(("mov_no_timecode", fixture))
        results["mov_no_timecode"] = fixture

        # Test 3: MXF with timecode
        print("\n" + "=" * 60)
        print("TEST 3: MXF with timecode (01:00:00:00)")
        print("=" * 60)
        mxf_path = os.path.join(temp_dir, "test_tc.mxf")
        generate_test_media(mxf_path, "mxf", timecode="01:00:00:00", audio_layout=[2])
        probe = probe_media(mxf_path)
        fixture = extract_fixture(probe)
        print(json.dumps(fixture, indent=2, ensure_ascii=False))
        fixtures_to_create.append(("mxf_tc", fixture))
        results["mxf_timecode"] = fixture

        # Test 4: Drop-frame timecode (29.97fps)
        print("\n" + "=" * 60)
        print("TEST 4: Drop-frame timecode (29.97fps, 01:00:00;00)")
        print("=" * 60)
        df_path = os.path.join(temp_dir, "test_df.mov")
        generate_test_media(
            df_path, "mov", rate=30000 / 1001, timecode="01:00:00;00", audio_layout=[2]
        )
        probe = probe_media(df_path)
        fixture = extract_fixture(probe)
        print(json.dumps(fixture, indent=2, ensure_ascii=False))
        fixtures_to_create.append(("drop_frame", fixture))
        results["drop_frame"] = fixture

        # Test 5: 8x1ch audio layout
        print("\n" + "=" * 60)
        print("TEST 5: 8x1ch audio layout")
        print("=" * 60)
        multi8x1_path = os.path.join(temp_dir, "test_8x1.mov")
        generate_test_media(
            multi8x1_path,
            "mov",
            timecode="01:00:00:00",
            audio_layout=[1, 1, 1, 1, 1, 1, 1, 1],
        )
        probe = probe_media(multi8x1_path)
        fixture = extract_fixture(probe)
        print(json.dumps(fixture, indent=2, ensure_ascii=False))
        fixtures_to_create.append(("audio_8x1ch", fixture))
        results["audio_8x1ch"] = fixture

        # Test rate gate support
        print("\n" + "=" * 60)
        print("RATE GATE SUPPORT TEST")
        print("=" * 60)
        rates_to_test = [25.0, 30.0, 29.97, 23.976, "30000/1001", "24000/1001"]
        rate_results = {}
        for rate_val in rates_to_test:
            try:
                supported = test_rate_support(rate_val)
                rate_results[str(rate_val)] = (
                    "✓ supported" if supported else "✗ not supported"
                )
                print(
                    f"  Rate {rate_val}: {'✓ supported' if supported else '✗ not supported'}"
                )
            except Exception as e:
                rate_results[str(rate_val)] = f"✗ error: {e}"
                print(f"  Rate {rate_val}: ✗ error: {e}")

        # Summary
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print("\nRate support:")
        for rate, result in rate_results.items():
            print(f"  {rate}: {result}")

        print("\nFixtures to create:")
        for name, fixture in fixtures_to_create:
            print(f"  tests/fixtures/ffprobe/{name}.json")

        print("\nAll observations complete. Ready to create fixture files.")
        print(f"\nGenerated files in: {temp_dir}")
        print("(Temp files will be cleaned up)")

    finally:
        # Cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
