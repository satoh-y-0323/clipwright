"""conftest.py — Shared fixtures for test_timeline_export.py (Wave 1, task test-timeline).

Provides two fixture families requested by the task instruction:

  1. ``roundtrip_timeline_factory``: builds a sequence-shaped V1(Video)+
     A1(Audio), 2-clip timeline (integer rate, whole-frame source-range
     boundaries) via ``clipwright.otio_utils.new_timeline``/``add_clip``,
     with dummy media files that actually exist on disk under ``tmp_path``
     and are wired in with OTIO-dir-relative POSIX ``target_url`` values
     (``clipwright.pathpolicy.media_ref_for_otio`` shape). Mirrors the real
     ``clipwright-sequence`` output shape (architecture-report §1 reuse
     table, otio_utils.py:32-46/101-133).

  2. ``lossy_timeline_factory``: builds a synthetic timeline carrying one
     instance of every clipwright annotation kind that architecture-report
     §5.1 lists as not representable by the EDL/FCPXML adapters, plus a
     ``scene_boundary`` marker (excluded from loss counting per
     spike-report-export-adapters.md §7b / architecture §5.1) and one
     unrecognised marker kind (ADR-EX-5 "other clipwright annotations"
     bucket).

This conftest.py is dedicated to the test-timeline task (per task
instruction: "他 test タスクは本 conftest に依存しない設計"); sibling test
files (test_chapters.py, test_schemas.py, test_server.py) define their own
fixtures inline to avoid a writes collision.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import opentimelineio as otio
import pytest
from clipwright.otio_utils import (
    add_clip,
    add_marker,
    get_clipwright_metadata,
    new_timeline,
    save_timeline,
    set_clipwright_metadata,
)
from clipwright.pathpolicy import media_ref_for_otio
from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

# ===========================================================================
# Shared media helper
# ===========================================================================

_MEDIA_BYTES = b"clipwright-export test fixture media bytes"


@pytest.fixture
def make_media_file(tmp_path: Path) -> Callable[[str], Path]:
    """Return a callable that writes a dummy media file under tmp_path.

    Shared by both fixture factories below and by ad-hoc timelines built
    directly inside test_timeline_export.py (e.g. missing-media, boundary-
    violation, and 2-video-track cases that do not fit either factory).
    """

    def _make(name: str) -> Path:
        path = tmp_path / name
        path.write_bytes(_MEDIA_BYTES)
        return path

    return _make


def _write_dummy_media(path: Path) -> None:
    path.write_bytes(_MEDIA_BYTES)


# ===========================================================================
# Family 1: sequence-shaped round-trip fixture (AC-1/AC-2/AC-3)
# ===========================================================================

# (name, start_s, dur_s) for the two V1 clips. Both are whole-frame at every
# integer rate exercised in tests (24/25/30) — spike-report §(3): whole-frame
# boundaries round-trip exactly (within frame-quantisation) through both
# adapters. Non-integer rates (23.976/29.97) reuse the same seconds so the
# only variable under test is the rate itself (ADR-EX-10).
_CLIP_SPECS_SEC: tuple[tuple[str, float, float], ...] = (
    ("clipA", 10.0, 40.0),
    ("clipB", 100.0, 55.0),
)


@dataclass(frozen=True)
class RoundtripFixture:
    """A saved OTIO timeline plus the ground-truth V1 clip specs used to build it."""

    otio_path: str
    media_dir: Path
    rate: float
    clip_specs: tuple[tuple[str, float, float], ...]


@pytest.fixture
def roundtrip_timeline_factory(
    tmp_path: Path,
) -> Callable[..., RoundtripFixture]:
    """Factory building a sequence-shaped V1(Video)+A1(Audio), 2-clip timeline.

    V1 carries clipA/clipB at the given *rate* per _CLIP_SPECS_SEC. When
    with_audio is True (default) A1 mirrors the same two clips, so EDL's
    "audio is silently dropped, video cuts only" behaviour (spike-report
    §(2)) has something to drop. Each clip's media_reference points at a
    real on-disk dummy file under tmp_path via an OTIO-dir-relative POSIX
    target_url (matches sequence's real reference shape).

    *name* selects the saved filename (default "roundtrip"); pass a distinct
    name per call when a single test needs more than one timeline so their
    tmp_path-relative .otio files do not collide.
    """

    def _build(
        rate: float = 30.0,
        *,
        with_audio: bool = True,
        name: str = "roundtrip",
    ) -> RoundtripFixture:
        media_a = tmp_path / "clipA.mov"
        media_b = tmp_path / "clipB.mov"
        if not media_a.exists():
            _write_dummy_media(media_a)
        if not media_b.exists():
            _write_dummy_media(media_b)

        tl = new_timeline(name=f"export-{name}")
        v1, a1 = tl.tracks[0], tl.tracks[1]

        available = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=rate),
            duration=RationalTimeModel(value=3600.0 * rate, rate=rate),
        )
        ref_a = media_ref_for_otio(media_a, tmp_path)
        ref_b = media_ref_for_otio(media_b, tmp_path)

        for (clip_name, start_s, dur_s), ref in zip(
            _CLIP_SPECS_SEC, (ref_a, ref_b), strict=True
        ):
            add_clip(
                v1,
                MediaRef(target_url=ref, available_range=available),
                TimeRangeModel(
                    start_time=RationalTimeModel(value=start_s * rate, rate=rate),
                    duration=RationalTimeModel(value=dur_s * rate, rate=rate),
                ),
                name=clip_name,
            )
            if with_audio:
                add_clip(
                    a1,
                    MediaRef(target_url=ref, available_range=available),
                    TimeRangeModel(
                        start_time=RationalTimeModel(value=start_s * rate, rate=rate),
                        duration=RationalTimeModel(value=dur_s * rate, rate=rate),
                    ),
                    name=f"{clip_name}_audio",
                )

        otio_path = tmp_path / f"{name}.otio"
        save_timeline(tl, str(otio_path))

        return RoundtripFixture(
            otio_path=str(otio_path),
            media_dir=tmp_path,
            rate=rate,
            clip_specs=_CLIP_SPECS_SEC,
        )

    return _build


# ===========================================================================
# Family 2: synthetic loss-annotation fixture (AC-4)
# ===========================================================================

# Marker-kind losses (architecture §5.1): each is a marker whose
# metadata["clipwright"]["kind"] matches the kind used by the real satellite
# tool (verified by grep, not guessed):
#   caption          -> clipwright-transcribe/transcribe.py:663
#   text_overlay     -> clipwright-text/text.py:209
#   image_overlay    -> clipwright-overlay/overlay.py:276
#   pip_overlay      -> clipwright-overlay/overlay.py:848
#   bgm              -> clipwright-bgm/bgm.py:226
#   scene_boundary   -> clipwright-scene/detect.py:338-359 (excluded from
#                       loss counting: position is transcribed by both
#                       adapters per spike-report §7b)
#   widget_overlay   -> deliberately NOT a recognised clipwright kind, to
#                       exercise the ADR-EX-5 "other clipwright annotations"
#                       generic bucket.
_LOSS_MARKER_KINDS: tuple[str, ...] = (
    "caption",
    "text_overlay",
    "image_overlay",
    "pip_overlay",
    "bgm",
    "scene_boundary",
    "widget_overlay",
)

# Timeline-level directive losses. Despite architecture §5.1's table wording
# ("clip.metadata clipwright.kind or effect"), the *real* satellite tools do
# not store these on the clip: they write a directive dict under
# tl.metadata["clipwright"][<key>], mirroring transition.py's
# tl.metadata["clipwright"]["transition"] shape (verified by grep):
#   color     -> clipwright-color/color.py:322-324   (key "color")
#   denoise   -> clipwright-noise/noise.py:184-186    (key "denoise")
#   loudness  -> clipwright-loudness/loudness.py:249-251 (key "loudness")
#   stabilize -> clipwright-stabilize/stabilize.py:187-189 (key "stabilize")
# This fixture reproduces that real tl-level shape so _loss_report is tested
# against what the satellite tools actually emit, not the table's wording.
_DIRECTIVE_KEYS: tuple[str, ...] = ("color", "denoise", "loudness", "stabilize")


@dataclass(frozen=True)
class LossyFixture:
    """A saved OTIO timeline carrying one instance of every known loss kind."""

    otio_path: str
    rate: float


@pytest.fixture
def lossy_timeline_factory(tmp_path: Path) -> Callable[..., LossyFixture]:
    """Factory building the synthetic loss-annotation timeline described above."""

    def _build(rate: float = 30.0, *, name: str = "lossy") -> LossyFixture:
        media = tmp_path / "clip.mov"
        if not media.exists():
            _write_dummy_media(media)

        tl = new_timeline(name=f"export-{name}")
        v1 = tl.tracks[0]

        available = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=rate),
            duration=RationalTimeModel(value=3600.0 * rate, rate=rate),
        )
        ref = media_ref_for_otio(media, tmp_path)
        clip = add_clip(
            v1,
            MediaRef(target_url=ref, available_range=available),
            TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=rate),
                duration=RationalTimeModel(value=300.0 * rate, rate=rate),
            ),
            name="clip1",
        )

        for i, kind in enumerate(_LOSS_MARKER_KINDS):
            add_marker(
                v1,
                TimeRangeModel(
                    start_time=RationalTimeModel(value=i * rate, rate=rate),
                    duration=RationalTimeModel(value=0.0, rate=rate),
                ),
                name=f"{kind}_{i}",
                metadata={
                    "tool": "clipwright-test-fixture",
                    "version": "0.0.0",
                    "kind": kind,
                },
            )

        existing = get_clipwright_metadata(tl)
        for directive_key in _DIRECTIVE_KEYS:
            existing[directive_key] = {
                "tool": f"clipwright-{directive_key}",
                "version": "0.0.0",
                "kind": directive_key,
            }
        existing["transition"] = {
            "tool": "clipwright-transition",
            "version": "0.0.0",
            "kind": "transition",
            "transitions": [
                {"after_clip_index": 0, "type": "dissolve", "duration_sec": 0.5},
            ],
        }
        set_clipwright_metadata(tl, existing)

        # speed: LinearTimeWarp effect on the clip, clipwright kind=="speed"
        # (clipwright-speed/speed.py:184-196 shape).
        warp = otio.schema.LinearTimeWarp(name="clipwright_speed", time_scalar=2.0)
        set_clipwright_metadata(
            warp,
            {
                "tool": "clipwright-speed",
                "version": "0.0.0",
                "kind": "speed",
                "speed": 2.0,
            },
        )
        clip.effects.append(warp)

        otio_path = tmp_path / f"{name}.otio"
        save_timeline(tl, str(otio_path))
        return LossyFixture(otio_path=str(otio_path), rate=rate)

    return _build
