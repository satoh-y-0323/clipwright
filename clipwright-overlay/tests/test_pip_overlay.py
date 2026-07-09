"""Tests for clipwright-overlay add_pip() core logic (PiP / video overlay).

All tests in this module verify the contract of add_pip() as defined in:
  - architecture-report-20260709-093022.md sec2.1 (module design) / sec3 (ADR-PIP-2..6)
  - ADR-PIP-2: OTIO representation -- marker kind="pip_overlay" on the V1 track only
    (no new track; render is the sole materialise point, same as image_overlay).
  - ADR-PIP-3: AddPipOptions field set + defaults (scale default 0.3, NOT 1.0 like
    image_overlay; media_start_sec/mix_audio/audio_volume/ducking are PiP-specific).
  - ADR-PIP-4: PipDuckingOptions/PipDuckingDirective are a *local* re-declaration of
    clipwright-bgm's DuckingOptions/DuckingDirective shape (enabled/threshold/ratio) --
    no cross-satellite-package import.
  - ADR-PIP-5: media_path must be a video file: extension in
    {".mp4", ".mkv", ".mov", ".webm"} AND inspect_media(media_path).streams must
    contain at least one codec_type=="video" entry (audio-only -> INVALID_INPUT,
    hint pointing at clipwright_add_bgm).
  - ADR-PIP-6: _MAX_PIP_OVERLAYS = 4 (DoS guard, mirrors V2-9's _MAX_IMAGE_OVERLAYS
    pattern but with a much lower cap since each PiP decodes a full video stream).

This file follows the two established sibling test files' style
(test_overlay.py / test_pathpolicy_overlay.py):
  - Self-contained helpers (no reliance on conftest.py fixtures) so this file does
    not need to touch conftest.py (shared across parallel wt_* tasks).
  - tmp dirs are created via tempfile.TemporaryDirectory() and always .resolve()'d
    immediately (macOS /tmp, /var symlink-prefix false-positive lesson, see
    MEMORY.md "validate_source_file x macOS /tmp/var symlink").
  - inspect_media is monkeypatched via unittest.mock.patch at
    "clipwright_overlay.overlay.inspect_media" (mirrors clipwright-bgm's
    test_bgm.py pattern: "clipwright_bgm.bgm.inspect_media") so tests never invoke
    a real ffprobe subprocess.
  - Symlink tests reuse the _try_symlink / _probe_symlink_support / _skip_no_symlinks
    pattern from test_pathpolicy_overlay.py, duplicated locally (that file does not
    export these as a shared module either).

RED PHASE: as of this writing, neither `AddPipOptions`/`PipDuckingOptions`
(clipwright_overlay.schemas) nor `add_pip`/`_MAX_PIP_OVERLAYS`
(clipwright_overlay.overlay) exist yet. The module-level imports below raise
ImportError/ModuleNotFoundError at collection time, so every test in this file
fails for the correct reason: the feature is not implemented yet (Red), not
because of a typo/syntax problem in the test itself. This mirrors the established
project pattern for Red-phase tests that target not-yet-existing symbols (see
MEMORY.md "fit/counter-scale plan テストパターン" / "subtitle plan/schemas
テストパターン": "ImportError で Red").
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo
from pydantic import ValidationError

from clipwright_overlay.overlay import _MAX_PIP_OVERLAYS, add_pip
from clipwright_overlay.schemas import AddPipOptions, PipDuckingOptions

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RATE = 24.0

# Allowed video extensions per ADR-PIP-5 (mirrors clipwright-render's
# _ALLOWED_EXTENSIONS for the same container set).
_ALLOWED_VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".webm")
_DISALLOWED_VIDEO_EXTENSIONS = (".avi", ".gif", ".mp3", ".txt")


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_clip(
    name: str, duration_sec: float = 10.0, rate: float = _RATE
) -> otio.schema.Clip:
    """Build a simple Clip with ExternalReference and source_range."""
    ref = otio.schema.ExternalReference(target_url=f"file:///media/{name}.mp4")
    sr = otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0.0, rate),
        duration=otio.opentime.RationalTime(duration_sec * rate, rate),
    )
    return otio.schema.Clip(name=name, media_reference=ref, source_range=sr)


def _make_v1_timeline(n_clips: int = 1, rate: float = _RATE) -> otio.schema.Timeline:
    """Return a timeline with a V1 video track containing n_clips clips."""
    tl = otio.schema.Timeline(name="test_tl")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(v1)
    tl.tracks.append(a1)
    for i in range(n_clips):
        v1.append(_make_clip(f"clip{i}", rate=rate))
    return tl


def _write_timeline(tl: otio.schema.Timeline, path: Path) -> None:
    otio.adapters.write_to_file(tl, str(path))


def _read_timeline(path: Path) -> otio.schema.Timeline:
    return otio.adapters.read_from_file(str(path))  # type: ignore[no-any-return]


def _get_pip_overlay_markers(tl: otio.schema.Timeline) -> list[otio.schema.Marker]:
    """Return all pip_overlay markers from the first Video track."""
    for track in tl.tracks:
        if track.kind == otio.schema.TrackKind.Video:
            return [
                m
                for m in track.markers
                if m.metadata.get("clipwright", {}).get("kind") == "pip_overlay"
            ]
    return []


def _write_dummy_video(path: Path) -> None:
    """Write placeholder bytes to the given path (content is irrelevant --
    inspect_media is always mocked in these tests, so no real video decoder
    ever inspects these bytes)."""
    path.write_bytes(b"FAKE_MP4_CONTAINER_BYTES")


def _video_media_info(
    has_video: bool = True, duration_sec: float = 5.0, rate: float = _RATE
) -> MediaInfo:
    """Build a MediaInfo mock for a PiP source: video+audio streams by default."""
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(
            StreamInfo(
                index=0,
                codec_type="video",
                codec_name="h264",
                width=640,
                height=360,
            )
        )
    streams.append(
        StreamInfo(
            index=1 if has_video else 0,
            codec_type="audio",
            codec_name="aac",
            sample_rate=48000,
            channels=2,
        )
    )
    return MediaInfo(
        path="pip.mp4",
        container="mp4",
        duration=RationalTimeModel(value=duration_sec * rate, rate=rate),
        streams=streams,
        bit_rate=1_000_000,
    )


def _audio_only_media_info(duration_sec: float = 5.0) -> MediaInfo:
    """Build a MediaInfo mock with NO video stream (audio-only source)."""
    return _video_media_info(has_video=False, duration_sec=duration_sec)


def _default_opts(media: Path, **overrides: object) -> AddPipOptions:
    """Return AddPipOptions with valid required fields plus overrides.

    fade_in_sec/fade_out_sec are pinned to 0.0 (rather than the schema
    defaults of 0.3) so tests that don't specifically exercise fades aren't
    accidentally constrained by a fade-sum <= duration_sec rule (mirrors
    test_overlay.py's _default_opts rationale).
    """
    base: dict[str, object] = {
        "media_path": str(media),
        "start_sec": 1.0,
        "duration_sec": 3.0,
        "fade_in_sec": 0.0,
        "fade_out_sec": 0.0,
    }
    base.update(overrides)
    return AddPipOptions(**base)  # type: ignore[arg-type]


def _try_symlink(link: Path, target: Path) -> None:
    """Create a symlink; skip the test if the OS refuses (Windows privilege).

    Same fallback as test_pathpolicy_overlay.py::_try_symlink: local Windows
    dev machines lack symlink-creation privilege (WinError 1314) so this
    SKIPs locally; CI (3 OS) actually exercises the symlink-rejection branch.
    """
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip(
            "Cannot create symlinks on this system (requires elevated privileges)"
        )


def _probe_symlink_support() -> bool:
    """Return True when the runtime environment allows symlink creation."""
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        real = base / "_probe_real.txt"
        real.write_bytes(b"probe")
        link = base / "_probe_link.txt"
        try:
            link.symlink_to(real)
        except OSError:
            return False
    return True


_SYMLINK_SUPPORTED: bool = _probe_symlink_support()
_SKIP_SYMLINK_REASON = (
    "Symlink creation requires elevated privileges on this system (WinError 1314)."
    " Enable Windows Developer Mode or run as Administrator."
)
_skip_no_symlinks = pytest.mark.skipif(
    not _SYMLINK_SUPPORTED,
    reason=_SKIP_SYMLINK_REASON,
)


# ===========================================================================
# AddPipOptions: required fields
# ===========================================================================


class TestRequiredFields:
    """media_path/start_sec/duration_sec are required; omitting any raises."""

    def test_missing_media_path_raises(self) -> None:
        with pytest.raises(ValidationError):
            AddPipOptions(start_sec=1.0, duration_sec=3.0)  # type: ignore[call-arg]

    def test_missing_start_sec_raises(self) -> None:
        with pytest.raises(ValidationError):
            AddPipOptions(media_path="pip.mp4", duration_sec=3.0)  # type: ignore[call-arg]

    def test_missing_duration_sec_raises(self) -> None:
        with pytest.raises(ValidationError):
            AddPipOptions(media_path="pip.mp4", start_sec=1.0)  # type: ignore[call-arg]

    def test_all_required_fields_present_constructs_ok(self) -> None:
        opts = AddPipOptions(media_path="pip.mp4", start_sec=1.0, duration_sec=3.0)
        assert opts.media_path == "pip.mp4"
        assert opts.start_sec == pytest.approx(1.0)
        assert opts.duration_sec == pytest.approx(3.0)


# ===========================================================================
# AddPipOptions: default values (ADR-PIP-3)
# ===========================================================================


class TestDefaultValues:
    """Optional fields must default exactly per ADR-PIP-3.

    Note scale defaults to 0.3 here (PiP), NOT 1.0 like AddOverlayOptions
    (image overlay) -- this is the one field where the two schemas diverge
    on default value, called out explicitly in the architecture report.
    """

    def test_scale_default_is_0_3(self) -> None:
        opts = AddPipOptions(media_path="pip.mp4", start_sec=1.0, duration_sec=3.0)
        assert opts.scale == pytest.approx(0.3)

    def test_opacity_default_is_1_0(self) -> None:
        opts = AddPipOptions(media_path="pip.mp4", start_sec=1.0, duration_sec=3.0)
        assert opts.opacity == pytest.approx(1.0)

    def test_fade_in_sec_default_is_0_3(self) -> None:
        opts = AddPipOptions(media_path="pip.mp4", start_sec=1.0, duration_sec=3.0)
        assert opts.fade_in_sec == pytest.approx(0.3)

    def test_fade_out_sec_default_is_0_3(self) -> None:
        opts = AddPipOptions(media_path="pip.mp4", start_sec=1.0, duration_sec=3.0)
        assert opts.fade_out_sec == pytest.approx(0.3)

    def test_media_start_sec_default_is_0_0(self) -> None:
        opts = AddPipOptions(media_path="pip.mp4", start_sec=1.0, duration_sec=3.0)
        assert opts.media_start_sec == pytest.approx(0.0)

    def test_mix_audio_default_is_false(self) -> None:
        opts = AddPipOptions(media_path="pip.mp4", start_sec=1.0, duration_sec=3.0)
        assert opts.mix_audio is False

    def test_audio_volume_default_is_1_0(self) -> None:
        opts = AddPipOptions(media_path="pip.mp4", start_sec=1.0, duration_sec=3.0)
        assert opts.audio_volume == pytest.approx(1.0)

    def test_ducking_default_disabled(self) -> None:
        opts = AddPipOptions(media_path="pip.mp4", start_sec=1.0, duration_sec=3.0)
        assert isinstance(opts.ducking, PipDuckingOptions)
        assert opts.ducking.enabled is False

    def test_ducking_default_threshold_and_ratio(self) -> None:
        """PipDuckingOptions mirrors clipwright-bgm's DuckingOptions shape
        (ADR-PIP-4): default threshold=0.05, ratio=4.0."""
        opts = AddPipOptions(media_path="pip.mp4", start_sec=1.0, duration_sec=3.0)
        assert opts.ducking.threshold == pytest.approx(0.05)
        assert opts.ducking.ratio == pytest.approx(4.0)

    def test_x_default_centered(self) -> None:
        opts = AddPipOptions(media_path="pip.mp4", start_sec=1.0, duration_sec=3.0)
        assert opts.x == "(W-w)/2"

    def test_y_default_centered(self) -> None:
        opts = AddPipOptions(media_path="pip.mp4", start_sec=1.0, duration_sec=3.0)
        assert opts.y == "(H-h)/2"


# ===========================================================================
# Happy path: V1 pip_overlay marker + metadata
# ===========================================================================


class TestHappyPath:
    """Valid co-located video source -> ok ToolResult + pip_0 marker + metadata."""

    def test_happy_path_ok_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd).resolve()
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            media = tmp / "pip.mp4"
            _write_dummy_video(media)

            opts = _default_opts(media, start_sec=1.0, duration_sec=3.0)
            with patch(
                "clipwright_overlay.overlay.inspect_media",
                return_value=_video_media_info(),
            ):
                result = add_pip(str(inp), str(out), opts)

            assert result["ok"] is True, f"Expected ok=True, got: {result.get('error')}"

    def test_happy_path_marker_name_pip_0(self) -> None:
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd).resolve()
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            media = tmp / "pip.mp4"
            _write_dummy_video(media)

            opts = _default_opts(media)
            with patch(
                "clipwright_overlay.overlay.inspect_media",
                return_value=_video_media_info(),
            ):
                result = add_pip(str(inp), str(out), opts)

            assert result["ok"] is True
            out_tl = _read_timeline(out)
            markers = _get_pip_overlay_markers(out_tl)
            assert len(markers) == 1
            assert markers[0].name == "pip_0", (
                f"Expected name='pip_0', got {markers[0].name!r}"
            )

    def test_happy_path_metadata_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd).resolve()
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            media = tmp / "pip.mp4"
            _write_dummy_video(media)

            opts = _default_opts(
                media,
                start_sec=2.0,
                duration_sec=4.0,
                media_start_sec=1.5,
                x="(W-w)/2",
                y="(H-h)/2",
                scale=0.4,
                opacity=0.9,
                fade_in_sec=0.3,
                fade_out_sec=0.3,
                mix_audio=True,
                audio_volume=1.5,
                ducking=PipDuckingOptions(enabled=True, threshold=0.1, ratio=6.0),
            )
            with patch(
                "clipwright_overlay.overlay.inspect_media",
                return_value=_video_media_info(),
            ):
                result = add_pip(str(inp), str(out), opts)

            assert result["ok"] is True, f"Got error: {result.get('error')}"
            out_tl = _read_timeline(out)
            markers = _get_pip_overlay_markers(out_tl)
            assert len(markers) == 1
            cw = markers[0].metadata.get("clipwright", {})

            assert cw.get("tool") == "clipwright-overlay"
            assert cw.get("kind") == "pip_overlay"
            assert "version" in cw
            assert cw.get("start_sec") == pytest.approx(2.0)
            assert cw.get("duration_sec") == pytest.approx(4.0)
            assert cw.get("media_start_sec") == pytest.approx(1.5)
            assert cw.get("x") == "(W-w)/2"
            assert cw.get("y") == "(H-h)/2"
            assert cw.get("scale") == pytest.approx(0.4)
            assert cw.get("opacity") == pytest.approx(0.9)
            assert cw.get("fade_in_sec") == pytest.approx(0.3)
            assert cw.get("fade_out_sec") == pytest.approx(0.3)
            assert cw.get("mix_audio") is True
            assert cw.get("audio_volume") == pytest.approx(1.5)
            ducking_meta = cw.get("ducking") or {}
            assert ducking_meta.get("enabled") is True
            assert ducking_meta.get("threshold") == pytest.approx(0.1)
            assert ducking_meta.get("ratio") == pytest.approx(6.0)

    def test_happy_path_media_path_stored_via_media_ref_for_otio(self) -> None:
        """media_path is stored via media_ref_for_otio: relative posix when the
        source is co-located under the output OTIO's parent directory (mirrors
        image_overlay's image_path storage rule, ADR-PP-1)."""
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd).resolve()
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            media = tmp / "pip.mp4"
            _write_dummy_video(media)

            opts = _default_opts(media)
            with patch(
                "clipwright_overlay.overlay.inspect_media",
                return_value=_video_media_info(),
            ):
                result = add_pip(str(inp), str(out), opts)

            assert result["ok"] is True
            out_tl = _read_timeline(out)
            markers = _get_pip_overlay_markers(out_tl)
            assert len(markers) == 1
            stored = markers[0].metadata["clipwright"]["media_path"]

            assert not Path(str(stored)).is_absolute(), (
                f"media_path must be relative when co-located; got: {stored!r}"
            )
            expected_rel = Path(
                os.path.relpath(media.resolve(), out.resolve().parent)
            ).as_posix()
            assert stored == expected_rel, (
                f"Expected relative posix {expected_rel!r}, got {stored!r}"
            )


