"""test_pip_ducking_integration.py — Red-phase tests for F-1 / F-2
(code-review-report-pip.md).

F-1 ([CR-NEW], High): `_marker_to_pip_overlay` (plan.py) reads
metadata["clipwright"]["ducking"] as `ducking: Any = cw.get("ducking")` and
stores the raw dict/AnyDictionary directly on `PipOverlay.ducking` WITHOUT
converting it to a `PipDuckingDirective` (unlike every other field on
`_marker_to_pip_overlay`, which re-validates). `PipOverlay.ducking`'s type
annotation is `PipDuckingDirective | None`, and `_append_pip_audio_pipe`
accesses `pip.ducking.enabled` assuming that type. Since
`clipwright-overlay`'s `_add_pip_inner` always writes a `ducking` dict
(even when `mix_audio=False` / `ducking.enabled=False`), `cw.get("ducking")`
is always a non-None raw mapping, so `pip.ducking.enabled` always raises
`AttributeError` the first time a `mix_audio=True` PiP reaches
`_append_pip_audio_pipe` — reproduced directly in the security/code review
reports (`AttributeError: 'opentimelineio._otio.AnyDictionary' object has no
attribute 'enabled'`).

F-2 ([CR-T-001], Medium): there was no test exercising the REAL
`PipOverlay` / `_marker_to_pip_overlay` / `_collect_pip_overlays` path with
`mix_audio=True` + `ducking.enabled=True` all the way through `build_plan()`
— test_pip_audio.py only ever used `_FakePipOverlay` (a duck-typed stand-in
that already carries a proper `PipDuckingDirective`, so it never exercises
the dict->Directive conversion at all). This file adds that missing
integration coverage (combined with F-1 per the review's suggestion).

These tests use REAL OTIO markers built the same way
clipwright-overlay's clipwright_add_pip actually writes them (mirrors
test_pip_video.py's `_add_pip_overlay_marker` helper — no cross-file import).
"""

from __future__ import annotations

from typing import Any

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode

from clipwright_render.schemas import RenderOptions

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


def _make_pip_marker(
    *,
    media_path: str = "clips/pip.mp4",
    start_sec: float = 2.0,
    duration_sec: float = 4.0,
    mix_audio: bool = True,
    audio_volume: float = 1.0,
    ducking: dict[str, Any] | None = None,
    name: str = "pip_0",
) -> otio.schema.Marker:
    """Build a standalone OTIO Marker shaped exactly like the metadata dict
    clipwright-overlay's _add_pip_inner writes (matches
    test_pip_video.py::_add_pip_overlay_marker's metadata shape)."""
    if ducking is None:
        ducking = {"enabled": False, "threshold": 0.05, "ratio": 4.0}
    marked_range = _tr(start_sec, duration_sec)
    return otio.schema.Marker(
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
                "media_start_sec": 0.0,
                "x": "(W-w)/2",
                "y": "(H-h)/2",
                "scale": 0.3,
                "opacity": 1.0,
                "fade_in_sec": 0.0,
                "fade_out_sec": 0.0,
                "mix_audio": mix_audio,
                "audio_volume": audio_volume,
                "ducking": ducking,
            }
        },
    )


def _add_pip_overlay_marker(
    timeline: otio.schema.Timeline, marker: otio.schema.Marker
) -> None:
    video_track: otio.schema.Track | None = None
    for track in timeline.tracks:
        if track.kind == otio.schema.TrackKind.Video:
            video_track = track
            break
    assert video_track is not None, "timeline must have a video track"
    video_track.markers.append(marker)


# ===========================================================================
# F-1: _marker_to_pip_overlay must convert the raw ducking dict/AnyDictionary
# into a validated PipDuckingDirective (not pass it through untouched).
# ===========================================================================


