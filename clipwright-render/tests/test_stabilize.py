"""test_stabilize.py — Tests for the stabilize → vidstabtransform extension (render side).

All tests pass (Green). Covers:
  - build_plan(..., stabilize=stabilize_dict) — vidstabtransform injection (FR-3)
  - _validate_stabilize(stabilize: dict) -> _RenderStabilize | None
  - RenderPlan.stabilize_cwd — trf parent directory for run(cwd=...)
  - render.py F-4: run called with cwd=<trf parent dir> when stabilize enabled,
    cwd=None when disabled
  - Guard tests: trf_path boundary (PATH_NOT_ALLOWED), trf missing (FILE_NOT_FOUND),
    filtergraph-unsafe basename (INVALID_INPUT / CWE-78)

Architecture reference: architecture-report-20260618-222323.md §6 + §7-B
Requirements: FR-3 (render-side vidstabtransform application)
Flags: F-1 (ffmpeg fully mocked), F-4 (output absolutisation and cwd backward compat)
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.otio_utils import get_clipwright_metadata, set_clipwright_metadata

from clipwright_render.plan import KeptRange, ProbeInfo, build_plan, resolve_kept_ranges
from clipwright_render.schemas import RenderOptions

# ---------------------------------------------------------------------------
# Constants / shared fixtures
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


def _make_timeline(
    clips: list[otio.schema.Clip],
    stabilize_directive: dict[str, Any] | None = None,
) -> otio.schema.Timeline:
    """Build a single-video-track Timeline.

    If stabilize_directive is given it is written to timeline-level
    metadata["clipwright"]["stabilize"] via the canonical get/modify/set pattern.
    """
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    for clip in clips:
        track.append(clip)
    tl = otio.schema.Timeline()
    tl.tracks.append(track)
    if stabilize_directive is not None:
        existing = get_clipwright_metadata(tl)
        existing["stabilize"] = stabilize_directive
        set_clipwright_metadata(tl, existing)
    return tl


def _make_probe(
    has_video: bool = True,
    audio_count: int = 1,
    bit_rate: int | None = 8_000_000,
    width: int | None = 1920,
    height: int | None = 1080,
    fps: float | None = 30.0,
) -> ProbeInfo:
    return ProbeInfo(
        has_video=has_video,
        audio_count=audio_count,
        bit_rate=bit_rate,
        width=width,
        height=height,
        fps=fps,
    )


def _make_stabilize_dict(trf_path: str, smoothing: int = 30) -> dict[str, Any]:
    """Return a minimal stabilize directive dict matching StabilizeDirective schema."""
    return {
        "tool": "clipwright-stabilize",
        "version": "0.1.0",
        "kind": "stabilize",
        "trf_path": trf_path,
        "smoothing": smoothing,
        "severity": 0.42,
        "shakiness": 5,
        "accuracy": 15,
    }


def _single_source_plan(
    stabilize: dict[str, Any] | None = None,
    options: RenderOptions | None = None,
    source: str = "/src/a.mp4",
) -> Any:
    """Build a single-source RenderPlan with an optional stabilize directive."""
    tl = _make_timeline([_make_clip(source, 0.0, 5.0)])
    ranges = resolve_kept_ranges(tl)
    probe = _make_probe()
    return build_plan(  # type: ignore[call-arg]
        ranges,
        probe,
        options or RenderOptions(),
        stabilize=stabilize,
    )


def _multi_source_plan(
    stabilize: dict[str, Any] | None = None,
    options: RenderOptions | None = None,
) -> Any:
    """Build a multi-source RenderPlan with two different sources + optional stabilize."""
    clips = [
        _make_clip("/src/a.mp4", 0.0, 3.0),
        _make_clip("/src/b.mp4", 0.0, 2.0),
    ]
    tl = _make_timeline(clips)
    ranges = resolve_kept_ranges(tl)
    source_probes = {
        "/src/a.mp4": _make_probe(width=1920, height=1080, fps=30.0),
        "/src/b.mp4": _make_probe(width=1920, height=1080, fps=30.0),
    }
    probe_info = source_probes["/src/a.mp4"]
    return build_plan(  # type: ignore[call-arg]
        ranges,
        probe_info,
        options or RenderOptions(),
        stabilize=stabilize,
        source_probes=source_probes,
    )


# ===========================================================================
# ST-1: stabilize present — single-source path
# ===========================================================================


class TestStabilizePresentSingleSource:
    """build_plan(..., stabilize=...) injects vidstabtransform in single-source path (FR-3)."""

    def test_vidstabtransform_substring_in_filter_complex(self, tmp_path: Path) -> None:
        """filter_complex contains vidstabtransform=input=<basename>:smoothing=30."""
        trf_path = str(tmp_path / "video.stabilize.trf")
        Path(trf_path).touch()
        stabilize = _make_stabilize_dict(trf_path, smoothing=30)
        plan = _single_source_plan(stabilize=stabilize)
        fc = plan.filter_complex
        basename = Path(trf_path).name
        assert f"vidstabtransform=input={basename}:smoothing=30" in fc

    def test_vidstabtransform_uses_basename_not_absolute_path(
        self, tmp_path: Path
    ) -> None:
        """filter_complex uses the basename of trf_path, not the full absolute path (P-2/P-3)."""
        trf_path = str(tmp_path / "myvideo.stabilize.trf")
        Path(trf_path).touch()
        stabilize = _make_stabilize_dict(trf_path)
        plan = _single_source_plan(stabilize=stabilize)
        fc = plan.filter_complex
        # Full absolute path must not appear (only basename)
        assert str(tmp_path) not in fc
        # Basename must appear
        assert "myvideo.stabilize.trf" in fc

    def test_vidstabtransform_trim_before_vidstabtransform_before_setpts(
        self, tmp_path: Path
    ) -> None:
        """Order must be: trim=...,vidstabtransform=...,setpts=... (ADR-ST-1, FR-3-2).

        Architecture §6-F: vidstabtransform is inserted trim-directly-after and
        setpts-directly-before in the per-clip filter chain.
        """
        trf_path = str(tmp_path / "clip.stabilize.trf")
        Path(trf_path).touch()
        stabilize = _make_stabilize_dict(trf_path, smoothing=30)
        plan = _single_source_plan(stabilize=stabilize)
        fc = plan.filter_complex
        basename = Path(trf_path).name

        # Joined filter_complex string must contain the exact ordering
        vst_fragment = f"vidstabtransform=input={basename}:smoothing=30"
        assert "trim=" in fc
        assert vst_fragment in fc
        assert "setpts=" in fc

        # Check positional ordering: trim ... vst ... setpts in the string
        trim_pos = fc.find("trim=")
        vst_pos = fc.find(vst_fragment)
        setpts_pos = fc.find("setpts=")
        assert trim_pos != -1
        assert vst_pos != -1
        assert setpts_pos != -1
        assert trim_pos < vst_pos < setpts_pos, (
            f"Expected trim ({trim_pos}) < vidstabtransform ({vst_pos}) < setpts ({setpts_pos})"
        )

    def test_vidstabtransform_smoothing_value_reflected(self, tmp_path: Path) -> None:
        """smoothing value from the directive is present in filter_complex."""
        trf_path = str(tmp_path / "clip.stabilize.trf")
        Path(trf_path).touch()
        stabilize = _make_stabilize_dict(trf_path, smoothing=50)
        plan = _single_source_plan(stabilize=stabilize)
        fc = plan.filter_complex
        basename = Path(trf_path).name
        assert f"vidstabtransform=input={basename}:smoothing=50" in fc


# ===========================================================================
# ST-2: warp (speed change) present — insertion order must be preserved
# ===========================================================================


class TestStabilizeInsertionOrderWithWarp:
    """trim → vidstabtransform → setpts order holds regardless of warp (ADR-ST-1)."""

    def _plan_with_warp(self, trf_path: str) -> Any:
        """Build a plan where warp != identity is triggered.

        KeptRange.time_scalar != 1.0 causes build_plan to emit a speed-change
        setpts=(PTS-STARTPTS)/<s:g> rather than the identity setpts=PTS-STARTPTS.
        We inject time_scalar=2.0 (2x speed) to exercise the warp code path.
        """
        stabilize = _make_stabilize_dict(trf_path, smoothing=30)

        # time_scalar=2.0 → warp present → setpts=(PTS-STARTPTS)/2 branch
        kr = KeptRange(
            source="/src/a.mp4",
            source_range=_tr(0.0, 5.0),
            time_scalar=2.0,
        )
        probe = _make_probe()
        return build_plan(  # type: ignore[call-arg]
            [kr],
            probe,
            RenderOptions(),
            stabilize=stabilize,
        )

    def test_warp_present_trim_before_vst_before_setpts(self, tmp_path: Path) -> None:
        """With speed change (warp): trim → vidstabtransform → setpts order preserved."""
        trf_path = str(tmp_path / "clip.stabilize.trf")
        Path(trf_path).touch()
        plan = self._plan_with_warp(trf_path)
        fc = plan.filter_complex
        basename = Path(trf_path).name
        vst_fragment = f"vidstabtransform=input={basename}:smoothing=30"

        trim_pos = fc.find("trim=")
        vst_pos = fc.find(vst_fragment)
        setpts_pos = fc.find("setpts=")

        assert trim_pos != -1, "trim= not found in filter_complex"
        assert vst_pos != -1, f"{vst_fragment!r} not found in filter_complex"
        assert setpts_pos != -1, "setpts= not found in filter_complex"
        assert trim_pos < vst_pos < setpts_pos, (
            f"Expected trim ({trim_pos}) < vst ({vst_pos}) < setpts ({setpts_pos})"
        )

    def test_no_warp_trim_before_vst_before_setpts(self, tmp_path: Path) -> None:
        """Without speed change (identity warp): trim → vidstabtransform → setpts order preserved."""
        trf_path = str(tmp_path / "clip.stabilize.trf")
        Path(trf_path).touch()
        stabilize = _make_stabilize_dict(trf_path, smoothing=30)
        plan = _single_source_plan(stabilize=stabilize)
        fc = plan.filter_complex
        basename = Path(trf_path).name
        vst_fragment = f"vidstabtransform=input={basename}:smoothing=30"

        trim_pos = fc.find("trim=")
        vst_pos = fc.find(vst_fragment)
        setpts_pos = fc.find("setpts=")

        assert trim_pos != -1
        assert vst_pos != -1
        assert setpts_pos != -1
        assert trim_pos < vst_pos < setpts_pos


# ===========================================================================
# ST-3: RenderPlan.stabilize_cwd
# ===========================================================================


class TestStabilizeCwd:
    """RenderPlan.stabilize_cwd holds str(trf parent dir) when stabilize enabled (FR-3)."""

    def test_stabilize_cwd_equals_trf_parent_dir(self, tmp_path: Path) -> None:
        """stabilize_cwd == str(Path(trf_path).resolve().parent) (§6-E, F-4)."""
        trf_path = str(tmp_path / "clip.stabilize.trf")
        Path(trf_path).touch()
        stabilize = _make_stabilize_dict(trf_path)
        plan = _single_source_plan(stabilize=stabilize)
        expected_cwd = str(Path(trf_path).resolve().parent)
        assert plan.stabilize_cwd == expected_cwd

    def test_stabilize_cwd_is_str_type(self, tmp_path: Path) -> None:
        """stabilize_cwd is a str (not Path), matching run(cwd=...) expectations."""
        trf_path = str(tmp_path / "clip.stabilize.trf")
        Path(trf_path).touch()
        stabilize = _make_stabilize_dict(trf_path)
        plan = _single_source_plan(stabilize=stabilize)
        assert isinstance(plan.stabilize_cwd, str)


# ===========================================================================
# ST-4: backward compatibility (stabilize=None and key absent)
# ===========================================================================


class TestStabilizeBackwardCompat:
    """stabilize=None and missing key → no vidstabtransform; filter_complex unchanged (FR-3-6)."""

    def _baseline_plan(self) -> Any:
        """Build reference plan without stabilize (existing behaviour)."""
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe()
        return build_plan(ranges, probe, RenderOptions())

    def test_stabilize_none_no_vidstabtransform_substring(self) -> None:
        """stabilize=None → filter_complex does not contain 'vidstabtransform' substring."""
        plan = _single_source_plan(stabilize=None)
        assert "vidstabtransform" not in plan.filter_complex

    def test_stabilize_none_identical_to_baseline(self) -> None:
        """stabilize=None → filter_complex byte-identical to the no-stabilize baseline."""
        baseline = self._baseline_plan()
        with_none = _single_source_plan(stabilize=None)
        assert baseline.filter_complex == with_none.filter_complex

    def test_stabilize_none_stabilize_cwd_is_none(self) -> None:
        """stabilize=None → RenderPlan.stabilize_cwd is None (backward compat)."""
        plan = _single_source_plan(stabilize=None)
        assert plan.stabilize_cwd is None

    def test_no_stabilize_kwarg_no_vidstabtransform_substring(self) -> None:
        """Calling build_plan without stabilize kwarg → no vidstabtransform in filter_complex."""
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe()
        plan = build_plan(ranges, probe, RenderOptions())
        assert "vidstabtransform" not in plan.filter_complex

    def test_no_stabilize_kwarg_stabilize_cwd_is_none(self) -> None:
        """Calling build_plan without stabilize kwarg → RenderPlan.stabilize_cwd is None."""
        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = _make_probe()
        plan = build_plan(ranges, probe, RenderOptions())
        assert plan.stabilize_cwd is None


# ===========================================================================
# ST-5: multi-source + stabilize → UNSUPPORTED_OPERATION
# ===========================================================================


class TestStabilizeMultiSourceUnsupported:
    """multi-source + stabilize → UNSUPPORTED_OPERATION (ADR-ST-2, FR-3-5)."""

    def test_multi_source_with_stabilize_raises_unsupported(
        self, tmp_path: Path
    ) -> None:
        """2 sources + stabilize directive → ClipwrightError(UNSUPPORTED_OPERATION)."""
        trf_path = str(tmp_path / "clip.stabilize.trf")
        Path(trf_path).touch()
        stabilize = _make_stabilize_dict(trf_path)
        with pytest.raises(ClipwrightError) as exc_info:
            _multi_source_plan(stabilize=stabilize)
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION

    def test_multi_source_without_stabilize_does_not_raise(self) -> None:
        """2 sources without stabilize → no error (backward compat)."""
        plan = _multi_source_plan(stabilize=None)
        assert plan is not None
        assert "vidstabtransform" not in plan.filter_complex


# ===========================================================================
# ST-6: _validate_stabilize — validation rules
# ===========================================================================


class TestValidateStabilize:
    """_validate_stabilize rejects invalid values; trf_path None/absent → None (§6-D, CWE-20)."""

    def _validate(self, stabilize: dict[str, Any]) -> Any:
        """Call _validate_stabilize directly (internal function)."""
        from clipwright_render.plan import _validate_stabilize  # type: ignore[attr-defined]

        return _validate_stabilize(stabilize)

    # --- trf_path absent or None → None (backward compat) ---

    def test_trf_path_none_returns_none(self) -> None:
        """trf_path=None → _validate_stabilize returns None (no stabilization)."""
        result = self._validate({"trf_path": None, "smoothing": 30})
        assert result is None

    def test_trf_path_key_absent_returns_none(self) -> None:
        """No trf_path key → _validate_stabilize returns None (backward compat)."""
        result = self._validate({"smoothing": 30})
        assert result is None

    def test_empty_dict_returns_none(self) -> None:
        """Empty dict → _validate_stabilize returns None."""
        result = self._validate({})
        assert result is None

    # --- smoothing out of range → INVALID_INPUT ---

    def test_smoothing_minus_one_raises_invalid_input(self, tmp_path: Path) -> None:
        """smoothing=-1 (below ge=0) → INVALID_INPUT."""
        trf_path = str(tmp_path / "clip.trf")
        Path(trf_path).touch()
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate({"trf_path": trf_path, "smoothing": -1})
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_smoothing_1001_raises_invalid_input(self, tmp_path: Path) -> None:
        """smoothing=1001 (above le=1000) → INVALID_INPUT."""
        trf_path = str(tmp_path / "clip.trf")
        Path(trf_path).touch()
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate({"trf_path": trf_path, "smoothing": 1001})
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    # --- type errors → INVALID_INPUT ---

    def test_smoothing_string_raises_invalid_input(self, tmp_path: Path) -> None:
        """smoothing='bad' (wrong type) → INVALID_INPUT."""
        trf_path = str(tmp_path / "clip.trf")
        Path(trf_path).touch()
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate({"trf_path": trf_path, "smoothing": "bad"})
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_trf_path_integer_raises_invalid_input(self) -> None:
        """trf_path=123 (wrong type) → INVALID_INPUT."""
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate({"trf_path": 123, "smoothing": 30})
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    # --- CWE-209: error message must not contain input values (trf_path) ---

    def test_error_message_does_not_expose_trf_path(self, tmp_path: Path) -> None:
        """INVALID_INPUT message must not expose the trf_path value (CWE-209)."""
        trf_path = str(tmp_path / "clip.stabilize.trf")
        Path(trf_path).touch()
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate({"trf_path": trf_path, "smoothing": -1})
        assert exc_info.value.code == ErrorCode.INVALID_INPUT
        # trf_path string must not appear in message
        assert trf_path not in exc_info.value.message
        assert str(tmp_path) not in exc_info.value.message

    def test_error_cause_is_none(self, tmp_path: Path) -> None:
        """from None cuts __cause__ chain (CWE-209)."""
        trf_path = str(tmp_path / "clip.trf")
        Path(trf_path).touch()
        with pytest.raises(ClipwrightError) as exc_info:
            self._validate({"trf_path": trf_path, "smoothing": -1})
        assert exc_info.value.__cause__ is None

    # --- extra keys (severity/shakiness/accuracy etc.) are ignored (ADR-ST-5) ---

    def test_extra_keys_are_ignored(self, tmp_path: Path) -> None:
        """Extra keys (severity, shakiness, accuracy, tool, version, kind) do not raise."""
        trf_path = str(tmp_path / "clip.trf")
        Path(trf_path).touch()
        result = self._validate(
            {
                "trf_path": trf_path,
                "smoothing": 30,
                "severity": 0.42,
                "shakiness": 5,
                "accuracy": 15,
                "tool": "clipwright-stabilize",
                "version": "0.1.0",
                "kind": "stabilize",
            }
        )
        assert result is not None

    # --- valid input: trf_path and smoothing are accessible ---

    def test_valid_stabilize_dict_returns_non_none(self, tmp_path: Path) -> None:
        """Valid stabilize dict → returns a non-None _RenderStabilize."""
        trf_path = str(tmp_path / "clip.trf")
        Path(trf_path).touch()
        result = self._validate({"trf_path": trf_path, "smoothing": 30})
        assert result is not None

    def test_valid_stabilize_dict_trf_path_accessible(self, tmp_path: Path) -> None:
        """Valid stabilize dict → returned object has trf_path field."""
        trf_path = str(tmp_path / "clip.trf")
        Path(trf_path).touch()
        result = self._validate({"trf_path": trf_path, "smoothing": 30})
        assert result is not None
        assert result.trf_path == trf_path

    def test_valid_stabilize_dict_smoothing_accessible(self, tmp_path: Path) -> None:
        """Valid stabilize dict → returned object has smoothing field."""
        trf_path = str(tmp_path / "clip.trf")
        Path(trf_path).touch()
        result = self._validate({"trf_path": trf_path, "smoothing": 50})
        assert result is not None
        assert result.smoothing == 50

    def test_smoothing_boundary_zero_accepted(self, tmp_path: Path) -> None:
        """smoothing=0 (lower boundary, ge=0) is accepted."""
        trf_path = str(tmp_path / "clip.trf")
        Path(trf_path).touch()
        result = self._validate({"trf_path": trf_path, "smoothing": 0})
        assert result is not None

    def test_smoothing_boundary_1000_accepted(self, tmp_path: Path) -> None:
        """smoothing=1000 (upper boundary, le=1000) is accepted."""
        trf_path = str(tmp_path / "clip.trf")
        Path(trf_path).touch()
        result = self._validate({"trf_path": trf_path, "smoothing": 1000})
        assert result is not None


# ===========================================================================
# ST-7: F-4 — render.py cwd pass-through
# ===========================================================================


class TestRenderRunCwd:
    """render.py passes stabilize_cwd to run(cwd=...) when stabilize is enabled (F-4).

    These tests mock render.py-level run and build_plan to isolate the
    cwd forwarding logic. Pattern mirrors existing render.py run mock tests
    (test_render.py TestMultiSourceFfmpegInputOrder).
    """

    def _write_timeline_with_stabilize(
        self, tmp_path: Path, trf_path: str
    ) -> tuple[Path, Path]:
        """Write an OTIO timeline file annotated with a stabilize directive.

        Returns (tl_path, source_path).
        """
        source = tmp_path / "src.mp4"
        source.touch()
        stabilize = _make_stabilize_dict(trf_path)
        tl = _make_timeline([_make_clip(str(source), 0.0, 5.0)], stabilize)
        tl_path = tmp_path / "tl.otio"
        otio.adapters.write_to_file(tl, str(tl_path))
        return tl_path, source

    def _make_media_info(self, path: str) -> Any:
        from clipwright.schemas import MediaInfo, StreamInfo

        return MediaInfo(
            path=path,
            container="mov,mp4,m4a,3gp,3g2,mj2",
            duration=None,
            streams=[StreamInfo(index=0, codec_type="video", codec_name="h264")],
            bit_rate=8_000_000,
        )

    def test_run_called_with_stabilize_cwd_when_stabilize_present(
        self, tmp_path: Path
    ) -> None:
        """render.py calls run(cmd, ..., cwd=<trf parent dir>) when stabilize enabled (F-4)."""
        from clipwright_render.render import render_timeline

        trf_path = str(tmp_path / "clip.stabilize.trf")
        Path(trf_path).touch()
        tl_path, source = self._write_timeline_with_stabilize(tmp_path, trf_path)
        output = tmp_path / "out.mp4"
        output.touch()
        output_str = str(output)

        expected_cwd = str(Path(trf_path).resolve().parent)
        captured_kwargs: list[dict[str, Any]] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:  # type: ignore[type-arg]
            captured_kwargs.append(dict(kwargs))
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=lambda path: self._make_media_info(path),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output_str,
                options=RenderOptions(overwrite=True),
            )

        assert result["ok"] is True, f"failed: {result.get('error')}"
        # run must have been called with cwd=<trf parent dir>
        assert len(captured_kwargs) >= 1
        run_cwd = captured_kwargs[-1].get("cwd")
        assert run_cwd == expected_cwd, (
            f"Expected run cwd={expected_cwd!r}, got {run_cwd!r}"
        )

    def test_run_called_with_cwd_none_when_stabilize_absent(
        self, tmp_path: Path
    ) -> None:
        """render.py calls run with cwd=None (no cwd kwarg) when stabilize absent (F-4 backward compat)."""
        from clipwright_render.render import render_timeline

        source = tmp_path / "src.mp4"
        source.touch()
        tl = _make_timeline([_make_clip(str(source), 0.0, 5.0)])  # no stabilize
        tl_path = tmp_path / "tl.otio"
        otio.adapters.write_to_file(tl, str(tl_path))
        output = tmp_path / "out.mp4"
        output.touch()

        captured_kwargs: list[dict[str, Any]] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:  # type: ignore[type-arg]
            captured_kwargs.append(dict(kwargs))
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=lambda path: self._make_media_info(path),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=str(output),
                options=RenderOptions(overwrite=True),
            )

        assert result["ok"] is True, f"failed: {result.get('error')}"
        # cwd must be None (absent) when stabilize is not enabled
        assert len(captured_kwargs) >= 1
        run_cwd = captured_kwargs[-1].get("cwd")
        assert run_cwd is None, (
            f"Expected run cwd=None when stabilize absent, got {run_cwd!r}"
        )


# ===========================================================================
# ST-8: render guard tests — boundary / existence / basename validation
# (CR-E-001 / SR-V-002 / SR-INJ-002)
# ===========================================================================


class TestRenderStabilizeGuards:
    """render_timeline raises early errors for invalid trf_path before ffmpeg runs.

    Tests cover:
      - trf_path outside the timeline directory → PATH_NOT_ALLOWED (SR-V-002)
      - trf_path missing from disk → FILE_NOT_FOUND (CR-E-001)
      - basename with filtergraph special characters → INVALID_INPUT (SR-INJ-002)
      - INVALID_INPUT message must not expose the raw basename (CWE-209)
    """

    def _make_media_info(self, path: str) -> Any:
        from clipwright.schemas import MediaInfo, StreamInfo

        return MediaInfo(
            path=path,
            container="mov,mp4,m4a,3gp,3g2,mj2",
            duration=None,
            streams=[StreamInfo(index=0, codec_type="video", codec_name="h264")],
            bit_rate=8_000_000,
        )

    def _write_timeline(self, tmp_path: Path, trf_path: str) -> tuple[Path, Path]:
        """Write an OTIO timeline file with a stabilize directive pointing to trf_path."""
        source = tmp_path / "src.mp4"
        source.touch()
        stabilize = _make_stabilize_dict(trf_path)
        tl = _make_timeline([_make_clip(str(source), 0.0, 5.0)], stabilize)
        tl_path = tmp_path / "tl.otio"
        import opentimelineio as otio

        otio.adapters.write_to_file(tl, str(tl_path))
        return tl_path, source

    def _render_with_mocks(
        self, tl_path: Path, output_path: Path, *, overwrite: bool = True
    ) -> dict[str, Any]:
        """Call render_timeline with inspect_media and resolve_tool mocked."""
        from clipwright_render.render import render_timeline

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=lambda path: self._make_media_info(path),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render.run",
                return_value=__import__(
                    "subprocess", fromlist=["CompletedProcess"]
                ).CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            ),
        ):
            return render_timeline(  # type: ignore[return-value]
                timeline=str(tl_path),
                output=str(output_path),
                options=RenderOptions(overwrite=overwrite),
            )

    def test_trf_path_outside_timeline_dir_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """trf_path outside the timeline directory → PATH_NOT_ALLOWED (SR-V-002 / CWE-22)."""
        # Create a separate directory outside tmp_path
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        trf_path = str(outside_dir / "clip.stabilize.trf")
        Path(trf_path).touch()

        # Timeline lives in tmp_path / "proj"
        proj = tmp_path / "proj"
        proj.mkdir()
        source = proj / "src.mp4"
        source.touch()
        stabilize = _make_stabilize_dict(trf_path)
        tl = _make_timeline([_make_clip(str(source), 0.0, 5.0)], stabilize)
        tl_path = proj / "tl.otio"
        import opentimelineio as otio

        otio.adapters.write_to_file(tl, str(tl_path))
        output_path = proj / "out.mp4"
        output_path.touch()

        result = self._render_with_mocks(tl_path, output_path)

        assert result["ok"] is False
        assert result["error"]["code"] == "PATH_NOT_ALLOWED"

    def test_trf_path_missing_raises_file_not_found(self, tmp_path: Path) -> None:
        """trf_path not present on disk → FILE_NOT_FOUND before ffmpeg runs (CR-E-001)."""
        trf_path = str(tmp_path / "missing.stabilize.trf")
        # Do NOT create the file — it must be absent.

        tl_path, _ = self._write_timeline(tmp_path, trf_path)
        output_path = tmp_path / "out.mp4"
        output_path.touch()

        result = self._render_with_mocks(tl_path, output_path)

        assert result["ok"] is False
        assert result["error"]["code"] == "FILE_NOT_FOUND"

    def test_trf_not_found_message_contains_basename_only(self, tmp_path: Path) -> None:
        """FILE_NOT_FOUND message must use basename only, not the full path (CWE-209)."""
        trf_path = str(tmp_path / "secret.stabilize.trf")
        # Do NOT create the file.

        tl_path, _ = self._write_timeline(tmp_path, trf_path)
        output_path = tmp_path / "out.mp4"
        output_path.touch()

        result = self._render_with_mocks(tl_path, output_path)

        assert result["ok"] is False
        msg = result["error"]["message"]
        # Full path must not appear in message
        assert str(tmp_path) not in msg
        # Basename is acceptable to include for actionability
        assert "secret.stabilize.trf" in msg

    def test_basename_with_colon_raises_invalid_input(self, tmp_path: Path) -> None:
        """Basename containing ':' (filtergraph special char) → INVALID_INPUT (SR-INJ-002 / CWE-78)."""
        # Use a trf_path that is within the timeline dir but has a colon in the name.
        # On Windows colons are not valid in filenames, so we test via build_plan directly.
        from clipwright_render.plan import _validate_stabilize_basename  # type: ignore[attr-defined]

        with pytest.raises(ClipwrightError) as exc_info:
            _validate_stabilize_basename("clip:bad.stabilize.trf")
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_basename_with_semicolon_raises_invalid_input(self, tmp_path: Path) -> None:
        """Basename containing ';' → INVALID_INPUT (SR-INJ-002 / CWE-78)."""
        from clipwright_render.plan import _validate_stabilize_basename  # type: ignore[attr-defined]

        with pytest.raises(ClipwrightError) as exc_info:
            _validate_stabilize_basename("clip;inject.trf")
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_invalid_basename_message_does_not_expose_raw_input(
        self, tmp_path: Path
    ) -> None:
        """INVALID_INPUT message for unsafe basename must not expose the raw filename (CWE-209)."""
        from clipwright_render.plan import _validate_stabilize_basename  # type: ignore[attr-defined]

        unsafe = "clip:colon;semi[bracket].trf"
        with pytest.raises(ClipwrightError) as exc_info:
            _validate_stabilize_basename(unsafe)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT
        # Raw unsafe string must not appear in message
        assert unsafe not in exc_info.value.message
        assert ":" not in exc_info.value.message
        assert ";" not in exc_info.value.message

    def test_safe_basename_does_not_raise(self, tmp_path: Path) -> None:
        """Normal basename (alphanumeric + hyphen/underscore/dot) must not raise."""
        from clipwright_render.plan import _validate_stabilize_basename  # type: ignore[attr-defined]

        # Should not raise
        _validate_stabilize_basename("my-video_clip.stabilize.trf")
