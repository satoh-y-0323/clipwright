"""test_loudness.py — Tests for the loudness extension of clipwright-render (ADR-L5/L5b/L6).

Targets:
  - build_plan(ranges, probe_info, options, denoise=..., loudness=...) — loudnorm/peak injection
  - audio map terminal label accumulation chain (ADR-L5b / DC-AM-001)
  - render_timeline() — LoudnessDirective validation, get_clipwright_metadata read path

Design rationale (§3.3):
  - ADR-L5: apply denoise then loudness in order. Filter injection order: loudnorm appended after afftdn.
  - ADR-L5b: audio map terminal label resolved via cumulative pipe helper (DC-AM-001).
    [outa] -> (denoise -> [outa_dn]) -> (track loudness -> [outa_ln])
  - ADR-L6: no loudness directive -> identical to existing (backward compatible).
  - DC-AM-002: peak + denoise together -> warning (measurement timing mismatch).
  - invalid directive -> INVALID_INPUT (target out of range, invalid mode/scope, missing measured,
    inf/nan).
  - has_audio=False + loudness -> not injected into filter + warnings.
  - scale + loudness both specified: ffmpeg_args has both [outvscaled] and [outa_ln] maps.

probe is constructed directly as ProbeInfo and build_plan is called as pure logic.
System tests for render_timeline verify the write-to-timeline-metadata -> read path.
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
# Helpers: OTIO construction (same shape as test_denoise.py)
# ---------------------------------------------------------------------------

FPS = 30.0
# Dummy bit_rate for tests (not an assertion target; constant makes future MediaInfo schema
# changes easier to update)
_TEST_BIT_RATE = 8_000_000


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
    loudness_directive: dict[str, Any] | None = None,
    denoise_directive: dict[str, Any] | None = None,
) -> otio.schema.Timeline:
    """Build a Timeline with a single video track.

    If loudness_directive or denoise_directive is given, each is written to
    timeline-level metadata via set_clipwright_metadata.
    """
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    for clip in clips:
        track.append(clip)
    tl = otio.schema.Timeline()
    tl.tracks.append(track)

    from clipwright.otio_utils import get_clipwright_metadata

    meta: dict[str, Any] = get_clipwright_metadata(tl)
    if denoise_directive is not None:
        meta["denoise"] = denoise_directive
    if loudness_directive is not None:
        meta["loudness"] = loudness_directive
    set_clipwright_metadata(tl, meta)
    return tl


def _single_range(source: str = "/src/a.mp4") -> list[KeptRange]:
    """Return a KeptRange list with one segment."""
    from clipwright_render.plan import resolve_kept_ranges

    tl = _make_timeline([_make_clip(source, 0.0, 5.0)])
    return resolve_kept_ranges(tl)


# ---------------------------------------------------------------------------
# Test LoudnessDirective definitions
# ---------------------------------------------------------------------------

# loudnorm mode (with measured required for linear application)
_VALID_LOUDNORM_DIRECTIVE: dict[str, Any] = {
    "tool": "clipwright-loudness",
    "version": "0.1.0",
    "kind": "loudness",
    "mode": "loudnorm",
    "scope": "track",
    "target": {"i": -14.0, "tp": -1.0, "lra": 11.0},
    "measured": {
        "input_i": -20.73,
        "input_tp": -7.68,
        "input_lra": 0.10,
        "input_thresh": -30.73,
        "target_offset": 0.03,
    },
}

# peak mode
_VALID_PEAK_DIRECTIVE: dict[str, Any] = {
    "tool": "clipwright-loudness",
    "version": "0.1.0",
    "kind": "loudness",
    "mode": "peak",
    "scope": "track",
    "target": {"peak_db": -1.0},
    "measured": {"max_volume_db": -7.68},
}

# denoise (afftdn) directive (same shape as test_denoise.py)
_VALID_AFFTDN_DIRECTIVE: dict[str, Any] = {
    "tool": "clipwright-noise",
    "version": "0.1.0",
    "kind": "denoise",
    "backend": "afftdn",
    "params": {"nr": 12.0, "nf": -50.0, "nt": "w"},
}


# ---------------------------------------------------------------------------
# build_plan — loudnorm injection (has_audio=True)
# ---------------------------------------------------------------------------


class TestBuildPlanLoudnormWithAudio:
    """build_plan with loudness=loudnorm + has_audio=True injects loudnorm (ADR-L5)."""

    def test_loudnorm_present_in_filter_complex(self) -> None:
        """loudnorm filter string is present in filter_complex."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert "loudnorm" in plan.filter_complex

    def test_loudnorm_target_i_in_filter_complex(self) -> None:
        """I=-14 is present in the loudnorm parameters in filter_complex."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert "I=-14" in plan.filter_complex

    def test_loudnorm_target_tp_in_filter_complex(self) -> None:
        """TP=-1 is present in the loudnorm parameters in filter_complex."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert "TP=-1" in plan.filter_complex

    def test_loudnorm_linear_true_in_filter_complex(self) -> None:
        """linear=true is present in filter_complex (ADR-L5 two-pass requirement)."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert "linear=true" in plan.filter_complex

    def test_loudnorm_measured_i_in_filter_complex(self) -> None:
        """measured_I is present in filter_complex (core of linear two-pass application)."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert "measured_I=" in plan.filter_complex

    def test_loudnorm_measured_tp_in_filter_complex(self) -> None:
        """measured_TP is present in filter_complex."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert "measured_TP=" in plan.filter_complex

    def test_loudnorm_measured_lra_in_filter_complex(self) -> None:
        """measured_LRA is present in filter_complex."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert "measured_LRA=" in plan.filter_complex

    def test_loudnorm_measured_thresh_in_filter_complex(self) -> None:
        """measured_thresh is present in filter_complex."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert "measured_thresh=" in plan.filter_complex

    def test_outa_ln_label_in_filter_complex(self) -> None:
        """[outa_ln] label is present in filter_complex (loudnorm output after concat)."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert "[outa_ln]" in plan.filter_complex

    def test_audio_map_is_outa_ln(self) -> None:
        """The -map in ffmpeg_args is replaced with [outa_ln]."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa_ln]" in args_str
        assert "-map [outa]" not in args_str

    def test_loudnorm_position_after_concat(self) -> None:
        """loudnorm line appears after the concat line (ADR-L5 ordering)."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        fc = plan.filter_complex
        concat_pos = fc.index("concat=")
        loudnorm_pos = fc.index("loudnorm")
        assert loudnorm_pos > concat_pos, (
            f"loudnorm({loudnorm_pos}) must appear after concat({concat_pos})"
        )

    def test_filter_complex_is_single_string_with_loudnorm(self) -> None:
        """filter_complex is a single string even with a loudness directive (prevents command injection)."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert isinstance(plan.filter_complex, str)


# ---------------------------------------------------------------------------
# build_plan — peak injection
# ---------------------------------------------------------------------------


class TestBuildPlanPeakMode:
    """build_plan with loudness=peak injects a volume filter."""

    def test_volume_filter_present_in_filter_complex(self) -> None:
        """volume filter string is present in filter_complex."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_PEAK_DIRECTIVE
        )
        assert "volume=" in plan.filter_complex

    def test_peak_audio_map_replaced(self) -> None:
        """peak mode replaces the audio map with an appropriate label."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_PEAK_DIRECTIVE
        )
        args_str = " ".join(plan.ffmpeg_args)
        # [outa] must not remain unchanged (some label must replace it)
        assert "-map [outa]" not in args_str

    def test_peak_and_denoise_adds_warning(self) -> None:
        """peak + denoise together adds a warning (DC-AM-002)."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(),
            denoise=_VALID_AFFTDN_DIRECTIVE,
            loudness=_VALID_PEAK_DIRECTIVE,
        )
        warning_text = " ".join(plan.warnings)
        assert len(plan.warnings) > 0
        assert any(
            kw in warning_text
            for kw in ("peak", "denoise", "warning", "measured", "mismatch")
        )


