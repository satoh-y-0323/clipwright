"""
Generate golden .otio using Issue #2 sample code.

This script implements the sample code from Issue #2 to generate a golden
.otio file with timecode and audio layout support, which will be used for
independent verification of the conform implementation.
"""

import sys
import json
import subprocess
import pathlib
import tempfile
import shutil
import os

# UTF-8 fix for Windows subprocess output
sys.stdout.reconfigure(encoding="utf-8")

import opentimelineio as otio

# sys.path to import clipwright
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))
from clipwright.otio_utils import save_timeline


def make_otio_clip_video(
    name: str | None = None,
    path: str | pathlib.Path = "",
    frame_start: int = 0,
    frame_length: int = 1,
    rate: float = 25.0,
    resolve_group_id: int | None = None,
) -> otio.schema.Clip:
    """Create a video clip with optional Resolve_OTIO metadata."""
    if resolve_group_id is not None and resolve_group_id < 1:
        raise otio.exceptions.OTIOError("make_otio_clip_video: resolve_group_id (Link Group ID) must be None or >= 1")

    return otio.schema.Clip(
        name=name or "",
        media_reference=otio.schema.ExternalReference(
            target_url="" if str(path) == "" else pathlib.Path(path).resolve().as_posix(),
        ),
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.from_frames(frame=frame_start, rate=rate),
            duration=otio.opentime.from_frames(frame=frame_length, rate=rate),
        ),
        metadata={} if resolve_group_id is None else {
            "Resolve_OTIO": {
                "Link Group ID": resolve_group_id,
            },
        },
    )


def make_otio_clip_audio(
    name: str | None = None,
    path: str | pathlib.Path = "",
    sample_start: int = 0,
    sample_count: int = 1,
    rate: float = 25.0,
    num_channels: int = 1,
    resolve_track_id: int | None = None,
    resolve_group_id: int | None = None,
) -> otio.schema.Clip:
    """Create an audio clip with optional Resolve_OTIO metadata."""
    if num_channels < 1:
        raise otio.exceptions.OTIOError("make_otio_clip_audio: num_channels must be at least 1")

    if resolve_track_id is not None and resolve_track_id < 0:
        raise otio.exceptions.OTIOError("make_otio_clip_audio: resolve_track_id (Source Track ID) must be None or >= 0")

    if resolve_group_id is not None and resolve_group_id < 1:
        raise otio.exceptions.OTIOError("make_otio_clip_audio: resolve_group_id (Link Group ID) must be None or >= 1")

    return otio.schema.Clip(
        name=name or "",
        media_reference=otio.schema.ExternalReference(
            target_url="" if str(path) == "" else pathlib.Path(path).resolve().as_posix(),
        ),
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(value=sample_start, rate=rate),
            duration=otio.opentime.RationalTime(value=sample_count, rate=rate),
        ),
        metadata={} if resolve_track_id is None else {
            "Resolve_OTIO": {
                "Channels": [{"Source Channel ID": i, "Source Track ID": resolve_track_id} for i in range(num_channels)],
            } | ({"Link Group ID": resolve_group_id} if resolve_group_id is not None else {})
        },
    )


def new_timeline(
    name: str | None = None,
    num_audio_tracks: int | None = None,
    global_start_time: otio.opentime.RationalTime | None = None,
) -> otio.schema.Timeline:
    """Create a new Timeline.

    Tracks are created in [V1(Video), A1(Audio), A2(Audio), ...] order.
    """
    if num_audio_tracks is not None and num_audio_tracks < 0:
        raise otio.exceptions.OTIOError("new_timeline: num_audio_tracks must be None or >= 0")

    return otio.schema.Timeline(
        name=name or "",
        tracks=[
            otio.schema.Track(name="", kind=otio.schema.TrackKind.Video,
                metadata={"Resolve_OTIO": {"Locked": False}}),
            *[
                otio.schema.Track(name="", kind=otio.schema.TrackKind.Audio,
                    metadata={"Resolve_OTIO": {"Audio Type": "Mono", "Locked": False, "SoloOn": False}})
                for i in range(num_audio_tracks or 0)
            ],
        ],
        global_start_time=global_start_time,
        metadata={"Resolve_OTIO": {"Resolve OTIO Meta Version": "1.0"}},
    )


def make_otio_from_video_info(path: str, num_frames: int, rate: float, timecode: str = "00:00:00:00", channels_per_stream: list = []) -> otio.schema.Timeline:
    """Create a Timeline from the first video stream and all audio streams.

    One OTIO audio track is created for each source audio stream.
    Track order: [V1(Video), A1(Audio), A2(Audio), ...].
    Audio mapping: An -> Source Track ID n-1, Source Channel ID 0..N-1.
    Video and audio clips are linked using Resolve Link Group ID 1.
    """
    name = pathlib.Path(path).name
    num_audio_tracks = len(channels_per_stream)

    try:
        frame_start = otio.opentime.from_timecode(timecode=timecode or "00:00:00:00", rate=rate).to_frames()
    except (TypeError, ValueError):
        frame_start = 0

    timeline = new_timeline(name=name,
        num_audio_tracks=num_audio_tracks,
        global_start_time=otio.opentime.from_frames(frame_start, rate),
    )

    video_clip_otio = make_otio_clip_video(
        name=name,
        path=path,
        frame_start=frame_start,
        frame_length=num_frames,
        rate=rate,
        resolve_group_id=1
    )
    timeline.tracks[0].append(video_clip_otio)

    for n in range(num_audio_tracks):
        sample_count = num_frames
        audio_clip_otio_n = make_otio_clip_audio(
            name=name,
            path=path,
            sample_start=frame_start,
            sample_count=sample_count,
            rate=rate,
            num_channels=channels_per_stream[n],
            resolve_track_id=n,
            resolve_group_id=1,
        )
        timeline.tracks[1+n].append(audio_clip_otio_n)

    return timeline


