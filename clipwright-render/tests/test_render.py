"""test_render.py — Red tests for render.py (orchestration + _probe()).

Targets:
  - _probe(source) -> ProbeInfo
    inspect_media call and MediaInfo->ProbeInfo adapter conversion
  - render_timeline(timeline, source, output, options, dry_run) orchestration
    input validation, dry_run path, execution path, error propagation
  - BGM orchestration extension (§7 ADR-B4-r2/B5-r2/B6-r2/B8)
    resolve_bgm call, build_plan bgm forwarding, -stream_loop -i ordering

inspect_media is verified by patching clipwright_render.render.inspect_media.
process.run is patched exclusively for ffmpeg calls.
No real ffmpeg/ffprobe binaries are used (integration tests are in a separate file).
"""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.schemas import MediaInfo, StreamInfo

from clipwright_render.schemas import RenderOptions

# ---------------------------------------------------------------------------
# Helpers: OTIO Timeline file / in-memory construction for tests
# ---------------------------------------------------------------------------

FPS = 30.0


def _rt(seconds: float, rate: float = FPS) -> otio.opentime.RationalTime:
    return otio.opentime.RationalTime(seconds * rate, rate)


def _tr(start: float, duration: float, rate: float = FPS) -> otio.opentime.TimeRange:
    return otio.opentime.TimeRange(
        start_time=_rt(start, rate),
        duration=_rt(duration, rate),
    )


def _make_clip(source: str, start: float, duration: float) -> otio.schema.Clip:
    clip = otio.schema.Clip()
    clip.media_reference = otio.schema.ExternalReference(target_url=source)
    clip.source_range = _tr(start, duration)
    return clip


def _make_timeline(clips: list[otio.schema.Clip]) -> otio.schema.Timeline:
    """Build a single-video-track Timeline."""
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    for clip in clips:
        track.append(clip)
    tl = otio.schema.Timeline()
    tl.tracks.append(track)
    return tl


def _write_timeline(path: Path, clips: list[otio.schema.Clip]) -> None:
    """Write an OTIO timeline to disk."""
    tl = _make_timeline(clips)
    otio.adapters.write_to_file(tl, str(path))


def _make_media_info(
    path: str = "/fake/source.mp4",
    *,
    bit_rate: int | None = 8_000_000,
    has_video: bool = True,
    audio_streams: int = 1,
    extra_streams: list[StreamInfo] | None = None,
) -> MediaInfo:
    """Build a MediaInfo for tests.

    Used as the mock return value for inspect_media.
    bit_rate is passed as int | None (assumed already converted by _to_optional_int).
    """
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(StreamInfo(index=0, codec_type="video", codec_name="h264"))
    for _i in range(audio_streams):
        streams.append(
            StreamInfo(
                index=len(streams),
                codec_type="audio",
                codec_name="aac",
            )
        )
    if extra_streams:
        streams.extend(extra_streams)
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=None,
        streams=streams,
        bit_rate=bit_rate,
    )


# ---------------------------------------------------------------------------
# _probe() tests (DC-GP-001 / AS-001 / AM-007)
# (a) migrated to inspect_media mock-based style
# ---------------------------------------------------------------------------


class TestProbe:
    """Verify _probe(source) behaviour.

    Replaced direct ffprobe mock with patching clipwright_render.render.inspect_media
    to supply MediaInfo (DC-GP-001/AD-3).
    """

    def test_probe_video_audio_bit_rate(self, tmp_path: Path) -> None:
        """MediaInfo with video+audio+bit_rate -> ProbeInfo conversion.

        Results in has_video=True, audio_count=1, bit_rate=8000000 (DC-GP-001).
        """
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        media_info = _make_media_info(
            path=source, bit_rate=8_000_000, has_video=True, audio_streams=1
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=media_info,
        ) as mock_inspect:
            info = _probe(source)

        mock_inspect.assert_called_once_with(source)
        assert info.has_video is True
        assert info.audio_count == 1
        assert info.bit_rate == 8_000_000

    def test_probe_audio_count_zero(self, tmp_path: Path) -> None:
        """Zero audio streams -> audio_count=0 (DC-GP-001)."""
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        media_info = _make_media_info(path=source, has_video=True, audio_streams=0)

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=media_info,
        ):
            info = _probe(source)

        assert info.audio_count == 0

    def test_probe_audio_count_multiple(self, tmp_path: Path) -> None:
        """Multiple audio streams -> audio_count=N (DC-GP-001)."""
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        media_info = _make_media_info(path=source, has_video=True, audio_streams=3)

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=media_info,
        ):
            info = _probe(source)

        assert info.audio_count == 3

    def test_probe_bit_rate_none(self, tmp_path: Path) -> None:
        """MediaInfo.bit_rate is None -> ProbeInfo.bit_rate is None (DC-GP-001)."""
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        media_info = _make_media_info(path=source, bit_rate=None)

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=media_info,
        ):
            info = _probe(source)

        assert info.bit_rate is None

    def test_probe_propagates_probe_failed(self, tmp_path: Path) -> None:
        """inspect_media raises PROBE_FAILED -> _probe propagates it.

        Error codes other than FILE_NOT_FOUND must propagate unchanged (DC-GP-001).
        """
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=ClipwrightError(
                    code=ErrorCode.PROBE_FAILED,
                    message="ffprobe output is not valid JSON.",
                    hint="Check that the input file is a valid media file.",
                ),
            ),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            _probe(source)

        assert exc_info.value.code == ErrorCode.PROBE_FAILED

    def test_probe_has_video_false(self, tmp_path: Path) -> None:
        """No video stream -> has_video=False (DC-GP-001)."""
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        media_info = _make_media_info(path=source, has_video=False, audio_streams=1)

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=media_info,
        ):
            info = _probe(source)

        assert info.has_video is False

    def test_probe_audio_count_single(self, tmp_path: Path) -> None:
        """Single audio stream -> audio_count=1 (DC-GP-001)."""
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        media_info = _make_media_info(path=source, has_video=True, audio_streams=1)

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=media_info,
        ):
            info = _probe(source)

        assert info.audio_count == 1

    def test_probe_file_not_found_replaces_abspath_with_basename(self) -> None:
        """On FILE_NOT_FOUND, _probe replaces message with basename only.

        When inspect_media raises FILE_NOT_FOUND, the ClipwrightError re-raised
        by _probe must contain no absolute path — only the basename (Sec M-1).
        No real symlink created, so this runs on Windows too (CR-T-001).
        """
        from clipwright_render.render import _probe

        source = "/abs/path/to/link.mp4"
        expected_hint = "Specify a real file instead of a symbolic link."

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=ClipwrightError(
                    code=ErrorCode.FILE_NOT_FOUND,
                    message="Symbolic links are not accepted: /abs/path/to/link.mp4",
                    hint=expected_hint,
                ),
            ),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            _probe(source)

        assert exc_info.value.code == ErrorCode.FILE_NOT_FOUND
        # Absolute path (directory part) must not be exposed
        assert "/abs/path/to" not in exc_info.value.message
        # Basename must be present
        assert "link.mp4" in exc_info.value.message
        # hint from inspect_media must be preserved (CR-T-004)
        assert exc_info.value.hint == expected_hint


# ---------------------------------------------------------------------------
# (d) Edge cases: missing/empty codec_type (DC-AM-002)
# ---------------------------------------------------------------------------


class TestProbeEdgeCases:
    """Verify _probe equivalence for missing/empty codec_type (DC-AM-002)."""

    def test_probe_codec_type_missing_or_empty_not_counted(
        self, tmp_path: Path
    ) -> None:
        """MediaInfo with missing (normalised to "") or empty codec_type streams
        results in has_video=False / audio_count=0 (equivalent to old implementation).

        Old impl: s.get("codec_type") == "video" -> None on missing -> not counted.
        New impl: StreamInfo.codec_type normalised to "" via str(s.get("codec_type",""))
                  -> does not match "video"/"audio" -> not counted. Both are equivalent (DC-AM-002).

        Covers both empty-string (ffprobe missing normalised to "") and non-video/audio
        codec_type values like "data"/"subtitle".
        """
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        # MediaInfo with "" codec_type (missing normalised) and non-video/audio values
        extra_streams = [
            StreamInfo(
                index=0, codec_type="", codec_name=None
            ),  # missing normalised to ""
            StreamInfo(
                index=1, codec_type="", codec_name="data"
            ),  # missing normalised to "" (with codec_name)
            StreamInfo(
                index=2, codec_type="data", codec_name=None
            ),  # data stream (non video/audio)
            StreamInfo(
                index=3, codec_type="subtitle", codec_name=None
            ),  # subtitle (non video/audio)
        ]
        media_info = MediaInfo(
            path=source,
            container=None,
            duration=None,
            streams=extra_streams,
            bit_rate=None,
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=media_info,
        ):
            info = _probe(source)

        assert info.has_video is False
        assert info.audio_count == 0


# ---------------------------------------------------------------------------
# clipwright_render — input validation tests (DC-GP-005 / AM-002 / AM-003)
# ---------------------------------------------------------------------------