# ---------------------------------------------------------------------------
# build_plan — denoise + loudnorm coexistence (ADR-L5/L5b)
# ---------------------------------------------------------------------------


class TestBuildPlanDenoiseAndLoudnorm:
    """Verify filter_complex chain and audio map terminal label for denoise + loudnorm coexistence (ADR-L5b / DC-AM-001)."""

    def test_afftdn_before_loudnorm_in_filter_complex(self) -> None:
        """afftdn appears before loudnorm (denoise -> loudnorm order / ADR-L5)."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(),
            denoise=_VALID_AFFTDN_DIRECTIVE,
            loudness=_VALID_LOUDNORM_DIRECTIVE,
        )
        fc = plan.filter_complex
        afftdn_pos = fc.index("afftdn")
        loudnorm_pos = fc.index("loudnorm")
        assert afftdn_pos < loudnorm_pos, (
            f"afftdn({afftdn_pos}) must appear before loudnorm({loudnorm_pos})"
        )

    def test_outa_dn_feeds_loudnorm_chain(self) -> None:
        """[outa_dn] appears as the input label for the loudnorm filter (cumulative chain ADR-L5b)."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(),
            denoise=_VALID_AFFTDN_DIRECTIVE,
            loudness=_VALID_LOUDNORM_DIRECTIVE,
        )
        fc = plan.filter_complex
        # [outa_dn]loudnorm or [outa_dn]...loudnorm form is expected
        assert "[outa_dn]" in fc
        # [outa_dn] must come before loudnorm (chain input)
        dn_pos = fc.index("[outa_dn]")
        ln_pos = fc.index("loudnorm")
        assert dn_pos < ln_pos, "[outa_dn] must appear before loudnorm (chain input)"

    def test_audio_map_terminal_is_outa_ln_when_both(self) -> None:
        """Audio map terminal is [outa_ln] when both denoise and loudnorm are active (ADR-L5b)."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(),
            denoise=_VALID_AFFTDN_DIRECTIVE,
            loudness=_VALID_LOUDNORM_DIRECTIVE,
        )
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa_ln]" in args_str
        assert "-map [outa]" not in args_str
        assert "-map [outa_dn]" not in args_str


# ---------------------------------------------------------------------------
# build_plan — exhaustive audio map terminal label coverage ({denoise} x {loudness} x {scale})
# ---------------------------------------------------------------------------


class TestAudioMapTerminalLabel:
    """Exhaustively verify terminal map labels for {denoise?} x {loudness?} x {scale?} combinations (ADR-L5b).

    denoise=True means afftdn, loudness=True means loudnorm, scale=True means width/height specified.
    """

    def _plan(
        self,
        denoise: bool = False,
        loudness: bool = False,
        scale: bool = False,
        audio_count: int = 1,
    ) -> Any:
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=audio_count, bit_rate=None)
        opts = RenderOptions(width=1280, height=720) if scale else RenderOptions()
        return build_plan(
            ranges,
            probe,
            opts,
            denoise=_VALID_AFFTDN_DIRECTIVE if denoise else None,
            loudness=_VALID_LOUDNORM_DIRECTIVE if loudness else None,
        )

    def test_no_denoise_no_loudness_map_outa(self) -> None:
        """no denoise, no loudness -> audio map is [outa] (backward compatible)."""
        plan = self._plan(denoise=False, loudness=False)
        args_str = " ".join(plan.ffmpeg_args)
        assert "-map [outa]" in args_str

    def test_denoise_only_map_outa_dn(self) -> None:
        """denoise only, no loudness -> audio map is [outa_dn]."""
        plan = self._plan(denoise=True, loudness=False)
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa_dn]" in args_str
        assert "-map [outa]" not in args_str

    def test_loudness_only_map_outa_ln(self) -> None:
        """no denoise, loudness only -> audio map is [outa_ln]."""
        plan = self._plan(denoise=False, loudness=True)
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa_ln]" in args_str
        assert "-map [outa]" not in args_str

    def test_denoise_and_loudness_map_outa_ln(self) -> None:
        """denoise and loudness both -> audio map is [outa_ln] (cumulative terminal)."""
        plan = self._plan(denoise=True, loudness=True)
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa_ln]" in args_str
        assert "-map [outa]" not in args_str
        assert "-map [outa_dn]" not in args_str

    def test_scale_and_loudness_has_outvscaled_and_outa_ln(self) -> None:
        """scale + loudness both specified -> ffmpeg_args contains both [outvscaled] and [outa_ln]."""
        plan = self._plan(denoise=False, loudness=True, scale=True)
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outvscaled]" in args_str, (
            "[outvscaled] required when scale is specified"
        )
        assert "[outa_ln]" in args_str, "[outa_ln] required when loudness is specified"

    def test_all_three_has_outvscaled_and_outa_ln(self) -> None:
        """denoise + scale + loudness all -> ffmpeg_args contains both [outvscaled] and [outa_ln]."""
        plan = self._plan(denoise=True, loudness=True, scale=True)
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outvscaled]" in args_str
        assert "[outa_ln]" in args_str
        assert "-map [outa]" not in args_str
        assert "-map [outa_dn]" not in args_str

    def test_no_audio_no_loudnorm_in_filter(self) -> None:
        """has_audio=False + loudness -> loudnorm is not injected into filter_complex."""
        plan = self._plan(denoise=False, loudness=True, audio_count=0)
        assert "loudnorm" not in plan.filter_complex

    def test_no_audio_loudness_adds_warning(self) -> None:
        """has_audio=False + loudness -> a warning is added."""
        plan = self._plan(denoise=False, loudness=True, audio_count=0)
        warning_text = " ".join(plan.warnings)
        assert len(plan.warnings) > 0
        assert any(kw in warning_text for kw in ("loudness", "audio", "skip"))


# ---------------------------------------------------------------------------
# build_plan — no loudness (backward compatible)
# ---------------------------------------------------------------------------


class TestBuildPlanLoudnessNone:
    """loudness=None is identical to existing logic (backward compatibility guarantee / ADR-L6)."""

    def test_no_loudnorm_without_loudness(self) -> None:
        """loudness=None: loudnorm is not present in filter_complex."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "loudnorm" not in plan.filter_complex

    def test_no_outa_ln_without_loudness(self) -> None:
        """loudness=None: [outa_ln] is not present in filter_complex or ffmpeg_args."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "[outa_ln]" not in plan.filter_complex
        assert "[outa_ln]" not in " ".join(plan.ffmpeg_args)

    def test_audio_map_is_outa_without_loudness(self) -> None:
        """loudness=None: audio map is [outa] when audio is present (backward compatible)."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa]" in args_str

    def test_explicit_none_same_as_omitted(self) -> None:
        """Explicitly passing loudness=None produces the same filter_complex as omitting it (ADR-L6)."""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan_omitted = build_plan(ranges, probe, RenderOptions())
        plan_explicit_none = build_plan(ranges, probe, RenderOptions(), loudness=None)
        assert plan_omitted.filter_complex == plan_explicit_none.filter_complex
        assert plan_omitted.ffmpeg_args == plan_explicit_none.ffmpeg_args


