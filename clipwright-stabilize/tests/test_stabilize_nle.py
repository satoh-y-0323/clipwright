"""test_stabilize_nle.py -- Resolve NLE conform wiring in detect_shake (Issue #2).

Context (architecture-report-20260715-191151.md §9 ADR-NI-10 rev.2 / FR-9 / NFR-4):
  detect_shake's new-creation path (timeline=None) calls
  clipwright.nle_interop.conform_timeline_for_nle right after _add_full_clip
  builds the V1+A1 full-length keep clips, and merges its returned warnings
  into the tool's own warnings list. The existing-timeline (accumulate) path
  must never be conformed (NFR-4).

Mock policy (mirrors test_stabilize.py / test_available_range.py):
  - Patch clipwright_stabilize.stabilize.inspect_media to inject MediaInfo.
  - Patch clipwright_stabilize.stabilize.run_vidstabdetect to avoid libvidstab.
  - No real ffmpeg/ffprobe binary or real libvidstab is invoked.

Verification points:
  (a) New-creation path, TC + 2ch audio: global_start_time is set from the
      timecode, timeline.metadata carries the Resolve_OTIO idempotency
      marker, the pre-existing A1 mirror clip is *adopted* in place
      (Channels + Link Group ID stamped, no new track appended, no "audio
      mirroring skipped" warning), and both V1 and A1 clips' source_range
      are shifted by the same timecode-derived offset.
  (b) Existing-timeline (accumulate) path: the output timeline must not be
      conformed at all -- no Resolve_OTIO metadata anywhere, track count is
      unchanged, and the pre-existing V1 clip's source_range/metadata are
      byte-for-byte unchanged (NFR-4).
  (c) New-creation path, no timecode: no shift is applied (source_range
      still starts at 0) and global_start_time stays None, but the audio
      layout metadata (Channels/Link Group ID/idempotency marker) is still
      stamped unconditionally.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
from clipwright.otio_utils import load_timeline
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_stabilize.schemas import DetectShakeOptions
from clipwright_stabilize.stabilize import detect_shake

FPS = 30.0
_TEST_BIT_RATE = 8_000_000


def _make_media_info(
    path: str,
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
    start_timecode: str | None = None,
    channels: int = 2,
) -> MediaInfo:
    streams = [
        StreamInfo(index=0, codec_type="video", codec_name="h264"),
        StreamInfo(index=1, codec_type="audio", codec_name="aac", channels=channels),
    ]
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=duration_sec * rate, rate=rate),
        streams=streams,
        bit_rate=_TEST_BIT_RATE,
        start_timecode=start_timecode,
    )


def _fake_analyze_result(
    trf_abs: Path, severity: float | None = 0.35
) -> dict[str, Any]:
    trf_abs.write_bytes(b"TRF1dummy")
    return {"trf_path": str(trf_abs), "severity": severity, "warnings": []}


def _video_track(tl: otio.schema.Timeline) -> otio.schema.Track:
    return next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)


def _audio_track(tl: otio.schema.Timeline) -> otio.schema.Track:
    return next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Audio)


def _sole_clip(track: otio.schema.Track) -> otio.schema.Clip:
    clips = [item for item in track if isinstance(item, otio.schema.Clip)]
    assert len(clips) == 1
    return clips[0]


# ===========================================================================
# (a) New-creation path, TC + 2ch audio: A1 is adopted, no skip warning
# ===========================================================================


class TestNewTimelineWithTimecode:
    def test_global_start_time_set_and_marker_present(self, tmp_path: Path) -> None:
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        media_info = _make_media_info(str(media), start_timecode="01:00:00:00")

        with (
            patch(
                "clipwright_stabilize.stabilize.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            ),
        ):
            result = detect_shake(
                media=str(media),
                output=str(output),
                options=DetectShakeOptions(),
                timeline=None,
            )

        assert result["ok"] is True, f"detect_shake failed: {result.get('error')}"
        tl = load_timeline(str(output))
        assert tl.global_start_time == otio.opentime.RationalTime(3600.0 * FPS, FPS), (
            "global_start_time must be derived from the 01:00:00:00 timecode."
        )
        assert tl.metadata.get("Resolve_OTIO") is not None, (
            "Resolve_OTIO idempotency marker must be stamped on the timeline."
        )

    def test_a1_adopted_no_mirroring_skipped_warning(self, tmp_path: Path) -> None:
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        media_info = _make_media_info(str(media), start_timecode="01:00:00:00")

        with (
            patch(
                "clipwright_stabilize.stabilize.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            ),
        ):
            result = detect_shake(
                media=str(media),
                output=str(output),
                options=DetectShakeOptions(),
                timeline=None,
            )

        assert result["ok"] is True
        warnings = result.get("warnings") or []
        assert not any("skipped" in w for w in warnings), (
            f"A1 must be adopted (mirror-match), not skipped: {warnings}"
        )

        tl = load_timeline(str(output))
        assert len(tl.tracks) == 2, (
            "No extra audio track should be appended for 1 audio stream."
        )
        a1_clip = _sole_clip(_audio_track(tl))
        a1_meta = a1_clip.metadata.get("Resolve_OTIO")
        assert a1_meta is not None
        assert "Channels" in a1_meta, "Adopted A1 clip must carry Channels metadata."
        assert "Link Group ID" in a1_meta

    def test_v1_and_a1_shifted_by_same_offset(self, tmp_path: Path) -> None:
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        media_info = _make_media_info(str(media), start_timecode="01:00:00:00")

        with (
            patch(
                "clipwright_stabilize.stabilize.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            ),
        ):
            result = detect_shake(
                media=str(media),
                output=str(output),
                options=DetectShakeOptions(),
                timeline=None,
            )

        assert result["ok"] is True
        tl = load_timeline(str(output))
        v1_clip = _sole_clip(_video_track(tl))
        a1_clip = _sole_clip(_audio_track(tl))

        expected_start = otio.opentime.RationalTime(3600.0 * FPS, FPS)
        assert v1_clip.source_range.start_time == expected_start
        assert a1_clip.source_range.start_time == expected_start
        assert v1_clip.source_range.duration == a1_clip.source_range.duration


# ===========================================================================
# (b) Existing-timeline (accumulate) path must never be conformed (NFR-4)
# ===========================================================================


class TestExistingTimelineNotConformed:
    def test_existing_timeline_is_not_conformed(self, tmp_path: Path) -> None:
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        tl_in = otio.schema.Timeline(name="existing")
        v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
        tl_in.tracks.append(v1)
        tl_in.tracks.append(a1)
        source_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, FPS),
            duration=otio.opentime.RationalTime(10.0 * FPS, FPS),
        )
        ref = otio.schema.ExternalReference(target_url=str(media.resolve()))
        original_clip = otio.schema.Clip(
            name=media.name, media_reference=ref, source_range=source_range
        )
        v1.append(original_clip)
        timeline_path = tmp_path / "existing.otio"
        otio.adapters.write_to_file(tl_in, str(timeline_path))

        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        media_info = _make_media_info(str(media), start_timecode="01:00:00:00")

        with (
            patch(
                "clipwright_stabilize.stabilize.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            ),
        ):
            result = detect_shake(
                media=str(media),
                output=str(output),
                options=DetectShakeOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is True, f"detect_shake failed: {result.get('error')}"
        tl_out = load_timeline(str(output))

        assert tl_out.metadata.get("Resolve_OTIO") is None, (
            "Existing timeline (accumulate path) must not gain Resolve_OTIO metadata."
        )
        assert tl_out.global_start_time is None, (
            "Existing timeline (accumulate path) must not gain global_start_time."
        )
        assert len(tl_out.tracks) == 2, "Track count must stay unchanged (NFR-4)."

        v1_clip = _sole_clip(_video_track(tl_out))
        assert v1_clip.source_range.start_time == otio.opentime.RationalTime(
            0.0, FPS
        ), "Pre-existing clip's source_range must not be timecode-shifted."
        assert dict(v1_clip.metadata) == {}, (
            "Pre-existing clip metadata must be unchanged (NFR-4)."
        )


# ===========================================================================
# (c) New-creation path, no timecode: no shift, audio metadata still stamped
# ===========================================================================


class TestNewTimelineWithoutTimecode:
    def test_no_shift_no_global_start_time(self, tmp_path: Path) -> None:
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        media_info = _make_media_info(str(media), start_timecode=None)

        with (
            patch(
                "clipwright_stabilize.stabilize.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            ),
        ):
            result = detect_shake(
                media=str(media),
                output=str(output),
                options=DetectShakeOptions(),
                timeline=None,
            )

        assert result["ok"] is True
        tl = load_timeline(str(output))
        assert tl.global_start_time is None

        v1_clip = _sole_clip(_video_track(tl))
        assert v1_clip.source_range.start_time == otio.opentime.RationalTime(0.0, FPS)

    def test_audio_metadata_still_stamped_without_timecode(
        self, tmp_path: Path
    ) -> None:
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        trf_abs = tmp_path / "video.stabilize.trf"
        media_info = _make_media_info(str(media), start_timecode=None)

        with (
            patch(
                "clipwright_stabilize.stabilize.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_stabilize.stabilize.run_vidstabdetect",
                lambda media_path, output_path, options: _fake_analyze_result(trf_abs),
            ),
        ):
            result = detect_shake(
                media=str(media),
                output=str(output),
                options=DetectShakeOptions(),
                timeline=None,
            )

        assert result["ok"] is True
        tl = load_timeline(str(output))

        assert tl.metadata.get("Resolve_OTIO") is not None, (
            "Idempotency marker must be stamped even without timecode."
        )
        v1_clip = _sole_clip(_video_track(tl))
        assert v1_clip.metadata.get("Resolve_OTIO", {}).get("Link Group ID") == 1

        a1_clip = _sole_clip(_audio_track(tl))
        a1_meta = a1_clip.metadata.get("Resolve_OTIO")
        assert a1_meta is not None
        assert "Channels" in a1_meta