def get_streams_via_ffprobe(path: str) -> dict:
    """Read the first video stream and all audio stream layouts via ffprobe."""
    ffprobe = os.environ.get("CLIPWRIGHT_FFPROBE")
    if not ffprobe or not os.path.isfile(ffprobe):
        ffprobe = "ffprobe"

    probe = json.loads(subprocess.check_output([
        ffprobe, "-v", "error", "-show_entries",
        "stream=codec_type,channels,nb_frames,duration,avg_frame_rate,r_frame_rate:"
        "stream_tags=timecode:format=duration:format_tags=timecode",
        "-of", "json", path,
    ], text=True))

    streams = probe["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")

    rate_str = next(video[k] for k in ("avg_frame_rate", "r_frame_rate") if video.get(k) not in (None, "N/A", "0/0"))
    fps_num, fps_den = map(int, rate_str.split("/"))
    rate = fps_num / fps_den

    duration = next(float(v) for v in (video.get("duration"), probe.get("format", {}).get("duration")) if v not in (None, "N/A"))
    num_frames = int(video["nb_frames"]) if video.get("nb_frames") not in (None, "N/A") else round(duration * rate)
    timecode = next((v for x in [probe.get("format", {}), *streams] for k, v in x.get("tags", {}).items() if k.casefold() == "timecode"), "00:00:00:00")
    channels_per_stream = [int(s["channels"]) for s in streams if s["codec_type"] == "audio"]

    return {
        "num_frames": num_frames,
        "rate": rate,
        "timecode": timecode,
        "channels_per_stream": channels_per_stream,
    }


def make_otio_from_video(path: str):
    """Create a Timeline from a video file (top-level interface)."""
    streams_info = get_streams_via_ffprobe(path)
    return make_otio_from_video_info(
        path=path,
        num_frames=streams_info["num_frames"],
        rate=streams_info["rate"],
        timecode=streams_info["timecode"],
        channels_per_stream=streams_info["channels_per_stream"]
    )


def generate_test_media(output_path: str) -> None:
    """Generate a test media file with timecode and 2-channel audio."""
    ffmpeg = os.environ.get("CLIPWRIGHT_FFMPEG")
    if not ffmpeg or not os.path.isfile(ffmpeg):
        ffmpeg = "ffmpeg"

    cmd = [
        ffmpeg,
        "-f", "lavfi",
        "-i", "testsrc2=duration=2.0",
        "-f", "lavfi",
        "-i", "sine=frequency=440:duration=2.0:sample_rate=44100",
        "-r", "25.0",
        "-timecode", "01:00:00:00",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-pix_fmt", "yuv420p",
        "-map", "0:v", "-map", "1:a",
        output_path,
    ]

    print(f"Generating test media: {output_path}")
    subprocess.run(cmd, check=True, capture_output=True)
    print(f"  ✓ Generated")


def main():
    # Create temp directory
    temp_dir = tempfile.mkdtemp(prefix="golden_otio_")
    print(f"Temp directory: {temp_dir}")

    try:
        # Generate test media
        test_media = os.path.join(temp_dir, "test_media.mov")
        generate_test_media(test_media)

        # Generate timeline using Issue #2 sample code
        print("\nGenerating golden .otio using Issue #2 sample code...")
        timeline = make_otio_from_video(test_media)

        # Save golden .otio
        output_path = "tests/fixtures/golden/issue2_sample.otio"
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        save_timeline(timeline, output_path)

        print(f"✓ Saved golden .otio: {output_path}")
        print(f"  - global_start_time: {timeline.global_start_time}")
        print(f"  - tracks: {len(timeline.tracks)}")
        print(f"  - V1 clips: {len(timeline.tracks[0])}")
        if len(timeline.tracks) > 1:
            print(f"  - A1 clips: {len(timeline.tracks[1])}")

        # Verify the output
        print("\nVerifying golden .otio structure...")
        with open(output_path) as f:
            otio_data = json.load(f)

        # Check for Resolve_OTIO metadata
        timeline_meta = otio_data.get("metadata", {}).get("Resolve_OTIO", {})
        print(f"  - Timeline Resolve_OTIO: {timeline_meta}")

        # Check tracks structure (it's a list of track objects)
        if "tracks" in otio_data and isinstance(otio_data["tracks"], list):
            for i, track in enumerate(otio_data["tracks"]):
                if isinstance(track, dict):
                    track_meta = track.get("metadata", {}).get("Resolve_OTIO", {})
                    if track_meta:
                        print(f"  - Track {i} Resolve_OTIO: {track_meta}")
                    for j, clip in enumerate(track.get("clips", [])):
                        if isinstance(clip, dict):
                            clip_meta = clip.get("metadata", {}).get("Resolve_OTIO", {})
                            if clip_meta:
                                print(f"    - Clip {j} Resolve_OTIO: {clip_meta}")

        print("\n✓ Golden .otio generated successfully")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
