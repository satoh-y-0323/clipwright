"""test_denoise.py — Red tests for the denoise extension of clipwright-render (DC-AS-005/AS-006/GP-001 B-2).

Targets:
  - build_plan(ranges, probe_info, options, denoise=...) — afftdn injection, has_audio branching, scale coexistence
  - render_timeline() — DenoiseDirective validation, get_clipwright_metadata read path

Design rationale (architecture-report-20260611-090313 §3 / 20260611-092647 §B-2):
  - backend=afftdn + has_audio=True: inject afftdn after concat [outa] and replace map with [outa_dn]
  - backend=afftdn + has_audio=False: skip afftdn injection and add a warning about no audio
  - scale + afftdn both specified: filter_complex has both [outvscaled] and [outa_dn] maps (B-2)
  - backend=deepfilternet: UNSUPPORTED_OPERATION (with hint)
  - no denoise: identical to existing logic (backward compatible)
  - invalid directive: INVALID_INPUT (nr type/out of range, invalid nt, unknown backend, missing params)

probe is constructed directly as ProbeInfo and build_plan is called as pure logic.
System tests for render_timeline verify the write-to-timeline-metadata → read path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.otio_utils import set_clipwright_metadata

from clipwright_render.plan import KeptRange, ProbeInfo
from clipwright_render.schemas import RenderOptions

# ---------------------------------------------------------------------------
# Helpers: OTIO construction
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
    denoise_directive: dict[str, Any] | None = None,
) -> otio.schema.Timeline:
    """Build a Timeline with a single video track.

    If denoise_directive is given, it is written to timeline-level metadata via
    set_clipwright_metadata (CR L-5: otio_utils helper unified).
    """
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    for clip in clips:
        track.append(clip)
    tl = otio.schema.Timeline()
    tl.tracks.append(track)
    if denoise_directive is not None:
        from clipwright.otio_utils import get_clipwright_metadata

        existing = get_clipwright_metadata(tl)
        existing["denoise"] = denoise_directive
        set_clipwright_metadata(tl, existing)
    return tl


def _single_range(source: str = "/src/a.mp4") -> list[KeptRange]:
    """Return a KeptRange list with one segment."""
    from clipwright_render.plan import resolve_kept_ranges

    tl = _make_timeline([_make_clip(source, 0.0, 5.0)])
    return resolve_kept_ranges(tl)


# Valid afftdn denoise directive dict (common base for tests)
_VALID_AFFTDN_DIRECTIVE: dict[str, Any] = {
    "tool": "clipwright-noise",
    "version": "0.1.0",
    "kind": "denoise",
    "backend": "afftdn",
    "params": {"nr": 12.0, "nf": -50.0, "nt": "w"},
}

# deepfilternet directive (params is empty)
_VALID_DEEPFILTERNET_DIRECTIVE: dict[str, Any] = {
    "tool": "clipwright-noise",
    "version": "0.1.0",
    "kind": "denoise",
    "backend": "deepfilternet",
    "params": {},
}


# ---------------------------------------------------------------------------
# build_plan — afftdn injection (has_audio=True)
# ---------------------------------------------------------------------------


class TestBuildPlanDenoiseAfftdnWithAudio:
    """build_plan with denoise=afftdn + has_audio=True injects afftdn (DC-AS-005/B-2)."""

    def test_afftdn_present_in_filter_complex(self) -> None:
        """afftdn filter string is present in filter_complex."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), denoise=_VALID_AFFTDN_DIRECTIVE
        )
        assert "afftdn" in plan.filter_complex

    def test_afftdn_uses_nr_from_params(self) -> None:
        """The afftdn nr parameter matches params.nr."""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": 12.0, "nf": -50.0, "nt": "w"},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert "nr=12" in plan.filter_complex

    def test_afftdn_uses_nf_from_params(self) -> None:
        """The afftdn nf parameter matches params.nf."""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": 12.0, "nf": -50.0, "nt": "w"},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert "nf=-50" in plan.filter_complex

    def test_afftdn_uses_nt_from_params(self) -> None:
        """The afftdn nt parameter matches params.nt."""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": 12.0, "nf": -50.0, "nt": "w"},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert "nt=w" in plan.filter_complex

    def test_outa_dn_label_in_filter_complex(self) -> None:
        """[outa_dn] label is present in filter_complex (afftdn output after concat [outa])."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), denoise=_VALID_AFFTDN_DIRECTIVE
        )
        assert "[outa_dn]" in plan.filter_complex

    def test_audio_map_is_outa_dn(self) -> None:
        """The -map in ffmpeg_args is replaced with [outa_dn] (not left as [outa])."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), denoise=_VALID_AFFTDN_DIRECTIVE
        )
        args_str = " ".join(plan.ffmpeg_args)
        # [outa_dn] appears as the -map value
        assert "[outa_dn]" in args_str
        # raw [outa] must not remain as a -map value
        # ([outa] may appear as a label inside filter_complex, but only [outa_dn] in ffmpeg_args)
        assert "-map [outa_dn]" in args_str or (
            args_str.count("[outa_dn]") >= 1 and "-map [outa]" not in args_str
        )

    def test_afftdn_position_after_concat(self) -> None:
        """afftdn line appears after the concat line (B-2 ordering)."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), denoise=_VALID_AFFTDN_DIRECTIVE
        )
        fc = plan.filter_complex
        concat_pos = fc.index("concat=")
        afftdn_pos = fc.index("afftdn")
        assert afftdn_pos > concat_pos, (
            f"afftdn({afftdn_pos}) must appear after concat({concat_pos})"
        )

    def test_filter_complex_is_single_string(self) -> None:
        """filter_complex is a single string even with a denoise directive (prevents command injection)."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), denoise=_VALID_AFFTDN_DIRECTIVE
        )
        assert isinstance(plan.filter_complex, str)


