#!/usr/bin/env python3
"""NLE adapter probe — test cmx_3600 / fcpxml with multi-audio and TC.

Spike to determine whether ADR-NI-7 (Audio track removal defense for EDL)
is necessary. Tests:
1. Multi-audio track behavior (up to 8×1ch)
2. global_start_time reflection in record TC (EDL) / sequence start (FCPXML)
3. timecode preservation on round-trip

Config (a)-(d):
  (a) V1+A1 baseline
  (b) V1+A2..A8 (8 audio tracks, 1ch each)
  (c) (b) + global_start_time = 01:00:00:00
  (d) (c) + TC-aware source_range / available_range
"""

from __future__ import annotations

import contextlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import opentimelineio as otio

# Windows cp932 encoding fix
sys.stdout.reconfigure(encoding="utf-8")

# ===========================================================================
# Configuration: 4 timeline variants
# ===========================================================================

CONFIGS = [
    {
        "name": "(a) V1+A1 baseline",
        "v1_only": False,
        "audio_tracks": 1,
        "global_start_time": None,
        "tc_shift": False,
    },
    {
        "name": "(b) V1+A2..A8 (8 audio tracks)",
        "v1_only": False,
        "audio_tracks": 8,
        "global_start_time": None,
        "tc_shift": False,
    },
    {
        "name": "(c) (b) + global_start_time",
        "v1_only": False,
        "audio_tracks": 8,
        "global_start_time": "01:00:00:00",
        "tc_shift": False,
    },
    {
        "name": "(d) (c) + TC-aware ranges",
        "v1_only": False,
        "audio_tracks": 8,
        "global_start_time": "01:00:00:00",
        "tc_shift": True,
    },
]


def make_timeline(
    config: dict[str, Any],
) -> otio.schema.Timeline:
    """Create OTIO timeline per config (a)-(d)."""
    timeline = otio.schema.Timeline(name="test_nle_interop")

    # V1 track with a single clip (10s @ 24fps)
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    ref_v1 = otio.schema.ExternalReference(target_url="file:///dummy/video.mov")

    # If TC shift is enabled, set available_range to TC 01:00:00:00 (86400 frames @ 24fps)
    if config["tc_shift"]:
        ref_v1.available_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(86400, 24),  # 01:00:00:00 @ 24fps
            duration=otio.opentime.RationalTime(240, 24),  # 10s
        )
        clip_v1 = otio.schema.Clip(
            name="video_tc",
            media_reference=ref_v1,
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(86400, 24),
                duration=otio.opentime.RationalTime(240, 24),
            ),
        )
    else:
        ref_v1.available_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, 24),
            duration=otio.opentime.RationalTime(240, 24),
        )
        clip_v1 = otio.schema.Clip(
            name="video",
            media_reference=ref_v1,
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 24),
                duration=otio.opentime.RationalTime(240, 24),
            ),
        )

    v1.append(clip_v1)
    timeline.tracks.append(v1)

    # Audio tracks (A1 or A1..A8)
    num_audio = config["audio_tracks"]
    for i in range(num_audio):
        track_name = f"A{i + 1}"
        audio_track = otio.schema.Track(
            name=track_name, kind=otio.schema.TrackKind.Audio
        )

        # 1ch audio clip
        ref_audio = otio.schema.ExternalReference(
            target_url=f"file:///dummy/audio{i + 1}.wav"
        )
        if config["tc_shift"]:
            ref_audio.available_range = otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(86400, 24),
                duration=otio.opentime.RationalTime(240, 24),
            )
            clip_audio = otio.schema.Clip(
                name=f"audio{i + 1}_tc",
                media_reference=ref_audio,
                source_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(86400, 24),
                    duration=otio.opentime.RationalTime(240, 24),
                ),
            )
        else:
            ref_audio.available_range = otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 24),
                duration=otio.opentime.RationalTime(240, 24),
            )
            clip_audio = otio.schema.Clip(
                name=f"audio{i + 1}",
                media_reference=ref_audio,
                source_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0, 24),
                    duration=otio.opentime.RationalTime(240, 24),
                ),
            )

        audio_track.append(clip_audio)
        timeline.tracks.append(audio_track)

    # Set global_start_time if specified
    if config["global_start_time"] is not None:
        try:
            gst = otio.opentime.from_timecode(config["global_start_time"], 24)
            timeline.global_start_time = gst
        except Exception as e:
            print(f"  [WARN] global_start_time parse failed: {e}", file=sys.stderr)

    return timeline