# ===========================================================================
# Accumulation + idempotency
# ===========================================================================


class TestAccumulationAndIdempotency:
    """Distinct calls accumulate pip_0/pip_1; identical re-application is a no-op."""

    def test_two_distinct_pips_accumulate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd).resolve()
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            mid = tmp / "mid.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            media1 = tmp / "pip1.mp4"
            media2 = tmp / "pip2.mp4"
            _write_dummy_video(media1)
            _write_dummy_video(media2)

            with patch(
                "clipwright_overlay.overlay.inspect_media",
                return_value=_video_media_info(),
            ):
                opts1 = _default_opts(media1, start_sec=1.0, duration_sec=2.0)
                r1 = add_pip(str(inp), str(mid), opts1)
                assert r1["ok"] is True, f"First call failed: {r1.get('error')}"

                opts2 = _default_opts(media2, start_sec=4.0, duration_sec=2.0)
                r2 = add_pip(str(mid), str(out), opts2)
                assert r2["ok"] is True, f"Second call failed: {r2.get('error')}"

            out_tl = _read_timeline(out)
            markers = _get_pip_overlay_markers(out_tl)
            assert len(markers) == 2
            names = {m.name for m in markers}
            assert names == {"pip_0", "pip_1"}, f"Expected pip_0/pip_1, got {names}"

    def test_idempotent_call_returns_applied_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd).resolve()
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            mid = tmp / "mid.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            media = tmp / "pip.mp4"
            _write_dummy_video(media)

            with patch(
                "clipwright_overlay.overlay.inspect_media",
                return_value=_video_media_info(),
            ):
                opts = _default_opts(media, start_sec=1.0, duration_sec=3.0)
                r1 = add_pip(str(inp), str(mid), opts)
                assert r1["ok"] is True, f"First call failed: {r1.get('error')}"

                opts2 = _default_opts(media, start_sec=1.0, duration_sec=3.0)
                r2 = add_pip(str(mid), str(out), opts2)

            assert r2["ok"] is True, (
                f"Idempotent call must still return ok=True: {r2.get('error')}"
            )
            data = r2.get("data") or {}
            assert data.get("applied") == 0, (
                f"Duplicate call must return applied=0, got {data.get('applied')!r}"
            )
            warnings = r2.get("warnings") or []
            assert len(warnings) > 0, "Duplicate call must emit at least one warning"


