"""test_pathpolicy_silence.py — Path-boundary constraint tests for detect_silence.

Verifies that the same-directory output constraint was removed (impl-detectcut) and
that media_ref_for_otio is applied correctly, while maintaining all other safety
invariants.

Test ID:
  DP-S-1  test_external_dir_output_allowed              silencedetect; external dir -> ok
  DP-S-2  test_clip_target_url_absolute_external_media  silencedetect; media outside -> abs
  DP-S-3  test_output_equals_media_still_rejected       regression guard
  DP-S-4  test_non_otio_extension_still_rejected        regression guard
  DP-S-5  test_missing_parent_dir_still_rejected        regression guard
  DP-S-6  test_vad_external_dir_output_allowed          VAD; external dir -> ok (no co-loc)
  DP-S-7  test_clip_target_url_relative_inside_media    silencedetect; media inside -> rel
"""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
from clipwright.errors import ErrorCode
from clipwright.otio_utils import load_timeline
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_silence.schemas import DetectSilenceOptions

# ===========================================================================
# Helpers
# ===========================================================================

FPS = 30.0


def _make_media_info(
    path: str,
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
) -> MediaInfo:
    """Construct a MediaInfo with one video + one audio stream."""
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


def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
    """Fake silencedetect run: no silence detected (all-keep; empty stderr)."""
    return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")


def _fake_vad_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
    """Fake VAD CLI run: no speech segments (all classified as silence)."""
    payload = json.dumps({"speech_segments": []})
    return CompletedProcess(args=cmd, returncode=0, stdout=payload, stderr="")


def _opts() -> DetectSilenceOptions:
    return DetectSilenceOptions(
        silence_threshold_db=-30.0,
        min_silence_duration=0.5,
        padding=0.0,
        min_keep_duration=0.0,
    )


# ===========================================================================
# DP-S-1 / DP-S-2: external dir output allowed + absolute media ref
# ===========================================================================


class TestExternalDirOutput:
    """Output placed in a directory different from media must be accepted."""

    def test_external_dir_output_allowed(self, tmp_path: Path) -> None:
        """DP-S-1: detect_silence succeeds when output dir differs from media dir."""
        from clipwright_silence.detect import detect_silence

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "work"
        out_dir.mkdir()
        media = str(media_dir / "video.mp4")
        Path(media).touch()
        output = str(out_dir / "out.otio")

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=_make_media_info(path=media),
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True

    def test_clip_target_url_absolute_external_media(self, tmp_path: Path) -> None:
        """DP-S-2: When media is outside otio_dir, clip target_url must be absolute.

        Verifies media_ref_for_otio rule: media outside otio_dir -> absolute path.
        """
        from clipwright_silence.detect import detect_silence

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "work"
        out_dir.mkdir()
        media = media_dir / "video.mp4"
        media.touch()
        output = str(out_dir / "out.otio")

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=_make_media_info(path=str(media)),
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run),
        ):
            result = detect_silence(str(media), output, _opts())

        assert result["ok"] is True

        tl = load_timeline(output)
        v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert clips, "No clips found in V1 after detect_silence succeeded"
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
# DP-S-6: VAD backend external dir output allowed (no co-location constraint)
# ===========================================================================


class TestVadExternalDirOutput:
    """VAD backend must accept output in a directory different from media."""

    def _vad_opts(self) -> DetectSilenceOptions:
        return DetectSilenceOptions(
            silence_threshold_db=-30.0,
            min_silence_duration=0.5,
            padding=0.0,
            min_keep_duration=0.0,
            backend="vad",
        )

    def test_vad_external_dir_output_allowed(self, tmp_path: Path) -> None:
        """DP-S-6: detect_silence with VAD backend succeeds when output dir != media dir.

        Verifies that impl-detectcut correctly removed the VAD co-location constraint.
        Without the fix, the code raised INVALID_INPUT before reaching inspect_media.
        """
        from clipwright_silence.detect import detect_silence

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "work"
        out_dir.mkdir()
        media = media_dir / "video.mp4"
        media.touch()
        output = str(out_dir / "out.otio")

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=_make_media_info(path=str(media)),
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_vad_run),
        ):
            result = detect_silence(str(media), output, self._vad_opts())

        assert result["ok"] is True, (
            f"Expected ok=True with VAD backend and external output dir; "
            f"got ok=False: {result.get('error')}"
        )


# ===========================================================================
# DP-S-7: media inside otio_dir -> relative POSIX target_url
# ===========================================================================


class TestInsideMediaRef:
    """When media is inside the OTIO output directory, target_url must be relative."""

    def test_clip_target_url_relative_inside_media(self, tmp_path: Path) -> None:
        """DP-S-7: When media is under otio_dir, clip target_url must be relative POSIX.

        Verifies media_ref_for_otio rule: media inside otio_dir -> relative POSIX path.
        This enables the OTIO file and its media to be moved together as a project unit.
        """
        from clipwright_silence.detect import detect_silence

        out_dir = tmp_path / "work"
        out_dir.mkdir()
        # Media is placed inside the same directory as the OTIO output.
        media = out_dir / "video.mp4"
        media.touch()
        output = str(out_dir / "out.otio")

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=_make_media_info(path=str(media)),
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run),
        ):
            result = detect_silence(str(media), output, _opts())

        assert result["ok"] is True

        tl = load_timeline(output)
        v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert clips, "No clips found in V1 after detect_silence succeeded"
        for clip in clips:
            ref = clip.media_reference
            assert isinstance(ref, otio.schema.ExternalReference)
            ref_path = Path(ref.target_url)
            assert not ref_path.is_absolute(), (
                "target_url must be relative when media is inside otio_dir; "
                f"got {ref.target_url!r}"
            )
            # Relative path must resolve back to the media file
            resolved = str((out_dir / ref_path).resolve())
            assert resolved == str(media.resolve()), (
                f"target_url {ref.target_url!r} resolved to {resolved!r}, "
                f"expected {media.resolve()!r}"
            )


# ===========================================================================
# DP-S-3 .. DP-S-5: regression guards (already pass; must not regress)
# ===========================================================================


class TestRegressionGuards:
    """Removing the same-dir constraint must not weaken other path safety invariants."""

    def test_output_equals_media_still_rejected(self, tmp_path: Path) -> None:
        """DP-S-3: output path identical to media path must remain INVALID_INPUT."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "same.otio")
        Path(media).touch()

        with patch(
            "clipwright_silence.detect.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = detect_silence(media, media, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_non_otio_extension_still_rejected(self, tmp_path: Path) -> None:
        """DP-S-4: output with non-.otio extension must remain INVALID_INPUT."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.mp4")

        result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_missing_parent_dir_still_rejected(self, tmp_path: Path) -> None:
        """DP-S-5: output whose parent directory does not exist must remain rejected."""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "nonexistent_dir" / "out.otio")

        result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] in (
            ErrorCode.INVALID_INPUT,
            ErrorCode.FILE_NOT_FOUND,
        )