class TestMarkerToPipOverlayDuckingConversion:
    """F-1 ([CR-NEW]): ducking must be a real PipDuckingDirective instance,
    never a raw dict / OTIO AnyDictionary, regardless of enabled=True/False."""

    def test_ducking_enabled_true_converted_to_directive(self) -> None:
        from clipwright_render.plan import PipDuckingDirective, _marker_to_pip_overlay

        marker = _make_pip_marker(
            mix_audio=True,
            ducking={"enabled": True, "threshold": 0.3, "ratio": 6.0},
        )
        result = _marker_to_pip_overlay(marker, timeline_path=None, input_index=1)

        assert isinstance(result.ducking, PipDuckingDirective), (
            "PipOverlay.ducking must be a PipDuckingDirective instance, not a raw"
            f" dict/AnyDictionary (F-1): got {type(result.ducking)!r}"
        )
        assert result.ducking.enabled is True
        assert result.ducking.threshold == 0.3
        assert result.ducking.ratio == 6.0

    def test_ducking_enabled_false_still_converted_to_directive_type(self) -> None:
        """Reproduces the exact repro from security-review-report-pip.md /
        code-review-report-pip.md F-1: the type mismatch occurs even when
        ducking.enabled=False (the AddPipOptions default), because
        clipwright-overlay always writes a ducking dict regardless of value."""
        from clipwright_render.plan import PipDuckingDirective, _marker_to_pip_overlay

        marker = _make_pip_marker(
            mix_audio=True,
            ducking={"enabled": False, "threshold": 0.05, "ratio": 4.0},
        )
        result = _marker_to_pip_overlay(marker, timeline_path=None, input_index=1)

        assert isinstance(result.ducking, PipDuckingDirective), (
            "ducking must be converted to PipDuckingDirective even when"
            f" enabled=False: got {type(result.ducking)!r}"
        )
        assert result.ducking.enabled is False

    def test_ducking_absent_key_stays_none(self) -> None:
        """Backward-compat regression guard: when the marker has no 'ducking'
        key at all, PipOverlay.ducking must remain None (not become some
        default-constructed Directive). This is NOT part of the F-1 bug and
        is expected to already pass."""
        from clipwright_render.plan import _marker_to_pip_overlay

        marker = _make_pip_marker(mix_audio=False)
        # Remove the ducking key entirely to simulate metadata predating
        # ADR-PIP-9, or a hand-crafted marker without it.
        del marker.metadata["clipwright"]["ducking"]

        result = _marker_to_pip_overlay(marker, timeline_path=None, input_index=1)
        assert result.ducking is None

    def test_invalid_ducking_threshold_raises_invalid_input_not_attribute_error(
        self,
    ) -> None:
        """An out-of-range ducking.threshold (<=0, per PipDuckingDirective's
        Field(gt=0.0, le=1.0)) must surface as ClipwrightError(INVALID_INPUT)
        at marker-conversion time — not silently pass through and later crash
        deep inside _append_pip_audio_pipe with AttributeError."""
        from clipwright_render.plan import _marker_to_pip_overlay

        marker = _make_pip_marker(
            mix_audio=True,
            ducking={"enabled": True, "threshold": -5.0, "ratio": 4.0},
        )

        with pytest.raises(ClipwrightError) as exc_info:
            _marker_to_pip_overlay(marker, timeline_path=None, input_index=1)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_invalid_ducking_ratio_raises_invalid_input(self) -> None:
        """ratio below PipDuckingDirective's Field(ge=1.0) lower bound must
        raise INVALID_INPUT, not be silently accepted."""
        from clipwright_render.plan import _marker_to_pip_overlay

        marker = _make_pip_marker(
            mix_audio=True,
            ducking={"enabled": True, "threshold": 0.3, "ratio": 0.5},
        )

        with pytest.raises(ClipwrightError) as exc_info:
            _marker_to_pip_overlay(marker, timeline_path=None, input_index=1)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT


# ===========================================================================
# F-2: mix_audio=True + ducking.enabled=True integration path (OTIO marker ->
# build_plan) must produce sidechaincompress, exercised through the REAL
# PipOverlay/_marker_to_pip_overlay/_collect_pip_overlays path (not
# _FakePipOverlay).
# ===========================================================================


class TestMixAudioDuckingIntegrationViaBuildPlan:
    """F-2 ([CR-T-001]): build_plan() must correctly wire a real pip_overlay
    marker's ducking metadata into a sidechaincompress filter stage, without
    crashing on the F-1 type-conversion bug."""

    def test_mix_audio_and_ducking_enabled_produces_sidechaincompress(self) -> None:
        from clipwright_render.plan import ProbeInfo, build_plan, resolve_kept_ranges

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        marker = _make_pip_marker(
            media_path="clips/pip.mp4",
            mix_audio=True,
            ducking={"enabled": True, "threshold": 0.3, "ratio": 6.0},
        )
        _add_pip_overlay_marker(tl, marker)

        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)

        plan = build_plan(ranges, probe, RenderOptions())

        assert "sidechaincompress=threshold=0.3:ratio=6" in plan.filter_complex, (
            "mix_audio=True + ducking.enabled=True must produce a"
            f" sidechaincompress stage in filter_complex: {plan.filter_complex!r}"
        )

    def test_mix_audio_true_ducking_disabled_does_not_crash(self) -> None:
        """Even ducking.enabled=False (the default) must not crash build_plan
        when mix_audio=True — this is the exact reproduction scenario from
        security-review-report-pip.md ("enabled=False でも再現する")."""
        from clipwright_render.plan import ProbeInfo, build_plan, resolve_kept_ranges

        tl = _make_timeline([_make_clip("/src/a.mp4", 0.0, 10.0)])
        marker = _make_pip_marker(
            media_path="clips/pip.mp4",
            mix_audio=True,
            ducking={"enabled": False, "threshold": 0.05, "ratio": 4.0},
        )
        _add_pip_overlay_marker(tl, marker)

        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)

        # Must not raise AttributeError (F-1 bug reproduction).
        plan = build_plan(ranges, probe, RenderOptions())
        assert "sidechaincompress" not in plan.filter_complex