# ===========================================================================
# _MAX_PIP_OVERLAYS cap (ADR-PIP-6)
# ===========================================================================


class TestMaxPipOverlaysCap:
    """Adding a pip_overlay marker beyond _MAX_PIP_OVERLAYS(=4) must fail."""

    def test_max_overlays_constant_is_4(self) -> None:
        assert _MAX_PIP_OVERLAYS == 4

    def test_exceeding_cap_returns_invalid_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd).resolve()
            media = tmp / "pip.mp4"
            _write_dummy_video(media)

            tl = _make_v1_timeline()
            v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
            for n in range(_MAX_PIP_OVERLAYS):
                marker = otio.schema.Marker(
                    name=f"pip_{n}",
                    marked_range=otio.opentime.TimeRange(
                        start_time=otio.opentime.RationalTime(float(n), _RATE),
                        duration=otio.opentime.RationalTime(_RATE, _RATE),
                    ),
                    metadata={
                        "clipwright": {
                            "tool": "clipwright-overlay",
                            "version": "0.1.0",
                            "kind": "pip_overlay",
                            "media_path": "pip.mp4",
                            "start_sec": float(n),
                            "duration_sec": 1.0,
                            "media_start_sec": 0.0,
                            "x": "(W-w)/2",
                            "y": "(H-h)/2",
                            "scale": 0.3,
                            "opacity": 1.0,
                            "fade_in_sec": 0.0,
                            "fade_out_sec": 0.0,
                            "mix_audio": False,
                            "audio_volume": 1.0,
                            "ducking": {
                                "enabled": False,
                                "threshold": 0.05,
                                "ratio": 4.0,
                            },
                        }
                    },
                )
                v1.markers.append(marker)

            inp = tmp / f"in_{_MAX_PIP_OVERLAYS}.otio"
            out = tmp / f"out_{_MAX_PIP_OVERLAYS + 1}.otio"
            _write_timeline(tl, inp)

            opts = _default_opts(media, start_sec=10.0, duration_sec=1.0)
            with patch(
                "clipwright_overlay.overlay.inspect_media",
                return_value=_video_media_info(),
            ):
                result = add_pip(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT beyond cap {_MAX_PIP_OVERLAYS}, "
                f"got {error.get('code')!r}"
            )
            assert error.get("hint"), "hint must be non-empty for cap violation"


