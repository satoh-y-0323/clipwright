"""test_pathpolicy_color.py — Path-boundary policy tests for clipwright-color.

Tests the new behavior after pathpolicy migration (spec4 #5, DC-AS-004 / DC-AM-004):
  - Output can be placed in a different directory from the media file (create type).
  - Written OTIO target_url follows media_ref_for_otio() rule:
      relative POSIX when media is under output directory; absolute otherwise.
  - Timeline input validated with check_media_ref:
      absolute existing sources are accepted; relative traversal rejected (CWE-22).
  - output==media and output==timeline remain rejected (non-destructive invariant).

TestCheckMediaRefNewPolicy.test_relative_traversal_in_timeline_rejected and
TestOutputConflictPreserved are regression guards (expected to remain passing).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest
from clipwright.errors import ErrorCode
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_color.schemas import (  # type: ignore[import-not-found]
    DetectColorOptions,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FPS = 30.0
_TEST_BIT_RATE = 8_000_000


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


def _fake_measured(yavg: float = 96.4) -> dict[str, Any]:
    """Return a fake measure_brightness result dict."""
    return {
        "measured": {
            "yavg": yavg,
            "ymin": 9.0,
            "ymax": 242.0,
            "sampled_frames": 12,
        },
        "warnings": [],
    }


def _build_v1a1_timeline_with_relative_source(
    target_url: str,
    *,
    fps: float = FPS,
) -> otio.schema.Timeline:
    """Build a V1+A1 OTIO timeline with a relative POSIX target_url.

    Simulates output of media_ref_for_otio() when media is co-located with
    the OTIO file (e.g., target_url="video.mp4" — basename only, no separators).
    """
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
        name=target_url,
        media_reference=otio.schema.ExternalReference(target_url=target_url),
        source_range=src_range,
    )
    v1.append(clip)
    return tl


def _build_v1a1_timeline_with_absolute_source(
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
    """DC-AS-004: output may be placed in a different directory from media (create type)."""

    def test_output_in_different_dir_succeeds(self, tmp_path: Path) -> None:
        """output in different dir from media, timeline=None: must return ok=True."""
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "work"
        out_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")
        output = out_dir / "out.otio"
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=100.0),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True, (
            "DC-AS-004: output in different dir from media must succeed after"
            f" pathpolicy migration. error={result.get('error')}"
        )

    def test_output_in_different_dir_with_timeline_colocated_succeeds(
        self, tmp_path: Path
    ) -> None:
        """output in different dir from media, timeline in media dir: must return ok=True."""
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "work"
        out_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")

        # Timeline in media_dir with absolute source = media (passes old boundary check)
        tl = _build_v1a1_timeline_with_absolute_source(media.resolve())
        timeline_path = media_dir / "base.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        output = out_dir / "out.otio"
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=100.0),
            )
            result = detect_color(
                media=str(media),
                output=str(output),
                options=opts,
                timeline=str(timeline_path),
            )

        assert result["ok"] is True, (
            "DC-AS-004: output in different dir with timeline provided must succeed."
            f" error={result.get('error')}"
        )


# ===========================================================================
# DC-AM-004: media_ref_for_otio() rule for written OTIO target_url
# ===========================================================================


class TestMediaRefForOtioRule:
    """DC-AM-004: written OTIO target_url must follow media_ref_for_otio() rule."""

    def test_media_ref_relative_when_media_in_otio_dir(self, tmp_path: Path) -> None:
        """When media is co-located with the output OTIO, target_url must be relative."""
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        # media and output in the same directory — media IS under otio_dir
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=100.0),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True, f"Prerequisite failed: {result.get('error')}"

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
        """When media is outside the output directory, target_url must be absolute."""
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "work"
        out_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")
        output = out_dir / "out.otio"
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=100.0),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True, (
            "DC-AM-004/DC-AS-004: output in different dir must succeed."
            f" error={result.get('error')}"
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
    """New check_media_ref policy replaces _check_source_within_timeline_dir."""

    def test_absolute_external_source_in_timeline_allowed(self, tmp_path: Path) -> None:
        """Timeline with absolute source outside timeline dir must be accepted.

        Setup: media in media_dir; timeline in timeline_dir with absolute source
        pointing to media; output in media_dir (same as media to isolate from the
        same-dir block and focus on check_media_ref behavior).

        After migration: check_media_ref accepts absolute existing regular files.
        """
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        timeline_dir = tmp_path / "timeline"
        timeline_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")

        # Timeline in timeline_dir; source = absolute path to media in media_dir
        tl = _build_v1a1_timeline_with_absolute_source(media.resolve())
        timeline_path = timeline_dir / "base.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        # Output in media_dir (same as media) so the same-dir block does not fire
        output = media_dir / "out.otio"
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=100.0),
            )
            result = detect_color(
                media=str(media),
                output=str(output),
                options=opts,
                timeline=str(timeline_path),
            )

        assert result["ok"] is True, (
            "check_media_ref must accept absolute existing source outside timeline dir."
            f" error={result.get('error')}"
        )

    def test_relative_traversal_in_timeline_rejected(self, tmp_path: Path) -> None:
        """Relative path traversal (../) in OTIO source must remain PATH_NOT_ALLOWED.

        Regression guard: CWE-22 guard must be maintained after migration.
        """
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

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
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            result = detect_color(
                media=str(media),
                output=str(output),
                options=opts,
                timeline=str(timeline_path),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED, (
            "CWE-22: relative traversal in OTIO source must remain PATH_NOT_ALLOWED."
            f" Got: {result['error']['code']!r}"
        )


# ===========================================================================
# Regression guards: non-destructive invariants preserved
# ===========================================================================


class TestOutputConflictPreserved:
    """output==media and output==timeline must remain rejected after pathpolicy migration."""

    def test_output_equals_media_still_rejected(self, tmp_path: Path) -> None:
        """output==media must still be rejected (non-destructive invariant)."""
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        # Use .otio extension to avoid the extension-check firing before conflict check
        media = tmp_path / "video.otio"
        media.write_bytes(b"dummy")
        opts = DetectColorOptions()

        result = detect_color(
            media=str(media), output=str(media), options=opts, timeline=None
        )

        assert result["ok"] is False
        assert result["error"]["code"] in (
            ErrorCode.INVALID_INPUT,
            ErrorCode.PATH_NOT_ALLOWED,
        ), f"output==media must be rejected. Got: {result['error']['code']!r}"

    def test_output_equals_timeline_still_rejected(self, tmp_path: Path) -> None:
        """output==timeline must still be rejected (non-destructive invariant)."""
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        timeline_path = tmp_path / "timeline.otio"
        timeline_path.write_bytes(b"dummy")
        opts = DetectColorOptions()

        result = detect_color(
            media=str(media),
            output=str(timeline_path),
            options=opts,
            timeline=str(timeline_path),
        )

        assert result["ok"] is False
        assert result["error"]["code"] in (
            ErrorCode.INVALID_INPUT,
            ErrorCode.PATH_NOT_ALLOWED,
        ), f"output==timeline must be rejected. Got: {result['error']['code']!r}"


# ===========================================================================
# spec5 D1 regression: CWD-independent timeline source matching
# ===========================================================================


class TestCwdIndependentTimelineMatch:
    """Regression guard for spec5 D1: relative target_url must be resolved against
    otio_dir, not CWD.

    After the fix (check_timeline_source_matches), relative target_url is joined
    against otio_dir, making the comparison CWD-independent.
    AC-1 verifies the matching source succeeds regardless of CWD.
    AC-2 confirms a genuinely mismatched source is still rejected.
    """

    def test_ac1_relative_source_colocated_different_cwd_succeeds(
        self, tmp_path: Path
    ) -> None:
        """AC-1: media and timeline co-located in otio_dir, CWD elsewhere -> ok=True.

        Verifies spec5 D1 fix (ADR-D1-1): relative target_url is resolved against
        otio_dir, not CWD, so the match succeeds even when CWD differs from otio_dir.
        """
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        other_cwd = tmp_path / "elsewhere"
        other_cwd.mkdir()

        media = otio_dir / "video.mp4"
        media.write_bytes(b"dummy")

        # target_url is just the basename — same as media_ref_for_otio() output
        # when media is inside the OTIO output directory.
        tl = _build_v1a1_timeline_with_relative_source("video.mp4")
        timeline_path = otio_dir / "base.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        output = otio_dir / "out.otio"
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.chdir(other_cwd)
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=100.0),
            )
            result = detect_color(
                media=str(media),
                output=str(output),
                options=opts,
                timeline=str(timeline_path),
            )

        assert result["ok"] is True, (
            "spec5 D1: relative target_url must be resolved against otio_dir, not CWD."
            " With CWD != otio_dir, the current code raises INVALID_INPUT."
            f" error={result.get('error')}"
        )

    def test_ac2_relative_source_mismatch_different_cwd_fails(
        self, tmp_path: Path
    ) -> None:
        """AC-2: timeline has a different relative source basename -> ok=False, INVALID_INPUT.

        Even after the D1 fix, a genuinely mismatched source must still be rejected
        with INVALID_INPUT.
        """
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        otio_dir = tmp_path / "project"
        otio_dir.mkdir()
        other_cwd = tmp_path / "elsewhere"
        other_cwd.mkdir()

        media = otio_dir / "video.mp4"
        media.write_bytes(b"dummy")

        # timeline references a *different* file — this must always be rejected.
        tl = _build_v1a1_timeline_with_relative_source("other_clip.mp4")
        timeline_path = otio_dir / "base.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        output = otio_dir / "out.otio"
        opts = DetectColorOptions()

        with pytest.MonkeyPatch().context() as mp:
            mp.chdir(other_cwd)
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=100.0),
            )
            result = detect_color(
                media=str(media),
                output=str(output),
                options=opts,
                timeline=str(timeline_path),
            )

        assert result["ok"] is False, (
            "AC-2: mismatched relative source must be rejected even after D1 fix."
        )
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT, (
            f"AC-2: expected INVALID_INPUT, got {result['error']['code']!r}"
        )
        # SR-R-001: CWE-209 regression guard — input media filename must not leak into error message.
        assert "video" not in result["error"]["message"]
