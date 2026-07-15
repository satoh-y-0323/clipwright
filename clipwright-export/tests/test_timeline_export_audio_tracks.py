"""test_timeline_export_audio_tracks.py — Multi-audio-track defense (FR-8 / ADR-NI-7).

Target function: export_timeline(timeline, output, options) -> ToolResult (boundary).

Spec source of truth (task instruction, most authoritative first):
  - spike-report-nle-adapters.md (measured facts): cmx_3600 (EDL) raises
    NotSupportedError for any timeline with more than 2 Audio tracks; no
    workaround exists at write time. FCPXML has no such limit and round-trips
    up to 8+ Audio tracks intact. global_start_time is accepted (write does
    not error) by both adapters but is lost on FCPXML round-trip; the audio
    track-count gate is hit *before* global_start_time is ever considered.
  - architecture-report-20260715-191151.md ADR-NI-7 + "spike-adapters 裁定"
    (§9 amendment, in the requirements-report §6 rationale): the architect
    ruling is to remove ALL Audio tracks from the write-time deep copy before
    an EDL write (not "keep up to 2" — that alternative was rejected because
    "which 2 to keep" is arbitrary and conflicts with the existing warning
    wording "were not written to the EDL", which already implies none are
    written).
  - requirements-report-20260715-190935.md FR-8, AC-9.

Implementation state (FR-8 shipped): the EDL path in timeline_export.py
(_export_timeline_inner step 6c) removes ALL Audio tracks from the
write-time deep copy (tl_copy) before calling _write_adapter, per the
architect's "remove ALL" ruling (not "keep up to 2" — see rationale above).
Before this was implemented, _write_adapter's
otio.adapters.write_to_file(..., adapter_name="cmx_3600") call raised
otio.exceptions.NotSupportedError for any timeline with more than 2 Audio
tracks, which the existing except otio.exceptions.OTIOError handler
converted to ClipwrightError(OTIO_ERROR) — i.e. the whole export failed
(ok=False) instead of succeeding with an audio-dropped warning. All tests
below now exercise the shipped behavior and serve as regression guards
against that failure mode recurring.

Verification aspects:
  (1) 8 Audio tracks (+ V1 clip): EDL export succeeds (ok=True) with a
      warning naming the dropped audio tracks. Was Red (ok=False,
      OTIO_ERROR) before FR-8 was implemented; now a regression guard.
  (2) The EDL output file exists and is re-readable via the same adapter
      (write-then-verify passed), for the same 8-track case. Was Red for
      the same underlying reason as (1) (no output file was ever produced);
      now a regression guard.
  (3) Exactly 2 Audio tracks (within cmx_3600's own native limit): the
      *actual* otio.adapters.write_to_file(...) call must be given a
      timeline with ZERO Audio tracks, locking in the architect's "remove
      ALL" ruling rather than an alternative "keep up to 2" implementation
      (which would also make the export succeed, but for the wrong reason).
      Was Red: the pre-FR-8 code passed tl_copy through with its 2 Audio
      tracks intact, since cmx_3600 natively accepts up to 2; now a
      regression guard on the "remove ALL" ruling specifically.
  (4) FCPXML with 8 Audio tracks + global_start_time: export succeeds and
      all 8 Audio tracks round-trip intact (spike Fact 2). This exercises a
      code path FR-8 does not touch (architecture: "FCPXML は無変更 + 回帰
      テスト") — passed before FR-8 and remains a regression guard.
  (5) EDL with 8 Audio tracks + global_start_time (spike configs (c)/(d)):
      export still succeeds despite global_start_time being present,
      confirming the audio-track gate is what blocks/permits the write
      (spike note: "Failure occurs before adapter checks global_start_time").
      Was Red for the same reason as (1); now a regression guard.
  (6) Non-destructive input: after an EDL export that removes Audio tracks
      on the write-time deep copy, the *source* .otio file on disk is
      unaffected — reloading it still shows all original Audio tracks.
      Passed before FR-8 and remains a regression guard (the removal must
      happen only on tl_copy, never on the loaded/source object).
"""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio
import pytest
from clipwright.otio_utils import load_timeline, save_timeline
from clipwright.pathpolicy import media_ref_for_otio

from clipwright_export.schemas import ExportTimelineOptions
from clipwright_export.timeline_export import export_timeline

_MEDIA_BYTES = b"clipwright-export audio-track-defense fixture bytes"

# 01:00:00:00 expressed in *rate* units, mirroring spike-report config (c)/(d).
_ONE_HOUR_SEC = 3600.0