# ---------------------------------------------------------------------------
# build_plan — afftdn + has_audio=False (DC-AS-005)
# ---------------------------------------------------------------------------


class TestBuildPlanDenoiseAfftdnNoAudio:
    """has_audio=False + denoise directive -> no afftdn injection + warnings (DC-AS-005)."""

    def test_afftdn_not_in_filter_complex_when_no_audio(self) -> None:
        """afftdn is not present in filter_complex when there is no audio."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), denoise=_VALID_AFFTDN_DIRECTIVE
        )
        assert "afftdn" not in plan.filter_complex

    def test_outa_dn_not_in_ffmpeg_args_when_no_audio(self) -> None:
        """[outa_dn] is not present in ffmpeg_args when there is no audio."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), denoise=_VALID_AFFTDN_DIRECTIVE
        )
        assert "[outa_dn]" not in " ".join(plan.ffmpeg_args)

    def test_warning_added_when_no_audio(self) -> None:
        """No audio + denoise directive -> a denoise-skip message is added to warnings."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), denoise=_VALID_AFFTDN_DIRECTIVE
        )
        assert len(plan.warnings) > 0
        warning_text = " ".join(plan.warnings)
        # Some text indicating denoise was skipped must be present
        assert any(kw in warning_text.lower() for kw in ("denoise", "skip", "no audio"))


# ---------------------------------------------------------------------------
# build_plan — scale + afftdn both specified (B-2)
# ---------------------------------------------------------------------------


class TestBuildPlanDenoiseWithScale:
    """scale + afftdn both specified: filter_complex has both [outvscaled] and [outa_dn] maps (B-2)."""

    def test_both_outvscaled_and_outa_dn_in_ffmpeg_args(self) -> None:
        """scale + afftdn: ffmpeg_args contains both [outvscaled] and [outa_dn]."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(width=1280, height=720),
            denoise=_VALID_AFFTDN_DIRECTIVE,
        )
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outvscaled]" in args_str, (
            "[outvscaled] is required in ffmpeg_args when scale is specified"
        )
        assert "[outa_dn]" in args_str, (
            "[outa_dn] is required in ffmpeg_args when afftdn is applied"
        )

    def test_scale_in_filter_complex_with_afftdn(self) -> None:
        """scale + afftdn: filter_complex contains both scale and afftdn."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(width=1280, height=720),
            denoise=_VALID_AFFTDN_DIRECTIVE,
        )
        assert "scale=1280:720" in plan.filter_complex
        assert "afftdn" in plan.filter_complex

    def test_no_vf_in_ffmpeg_args_with_afftdn_and_scale(self) -> None:
        """-vf is not present in ffmpeg_args (conflicts with filter_complex — forbidden)."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(width=1280, height=720),
            denoise=_VALID_AFFTDN_DIRECTIVE,
        )
        assert "-vf" not in plan.ffmpeg_args