# ===========================================================================
# Video extension allowlist (ADR-PIP-5)
# ===========================================================================


class TestVideoExtensionAllowlist:
    """media_path extension must be in {.mp4, .mkv, .mov, .webm}."""

    @pytest.mark.parametrize("ext", _DISALLOWED_VIDEO_EXTENSIONS)
    def test_disallowed_extension_returns_invalid_input(self, ext: str) -> None:
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd).resolve()
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            bad_file = tmp / f"pip{ext}"
            _write_dummy_video(bad_file)

            opts = _default_opts(bad_file)
            with patch(
                "clipwright_overlay.overlay.inspect_media",
                return_value=_video_media_info(),
            ):
                result = add_pip(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT for {ext}, got {error.get('code')!r}"
            )
            assert error.get("hint")

    @pytest.mark.parametrize("ext", _ALLOWED_VIDEO_EXTENSIONS)
    def test_allowed_extension_accepted(self, ext: str) -> None:
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd).resolve()
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            good_file = tmp / f"pip{ext}"
            _write_dummy_video(good_file)

            opts = _default_opts(good_file)
            with patch(
                "clipwright_overlay.overlay.inspect_media",
                return_value=_video_media_info(),
            ):
                result = add_pip(str(inp), str(out), opts)

            assert result["ok"] is True, (
                f"Extension {ext} must be allowed, got: {result.get('error')}"
            )