class TestInputValidation:
    """Verify input validation for clipwright_render."""

    def test_timeline_not_found_raises_file_not_found(self, tmp_path: Path) -> None:
        """Missing timeline (.otio) -> FILE_NOT_FOUND (DC-GP-005)."""
        from clipwright_render.render import render_timeline

        missing_tl = str(tmp_path / "nonexistent.otio")
        output = str(tmp_path / "out.mp4")
        result = render_timeline(
            timeline=missing_tl, output=output, options=RenderOptions()
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND

    def test_source_not_found_raises_file_not_found(self, tmp_path: Path) -> None:
        """Missing source file -> FILE_NOT_FOUND (DC-GP-005)."""
        from clipwright_render.render import render_timeline

        tl_path = tmp_path / "tl.otio"
        missing_source = str(tmp_path / "missing.mp4")
        _write_timeline(tl_path, [_make_clip(missing_source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        result = render_timeline(
            timeline=str(tl_path), output=output, options=RenderOptions()
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND

    def test_output_parent_dir_not_found_raises_file_not_found(
        self, tmp_path: Path
    ) -> None:
        """Missing output parent directory -> FILE_NOT_FOUND (no auto-creation, DC-GP-005)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        # Non-existent subdirectory
        output = str(tmp_path / "nonexistent_dir" / "out.mp4")

        result = render_timeline(
            timeline=str(tl_path), output=output, options=RenderOptions()
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND

    @pytest.mark.parametrize("ext", [".avi", ".wmv", ".ts", ".txt", ""])
    def test_invalid_extension_raises_invalid_input(
        self, tmp_path: Path, ext: str
    ) -> None:
        """Invalid extension (not on whitelist) -> INVALID_INPUT (DC-AM-003)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / f"out{ext}")

        result = render_timeline(
            timeline=str(tl_path), output=output, options=RenderOptions()
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    @pytest.mark.parametrize("ext", [".mp4", ".mkv", ".mov", ".webm"])
    def test_valid_extensions_pass_validation(self, tmp_path: Path, ext: str) -> None:
        """Whitelisted extensions pass input validation (verified via dry_run, DC-AM-003)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / f"out{ext}")

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=source),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )
        # Must not be INVALID_INPUT (ok=True or a different error)
        if not result["ok"]:
            assert result["error"]["code"] != ErrorCode.INVALID_INPUT

    def test_existing_output_without_overwrite_raises_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """Existing output with overwrite=False -> INVALID_INPUT (DC-AM-002)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")
        Path(output).touch()  # Create existing file

        result = render_timeline(
            timeline=str(tl_path),
            output=output,
            options=RenderOptions(overwrite=False),
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT
        # hint must mention overwrite
        assert "overwrite" in result["error"]["hint"].lower()

    def test_existing_output_with_overwrite_true_passes(self, tmp_path: Path) -> None:
        """With overwrite=True, an existing output file passes validation (DC-AM-002)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=source),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
                dry_run=True,
            )
        # Must not be INVALID_INPUT
        if not result["ok"]:
            assert result["error"]["code"] != ErrorCode.INVALID_INPUT

    def test_output_equals_source_raises_path_not_allowed(self, tmp_path: Path) -> None:
        """output == source -> PATH_NOT_ALLOWED (DC-AM-002)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])

        result = render_timeline(
            timeline=str(tl_path),
            output=source,  # output == source
            options=RenderOptions(),
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED

    def test_source_outside_timeline_dir_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """ADR-PP-1: Absolute external source (existing real file) outside timeline dir
        must be allowed (not raise PATH_NOT_ALLOWED).

        Under the old policy (Sec M-2), any source outside the timeline directory was
        rejected with PATH_NOT_ALLOWED.  Under ADR-PP-1, render.py delegates to
        pathpolicy.check_media_ref which allows absolute references to existing real
        files regardless of their location.

        Red: current render.py still calls _check_source_within_timeline_dir, which
        raises PATH_NOT_ALLOWED → ok is False → this test FAILS until impl is done.
        """
        from clipwright_render.render import render_timeline

        # timeline placed in subdir1
        subdir1 = tmp_path / "project"
        subdir1.mkdir()
        tl_path = subdir1 / "tl.otio"

        # source in a different directory (outside boundary, but real file, no symlink)
        subdir2 = tmp_path / "outside"
        subdir2.mkdir()
        outside_source = str(subdir2 / "secret.mp4")
        Path(outside_source).touch()

        _write_timeline(tl_path, [_make_clip(outside_source, 0.0, 5.0)])
        output = str(subdir1 / "out.mp4")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=outside_source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )
        # ADR-PP-1: absolute external real file must be allowed.
        # Red until render.py delegates to pathpolicy.check_media_ref.
        assert result["ok"] is True

    def test_symlink_source_raises_file_not_found(self, tmp_path: Path) -> None:
        """Symlink source passed to render_timeline returns FILE_NOT_FOUND (DC-AS-001).

        Regression test verifying that _probe -> inspect_media rejects symlinks with FILE_NOT_FOUND
        when reached via render_timeline.
        Path.exists() returns True if the symlink target exists, so it passes the existence check;
        the rejection fires inside inspect_media within _probe.
        error.message must not expose the absolute path (directory etc.) — only the basename (Sec M-1).
        """
        from clipwright_render.render import render_timeline

        # Create a real file and a symlink pointing to it
        real_file = tmp_path / "real.mp4"
        real_file.touch()
        symlink_source = tmp_path / "link.mp4"
        # Symlink creation requires elevated privileges on Windows; guard with skip (same policy as core)
        try:
            symlink_source.symlink_to(real_file)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(
                f"Symlink creation failed (insufficient privileges or unsupported environment): {exc}"
            )

        # timeline references the symlink as its source
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(str(symlink_source), 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        # Pass through real inspect_media (symlink rejection is handled by _validate_existing_file)
        # Not patching confirms the symlink rejection logic fires in the actual implementation
        result = render_timeline(
            timeline=str(tl_path),
            output=output,
            options=RenderOptions(),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND
        # error.message must not expose absolute path (parent dir of real_file etc.)
        # — only basename (Sec M-1)
        error_message: str = result["error"]["message"]
        assert str(tmp_path) not in error_message
        assert str(real_file.parent) not in error_message
        assert "link.mp4" in error_message


# ---------------------------------------------------------------------------
# clipwright_render — dry_run tests (§3 data flow 6a)
# (b) probe mock: migrated to patching clipwright_render.render.inspect_media
# ---------------------------------------------------------------------------


class TestDryRun:
    """Verify dry_run=True behaviour."""

    def test_dry_run_does_not_call_ffmpeg(self, tmp_path: Path) -> None:
        """dry_run=True -> ffmpeg is not called (inspect_media is still called)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        run_calls: list[list[str]] = []

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            run_calls.append(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True
        # run is patched exclusively for ffmpeg -> must not be called when dry_run=True
        ffmpeg_calls = [c for c in run_calls if "ffmpeg" in c[0]]
        assert len(ffmpeg_calls) == 0

    def test_dry_run_returns_ok_envelope(self, tmp_path: Path) -> None:
        """dry_run=True returns an ok=True envelope."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=source, bit_rate=8_000_000),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True
        assert "summary" in result
        assert "data" in result
        assert "artifacts" in result
        assert "warnings" in result

    def test_dry_run_summary_contains_segment_count_and_duration(
        self, tmp_path: Path
    ) -> None:
        """dry_run summary contains the segment count and expected duration (§3 data flow 6a)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(
            tl_path,
            [
                _make_clip(source, 0.0, 3.0),
                _make_clip(source, 5.0, 2.0),
            ],
        )
        output = str(tmp_path / "out.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=source, bit_rate=8_000_000),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True
        summary: str = result["summary"]
        # 2 segments info must be present
        assert "2" in summary

    def test_dry_run_data_contains_planned_command(self, tmp_path: Path) -> None:
        """dry_run data contains the planned command (§3 data flow 6a)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True
        # data must contain the planned command (ffmpeg_args or equivalent key)
        assert len(result["data"]) > 0

    def test_dry_run_summary_contains_estimated_size(self, tmp_path: Path) -> None:
        """dry_run summary with bit_rate contains estimated size information (ADR-3)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 10.0)])
        output = str(tmp_path / "out.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=source, bit_rate=8_000_000),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True
        # summary or data must contain some size/bytes-related information
        assert result["data"] or result["summary"]


# ---------------------------------------------------------------------------
# clipwright_render — execution path tests (dry_run=False, §3 data flow 6b)
# (b) probe mock: migrated to inspect_media -> render.run(ffmpeg) ordering
# ---------------------------------------------------------------------------


class TestExecutionPath:
    """Verify the dry_run=False execution path."""

    def test_inspect_media_called_before_ffmpeg(self, tmp_path: Path) -> None:
        """inspect_media is called before ffmpeg (§3 data flow).

        Old: ordering of ffprobe -> ffmpeg run calls.
        New: inspect_media (ffprobe internally) -> render.run(ffmpeg) ordering.
        """
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")
        Path(output).touch()  # Treat as an existing file after successful render

        call_order: list[str] = []

        def _inspecting(*args: Any, **kwargs: Any) -> MediaInfo:
            call_order.append("inspect_media")
            return _make_media_info(path=source)

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            call_order.append("ffmpeg")
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=_inspecting,
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert call_order[0] == "inspect_media"
        assert "ffmpeg" in call_order

    def test_ffmpeg_called_with_array_args(self, tmp_path: Path) -> None:
        """ffmpeg is called with an argument array (prevents command injection, ADR-4)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        ffmpeg_cmd: list[str] = []

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            ffmpeg_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert isinstance(ffmpeg_cmd, list)
        assert len(ffmpeg_cmd) > 0
        # filter_complex must be passed as a single argument (not string-concatenated)
        assert "-filter_complex" in ffmpeg_cmd
        fc_idx = ffmpeg_cmd.index("-filter_complex")
        assert isinstance(ffmpeg_cmd[fc_idx + 1], str)

    def test_ffmpeg_cmd_starts_with_resolved_path(self, tmp_path: Path) -> None:
        """ffmpeg command starts with the path returned by resolve_tool (ADR-4)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        ffmpeg_first_arg: list[str] = []

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            ffmpeg_first_arg.append(cmd[0])
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/custom/path/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert len(ffmpeg_first_arg) > 0
        assert ffmpeg_first_arg[0] == "/custom/path/ffmpeg"

    def test_ffmpeg_timeout_is_max_300_or_duration_times_10(
        self, tmp_path: Path
    ) -> None:
        """ffmpeg timeout = max(300, ceil(total_seconds * 10)) (DC-AM-006)."""
        from clipwright_render.render import render_timeline

        # total_duration = 5s -> 5*10=50 < 300 -> timeout=300
        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        ffmpeg_timeout: list[float] = []

        def _fake_ffmpeg_run(
            cmd: list[str], *, timeout: float = 60.0, **kwargs: Any
        ) -> CompletedProcess[str]:
            ffmpeg_timeout.append(timeout)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert len(ffmpeg_timeout) > 0
        assert ffmpeg_timeout[0] == 300  # max(300, ceil(5*10)) = 300

    def test_ffmpeg_timeout_long_video(self, tmp_path: Path) -> None:
        """Total duration 60s -> timeout = max(300, ceil(600)) = 600 (DC-AM-006)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 60.0)])
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        ffmpeg_timeout: list[float] = []

        def _fake_ffmpeg_run(
            cmd: list[str], *, timeout: float = 60.0, **kwargs: Any
        ) -> CompletedProcess[str]:
            ffmpeg_timeout.append(timeout)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert len(ffmpeg_timeout) > 0
        assert ffmpeg_timeout[0] == 600  # max(300, ceil(60*10)) = 600

    def test_success_returns_ok_envelope_with_artifact(self, tmp_path: Path) -> None:
        """On success, returns an ok=True envelope with the output path as an Artifact (§3)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source, bit_rate=8_000_000),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert result["ok"] is True
        assert "summary" in result
        assert "artifacts" in result

    def test_success_summary_contains_duration_and_clip_count(
        self, tmp_path: Path
    ) -> None:
        """Success summary contains total duration and concatenated clip count (§3 data flow 6b)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(
            tl_path,
            [
                _make_clip(source, 0.0, 3.0),
                _make_clip(source, 5.0, 2.0),
            ],
        )
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source, bit_rate=8_000_000),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert result["ok"] is True
        summary: str = result["summary"]
        assert "2" in summary  # 2 segments


# ---------------------------------------------------------------------------
# clipwright_render — error propagation tests (DC-GP-004)
# (b) probe mock: migrated to patching inspect_media
# ---------------------------------------------------------------------------


class TestErrorPropagation:
    """Error propagation: ClipwrightError is converted to an error_result envelope."""

    def test_ffmpeg_failed_returns_subprocess_failed(self, tmp_path: Path) -> None:
        """ffmpeg failure -> SUBPROCESS_FAILED envelope (DC-GP-004)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="Command exited with code 1.",
                hint="Check the command arguments.",
            )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
        ):
            result = render_timeline(
                timeline=str(tl_path), output=output, options=RenderOptions()
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.SUBPROCESS_FAILED

    def test_ffmpeg_timeout_returns_subprocess_timeout(self, tmp_path: Path) -> None:
        """ffmpeg timeout -> SUBPROCESS_TIMEOUT envelope (DC-GP-004)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_TIMEOUT,
                message="Timed out.",
                hint="Increase the timeout value.",
            )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
        ):
            result = render_timeline(
                timeline=str(tl_path), output=output, options=RenderOptions()
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.SUBPROCESS_TIMEOUT

    def test_ffmpeg_not_found_returns_dependency_missing(self, tmp_path: Path) -> None:
        """ffmpeg not found -> DEPENDENCY_MISSING envelope (DC-GP-004)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        def _fake_resolve(name: str, env: str | None = None) -> str:
            raise ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="ffmpeg not found.",
                hint="Add ffmpeg to your PATH.",
            )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch("clipwright_render.render.resolve_tool", side_effect=_fake_resolve),
        ):
            result = render_timeline(
                timeline=str(tl_path), output=output, options=RenderOptions()
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.DEPENDENCY_MISSING

    def test_probe_failure_returns_probe_failed(self, tmp_path: Path) -> None:
        """probe failure (inspect_media raises) -> PROBE_FAILED envelope (DC-GP-004).

        Verifies ClipwrightError is converted to error_result (GP-001).
        """
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.PROBE_FAILED,
                message="ffprobe output is not valid JSON.",
                hint="Check that the input file is a valid media file.",
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path), output=output, options=RenderOptions()
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PROBE_FAILED

    def test_error_does_not_expose_raw_stderr(self, tmp_path: Path) -> None:
        """Error message must not expose raw ffmpeg stderr (DC-GP-004)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        raw_stderr = "SUPER SECRET INTERNAL PATH /home/user/private/data"

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="Command exited with code 1: partial error",
                hint="Check the command.",
            )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
        ):
            result = render_timeline(
                timeline=str(tl_path), output=output, options=RenderOptions()
            )

        assert result["ok"] is False
        # Raw stderr and internal paths must not be exposed
        error_str = json.dumps(result["error"])
        assert raw_stderr not in error_str

    def test_error_does_not_expose_internal_exception(self, tmp_path: Path) -> None:
        """Error envelope must not contain raw exceptions or stack traces (DC-GP-004)."""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.PROBE_FAILED,
                message="ffprobe output is not valid JSON.",
                hint="Check that the input file is a valid media file.",
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path), output=output, options=RenderOptions()
            )

        assert result["ok"] is False
        # Traceback and Exception class name must not be present
        error_str = json.dumps(result["error"])
        assert "Traceback" not in error_str
        assert "JSONDecodeError" not in error_str