# ---------------------------------------------------------------------------
# build_plan — backend=deepfilternet -> UNSUPPORTED_OPERATION
# ---------------------------------------------------------------------------


class TestBuildPlanDenoiseDeepfilternet:
    """backend=deepfilternet -> UNSUPPORTED_OPERATION (with hint)."""

    def test_deepfilternet_raises_unsupported(self) -> None:
        """deepfilternet -> ClipwrightError(UNSUPPORTED_OPERATION)."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(
                ranges, probe, RenderOptions(), denoise=_VALID_DEEPFILTERNET_DIRECTIVE
            )
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION

    def test_deepfilternet_error_has_hint(self) -> None:
        """The deepfilternet error includes a hint (indicating an alternative)."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(
                ranges, probe, RenderOptions(), denoise=_VALID_DEEPFILTERNET_DIRECTIVE
            )
        assert exc_info.value.hint, "hint must not be empty"
        # hint must point to a practical alternative (switch to afftdn or future version) (NR-L-3)
        assert (
            "afftdn" in exc_info.value.hint or "future" in exc_info.value.hint.lower()
        )


# ---------------------------------------------------------------------------
# build_plan — denoise=None (backward compatible)
# ---------------------------------------------------------------------------


class TestBuildPlanDenoiseNone:
    """denoise=None is identical to existing logic (backward compatibility guarantee)."""

    def test_no_afftdn_without_denoise(self) -> None:
        """denoise=None: afftdn is not present in filter_complex."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        # call without denoise argument (existing interface)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "afftdn" not in plan.filter_complex

    def test_no_outa_dn_without_denoise(self) -> None:
        """denoise=None: [outa_dn] is not present in filter_complex or ffmpeg_args."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "[outa_dn]" not in plan.filter_complex
        assert "[outa_dn]" not in " ".join(plan.ffmpeg_args)

    def test_audio_map_is_outa_without_denoise(self) -> None:
        """denoise=None: audio map is [outa] when audio is present (backward compatible)."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa]" in args_str

    def test_explicit_none_denoise_same_as_omitted(self) -> None:
        """Explicitly passing denoise=None produces the same filter_complex as omitting it."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan_omitted = build_plan(ranges, probe, RenderOptions())
        plan_explicit_none = build_plan(ranges, probe, RenderOptions(), denoise=None)
        assert plan_omitted.filter_complex == plan_explicit_none.filter_complex
        assert plan_omitted.ffmpeg_args == plan_explicit_none.ffmpeg_args


# ---------------------------------------------------------------------------
# build_plan — invalid denoise directive -> INVALID_INPUT (DC-AS-006)
# ---------------------------------------------------------------------------