# ===========================================================================
# Audio-only media_path rejection (ADR-PIP-5)
# ===========================================================================


class TestAudioOnlyMediaRejected:
    """media_path with no video stream -> INVALID_INPUT, hint points at add_bgm."""

    def test_audio_only_media_returns_invalid_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd).resolve()
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            # Extension is a valid video container, but the stream contents
            # (mocked below) contain no video stream.
            media = tmp / "audio_only.mp4"
            _write_dummy_video(media)

            opts = _default_opts(media)
            with patch(
                "clipwright_overlay.overlay.inspect_media",
                return_value=_audio_only_media_info(),
            ):
                result = add_pip(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT for audio-only media_path, "
                f"got {error.get('code')!r}"
            )
            hint = error.get("hint") or ""
            assert "clipwright_add_bgm" in hint, (
                f"hint must direct the caller to clipwright_add_bgm for "
                f"audio-only sources; got: {hint!r}"
            )


# ===========================================================================
# x/y allowlist
# ===========================================================================


class TestXYAllowlist:
    """x/y allowlist: forbids `: ; [ ] , '` and control chars (same as image_overlay)."""

    @pytest.mark.parametrize(
        "field,bad_value",
        [
            ("x", "(W-w):2"),
            ("x", "(W;w)/2"),
            ("x", "[W-w]/2"),
            ("x", "(W-w])/2"),
            ("x", "(W-w)/2,0"),
            ("x", "(W-w)'/2"),
            ("x", "(W-w)\x00/2"),
            ("y", "(H-h):2"),
            ("y", "(H-h)'/2"),
            ("y", "(H-h)\n/2"),
        ],
    )
    def test_invalid_xy_returns_invalid_input(self, field: str, bad_value: str) -> None:
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd).resolve()
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            media = tmp / "pip.mp4"
            _write_dummy_video(media)

            opts = _default_opts(media, **{field: bad_value})
            with patch(
                "clipwright_overlay.overlay.inspect_media",
                return_value=_video_media_info(),
            ):
                result = add_pip(str(inp), str(out), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "INVALID_INPUT", (
                f"Expected INVALID_INPUT for {field}={bad_value!r}, "
                f"got {error.get('code')!r}"
            )
            assert error.get("hint")

    @pytest.mark.parametrize(
        "field,good_value",
        [
            ("x", "(W-w)/2"),
            ("x", "main_w-overlay_w-10"),
            ("x", "0"),
            ("y", "(H-h)/2"),
            ("y", "main_h-overlay_h-10"),
        ],
    )
    def test_valid_xy_accepted(self, field: str, good_value: str) -> None:
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd).resolve()
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)
            media = tmp / "pip.mp4"
            _write_dummy_video(media)

            opts = _default_opts(media, **{field: good_value})
            with patch(
                "clipwright_overlay.overlay.inspect_media",
                return_value=_video_media_info(),
            ):
                result = add_pip(str(inp), str(out), opts)

            assert result["ok"] is True, (
                f"Valid {field}={good_value!r} must be accepted, "
                f"got error: {result.get('error')}"
            )