# ---------------------------------------------------------------------------
# Non-destructive tests
# ---------------------------------------------------------------------------


class TestNonDestructive:
    """Verify that the input timeline and source media are not modified."""

    def test_source_file_unchanged_after_render(self, tmp_path: Path) -> None:
        """Source file contents are unchanged after rendering (non-destructive)."""
        from clipwright_render.render import render_timeline

        source = tmp_path / "a.mp4"
        source.write_bytes(b"dummy source content")
        original_bytes = source.read_bytes()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(str(source), 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")
        (tmp_path / "out.mp4").touch()

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=str(source)),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert source.read_bytes() == original_bytes

    def test_timeline_file_unchanged_after_render(self, tmp_path: Path) -> None:
        """timeline (.otio) contents are unchanged after rendering (non-destructive)."""
        from clipwright_render.render import render_timeline

        source = tmp_path / "a.mp4"
        source.touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(str(source), 0.0, 5.0)])
        original_tl_bytes = tl_path.read_bytes()
        output = str(tmp_path / "out.mp4")
        (tmp_path / "out.mp4").touch()

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=str(source)),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert tl_path.read_bytes() == original_tl_bytes


# ---------------------------------------------------------------------------
# Multi-source orchestration extension tests
# (ADR-C2-r2 / ADR-C8 / ADR-C9-r2 / DC-GP-001)
# ---------------------------------------------------------------------------


def _make_media_info_with_video_stream(
    path: str,
    *,
    width: int = 1920,
    height: int = 1080,
    bit_rate: int | None = 8_000_000,
    audio_streams: int = 1,
    fps_rate: float | None = 30.0,
) -> MediaInfo:
    """Build a MediaInfo with a video stream (width/height) and duration.

    fps_rate=None -> duration=None (used to verify sentinel avoidance for audio-only sources).
    fps_rate specified -> generates RationalTimeModel with duration.rate = fps_rate.
    duration.rate=1000.0 is used as the sentinel for audio-only sources.
    """
    from clipwright.schemas import RationalTimeModel

    streams: list[StreamInfo] = []
    streams.append(
        StreamInfo(
            index=0,
            codec_type="video",
            codec_name="h264",
            width=width,
            height=height,
        )
    )
    for _i in range(audio_streams):
        streams.append(
            StreamInfo(index=len(streams), codec_type="audio", codec_name="aac")
        )

    duration = None
    if fps_rate is not None:
        # duration.rate = fps_rate, used to test ProbeInfo.fps retrieval
        duration = RationalTimeModel(value=10.0 * fps_rate, rate=fps_rate)

    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=duration,
        streams=streams,
        bit_rate=bit_rate,
    )


def _make_audio_only_media_info(path: str) -> MediaInfo:
    """Build a MediaInfo for an audio-only source (rate=1000.0 sentinel).

    media.py rate determination rule: no video stream -> rate=1000.0 sentinel.
    Even when duration.rate is 1000.0, it must not be adopted as fps (ADR-C2-r2).
    """
    from clipwright.schemas import RationalTimeModel

    streams = [StreamInfo(index=0, codec_type="audio", codec_name="aac")]
    # Generate duration with sentinel rate=1000.0 (mimics actual behaviour of audio-only media)
    duration = RationalTimeModel(value=10000.0, rate=1000.0)
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=duration,
        streams=streams,
        bit_rate=4_000_000,
    )


def _make_multi_source_otio_file(
    clips: list[tuple[str, float, float]],
    tmp_path: Path,
) -> Path:
    """Build and write an OTIO file for a multi-source Timeline; returns the Path.

    clips: [(source_path, start_sec, duration_sec), ...]
    Named _make_multi_source_otio_file to distinguish from the same-named helper in
    test_e2e_merge.py (which returns an in-memory OTIO) (CR L-1).
    """
    otio_clips = [_make_clip(src, start, dur) for src, start, dur in clips]
    tl_path = tmp_path / "tl.otio"
    _write_timeline(tl_path, otio_clips)
    return tl_path


class TestMultiSourceProbeAllSources:
    """Aspect 1: inspect_media is called for every unique source in a multi-source timeline.

    ADR-C8: probe is applied to all unique sources.
    render.py builds source_probes before calling build_plan, so inspect_media is
    called exactly as many times as there are unique sources.
    """

    def test_all_unique_sources_are_probed(self, tmp_path: Path) -> None:
        """2-source timeline -> inspect_media called twice (once per source)."""
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        src1 = str(tmp_path / "src1.mp4")
        Path(src0).touch()
        Path(src1).touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src1, 0.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        probe_calls: list[str] = []

        def _fake_inspect(path: str) -> MediaInfo:
            probe_calls.append(path)
            return _make_media_info_with_video_stream(path)

        with (
            patch("clipwright_render.render.inspect_media", side_effect=_fake_inspect),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render.run",
                return_value=CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                ),
            ),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        # Each unique source probed exactly once (order irrelevant, deduplication)
        assert src0 in probe_calls
        assert src1 in probe_calls
        # 2 unique sources -> 2 calls
        assert len(probe_calls) == 2

    def test_duplicate_source_is_probed_once(self, tmp_path: Path) -> None:
        """Same source used in 2 clips -> inspect_media is called only once.

        Deduplication minimises probe cost (ADR-C1).
        """
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        Path(src0).touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src0, 5.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        probe_calls: list[str] = []

        def _fake_inspect(path: str) -> MediaInfo:
            probe_calls.append(path)
            return _make_media_info_with_video_stream(path)

        with (
            patch("clipwright_render.render.inspect_media", side_effect=_fake_inspect),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render.run",
                return_value=CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                ),
            ),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        # 1 unique source -> probe called once
        assert len(probe_calls) == 1
        assert probe_calls[0] == src0


