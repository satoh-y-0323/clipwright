"""test_timeline_export.py — Tests for timeline_export.py (clipwright_export_timeline).

Target functions:
  - export_timeline(timeline, output, options) -> ToolResult   (boundary)
  - _loss_report(tl) -> list[str]                              (raising, pure)

Spec source of truth:
  - spike-report-export-adapters.md (Wave 0 measured facts; §A-§E).
  - architecture-report-20260710-161944.md §2/§4/§5/§9.1/§9.2/§13
    (§13 ADR-EX-10/ADR-EX-11 overrides §8.3 wherever they disagree).
  - requirements-report-20260710-161944.md FR-2, AC-1/AC-2/AC-3/AC-4/AC-5/AC-12.

History: at authoring time clipwright_export.timeline_export did not exist yet
(only schemas.py had landed), so every test in this module initially failed
at collection with ModuleNotFoundError (the expected TDD Red state). That Red
phase has since been resolved by the implementation; the classes below now
exercise the implemented functions, plus follow-up regression/verification
tests added from code-review and security-review findings (see the (H)/(I)/
(J)/(K)/(L) sections below, each tagged with its finding ID).

Verification aspects:
  (A) Round-trip in/out (AC-1/AC-2, architecture §13.4 items 1-4)
      (A-1) EDL: re-read with adapter_name="cmx_3600" and rate=<source rate>
            explicit (spike §(4b)/§C-2 — unspecified rate defaults to 24fps
            and drifts). in/out match within 1/rate seconds, compared in
            seconds (not RationalTime value/rate — EDL renormalises rate to
            24 on re-read; spike §(3)).
      (A-2) FCPXML: re-read with adapter_name="fcpx_xml" (no rate needed;
            rate is preserved). Same seconds-based, 1/rate-tolerant compare.
      (A-3) Parametrized over integer rates 24/25/30 (spike §D-3: fixtures
            must use integer rates; non-integer is a separate rejection
            case, not a round-trip case).
      (A-4) Sub-second (but frame-aligned) source ranges pass write-then-
            verify without a false-fail (§13.2 false-positive safety net;
            spike §(4b)).
  (B) Media absolutization (AC-3, architecture §4)
      (B-1) FCPXML output: every clip's re-read media_reference.target_url
            is absolute and resolves to the real on-disk dummy media file.
      (B-2) The *source* OTIO file's bytes and its clip target_urls are
            byte-identical/relative before and after export (non-destructive).
      (B-3) A clip referencing a relative target_url that does not exist on
            disk is skipped (kept relative) with a "could not be resolved"
            warning; export still succeeds (§4.2 ADR-EX-4 best-effort).
      (B-4) A clip whose relative target_url resolves outside the OTIO
            directory (CWE-22 boundary violation) fails the *whole* export
            with PATH_NOT_ALLOWED and writes no output file (§4.2 exception
            case — not best-effort).
  (C) Loss report (AC-4, architecture §5.1)
      (C-1) _loss_report(tl) direct call: every known kind (caption,
            text_overlay, image_overlay, pip_overlay, bgm, color, denoise,
            loudness, stabilize, speed, transition) appears with its table
            label.
      (C-2) scene_boundary is excluded from the report entirely (position
            is transcribed by both adapters; spike §7b).
      (C-3) The unrecognised marker kind is grouped under the ADR-EX-5
            generic "other clipwright annotations" bucket, naming the kind.
      (C-4) A clean timeline (no clipwright annotations) yields [] (empty
            list) — no aggregate warning is emitted downstream.
      (C-5) export_timeline surfaces the loss report content in its
            `warnings` list (integration, not just the pure helper).
  (D) Non-integer frame rate rejection (ADR-EX-10, architecture §13.1/§13.4-5)
      (D-1) 23.976 and 29.97, both edl and fcpxml: INVALID_INPUT, no output
            file is ever created (write-before-check is prohibited).
      (D-2) FCPXML 29.97 (which spike §(4) showed *could* be lossily
            rescued by rounding to 29) is rejected the same as the others —
            no partial rescue.
  (E) Write-then-verify failure (ADR-EX-11, architecture §13.2/§13.4-7)
      (E-1) Forcing the post-write re-read to raise (only for the verify
            call, which always passes adapter_name= explicitly; the
            *input*-load call to otio.adapters.read_from_file does not)
            yields OTIO_ERROR, and the written artifact is deleted.
      (E-2) The error message/hint do not contain the input or output file
            basenames (CWE-209).
  (F) EDL-specific behaviour (spike §(2)/§(7), architecture §13.3)
      (F-1) V1+A1 input: EDL contains exactly one event line per V1 clip
            (never per A1 clip) plus a "video cuts only" / audio-dropped
            warning.
      (F-2) Two video tracks: NotSupportedError (OTIOError subclass) is
            caught and converted to OTIO_ERROR; no output file remains.
      (F-3) A warning naming the representative rate is present (rate-
            explicit-import-required warning, spike §C-2).
  (G) Error taxonomy (AC-5, FR-2, AC-12)
      (G-1) output == timeline: PATH_NOT_ALLOWED (check_output_not_source;
            same real-world precedent as clipwright-speed, not the
            INVALID_INPUT wording in the requirements prose — see inline
            comment).
      (G-2) Nonexistent timeline path: FILE_NOT_FOUND.
      (G-3) Structurally invalid (non-JSON/non-OTIO) timeline file:
            OTIO_ERROR.
      (G-4) Output suffix not matching the requested format's allow-list:
            INVALID_INPUT, and the offending suffix string itself is never
            echoed in message/hint (SR L-1 / CWE-209 pattern).
      (G-5) An unexpected (non-ClipwrightError) exception at any point is
            caught by the outer boundary and returned as INTERNAL with a
            fixed generic message; no input/output path leaks (AC-12).
  (H) [SR-V-002] / [CR-T-001] Absolute media-reference symlink rejection
      (H-1) A clip whose media_reference.target_url is an absolute path with
            a symlink component is rejected with PATH_NOT_ALLOWED and no
            output file is produced. Exercises the local re-implementation
            (_normalize_ref/_has_symlink_component) that the absolute branch
            of _absolutize_media_refs uses in place of check_media_ref
            (CWE-59). Skipped locally when the OS refuses symlink creation
            (Windows without elevated privileges); runs on CI's 3-OS matrix.
  (I) [CR-T-001] DEPENDENCY_MISSING (ADR-EX-9 defensive branch)
      (I-1) When otio.adapters.available_adapter_names() does not contain
            the expected adapter name, _write_adapter raises
            DEPENDENCY_MISSING instead of an unguarded AttributeError/
            KeyError, and the error message/hint do not leak the timeline/
            output paths.
  (J) [CR-E-004] _write_adapter hint must match the requested format
      (J-1) A fcpxml write failure (OTIOError) must not surface the
            EDL-specific hint text ("single video track" / "export to
            FCPXML instead" is nonsensical when fcpxml was already
            requested). This test was Red until _write_adapter's OTIOError
            handler was made format-aware; it now guards against that
            regression.
  (K) [CR-NEW] _representative_rate must prefer the Video track's rate
      (K-1) When an Audio track is enumerated before the Video track in
            tl.tracks (Stack order is not kind-sorted), the EDL rate warning
            must still name the Video track's rate, not whichever clip
            _iter_clips happens to yield first. This test was Red until
            _representative_rate was made track-kind-aware; it now guards
            against that regression.
  (L) [SR-V-001] _loss_report unknown-kind length bound (CWE-400)
      (L-1) An unbounded-length unknown marker kind string must not be
            echoed verbatim into the aggregated warning; the embedded kind
            text must be truncated to a small fixed upper bound (<=64
            chars). This test was Red until _loss_report truncated the kind
            string; it now guards against that regression.
  (M) [ADR-EX-12] EDL frame-boundary quantization (architecture-report
      20260717-095045.md sect 2/4/5/6; requirements-report
      20260717-094900.md acceptance criteria)
      (M-1) Fractional-frame clip/gap durations (the bug-reproducing shape
            that silence detection / second-based trims typically produce)
            on the EDL path succeed with an aggregated quantization
            warning, parametrized over integer rates 24/25/30 plus a
            dedicated half-frame-boundary (62.5f) case pinning half-up
            rounding (floor(x+0.5+eps)) against banker's round()'s 62.
            This class was Red at authoring time -- _quantize_to_frame_
            boundaries did not yet exist, so the EDL write-then-verify
            (ADR-EX-11) rejected these inputs with OTIO_ERROR instead of
            succeeding.
      (M-2) Re-read cut points stay within 0.5/rate + eps seconds of the
            original (pre-quantization) cumulative value.
      (M-3) 3+ clips plus a Gap: quantized durations match an independent
            reference boundary-diff model exactly (no drift accumulation
            regardless of item count).
      (M-4) A frame-aligned input (roundtrip_timeline_factory) produces no
            quantization warning and is unchanged.
      (M-5) FCPXML is not quantized for the same fractional-duration input
            (rational-seconds representation round-trips losslessly).
      (M-6) The input .otio file is byte-unchanged after an EDL export of
            fractional-duration clips (non-destructive, EDL variant of the
            (B-2) pattern).
      (M-7) A sub-half-frame clip collapses to zero length with its own
            "collapsed to zero length" warning fragment.
      (M-8) The written EDL re-reads via read_from_file(..., "cmx_3600",
            rate=...) without raising (the exact EDLParseError this ADR
            eliminates).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest
from clipwright.errors import ErrorCode
from clipwright.otio_utils import (
    add_clip,
    add_gap,
    add_marker,
    load_timeline,
    new_timeline,
    save_timeline,
)
from clipwright.pathpolicy import media_ref_for_otio
from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

from clipwright_export.schemas import ExportTimelineOptions
from clipwright_export.timeline_export import _loss_report, export_timeline

from .conftest import LossyFixture, RoundtripFixture

# ===========================================================================
# Symlink availability detection (for pytest.mark.skipif at collection time)
#
# Mirrors tests/test_pathpolicy.py and clipwright-bgm/tests/test_pathpolicy_
# bgm.py (see agent-memory symlink-test-local-skip-vs-ci): symlink creation
# requires elevated privileges on Windows without Developer Mode, so this
# probe lets [H] skip locally and run unattended on CI's 3-OS matrix.
# ===========================================================================


def _probe_symlink_support() -> bool:
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            real = base / "_probe_real.txt"
            real.write_bytes(b"probe")
            link = base / "_probe_link.txt"
            link.symlink_to(real)
        return True
    except OSError:
        return False


_SYMLINK_SUPPORTED: bool = _probe_symlink_support()
_SKIP_SYMLINK_REASON = (
    "Symlink creation requires elevated privileges on this system (WinError 1314)."
    " Enable Windows Developer Mode or run as Administrator."
)
_skip_no_symlinks = pytest.mark.skipif(
    not _SYMLINK_SUPPORTED,
    reason=_SKIP_SYMLINK_REASON,
)


def _try_symlink(link: Path, target: Path) -> None:
    """Create a symlink; skip the test if the OS refuses (Windows privilege)."""
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip(
            "Cannot create symlinks on this system (requires elevated privileges)"
        )


# ===========================================================================
# Helpers
# ===========================================================================


def _seconds(rt: otio.opentime.RationalTime) -> float:
    return rt.value / rt.rate


def _video_clips(tl: otio.schema.Timeline) -> list[otio.schema.Clip]:
    """Return Clip objects on Video-kind tracks, in track/item order."""
    return [
        item
        for track in tl.tracks
        if track.kind == otio.schema.TrackKind.Video
        for item in track
        if isinstance(item, otio.schema.Clip)
    ]


# ===========================================================================
# ADR-EX-12 (EDL frame-boundary quantization) helpers -- used only by
# TestEdlFrameQuantization below.
# ===========================================================================

# Pinned substrings from architecture-report §4 (_quantize_warnings); not
# guessed -- the exact aggregate-warning wording the fix is required to emit.
_QUANT_WARNING_SUBSTR = "quantized to whole frames"
_ZERO_COLLAPSE_SUBSTR = "collapsed to zero length"


def _round_half_up_frames(x: float) -> int:
    """Independent half-up reference (architecture §2.2): floor(x+0.5+eps).

    This is a *test oracle*, not a copy of the implementation (which does
    not exist yet at Red-authoring time) -- it exists so quantization tests
    can state exact expected integer frame values from the spec formula
    alone. Deliberately NOT round(), which uses banker's rounding and would
    send 62.5 -> 62 instead of the required 63 (see the half_boundary case
    below).
    """
    return math.floor(x + 0.5 + 1e-6)


def _expected_quantized_durations(items: list[tuple[str, float]]) -> list[int]:
    """Reference boundary-diff model (architecture §2.3) for a track's item
    sequence, given as (kind, duration_frames) pairs where kind is "clip" or
    "gap" (kind itself is not used by the math, only documents intent at
    call sites). Returns each item's expected exact quantized duration, in
    the same order. Independent of the implementation under test -- see
    _round_half_up_frames docstring.
    """
    raw_acc = 0.0
    prev_rounded = 0
    out: list[int] = []
    for _, dur in items:
        raw_acc += dur
        new_rounded = _round_half_up_frames(raw_acc)
        out.append(new_rounded - prev_rounded)
        prev_rounded = new_rounded
    return out


def _build_frame_duration_timeline(
    tmp_path: Path,
    make_media_file: Any,
    rate: float,
    durations_frames: tuple[float, ...],
    *,
    gap_after_index: int | None = None,
    gap_duration_frames: float = 0.0,
    name: str = "frac",
) -> str:
    """Build and save a V1-only timeline whose clips carry the given
    frame-unit (possibly fractional) durations at *rate* -- the shape that
    reproduces the ADR-EX-12 EDL desync bug (silence detection / second-
    based trims generally produce non-frame-aligned durations, so two or
    more fractional-duration clips in a row phase-shift the record_in of
    later clips against their source duration).

    Each clip's source_range.start_time is set to an arbitrary, distinct
    in-source-media position (n * 1000 frames) rather than a value related
    to its record/timeline position: a Track composes items sequentially by
    duration alone, so a clip's source_range.start_time is only its in-point
    *within the source media*, never its record position (ADR-EX-12 §2.3
    quantizes the two independently -- this distinguishes a start-
    quantization bug from a duration/boundary-quantization bug).

    Optionally inserts one Gap (via clipwright.otio_utils.add_gap, reused
    as-is, not reimplemented) of *gap_duration_frames* immediately after
    clip index *gap_after_index* (0-based).
    """
    media = make_media_file(f"{name}.mov")
    tl = new_timeline(name=f"export-{name}")
    v1 = tl.tracks[0]
    available = TimeRangeModel(
        start_time=RationalTimeModel(value=0.0, rate=rate),
        duration=RationalTimeModel(value=100000.0, rate=rate),
    )
    ref = media_ref_for_otio(media, tmp_path)
    for i, dur in enumerate(durations_frames):
        add_clip(
            v1,
            MediaRef(target_url=ref, available_range=available),
            TimeRangeModel(
                start_time=RationalTimeModel(value=i * 1000.0, rate=rate),
                duration=RationalTimeModel(value=dur, rate=rate),
            ),
            name=f"clip{i}",
        )
        if gap_after_index is not None and i == gap_after_index:
            add_gap(v1, RationalTimeModel(value=gap_duration_frames, rate=rate))
    otio_path = tmp_path / f"{name}.otio"
    save_timeline(tl, str(otio_path))
    return str(otio_path)


# Table labels from architecture-report §5.1 (fixed strings, independent of
# count — the §5.2 template is "{n} {label}", never singular/plural agreement).
_LOSS_LABELS: dict[str, str] = {
    "caption": "captions",
    "text_overlay": "text overlays",
    "image_overlay": "image overlays",
    "pip_overlay": "picture-in-picture overlays",
    "bgm": "background music tracks",
    "color": "color grades",
    "denoise": "noise reductions",
    "loudness": "loudness adjustments",
    "stabilize": "stabilizations",
    "speed": "speed changes",
    "transition": "transitions",
}


def _build_bare_clip_timeline(
    tmp_path: Path,
    media: Path,
    *,
    rate: float = 30.0,
    target_url: str | None = None,
) -> otio.schema.Timeline:
    """Build a minimal single-clip V1(+A1) timeline for ad-hoc error cases.

    Does not use the shared factories (they always wire a valid, existing,
    in-boundary media reference); this helper lets individual error tests
    override target_url with a deliberately broken reference.
    """
    tl = new_timeline(name="ad-hoc")
    v1 = tl.tracks[0]
    ref = target_url if target_url is not None else media_ref_for_otio(media, tmp_path)
    available = TimeRangeModel(
        start_time=RationalTimeModel(value=0.0, rate=rate),
        duration=RationalTimeModel(value=3600.0 * rate, rate=rate),
    )
    add_clip(
        v1,
        MediaRef(target_url=ref, available_range=available),
        TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=rate),
            duration=RationalTimeModel(value=10.0 * rate, rate=rate),
        ),
        name="clip1",
    )
    return tl


# ===========================================================================
# (A) Round-trip in/out — AC-1 / AC-2
# ===========================================================================


class TestRoundtripEdl:
    """AC-1: EDL export + re-read preserves cut points within 1 frame."""

    @pytest.mark.parametrize("rate", [24.0, 25.0, 30.0])
    def test_inout_matches_within_one_frame(
        self,
        roundtrip_timeline_factory: Any,
        tmp_path: Path,
        rate: float,
    ) -> None:
        fixture: RoundtripFixture = roundtrip_timeline_factory(
            rate=rate, name=f"rt_edl_{int(rate)}"
        )
        out = tmp_path / f"out_edl_{int(rate)}.edl"

        result = export_timeline(
            timeline=fixture.otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )

        assert result.ok is True, result.error
        assert out.exists()

        # spike §C-2: EDL re-read requires rate= explicit or it silently
        # defaults to 24fps and seconds drift.
        back = otio.adapters.read_from_file(
            str(out), adapter_name="cmx_3600", rate=rate
        )
        video_clips = _video_clips(back)
        assert len(video_clips) == len(fixture.clip_specs)

        tolerance = 1.0 / rate
        for clip, (_, start_s, dur_s) in zip(
            video_clips, fixture.clip_specs, strict=True
        ):
            sr = clip.source_range
            assert abs(_seconds(sr.start_time) - start_s) <= tolerance
            assert abs(_seconds(sr.duration) - dur_s) <= tolerance

    def test_subsecond_frame_aligned_boundaries_pass_verify(
        self, tmp_path: Path, make_media_file: Any
    ) -> None:
        """§13.2 false-positive safety net (spike §(4b)).

        ADR-EX-12 note: despite the test name, this clip's source_range is
        actually fractional-frame (10.5s start / 40.25s duration * 30fps =
        315.0f start / 1207.5f duration -- 1207.5f is not frame-aligned).
        Before ADR-EX-12 this happened to still pass write-then-verify
        because it is a *single* clip on an otherwise-empty track (the
        desync only appears once a second fractional-duration clip/gap
        follows and accumulates phase error into the next record_in). After
        the ADR-EX-12 fix this now also exercises the quantization path
        (1207.5f rounds half-up to 1208f) and still passes verify; the
        assertions below (ok/exists only) are intentionally left unchanged
        as this test's role is a false-positive safety net, not a
        quantization-value regression pin (see TestEdlFrameQuantization for
        that).
        """
        rate = 30.0
        media = make_media_file("clip.mov")
        tl = new_timeline(name="subsecond")
        v1 = tl.tracks[0]
        ref = media_ref_for_otio(media, tmp_path)
        available = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=rate),
            duration=RationalTimeModel(value=3600.0 * rate, rate=rate),
        )
        add_clip(
            v1,
            MediaRef(target_url=ref, available_range=available),
            TimeRangeModel(
                start_time=RationalTimeModel(value=10.5 * rate, rate=rate),
                duration=RationalTimeModel(value=40.25 * rate, rate=rate),
            ),
            name="clip1",
        )
        otio_path = tmp_path / "subsecond.otio"
        save_timeline(tl, str(otio_path))

        out = tmp_path / "subsecond.edl"
        result = export_timeline(
            timeline=str(otio_path),
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )
        assert result.ok is True, result.error
        assert out.exists()


class TestRoundtripFcpxml:
    """AC-2: FCPXML export + re-read preserves cut points within 1 frame."""

    @pytest.mark.parametrize("rate", [24.0, 25.0, 30.0])
    def test_inout_matches_within_one_frame(
        self,
        roundtrip_timeline_factory: Any,
        tmp_path: Path,
        rate: float,
    ) -> None:
        fixture: RoundtripFixture = roundtrip_timeline_factory(
            rate=rate, name=f"rt_fcpxml_{int(rate)}"
        )
        out = tmp_path / f"out_fcpxml_{int(rate)}.fcpxml"

        result = export_timeline(
            timeline=fixture.otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="fcpxml"),
        )

        assert result.ok is True, result.error
        assert out.exists()

        back = otio.adapters.read_from_file(str(out), adapter_name="fcpx_xml")
        video_clips = _video_clips(back)
        assert len(video_clips) == len(fixture.clip_specs)

        tolerance = 1.0 / rate
        for clip, (_, start_s, dur_s) in zip(
            video_clips, fixture.clip_specs, strict=True
        ):
            sr = clip.source_range
            assert abs(_seconds(sr.start_time) - start_s) <= tolerance
            assert abs(_seconds(sr.duration) - dur_s) <= tolerance


# ===========================================================================
# (B) Media absolutization — AC-3
# ===========================================================================


class TestMediaAbsolutization:
    def test_output_refs_are_absolute_and_resolve_to_real_media(
        self, roundtrip_timeline_factory: Any, tmp_path: Path
    ) -> None:
        fixture: RoundtripFixture = roundtrip_timeline_factory(
            rate=30.0, name="abs_fcpxml"
        )
        out = tmp_path / "abs.fcpxml"

        result = export_timeline(
            timeline=fixture.otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="fcpxml"),
        )
        assert result.ok is True, result.error

        back = otio.adapters.read_from_file(str(out), adapter_name="fcpx_xml")
        urls = [clip.media_reference.target_url for clip in _video_clips(back)]
        assert urls, "expected at least one video clip with a media reference"
        for url in urls:
            assert Path(url).is_absolute(), f"expected absolute target_url, got {url!r}"

        expected = {
            (fixture.media_dir / "clipA.mov").resolve().as_posix(),
            (fixture.media_dir / "clipB.mov").resolve().as_posix(),
        }
        assert set(urls) == expected

    def test_source_otio_file_is_unchanged_after_export(
        self, roundtrip_timeline_factory: Any, tmp_path: Path
    ) -> None:
        fixture: RoundtripFixture = roundtrip_timeline_factory(
            rate=30.0, name="unchanged"
        )
        out = tmp_path / "unchanged.fcpxml"

        before = Path(fixture.otio_path).read_bytes()
        result = export_timeline(
            timeline=fixture.otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="fcpxml"),
        )
        assert result.ok is True, result.error

        after = Path(fixture.otio_path).read_bytes()
        assert before == after, "the input OTIO file must never be modified"

        reread = load_timeline(fixture.otio_path)
        clip_urls = [
            item.media_reference.target_url
            for item in reread.tracks[0]
            if isinstance(item, otio.schema.Clip)
        ]
        assert clip_urls, "expected clips on the reloaded source timeline"
        assert all(not Path(u).is_absolute() for u in clip_urls), (
            "source timeline's own references must remain relative"
        )

    def test_missing_media_is_skipped_with_warning(
        self, tmp_path: Path, make_media_file: Any
    ) -> None:
        tl = _build_bare_clip_timeline(
            tmp_path,
            media=tmp_path / "unused.mov",  # never written to disk
            target_url="missing.mp4",
        )
        otio_path = tmp_path / "missing_media.otio"
        save_timeline(tl, str(otio_path))

        out = tmp_path / "missing_media.fcpxml"
        result = export_timeline(
            timeline=str(otio_path),
            output=str(out),
            options=ExportTimelineOptions(format="fcpxml"),
        )
        assert result.ok is True, result.error
        joined = " ".join(result.warnings).lower()
        assert "could not be resolved to an absolute path" in joined

        reread = load_timeline(str(otio_path))
        clip = next(
            item for item in reread.tracks[0] if isinstance(item, otio.schema.Clip)
        )
        assert clip.media_reference.target_url == "missing.mp4"

    def test_boundary_violating_relative_ref_fails_whole_export(
        self, tmp_path: Path, make_media_file: Any
    ) -> None:
        tl = _build_bare_clip_timeline(
            tmp_path,
            media=tmp_path / "unused.mov",
            target_url="../outside/escaped.mp4",
        )
        otio_path = tmp_path / "boundary.otio"
        save_timeline(tl, str(otio_path))

        out = tmp_path / "boundary.fcpxml"
        result = export_timeline(
            timeline=str(otio_path),
            output=str(out),
            options=ExportTimelineOptions(format="fcpxml"),
        )
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.PATH_NOT_ALLOWED
        assert not out.exists()


# ===========================================================================
# (C) Loss report — AC-4
# ===========================================================================


class TestLossReport:
    def test_counts_and_labels_all_known_kinds(
        self, lossy_timeline_factory: Any
    ) -> None:
        fixture: LossyFixture = lossy_timeline_factory()
        tl = load_timeline(fixture.otio_path)

        result = _loss_report(tl)

        assert isinstance(result, list)
        assert result, "expected at least one loss entry"
        joined = " ".join(result).lower()
        for label in _LOSS_LABELS.values():
            assert label in joined, (
                f"expected label {label!r} in loss report: {result!r}"
            )

    def test_excludes_scene_boundary(self, lossy_timeline_factory: Any) -> None:
        fixture: LossyFixture = lossy_timeline_factory()
        tl = load_timeline(fixture.otio_path)

        joined = " ".join(_loss_report(tl)).lower()
        assert "scene marker" not in joined
        assert "scene_boundary" not in joined

    def test_unknown_kind_is_grouped_as_other(
        self, lossy_timeline_factory: Any
    ) -> None:
        fixture: LossyFixture = lossy_timeline_factory()
        tl = load_timeline(fixture.otio_path)

        joined = " ".join(_loss_report(tl))
        assert "other clipwright annotations" in joined.lower()
        assert "widget_overlay" in joined

    def test_empty_when_no_losses(self, roundtrip_timeline_factory: Any) -> None:
        fixture: RoundtripFixture = roundtrip_timeline_factory(rate=30.0, name="clean")
        tl = load_timeline(fixture.otio_path)

        assert _loss_report(tl) == []

    def test_export_timeline_surfaces_loss_warnings(
        self, lossy_timeline_factory: Any, tmp_path: Path
    ) -> None:
        fixture: LossyFixture = lossy_timeline_factory()
        out = tmp_path / "lossy_out.fcpxml"

        result = export_timeline(
            timeline=fixture.otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="fcpxml"),
        )
        assert result.ok is True, result.error
        joined = " ".join(result.warnings).lower()
        assert "captions" in joined
        assert "transitions" in joined


# ===========================================================================
# (D) Non-integer frame rate rejection — ADR-EX-10
# ===========================================================================


class TestNonIntegerRateRejection:
    @pytest.mark.parametrize("rate", [23.976, 29.97])
    @pytest.mark.parametrize("fmt,ext", [("edl", ".edl"), ("fcpxml", ".fcpxml")])
    def test_rejected_before_any_write(
        self,
        roundtrip_timeline_factory: Any,
        tmp_path: Path,
        rate: float,
        fmt: str,
        ext: str,
    ) -> None:
        safe_rate = str(rate).replace(".", "_")
        fixture: RoundtripFixture = roundtrip_timeline_factory(
            rate=rate, name=f"ntsc_{fmt}_{safe_rate}"
        )
        out = tmp_path / f"ntsc_{fmt}_{safe_rate}{ext}"

        result = export_timeline(
            timeline=fixture.otio_path,
            output=str(out),
            options=ExportTimelineOptions(format=fmt),
        )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT
        assert not out.exists(), "non-integer rate must never produce a written file"

        msg_lower = result.error.message.lower()
        assert "frame rate" in msg_lower
        hint_lower = result.error.hint.lower()
        assert "render" in hint_lower
        assert "integer rate" in hint_lower or "conform" in hint_lower


# ===========================================================================
# (E) Write-then-verify failure — ADR-EX-11
# ===========================================================================


class TestWriteThenVerify:
    def test_verify_failure_returns_otio_error_and_deletes_output(
        self,
        roundtrip_timeline_factory: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fixture: RoundtripFixture = roundtrip_timeline_factory(
            rate=30.0, name="verify_fail"
        )
        out = tmp_path / "verify_fail.fcpxml"

        original_read = otio.adapters.read_from_file

        def _fake_read(*args: object, **kwargs: object) -> otio.schema.Timeline:
            # ADR-EX-11's verify re-read always passes adapter_name=
            # explicitly; load_timeline (loading the *input* timeline) does
            # not. Only the verify call should fail here.
            if "adapter_name" in kwargs:
                raise RuntimeError("simulated adapter verify failure")
            result: otio.schema.Timeline = original_read(*args, **kwargs)
            return result

        monkeypatch.setattr(otio.adapters, "read_from_file", _fake_read)

        result = export_timeline(
            timeline=fixture.otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="fcpxml"),
        )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.OTIO_ERROR
        assert not out.exists(), "verify failure must delete the written artifact"

    def test_verify_failure_does_not_leak_paths(
        self,
        roundtrip_timeline_factory: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fixture: RoundtripFixture = roundtrip_timeline_factory(
            rate=30.0, name="verify_fail_cwe"
        )
        out = tmp_path / "verify_fail_cwe.fcpxml"

        original_read = otio.adapters.read_from_file

        def _fake_read(*args: object, **kwargs: object) -> otio.schema.Timeline:
            if "adapter_name" in kwargs:
                raise RuntimeError("simulated adapter verify failure")
            result: otio.schema.Timeline = original_read(*args, **kwargs)
            return result

        monkeypatch.setattr(otio.adapters, "read_from_file", _fake_read)

        result = export_timeline(
            timeline=fixture.otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="fcpxml"),
        )
        assert result.error is not None
        assert out.name not in result.error.message
        assert Path(fixture.otio_path).name not in result.error.message
        assert out.name not in result.error.hint
        assert Path(fixture.otio_path).name not in result.error.hint


# ===========================================================================
# (F) EDL-specific behaviour
# ===========================================================================


class TestEdlSpecific:
    def test_audio_track_dropped_with_warning(
        self, roundtrip_timeline_factory: Any, tmp_path: Path
    ) -> None:
        fixture: RoundtripFixture = roundtrip_timeline_factory(
            rate=30.0, with_audio=True, name="edl_audio"
        )
        out = tmp_path / "edl_audio.edl"

        result = export_timeline(
            timeline=fixture.otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )
        assert result.ok is True, result.error

        text = out.read_text(encoding="utf-8", errors="replace")
        event_lines = [ln for ln in text.splitlines() if ln.strip()[:3].isdigit()]
        # spike §(2): EDL carries only video events, never audio events, so
        # the event-line count must equal the V1 clip count (2), not 4.
        assert len(event_lines) == len(fixture.clip_specs)

        joined = " ".join(result.warnings).lower()
        assert "video cuts only" in joined or "audio track" in joined

    def test_two_video_tracks_returns_otio_error(
        self, tmp_path: Path, make_media_file: Any
    ) -> None:
        rate = 30.0
        media = make_media_file("clip.mov")
        ref_str = media_ref_for_otio(media, tmp_path)

        tl = otio.schema.Timeline(name="two-video")
        for track_name in ("V1", "V2"):
            track = otio.schema.Track(name=track_name, kind=otio.schema.TrackKind.Video)
            tl.tracks.append(track)
            ref = otio.schema.ExternalReference(target_url=ref_str)
            ref.available_range = otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0.0, rate),
                duration=otio.opentime.RationalTime(3600.0 * rate, rate),
            )
            track.append(
                otio.schema.Clip(
                    name="clip",
                    media_reference=ref,
                    source_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(0.0, rate),
                        duration=otio.opentime.RationalTime(10.0 * rate, rate),
                    ),
                )
            )
        otio_path = tmp_path / "two_video.otio"
        save_timeline(tl, str(otio_path))

        out = tmp_path / "two_video.edl"
        result = export_timeline(
            timeline=str(otio_path),
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.OTIO_ERROR
        assert not out.exists()
        # CR-E-004 regression guard: the EDL-specific hint must stay in
        # place for the case it was actually written for (contrast with the
        # fcpxml case in TestWriteAdapterHintMatchesFormat below).
        assert result.error.hint is not None
        assert "single video track" in result.error.hint.lower()

    def test_representative_rate_prefers_video_over_audio_track_order(
        self, tmp_path: Path, make_media_file: Any
    ) -> None:
        """CR-NEW (K): _representative_rate must use the Video track's rate
        even when an Audio track is enumerated first in tl.tracks (Stack
        order is not kind-sorted). Uses distinct integer rates per track
        kind so a wrong pick is observable in the EDL rate warning text.
        This test was Red until _representative_rate was made
        track-kind-aware; it now guards against that regression.
        """
        media = make_media_file("clip.mov")
        ref_str = media_ref_for_otio(media, tmp_path)

        audio_rate = 48.0
        video_rate = 30.0

        tl = otio.schema.Timeline(name="audio-first")

        audio_track = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
        tl.tracks.append(audio_track)
        audio_ref = otio.schema.ExternalReference(target_url=ref_str)
        audio_ref.available_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, audio_rate),
            duration=otio.opentime.RationalTime(3600.0 * audio_rate, audio_rate),
        )
        audio_track.append(
            otio.schema.Clip(
                name="audio_clip",
                media_reference=audio_ref,
                source_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0.0, audio_rate),
                    duration=otio.opentime.RationalTime(10.0 * audio_rate, audio_rate),
                ),
            )
        )

        video_track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        tl.tracks.append(video_track)
        video_ref = otio.schema.ExternalReference(target_url=ref_str)
        video_ref.available_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, video_rate),
            duration=otio.opentime.RationalTime(3600.0 * video_rate, video_rate),
        )
        video_track.append(
            otio.schema.Clip(
                name="video_clip",
                media_reference=video_ref,
                source_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0.0, video_rate),
                    duration=otio.opentime.RationalTime(10.0 * video_rate, video_rate),
                ),
            )
        )

        otio_path = tmp_path / "audio_first.otio"
        save_timeline(tl, str(otio_path))

        out = tmp_path / "audio_first.edl"
        result = export_timeline(
            timeline=str(otio_path),
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )
        assert result.ok is True, result.error

        joined = " ".join(result.warnings)
        assert f"{int(video_rate)} fps" in joined, (
            "expected the EDL rate warning to name the Video track's rate "
            f"({int(video_rate)}), not the Audio track's ({int(audio_rate)}); "
            f"got warnings: {result.warnings!r}"
        )

    def test_rate_warning_mentions_representative_rate(
        self, roundtrip_timeline_factory: Any, tmp_path: Path
    ) -> None:
        fixture: RoundtripFixture = roundtrip_timeline_factory(
            rate=25.0, name="edl_rate_warn"
        )
        out = tmp_path / "edl_rate_warn.edl"

        result = export_timeline(
            timeline=fixture.otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )
        assert result.ok is True, result.error
        joined = " ".join(result.warnings)
        assert "25" in joined
        assert "fps" in joined.lower()


# ===========================================================================
# (G) Error taxonomy — AC-5 / FR-2 / AC-12
# ===========================================================================


class TestErrors:
    def test_output_equals_timeline_returns_path_not_allowed(
        self, roundtrip_timeline_factory: Any
    ) -> None:
        # check_output_not_source (pathpolicy.py:170-196) always raises
        # PATH_NOT_ALLOWED; this is the same real-world precedent as
        # clipwright-speed (test_speed.py:492-506), even though the
        # requirements prose (FR-2/AC-5) says "INVALID_INPUT" -- the shared
        # helper cannot be parametrized to raise a different code, and
        # architecture §1 explicitly reuses check_output_not_source as-is.
        fixture: RoundtripFixture = roundtrip_timeline_factory(
            rate=30.0, name="same_path"
        )
        result = export_timeline(
            timeline=fixture.otio_path,
            output=fixture.otio_path,
            options=ExportTimelineOptions(format="edl"),
        )
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.PATH_NOT_ALLOWED

    def test_nonexistent_timeline_returns_file_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.otio"
        out = tmp_path / "out.edl"
        result = export_timeline(
            timeline=str(missing),
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.FILE_NOT_FOUND
        assert not out.exists()

    def test_invalid_otio_file_returns_otio_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.otio"
        bad.write_text("not a valid otio json", encoding="utf-8")
        out = tmp_path / "out.edl"
        result = export_timeline(
            timeline=str(bad),
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.OTIO_ERROR
        assert not out.exists()

    @pytest.mark.parametrize(
        "fmt,wrong_ext",
        [("edl", ".fcpxml"), ("edl", ".txt"), ("fcpxml", ".edl"), ("fcpxml", ".txt")],
    )
    def test_wrong_extension_rejected_without_suffix_in_message(
        self,
        roundtrip_timeline_factory: Any,
        tmp_path: Path,
        fmt: str,
        wrong_ext: str,
    ) -> None:
        safe = f"{fmt}{wrong_ext}".replace(".", "_")
        fixture: RoundtripFixture = roundtrip_timeline_factory(
            rate=30.0, name=f"wrongext_{safe}"
        )
        out = tmp_path / f"out_{safe}{wrong_ext}"

        result = export_timeline(
            timeline=fixture.otio_path,
            output=str(out),
            options=ExportTimelineOptions(format=fmt),
        )
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT
        assert not out.exists()
        # SR L-1 / CWE-209 pattern: the offending suffix string itself must
        # never be echoed back in the message or hint.
        assert wrong_ext not in result.error.message
        assert wrong_ext not in result.error.hint

    def test_uncaught_exception_does_not_leak_path(
        self,
        roundtrip_timeline_factory: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fixture: RoundtripFixture = roundtrip_timeline_factory(rate=30.0, name="cwe209")
        out = tmp_path / "cwe209.fcpxml"

        def _boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError(f"unexpected failure touching {fixture.otio_path}")

        monkeypatch.setattr(otio.adapters, "write_to_file", _boom)

        result = export_timeline(
            timeline=fixture.otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="fcpxml"),
        )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INTERNAL
        assert Path(fixture.otio_path).name not in result.error.message
        assert Path(fixture.otio_path).name not in result.error.hint
        assert not out.exists()


# ===========================================================================
# (H) [SR-V-002] / [CR-T-001] Absolute media-reference symlink rejection
# ===========================================================================


class TestAbsoluteMediaRefSymlinkRejected:
    """The absolute-reference branch of _absolutize_media_refs
    (_normalize_ref/_has_symlink_component, timeline_export.py:120-134/
    252-263) must reject a symlink the same way the relative branch (via
    check_media_ref) already does — this local re-implementation exists
    because check_media_ref's absolute branch fails a *missing* absolute
    reference, which conflicts with ADR-EX-4's best-effort skip requirement,
    so export.py cannot simply delegate to it.

    Verified as a basic Green check: the implementation logic was inspected
    against pathpolicy's leaf-to-root is_symlink() walk (ADR-PP-2) and found
    equivalent; this test locks that in as a regression guard rather than
    driving new implementation work.
    """

    @_skip_no_symlinks
    def test_symlinked_absolute_media_ref_returns_path_not_allowed(
        self, tmp_path: Path, make_media_file: Any
    ) -> None:
        real_media = make_media_file("real_clip.mov")
        link_path = tmp_path / "link_clip.mov"
        _try_symlink(link_path, real_media)

        tl = _build_bare_clip_timeline(
            tmp_path,
            media=tmp_path / "unused.mov",
            target_url=str(link_path),
        )
        otio_path = tmp_path / "symlink_abs.otio"
        save_timeline(tl, str(otio_path))

        out = tmp_path / "symlink_abs.fcpxml"
        result = export_timeline(
            timeline=str(otio_path),
            output=str(out),
            options=ExportTimelineOptions(format="fcpxml"),
        )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.PATH_NOT_ALLOWED
        assert not out.exists(), (
            "a symlinked absolute media reference must never produce an output file"
        )


# ===========================================================================
# (I) [CR-T-001] DEPENDENCY_MISSING (ADR-EX-9 defensive branch)
# ===========================================================================


class TestDependencyMissing:
    """Verified as a basic Green check: the defensive branch that guards
    against the exchange adapter not being registered (a packaging/
    installation failure that should not normally occur, per ADR-EX-9)."""

    def test_missing_adapter_returns_dependency_missing(
        self,
        roundtrip_timeline_factory: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fixture: RoundtripFixture = roundtrip_timeline_factory(
            rate=30.0, name="dep_missing"
        )
        out = tmp_path / "dep_missing.edl"

        monkeypatch.setattr(otio.adapters, "available_adapter_names", lambda: [])

        result = export_timeline(
            timeline=fixture.otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.DEPENDENCY_MISSING
        assert not out.exists()
        # CWE-209: the missing-adapter message must not leak either path.
        assert out.name not in result.error.message
        assert Path(fixture.otio_path).name not in result.error.message
        assert out.name not in result.error.hint
        assert Path(fixture.otio_path).name not in result.error.hint


# ===========================================================================
# (J) [CR-E-004] _write_adapter hint must match the requested format
# ===========================================================================


class TestWriteAdapterHintMatchesFormat:
    """An earlier revision of _write_adapter's OTIOError handler always
    returned the EDL-specific hint wording regardless of *fmt*. This test
    was Red until the handler was fixed to pick its hint text based on
    *fmt*; it now guards against that regression."""

    def test_fcpxml_otio_error_hint_does_not_reference_edl(
        self,
        roundtrip_timeline_factory: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fixture: RoundtripFixture = roundtrip_timeline_factory(
            rate=30.0, name="hint_fcpxml"
        )
        out = tmp_path / "hint_fcpxml.fcpxml"

        def _boom(*args: object, **kwargs: object) -> None:
            raise otio.exceptions.OTIOError("simulated fcpxml write failure")

        monkeypatch.setattr(otio.adapters, "write_to_file", _boom)

        result = export_timeline(
            timeline=fixture.otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="fcpxml"),
        )

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.OTIO_ERROR
        assert not out.exists()

        hint_lower = result.error.hint.lower()
        assert "edl" not in hint_lower, (
            "a fcpxml write-failure hint must not reference EDL (the caller "
            f"already requested fcpxml); got hint: {result.error.hint!r}"
        )
        assert "single video track" not in hint_lower


# ===========================================================================
# (L) [SR-V-001] _loss_report unknown-kind length bound (CWE-400)
# ===========================================================================


class TestLossReportUnknownKindLengthBound:
    """An earlier revision of _loss_report embedded an over-long unknown
    marker kind string verbatim into the aggregated warning sentence. This
    test was Red until _loss_report was fixed to truncate it to a small
    fixed upper bound; it now guards against that regression."""

    def test_unknown_kind_is_truncated_to_length_limit(self) -> None:
        tl = new_timeline(name="kind-overflow")
        v1 = tl.tracks[0]
        long_kind = "z" * 300
        add_marker(
            v1,
            TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=0.0, rate=30.0),
            ),
            name="overflow_marker",
            metadata={
                "tool": "clipwright-test-fixture",
                "version": "0.0.0",
                "kind": long_kind,
            },
        )

        result = _loss_report(tl)

        assert result, "expected a loss entry for the unknown kind"
        joined = " ".join(result)
        assert long_kind not in joined, (
            "the full 300-char kind string must not be echoed verbatim into "
            "the warning (CWE-400 unbounded-size defence)"
        )

        marker = "kind="
        idx = joined.find(marker)
        assert idx != -1, f"expected a 'kind=' marker in the warning: {joined!r}"
        tail = joined[idx + len(marker) :]
        close = tail.find(")")
        embedded = tail[:close] if close != -1 else tail
        assert len(embedded) <= 64, (
            "expected the embedded kind string truncated to <=64 chars, got "
            f"{len(embedded)} chars: {embedded[:80]!r}..."
        )


# ===========================================================================
# (M) [ADR-EX-12] EDL frame-boundary quantization
# ===========================================================================


class TestEdlFrameQuantization:
    """ADR-EX-12: fractional-frame clip/gap durations on the EDL path are
    quantized to whole-frame boundaries on the write-time deep copy
    (_quantize_to_frame_boundaries) instead of failing write-then-verify
    (ADR-EX-11) with cmx_3600's EDLParseError("Source and record duration
    don't match"). This class was Red at authoring time:
    _quantize_to_frame_boundaries did not yet exist in timeline_export.py,
    so every test below failed -- most directly via the desync bug itself
    (export_timeline returned ok=False / OTIO_ERROR instead of ok=True),
    and the aligned-input/FCPXML/non-destructive tests failed their "no
    quantization warning" assertions once the warning text was introduced
    elsewhere in the class's shared fixtures. That Red phase was resolved by
    implementing and wiring in _quantize_to_frame_boundaries per
    architecture-report-20260717-095045.md §1-§6; these tests now guard
    against a regression of the quantization behaviour.
    """

    # (10.3, 20.7) is a measured bug-reproducing pair (verified by direct
    # probe against the pre-fix implementation): at rate 24/25/30 it makes
    # export_timeline return ok=False/OTIO_ERROR today (the cmx_3600
    # write-then-verify EDLParseError this ADR eliminates), not just a
    # missing-warning gap.
    @pytest.mark.parametrize(
        "case_id,rate,durations",
        [
            ("rate24", 24.0, (10.3, 20.7)),
            ("rate25", 25.0, (10.3, 20.7)),
            ("rate30", 30.0, (10.3, 20.7)),
            # Half-up regression pin (architecture §2.2/§6): the first
            # clip's cumulative raw boundary lands exactly on 10.5 frames;
            # half-up (floor(x+0.5+eps)) must round it to 11, never
            # banker's round()'s 10 (round-half-to-even). Also a measured
            # bug-reproducing pair (ok=False today).
            ("half_boundary", 25.0, (10.5, 20.7)),
        ],
    )
    def test_fractional_clips_succeed_with_quantization_warning(
        self,
        tmp_path: Path,
        make_media_file: Any,
        case_id: str,
        rate: float,
        durations: tuple[float, float],
    ) -> None:
        otio_path = _build_frame_duration_timeline(
            tmp_path, make_media_file, rate, durations, name=f"quant_{case_id}"
        )
        out = tmp_path / f"quant_{case_id}.edl"

        result = export_timeline(
            timeline=otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )

        assert result.ok is True, result.error
        assert out.exists()
        joined = " ".join(result.warnings).lower()
        assert _QUANT_WARNING_SUBSTR in joined

        if case_id == "half_boundary":
            back = otio.adapters.read_from_file(
                str(out), adapter_name="cmx_3600", rate=rate
            )
            first_clip = _video_clips(back)[0]
            assert first_clip.source_range.duration.value == pytest.approx(
                11.0, abs=1e-6
            ), (
                "10.5 frames must round half-up to 11 (never banker's "
                "round()'s 10); regression pin for the ADR-EX-12 half-up rule"
            )

    @pytest.mark.parametrize("rate", [24.0, 25.0, 30.0])
    def test_cut_points_shift_at_most_half_a_frame(
        self, tmp_path: Path, make_media_file: Any, rate: float
    ) -> None:
        durations = (10.3, 20.7)
        otio_path = _build_frame_duration_timeline(
            tmp_path, make_media_file, rate, durations, name=f"cutpt_{int(rate)}"
        )
        out = tmp_path / f"cutpt_{int(rate)}.edl"

        result = export_timeline(
            timeline=otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )
        assert result.ok is True, result.error

        back = otio.adapters.read_from_file(
            str(out), adapter_name="cmx_3600", rate=rate
        )
        video_clips = _video_clips(back)
        assert len(video_clips) == len(durations)

        tolerance_s = 0.5 / rate + 1e-6
        back_durations_frames = [c.source_range.duration.value for c in video_clips]
        cumulative_original = 0.0
        cumulative_back = 0.0
        for orig_dur, back_dur in zip(durations, back_durations_frames, strict=True):
            cumulative_original += orig_dur
            cumulative_back += back_dur
            original_s = cumulative_original / rate
            back_s = cumulative_back / rate
            assert abs(back_s - original_s) <= tolerance_s, (
                f"cut point at cumulative {cumulative_original} frames drifted "
                f"{abs(back_s - original_s)}s, exceeding the 0.5-frame tolerance "
                f"{tolerance_s}s"
            )

    def test_multi_clip_with_gap_has_no_cumulative_drift(
        self, tmp_path: Path, make_media_file: Any
    ) -> None:
        rate = 30.0
        clip_durations = (62.4, 45.3, 33.7)
        gap_duration = 20.6
        otio_path = _build_frame_duration_timeline(
            tmp_path,
            make_media_file,
            rate,
            clip_durations,
            gap_after_index=1,
            gap_duration_frames=gap_duration,
            name="drift",
        )
        out = tmp_path / "drift.edl"

        result = export_timeline(
            timeline=otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )
        assert result.ok is True, result.error

        back = otio.adapters.read_from_file(
            str(out), adapter_name="cmx_3600", rate=rate
        )
        video_clips = _video_clips(back)
        assert len(video_clips) == len(clip_durations)

        back_durations = [c.source_range.duration.value for c in video_clips]
        assert all(d >= 0 for d in back_durations), (
            "quantized durations must never go negative (record boundaries "
            "must be monotonic non-decreasing)"
        )

        # Independent reference model (architecture §2.3 boundary-diff):
        # walking clip0, clip1, gap, clip2 in order and diffing successively
        # rounded cumulative boundaries. Matching this exactly (not just
        # approximately) demonstrates zero drift regardless of item count --
        # naive independent per-item rounding would instead accumulate up to
        # 0.5 frame of drift per item.
        items: list[tuple[str, float]] = [
            ("clip", clip_durations[0]),
            ("clip", clip_durations[1]),
            ("gap", gap_duration),
            ("clip", clip_durations[2]),
        ]
        expected = _expected_quantized_durations(items)
        expected_clip_durations = [expected[0], expected[1], expected[3]]
        assert back_durations == pytest.approx(
            [float(d) for d in expected_clip_durations], abs=1e-6
        ), (
            f"expected quantized clip durations {expected_clip_durations} "
            f"(boundary-diff, drift-free), got {back_durations}"
        )

    @pytest.mark.parametrize("rate", [24.0, 25.0, 30.0])
    def test_frame_aligned_input_is_unchanged_no_warning(
        self, roundtrip_timeline_factory: Any, tmp_path: Path, rate: float
    ) -> None:
        fixture: RoundtripFixture = roundtrip_timeline_factory(
            rate=rate, name=f"aligned_quant_{int(rate)}"
        )
        out = tmp_path / f"aligned_quant_{int(rate)}.edl"

        result = export_timeline(
            timeline=fixture.otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )
        assert result.ok is True, result.error
        joined = " ".join(result.warnings).lower()
        assert _QUANT_WARNING_SUBSTR not in joined, (
            "a frame-aligned input must produce zero mutation, so no "
            f"quantization warning is expected; got warnings: {result.warnings!r}"
        )

        back = otio.adapters.read_from_file(
            str(out), adapter_name="cmx_3600", rate=rate
        )
        video_clips = _video_clips(back)
        assert len(video_clips) == len(fixture.clip_specs)
        tolerance = 1.0 / rate
        for clip, (_, start_s, dur_s) in zip(
            video_clips, fixture.clip_specs, strict=True
        ):
            sr = clip.source_range
            assert abs(_seconds(sr.start_time) - start_s) <= tolerance
            assert abs(_seconds(sr.duration) - dur_s) <= tolerance

    def test_fcpxml_is_not_quantized_for_fractional_clips(
        self, tmp_path: Path, make_media_file: Any
    ) -> None:
        rate = 30.0
        durations = (10.3, 20.7)
        otio_path = _build_frame_duration_timeline(
            tmp_path, make_media_file, rate, durations, name="fcpxml_frac"
        )
        out = tmp_path / "fcpxml_frac.fcpxml"

        result = export_timeline(
            timeline=otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="fcpxml"),
        )
        assert result.ok is True, result.error
        joined = " ".join(result.warnings).lower()
        assert _QUANT_WARNING_SUBSTR not in joined, (
            "FCPXML must never be quantized (rational-seconds representation "
            f"round-trips losslessly); got warnings: {result.warnings!r}"
        )

        back = otio.adapters.read_from_file(str(out), adapter_name="fcpx_xml")
        video_clips = _video_clips(back)
        assert len(video_clips) == len(durations)
        tolerance = 1.0 / rate
        for clip, dur_frames in zip(video_clips, durations, strict=True):
            assert (
                abs(_seconds(clip.source_range.duration) - dur_frames / rate)
                <= tolerance
            )

    def test_input_otio_bytes_unchanged_after_edl_export(
        self, tmp_path: Path, make_media_file: Any
    ) -> None:
        rate = 30.0
        durations = (10.3, 20.7)
        otio_path = _build_frame_duration_timeline(
            tmp_path, make_media_file, rate, durations, name="nondestructive"
        )
        out = tmp_path / "nondestructive.edl"

        before = Path(otio_path).read_bytes()
        result = export_timeline(
            timeline=otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )
        assert result.ok is True, result.error

        after = Path(otio_path).read_bytes()
        assert before == after, "the input OTIO file must never be modified"

    def test_zero_collapse_clip_reports_warning(
        self, tmp_path: Path, make_media_file: Any
    ) -> None:
        rate = 30.0
        # The first clip is well under half a frame (0.3f); its cumulative
        # raw boundary (0.3) rounds half-up to 0, collapsing it to zero
        # length in the EDL (architecture §5 edge-case table). The second
        # clip keeps the export non-degenerate.
        durations = (0.3, 50.0)
        otio_path = _build_frame_duration_timeline(
            tmp_path, make_media_file, rate, durations, name="zerocollapse"
        )
        out = tmp_path / "zerocollapse.edl"

        result = export_timeline(
            timeline=otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )
        assert result.ok is True, result.error
        joined = " ".join(result.warnings).lower()
        assert _ZERO_COLLAPSE_SUBSTR in joined, (
            f"expected a zero-collapse warning fragment; got: {result.warnings!r}"
        )

    def test_output_edl_rereads_without_exception(
        self, tmp_path: Path, make_media_file: Any
    ) -> None:
        rate = 24.0
        durations = (10.3, 20.7)
        otio_path = _build_frame_duration_timeline(
            tmp_path, make_media_file, rate, durations, name="rereadonly"
        )
        out = tmp_path / "rereadonly.edl"

        result = export_timeline(
            timeline=otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )
        assert result.ok is True, result.error

        # Must not raise EDLParseError("Source and record duration don't
        # match") -- the exact desync this ADR eliminates.
        back = otio.adapters.read_from_file(
            str(out), adapter_name="cmx_3600", rate=rate
        )
        assert isinstance(back, otio.schema.Timeline)