# ===========================================================================
# output == timeline (check_output_not_source)
# ===========================================================================


class TestOutputEqualsTimeline:
    """output path identical to timeline path must return PATH_NOT_ALLOWED."""

    def test_output_equals_timeline_returns_path_not_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd).resolve()
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            _write_timeline(tl, inp)
            media = tmp / "pip.mp4"
            _write_dummy_video(media)

            opts = _default_opts(media)
            with patch(
                "clipwright_overlay.overlay.inspect_media",
                return_value=_video_media_info(),
            ):
                result = add_pip(str(inp), str(inp), opts)

            assert result["ok"] is False
            error = result.get("error") or {}
            assert error.get("code") == "PATH_NOT_ALLOWED", (
                f"Expected PATH_NOT_ALLOWED when output==timeline, "
                f"got {error.get('code')!r}"
            )
            assert error.get("hint")


# ===========================================================================
# media_path symlink rejection (CWE-59)
# ===========================================================================


class TestMediaPathSymlinkRejected:
    """media_path containing a symlink component must be rejected.

    Mirrors test_pathpolicy_overlay.py::TestImagePathSymlinkRejected: overlay's
    field validation is expected to delegate existence/symlink checks to
    clipwright.pathpolicy.validate_source_or_basename (ADR-PP-2:
    islink-before-resolve), which raises PATH_NOT_ALLOWED for any symlink path
    component, checked leaf-to-root before resolve().

    Local Windows dev machines lack the privilege to create symlinks
    (WinError 1314) -> SKIP locally; CI (3 OS) exercises this branch
    (MEMORY.md "symlinkテストはローカルSKIP/CIで実行").
    """

    @_skip_no_symlinks
    def test_media_path_leaf_symlink_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd).resolve()
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            real_media = tmp / "real_pip.mp4"
            _write_dummy_video(real_media)
            link_media = tmp / "link_pip.mp4"
            _try_symlink(link_media, real_media)

            opts = _default_opts(link_media)
            with patch(
                "clipwright_overlay.overlay.inspect_media",
                return_value=_video_media_info(),
            ):
                result = add_pip(str(inp), str(out), opts)

            assert result["ok"] is False, (
                f"symlinked media_path must be rejected; got ok=True, "
                f"data={result.get('data')!r}"
            )
            error = result.get("error") or {}
            assert error.get("code") == "PATH_NOT_ALLOWED", (
                f"symlinked media_path must return PATH_NOT_ALLOWED; "
                f"got {error.get('code')!r}"
            )
            assert str(tmp) not in (error.get("message") or ""), (
                f"message must not expose the directory path (CWE-209); "
                f"got {error.get('message')!r}"
            )

    @_skip_no_symlinks
    def test_media_path_intermediate_dir_symlink_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpd:
            tmp = Path(tmpd).resolve()
            tl = _make_v1_timeline()
            inp = tmp / "in.otio"
            out = tmp / "out.otio"
            _write_timeline(tl, inp)

            real_dir = tmp / "real_assets"
            real_dir.mkdir()
            real_media = real_dir / "pip.mp4"
            _write_dummy_video(real_media)

            sym_dir = tmp / "sym_assets"
            _try_symlink(sym_dir, real_dir)

            opts = _default_opts(sym_dir / "pip.mp4")
            with patch(
                "clipwright_overlay.overlay.inspect_media",
                return_value=_video_media_info(),
            ):
                result = add_pip(str(inp), str(out), opts)

            assert result["ok"] is False, (
                f"media_path through a symlinked directory must be rejected; "
                f"got ok=True, data={result.get('data')!r}"
            )
            error = result.get("error") or {}
            assert error.get("code") == "PATH_NOT_ALLOWED", (
                f"symlinked intermediate dir must return PATH_NOT_ALLOWED; "
                f"got {error.get('code')!r}"
            )
            assert str(tmp) not in (error.get("message") or ""), (
                f"message must not expose the directory path (CWE-209); "
                f"got {error.get('message')!r}"
            )
