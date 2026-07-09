"""test_available_range.py — available_range wiring for the generated OTIO clip.

Target: clipwright_transcribe.transcribe.transcribe_media

transcribe is a full-length tool (§6 tool-chaining contract vocabulary: "create").
The generated clip's source_range already spans the whole media
(start_time=0, duration=media_info.duration; DC-AM-001). This test verifies that
the ExternalReference.available_range wired via otio_utils.add_clip (ADR-4, already
supports MediaRef.available_range) is populated with the same full-length range, so
that:

  - available_range is not left unset (None).
  - available_range == source_range (both 0..media_info.duration).
  - source_range is contained within available_range (subset check, expressed via
    start_time/end_time_exclusive comparison rather than direct equality, so the
    intent is verified independently of the "happens to be equal" observation
    above).

Mock strategy mirrors test_transcribe.py TestOtioConstruction: patch inspect_media
and _run_whisper, no real ffmpeg/whisper binaries are invoked.

Red-phase status (expected at authoring time):
  transcribe.py builds `MediaRef(target_url=...)` without `available_range=` when
  calling add_clip (see _transcribe_inner). Because MediaRef.available_range
  defaults to None, otio_utils.add_clip leaves ExternalReference.available_range
  unset, so the round-tripped clip.media_reference.available_range is None.
  This test therefore fails at `assert available_range is not None`
  (AssertionError), which is the correct Red reason: the wiring is simply
  missing, not a broken import/typo. It will pass once transcribe.py is updated
  to pass `available_range=full_source_range` (or equivalent) into MediaRef.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_transcribe.schemas import TranscribeOptions
from clipwright_transcribe.transcribe import transcribe_media

from ._whisper_run import _whisper_run

FPS = 30.0
DURATION_SEC = 10.0


def _make_media_info(path: str) -> MediaInfo:
    streams = [
        StreamInfo(index=0, codec_type="video", codec_name="h264"),
        StreamInfo(index=1, codec_type="audio", codec_name="aac"),
    ]
    duration = RationalTimeModel(value=DURATION_SEC * FPS, rate=FPS)
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=duration,
        streams=streams,
        bit_rate=8_000_000,
    )


def _make_paths(tmp_path: Path) -> tuple[str, str, str]:
    media = tmp_path / "video.mp4"
    media.write_bytes(b"fake")
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"fake-model")
    output = tmp_path / "out.otio"
    return str(media), str(output), str(model)


def _run_transcribe(tmp_path: Path) -> otio.schema.Clip:
    """Run transcribe_media with mocked inspect_media/_run_whisper and return the
    single V1 clip from the resulting OTIO timeline."""
    media, output, model = _make_paths(tmp_path)
    with (
        patch(
            "clipwright_transcribe.transcribe.inspect_media",
            return_value=_make_media_info(media),
        ),
        patch(
            "clipwright_transcribe.transcribe._run_whisper",
            return_value=_whisper_run([]),
        ),
    ):
        result = transcribe_media(media, output, TranscribeOptions(model_path=model))
    assert result.ok is True

    timeline = otio.adapters.read_from_file(output)
    v1 = timeline.tracks[0]
    clips = [c for c in v1 if isinstance(c, otio.schema.Clip)]
    assert len(clips) == 1
    return clips[0]


class TestAvailableRange:
    def test_available_range_is_set(self, tmp_path: Path) -> None:
        """ExternalReference.available_range must not be left unset (None)."""
        clip = _run_transcribe(tmp_path)
        available_range = clip.media_reference.available_range
        assert available_range is not None

    def test_available_range_equals_source_range(self, tmp_path: Path) -> None:
        """available_range == source_range: both span 0..media_info.duration
        (full-length tool; DC-AM-001)."""
        clip = _run_transcribe(tmp_path)
        available_range = clip.media_reference.available_range
        assert available_range is not None

        source_range = clip.source_range
        assert available_range.start_time == source_range.start_time
        assert available_range.duration == source_range.duration

        expected_start = otio.opentime.RationalTime(0.0, FPS)
        expected_duration = otio.opentime.RationalTime(DURATION_SEC * FPS, FPS)
        assert available_range.start_time == expected_start
        assert available_range.duration == expected_duration

    def test_source_range_is_subset_of_available_range(self, tmp_path: Path) -> None:
        """source_range must be contained within available_range
        (source_range ⊆ available_range), expressed as start/end comparisons
        rather than direct equality so the containment intent is verified
        independently of the "happens to be equal" observation in the previous
        test."""
        clip = _run_transcribe(tmp_path)
        available_range = clip.media_reference.available_range
        assert available_range is not None

        source_range = clip.source_range
        assert source_range.start_time >= available_range.start_time
        assert source_range.end_time_exclusive() <= available_range.end_time_exclusive()

    def test_zero_segments_still_wires_available_range(self, tmp_path: Path) -> None:
        """Even with zero transcription segments (no markers), the full-length
        clip's available_range must still be wired (DC-GP-002 zero-segment path
        must not skip this)."""
        clip = _run_transcribe(tmp_path)
        assert clip.media_reference.available_range is not None
