"""test_bgm_relpath_regression.py — Regression guard for co-located BGM sources.

Bug (task_id: fix2-bgm-relpath, found by the Resolve NLE e2e smoke):
  ``clipwright-bgm``'s ``media_ref_for_otio`` stores a *relative* POSIX
  target_url (e.g. ``"bgm.wav"``) when the BGM media and the output .otio share
  a directory (co-location optimisation). ``clipwright-render`` resolved the
  main-media and image-overlay relative refs to absolute paths before handing
  them to ffmpeg, but the BGM boundary-validation block only *validated* the
  relative BGM ref without writing the resolved absolute path back onto the
  (frozen) ``BgmClip``. As a result ``plan.bgm_source`` reached ffmpeg as a
  bare relative ``-i bgm.wav``; ffmpeg runs with ``cwd=None`` (see
  ``clipwright.process.run``), so the path failed to open and render crashed
  with ``SUBPROCESS_FAILED``.

These tests pin the fix: the BGM ``-i`` argument that reaches ffmpeg must be an
absolute path resolved against the timeline directory, even when the OTIO
stores a relative co-located BGM ref.

The primary test needs no real ffmpeg (it stubs the probe + subprocess runner
and inspects the assembled command string). A real-ffmpeg e2e variant repeats
the check through an actual render.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import opentimelineio as otio
import pytest

from clipwright_render.plan import ProbeInfo
from clipwright_render.render import render_timeline
from clipwright_render.schemas import RenderOptions

_RATE = 25.0
_MAIN_DUR = 2.0
_BGM_DUR = 1.0
_BGM_RATE = 48000.0


def _bgm_directive() -> dict[str, Any]:
    """Return a BgmDirective-equivalent metadata dict (ADR-B3/B9-r2)."""
    return {
        "tool": "clipwright-bgm",
        "version": "0.1.0",
        "kind": "bgm",
        "volume_db": -6.0,
        "fade_in_sec": 0.0,
        "fade_out_sec": 0.0,
        "ducking": {"enabled": False, "threshold": 0.05, "ratio": 4.0},
    }


def _build_colocated_bgm_timeline(
    main_url: str,
    bgm_url: str,
) -> otio.schema.Timeline:
    """Build a V1(main) + A2(BGM) timeline where the media refs use the given
    (possibly relative) target_urls.

    ``bgm_url`` is stored verbatim as the BGM ExternalReference target_url so
    that callers can reproduce the co-located relative-path case
    (media_ref_for_otio stores ``"bgm.wav"`` when bgm and .otio share a dir).
    """
    main_ref = otio.schema.ExternalReference(target_url=main_url)
    main_clip = otio.schema.Clip(
        name="main",
        media_reference=main_ref,
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, _RATE),
            duration=otio.opentime.RationalTime(_MAIN_DUR * _RATE, _RATE),
        ),
    )
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    v1.append(main_clip)

    bgm_ref = otio.schema.ExternalReference(target_url=bgm_url)
    bgm_clip = otio.schema.Clip(
        name="bgm",
        media_reference=bgm_ref,
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, _BGM_RATE),
            duration=otio.opentime.RationalTime(_BGM_DUR * _BGM_RATE, _BGM_RATE),
        ),
        metadata={"clipwright": _bgm_directive()},
    )
    a2 = otio.schema.Track(name="A2", kind=otio.schema.TrackKind.Audio)
    a2.append(bgm_clip)

    timeline = otio.schema.Timeline(name="bgm_relpath_regression")
    timeline.tracks.append(v1)
    timeline.tracks.append(a2)
    return timeline


def test_colocated_relative_bgm_reaches_ffmpeg_as_absolute_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A co-located BGM stored as a relative OTIO ref must reach ffmpeg as an
    absolute ``-i`` argument (regression guard; no real ffmpeg required).

    The probe and subprocess runner are stubbed so the assembled ffmpeg command
    can be inspected without executing a real encode.
    """
    # Co-located layout: main.mp4, bgm.wav and timeline.otio share a directory.
    main_file = tmp_path / "main.mp4"
    bgm_file = tmp_path / "bgm.wav"
    main_file.write_bytes(b"\x00")  # existence check only; probe is stubbed
    bgm_file.write_bytes(b"\x00")

    # Store the BGM ref as a bare relative POSIX path (co-location optimisation),
    # matching what clipwright-bgm's media_ref_for_otio writes.
    timeline = _build_colocated_bgm_timeline(
        main_url="main.mp4",
        bgm_url="bgm.wav",
    )
    timeline_path = tmp_path / "timeline.otio"
    otio.adapters.write_to_file(timeline, str(timeline_path))

    # Stub the source probe (avoids the real ffprobe dependency).
    def _fake_probe(_source: str) -> ProbeInfo:
        return ProbeInfo(
            has_video=True,
            audio_count=1,
            bit_rate=None,
            width=320,
            height=240,
            fps=_RATE,
        )

    monkeypatch.setattr("clipwright_render.render._probe", _fake_probe)
    monkeypatch.setattr(
        "clipwright_render.render.resolve_tool", lambda _tool, _env: "ffmpeg"
    )

    captured: dict[str, list[str]] = {}

    def _fake_run(cmd: list[str], **_kwargs: Any) -> None:
        captured["cmd"] = list(cmd)
        # render_timeline verifies the output exists after run() returns.
        Path(cmd[-1]).write_bytes(b"\x00")

    monkeypatch.setattr("clipwright_render.render.run", _fake_run)

    out_path = tmp_path / "out.mp4"
    result = render_timeline(
        str(timeline_path), str(out_path), RenderOptions(), dry_run=False
    )
    assert result["ok"] is True, f"render failed: {result}"

    cmd = captured["cmd"]
    # BGM input is emitted as: -stream_loop -1 -i <bgm_source> (ADR-B6-r2).
    assert "-stream_loop" in cmd, f"BGM -stream_loop not found in cmd: {cmd}"
    sl_idx = cmd.index("-stream_loop")
    # -stream_loop -1 -i <path> : the BGM path is 3 tokens after -stream_loop.
    assert cmd[sl_idx + 1] == "-1"
    assert cmd[sl_idx + 2] == "-i"
    bgm_arg = cmd[sl_idx + 3]

    assert Path(bgm_arg).is_absolute(), (
        "BGM source reached ffmpeg as a relative path; the co-located relative "
        f"OTIO ref was not resolved to an absolute path (regression). arg={bgm_arg!r}"
    )
    assert Path(bgm_arg) == bgm_file.resolve(), (
        "BGM absolute path does not match the timeline-directory-resolved file. "
        f"got={bgm_arg!r} expected={bgm_file.resolve()!r}"
    )