class TestMultiSourceFfmpegInputOrder:
    """Aspect 2: ffmpeg -i ordering matches RenderPlan.input_sources order.

    ADR-C9-r2: render.py uses RenderPlan.input_sources as-is; it does not
    recompute the order independently.
    """

    def test_two_source_ffmpeg_has_two_i_flags(self, tmp_path: Path) -> None:
        """2-source timeline -> ffmpeg command contains 2 -i flags."""
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        src1 = str(tmp_path / "src1.mp4")
        Path(src0).touch()
        Path(src1).touch()
        (tmp_path / "out.mp4").touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src1, 0.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        captured_cmd: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=lambda path: _make_media_info_with_video_stream(path),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert result["ok"] is True, f"failed: {result.get('error')}"
        # 2 -i flags must be present
        i_indices = [i for i, v in enumerate(captured_cmd) if v == "-i"]
        assert len(i_indices) == 2
        # Paths after -i must follow src0->src1 order (RenderPlan.input_sources order)
        assert captured_cmd[i_indices[0] + 1] == src0
        assert captured_cmd[i_indices[1] + 1] == src1

    def test_single_source_ffmpeg_has_one_i_flag(self, tmp_path: Path) -> None:
        """Single-source timeline has exactly 1 -i flag (backward compatibility).

        Combines with aspect 7 (backward compat): single source still has 1 -i after
        multi-source extension.
        """
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        Path(src0).touch()
        (tmp_path / "out.mp4").touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src0, 5.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        captured_cmd: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info_with_video_stream(src0),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert result["ok"] is True, f"failed: {result.get('error')}"
        i_indices = [i for i, v in enumerate(captured_cmd) if v == "-i"]
        assert len(i_indices) == 1
        assert captured_cmd[i_indices[0] + 1] == src0

    def test_input_order_matches_render_plan_input_sources(
        self, tmp_path: Path
    ) -> None:
        """ffmpeg -i ordering strictly matches RenderPlan.input_sources.

        ADR-C9-r2: render.py uses RenderPlan.input_sources without recomputing order.
        build_plan is mocked to explicitly control input_sources, and the -i ordering
        in the ffmpeg command must match it exactly.
        """
        from clipwright_render.plan import RenderPlan
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        src1 = str(tmp_path / "src1.mp4")
        Path(src0).touch()
        Path(src1).touch()
        (tmp_path / "out.mp4").touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src1, 0.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        # RenderPlan returned by build_plan includes input_sources
        fake_plan = RenderPlan(
            filter_complex="[0:v]trim=0:3,setpts=PTS-STARTPTS[v0];[1:v]trim=0:2,setpts=PTS-STARTPTS[v1];[v0][v1]concat=n=2:v=1:a=0[outv]",
            ffmpeg_args=["-filter_complex", "...", "-map", "[outv]", "-c:v", "libx264"],
            segment_count=2,
            total_duration_seconds=5.0,
            input_sources=[src0, src1],  # ADR-C9-r2: explicit ordering
        )

        captured_cmd: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=lambda path: _make_media_info_with_video_stream(path),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.build_plan", return_value=fake_plan),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert result["ok"] is True, f"failed: {result.get('error')}"
        i_indices = [i for i, v in enumerate(captured_cmd) if v == "-i"]
        assert len(i_indices) == 2
        # -i must follow input_sources=[src0, src1] order
        assert captured_cmd[i_indices[0] + 1] == src0
        assert captured_cmd[i_indices[1] + 1] == src1


class TestMultiSourceBoundaryCheck:
    """Aspects 3/4/5: ADR-C8 boundary validation applied to all unique sources.

    Detects boundary violations, path collisions, and missing files for
    the second and subsequent sources.
    """

    def test_second_source_outside_timeline_dir_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """ADR-PP-1: Second source absolute + outside timeline dir (existing real file)
        must be allowed (not raise PATH_NOT_ALLOWED).

        Under old policy (ADR-C8 / Sec M-2), any source outside the timeline directory
        was rejected.  Under ADR-PP-1, both sources pass as long as they are absolute
        references to existing real files without symlinks.

        Red: current render.py raises PATH_NOT_ALLOWED for the second source via
        _check_source_within_timeline_dir → this test FAILS until impl is done.
        """
        from clipwright_render.render import render_timeline

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        src0 = str(project_dir / "src0.mp4")
        src1 = str(outside_dir / "clip1.mp4")  # outside boundary, existing real file
        Path(src0).touch()
        Path(src1).touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src1, 0.0, 2.0)],
            project_dir,  # timeline is placed under project_dir
        )
        output = str(project_dir / "out.mp4")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=lambda path: _make_media_info_with_video_stream(path),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        # ADR-PP-1: absolute external real file must be allowed for all sources.
        # Red until render.py delegates to pathpolicy.check_media_ref.
        assert result["ok"] is True

    def test_second_source_equals_output_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """output == second source -> PATH_NOT_ALLOWED (DC-GP-001, non-destructive principle).

        _check_path_not_allowed must apply to all sources, not only the first.
        Detects when the second source (different from the first) matches the output.
        """
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        src1 = str(tmp_path / "src1.mp4")
        Path(src0).touch()
        Path(src1).touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src1, 0.0, 2.0)],
            tmp_path,
        )
        # output == src1 (second source)
        output = src1

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=lambda path: _make_media_info_with_video_stream(path),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED

    def test_second_source_not_found_returns_file_not_found(
        self, tmp_path: Path
    ) -> None:
        """Second source does not exist -> FILE_NOT_FOUND (ADR-C8)."""
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        src1 = str(tmp_path / "missing_src1.mp4")  # does not exist
        Path(src0).touch()
        # src1 is intentionally not created

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src1, 0.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=lambda path: _make_media_info_with_video_stream(path),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND

    def test_second_source_not_found_basename_only_in_message(
        self, tmp_path: Path
    ) -> None:
        """Missing second source error message must not expose absolute path (CWE-209).

        Only the basename must be present; the directory part must not appear.
        """
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        src1 = str(tmp_path / "missing_src1.mp4")
        Path(src0).touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src1, 0.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=lambda path: _make_media_info_with_video_stream(path),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND
        error_message: str = result["error"]["message"]
        # Absolute path (directory part) must not be exposed
        assert str(tmp_path) not in error_message
        # Basename must be present
        assert "missing_src1.mp4" in error_message


class TestProbeAudioOnlyFpsNone:
    """Aspect 6: _probe returns fps=None for audio-only sources (ADR-C2-r2).

    The rate=1000.0 sentinel must not be mistakenly adopted as fps.
    ProbeInfo.fps is set only when first video StreamInfo exists AND duration is not None.
    """

    def test_audio_only_source_probe_fps_is_none(self, tmp_path: Path) -> None:
        """Audio-only source (no video stream) -> ProbeInfo.fps = None (ADR-C2-r2).

        Even with duration.rate=1000.0 (sentinel), it must not be adopted as fps.
        """
        from clipwright_render.render import _probe

        source = str(tmp_path / "audio_only.mp4")
        Path(source).touch()

        # Audio-only: rate=1000.0 sentinel, no video stream
        audio_only_info = _make_audio_only_media_info(source)

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=audio_only_info,
        ):
            info = _probe(source)

        # fps is None because there is no video stream
        assert info.fps is None  # type: ignore[attr-defined]
        # width/height also None (no video stream)
        assert info.width is None  # type: ignore[attr-defined]
        assert info.height is None  # type: ignore[attr-defined]

    def test_video_source_with_duration_none_fps_is_none(self, tmp_path: Path) -> None:
        """Video stream present but duration=None -> ProbeInfo.fps = None (ADR-C2-r2).

        Accessing duration.rate when duration is None must not raise AttributeError.
        """
        from clipwright_render.render import _probe

        source = str(tmp_path / "no_duration.mp4")
        Path(source).touch()

        # video stream present, duration=None (format.duration unavailable)
        info_no_duration = MediaInfo(
            path=source,
            container="mov,mp4,m4a,3gp,3g2,mj2",
            duration=None,
            streams=[
                StreamInfo(
                    index=0,
                    codec_type="video",
                    codec_name="h264",
                    width=1920,
                    height=1080,
                )
            ],
            bit_rate=8_000_000,
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=info_no_duration,
        ):
            info = _probe(source)

        # fps is None when duration=None (must not raise AttributeError)
        assert info.fps is None  # type: ignore[attr-defined]
        # width/height are retrieved from the first video StreamInfo
        assert info.width == 1920  # type: ignore[attr-defined]
        assert info.height == 1080  # type: ignore[attr-defined]

    def test_video_source_with_valid_fps(self, tmp_path: Path) -> None:
        """Video stream present with valid duration -> fps set correctly (ADR-C2-r2).

        Video source with duration.rate=30.0 must return fps=30.0.
        """
        from clipwright_render.render import _probe

        source = str(tmp_path / "video.mp4")
        Path(source).touch()

        video_info = _make_media_info_with_video_stream(
            source, width=1920, height=1080, fps_rate=30.0
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=video_info,
        ):
            info = _probe(source)

        # fps is set from duration.rate
        assert info.fps == 30.0  # type: ignore[attr-defined]
        assert info.width == 1920  # type: ignore[attr-defined]
        assert info.height == 1080  # type: ignore[attr-defined]


class TestSingleSourceBackwardCompat:
    """Aspect 7: backward compatibility verification for single-source timelines.

    probe called once, 1 -i flag, summary format unchanged from current behaviour.
    """

    def test_single_source_probe_called_once(self, tmp_path: Path) -> None:
        """Single-source timeline -> inspect_media called only once (backward compat)."""
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        Path(src0).touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src0, 5.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        probe_calls: list[str] = []

        def _fake_inspect(path: str) -> MediaInfo:
            probe_calls.append(path)
            return _make_media_info(path=path)

        with (
            patch("clipwright_render.render.inspect_media", side_effect=_fake_inspect),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render.run",
                return_value=CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                ),
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"failed: {result.get('error')}"
        # 1 unique source -> 1 probe call
        assert len(probe_calls) == 1

    def test_single_source_summary_contains_segment_count(self, tmp_path: Path) -> None:
        """Single-source dry_run summary contains segment_count (backward compat)."""
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        Path(src0).touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src0, 5.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src0, bit_rate=8_000_000),
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"failed: {result.get('error')}"
        assert "segment_count" in result["data"]
        assert result["data"]["segment_count"] == 2
        assert "total_duration_seconds" in result["data"]
        assert abs(result["data"]["total_duration_seconds"] - 5.0) < 0.01


