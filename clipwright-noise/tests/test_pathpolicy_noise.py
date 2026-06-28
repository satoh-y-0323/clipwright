"""test_pathpolicy_noise.py — Path-boundary constraint tests for detect_noise.

Verifies that detect_noise accepts output in any directory and that media_ref_for_otio
is applied correctly, while maintaining all other safety invariants including timeline
source validation.

Test ID:
  DP-N-1  test_external_dir_output_allowed              external dir -> ok
  DP-N-2  test_clip_target_url_absolute_external_media  media outside -> abs target_url
  DP-N-7  test_timeline_validation_maintained_across_dirs  timeline across dirs -> ok
  DP-N-3  test_output_equals_media_still_rejected       regression guard (accepts INVALID_INPUT or PATH_NOT_ALLOWED)
  DP-N-4  test_output_equals_timeline_still_rejected    regression guard (accepts INVALID_INPUT or PATH_NOT_ALLOWED)
  DP-N-5  test_non_otio_extension_still_rejected        regression guard
  DP-N-6  test_missing_parent_dir_still_rejected        regression guard
  DP-N-8  test_clip_target_url_relative_inside_media    media inside -> rel POSIX target_url
  DP-N-9  test_output_equals_source_path_not_allowed    SR-L-4: output==source -> PATH_NOT_ALLOWED
  DP-N-10 test_relative_traversal_in_timeline_rejected  SR-M-1/CR-M-1/CR-M-6: ../ -> PATH_NOT_ALLOWED
  DP-N-11 test_absolute_external_source_in_timeline_allowed  absolute existing source outside timeline dir -> ok
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ErrorCode
from clipwright.otio_utils import load_timeline
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo, ToolResult

from clipwright_noise.schemas import DetectNoiseOptions

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

FPS = 30.0

# ===========================================================================
# Helpers
# ===========================================================================


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
    """Removing the same-dir constraint must not weaken other path safety invariants.

    DP-N-3 / DP-N-4 accept both INVALID_INPUT and PATH_NOT_ALLOWED so they remain
    valid regression guards through the SR-L-4 migration (check_output_not_source
    changes the error code from INVALID_INPUT to PATH_NOT_ALLOWED).
    """

    def test_output_equals_media_still_rejected(self, tmp_path: Path) -> None:
        """DP-N-3: output path identical to media path must remain rejected.

        Accepts either INVALID_INPUT (current) or PATH_NOT_ALLOWED (after SR-L-4
        migration to check_output_not_source) so this guard survives the migration.
        """
        from clipwright_noise.noise import detect_noise

        media = tmp_path / "video.otio"
        media.write_bytes(b"dummy")

        result = detect_noise(
            str(media), str(media), DetectNoiseOptions(), timeline=None
        )

        d = _d(result)
        assert d["ok"] is False
        assert d["error"]["code"] in (
            ErrorCode.INVALID_INPUT,
            ErrorCode.PATH_NOT_ALLOWED,
        ), f"output==media must be rejected. Got: {d['error']['code']!r}"

    def test_output_equals_timeline_still_rejected(self, tmp_path: Path) -> None:
        """DP-N-4: output path identical to timeline path must remain rejected.

        Accepts either INVALID_INPUT (current) or PATH_NOT_ALLOWED (after SR-L-4
        migration) so this guard survives the migration.
        """
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
        assert d["error"]["code"] in (
            ErrorCode.INVALID_INPUT,
            ErrorCode.PATH_NOT_ALLOWED,
        ), f"output==timeline must be rejected. Got: {d['error']['code']!r}"

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


# ===========================================================================
# DP-N-9: SR-L-4 — output == source must return PATH_NOT_ALLOWED
# ===========================================================================


class TestOutputEqualsSourcePathNotAllowed:
    """SR-L-4: output==source rejection must use PATH_NOT_ALLOWED via check_output_not_source.

    noise.py uses check_output_not_source which raises PATH_NOT_ALLOWED (consistent
    with other tools).
    """

    def test_output_equals_source_path_not_allowed(self, tmp_path: Path) -> None:
        """DP-N-9: output identical to media must return PATH_NOT_ALLOWED.

        SR-L-4: check_output_not_source raises PATH_NOT_ALLOWED.
        """
        from clipwright_noise.noise import detect_noise

        # Use .otio extension so the extension check does not fire before the
        # output==source check.
        media = tmp_path / "video.otio"
        media.write_bytes(b"dummy")

        result = detect_noise(
            str(media), str(media), DetectNoiseOptions(), timeline=None
        )

        d = _d(result)
        assert d["ok"] is False
        assert d["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED, (
            "SR-L-4: output==media must return PATH_NOT_ALLOWED after migration to"
            f" check_output_not_source. Got: {d['error']['code']!r}"
        )


# ===========================================================================
# DP-N-10 / DP-N-11: SR-M-1 / CR-M-1 / CR-M-6
#   _load_and_validate_timeline must call check_media_ref for each target_url
# ===========================================================================


def _build_timeline_with_traversal_source(
    timeline_dir: Path,
    traversal_url: str = "../outside.mp4",
    *,
    fps: float = FPS,
) -> otio.schema.Timeline:
    """Build a V1-only OTIO timeline whose clip has a relative traversal target_url.

    Used to exercise the CWE-22 guard that check_media_ref must enforce.
    """
    tl = otio.schema.Timeline(name="test")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    tl.tracks.append(v1)
    src_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, fps),
        duration=otio.opentime.RationalTime(300.0, fps),
    )
    clip = otio.schema.Clip(
        name="outside.mp4",
        media_reference=otio.schema.ExternalReference(target_url=traversal_url),
        source_range=src_range,
    )
    v1.append(clip)
    return tl


def _build_timeline_with_absolute_source(
    media_abs: Path,
    *,
    fps: float = FPS,
) -> otio.schema.Timeline:
    """Build a V1-only OTIO timeline with an absolute source pointing to *media_abs*."""
    tl = otio.schema.Timeline(name="test")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    tl.tracks.append(v1)
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


class TestCheckMediaRefNewPolicy:
    """SR-M-1 / CR-M-1 / CR-M-6: _load_and_validate_timeline must validate OTIO target_url
    via check_media_ref (same pattern as color / loudness / stabilize).

    Absolute paths to existing regular files are accepted;
    relative path traversal (../) is rejected as PATH_NOT_ALLOWED (CWE-22 guard).
    """

    def test_absolute_external_source_in_timeline_allowed(self, tmp_path: Path) -> None:
        """DP-N-11: Timeline with absolute source outside timeline dir must be accepted.

        After SR-M-1 migration, check_media_ref must accept absolute paths to existing
        regular files regardless of which directory the timeline is stored in.
        This is a regression guard that must remain Green before and after migration.
        """
        from clipwright_noise.noise import detect_noise

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        tl_dir = tmp_path / "timeline"
        tl_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")

        # Timeline in tl_dir; clip source = absolute path to media in media_dir.
        tl = _build_timeline_with_absolute_source(media.resolve())
        timeline_path = tl_dir / "base.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        # Output in media_dir (same dir as media) so the output==source check
        # does not fire and we can isolate the check_media_ref behavior.
        output = media_dir / "out.otio"
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
                timeline=str(timeline_path),
            )

        d = _d(result)
        assert d["ok"] is True, (
            "SR-M-1/check_media_ref: absolute existing source outside timeline dir"
            f" must be accepted. error={d.get('error')}"
        )

    def test_relative_traversal_in_timeline_rejected(self, tmp_path: Path) -> None:
        """DP-N-10: Relative path traversal (../) in OTIO target_url must return PATH_NOT_ALLOWED.

        SR-M-1 / CR-M-1 / CR-M-6: _load_and_validate_timeline must call
        check_media_ref for each target_url.  check_media_ref rejects relative
        traversal as PATH_NOT_ALLOWED (CWE-22 guard).

        """
        from clipwright_noise.noise import detect_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        # Build a timeline whose clip has a relative traversal target_url.
        tl = _build_timeline_with_traversal_source(tmp_path, "../outside.mp4")
        timeline_path = tmp_path / "bad.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with patch("clipwright_noise.noise.inspect_media", return_value=media_info):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_path),
            )

        d = _d(result)
        assert d["ok"] is False
        assert d["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED, (
            "CWE-22 / SR-M-1: relative traversal in OTIO target_url must return"
            " PATH_NOT_ALLOWED after adding check_media_ref to"
            f" _load_and_validate_timeline. Got: {d['error']['code']!r}"
        )


# ===========================================================================
# AC-1 / AC-2: CWD-independent timeline source validation (spec5 D1 regression)
# ===========================================================================


def _make_otio_timeline_relative(
    relative_url: str,
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
) -> otio.schema.Timeline:
    """Build a V1-only OTIO timeline whose clip has a relative POSIX target_url.

    Used to exercise the CWD-independence fix (spec5 D1): the B-4 comparison
    must resolve the relative URL against the OTIO directory, not CWD.
    """
    tl = otio.schema.Timeline(name="test")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    tl.tracks.append(track)
    source_range = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, rate),
        duration=otio.opentime.RationalTime(duration_sec * rate, rate),
    )
    ref = otio.schema.ExternalReference(target_url=relative_url)
    clip = otio.schema.Clip(
        name=relative_url,
        media_reference=ref,
        source_range=source_range,
    )
    track.append(clip)
    return tl


class TestCwdRegressionNoise:
    """spec5 D1: timeline source match must be CWD-independent.

    After fix (check_timeline_source_matches), the relative URL is joined onto
    otio_dir before comparison, making CWD irrelevant.

    AC-1  relative source matches media, CWD != otio_dir -> ok=True
    AC-2  relative source is a different basename, CWD != otio_dir -> ok=False / INVALID_INPUT
    """

    def test_ac1_relative_source_match_cwd_independent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-1: relative target_url matching media must succeed regardless of CWD.

        Regression guard for spec5 D1 fix: check_timeline_source_matches resolves
        relative target_url against otio_dir, so the match succeeds even when
        CWD differs from otio_dir.
        """
        from clipwright_noise.noise import detect_noise

        otio_dir = tmp_path / "otio"
        otio_dir.mkdir()
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        # Media and timeline co-located in otio_dir
        media = otio_dir / "video.mp4"
        media.write_bytes(b"dummy")

        # Timeline with relative POSIX target_url = basename only (media_ref_for_otio style)
        tl = _make_otio_timeline_relative("video.mp4")
        timeline_file = otio_dir / "noise.otio"
        otio.adapters.write_to_file(tl, str(timeline_file))

        output = out_dir / "out.otio"
        media_info = _make_media_info(str(media))

        # Change CWD to a directory other than otio_dir to trigger D1 bug
        monkeypatch.chdir(tmp_path)

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
        assert d["ok"] is True, (
            "spec5 D1: relative target_url matching media must succeed when CWD != otio_dir."
            f" error={d.get('error')}"
        )

    def test_ac2_relative_source_mismatch_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-2: relative target_url with mismatched basename must return INVALID_INPUT.

        Even after the D1 fix, a timeline whose relative source points to a different
        media file must be rejected with INVALID_INPUT.  Verifies the equivalence check
        is preserved post-fix.  Error code only is asserted; canonical message text is
        validated after impl-noise applies check_timeline_source_matches.
        """
        from clipwright_noise.noise import detect_noise

        otio_dir = tmp_path / "otio"
        otio_dir.mkdir()
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        media = otio_dir / "video.mp4"
        media.write_bytes(b"dummy")

        # Timeline with relative target_url pointing to a DIFFERENT file
        tl = _make_otio_timeline_relative("other.mp4")
        timeline_file = otio_dir / "noise.otio"
        otio.adapters.write_to_file(tl, str(timeline_file))

        output = out_dir / "out.otio"
        media_info = _make_media_info(str(media))

        # Change CWD to a directory other than otio_dir
        monkeypatch.chdir(tmp_path)

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
        assert d["ok"] is False, "Mismatched relative source must be rejected."
        assert d["error"]["code"] == ErrorCode.INVALID_INPUT, (
            "spec5 D1: mismatched relative source must return INVALID_INPUT."
            f" Got: {d['error']['code']!r}"
        )
        # SR-R-001: CWE-209 regression guard — input media filename must not leak into error message.
        assert "video" not in d["error"]["message"]
