"""test_pathpolicy_noise.py — Path-boundary constraint tests for detect_noise.

Verifies that detect_noise accepts output in any directory and that media_ref_for_otio
is applied correctly, while maintaining all other safety invariants including timeline
source validation.

Test ID:
  DP-N-1  test_external_dir_output_allowed              external dir -> ok
  DP-N-2  test_clip_target_url_absolute_external_media  media outside -> abs target_url
  DP-N-7  test_timeline_validation_maintained_across_dirs  timeline across dirs -> ok
  DP-N-3  test_output_equals_media_still_rejected       regression guard
  DP-N-4  test_output_equals_timeline_still_rejected    regression guard
  DP-N-5  test_non_otio_extension_still_rejected        regression guard
  DP-N-6  test_missing_parent_dir_still_rejected        regression guard
  DP-N-8  test_clip_target_url_relative_inside_media    media inside -> rel POSIX target_url
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
from clipwright.errors import ErrorCode
from clipwright.otio_utils import load_timeline
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo, ToolResult

from clipwright_noise.schemas import DetectNoiseOptions

# ===========================================================================
# Helpers
# ===========================================================================

FPS = 30.0

_FAKE_MEASURE_RESULT = {
    "params": {"nr": 12.0, "nf": -50.0, "nt": "w"},
    "measured_noise_floor_db": -50.0,
    "warnings": [],
}


def _d(result: ToolResult) -> dict:  # type: ignore[type-arg]
    """Convert a ToolResult to a plain dict for assertion compatibility."""
    return result.model_dump()


def _make_media_info(
    path: str,
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
) -> MediaInfo:
    """Build a MediaInfo with one video + one audio stream."""
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


def _make_otio_timeline(
    media_path: Path,
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
) -> otio.schema.Timeline:
    """Create a minimal OTIO timeline with one clip whose target_url is media_path (absolute)."""
    tl = otio.schema.Timeline(name="test")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    tl.tracks.append(track)
    source_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, rate),
        duration=otio.opentime.RationalTime(duration_sec * rate, rate),
    )
    try:
        target_url = str(media_path.resolve())
    except OSError:
        target_url = str(media_path.absolute())
    ref = otio.schema.ExternalReference(target_url=target_url)
    clip = otio.schema.Clip(
        name=media_path.name, media_reference=ref, source_range=source_range
    )
    track.append(clip)
    return tl


# ===========================================================================
# DP-N-1 / DP-N-2: external dir output allowed + absolute media ref
# ===========================================================================


class TestExternalDirOutput:
    """Output placed in a directory different from media must be accepted."""

    def test_external_dir_output_allowed(self, tmp_path: Path) -> None:
        """DP-N-1: detect_noise succeeds when output dir differs from media dir."""
        from clipwright_noise.noise import detect_noise

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "work"
        out_dir.mkdir()
        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")
        output = out_dir / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            result = detect_noise(
                str(media), str(output), DetectNoiseOptions(), timeline=None
            )

        d = _d(result)
        assert d["ok"] is True

    def test_clip_target_url_absolute_external_media(self, tmp_path: Path) -> None:
        """DP-N-2: When media is outside otio_dir, target_url must be the absolute path.

        Verifies media_ref_for_otio rule: media outside otio_dir -> absolute path.
        """
        from clipwright_noise.noise import detect_noise

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "work"
        out_dir.mkdir()
        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")
        output = out_dir / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            result = detect_noise(
                str(media), str(output), DetectNoiseOptions(), timeline=None
            )

        d = _d(result)
        assert d["ok"] is True

        tl = load_timeline(str(output))
        v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert clips, "No clips in V1 after detect_noise succeeded"
        for clip in clips:
            ref = clip.media_reference
            assert isinstance(ref, otio.schema.ExternalReference)
            ref_path = Path(ref.target_url)
            assert ref_path.is_absolute(), (
                "target_url must be absolute when media is outside otio_dir; "
                f"got {ref.target_url!r}"
            )
            try:
                resolved = str(ref_path.resolve())
            except OSError:
                resolved = str(ref_path.absolute())
            assert resolved == str(media.resolve()), (
                f"target_url resolved to {resolved!r}, expected {media.resolve()!r}"
            )


# ===========================================================================
# DP-N-7: timeline source validation maintained when dirs differ
# ===========================================================================


class TestTimelineAcrossDirs:
    """Timeline source validation must remain intact even when output dir != media dir."""

    def test_timeline_validation_maintained_across_dirs(self, tmp_path: Path) -> None:
        """DP-N-7: detect_noise with a matching-source timeline across dirs succeeds.

        Verifies that timeline source validation still works when media, timeline, and
        output are in different directories.
        """
        from clipwright_noise.noise import detect_noise

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "work"
        out_dir.mkdir()
        tl_dir = tmp_path / "timeline"
        tl_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")

        # Build a timeline whose V1 clip references media via absolute path
        tl = _make_otio_timeline(media)
        timeline_file = tl_dir / "silence.otio"
        otio.adapters.write_to_file(tl, str(timeline_file))

        output = out_dir / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_file),
            )

        d = _d(result)
        assert d["ok"] is True


# ===========================================================================
# DP-N-3..DP-N-6: regression guards (already pass; must not regress)
# ===========================================================================


class TestRegressionGuards:
    """Removing the same-dir constraint must not weaken other path safety invariants."""

    def test_output_equals_media_still_rejected(self, tmp_path: Path) -> None:
        """DP-N-3: output path identical to media path must remain INVALID_INPUT."""
        from clipwright_noise.noise import detect_noise

        media = tmp_path / "video.otio"
        media.write_bytes(b"dummy")

        result = detect_noise(
            str(media), str(media), DetectNoiseOptions(), timeline=None
        )

        d = _d(result)
        assert d["ok"] is False
        assert d["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_output_equals_timeline_still_rejected(self, tmp_path: Path) -> None:
        """DP-N-4: output path identical to timeline path must remain INVALID_INPUT."""
        from clipwright_noise.noise import detect_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        timeline_path = tmp_path / "timeline.otio"
        timeline_path.write_bytes(b"dummy")

        result = detect_noise(
            str(media),
            str(timeline_path),
            DetectNoiseOptions(),
            timeline=str(timeline_path),
        )

        d = _d(result)
        assert d["ok"] is False
        assert d["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_non_otio_extension_still_rejected(self, tmp_path: Path) -> None:
        """DP-N-5: output with non-.otio extension must remain INVALID_INPUT."""
        from clipwright_noise.noise import detect_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.mp4"

        result = detect_noise(
            str(media), str(output), DetectNoiseOptions(), timeline=None
        )

        d = _d(result)
        assert d["ok"] is False
        assert d["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_missing_parent_dir_still_rejected(self, tmp_path: Path) -> None:
        """DP-N-6: output whose parent directory does not exist must remain rejected."""
        from clipwright_noise.noise import detect_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "nonexistent_dir" / "out.otio"

        result = detect_noise(
            str(media), str(output), DetectNoiseOptions(), timeline=None
        )

        d = _d(result)
        assert d["ok"] is False
        assert d["error"]["code"] in (
            ErrorCode.INVALID_INPUT,
            ErrorCode.FILE_NOT_FOUND,
        )


# ===========================================================================
# DP-N-8: media inside otio_dir -> relative POSIX target_url
# ===========================================================================


class TestInsideMediaRef:
    """When media is inside the OTIO output directory, target_url must be relative."""

    def test_clip_target_url_relative_inside_media(self, tmp_path: Path) -> None:
        """DP-N-8: When media is under otio_dir, clip target_url must be relative POSIX.

        Verifies media_ref_for_otio rule: media inside otio_dir -> relative POSIX path.
        This enables the OTIO file and its media to be moved together as a project unit.
        """
        from clipwright_noise.noise import detect_noise

        out_dir = tmp_path / "work"
        out_dir.mkdir()
        # Media is placed inside the same directory as the OTIO output.
        media = out_dir / "video.mp4"
        media.write_bytes(b"dummy")
        output = out_dir / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            result = detect_noise(
                str(media), str(output), DetectNoiseOptions(), timeline=None
            )

        d = _d(result)
        assert d["ok"] is True

        tl = load_timeline(str(output))
        v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert clips, "No clips in V1 after detect_noise succeeded"
        for clip in clips:
            ref = clip.media_reference
            assert isinstance(ref, otio.schema.ExternalReference)
            ref_path = Path(ref.target_url)
            assert not ref_path.is_absolute(), (
                "target_url must be relative when media is inside otio_dir; "
                f"got {ref.target_url!r}"
            )
            # Relative path must resolve back to the original media file.
            resolved = str((out_dir / ref_path).resolve())
            assert resolved == str(media.resolve()), (
                f"target_url {ref.target_url!r} resolved to {resolved!r}, "
                f"expected {media.resolve()!r}"
            )
