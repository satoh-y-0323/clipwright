"""test_pathpolicy_loudness.py — Path-boundary policy tests for clipwright-loudness.

Tests the new behavior after pathpolicy migration (spec4 #5, DC-AS-004 / DC-AM-004):
  - Output can be placed in a different directory from the media file (create type).
  - Written OTIO target_url follows media_ref_for_otio() rule:
      relative POSIX when media is under output directory; absolute otherwise.
  - Timeline input validated with check_media_ref:
      absolute existing sources are accepted; relative traversal rejected (CWE-22).
  - output==media and output==timeline remain rejected (non-destructive invariant).

All tests in TestOutputInDifferentDirAllowed, TestMediaRefForOtioRule, and
TestCheckMediaRefNewPolicy.test_absolute_external_source_in_timeline_allowed are RED
(failing) against the pre-migration implementation because:
  - inline same-dir block (loudness.py L121-136) returns INVALID_INPUT for
    different-directory output.
  - _add_full_clip() always writes str(media_path.resolve()) (absolute), ignoring
    the otio_dir relationship.
  - _check_source_within_timeline_dir() raises PATH_NOT_ALLOWED for absolute sources
    that resolve outside the timeline parent directory.

TestCheckMediaRefNewPolicy.test_relative_traversal_in_timeline_rejected and
TestOutputConflictPreserved are regression guards (expected to remain passing after
implementation).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
from clipwright.errors import ErrorCode
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_loudness.schemas import DetectLoudnessOptions

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FPS = 30.0
_TEST_BIT_RATE = 8_000_000

_FAKE_LOUDNORM_MEASURED: dict = {
    "measured": {
        "input_i": -21.75,
        "input_tp": -18.06,
        "input_lra": 0.0,
        "input_thresh": -31.75,
        "target_offset": 0.03,
    },
    "warnings": [],
}


def _make_media_info(
    path: str,
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
    has_video: bool = True,
    has_audio: bool = True,
) -> MediaInfo:
    """Construct a MediaInfo for testing."""
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(StreamInfo(index=0, codec_type="video", codec_name="h264"))
    if has_audio:
        streams.append(
            StreamInfo(index=len(streams), codec_type="audio", codec_name="aac")
        )
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=duration_sec * rate, rate=rate),
        streams=streams,
        bit_rate=_TEST_BIT_RATE,
    )


def _build_timeline_with_absolute_source(
    media_abs: Path,
    *,
    fps: float = FPS,
) -> otio.schema.Timeline:
    """Build a V1+A1 OTIO timeline with an absolute source pointing to *media_abs*."""
    tl = otio.schema.Timeline(name="test")
    for kind, name in (
        (otio.schema.TrackKind.Video, "V1"),
        (otio.schema.TrackKind.Audio, "A1"),
    ):
        tl.tracks.append(otio.schema.Track(name=name, kind=kind))

    v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
    src_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, fps),
        duration=otio.opentime.RationalTime(300.0, fps),
    )
    clip = otio.schema.Clip(
        name=media_abs.name,
        media_reference=otio.schema.ExternalReference(target_url=str(media_abs)),
        source_range=src_range,
    )
    v1.append(clip)
    return tl


# ===========================================================================
# DC-AS-004: output in different dir from media must now succeed
# ===========================================================================


class TestOutputInDifferentDirAllowed:
    """DC-AS-004: output may be placed in a different directory from media (create type).

    RED against pre-migration code: inline same-dir block (loudness.py L121-136)
    raises INVALID_INPUT before any probe or OTIO I/O occurs.
    """

    def test_output_in_different_dir_no_timeline_succeeds(self, tmp_path: Path) -> None:
        """output in different dir from media, timeline=None: must return ok=True.

        RED: current implementation returns INVALID_INPUT (same-dir block L121-136).
        """
        from clipwright_loudness.loudness import detect_loudness

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "work"
        out_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")
        output = out_dir / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            result = detect_loudness(
                str(media), str(output), DetectLoudnessOptions(), timeline=None
            )

        assert result.ok is True, (
            "DC-AS-004: output in different dir from media must succeed after"
            f" pathpolicy migration. error={result.error}"
        )

    def test_output_in_different_dir_with_timeline_colocated_succeeds(
        self, tmp_path: Path
    ) -> None:
        """output in different dir from media, timeline in media dir: must return ok=True.

        The timeline is placed in the same directory as media so that the timeline
        source (media) passes the timeline-dir boundary check; only the same-dir
        output block (L121-136) causes the RED failure.
        """
        from clipwright_loudness.loudness import detect_loudness

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "work"
        out_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")

        # Timeline in media_dir with absolute source = media (passes old boundary check)
        tl = _build_timeline_with_absolute_source(media.resolve())
        timeline_path = media_dir / "base.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        output = out_dir / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        assert result.ok is True, (
            "DC-AS-004: output in different dir with timeline provided must succeed."
            f" error={result.error}"
        )


# ===========================================================================
# DC-AM-004: media_ref_for_otio() rule for written OTIO target_url
# ===========================================================================


class TestMediaRefForOtioRule:
    """DC-AM-004: written OTIO target_url must follow media_ref_for_otio() rule.

    Rule: relative POSIX when media is inside the output directory tree;
          absolute path when media is outside the output directory tree.
    """

    def test_media_ref_relative_when_media_in_otio_dir(self, tmp_path: Path) -> None:
        """When media is co-located with the output OTIO, target_url must be relative.

        RED: current _add_full_clip() always writes str(media_path.resolve()),
        which is absolute regardless of whether media is inside the output directory.
        """
        from clipwright_loudness.loudness import detect_loudness

        # media and output in the same directory — media IS under otio_dir
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            result = detect_loudness(
                str(media), str(output), DetectLoudnessOptions(), timeline=None
            )

        assert result.ok is True, f"Prerequisite failed: {result.error}"

        tl = otio.adapters.read_from_file(str(output))
        v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert clips, "V1 must have at least one clip."
        ref = clips[0].media_reference
        assert isinstance(ref, otio.schema.ExternalReference)
        target_url = ref.target_url
        assert not Path(target_url).is_absolute(), (
            "DC-AM-004: media is inside otio_dir; target_url must be a relative POSIX"
            f" path, not absolute. Got: {target_url!r}"
        )

    def test_media_ref_absolute_when_media_outside_otio_dir(
        self, tmp_path: Path
    ) -> None:
        """When media is outside the output directory, target_url must be absolute.

        RED: fails because same-dir block (L121-136) prevents OTIO creation when
        output is in a different directory from media (ok=False rather than ok=True).
        """
        from clipwright_loudness.loudness import detect_loudness

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "work"
        out_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")
        output = out_dir / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            result = detect_loudness(
                str(media), str(output), DetectLoudnessOptions(), timeline=None
            )

        assert result.ok is True, (
            "DC-AM-004/DC-AS-004: output in different dir must succeed."
            f" error={result.error}"
        )

        tl = otio.adapters.read_from_file(str(output))
        v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert clips, "V1 must have at least one clip."
        ref = clips[0].media_reference
        assert isinstance(ref, otio.schema.ExternalReference)
        target_url = ref.target_url
        assert Path(target_url).is_absolute(), (
            "DC-AM-004: media is outside otio_dir; target_url must be absolute."
            f" Got: {target_url!r}"
        )


# ===========================================================================
# check_media_ref: new policy for OTIO source reference validation
# ===========================================================================


class TestCheckMediaRefNewPolicy:
    """New check_media_ref policy replaces _check_source_within_timeline_dir.

    Absolute paths to existing regular files are accepted regardless of directory;
    relative path traversal is still rejected (CWE-22 guard maintained).
    """

    def test_absolute_external_source_in_timeline_allowed(self, tmp_path: Path) -> None:
        """Timeline with absolute source outside timeline dir must be accepted.

        Setup: media in media_dir; timeline saved in timeline_dir with absolute
        source pointing to media in media_dir; output in media_dir (same as media
        to isolate from the same-dir block and focus on check_media_ref behavior).

        RED: current _check_source_within_timeline_dir raises PATH_NOT_ALLOWED
        because the absolute source resolves outside the timeline parent directory.
        After migration: check_media_ref accepts absolute existing regular files.
        """
        from clipwright_loudness.loudness import detect_loudness

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        timeline_dir = tmp_path / "timeline"
        timeline_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")

        # Timeline saved in timeline_dir; clip source = absolute path to media in media_dir
        tl = _build_timeline_with_absolute_source(media.resolve())
        timeline_path = timeline_dir / "base.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        # Output in media_dir (same dir as media) so the same-dir block does not fire
        output = media_dir / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        assert result.ok is True, (
            "check_media_ref must accept absolute existing source outside timeline dir."
            f" error={result.error}"
        )

    def test_relative_traversal_in_timeline_rejected(self, tmp_path: Path) -> None:
        """Relative path traversal (../) in OTIO source must remain PATH_NOT_ALLOWED.

        Regression guard: both old _check_source_within_timeline_dir and new
        check_media_ref must reject relative traversal (CWE-22 guard).
        """
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        # Build timeline with relative traversal source
        tl = otio.schema.Timeline(name="test")
        v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
        tl.tracks.append(v1)
        tl.tracks.append(a1)
        src_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, FPS),
            duration=otio.opentime.RationalTime(300.0, FPS),
        )
        clip = otio.schema.Clip(
            name="outside.mp4",
            media_reference=otio.schema.ExternalReference(target_url="../outside.mp4"),
            source_range=src_range,
        )
        v1.append(clip)

        timeline_path = tmp_path / "bad.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with patch(
            "clipwright_loudness.loudness.inspect_media", return_value=media_info
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.PATH_NOT_ALLOWED, (
            "CWE-22: relative traversal in OTIO source must remain PATH_NOT_ALLOWED."
            f" Got: {result.error.code!r}"
        )


# ===========================================================================
# Regression guards: non-destructive invariants preserved
# ===========================================================================


class TestOutputConflictPreserved:
    """output==media and output==timeline must remain rejected after pathpolicy migration."""

    def test_output_equals_media_still_rejected(self, tmp_path: Path) -> None:
        """output==media must still be rejected (non-destructive invariant)."""
        from clipwright_loudness.loudness import detect_loudness

        # Use .otio extension to avoid the extension-check firing before the conflict check
        media = tmp_path / "video.otio"
        media.write_bytes(b"dummy")

        result = detect_loudness(
            str(media), str(media), DetectLoudnessOptions(), timeline=None
        )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code in (
            ErrorCode.INVALID_INPUT,
            ErrorCode.PATH_NOT_ALLOWED,
        ), f"output==media must be rejected. Got code: {result.error.code!r}"

    def test_output_equals_timeline_still_rejected(self, tmp_path: Path) -> None:
        """output==timeline must still be rejected (non-destructive invariant)."""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        timeline_path = tmp_path / "timeline.otio"
        timeline_path.write_bytes(b"dummy")

        result = detect_loudness(
            str(media),
            str(timeline_path),
            DetectLoudnessOptions(),
            timeline=str(timeline_path),
        )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code in (
            ErrorCode.INVALID_INPUT,
            ErrorCode.PATH_NOT_ALLOWED,
        ), f"output==timeline must be rejected. Got code: {result.error.code!r}"
