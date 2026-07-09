"""test_pip_render_wiring.py — Red-phase tests for [SR-NEW]
(security-review-report-pip.md, Critical).

Target: the REAL ffmpeg execution path (`render_timeline` / `_render_inner`),
NOT `render_plan()` / `_build_ffmpeg_inputs()` (a test-only orchestration
helper documented as "Minimal orchestration layer used by tests" in
render.py, which already reads `plan.pip_sources` correctly).

security-review-report-pip.md ([SR-NEW], Critical) found two independent
defects that together mean PiP video is never actually rendered and never
receives the same render-time re-validation that image_overlay gets:

  1. `build_plan()`'s `return RenderPlan(...)` (plan.py ~L5169-5180) never
     passes `pip_sources=...`, so `RenderPlan.pip_sources` is always `[]`
     (the dataclass `field(default_factory=list)` default) even when
     `_collect_pip_overlays` found real pip_overlay markers.
  2. `_render_inner`'s actual `-i` construction loop (render.py ~L1186-1245)
     only reads `plan.input_sources` / `plan.bgm_source` / `plan.image_sources`.
     It never reads `plan.pip_sources` at all, and therefore never applies
     the second-layer defence-in-depth checks that image_overlay receives
     there (existence -> extension allowlist -> check_media_ref (CWE-22/
     CWE-59 boundary+symlink re-check, TOCTOU) -> magic-byte/content check
     -> check_output_not_source).

Section 1 exercises the full stack (build_plan is NOT mocked) to catch
defect 1 and defect 2 together: a real pip_overlay marker's media_path must
end up as an ffmpeg `-i` argument.

Section 2 isolates defect 2 (render.py's second-layer validation) by mocking
`clipwright_render.render.build_plan` to return a fixed RenderPlan with
`pip_sources` already populated — mirroring the existing BGM `captured_cmd`
integration-test style in test_render.py (`TestBgm...`: mocks build_plan +
inspect_media + resolve_tool + run, then inspects the constructed ffmpeg
command). This isolates "does render.py re-validate/wire pip_sources" from
"does build_plan populate pip_sources correctly" (Section 1's concern).

Test isolation:
  - No cross-file import of helpers (mirrors test_pip_video.py / test_pip_audio.py
    / test_render.py — each test file defines its own small OTIO/mock helpers).
  - Symlink test is skipped (not xfail) when the host cannot create symlinks
    without elevated privileges (same policy as
    test_render.py::test_symlink_source_raises_path_not_allowed).
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ErrorCode
from clipwright.schemas import MediaInfo, StreamInfo

from clipwright_render.schemas import RenderOptions

# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_pip_video.py / test_render.py — no cross-file
# import).
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


def _make_timeline(clips: list[Any]) -> otio.schema.Timeline:
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    for c in clips:
        track.append(c)
    tl = otio.schema.Timeline()
    tl.tracks.append(track)
    return tl


def _make_media_info(
    path: str = "/fake/source.mp4",
    *,
    bit_rate: int | None = 8_000_000,
    has_video: bool = True,
    audio_streams: int = 1,
) -> MediaInfo:
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(StreamInfo(index=0, codec_type="video", codec_name="h264"))
    for _i in range(audio_streams):
        streams.append(
            StreamInfo(index=len(streams), codec_type="audio", codec_name="aac")
        )
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=None,
        streams=streams,
        bit_rate=bit_rate,
    )


def _add_pip_overlay_marker(
    timeline: otio.schema.Timeline,
    *,
    media_path: str = "clips/pip.mp4",
    start_sec: float = 1.0,
    duration_sec: float = 3.0,
    media_start_sec: float = 0.0,
    x: str = "(W-w)/2",
    y: str = "(H-h)/2",
    scale: float = 0.3,
    opacity: float = 1.0,
    fade_in_sec: float = 0.0,
    fade_out_sec: float = 0.0,
    mix_audio: bool = False,
    audio_volume: float = 1.0,
    name: str = "pip_0",
) -> None:
    """Attach a pip_overlay marker directly to the first video track.

    mix_audio defaults to False (unlike test_pip_video.py's helper default of
    also False) so that this file's Section 1 wiring test is isolated from
    the F-1 ducking-type bug (code-review-report-pip.md); see module
    docstring.
    """
    video_track: otio.schema.Track | None = None
    for track in timeline.tracks:
        if track.kind == otio.schema.TrackKind.Video:
            video_track = track
            break
    assert video_track is not None, "timeline must have a video track"

    marked_range = _tr(start_sec, duration_sec)
    marker = otio.schema.Marker(
        name=name,
        marked_range=marked_range,
        metadata={
            "clipwright": {
                "kind": "pip_overlay",
                "tool": "clipwright-overlay",
                "version": "0.1.0",
                "media_path": media_path,
                "start_sec": start_sec,
                "duration_sec": duration_sec,
                "media_start_sec": media_start_sec,
                "x": x,
                "y": y,
                "scale": scale,
                "opacity": opacity,
                "fade_in_sec": fade_in_sec,
                "fade_out_sec": fade_out_sec,
                "mix_audio": mix_audio,
                "audio_volume": audio_volume,
                "ducking": {"enabled": False, "threshold": 0.05, "ratio": 4.0},
            }
        },
    )
    video_track.markers.append(marker)


def _write_full_timeline(path: Path, tl: otio.schema.Timeline) -> None:
    otio.adapters.write_to_file(tl, str(path))


def _fake_run_capturing(captured_cmd: list[str]) -> Any:
    def _run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        captured_cmd.extend(cmd)
        # Create the output file (last argument to ffmpeg is typically the output)
        # so that render_timeline's output existence check passes (mimics ffmpeg
        # successfully writing the output file).
        if cmd and not cmd[-1].startswith("-"):
            Path(cmd[-1]).touch()
        return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    return _run


# ===========================================================================
# Section 1: full-stack wiring — build_plan is NOT mocked (catches both
# defect 1 [build_plan omits pip_sources] and defect 2 [_render_inner ignores
# plan.pip_sources] together).
# ===========================================================================


class TestPipSourceWiredIntoFfmpegInputs:
    """A real pip_overlay marker's media_path must reach the ffmpeg -i args."""

    def test_pip_media_path_appears_in_ffmpeg_dash_i_args(self, tmp_path: Path) -> None:
        from clipwright_render.render import render_timeline

        main_src = tmp_path / "main.mp4"
        main_src.touch()
        pip_dir = tmp_path / "clips"
        pip_dir.mkdir()
        pip_src = pip_dir / "pip.mp4"
        pip_src.touch()

        tl = _make_timeline([_make_clip(str(main_src), 0.0, 10.0)])
        _add_pip_overlay_marker(
            tl, media_path="clips/pip.mp4", start_sec=1.0, duration_sec=3.0
        )
        tl_path = tmp_path / "tl.otio"
        _write_full_timeline(tl_path, tl)
        output = str(tmp_path / "out.mp4")

        captured_cmd: list[str] = []

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=str(main_src)),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render.run",
                side_effect=_fake_run_capturing(captured_cmd),
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert result["ok"] is True, f"render_timeline failed: {result.get('error')}"
        expected_pip_path = str(pip_src.resolve())
        assert expected_pip_path in captured_cmd, (
            "PiP media_path must be added as an ffmpeg -i input"
            " (SR-NEW: RenderPlan.pip_sources is never populated by build_plan,"
            f" and/or _render_inner never reads it): {captured_cmd!r}"
        )

    def test_pip_stream_index_has_a_corresponding_dash_i(self, tmp_path: Path) -> None:
        """The filter_complex references [{pip.input_index}:v]; there must be
        an -i entry occupying that stream index (otherwise ffmpeg would fail
        with 'Stream specifier matches no streams' — the functional failure
        mode described in security-review-report-pip.md)."""
        from clipwright_render.render import render_timeline

        main_src = tmp_path / "main.mp4"
        main_src.touch()
        pip_dir = tmp_path / "clips"
        pip_dir.mkdir()
        pip_src = pip_dir / "pip.mp4"
        pip_src.touch()

        tl = _make_timeline([_make_clip(str(main_src), 0.0, 10.0)])
        _add_pip_overlay_marker(tl, media_path="clips/pip.mp4")
        tl_path = tmp_path / "tl.otio"
        _write_full_timeline(tl_path, tl)
        output = str(tmp_path / "out.mp4")

        captured_cmd: list[str] = []

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=str(main_src)),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render.run",
                side_effect=_fake_run_capturing(captured_cmd),
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert result["ok"] is True, f"render_timeline failed: {result.get('error')}"
        # Single main source, no bgm, no image overlays -> pip_index_base == 1,
        # so the filter_complex references "[1:v]". There must be exactly two
        # -i flags (main + pip) for stream index 1 to exist.
        i_indices = [i for i, v in enumerate(captured_cmd) if v == "-i"]
        assert len(i_indices) == 2, (
            "Expected 2 -i flags (main source + PiP source) so that filter_complex's"
            f" [1:v] reference resolves to a real input: {captured_cmd!r}"
        )