# ===========================================================================
# Real-ffmpeg e2e variant (skipped when ffmpeg/ffprobe are absent)
# ===========================================================================


def _find_binary(name: str, env_var: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    env_val = os.environ.get(env_var)
    if env_val and Path(env_val).is_file():
        return env_val
    return None


_FFMPEG = _find_binary("ffmpeg", "CLIPWRIGHT_FFMPEG")
_FFPROBE = _find_binary("ffprobe", "CLIPWRIGHT_FFPROBE")

requires_ffmpeg = pytest.mark.skipif(
    _FFMPEG is None or _FFPROBE is None,
    reason=(
        "ffmpeg/ffprobe not found. Add them to PATH or set CLIPWRIGHT_FFMPEG / "
        "CLIPWRIGHT_FFPROBE."
    ),
)


@pytest.mark.e2e
@requires_ffmpeg
def test_colocated_relative_bgm_renders_ok_real_ffmpeg(tmp_path: Path) -> None:
    """Real ffmpeg render of a co-located relative BGM ref succeeds (regression
    for the SUBPROCESS_FAILED reported by the NLE e2e smoke)."""
    assert _FFMPEG is not None
    import subprocess

    main_file = tmp_path / "main.mp4"
    bgm_file = tmp_path / "bgm.wav"

    subprocess.run(
        [
            _FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=320x240:rate={int(_RATE)}:duration={_MAIN_DUR}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=48000:duration={_MAIN_DUR}",
            "-t",
            str(_MAIN_DUR),
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-pix_fmt",
            "yuv420p",
            str(main_file),
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=True,
    )
    subprocess.run(
        [
            _FFMPEG,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=880:sample_rate=48000:duration={_BGM_DUR}",
            "-t",
            str(_BGM_DUR),
            str(bgm_file),
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=True,
    )

    # Co-located relative BGM ref (the failing case).
    timeline = _build_colocated_bgm_timeline(
        main_url="main.mp4",
        bgm_url="bgm.wav",
    )
    timeline_path = tmp_path / "timeline.otio"
    otio.adapters.write_to_file(timeline, str(timeline_path))

    out_path = tmp_path / "out.mp4"
    result = render_timeline(
        str(timeline_path), str(out_path), RenderOptions(), dry_run=False
    )
    assert result["ok"] is True, f"render failed: {result}"
    assert out_path.exists() and out_path.stat().st_size > 0