class TestMultiSourceDryRun:
    """Aspect 8: dry_run multi-source -> ok_result returns concatenation plan, run not called.

    ADR-C10: total_duration = sum of each clip's source_range duration.
    """

    def test_dry_run_multi_source_no_run_called(self, tmp_path: Path) -> None:
        """dry_run=True multi-source timeline -> ffmpeg run is not called."""
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        src1 = str(tmp_path / "src1.mp4")
        Path(src0).touch()
        Path(src1).touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src1, 0.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        run_called = False

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            nonlocal run_called
            run_called = True
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=lambda path: _make_media_info_with_video_stream(path),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"failed: {result.get('error')}"
        assert run_called is False

    def test_dry_run_multi_source_returns_segment_count_and_duration(
        self, tmp_path: Path
    ) -> None:
        """dry_run multi-source result contains segment_count and total_duration.

        3s + 2s = 5s timeline -> segment_count=2, total_duration=5.0.
        """
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        src1 = str(tmp_path / "src1.mp4")
        Path(src0).touch()
        Path(src1).touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src1, 0.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=lambda path: _make_media_info_with_video_stream(path),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"failed: {result.get('error')}"
        assert "segment_count" in result["data"]
        assert result["data"]["segment_count"] == 2
        assert "total_duration_seconds" in result["data"]
        assert abs(result["data"]["total_duration_seconds"] - 5.0) < 0.01


# ---------------------------------------------------------------------------
# BGM orchestration extension tests (§7 ADR-B4-r2 / B5-r2 / B6-r2 / B8)
# ---------------------------------------------------------------------------
# plan.resolve_bgm / plan.BgmClip / plan.build_plan(bgm=...) / RenderPlan.bgm_source
# inspect_media / resolve_tool / run / plan.resolve_bgm / plan.build_plan are all mocked.
# No real ffmpeg/ffprobe binaries are used.
# ---------------------------------------------------------------------------


def _make_bgm_otio_file(
    main_clips: list[tuple[str, float, float]],
    bgm_source: str,
    bgm_duration: float,
    tmp_path: Path,
) -> Path:
    """Build and write an OTIO timeline with A1 main clips + A2 BGM clip.

    The BGM clip on the A2 AudioTrack carries metadata["clipwright"]["kind"]=="bgm".
    bgm_directive is minimal (volume_db=-6.0, fade_in_sec=0.0, fade_out_sec=0.0).
    """
    import opentimelineio as otio

    # Video track + A1 main Audio track
    v_track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    a1_track = otio.schema.Track(kind=otio.schema.TrackKind.Audio, name="A1")
    for src, start, dur in main_clips:
        clip = otio.schema.Clip()
        clip.media_reference = otio.schema.ExternalReference(target_url=src)
        clip.source_range = _tr(start, dur)
        v_track.append(clip)
        a1_clip = otio.schema.Clip()
        a1_clip.media_reference = otio.schema.ExternalReference(target_url=src)
        a1_clip.source_range = _tr(start, dur)
        a1_track.append(a1_clip)

    # A2 BGM Audio track
    a2_track = otio.schema.Track(kind=otio.schema.TrackKind.Audio, name="A2")
    bgm_clip = otio.schema.Clip()
    bgm_clip.media_reference = otio.schema.ExternalReference(target_url=bgm_source)
    bgm_clip.source_range = _tr(0.0, bgm_duration)
    bgm_clip.metadata["clipwright"] = {
        "tool": "clipwright-bgm",
        "version": "0.1.0",
        "kind": "bgm",
        "volume_db": -6.0,
        "fade_in_sec": 0.0,
        "fade_out_sec": 0.0,
        "ducking": {"enabled": False, "threshold": 0.05, "ratio": 4.0},
    }
    a2_track.append(bgm_clip)

    tl = otio.schema.Timeline()
    tl.tracks.append(v_track)
    tl.tracks.append(a1_track)
    tl.tracks.append(a2_track)

    tl_path = tmp_path / "tl_with_bgm.otio"
    otio.adapters.write_to_file(tl, str(tl_path))
    return tl_path


class TestBgmResolveBgmCalled:
    """Aspect 1: resolve_bgm is called for a BGM-containing timeline; bgm= forwarded to build_plan.

    ADR-B4-r2: _render_inner calls resolve_bgm(tl) to obtain BgmClip.
    ADR-B5-r2: build_plan receives bgm=BgmClip.
    """

    def test_resolve_bgm_called_and_bgm_passed_to_build_plan(
        self, tmp_path: Path
    ) -> None:
        """BGM-containing timeline -> resolve_bgm called once, bgm=BgmClip passed to
        build_plan (ADR-B4-r2/B5-r2).

        Red: render.resolve_bgm does not yet exist -> AttributeError expected.
        """
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "main.mp4")
        bgm = str(tmp_path / "bgm.mp3")
        Path(src).touch()
        Path(bgm).touch()

        tl_path = _make_bgm_otio_file(
            [(src, 0.0, 5.0)], bgm_source=bgm, bgm_duration=30.0, tmp_path=tmp_path
        )
        output = str(tmp_path / "out.mp4")

        resolve_bgm_calls: list[Any] = []
        build_plan_bgm_args: list[Any] = []

        fake_bgm_clip_sentinel = object()  # sentinel standing in for BgmClip

        def _fake_resolve_bgm(tl: Any) -> Any:
            resolve_bgm_calls.append(tl)
            return fake_bgm_clip_sentinel

        from clipwright_render.plan import RenderPlan

        def _fake_build_plan(*args: Any, **kwargs: Any) -> RenderPlan:
            build_plan_bgm_args.append(kwargs.get("bgm"))
            return RenderPlan(
                filter_complex=(
                    "[0:v]trim=0:5,setpts=PTS-STARTPTS[v0];[v0]concat=n=1:v=1:a=0[outv]"
                ),
                ffmpeg_args=["-filter_complex", "...", "-map", "[outv]"],
                segment_count=1,
                total_duration_seconds=5.0,
                input_sources=[src],
            )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                side_effect=_fake_resolve_bgm,
            ),
            patch(
                "clipwright_render.render.build_plan",
                side_effect=_fake_build_plan,
            ),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        # resolve_bgm must be called once
        assert len(resolve_bgm_calls) == 1
        # bgm= must be forwarded to build_plan
        assert len(build_plan_bgm_args) == 1
        assert build_plan_bgm_args[0] is fake_bgm_clip_sentinel


class TestBgmFfmpegInputOrder:
    """Aspect 2: ffmpeg -i ordering is [*input_sources, -stream_loop, -1, -i, bgm_source].

    ADR-B6-r2/DC-AS-005/B5-r2: BGM appended last; -stream_loop immediately before BGM -i.
    Single source + BGM -> 2 -i flags, 1 -stream_loop.
    """

    def test_bgm_input_appended_after_main_sources_with_stream_loop(
        self, tmp_path: Path
    ) -> None:
        """Single source + BGM ffmpeg command: 2 -i flags, -stream_loop -1 before
        the second -i (ADR-B6-r2).

        Red: RenderPlan.bgm_source attribute not yet defined -> TypeError expected.
        """
        from clipwright_render.plan import RenderPlan
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "main.mp4")
        bgm = str(tmp_path / "bgm.mp3")
        Path(src).touch()
        Path(bgm).touch()
        (tmp_path / "out.mp4").touch()

        tl_path = _make_bgm_otio_file(
            [(src, 0.0, 5.0)], bgm_source=bgm, bgm_duration=30.0, tmp_path=tmp_path
        )
        output = str(tmp_path / "out.mp4")

        # Mock build_plan to return a RenderPlan with bgm_source
        # bgm_source field not yet implemented -> type: ignore[call-arg]
        fake_plan = RenderPlan(
            filter_complex=(
                "[0:v]trim=0:5,setpts=PTS-STARTPTS[v0];"
                "[v0]concat=n=1:v=1:a=1[outv][outa];"
                "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                "atrim=0:5,asetpts=PTS-STARTPTS,volume=-6dB[bgm];"
                "[main_fmt][bgm]amix=inputs=2:normalize=0,alimiter=limit=1.0[outa_bgm]"
            ),
            ffmpeg_args=[
                "-filter_complex",
                "...",
                "-map",
                "[outv]",
                "-map",
                "[outa_bgm]",
            ],
            segment_count=1,
            total_duration_seconds=5.0,
            input_sources=[src],
            bgm_source=bgm,  # type: ignore[call-arg]
        )

        captured_cmd: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                return_value=object(),  # BgmClip sentinel
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.build_plan", return_value=fake_plan),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert result["ok"] is True, f"failed: {result.get('error')}"

        # Enumerate -i occurrence indices
        i_indices = [i for i, v in enumerate(captured_cmd) if v == "-i"]
        assert len(i_indices) == 2, f"expected 2 -i flags: {captured_cmd}"

        # First -i is src (main source)
        assert captured_cmd[i_indices[0] + 1] == src

        # Second -i is bgm; immediately before it: "-stream_loop" "-1" (ADR-B6-r2)
        bgm_i_pos = i_indices[1]
        assert captured_cmd[bgm_i_pos + 1] == bgm, (
            "The token after the second -i must be bgm_source"
        )
        assert bgm_i_pos >= 2, "Not enough space for -stream_loop -1 before BGM -i"
        assert captured_cmd[bgm_i_pos - 2] == "-stream_loop", (
            f"-stream_loop must appear 2 positions before BGM -i: {captured_cmd}"
        )
        assert captured_cmd[bgm_i_pos - 1] == "-1", (
            f"-1 must appear 1 position before BGM -i: {captured_cmd}"
        )

        # BGM index = len(input_sources) = 1 (DC-AS-005 invariant)
        bgm_index = len(fake_plan.input_sources)
        assert bgm_index == 1  # single source -> BGM at index 1


