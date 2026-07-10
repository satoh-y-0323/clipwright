"""adapter_probe.py — Wave 0 spike for clipwright-export adapter constraints.

Temporary investigation script (delete or promote to tests later). Measures the
real write/read behaviour of the OpenTimelineIO ``cmx_3600`` (EDL) and
``fcpx_xml`` (FCPXML) adapters against a sequence-built V1(Video)+A1(Audio)
timeline, so that timeline_export's schema / warnings / track pre-processing can
be finalised on measured facts rather than assumptions (architecture §8.2/§8.3).

Run inside clipwright-export via ``uv run`` (bare python resolves a stale core):
    uv run python spikes/adapter_probe.py

All prints use utf-8 (cp932 guard on Windows).
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

import opentimelineio as otio

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


def _rt(value: float, rate: float) -> otio.opentime.RationalTime:
    return otio.opentime.RationalTime(value=value, rate=rate)


def build_timeline(
    rate: float,
    *,
    with_audio: bool,
    media_urls: tuple[str, str] = ("clipA.mov", "clipB.mov"),
) -> otio.schema.Timeline:
    """Build a 2-clip V1 (+ optional A1) timeline with explicit source_range.

    Each clip carries an ExternalReference with an available_range wide enough to
    contain the source_range so round-trip in/out can be verified.
    """
    tl = otio.schema.Timeline(name="spike")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    tl.tracks.append(v1)
    a1: otio.schema.Track | None = None
    if with_audio:
        a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
        tl.tracks.append(a1)

    # Clip A: source in [10, 10+40) ; Clip B: source in [100, 100+55)
    specs = [
        (media_urls[0], 10.0, 40.0),
        (media_urls[1], 100.0, 55.0),
    ]
    for url, start, dur in specs:
        ref = otio.schema.ExternalReference(target_url=url)
        ref.available_range = otio.opentime.TimeRange(
            start_time=_rt(0.0, rate),
            duration=_rt(3600.0 * rate, rate),  # 1h of available media
        )
        source_range = otio.opentime.TimeRange(
            start_time=_rt(start * rate, rate),
            duration=_rt(dur * rate, rate),
        )
        clip = otio.schema.Clip(
            name=Path(url).stem,
            media_reference=ref,
            source_range=source_range,
        )
        v1.append(clip)
        if a1 is not None:
            a_ref = otio.schema.ExternalReference(target_url=url)
            a_ref.available_range = otio.opentime.TimeRange(
                start_time=_rt(0.0, rate),
                duration=_rt(3600.0 * rate, rate),
            )
            a_clip = otio.schema.Clip(
                name=Path(url).stem + "_audio",
                media_reference=a_ref,
                source_range=otio.opentime.TimeRange(
                    start_time=_rt(start * rate, rate),
                    duration=_rt(dur * rate, rate),
                ),
            )
            a1.append(a_clip)
    return tl


def _clip_ranges(tl: otio.schema.Timeline) -> list[tuple[str, float, float, float]]:
    """Return (track_kind, start_value, dur_value, rate) for every clip on V tracks."""
    out: list[tuple[str, float, float, float]] = []
    for track in tl.tracks:
        for item in track:
            if isinstance(item, otio.schema.Clip):
                sr = item.source_range
                out.append(
                    (
                        track.kind,
                        sr.start_time.value,
                        sr.duration.value,
                        sr.start_time.rate,
                    )
                )
    return out


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def probe_availability() -> None:
    section("(1) adapter availability")
    names = otio.adapters.available_adapter_names()
    print("cmx_3600 present:", "cmx_3600" in names)
    print("fcpx_xml present:", "fcpx_xml" in names)


def probe_write(adapter: str, tl: otio.schema.Timeline, label: str) -> Path | None:
    """Try to write and report exception (if any). Returns path on success."""
    suffix = ".edl" if adapter == "cmx_3600" else ".fcpxml"
    out = Path(tempfile.mkstemp(suffix=suffix)[1])
    try:
        otio.adapters.write_to_file(tl, str(out), adapter_name=adapter)
        print(f"  [{label}] write OK -> {out.name} ({out.stat().st_size} bytes)")
        return out
    except Exception as exc:  # noqa: BLE001 - spike: capture every failure mode
        print(f"  [{label}] write RAISED {type(exc).__name__}: {exc}")
        return None


def probe_a1(rate: float) -> None:
    section("(2) V1+A1 write behaviour (EDL single-track bias)")
    tl_va = build_timeline(rate, with_audio=True)
    print(f" V1+A1 timeline, rate={rate}")
    edl = probe_write("cmx_3600", tl_va, "EDL V1+A1")
    if edl is not None:
        text = edl.read_text(encoding="utf-8", errors="replace")
        print("  --- EDL head (V1+A1 input) ---")
        for line in text.splitlines()[:14]:
            print("   |", line)
        # count event records to see whether audio clips were emitted
        nevents = sum(1 for ln in text.splitlines() if ln.strip()[:3].isdigit())
        print(f"  EDL event-numbered lines: {nevents}")
    fcp = probe_write("fcpx_xml", tl_va, "FCPXML V1+A1")
    if fcp is not None:
        text = fcp.read_text(encoding="utf-8", errors="replace")
        has_audio = "audio" in text.lower()
        print(f"  FCPXML mentions 'audio': {has_audio}")


def probe_roundtrip(rate: float, label: str) -> None:
    section(f"(3)/(4) round-trip in/out — rate={rate} ({label})")
    tl = build_timeline(rate, with_audio=False)  # V1 only for clean comparison
    print(" input V1 clip ranges (kind,start,dur,rate):")
    for r in _clip_ranges(tl):
        print("   ", r)
    for adapter, suffix in (("cmx_3600", ".edl"), ("fcpx_xml", ".fcpxml")):
        out = Path(tempfile.mkstemp(suffix=suffix)[1])
        try:
            otio.adapters.write_to_file(tl, str(out), adapter_name=adapter)
            back = otio.adapters.read_from_file(str(out), adapter_name=adapter)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{adapter}] round-trip RAISED {type(exc).__name__}: {exc}")
            continue
        print(f"  [{adapter}] re-read clip ranges (kind,start,dur,rate):")
        in_ranges = [r for r in _clip_ranges(tl) if r[0] == otio.schema.TrackKind.Video]
        out_ranges = [
            r
            for r in _clip_ranges(back)
            if r[0] == otio.schema.TrackKind.Video
        ]
        for r in out_ranges:
            print("    ", r)
        # compare in seconds (rate may differ after round-trip)
        print("  --- in/out delta (seconds) ---")
        for i, (a, b) in enumerate(zip(in_ranges, out_ranges, strict=False)):
            in_start_s = a[1] / a[3]
            in_dur_s = a[2] / a[3]
            out_start_s = b[1] / b[3]
            out_dur_s = b[2] / b[3]
            print(
                f"    clip{i}: start dsec={out_start_s - in_start_s:+.6f} "
                f"dur dsec={out_dur_s - in_dur_s:+.6f} "
                f"(in_rate={a[3]} out_rate={b[3]})"
            )


def probe_fcpxml_attrs(rate: float) -> None:
    section("(5) FCPXML format/version/frameDuration attributes")
    tl = build_timeline(rate, with_audio=False)
    out = Path(tempfile.mkstemp(suffix=".fcpxml")[1])
    try:
        otio.adapters.write_to_file(tl, str(out), adapter_name="fcpx_xml")
    except Exception as exc:  # noqa: BLE001
        print(f"  FCPXML write RAISED {type(exc).__name__}: {exc}")
        return
    text = out.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        s = line.strip()
        if any(
            k in s
            for k in ("<?xml", "<fcpxml", "<format", "frameDuration", "<asset ")
        ):
            print("   |", s[:160])


def probe_absolute_urls(rate: float) -> None:
    section("(6) absolute POSIX target_url -> EDL reel / FCPXML asset path")
    abs_url = "C:/Users/shoma/media/clipA.mov"
    abs_url_b = "C:/Users/shoma/media/clipB.mov"
    tl = build_timeline(rate, with_audio=False, media_urls=(abs_url, abs_url_b))
    for adapter, suffix in (("cmx_3600", ".edl"), ("fcpx_xml", ".fcpxml")):
        out = Path(tempfile.mkstemp(suffix=suffix)[1])
        try:
            otio.adapters.write_to_file(tl, str(out), adapter_name=adapter)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{adapter}] RAISED {type(exc).__name__}: {exc}")
            continue
        text = out.read_text(encoding="utf-8", errors="replace")
        print(f"  --- {adapter} lines referencing the path ---")
        for line in text.splitlines():
            low = line.lower()
            if "clipa" in low or "clipb" in low or "reel" in low or "shoma" in low:
                print("   |", line.strip()[:160])


def probe_error_structures(rate: float) -> None:
    section("(7) error-triggering structures (empty / gap-only / marker)")

    # empty timeline (no tracks)
    empty = otio.schema.Timeline(name="empty")
    for adapter in ("cmx_3600", "fcpx_xml"):
        try:
            out = tempfile.mkstemp(suffix=".x")[1]
            otio.adapters.write_to_file(empty, out, adapter_name=adapter)
            print(f"  [empty/{adapter}] wrote OK")
        except Exception as exc:  # noqa: BLE001
            print(f"  [empty/{adapter}] RAISED {type(exc).__name__}: {exc}")

    # V1 with a single Gap only
    gap_tl = otio.schema.Timeline(name="gaponly")
    gv = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    gap_tl.tracks.append(gv)
    gv.append(
        otio.schema.Gap(
            source_range=otio.opentime.TimeRange(
                start_time=_rt(0, rate), duration=_rt(10 * rate, rate)
            )
        )
    )
    for adapter in ("cmx_3600", "fcpx_xml"):
        try:
            out = tempfile.mkstemp(suffix=".x")[1]
            otio.adapters.write_to_file(gap_tl, out, adapter_name=adapter)
            print(f"  [gap-only/{adapter}] wrote OK")
        except Exception as exc:  # noqa: BLE001
            print(f"  [gap-only/{adapter}] RAISED {type(exc).__name__}: {exc}")

    # timeline with a marker on a clip
    marked = build_timeline(rate, with_audio=False)
    first_clip = next(
        item for item in marked.tracks[0] if isinstance(item, otio.schema.Clip)
    )
    marker = otio.schema.Marker(
        name="scene_1",
        marked_range=otio.opentime.TimeRange(
            start_time=_rt(0, rate), duration=_rt(0, rate)
        ),
    )
    marker.metadata["clipwright"] = {"kind": "scene_boundary"}
    first_clip.markers.append(marker)
    for adapter, suffix in (("cmx_3600", ".edl"), ("fcpx_xml", ".fcpxml")):
        try:
            out = tempfile.mkstemp(suffix=suffix)[1]
            otio.adapters.write_to_file(marked, out, adapter_name=adapter)
            text = Path(out).read_text(encoding="utf-8", errors="replace")
            transcribed = "scene_1" in text or "marker" in text.lower()
            print(f"  [marker/{adapter}] wrote OK; marker transcribed: {transcribed}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [marker/{adapter}] RAISED {type(exc).__name__}: {exc}")


def probe_marker_detail(rate: float) -> None:
    section("(7b) marker transcription detail (literal name / tag)")
    marked = build_timeline(rate, with_audio=False)
    first_clip = next(
        item for item in marked.tracks[0] if isinstance(item, otio.schema.Clip)
    )
    marker = otio.schema.Marker(
        name="scene_1",
        marked_range=otio.opentime.TimeRange(
            start_time=_rt(0, rate), duration=_rt(0, rate)
        ),
    )
    marker.metadata["clipwright"] = {"kind": "scene_boundary"}
    first_clip.markers.append(marker)
    for adapter, suffix in (("cmx_3600", ".edl"), ("fcpx_xml", ".fcpxml")):
        out = tempfile.mkstemp(suffix=suffix)[1]
        otio.adapters.write_to_file(marked, out, adapter_name=adapter)
        text = Path(out).read_text(encoding="utf-8", errors="replace")
        literal = "scene_1" in text
        tag = "<marker" in text.lower() or "* loc:" in text.lower()
        print(f"  [{adapter}] literal 'scene_1' present: {literal}; marker tag: {tag}")
        # re-read and count markers preserved
        try:
            back = otio.adapters.read_from_file(out, adapter_name=adapter)
            nmark = sum(
                len(item.markers)
                for track in back.tracks
                for item in track
                if isinstance(item, otio.schema.Clip)
            )
            print(f"  [{adapter}] markers after re-read: {nmark}")
        except Exception as exc:  # noqa: BLE001
            print(f"  [{adapter}] re-read RAISED {type(exc).__name__}: {exc}")


def probe_multi_video(rate: float) -> None:
    section("(7c) EDL with 2 video tracks")
    tl = otio.schema.Timeline(name="multiv")
    for tname in ("V1", "V2"):
        v = otio.schema.Track(name=tname, kind=otio.schema.TrackKind.Video)
        tl.tracks.append(v)
        ref = otio.schema.ExternalReference(target_url="clipA.mov")
        ref.available_range = otio.opentime.TimeRange(
            start_time=_rt(0.0, rate), duration=_rt(3600.0 * rate, rate)
        )
        v.append(
            otio.schema.Clip(
                name="clipA",
                media_reference=ref,
                source_range=otio.opentime.TimeRange(
                    start_time=_rt(10 * rate, rate), duration=_rt(40 * rate, rate)
                ),
            )
        )
    for adapter in ("cmx_3600", "fcpx_xml"):
        try:
            out = tempfile.mkstemp(suffix=".x")[1]
            otio.adapters.write_to_file(tl, out, adapter_name=adapter)
            print(f"  [2-video/{adapter}] wrote OK")
        except Exception as exc:  # noqa: BLE001
            print(f"  [2-video/{adapter}] RAISED {type(exc).__name__}: {exc}")


def probe_fractional_frame(rate: float) -> None:
    section(f"(4b) sub-second frame offsets — EDL rate-24 renorm risk (rate={rate})")
    # source_range not aligned to whole seconds: start=10.5s, dur=40.25s
    tl = otio.schema.Timeline(name="frac")
    v = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    tl.tracks.append(v)
    ref = otio.schema.ExternalReference(target_url="clipA.mov")
    ref.available_range = otio.opentime.TimeRange(
        start_time=_rt(0.0, rate), duration=_rt(3600.0 * rate, rate)
    )
    start_s, dur_s = 10.5, 40.25
    v.append(
        otio.schema.Clip(
            name="clipA",
            media_reference=ref,
            source_range=otio.opentime.TimeRange(
                start_time=_rt(start_s * rate, rate),
                duration=_rt(dur_s * rate, rate),
            ),
        )
    )
    for adapter, suffix in (("cmx_3600", ".edl"), ("fcpx_xml", ".fcpxml")):
        out = tempfile.mkstemp(suffix=suffix)[1]
        try:
            otio.adapters.write_to_file(tl, out, adapter_name=adapter)
            back = otio.adapters.read_from_file(out, adapter_name=adapter)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{adapter}] RAISED {type(exc).__name__}: {exc}")
            continue
        for track in back.tracks:
            for item in track:
                if isinstance(item, otio.schema.Clip):
                    sr = item.source_range
                    os_s = sr.start_time.value / sr.start_time.rate
                    od_s = sr.duration.value / sr.duration.rate
                    print(
                        f"  [{adapter}] in(start={start_s},dur={dur_s}) "
                        f"out(start={os_s:.5f},dur={od_s:.5f}) "
                        f"dstart={os_s - start_s:+.5f} ddur={od_s - dur_s:+.5f} "
                        f"out_rate={sr.start_time.rate}"
                    )


def main() -> None:
    print("OTIO version:", otio.__version__)
    probe_availability()
    probe_a1(30.0)
    probe_roundtrip(30.0, "integer 30")
    probe_roundtrip(25.0, "integer 25")
    probe_roundtrip(23.976, "non-integer 23.976")
    probe_roundtrip(29.97, "non-integer 29.97")
    probe_fractional_frame(30.0)
    probe_fcpxml_attrs(30.0)
    probe_fcpxml_attrs(23.976)
    probe_absolute_urls(30.0)
    probe_error_structures(30.0)
    probe_marker_detail(30.0)
    probe_multi_video(30.0)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - spike: surface full traceback
        traceback.print_exc()
        sys.exit(1)