# ---------------------------------------------------------------------------
# build_plan — invalid LoudnessDirective -> INVALID_INPUT
# ---------------------------------------------------------------------------


class TestBuildPlanLoudnessInvalidDirective:
    """Invalid loudness directives raise INVALID_INPUT."""

    def test_target_i_out_of_range_raises_invalid_input(self) -> None:
        """target.i out of range (> -5) -> INVALID_INPUT."""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_LOUDNORM_DIRECTIVE,
            "target": {"i": -3.0, "tp": -1.0, "lra": 11.0},  # i > -5 is out of range
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_target_i_too_low_raises_invalid_input(self) -> None:
        """target.i out of range (< -70) -> INVALID_INPUT."""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_LOUDNORM_DIRECTIVE,
            "target": {"i": -75.0, "tp": -1.0, "lra": 11.0},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_target_tp_out_of_range_raises_invalid_input(self) -> None:
        """target.tp out of range (> 0) -> INVALID_INPUT."""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_LOUDNORM_DIRECTIVE,
            "target": {"i": -14.0, "tp": 1.0, "lra": 11.0},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_target_lra_out_of_range_raises_invalid_input(self) -> None:
        """target.lra out of range (> 50) -> INVALID_INPUT."""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_LOUDNORM_DIRECTIVE,
            "target": {"i": -14.0, "tp": -1.0, "lra": 55.0},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_invalid_mode_raises_invalid_input(self) -> None:
        """mode other than loudnorm/peak -> INVALID_INPUT."""
        from clipwright_render.plan import build_plan

        directive = {**_VALID_LOUDNORM_DIRECTIVE, "mode": "unknown_mode"}
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_invalid_scope_raises_invalid_input(self) -> None:
        """scope other than track (per_clip) -> INVALID_INPUT (per_clip out of scope)."""
        from clipwright_render.plan import build_plan

        directive = {**_VALID_LOUDNORM_DIRECTIVE, "scope": "per_clip"}
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_loudnorm_missing_measured_raises_invalid_input(self) -> None:
        """loudnorm with measured=None -> INVALID_INPUT (measured required for linear application)."""
        from clipwright_render.plan import build_plan

        directive = {**_VALID_LOUDNORM_DIRECTIVE, "measured": None}
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_measured_input_i_inf_raises_invalid_input(self) -> None:
        """measured.input_i=inf -> INVALID_INPUT (allow_inf_nan=False)."""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_LOUDNORM_DIRECTIVE,
            "measured": {
                **_VALID_LOUDNORM_DIRECTIVE["measured"],
                "input_i": float("inf"),
            },
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_measured_input_i_nan_raises_invalid_input(self) -> None:
        """measured.input_i=nan -> INVALID_INPUT (allow_inf_nan=False)."""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_LOUDNORM_DIRECTIVE,
            "measured": {
                **_VALID_LOUDNORM_DIRECTIVE["measured"],
                "input_i": float("nan"),
            },
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_peak_target_out_of_range_raises_invalid_input(self) -> None:
        """peak mode with peak_db > 0 -> INVALID_INPUT."""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_PEAK_DIRECTIVE,
            "target": {"peak_db": 3.0},  # > 0 is out of range
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_error_message_not_contain_sensitive_value(self) -> None:
        """Error message for an invalid directive does not expose input values (SR M-1)."""
        from clipwright_render.plan import build_plan

        directive = {**_VALID_LOUDNORM_DIRECTIVE, "mode": "INJECTED_SENSITIVE_VALUE"}
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert "INJECTED_SENSITIVE_VALUE" not in exc_info.value.message


