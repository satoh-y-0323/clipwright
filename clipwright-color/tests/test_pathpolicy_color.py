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

        check_media_ref accepts absolute existing regular files.
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

        Regression guard: the CWE-22 traversal guard must hold.
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

    check_timeline_source_matches resolves relative target_url against otio_dir,
    making the comparison CWD-independent.
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
            " With CWD != otio_dir, the relative source must still resolve against otio_dir and succeed."
            f" error={result.get('error')}"
        )

    def test_ac2_relative_source_mismatch_different_cwd_fails(
        self, tmp_path: Path
    ) -> None:
        """AC-2: timeline has a different relative source basename -> ok=False, INVALID_INPUT.

        A genuinely mismatched source must be rejected with INVALID_INPUT.
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
        # SR-R-001: CWE-209 regression guard — input filenames must not leak into error message.
        # G-2: canonical error message must be the fixed sentinel string.
        assert (
            "Timeline source file does not match input media."
            in result["error"]["message"]
        )
        assert "video" not in result["error"]["message"]
        assert "other_clip" not in result["error"]["message"]


# ===========================================================================
# .cube / LUT path-boundary tests (AC-4 / AC-7 / CWE-59 / ADR-CO-10)
# ===========================================================================


class TestLutCubePathpolicy:
    """.cube LUT path validation: storage form, symlink rejection, traversal, CWE-209."""

    # -------------------------------------------------------------------------
    # Valid .cube — stored as relative POSIX inside OTIO dir (AC-4)
    # -------------------------------------------------------------------------

    def test_valid_cube_inside_otio_dir_stored_relative(self, tmp_path: Path) -> None:
        """Valid .cube co-located with output OTIO must be stored as relative POSIX path.

        directive.lut must be a relative POSIX path (no backslash, no leading '/'),
        using media_ref_for_otio semantics.
        """
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        cube_file = tmp_path / "grade.cube"
        cube_file.write_text("# dummy cube")
        output = tmp_path / "out.otio"

        opts = DetectColorOptions(  # type: ignore[call-arg]
            target_luma=128.0,
            lut=str(cube_file),
        )

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=128.0),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True, f"error={result.get('error')}"
        import opentimelineio as otio

        tl = otio.adapters.read_from_file(str(output))
        color_meta = tl.metadata["clipwright"]["color"]
        lut_stored = color_meta.get("lut")
        assert lut_stored is not None, (
            "AC-4: directive.lut must be written for a valid .cube co-located with OTIO."
        )
        assert not Path(lut_stored).is_absolute(), (
            "DC-AM-004: .cube inside OTIO dir must be stored as relative POSIX path."
            f" Got: {lut_stored!r}"
        )
        assert "\\" not in lut_stored, (
            "Stored lut path must use forward slashes (POSIX), not backslashes."
            f" Got: {lut_stored!r}"
        )

    def test_valid_cube_outside_otio_dir_stored_absolute(self, tmp_path: Path) -> None:
        """Valid .cube outside the output directory must be stored as absolute path."""
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        lut_dir = tmp_path / "luts"
        lut_dir.mkdir()
        cube_file = lut_dir / "grade.cube"
        cube_file.write_text("# dummy cube")

        out_dir = tmp_path / "work"
        out_dir.mkdir()
        media = out_dir / "video.mp4"
        media.write_bytes(b"dummy")
        output = out_dir / "out.otio"

        opts = DetectColorOptions(  # type: ignore[call-arg]
            target_luma=128.0,
            lut=str(cube_file),
        )

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=128.0),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is True, f"error={result.get('error')}"
        import opentimelineio as otio

        tl = otio.adapters.read_from_file(str(output))
        lut_stored = tl.metadata["clipwright"]["color"].get("lut")
        assert lut_stored is not None, "directive.lut must be written"
        assert Path(lut_stored).is_absolute(), (
            "DC-AM-004: .cube outside OTIO dir must be stored as absolute path."
            f" Got: {lut_stored!r}"
        )

    # -------------------------------------------------------------------------
    # Symlink .cube rejected (CWE-59)
    # -------------------------------------------------------------------------

    def test_symlink_cube_rejected(self, tmp_path: Path) -> None:
        """A .cube that is a symlink must be rejected (CWE-59 / ADR-CO-10).

        Expected outcome: FILE_NOT_FOUND or PATH_NOT_ALLOWED.
        """

        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"

        real_cube = tmp_path / "real.cube"
        real_cube.write_text("# dummy cube")
        symlink_cube = tmp_path / "link.cube"
        try:
            symlink_cube.symlink_to(real_cube)
        except OSError:
            pytest.skip("Symlink creation not available on this system")

        opts = DetectColorOptions(  # type: ignore[call-arg]
            target_luma=128.0,
            lut=str(symlink_cube),
        )

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=128.0),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is False, "CWE-59: symlink .cube must be rejected."
        assert result["error"]["code"] in (
            ErrorCode.FILE_NOT_FOUND.value,
            ErrorCode.PATH_NOT_ALLOWED.value,
            ErrorCode.INVALID_INPUT.value,
        ), f"Unexpected error code: {result['error']['code']!r}"

    # -------------------------------------------------------------------------
    # Directory traversal in .cube path rejected
    # -------------------------------------------------------------------------

    def test_traversal_cube_path_rejected(self, tmp_path: Path) -> None:
        """A .cube path containing ../ traversal must be rejected (CWE-22 / CWE-59)."""
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"

        traversal_path = str(tmp_path / ".." / "outside.cube")
        opts = DetectColorOptions(  # type: ignore[call-arg]
            target_luma=128.0,
            lut=traversal_path,
        )

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=128.0),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        # Accept either FILE_NOT_FOUND or PATH_NOT_ALLOWED (traversal rejected).
        assert result["ok"] is False, (
            "Directory traversal ../ in .cube path must be rejected."
        )

    # -------------------------------------------------------------------------
    # AC-7 / CWE-209: error message must NOT expose the full .cube path
    # -------------------------------------------------------------------------

    def test_cube_validation_error_does_not_leak_full_path(
        self, tmp_path: Path
    ) -> None:
        """AC-7/CWE-209: .cube validation failure must not expose the full file path.

        validate_source_file() leaks 'File not found: <path>' verbatim.
        The implementation wraps/scrubs that message (ADR-CO-10 / §5.1).
        Error message/hint must not contain the sentinel dir segment.
        """
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"

        # Path that does NOT exist — triggers validate_source_file FILE_NOT_FOUND
        nonexistent_cube = tmp_path / "secret_dir_sentinel" / "grade.cube"
        opts = DetectColorOptions(  # type: ignore[call-arg]
            target_luma=128.0,
            lut=str(nonexistent_cube),
        )

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(
                "clipwright_color.color.inspect_media",
                lambda p: _make_media_info(str(p)),
            )
            mp.setattr(
                "clipwright_color.color.measure_brightness",
                lambda media_path, options: _fake_measured(yavg=128.0),
            )
            result = detect_color(
                media=str(media), output=str(output), options=opts, timeline=None
            )

        assert result["ok"] is False
        msg = result["error"].get("message", "")
        hint = result["error"].get("hint", "")
        # The sentinel directory name must NOT appear in the scrubbed error
        assert "secret_dir_sentinel" not in msg, (
            f"CWE-209: full .cube path leaked in message: {msg!r}"
        )
        assert "secret_dir_sentinel" not in hint, (
            f"CWE-209: full .cube path leaked in hint: {hint!r}"
        )


# ===========================================================================
# SR-INJ-002: detect_color lut injection-char rejection (function-body contract)
# ===========================================================================


class TestDetectColorOptionsLutInjection:
    """SR-INJ-002: detect_color must reject lut paths containing injection chars.

    The schema layer (DetectColorOptions) accepts any non-empty str for lut;
    injection char validation (single quote, control chars 0x00-0x1F, DEL 0x7F)
    is performed inside detect_color and returned as ok=False / INVALID_INPUT.
    CWE-209: the offending path value must not be echoed in error message or hint.

    Mirrors the injection guard on SubtitleOptions.path / image_path / font_path
    but implemented at the function-body level rather than the schema level.
    """

    # -------------------------------------------------------------------------
    # Schema-level contract: DetectColorOptions accepts injection chars as str
    # -------------------------------------------------------------------------

    def test_schema_accepts_single_quote_in_lut(self) -> None:
        """DetectColorOptions.lut with single-quote must be accepted by the schema.

        Injection validation is delegated to the detect_color function body.
        The schema only enforces str type, min_length=1, and max_length=4096.
        """
        opts = DetectColorOptions(lut="/luts/a'b.cube")
        assert opts.lut == "/luts/a'b.cube"

    # -------------------------------------------------------------------------
    # Function-body injection rejection: single-quote
    # -------------------------------------------------------------------------

    def test_single_quote_in_lut_path_rejected(self, tmp_path: Path) -> None:
        """lut path with single-quote causes detect_color to return ok=False / INVALID_INPUT."""
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(lut="/luts/a'b.cube")

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

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT
        msg = result["error"].get("message", "")
        hint = result["error"].get("hint", "")
        # CWE-209: offending path fragments must not appear in error text
        assert "a'b" not in msg
        assert "a'b" not in hint

    def test_single_quote_error_does_not_echo_path(self, tmp_path: Path) -> None:
        """CWE-209: INVALID_INPUT for single-quote lut must not expose the offending path value."""
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        _SENTINEL = "unique_sentinel_xzy987_lutpath"
        opts = DetectColorOptions(lut=f"/luts/{_SENTINEL}'.cube")

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

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT
        err_text = result["error"].get("message", "") + result["error"].get("hint", "")
        assert _SENTINEL not in err_text, (
            "CWE-209: offending path value must not appear in the error text."
            f" Got: {err_text!r}"
        )

    # -------------------------------------------------------------------------
    # Function-body injection rejection: control chars (0x00-0x1F)
    # -------------------------------------------------------------------------

    def test_null_byte_in_lut_path_rejected(self, tmp_path: Path) -> None:
        """lut path with null byte (\\x00) causes detect_color to return ok=False / INVALID_INPUT."""
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(lut="/luts/\x00grade.cube")

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

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_newline_in_lut_path_rejected(self, tmp_path: Path) -> None:
        """lut path with newline (\\n) causes detect_color to return ok=False / INVALID_INPUT."""
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(lut="/luts/\ngrade.cube")

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

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    # -------------------------------------------------------------------------
    # SR-L-1-new: DEL (0x7F) control-char parity with reader-side _CONTROL_CHARS
    # -------------------------------------------------------------------------

    def test_del_char_in_lut_path_rejected(self, tmp_path: Path) -> None:
        """lut path with DEL (\\x7f) causes detect_color to return ok=False / INVALID_INPUT.

        SR-L-1-new: the function body rejects c < '\\x20' (0x00-0x1F) AND 0x7F (DEL),
        matching the reader-side _CONTROL_CHARS in clipwright-render plan.py.
        """
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        opts = DetectColorOptions(lut="/luts/grade\x7ftest.cube")

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

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_del_char_error_does_not_echo_path(self, tmp_path: Path) -> None:
        """CWE-209: INVALID_INPUT for DEL-bearing lut must not expose the path value.

        SR-L-1-new / CWE-209: confirms path value is suppressed for the 0x7F (DEL) case.
        """
        from clipwright_color.color import (  # type: ignore[import-not-found]
            detect_color,
        )

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        _SENTINEL = "grade_del_sentinel_7f"
        opts = DetectColorOptions(lut=f"/luts/{_SENTINEL}\x7ftest.cube")

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

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT
        err_text = result["error"].get("message", "") + result["error"].get("hint", "")
        assert _SENTINEL not in err_text, (
            "CWE-209: DEL-path value must not appear in the error text."
            f" Got: {err_text!r}"
        )
        assert "/luts/" not in err_text, (
            "CWE-209: path fragment '/luts/' must not appear in error text."
            f" Got: {err_text!r}"
        )

    # -------------------------------------------------------------------------
    # Parity sanity: tilde (0x7E) is NOT in the rejection set
    # -------------------------------------------------------------------------

    def test_tilde_char_in_lut_path_not_rejected(self) -> None:
        """lut path containing '~' (chr(0x7E)) must NOT be rejected by the control-char rule.

        Parity sanity: only 0x00-0x1F and 0x7F are in the rejection set;
        0x7E '~' is valid printable ASCII and must be accepted at schema level.
        Pins the exact upper boundary of the rejection set.
        """
        opts = DetectColorOptions(lut="/luts/grade~test.cube")
        assert opts.lut == "/luts/grade~test.cube"