class TestBgmSourceBoundaryCheck:
    """Aspects 3/4: BGM source boundary validation (ADR-B8).

    Aspect 3: BGM source outside timeline directory -> PATH_NOT_ALLOWED
    Aspect 4: output == BGM source -> PATH_NOT_ALLOWED (_check_path_not_allowed for all sources)
    """

    def test_bgm_source_outside_timeline_dir_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """ADR-PP-1: BGM source absolute + outside timeline dir (existing real file)
        must be allowed (not raise PATH_NOT_ALLOWED).

        Under old policy (ADR-B8), any source including BGM outside the timeline directory
        was rejected.  Under ADR-PP-1, the BGM source also benefits from the unified
        pathpolicy.check_media_ref which allows absolute external refs to existing real files.

        Red: current render.py raises PATH_NOT_ALLOWED for BGM source via
        _check_source_within_timeline_dir → this test FAILS until impl is done.
        """
        from clipwright_render.render import render_timeline

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        src = str(project_dir / "main.mp4")
        bgm_outside = str(outside_dir / "bgm.mp3")  # outside boundary, real file
        Path(src).touch()
        Path(bgm_outside).touch()

        tl_path = _make_bgm_otio_file(
            [(src, 0.0, 5.0)],
            bgm_source=bgm_outside,
            bgm_duration=30.0,
            tmp_path=project_dir,
        )
        output = str(project_dir / "out.mp4")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        # ADR-PP-1: absolute external real BGM source must be allowed.
        # Red until render.py delegates to pathpolicy.check_media_ref for BGM.
        assert result["ok"] is True

    def test_output_equals_bgm_source_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """output == BGM source -> PATH_NOT_ALLOWED (ADR-B8, non-destructive).

        _check_path_not_allowed must also apply to the BGM source.
        Red: render_timeline does not yet return PATH_NOT_ALLOWED -> ok=True or other error.
        """
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "main.mp4")
        bgm = str(tmp_path / "bgm.mp3")
        Path(src).touch()
        Path(bgm).touch()

        tl_path = _make_bgm_otio_file(
            [(src, 0.0, 5.0)], bgm_source=bgm, bgm_duration=30.0, tmp_path=tmp_path
        )
        # output == bgm (same path as BGM source -> violates non-destructive principle)
        output = bgm

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED


class TestBgmSourceNotFound:
    """Aspect 5: Missing BGM source -> FILE_NOT_FOUND, basename only, no absolute path (CWE-209).

    ADR-B8: existence check must also apply to BGM source.
    """

    def test_bgm_source_not_found_returns_file_not_found(self, tmp_path: Path) -> None:
        """BGM source does not exist -> FILE_NOT_FOUND (ADR-B8).

        Red: render_timeline does not yet return FILE_NOT_FOUND -> test fails.
        """
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "main.mp4")
        bgm_missing = str(tmp_path / "missing_bgm.mp3")  # does not exist
        Path(src).touch()
        # bgm_missing is intentionally not created

        tl_path = _make_bgm_otio_file(
            [(src, 0.0, 5.0)],
            bgm_source=bgm_missing,
            bgm_duration=30.0,
            tmp_path=tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND

    def test_bgm_source_not_found_basename_only_in_message(
        self, tmp_path: Path
    ) -> None:
        """Missing BGM source error message exposes only basename, not absolute path (CWE-209).

        Red: basename-only verification not yet satisfied -> test fails.
        """
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "main.mp4")
        bgm_missing = str(tmp_path / "missing_bgm.mp3")
        Path(src).touch()

        tl_path = _make_bgm_otio_file(
            [(src, 0.0, 5.0)],
            bgm_source=bgm_missing,
            bgm_duration=30.0,
            tmp_path=tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND
        error_message: str = result["error"]["message"]
        # Absolute path (directory part) must not be exposed
        assert str(tmp_path) not in error_message
        # Basename must be present
        assert "missing_bgm.mp3" in error_message


class TestBgmBackwardCompat:
    """Aspect 6: No BGM clip (resolve_bgm->None) -> backward compatibility verification.

    ADR-B7: BGM path branches on clip presence; without BGM, existing behaviour is fully preserved.
    - build_plan receives bgm=None
    - -i is input_sources only (bgm_source is None)
    - no -stream_loop
    - dry_run summary matches existing format
    """

    def test_no_bgm_build_plan_receives_bgm_none(self, tmp_path: Path) -> None:
        """No-BGM timeline -> build_plan receives bgm=None (ADR-B7).

        Red: bgm=None not forwarded, or resolve_bgm not yet importable -> ImportError.
        """
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "main.mp4")
        Path(src).touch()

        # No-BGM timeline (normal single-source)
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        build_plan_bgm_args: list[Any] = []

        from clipwright_render.plan import RenderPlan

        def _fake_build_plan(*args: Any, **kwargs: Any) -> RenderPlan:
            build_plan_bgm_args.append(kwargs.get("bgm"))
            return RenderPlan(
                filter_complex=(
                    "[0:v]trim=0:5,setpts=PTS-STARTPTS[v0];[v0]concat=n=1:v=1:a=0[outv]"
                ),
                ffmpeg_args=["-filter_complex", "...", "-map", "[outv]"],
                segment_count=1,
                total_duration_seconds=5.0,
                input_sources=[src],
            )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                return_value=None,
            ),
            patch(
                "clipwright_render.render.build_plan",
                side_effect=_fake_build_plan,
            ),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert len(build_plan_bgm_args) == 1
        assert build_plan_bgm_args[0] is None

    def test_no_bgm_ffmpeg_has_no_stream_loop(self, tmp_path: Path) -> None:
        """No BGM clip -> ffmpeg command must not contain -stream_loop (ADR-B7).

        Red: -stream_loop not excluded, or resolve_bgm not yet importable -> ImportError.
        """
        from clipwright_render.plan import RenderPlan
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "main.mp4")
        Path(src).touch()
        (tmp_path / "out.mp4").touch()

        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        # No bgm_source field (existing RenderPlan)
        fake_plan = RenderPlan(
            filter_complex=(
                "[0:v]trim=0:5,setpts=PTS-STARTPTS[v0];[v0]concat=n=1:v=1:a=0[outv]"
            ),
            ffmpeg_args=["-filter_complex", "...", "-map", "[outv]"],
            segment_count=1,
            total_duration_seconds=5.0,
            input_sources=[src],
        )

        captured_cmd: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                return_value=None,
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.build_plan", return_value=fake_plan),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert result["ok"] is True, f"failed: {result.get('error')}"
        # -stream_loop must not be present (no BGM -> ADR-B7)
        assert "-stream_loop" not in captured_cmd, (
            f"-stream_loop unexpectedly present in command: {captured_cmd}"
        )
        # -i for src only
        i_indices = [i for i, v in enumerate(captured_cmd) if v == "-i"]
        assert len(i_indices) == 1
        assert captured_cmd[i_indices[0] + 1] == src

    def test_no_bgm_dry_run_summary_unchanged(self, tmp_path: Path) -> None:
        """No BGM clip -> dry_run summary format unchanged from pre-BGM-extension (ADR-B7).

        Backward compatibility test: repeats the same assertions as
        test_dry_run_summary_contains_segment_count_and_duration after BGM extension.
        Red: resolve_bgm not yet importable -> ImportError.
        """
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "main.mp4")
        Path(src).touch()

        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src, bit_rate=8_000_000),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                return_value=None,
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True
        assert "summary" in result
        # segment_count must be present
        assert "1" in result["summary"]
        # data must contain segment_count and total_duration_seconds
        assert result["data"]["segment_count"] == 1
        assert abs(result["data"]["total_duration_seconds"] - 5.0) < 0.01


class TestBgmDryRun:
    """Aspect 7: dry_run (with BGM) -> ok_result contains filter_complex, run not called.

    ADR-B5-r2: dry_run returns filter_complex (including BGM stage) in data.
    run must not be called.
    """

    def test_bgm_dry_run_returns_ok_and_no_run(self, tmp_path: Path) -> None:
        """BGM-present dry_run=True -> ok=True returned, ffmpeg run not called.

        Red: resolve_bgm or RenderPlan.bgm_source does not yet exist -> AttributeError.
        """
        from clipwright_render.plan import RenderPlan
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "main.mp4")
        bgm = str(tmp_path / "bgm.mp3")
        Path(src).touch()
        Path(bgm).touch()

        tl_path = _make_bgm_otio_file(
            [(src, 0.0, 5.0)], bgm_source=bgm, bgm_duration=30.0, tmp_path=tmp_path
        )
        output = str(tmp_path / "out.mp4")

        bgm_filter = (
            "[0:v]trim=0:5,setpts=PTS-STARTPTS[v0];"
            "[0:a]atrim=0:5,asetpts=PTS-STARTPTS,"
            "aformat=sample_rates=48000:channel_layouts=stereo[a0];"
            "[v0][a0]concat=n=1:v=1:a=1[outv][outa];"
            "[outa]aformat=sample_rates=48000:channel_layouts=stereo[main_fmt];"
            "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,"
            "atrim=0:5,asetpts=PTS-STARTPTS,volume=-6dB[bgm];"
            "[main_fmt][bgm]amix=inputs=2:normalize=0,alimiter=limit=1.0[outa_bgm]"
        )
        fake_plan = RenderPlan(
            filter_complex=bgm_filter,
            ffmpeg_args=[
                "-filter_complex",
                bgm_filter,
                "-map",
                "[outv]",
                "-map",
                "[outa_bgm]",
            ],
            segment_count=1,
            total_duration_seconds=5.0,
            input_sources=[src],
            bgm_source=bgm,  # type: ignore[call-arg]
        )

        run_called = False

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            nonlocal run_called
            run_called = True
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                return_value=object(),  # BgmClip sentinel
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.build_plan", return_value=fake_plan),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"failed: {result.get('error')}"
        # run must not have been called
        assert run_called is False
        # data must contain filter_complex
        assert "filter_complex" in result["data"]
        # filter_complex must contain a BGM stage (amix, alimiter, or bgm label)
        fc = result["data"]["filter_complex"]
        assert "amix" in fc or "alimiter" in fc or "bgm" in fc.lower(), (
            f"No BGM stage found in filter_complex: {fc}"
        )


# ---------------------------------------------------------------------------
# Subtitle burn-in orchestration tests (§7 v2 ADR-S4-r2/S5-r2/S7/S10)
# ---------------------------------------------------------------------------
# - When options.subtitle is present, _render_inner applies boundary validation,
#   absolute-path conversion, and extension whitelist.
# - Subtitles are read directly via filename= inside -filter_complex, so no extra -i (ADR-S10).
# - subtitle=None is backward compatible (skips subtitle validation, ADR-S8).
# inspect_media / resolve_tool / run / build_plan are all mocked.
# No real ffmpeg/ffprobe binaries are used.
# ---------------------------------------------------------------------------