def test_adapter(
    timeline: otio.schema.Timeline,
    adapter_name: str,
    suffix: str,
    config_name: str,
) -> dict[str, Any]:
    """Test write/read round-trip for one adapter."""
    result = {
        "adapter": adapter_name,
        "config": config_name,
        "write_success": False,
        "write_error": None,
        "num_tracks": len(timeline.tracks),
        "num_audio_tracks": sum(
            1 for t in timeline.tracks if t.kind == otio.schema.TrackKind.Audio
        ),
        "global_start_time": None,
        "read_success": False,
        "read_error": None,
        "round_trip_global_start_time": None,
        "round_trip_num_audio_tracks": None,
    }

    if timeline.global_start_time is not None:
        result["global_start_time"] = str(timeline.global_start_time)

    # Write
    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = str(Path(tmpdir) / f"test{suffix}")
        try:
            otio.adapters.write_to_file(timeline, output_path)
            result["write_success"] = True
        except Exception as e:
            result["write_error"] = f"{type(e).__name__}: {str(e)}"
            return result

        # Read (round-trip)
        try:
            loaded_tl = otio.adapters.read_from_file(output_path)
            if isinstance(loaded_tl, otio.schema.Timeline):
                result["read_success"] = True
                result["round_trip_global_start_time"] = (
                    str(loaded_tl.global_start_time)
                    if loaded_tl.global_start_time is not None
                    else None
                )
                result["round_trip_num_audio_tracks"] = sum(
                    1 for t in loaded_tl.tracks if t.kind == otio.schema.TrackKind.Audio
                )
            else:
                result["read_error"] = (
                    f"Expected Timeline, got {type(loaded_tl).__name__}"
                )
        except Exception as e:
            result["read_error"] = f"{type(e).__name__}: {str(e)}"

    return result


def main() -> None:
    """Run spike: test each (a)-(d) config with cmx_3600 and fcpxml."""
    print("=" * 80)
    print("NLE Adapter Probe: cmx_3600 / fcpxml with multi-audio + global_start_time")
    print("=" * 80)

    results = []

    for config in CONFIGS:
        print(f"\n{'=' * 80}")
        print(f"Config: {config['name']}")
        print(f"{'=' * 80}")

        timeline = make_timeline(config)
        audio_range = (
            "1" if config["audio_tracks"] == 1 else f"1..{config['audio_tracks']}"
        )
        print(f"Timeline created: V1 + A{audio_range}")
        print(f"  Track count: {len(timeline.tracks)}")
        print(
            f"  Audio track count: {sum(1 for t in timeline.tracks if t.kind == otio.schema.TrackKind.Audio)}"
        )
        if timeline.global_start_time is not None:
            print(f"  global_start_time: {timeline.global_start_time}")

        # Test CMX 3600 (EDL)
        print("\n  [CMX 3600 / EDL]")
        result_cmx = test_adapter(timeline, "cmx_3600", ".edl", config["name"])
        results.append(result_cmx)
        print(f"    Write: {'SUCCESS' if result_cmx['write_success'] else 'FAILED'}")
        if result_cmx["write_error"]:
            print(f"      Error: {result_cmx['write_error']}")
        if result_cmx["write_success"]:
            print(f"    Read:  {'SUCCESS' if result_cmx['read_success'] else 'FAILED'}")
            if result_cmx["read_error"]:
                print(f"      Error: {result_cmx['read_error']}")
            if result_cmx["read_success"]:
                print(
                    f"      Round-trip global_start_time: {result_cmx['round_trip_global_start_time']}"
                )
                print(
                    f"      Round-trip audio tracks: {result_cmx['round_trip_num_audio_tracks']}"
                )

        # Test FCPXML
        print("\n  [FCPXML]")
        result_fcpx = test_adapter(timeline, "fcpx_xml", ".fcpxml", config["name"])
        results.append(result_fcpx)
        print(f"    Write: {'SUCCESS' if result_fcpx['write_success'] else 'FAILED'}")
        if result_fcpx["write_error"]:
            print(f"      Error: {result_fcpx['write_error']}")
        if result_fcpx["write_success"]:
            print(
                f"    Read:  {'SUCCESS' if result_fcpx['read_success'] else 'FAILED'}"
            )
            if result_fcpx["read_error"]:
                print(f"      Error: {result_fcpx['read_error']}")
            if result_fcpx["read_success"]:
                print(
                    f"      Round-trip global_start_time: {result_fcpx['round_trip_global_start_time']}"
                )
                print(
                    f"      Round-trip audio tracks: {result_fcpx['round_trip_num_audio_tracks']}"
                )

    # Summary
    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print("=" * 80)
    for res in results:
        status = "✓" if res["write_success"] else "✗"
        print(
            f"{status} {res['adapter']:15} | {res['config']:30} | "
            f"Write: {res['write_success']:<5} | Read: {res['read_success']:<5}"
        )
        if res["write_error"]:
            print(f"  Write error: {res['write_error']}")
        if res["read_error"]:
            print(f"  Read error: {res['read_error']}")

    # Export results as JSON for spike-report
    print(f"\n{'=' * 80}")
    print("RESULTS (JSON)")
    print("=" * 80)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
