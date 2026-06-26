"""test_pathpolicy_sequence.py — Path-boundary policy tests for external-source placement.

Encodes the new path policy for clipwright-sequence where sources may reside
outside the output OTIO directory.

New policy under test:
- External sources (outside otio_dir) are ALLOWED if they exist as regular
  non-symlink files.
- target_url in OTIO follows media_ref_for_otio rules:
    - Source under otio_dir: relative POSIX path.
    - Source outside otio_dir: absolute path.
- output == any source (resolved equal) is rejected (PATH_NOT_ALLOWED).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
from clipwright.errors import ErrorCode
from clipwright.otio_utils import load_timeline
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_sequence.schemas import SequenceClip
from clipwright_sequence.sequence import build_sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FPS = 30.0
DURATION_SEC = 10.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_media_info(
    path: str = "/fake/video.mp4",
    *,
    duration_sec: float | None = DURATION_SEC,
    rate: float = FPS,
    has_video: bool = True,
) -> MediaInfo:
    """Synthetic MediaInfo for mocking inspect_media."""
    if has_video:
        streams: list[StreamInfo] = [
            StreamInfo(index=0, codec_type="video", codec_name="h264"),
            StreamInfo(index=1, codec_type="audio", codec_name="aac"),
        ]
    else:
        streams = [StreamInfo(index=0, codec_type="audio", codec_name="aac")]
    duration = (
        RationalTimeModel(value=duration_sec * rate, rate=rate)
        if duration_sec is not None
        else None
    )
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=duration,
        streams=streams,
        bit_rate=8_000_000,
    )


def _clip(
    media: str,
    start_sec: float | None = None,
    end_sec: float | None = None,
) -> SequenceClip:
    """Build a SequenceClip for tests."""
    return SequenceClip(media=media, start_sec=start_sec, end_sec=end_sec)


# ===========================================================================
# External sources: allowed under new policy
# ===========================================================================


class TestExternalSourcesAllowed:
    """Sources outside the output OTIO directory must be accepted."""

    def test_single_external_source_ok_true(self, tmp_path: Path) -> None:
        """Single source outside output.parent -> ok=True (new external-source policy)."""
        ext_dir = tmp_path / "external"
        ext_dir.mkdir()
        media = str(ext_dir / "video.mp4")
        Path(media).touch()
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        output = str(work_dir / "out.otio")
        clips = [_clip(media, 0.0, 5.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = build_sequence(clips=clips, output=output)
        assert result["ok"] is True

    def test_multiple_external_sources_ok_true(self, tmp_path: Path) -> None:
        """Two sources in separate external directories -> ok=True."""
        src1_dir = tmp_path / "src1"
        src1_dir.mkdir()
        src2_dir = tmp_path / "src2"
        src2_dir.mkdir()
        media_a = str(src1_dir / "a.mp4")
        media_b = str(src2_dir / "b.mp4")
        Path(media_a).touch()
        Path(media_b).touch()
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        output = str(work_dir / "out.otio")
        clips = [
            _clip(media_a, 0.0, 5.0),
            _clip(media_b, 0.0, 5.0),
            _clip(media_a, 5.0, 10.0),
        ]

        def _fake_inspect(path: str) -> MediaInfo:
            return _make_media_info(path=path)

        with patch(
            "clipwright_sequence.sequence.inspect_media",
            side_effect=_fake_inspect,
        ):
            result = build_sequence(clips=clips, output=output)
        assert result["ok"] is True

    def test_mixed_external_internal_sources_ok_true(self, tmp_path: Path) -> None:
        """External source and internal source in same sequence -> ok=True."""
        ext_dir = tmp_path / "external"
        ext_dir.mkdir()
        media_ext = str(ext_dir / "ext_video.mp4")
        Path(media_ext).touch()
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        media_int = str(work_dir / "int_video.mp4")
        Path(media_int).touch()
        output = str(work_dir / "out.otio")
        clips = [
            _clip(media_ext, 0.0, 5.0),
            _clip(media_int, 0.0, 5.0),
        ]

        def _fake_inspect(path: str) -> MediaInfo:
            return _make_media_info(path=path)

        with patch(
            "clipwright_sequence.sequence.inspect_media",
            side_effect=_fake_inspect,
        ):
            result = build_sequence(clips=clips, output=output)
        assert result["ok"] is True


# ===========================================================================
# media_ref_for_otio: target_url rules for OTIO references
# ===========================================================================


class TestMediaRefForOtio:
    """OTIO target_url must follow media_ref_for_otio rules.

    - External source (outside otio_dir): absolute path.
    - Internal source (under otio_dir): relative POSIX path.
    """

    def test_external_source_target_url_is_absolute(self, tmp_path: Path) -> None:
        """Source outside otio_dir -> target_url stored as absolute path in OTIO."""
        ext_dir = tmp_path / "external"
        ext_dir.mkdir()
        media = str(ext_dir / "video.mp4")
        Path(media).touch()
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        output = str(work_dir / "out.otio")
        clips = [_clip(media, 0.0, 5.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = build_sequence(clips=clips, output=output)
        assert result["ok"] is True
        tl = load_timeline(output)
        v1_clips = [it for it in tl.tracks[0] if isinstance(it, otio.schema.Clip)]
        # media_ref_for_otio returns POSIX-style absolute path for external sources
        expected_url = Path(media).resolve().as_posix()
        for clip in v1_clips:
            assert clip.media_reference.target_url == expected_url

    def test_internal_source_target_url_is_relative_posix(self, tmp_path: Path) -> None:
        """Source inside otio_dir -> target_url stored as relative POSIX path in OTIO."""
        # Source in a subdirectory of the output directory (internal)
        sub_dir = tmp_path / "footage"
        sub_dir.mkdir()
        media = str(sub_dir / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        clips = [_clip(media, 0.0, 5.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = build_sequence(clips=clips, output=output)
        assert result["ok"] is True
        tl = load_timeline(output)
        v1_clips = [it for it in tl.tracks[0] if isinstance(it, otio.schema.Clip)]
        # media_ref_for_otio: source under otio_dir -> relative POSIX path
        expected_url = "footage/video.mp4"
        for clip in v1_clips:
            assert clip.media_reference.target_url == expected_url


# ===========================================================================
# output == any source: rejected even when sources are external
# ===========================================================================


class TestOutputEqualsAnySource:
    """output == any source (resolved equal) -> PATH_NOT_ALLOWED.

    test_output_equals_external_source_path_not_allowed: regression (green under
    both old and new code, because co-location passes for this setup).

    test_output_equals_one_of_multiple_sources_path_not_allowed: verifies that
    when clips[0] is external and clips[1] equals output, the identity error
    message is returned (not the co-location boundary message).
    """

    def test_output_equals_external_source_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """output == source when both are in an external directory -> PATH_NOT_ALLOWED.

        Regression test: verifies output==source rejection applies regardless of
        which directory the source/output reside in.
        """
        ext_dir = tmp_path / "external"
        ext_dir.mkdir()
        # Source and output share the same path in ext_dir (output == source).
        media = str(ext_dir / "video.otio")
        Path(media).touch()
        output = media  # output == source
        clips = [_clip(media, 0.0, 5.0)]
        with patch(
            "clipwright_sequence.sequence.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = build_sequence(clips=clips, output=output)
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED

    def test_output_equals_one_of_multiple_sources_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """output == one of multiple sources; first source is external -> PATH_NOT_ALLOWED.

        clips[0] is an external valid source; clips[1] path equals output.
        After impl-sequence, clips[0] is accepted and the output==source check
        on clips[1] produces the identity error message.
        The message assertion distinguishes the identity error from any boundary error.
        """
        ext_dir = tmp_path / "external"
        ext_dir.mkdir()
        # clips[0]: valid external source (different from output)
        media_ext = str(ext_dir / "ext_video.mp4")
        Path(media_ext).touch()
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        # clips[1]: path equals output -> output==source violation
        media_same_as_output = str(work_dir / "out.otio")
        Path(media_same_as_output).touch()
        output = media_same_as_output
        clips = [
            _clip(media_ext, 0.0, 5.0),  # external, processed first
            _clip(media_same_as_output, 0.0, 5.0),  # equals output
        ]

        def _fake_inspect(path: str) -> MediaInfo:
            return _make_media_info(path=path)

        with patch(
            "clipwright_sequence.sequence.inspect_media",
            side_effect=_fake_inspect,
        ):
            result = build_sequence(clips=clips, output=output)
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED
        # New impl produces the output==source identity message for clips[1].
        msg = result["error"]["message"].lower()
        assert "same" in msg or "equal" in msg or "identical" in msg