def _make_subtitle_render_setup(
    tmp_path: Path,
    *,
    subtitle_filename: str = "subs.srt",
    inside_timeline_dir: bool = True,
) -> tuple[str, str, str, Path]:
    """Setup helper for subtitle tests.

    inside_timeline_dir=True  : timeline placed in tmp_path/project/;
                                 subtitle also placed directly under project/ (within boundary).
    inside_timeline_dir=False : timeline placed in tmp_path/project/;
                                 subtitle placed in tmp_path/elsewhere/ (truly outside boundary).
    Uses the same "project vs outside" pattern as source/BGM boundary tests.

    Returns:
        (source_path, subtitle_path, output_path, tl_path)
    """
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True, exist_ok=True)

    src = str(project_dir / "source.mp4")
    Path(src).touch()

    if inside_timeline_dir:
        # Place subtitle in the same project directory as the timeline
        sub_path = project_dir / subtitle_filename
        sub_path.touch()
        subtitle = str(sub_path)
    else:
        # Place outside the timeline (tmp_path/elsewhere/) -> truly outside boundary
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir(parents=True, exist_ok=True)
        sub_path = elsewhere / subtitle_filename
        sub_path.touch()
        subtitle = str(sub_path)

    tl_path = project_dir / "tl.otio"
    _write_timeline(tl_path, [_make_clip(src, 0.0, 5.0)])
    output = str(project_dir / "out.mp4")
    return src, subtitle, output, tl_path


class TestSubtitleBoundaryAndValidation:
    """Aspects 1-5: subtitle path boundary validation, extension whitelist, fonts_dir
    validation (ADR-S7/S4-r2/S5-r2).

    When _render_inner receives options.subtitle:
    - timeline dir confinement enforced (_check_source_within_timeline_dir)
    - existence check (FILE_NOT_FOUND, basename only, CWE-209)
    - extension whitelist (srt/vtt/ass; others -> INVALID_INPUT)
    - fonts_dir missing -> INVALID_INPUT
    - absolutised and forwarded to build_plan (DC-AS-005)

    SubtitleOptions / RenderOptions.subtitle not yet implemented, so all tests
    are expected to fail Red with ImportError / AttributeError.
    """

    def test_subtitle_within_timeline_dir_builds_plan_with_absolute_path(
        self, tmp_path: Path
    ) -> None:
        """Aspect 1: subtitle path inside timeline dir -> passes boundary validation;
        options forwarded to build_plan with absolutised subtitle.path (ADR-S7/S5-r2).

        Strictly verifies that options.subtitle.path passed to build_plan is absolute
        (filename= is cwd-independent, DC-AS-005).
        Red: SubtitleOptions not yet importable -> ImportError.
        """
        from clipwright_render.render import render_timeline

        src, subtitle, output, tl_path = _make_subtitle_render_setup(tmp_path)

        # SubtitleOptions and RenderOptions.subtitle not yet implemented -> type: ignore
        from clipwright_render.schemas import (  # type: ignore[attr-defined]
            RenderOptions,
            SubtitleOptions,
        )

        options = RenderOptions(
            subtitle=SubtitleOptions(path=subtitle)  # type: ignore[call-arg]
        )

        build_plan_options_received: list[Any] = []

        from clipwright_render.plan import RenderPlan

        def _fake_build_plan(*args: Any, **kwargs: Any) -> RenderPlan:
            # options is the 3rd positional arg or a keyword
            if len(args) >= 3:
                build_plan_options_received.append(args[2])
            else:
                build_plan_options_received.append(kwargs.get("options"))
            return RenderPlan(
                filter_complex="[0:v]trim=0:5,setpts=PTS-STARTPTS[v0];[v0]concat=n=1:v=1:a=0[outv]",
                ffmpeg_args=["-filter_complex", "...", "-map", "[outv]"],
                segment_count=1,
                total_duration_seconds=5.0,
                input_sources=[src],
            )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                return_value=None,
            ),
            patch(
                "clipwright_render.render.build_plan",
                side_effect=_fake_build_plan,
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=options,
                dry_run=True,
            )

        assert result["ok"] is True, (
            f"subtitle within boundary failed: {result.get('error')}"
        )
        assert len(build_plan_options_received) == 1
        received_options = build_plan_options_received[0]
        # subtitle.path must be absolute (DC-AS-005, filename= is cwd-independent)
        assert received_options.subtitle is not None
        received_path = received_options.subtitle.path
        assert Path(received_path).is_absolute(), (
            f"subtitle.path is not absolute: {received_path}"
        )
        # Absolute path must point to the expected subtitle file
        assert Path(received_path).name == "subs.srt"

    def test_subtitle_outside_timeline_dir_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """ADR-PP-1: Absolute subtitle ref (existing .srt) outside timeline dir
        must be allowed (not raise PATH_NOT_ALLOWED).

        Under old policy (ADR-S7), subtitle path was required to be within the
        timeline directory.  Under ADR-PP-1, pathpolicy.check_media_ref allows
        absolute references to existing real files regardless of location.

        Red: current render.py raises PATH_NOT_ALLOWED via
        _check_subtitle_within_timeline_dir → this test FAILS until impl is done.
        """
        from clipwright_render.render import render_timeline

        src, subtitle, output, tl_path = _make_subtitle_render_setup(
            tmp_path, inside_timeline_dir=False
        )

        from clipwright_render.schemas import (  # type: ignore[attr-defined]
            RenderOptions,
            SubtitleOptions,
        )

        options = RenderOptions(
            subtitle=SubtitleOptions(path=subtitle)  # type: ignore[call-arg]
        )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=options,
                dry_run=True,
            )

        # ADR-PP-1: absolute external subtitle must be allowed.
        # Red until render.py delegates to pathpolicy.check_media_ref for subtitle.
        assert result["ok"] is True

    def test_subtitle_in_subdir_of_timeline_dir_is_allowed(
        self, tmp_path: Path
    ) -> None:
        """Aspect 2b: subtitle in a subdirectory of timeline dir -> passes boundary (ADR-S7 recursive allow).

        Same "recursive subdirectory allow" logic as _check_source_within_timeline_dir:
        timeline_dir/subs/foo.srt must not trigger PATH_NOT_ALLOWED.
        Same allow condition as source/BGM boundary tests.
        """
        from clipwright_render.render import render_timeline
        from clipwright_render.schemas import (  # type: ignore[attr-defined]
            RenderOptions,
            SubtitleOptions,
        )

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        subs_dir = project_dir / "subs"
        subs_dir.mkdir()

        src = str(project_dir / "source.mp4")
        Path(src).touch()
        sub_in_subdir = str(subs_dir / "foo.srt")
        Path(sub_in_subdir).touch()

        tl_path = project_dir / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 5.0)])
        output = str(project_dir / "out.mp4")

        options = RenderOptions(
            subtitle=SubtitleOptions(path=sub_in_subdir)  # type: ignore[call-arg]
        )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                return_value=None,
            ),
            patch(
                "clipwright_render.render.build_plan",
                return_value=__import__(
                    "clipwright_render.plan", fromlist=["RenderPlan"]
                ).RenderPlan(
                    filter_complex="[0:v]trim=0:5,setpts=PTS-STARTPTS[v0];[v0]concat=n=1:v=1:a=0[outv]",
                    ffmpeg_args=["-filter_complex", "...", "-map", "[outv]"],
                    segment_count=1,
                    total_duration_seconds=5.0,
                    input_sources=[src],
                ),
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=options,
                dry_run=True,
            )

        # Subtitle in subdirectory passes boundary validation -> ok=True (S-M-3 / CR-T-004)
        # Assert ok=True definitively rather than merely "not PATH_NOT_ALLOWED"
        assert result["ok"] is True, (
            f"Subtitle in subdirectory unexpectedly failed: {result.get('error')}"
        )

    def test_subtitle_not_found_returns_file_not_found_basename_only(
        self, tmp_path: Path
    ) -> None:
        """Aspect 3: missing subtitle path -> FILE_NOT_FOUND, basename only, no absolute path (CWE-209).

        error.message must not contain the absolute path (directory part), only the basename.
        Red: render_timeline does not yet return FILE_NOT_FOUND -> test fails.
        """
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "source.mp4")
        Path(src).touch()
        # Subtitle file intentionally not created (missing path)
        missing_sub = str(tmp_path / "missing_subs.srt")

        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        from clipwright_render.schemas import (  # type: ignore[attr-defined]
            RenderOptions,
            SubtitleOptions,
        )

        options = RenderOptions(
            subtitle=SubtitleOptions(path=missing_sub)  # type: ignore[call-arg]
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=options,
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND
        error_message: str = result["error"]["message"]
        # Absolute path (directory part) must not be exposed (CWE-209)
        assert str(tmp_path) not in error_message
        # Basename must be present
        assert "missing_subs.srt" in error_message

    @pytest.mark.parametrize("bad_ext", [".txt", ".pdf", ".subs", ".xml", ""])
    def test_invalid_subtitle_extension_raises_invalid_input(
        self, tmp_path: Path, bad_ext: str
    ) -> None:
        """Aspect 4: extension not on whitelist (.srt/.vtt/.ass) -> INVALID_INPUT (ADR-S3).

        Red: render_timeline does not yet return INVALID_INPUT -> test fails.
        """
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "source.mp4")
        Path(src).touch()
        # Create the file with an invalid extension (exists, but extension not on whitelist)
        bad_sub = tmp_path / f"subs{bad_ext}"
        bad_sub.touch()

        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        from clipwright_render.schemas import (  # type: ignore[attr-defined]
            RenderOptions,
            SubtitleOptions,
        )

        options = RenderOptions(
            subtitle=SubtitleOptions(path=str(bad_sub))  # type: ignore[call-arg]
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=options,
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_fonts_dir_not_found_raises_invalid_input(self, tmp_path: Path) -> None:
        """Aspect 5: fonts_dir points to non-existent directory -> INVALID_INPUT (ADR-S7).

        fonts_dir has no boundary enforcement, but existence as a directory is validated.
        Red: render_timeline does not yet return INVALID_INPUT -> test fails.
        """
        from clipwright_render.render import render_timeline

        src, subtitle, output, tl_path = _make_subtitle_render_setup(tmp_path)
        missing_fonts_dir = str(tmp_path / "nonexistent_fonts")

        from clipwright_render.schemas import (  # type: ignore[attr-defined]
            RenderOptions,
            SubtitleOptions,
        )

        options = RenderOptions(
            subtitle=SubtitleOptions(  # type: ignore[call-arg]
                path=subtitle,
                fonts_dir=missing_fonts_dir,
            )
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=options,
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT
        # fonts_dir absolute path must not be exposed in error message (SR-R-001 / CWE-209)
        error_message: str = result["error"]["message"]
        assert str(tmp_path) not in error_message, (
            f"fonts_dir parent path (tmp_path) exposed in error message: {error_message}"
        )
        assert missing_fonts_dir not in error_message, (
            f"fonts_dir absolute path exposed in error message: {error_message}"
        )

    def test_relative_fonts_dir_is_absolutized_before_build_plan(
        self, tmp_path: Path
    ) -> None:
        """Aspect SR-M-2: relative fonts_dir -> absolutised before forwarding to build_plan (SR-INJ-002).

        ADR-S5-r2 scope extended to fonts_dir: render.py resolves fonts_dir to absolute
        path before passing it to build_plan (SR-INJ-002).
        """
        from clipwright_render.plan import RenderPlan
        from clipwright_render.render import render_timeline

        src, subtitle, output, tl_path = _make_subtitle_render_setup(tmp_path)
        # Specify an existing directory (tmp_path itself) as a relative path
        fonts_dir_abs = tmp_path
        # Compute relative path assuming os.getcwd() is the parent of tmp_path
        import os

        try:
            fonts_dir_rel = os.path.relpath(str(fonts_dir_abs))
        except ValueError:
            # Skip on Windows when drives differ
            pytest.skip("Cannot compute relative path (different drives on Windows)")

        from clipwright_render.schemas import (  # type: ignore[attr-defined]
            RenderOptions,
            SubtitleOptions,
        )

        options = RenderOptions(
            subtitle=SubtitleOptions(  # type: ignore[call-arg]
                path=subtitle,
                fonts_dir=fonts_dir_rel,  # relative path specified
            )
        )

        build_plan_options_received: list[Any] = []

        def _fake_build_plan(*args: Any, **kwargs: Any) -> RenderPlan:
            if len(args) >= 3:
                build_plan_options_received.append(args[2])
            else:
                build_plan_options_received.append(kwargs.get("options"))
            return RenderPlan(
                filter_complex="[0:v]trim=0:5,setpts=PTS-STARTPTS[v0];[v0]concat=n=1:v=1:a=0[outv]",
                ffmpeg_args=["-filter_complex", "...", "-map", "[outv]"],
                segment_count=1,
                total_duration_seconds=5.0,
                input_sources=[src],
            )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                return_value=None,
            ),
            patch(
                "clipwright_render.render.build_plan",
                side_effect=_fake_build_plan,
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=options,
                dry_run=True,
            )

        assert result["ok"] is True, f"relative fonts_dir failed: {result.get('error')}"
        assert len(build_plan_options_received) == 1
        received_options = build_plan_options_received[0]
        assert received_options.subtitle is not None
        received_fonts_dir = received_options.subtitle.fonts_dir
        assert received_fonts_dir is not None
        # fonts_dir must be converted to absolute path (SR-INJ-002, ADR-S5-r2 extended)
        assert Path(received_fonts_dir).is_absolute(), (
            f"fonts_dir not converted to absolute path: {received_fonts_dir}"
        )
        # Absolute path must point to the correct location
        assert Path(received_fonts_dir).resolve() == fonts_dir_abs.resolve(), (
            f"fonts_dir absolute path differs from expected: {received_fonts_dir}"
        )


class TestSubtitleNoAdditionalInputFlag:
    """Aspect 6: with subtitle, -i is still input_sources only (ADR-S10).

    Subtitles are read directly via filename= inside -filter_complex, so no extra -i.
    Unlike BGM, no additional -i flag.
    Red: -i count increases, or AttributeError -> test fails.
    """

    def test_subtitle_present_i_flag_count_equals_source_count(
        self, tmp_path: Path
    ) -> None:
        """Subtitle present, single source -> ffmpeg command has exactly 1 -i (input_sources only, ADR-S10).

        Subtitle -i must not be added (unlike BGM, ADR-S10).
        """
        from clipwright_render.plan import RenderPlan
        from clipwright_render.render import render_timeline

        src, subtitle, output, tl_path = _make_subtitle_render_setup(tmp_path)
        Path(output).touch()

        from clipwright_render.schemas import (  # type: ignore[attr-defined]
            RenderOptions,
            SubtitleOptions,
        )

        options = RenderOptions(
            subtitle=SubtitleOptions(path=subtitle),  # type: ignore[call-arg]
            overwrite=True,
        )

        # RenderPlan returned by build_plan with subtitle options (input_sources unchanged)
        fake_plan = RenderPlan(
            filter_complex=(
                "[0:v]trim=0:5,setpts=PTS-STARTPTS[v0];"
                "[v0]subtitles=filename='subs.srt'[outvsub];"
                "[outvsub]concat=n=1:v=1:a=0[outv]"
            ),
            ffmpeg_args=["-filter_complex", "...", "-map", "[outv]"],
            segment_count=1,
            total_duration_seconds=5.0,
            input_sources=[src],  # subtitle is not included in input_sources
        )

        captured_cmd: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                return_value=None,
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.build_plan", return_value=fake_plan),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=options,
            )

        assert result["ok"] is True, f"failed: {result.get('error')}"
        # -i count must equal input_sources count (no subtitle -i added, ADR-S10)
        i_indices = [i for i, v in enumerate(captured_cmd) if v == "-i"]
        assert len(i_indices) == 1, (
            f"subtitle present but {len(i_indices)} -i flags found: {captured_cmd}"
        )
        assert captured_cmd[i_indices[0] + 1] == src