def _build_multi_audio_timeline(
    tmp_path: Path,
    *,
    n_audio: int,
    rate: float = 30.0,
    with_global_start: bool = False,
    name: str = "multi_audio",
) -> str:
    """Build+save a V1(1 clip) + A1..An(1 clip each) timeline; return its path.

    Mirrors conftest.py's roundtrip_timeline_factory shape (real on-disk
    dummy media file, OTIO-dir-relative POSIX target_url via
    media_ref_for_otio) but supports an arbitrary Audio track count, which
    that shared fixture (fixed at A1) cannot. A dedicated helper is used
    instead of extending conftest.py, per the task's writes-file scope
    (this test file only).
    """
    media = tmp_path / f"{name}_media.mov"
    if not media.exists():
        media.write_bytes(_MEDIA_BYTES)
    ref_str = media_ref_for_otio(media, tmp_path)

    def _make_clip(clip_name: str) -> otio.schema.Clip:
        ref = otio.schema.ExternalReference(target_url=ref_str)
        ref.available_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, rate),
            duration=otio.opentime.RationalTime(_ONE_HOUR_SEC * rate, rate),
        )
        return otio.schema.Clip(
            name=clip_name,
            media_reference=ref,
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0.0, rate),
                duration=otio.opentime.RationalTime(10.0 * rate, rate),
            ),
        )

    tl = otio.schema.Timeline(name=f"export-{name}")

    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    v1.append(_make_clip("video_clip"))
    tl.tracks.append(v1)

    for i in range(1, n_audio + 1):
        track = otio.schema.Track(name=f"A{i}", kind=otio.schema.TrackKind.Audio)
        track.append(_make_clip(f"audio_clip_{i}"))
        tl.tracks.append(track)

    if with_global_start:
        tl.global_start_time = otio.opentime.RationalTime(_ONE_HOUR_SEC * rate, rate)

    otio_path = tmp_path / f"{name}.otio"
    save_timeline(tl, str(otio_path))
    return str(otio_path)


def _audio_track_count(tl: otio.schema.Timeline) -> int:
    return sum(1 for t in tl.tracks if t.kind == otio.schema.TrackKind.Audio)


# ===========================================================================
# (1)/(2) 8 Audio tracks: EDL export succeeds with a warning + readable output
# ===========================================================================


class TestEightAudioTracksEdlSucceeds:
    def test_eight_audio_tracks_edl_succeeds_with_warning(self, tmp_path: Path) -> None:
        otio_path = _build_multi_audio_timeline(
            tmp_path, n_audio=8, rate=30.0, name="eight_audio_warn"
        )
        out = tmp_path / "eight_audio_warn.edl"

        result = export_timeline(
            timeline=otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )

        # Was Red: cmx_3600 raised NotSupportedError for >2 Audio tracks, which
        # the OTIOError handler converted to OTIO_ERROR (ok=False). FR-8 now
        # removes all Audio tracks from the write-time copy before calling
        # write_to_file, eliminating the error.
        assert result.ok is True, result.error
        joined = " ".join(result.warnings).lower()
        assert "were not written to the edl" in joined
        assert "8 audio track" in joined

    def test_eight_audio_tracks_edl_output_is_readable_after_write(
        self, tmp_path: Path
    ) -> None:
        otio_path = _build_multi_audio_timeline(
            tmp_path, n_audio=8, rate=30.0, name="eight_audio_readable"
        )
        out = tmp_path / "eight_audio_readable.edl"

        result = export_timeline(
            timeline=otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )

        # Was Red for the same underlying reason as above: no output file was
        # produced because the write raised NotSupportedError. FR-8 now ensures
        # the write succeeds by removing Audio tracks before calling write_to_file.
        assert result.ok is True, result.error
        assert out.exists(), (
            "write-then-verify (ADR-EX-11) must leave the artifact in place"
        )

        back = otio.adapters.read_from_file(
            str(out), adapter_name="cmx_3600", rate=30.0
        )
        assert isinstance(back, otio.schema.Timeline)


# ===========================================================================
# (3) 2 Audio tracks (within cmx_3600's own limit): "remove ALL" is locked in
# ===========================================================================