# ===========================================================================
# Section 2: second-layer render-time validation of pip_sources, isolated by
# mocking build_plan's return value (mirrors TestBgm...'s captured_cmd style
# in test_render.py).
# ===========================================================================


class TestPipSourceSecondLayerValidation:
    """render.py must re-validate plan.pip_sources with the same
    defence-in-depth as plan.image_sources (existence -> extension allowlist
    -> check_media_ref -> check_output_not_source). Isolated from
    build_plan/_marker_to_pip_overlay by mocking build_plan directly, so
    these tests target render.py's wiring regardless of whether build_plan
    itself has been fixed to populate pip_sources (Section 1's concern)."""

    def _fake_plan(self, main_src: str, pip_source: str) -> Any:
        from clipwright_render.plan import RenderPlan

        return RenderPlan(
            filter_complex="[0:v][1:v]overlay=x='0':y='0'[outv]",
            ffmpeg_args=["-map", "[outv]", "-map", "0:a?"],
            segment_count=1,
            total_duration_seconds=5.0,
            input_sources=[main_src],
            pip_sources=[pip_source],
        )

    def _render_with_fake_plan(
        self,
        tmp_path: Path,
        main_src: str,
        pip_source: str,
        output: str,
    ) -> Any:
        from clipwright_render.render import render_timeline

        tl = _make_timeline([_make_clip(main_src, 0.0, 5.0)])
        tl_path = tmp_path / "tl.otio"
        _write_full_timeline(tl_path, tl)

        fake_plan = self._fake_plan(main_src, pip_source)

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=main_src),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.build_plan", return_value=fake_plan),
            patch("clipwright_render.render.run") as mock_run,
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )
            self._last_mock_run = mock_run
        return result

    def test_missing_pip_source_raises_file_not_found(self, tmp_path: Path) -> None:
        main_src = str(tmp_path / "main.mp4")
        Path(main_src).touch()
        output = str(tmp_path / "out.mp4")
        missing_pip = str(tmp_path / "missing_pip.mp4")  # deliberately never created

        result = self._render_with_fake_plan(tmp_path, main_src, missing_pip, output)

        assert result["ok"] is False, (
            "A missing PiP source must be rejected before ffmpeg runs (SR-NEW"
            f" second-layer defence not wired into _render_inner): {result!r}"
        )
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND
        # CWE-209: basename only, no directory component leaked.
        assert "missing_pip.mp4" in result["error"]["message"]
        assert str(tmp_path) not in result["error"]["message"]
        self._last_mock_run.assert_not_called()

    def test_disallowed_extension_pip_source_rejected(self, tmp_path: Path) -> None:
        main_src = str(tmp_path / "main.mp4")
        Path(main_src).touch()
        output = str(tmp_path / "out.mp4")
        bad_ext_pip = tmp_path / "pip.avi"  # not in _ALLOWED_PIP_VIDEO_EXTENSIONS
        bad_ext_pip.touch()

        result = self._render_with_fake_plan(
            tmp_path, main_src, str(bad_ext_pip), output
        )

        assert result["ok"] is False, (
            "A PiP source with a disallowed extension must be rejected before"
            f" ffmpeg runs: {result!r}"
        )
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT
        self._last_mock_run.assert_not_called()

    def test_symlink_pip_source_raises_path_not_allowed(self, tmp_path: Path) -> None:
        """TOCTOU: media_path validated once at clipwright_add_pip time is not
        re-validated at render time today. This models the case where the
        media_path is (or has become, via symlink swap) a symlink by the time
        render runs; check_media_ref's symlink rejection must fire again."""
        main_src = str(tmp_path / "main.mp4")
        Path(main_src).touch()
        output = str(tmp_path / "out.mp4")

        real_file = tmp_path / "real_pip.mp4"
        real_file.touch()
        pip_symlink = tmp_path / "pip_link.mp4"
        try:
            pip_symlink.symlink_to(real_file)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(
                "Symlink creation failed (insufficient privileges or unsupported"
                f" environment): {exc}"
            )

        result = self._render_with_fake_plan(
            tmp_path, main_src, str(pip_symlink), output
        )

        assert result["ok"] is False, (
            "A symlinked PiP source must be rejected via check_media_ref (CWE-59"
            f" / TOCTOU re-check not wired into _render_inner): {result!r}"
        )
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED
        error_message: str = result["error"]["message"]
        assert str(tmp_path) not in error_message
        assert "pip_link.mp4" not in error_message
        self._last_mock_run.assert_not_called()

    def test_pip_source_equal_to_output_rejected(self, tmp_path: Path) -> None:
        main_src = str(tmp_path / "main.mp4")
        Path(main_src).touch()
        output = str(tmp_path / "collide.mp4")
        # Pre-create it as a real, valid-extension file so the collision check
        # (not the existence/extension checks) is what this test pins down.
        Path(output).touch()

        result = self._render_with_fake_plan(tmp_path, main_src, output, output)

        assert result["ok"] is False, (
            "output == PiP media_path must be rejected via check_output_not_source"
            f" (currently not applied to pip_sources at all): {result!r}"
        )
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED
        self._last_mock_run.assert_not_called()