# ---------------------------------------------------------------------------
# render_timeline — LoudnessDirective validation / get_clipwright_metadata read path
# ---------------------------------------------------------------------------


class TestRenderTimelineLoudnessDirective:
    """render_timeline reads LoudnessDirective from timeline metadata and passes it to build_plan."""

    def _write_timeline_with_loudness(
        self,
        tmp_path: Path,
        loudness_directive: dict[str, Any] | None,
        denoise_directive: dict[str, Any] | None = None,
        source_name: str = "source.mp4",
    ) -> tuple[Path, Path, Path]:
        """Write an OTIO file to tmp_path."""
        source_path = tmp_path / source_name
        source_path.write_bytes(b"fake")

        tl = _make_timeline(
            [_make_clip(str(source_path), 0.0, 5.0)],
            loudness_directive=loudness_directive,
            denoise_directive=denoise_directive,
        )
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        output_path = tmp_path / "out.mp4"
        return timeline_path, source_path, output_path

    def _fake_media_info(self, source_path: Path) -> Any:
        from clipwright.schemas import MediaInfo, StreamInfo

        return MediaInfo(
            path=str(source_path),
            container="mov,mp4,m4a,3gp,3g2,mj2",
            duration=None,
            streams=[
                StreamInfo(index=0, codec_type="video", codec_name="h264"),
                StreamInfo(index=1, codec_type="audio", codec_name="aac"),
            ],
            bit_rate=_TEST_BIT_RATE,
        )

    def test_render_reads_loudnorm_from_metadata_and_injects(
        self, tmp_path: Path
    ) -> None:
        """render_timeline reads loudness from timeline metadata and injects loudnorm into filter_complex."""
        from clipwright_render.render import render_timeline

        timeline_path, source_path, output_path = self._write_timeline_with_loudness(
            tmp_path, _VALID_LOUDNORM_DIRECTIVE
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=self._fake_media_info(source_path),
        ):
            result = render_timeline(
                str(timeline_path),
                str(output_path),
                RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"dry_run failed: {result}"
        fc = result["data"]["filter_complex"]
        assert "loudnorm" in fc, f"loudnorm not found in filter_complex: {fc}"
        assert "linear=true" in fc, f"linear=true not found in filter_complex: {fc}"

    def test_render_no_loudness_metadata_backward_compatible(
        self, tmp_path: Path
    ) -> None:
        """A timeline without loudness metadata is identical to existing logic (backward compatible)."""
        from clipwright_render.render import render_timeline

        timeline_path, source_path, output_path = self._write_timeline_with_loudness(
            tmp_path, None
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=self._fake_media_info(source_path),
        ):
            result = render_timeline(
                str(timeline_path),
                str(output_path),
                RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"backward compatibility test failed: {result}"
        fc = result["data"]["filter_complex"]
        assert "loudnorm" not in fc, f"loudnorm incorrectly present: {fc}"

    def test_render_invalid_loudness_directive_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """Invalid loudness directive (bad mode) -> ok=False / code=INVALID_INPUT."""
        from clipwright_render.render import render_timeline

        bad_directive = {**_VALID_LOUDNORM_DIRECTIVE, "mode": "bad_mode"}
        timeline_path, source_path, output_path = self._write_timeline_with_loudness(
            tmp_path, bad_directive
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=self._fake_media_info(source_path),
        ):
            result = render_timeline(
                str(timeline_path),
                str(output_path),
                RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value

    def test_render_loudness_scope_per_clip_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """scope=per_clip -> ok=False / code=INVALID_INPUT (per_clip out of scope)."""
        from clipwright_render.render import render_timeline

        bad_directive = {**_VALID_LOUDNORM_DIRECTIVE, "scope": "per_clip"}
        timeline_path, source_path, output_path = self._write_timeline_with_loudness(
            tmp_path, bad_directive
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=self._fake_media_info(source_path),
        ):
            result = render_timeline(
                str(timeline_path),
                str(output_path),
                RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value

    def test_render_denoise_and_loudnorm_both_present(self, tmp_path: Path) -> None:
        """denoise + loudnorm both specified: filter_complex contains both afftdn and loudnorm (ADR-L5)."""
        from clipwright_render.render import render_timeline

        timeline_path, source_path, output_path = self._write_timeline_with_loudness(
            tmp_path,
            loudness_directive=_VALID_LOUDNORM_DIRECTIVE,
            denoise_directive=_VALID_AFFTDN_DIRECTIVE,
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=self._fake_media_info(source_path),
        ):
            result = render_timeline(
                str(timeline_path),
                str(output_path),
                RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"dry_run failed: {result}"
        fc = result["data"]["filter_complex"]
        assert "afftdn" in fc, f"afftdn not found in filter_complex: {fc}"
        assert "loudnorm" in fc, f"loudnorm not found in filter_complex: {fc}"