class TestAudioTrackRemovalScopeIsAll:
    """Locks in the architect ruling (requirements-report §6 rationale):
    ALL Audio tracks must be stripped from the write-time deep copy before
    an EDL write, even when the input Audio-track count (2) would itself
    fit within cmx_3600's native limit. A "keep up to 2" implementation
    would also make this export succeed, but must not pass this test.
    """

    def test_two_audio_tracks_are_fully_removed_before_write(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        otio_path = _build_multi_audio_timeline(
            tmp_path, n_audio=2, rate=30.0, name="two_audio_scope"
        )
        out = tmp_path / "two_audio_scope.edl"

        captured: list[otio.schema.Timeline] = []
        original_write = otio.adapters.write_to_file

        def _spy_write(tl: otio.schema.Timeline, output: str, **kwargs: object) -> None:
            captured.append(tl)
            original_write(tl, output, **kwargs)

        monkeypatch.setattr(otio.adapters, "write_to_file", _spy_write)

        result = export_timeline(
            timeline=otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )

        assert result.ok is True, result.error
        assert captured, "expected otio.adapters.write_to_file to have been called"
        written_tl = captured[0]
        remaining_audio = _audio_track_count(written_tl)
        # Was Red: the implementation did not remove Audio tracks from tl_copy,
        # so remaining_audio was 2 instead of 0. FR-8 now removes all Audio
        # tracks before write_to_file is called, satisfying the architect ruling.
        assert remaining_audio == 0, (
            "ADR-NI-7 mandates removing ALL Audio tracks from the deep copy "
            "before an EDL write (architect ruling: full removal, not "
            f"'keep up to 2'); found {remaining_audio} Audio track(s) still "
            "present in the timeline passed to write_to_file"
        )

        joined = " ".join(result.warnings).lower()
        assert "were not written to the edl" in joined


# ===========================================================================
# (4) FCPXML regression: 8 Audio tracks + global_start_time still succeed
# ===========================================================================


class TestFcpxmlEightAudioTracksRegression:
    def test_fcpxml_eight_audio_tracks_with_global_start_time_succeeds(
        self, tmp_path: Path
    ) -> None:
        otio_path = _build_multi_audio_timeline(
            tmp_path,
            n_audio=8,
            rate=30.0,
            with_global_start=True,
            name="fcpxml_eight_gst",
        )
        out = tmp_path / "fcpxml_eight_gst.fcpxml"

        result = export_timeline(
            timeline=otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="fcpxml"),
        )

        # Expected to already pass (architecture: "FCPXML は無変更"); this is
        # a regression guard, not a Red-driving test.
        assert result.ok is True, result.error
        assert out.exists()

        back = otio.adapters.read_from_file(str(out), adapter_name="fcpx_xml")
        assert _audio_track_count(back) == 8, (
            "FCPXML must preserve all 8 Audio tracks on round-trip "
            "(spike-report Fact 2)"
        )


# ===========================================================================
# (5) EDL + global_start_time (spike configs (c)/(d)): still succeeds
# ===========================================================================


class TestEdlWithGlobalStartTime:
    def test_edl_eight_audio_tracks_with_global_start_time_succeeds(
        self, tmp_path: Path
    ) -> None:
        otio_path = _build_multi_audio_timeline(
            tmp_path,
            n_audio=8,
            rate=30.0,
            with_global_start=True,
            name="edl_eight_gst",
        )
        out = tmp_path / "edl_eight_gst.edl"

        result = export_timeline(
            timeline=otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )

        # Was Red: same underlying NotSupportedError as (1). The spike confirmed
        # that the audio-track gate is hit before global_start_time is ever
        # considered. FR-8 ensures export succeeds by removing Audio tracks
        # before write_to_file is called, regardless of global_start_time.
        assert result.ok is True, result.error
        assert out.exists()
        joined = " ".join(result.warnings).lower()
        assert "were not written to the edl" in joined


# ===========================================================================
# (6) Non-destructive input: source .otio Audio tracks survive an EDL export
# ===========================================================================


class TestInputTimelineNonDestructive:
    def test_input_timeline_audio_tracks_unaffected_by_edl_export(
        self, tmp_path: Path
    ) -> None:
        otio_path = _build_multi_audio_timeline(
            tmp_path, n_audio=8, rate=30.0, name="nondestructive_audio"
        )
        out = tmp_path / "nondestructive_audio.edl"

        before_bytes = Path(otio_path).read_bytes()

        # This assertion is independent of whether FR-8 has landed yet: the
        # input file must never be modified by export_timeline regardless of
        # whether the EDL write itself ends up succeeding or failing, so it
        # is not gated on result.ok.
        export_timeline(
            timeline=otio_path,
            output=str(out),
            options=ExportTimelineOptions(format="edl"),
        )

        after_bytes = Path(otio_path).read_bytes()
        assert before_bytes == after_bytes, (
            "the input OTIO file must never be modified by export (AC-3)"
        )

        reread = load_timeline(otio_path)
        assert _audio_track_count(reread) == 8, (
            "ADR-NI-7's Audio-track removal must happen only on the "
            "write-time deep copy; the source .otio's Audio tracks must "
            "remain intact on disk"
        )