class TestBuildPlanDenoiseInvalidDirective:
    """Invalid denoise directives raise INVALID_INPUT (DC-AS-006 strict validation)."""

    def test_nr_as_string_raises_invalid_input(self) -> None:
        """params.nr as a string -> INVALID_INPUT."""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": "bad", "nf": -50.0, "nt": "w"},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_nr_out_of_range_raises_invalid_input(self) -> None:
        """params.nr out of range (>97) -> INVALID_INPUT (AfftdnParams ge=0.01 le=97)."""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": 100.0, "nf": -50.0, "nt": "w"},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_nr_zero_raises_invalid_input(self) -> None:
        """params.nr=0.0 (below ge=0.01) -> INVALID_INPUT."""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": 0.0, "nf": -50.0, "nt": "w"},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_nt_invalid_value_raises_invalid_input(self) -> None:
        """params.nt other than Literal['w','v'] -> INVALID_INPUT."""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": 12.0, "nf": -50.0, "nt": "x"},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_unknown_backend_raises_invalid_input(self) -> None:
        """Unknown backend -> INVALID_INPUT (Literal validation failure)."""
        from clipwright_render.plan import build_plan

        directive = {**_VALID_AFFTDN_DIRECTIVE, "backend": "unknown_backend"}
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_missing_params_raises_invalid_input(self) -> None:
        """Missing params key -> INVALID_INPUT."""
        from clipwright_render.plan import build_plan

        directive = {
            "tool": "clipwright-noise",
            "version": "0.1.0",
            "kind": "denoise",
            "backend": "afftdn",
            # params field absent
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_nf_out_of_range_raises_invalid_input(self) -> None:
        """params.nf out of range (>-20) -> INVALID_INPUT (AfftdnParams ge=-80 le=-20)."""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": 12.0, "nf": -10.0, "nt": "w"},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_measured_noise_floor_inf_raises_invalid_input(self) -> None:
        """measured_noise_floor_db=inf -> INVALID_INPUT (SR L-3: inf/nan rejected)."""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "measured_noise_floor_db": float("inf"),
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_measured_noise_floor_nan_raises_invalid_input(self) -> None:
        """measured_noise_floor_db=nan -> INVALID_INPUT (SR L-3: inf/nan rejected)."""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "measured_noise_floor_db": float("nan"),
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_error_message_does_not_contain_exc_detail(self) -> None:
        """Error message for an invalid directive does not expose ValidationError details (SR M-1)."""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": "INJECTED_SENSITIVE_VALUE", "nf": -50.0, "nt": "w"},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        # Input value must not leak into the error message
        assert "INJECTED_SENSITIVE_VALUE" not in exc_info.value.message
        # Exception chain is severed (from None)
        assert exc_info.value.__cause__ is None


# ---------------------------------------------------------------------------
# render_timeline — DenoiseDirective validation / get_clipwright_metadata read path
# ---------------------------------------------------------------------------


class TestRenderTimelineDenoiseDirective:
    """render_timeline reads DenoiseDirective from timeline metadata and passes it to build_plan."""

    def _write_timeline_with_denoise(
        self,
        tmp_path: Path,
        denoise_directive: dict[str, Any] | None,
        source_name: str = "source.mp4",
    ) -> tuple[Path, Path, Path]:
        """Write an OTIO file to tmp_path.

        Returns:
            Tuple of (timeline_path, source_path, output_path).
        """
        source_path = tmp_path / source_name
        source_path.write_bytes(b"fake")  # pass the file-existence check

        tl = _make_timeline(
            [_make_clip(str(source_path), 0.0, 5.0)],
            denoise_directive=denoise_directive,
        )
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        output_path = tmp_path / "out.mp4"
        return timeline_path, source_path, output_path

    def test_render_reads_denoise_from_metadata_and_passes_to_build_plan(
        self, tmp_path: Path
    ) -> None:
        """render_timeline reads denoise from timeline metadata and passes it to build_plan.

        Verifies via dry_run that afftdn is in filter_complex when denoise=afftdn is
        received (real ffmpeg is not called).
        """
        from clipwright.schemas import MediaInfo, StreamInfo

        from clipwright_render.render import render_timeline

        timeline_path, source_path, output_path = self._write_timeline_with_denoise(
            tmp_path, _VALID_AFFTDN_DIRECTIVE
        )

        fake_info = MediaInfo(
            path=str(source_path),
            container="mov,mp4,m4a,3gp,3g2,mj2",
            duration=None,
            streams=[
                StreamInfo(index=0, codec_type="video", codec_name="h264"),
                StreamInfo(index=1, codec_type="audio", codec_name="aac"),
            ],
            bit_rate=8_000_000,
        )

        with patch("clipwright_render.render.inspect_media", return_value=fake_info):
            result = render_timeline(
                str(timeline_path),
                str(output_path),
                RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"dry_run failed: {result}"
        fc = result["data"]["filter_complex"]
        assert "afftdn" in fc, f"afftdn not found in filter_complex: {fc}"

    def test_render_no_denoise_metadata_backward_compatible(
        self, tmp_path: Path
    ) -> None:
        """A timeline without denoise metadata is identical to existing logic (backward compatible)."""
        from clipwright.schemas import MediaInfo, StreamInfo

        from clipwright_render.render import render_timeline

        timeline_path, source_path, output_path = self._write_timeline_with_denoise(
            tmp_path,
            None,  # no denoise
        )

        fake_info = MediaInfo(
            path=str(source_path),
            container="mov,mp4,m4a,3gp,3g2,mj2",
            duration=None,
            streams=[
                StreamInfo(index=0, codec_type="video", codec_name="h264"),
                StreamInfo(index=1, codec_type="audio", codec_name="aac"),
            ],
            bit_rate=8_000_000,
        )

        with patch("clipwright_render.render.inspect_media", return_value=fake_info):
            result = render_timeline(
                str(timeline_path),
                str(output_path),
                RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"backward compatibility test failed: {result}"
        fc = result["data"]["filter_complex"]
        assert "afftdn" not in fc, f"afftdn incorrectly present: {fc}"

    def test_render_invalid_denoise_directive_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """Invalid denoise directive (nr as string) -> ok=False / code=INVALID_INPUT."""
        from clipwright.schemas import MediaInfo, StreamInfo

        from clipwright_render.render import render_timeline

        bad_directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": "bad", "nf": -50.0, "nt": "w"},
        }
        timeline_path, source_path, output_path = self._write_timeline_with_denoise(
            tmp_path, bad_directive
        )

        fake_info = MediaInfo(
            path=str(source_path),
            container="mov,mp4,m4a,3gp,3g2,mj2",
            duration=None,
            streams=[
                StreamInfo(index=0, codec_type="video", codec_name="h264"),
                StreamInfo(index=1, codec_type="audio", codec_name="aac"),
            ],
            bit_rate=8_000_000,
        )

        with patch("clipwright_render.render.inspect_media", return_value=fake_info):
            result = render_timeline(
                str(timeline_path),
                str(output_path),
                RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value

    def test_render_deepfilternet_directive_returns_unsupported(
        self, tmp_path: Path
    ) -> None:
        """deepfilternet directive -> ok=False / code=UNSUPPORTED_OPERATION."""
        from clipwright.schemas import MediaInfo, StreamInfo

        from clipwright_render.render import render_timeline

        timeline_path, source_path, output_path = self._write_timeline_with_denoise(
            tmp_path, _VALID_DEEPFILTERNET_DIRECTIVE
        )

        fake_info = MediaInfo(
            path=str(source_path),
            container="mov,mp4,m4a,3gp,3g2,mj2",
            duration=None,
            streams=[
                StreamInfo(index=0, codec_type="video", codec_name="h264"),
                StreamInfo(index=1, codec_type="audio", codec_name="aac"),
            ],
            bit_rate=8_000_000,
        )

        with patch("clipwright_render.render.inspect_media", return_value=fake_info):
            result = render_timeline(
                str(timeline_path),
                str(output_path),
                RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.UNSUPPORTED_OPERATION.value