class TestSubtitleBackwardCompat:
    """Aspect 7: subtitle=None -> subtitle validation skipped, existing -i/summary unchanged (ADR-S8).

    Backward compatibility: adding subtitle field must not change behaviour when None.
    None must work even when SubtitleOptions is not yet implemented.
    """

    def test_subtitle_none_skips_subtitle_validation(self, tmp_path: Path) -> None:
        """subtitle=None -> subtitle path validation not called; result same as existing path (ADR-S8).

        subtitle=None returns ok=True and 1 -i flag (input_sources only).
        """
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "source.mp4")
        Path(src).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                return_value=None,
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),  # subtitle=None (default)
                dry_run=True,
            )

        assert result["ok"] is True, f"subtitle=None failed: {result.get('error')}"
        # data must contain segment_count (existing spec maintained)
        assert result["data"]["segment_count"] == 1
        assert abs(result["data"]["total_duration_seconds"] - 5.0) < 0.01

    def test_subtitle_none_ffmpeg_i_count_unchanged(self, tmp_path: Path) -> None:
        """subtitle=None -> ffmpeg -i count is input_sources only (backward compat, ADR-S8).

        Same 1 -i flag as before the subtitle field was added.
        """
        from clipwright_render.plan import RenderPlan
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "source.mp4")
        Path(src).touch()
        (tmp_path / "out.mp4").touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        fake_plan = RenderPlan(
            filter_complex="[0:v]trim=0:5,setpts=PTS-STARTPTS[v0];[v0]concat=n=1:v=1:a=0[outv]",
            ffmpeg_args=["-filter_complex", "...", "-map", "[outv]"],
            segment_count=1,
            total_duration_seconds=5.0,
            input_sources=[src],
        )

        captured_cmd: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                return_value=None,
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.build_plan", return_value=fake_plan),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),  # subtitle=None (default)
            )

        assert result["ok"] is True, f"failed: {result.get('error')}"
        i_indices = [i for i, v in enumerate(captured_cmd) if v == "-i"]
        assert len(i_indices) == 1
        assert captured_cmd[i_indices[0] + 1] == src


class TestSubtitleDryRun:
    """Aspect 8: dry_run (with subtitle) -> filter_complex contains subtitle stage, run not called (ADR-S10).

    filter_complex returned by build_plan must be present in data, including the
    subtitle filename= stage. run must not be called.
    Red: AttributeError or subtitle stage absent -> test fails.
    """

    def test_subtitle_dry_run_filter_complex_contains_subtitle_segment(
        self, tmp_path: Path
    ) -> None:
        """dry_run (with subtitle) -> data.filter_complex contains subtitle filename= stage,
        run not called (ADR-S10).

        Mocks build_plan to return a filter_complex with a subtitle stage, and verifies
        that render_timeline reflects it in data.
        """
        from clipwright_render.plan import RenderPlan
        from clipwright_render.render import render_timeline

        src, subtitle, output, tl_path = _make_subtitle_render_setup(tmp_path)

        from clipwright_render.schemas import (  # type: ignore[attr-defined]
            RenderOptions,
            SubtitleOptions,
        )

        options = RenderOptions(
            subtitle=SubtitleOptions(path=subtitle)  # type: ignore[call-arg]
        )

        subtitle_abs = str(Path(subtitle).resolve())

        # build_plan returns a filter_complex containing the subtitle stage (equivalent to _append_subtitle_filter)
        subtitle_filter = (
            f"[0:v]trim=0:5,setpts=PTS-STARTPTS[v0];"
            f"[v0]subtitles=filename='{subtitle_abs}'[outvsub];"
            f"[outvsub]concat=n=1:v=1:a=0[outv]"
        )
        fake_plan = RenderPlan(
            filter_complex=subtitle_filter,
            ffmpeg_args=["-filter_complex", subtitle_filter, "-map", "[outv]"],
            segment_count=1,
            total_duration_seconds=5.0,
            input_sources=[src],
        )

        run_called = False

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            nonlocal run_called
            run_called = True
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                return_value=None,
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.build_plan", return_value=fake_plan),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=options,
                dry_run=True,
            )

        assert result["ok"] is True, f"failed: {result.get('error')}"
        # run must not have been called (dry_run)
        assert run_called is False
        # filter_complex must contain a subtitle stage (subtitles=filename= or [outvsub])
        fc = result["data"]["filter_complex"]
        assert "subtitles" in fc or "outvsub" in fc, (
            f"No subtitle stage found in filter_complex: {fc}"
        )
