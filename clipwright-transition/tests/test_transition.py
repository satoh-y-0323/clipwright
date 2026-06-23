"""test_transition.py — Tests for transition.py orchestration layer.

Covers:
- count_video_clips: Gap skip / Transition rejection / multiple track rejection /
  zero track rejection.
- add_transition: output == input (INVALID_INPUT), output extension, output parent
  directory, non-destructive (input file unchanged), directive canonical form
  (existing directive preserved), FILE_NOT_FOUND, output boundary (PATH_NOT_ALLOWED),
  save_timeline failure (INTERNAL / fixed wording without tmp path exposure).
"""

from __future__ import annotations

import collections.abc
from pathlib import Path

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode

from clipwright_transition.schemas import AddTransitionOptions, TransitionSpec
from clipwright_transition.transition import add_transition, count_video_clips

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_clip(
    path: str,
    start_sec: float = 0.0,
    duration_sec: float = 10.0,
    rate: float = 30.0,
) -> otio.schema.Clip:
    """Create a minimal OTIO Clip with a known source range."""
    return otio.schema.Clip(
        name=Path(path).stem,
        media_reference=otio.schema.ExternalReference(target_url=path),
        source_range=otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(start_sec * rate, rate),
            duration=otio.opentime.RationalTime(duration_sec * rate, rate),
        ),
    )


def _two_clip_timeline(tmp_path: Path) -> otio.schema.Timeline:
    """Return a minimal two-clip OTIO timeline."""
    tl = otio.schema.Timeline(name="test_seq")
    track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    track.append(_make_clip(str(tmp_path / "clip_a.mp4")))
    track.append(_make_clip(str(tmp_path / "clip_b.mp4")))
    tl.tracks.append(track)
    return tl


def _write_timeline(tl: otio.schema.Timeline, path: Path) -> None:
    otio.adapters.write_to_file(tl, str(path))


def _uniform_opts(
    transition_type: str = "dissolve", duration: float = 0.5
) -> AddTransitionOptions:
    return AddTransitionOptions(
        uniform=TransitionSpec(type=transition_type, duration_sec=duration)
    )


# ---------------------------------------------------------------------------
# count_video_clips
# ---------------------------------------------------------------------------


class TestCountVideoClips:
    def test_counts_clips_in_two_clip_timeline(self, tmp_path: Path) -> None:
        tl = _two_clip_timeline(tmp_path)
        assert count_video_clips(tl) == 2

    def test_counts_clips_skipping_gaps(self, tmp_path: Path) -> None:
        """Gaps must be skipped; only Clips are counted."""
        tl = otio.schema.Timeline(name="gap_test")
        track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        track.append(_make_clip(str(tmp_path / "clip_a.mp4")))
        gap = otio.schema.Gap(
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(0, 30),
                duration=otio.opentime.RationalTime(30, 30),
            )
        )
        track.append(gap)
        track.append(_make_clip(str(tmp_path / "clip_b.mp4")))
        tl.tracks.append(track)
        assert count_video_clips(tl) == 2

    def test_rejects_existing_otio_transition(self, tmp_path: Path) -> None:
        """A timeline with an existing OTIO Transition must raise INVALID_INPUT."""
        tl = otio.schema.Timeline(name="transition_test")
        track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        track.append(_make_clip(str(tmp_path / "clip_a.mp4")))
        # Insert an OTIO Transition between clips.
        tr = otio.schema.Transition(
            in_offset=otio.opentime.RationalTime(15, 30),
            out_offset=otio.opentime.RationalTime(15, 30),
        )
        track.append(tr)
        track.append(_make_clip(str(tmp_path / "clip_b.mp4")))
        tl.tracks.append(track)

        with pytest.raises(ClipwrightError) as exc_info:
            count_video_clips(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT
        assert "Transition" in exc_info.value.message

    def test_rejects_multiple_video_tracks(self, tmp_path: Path) -> None:
        """Two video tracks must raise INVALID_INPUT."""
        tl = otio.schema.Timeline(name="multi_track")
        for name in ("V1", "V2"):
            track = otio.schema.Track(name=name, kind=otio.schema.TrackKind.Video)
            track.append(_make_clip(str(tmp_path / f"{name}_clip.mp4")))
            tl.tracks.append(track)

        with pytest.raises(ClipwrightError) as exc_info:
            count_video_clips(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT
        assert "two or more video tracks" in exc_info.value.message.lower()

    def test_rejects_zero_video_tracks(self) -> None:
        """A timeline with no video track must raise INVALID_INPUT."""
        tl = otio.schema.Timeline(name="no_video")
        audio_track = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
        tl.tracks.append(audio_track)

        with pytest.raises(ClipwrightError) as exc_info:
            count_video_clips(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_three_clips(self, tmp_path: Path) -> None:
        tl = otio.schema.Timeline(name="three_clips")
        track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        for i in range(3):
            track.append(_make_clip(str(tmp_path / f"clip_{i}.mp4")))
        tl.tracks.append(track)
        assert count_video_clips(tl) == 3


# ---------------------------------------------------------------------------
# add_transition — output == input (INVALID_INPUT)
# ---------------------------------------------------------------------------


class TestOutputEqualsInput:
    def test_same_path_raises_invalid_input(self, tmp_path: Path) -> None:
        tl = _two_clip_timeline(tmp_path)
        otio_path = tmp_path / "timeline.otio"
        _write_timeline(tl, otio_path)

        result = add_transition(
            timeline=str(otio_path),
            output=str(otio_path),
            options=_uniform_opts(),
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# add_transition — output extension check
# ---------------------------------------------------------------------------


class TestOutputExtension:
    def test_non_otio_extension_raises_invalid_input(self, tmp_path: Path) -> None:
        tl = _two_clip_timeline(tmp_path)
        otio_path = tmp_path / "timeline.otio"
        _write_timeline(tl, otio_path)

        result = add_transition(
            timeline=str(otio_path),
            output=str(tmp_path / "output.mp4"),
            options=_uniform_opts(),
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"
        assert ".otio" in result["error"]["hint"].lower()

    def test_otio_extension_accepted(self, tmp_path: Path) -> None:
        tl = _two_clip_timeline(tmp_path)
        otio_path = tmp_path / "timeline.otio"
        _write_timeline(tl, otio_path)

        result = add_transition(
            timeline=str(otio_path),
            output=str(tmp_path / "output.otio"),
            options=_uniform_opts(),
        )
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# add_transition — output parent directory check
# ---------------------------------------------------------------------------


class TestOutputParentDirectory:
    def test_missing_parent_dir_raises_invalid_input(self, tmp_path: Path) -> None:
        tl = _two_clip_timeline(tmp_path)
        otio_path = tmp_path / "timeline.otio"
        _write_timeline(tl, otio_path)

        result = add_transition(
            timeline=str(otio_path),
            output=str(tmp_path / "nonexistent_dir" / "output.otio"),
            options=_uniform_opts(),
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"
        assert "directory" in result["error"]["message"].lower()


# ---------------------------------------------------------------------------
# add_transition — FILE_NOT_FOUND
# ---------------------------------------------------------------------------


class TestFileNotFound:
    def test_missing_timeline_returns_file_not_found(self, tmp_path: Path) -> None:
        result = add_transition(
            timeline=str(tmp_path / "does_not_exist.otio"),
            output=str(tmp_path / "output.otio"),
            options=_uniform_opts(),
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "FILE_NOT_FOUND"
        # basename only (CWE-209): full path must not appear in message
        assert str(tmp_path) not in result["error"]["message"]

    def test_file_not_found_message_contains_filename(self, tmp_path: Path) -> None:
        result = add_transition(
            timeline=str(tmp_path / "missing_file.otio"),
            output=str(tmp_path / "output.otio"),
            options=_uniform_opts(),
        )
        assert result["ok"] is False
        assert "missing_file.otio" in result["error"]["message"]


# ---------------------------------------------------------------------------
# add_transition — non-destructive (input file unchanged)
# ---------------------------------------------------------------------------


class TestNonDestructive:
    def test_input_file_unchanged_after_add_transition(self, tmp_path: Path) -> None:
        """Input OTIO file must not be modified on disk."""
        tl = _two_clip_timeline(tmp_path)
        otio_path = tmp_path / "timeline.otio"
        _write_timeline(tl, otio_path)

        # Record original bytes.
        original_bytes = otio_path.read_bytes()

        result = add_transition(
            timeline=str(otio_path),
            output=str(tmp_path / "output.otio"),
            options=_uniform_opts(),
        )
        assert result["ok"] is True

        # Input file must be byte-identical after the call.
        assert otio_path.read_bytes() == original_bytes


# ---------------------------------------------------------------------------
# add_transition — directive canonical form
# ---------------------------------------------------------------------------


class TestDirectiveCanonicalForm:
    def test_uniform_directive_written_in_expanded_form(self, tmp_path: Path) -> None:
        """Uniform mode must be expanded to all boundaries in the directive."""
        tl = _two_clip_timeline(tmp_path)
        otio_path = tmp_path / "timeline.otio"
        _write_timeline(tl, otio_path)
        output_path = tmp_path / "output.otio"

        result = add_transition(
            timeline=str(otio_path),
            output=str(output_path),
            options=_uniform_opts(transition_type="dissolve", duration=0.5),
        )
        assert result["ok"] is True

        out_tl = otio.adapters.read_from_file(str(output_path))
        meta = out_tl.metadata.get("clipwright", {})
        # AnyDictionary -> work with Mapping
        assert isinstance(meta, collections.abc.Mapping)
        tr_directive = meta["transition"]
        assert tr_directive["kind"] == "transition"
        assert tr_directive["tool"] == "clipwright_add_transition"
        transitions = tr_directive["transitions"]
        assert len(transitions) == 1  # 2 clips -> 1 boundary
        assert transitions[0]["after_clip_index"] == 0
        assert transitions[0]["type"] == "dissolve"
        assert transitions[0]["duration_sec"] == 0.5

    def test_transitions_ascending_order(self, tmp_path: Path) -> None:
        """Transitions must be sorted ascending by after_clip_index."""
        # Build a three-clip timeline.
        tl = otio.schema.Timeline(name="three_clips")
        track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        for i in range(3):
            track.append(_make_clip(str(tmp_path / f"clip_{i}.mp4")))
        tl.tracks.append(track)
        otio_path = tmp_path / "timeline3.otio"
        _write_timeline(tl, otio_path)
        output_path = tmp_path / "output3.otio"

        result = add_transition(
            timeline=str(otio_path),
            output=str(output_path),
            options=_uniform_opts(),
        )
        assert result["ok"] is True

        out_tl = otio.adapters.read_from_file(str(output_path))
        meta = out_tl.metadata.get("clipwright", {})
        transitions = list(meta["transition"]["transitions"])
        indices = [t["after_clip_index"] for t in transitions]
        assert indices == sorted(indices)
        assert len(transitions) == 2  # 3 clips -> 2 boundaries

    def test_existing_directive_preserved(self, tmp_path: Path) -> None:
        """Pre-existing clipwright directives (e.g. reframe) must be preserved."""
        tl = _two_clip_timeline(tmp_path)
        # Write a pre-existing directive under "reframe" key.
        tl.metadata["clipwright"] = {"reframe": {"tool": "clipwright_reframe"}}
        otio_path = tmp_path / "timeline_with_directive.otio"
        _write_timeline(tl, otio_path)
        output_path = tmp_path / "output_with_directive.otio"

        result = add_transition(
            timeline=str(otio_path),
            output=str(output_path),
            options=_uniform_opts(),
        )
        assert result["ok"] is True

        out_tl = otio.adapters.read_from_file(str(output_path))
        meta = out_tl.metadata.get("clipwright", {})
        # Both keys must be present.
        assert "reframe" in meta
        assert "transition" in meta
        assert meta["reframe"]["tool"] == "clipwright_reframe"

    def test_directive_overwrites_previous_transition(self, tmp_path: Path) -> None:
        """Re-running add_transition replaces the transition directive only."""
        tl = _two_clip_timeline(tmp_path)
        tl.metadata["clipwright"] = {
            "transition": {
                "tool": "clipwright_add_transition",
                "version": "0.0.0",
                "kind": "transition",
                "transitions": [
                    {"after_clip_index": 0, "type": "fade", "duration_sec": 1.0}
                ],
            }
        }
        otio_path = tmp_path / "timeline_prev_transition.otio"
        _write_timeline(tl, otio_path)
        output_path = tmp_path / "output_replaced.otio"

        result = add_transition(
            timeline=str(otio_path),
            output=str(output_path),
            options=_uniform_opts(transition_type="dissolve", duration=0.3),
        )
        assert result["ok"] is True

        out_tl = otio.adapters.read_from_file(str(output_path))
        meta = out_tl.metadata.get("clipwright", {})
        tr = meta["transition"]
        assert tr["transitions"][0]["type"] == "dissolve"
        assert tr["transitions"][0]["duration_sec"] == 0.3


# ---------------------------------------------------------------------------
# add_transition — ok_result envelope shape (ADR-T-6)
# ---------------------------------------------------------------------------


class TestOkResultEnvelope:
    def test_ok_result_contains_boundary_count_and_mode(self, tmp_path: Path) -> None:
        tl = _two_clip_timeline(tmp_path)
        otio_path = tmp_path / "timeline.otio"
        _write_timeline(tl, otio_path)
        output_path = tmp_path / "output.otio"

        result = add_transition(
            timeline=str(otio_path),
            output=str(output_path),
            options=_uniform_opts(),
        )
        assert result["ok"] is True
        assert "1" in result["summary"] or "boundary" in result["summary"].lower()
        assert result["data"]["boundary_count"] == 1
        assert result["data"]["mode"] == "uniform"

    def test_output_artifact_path_matches(self, tmp_path: Path) -> None:
        tl = _two_clip_timeline(tmp_path)
        otio_path = tmp_path / "timeline.otio"
        _write_timeline(tl, otio_path)
        output_path = tmp_path / "output.otio"

        result = add_transition(
            timeline=str(otio_path),
            output=str(output_path),
            options=_uniform_opts(),
        )
        assert result["ok"] is True
        assert output_path.exists()
        artifact_path = result["artifacts"][0]["path"]
        assert artifact_path == str(output_path)


# ---------------------------------------------------------------------------
# add_transition — output boundary (SR L-3: PATH_NOT_ALLOWED)
# ---------------------------------------------------------------------------


class TestOutputBoundary:
    """SR L-3: output outside the timeline directory must return PATH_NOT_ALLOWED."""

    def test_output_outside_timeline_dir_returns_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """Output placed in a sibling directory must be rejected with PATH_NOT_ALLOWED."""
        # Create two separate sibling directories under tmp_path.
        timeline_dir = tmp_path / "project"
        timeline_dir.mkdir()
        other_dir = tmp_path / "other"
        other_dir.mkdir()

        tl = _two_clip_timeline(timeline_dir)
        otio_path = timeline_dir / "timeline.otio"
        _write_timeline(tl, otio_path)

        # Output points into other_dir, which is outside the timeline directory.
        result = add_transition(
            timeline=str(otio_path),
            output=str(other_dir / "output.otio"),
            options=_uniform_opts(),
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "PATH_NOT_ALLOWED"

    def test_output_in_same_dir_as_timeline_is_allowed(self, tmp_path: Path) -> None:
        """Output placed in the same directory as the timeline must succeed."""
        tl = _two_clip_timeline(tmp_path)
        otio_path = tmp_path / "timeline.otio"
        _write_timeline(tl, otio_path)

        result = add_transition(
            timeline=str(otio_path),
            output=str(tmp_path / "output.otio"),
            options=_uniform_opts(),
        )
        assert result["ok"] is True

    def test_output_in_subdirectory_of_timeline_dir_is_allowed(
        self, tmp_path: Path
    ) -> None:
        """Output placed in a subdirectory of the timeline directory must succeed."""
        sub = tmp_path / "sub"
        sub.mkdir()
        tl = _two_clip_timeline(tmp_path)
        otio_path = tmp_path / "timeline.otio"
        _write_timeline(tl, otio_path)

        result = add_transition(
            timeline=str(otio_path),
            output=str(sub / "output.otio"),
            options=_uniform_opts(),
        )
        assert result["ok"] is True

    def test_path_not_allowed_message_does_not_expose_full_path(
        self, tmp_path: Path
    ) -> None:
        """Error message must not expose the full filesystem path (CWE-209)."""
        timeline_dir = tmp_path / "project"
        timeline_dir.mkdir()
        other_dir = tmp_path / "other"
        other_dir.mkdir()

        tl = _two_clip_timeline(timeline_dir)
        otio_path = timeline_dir / "timeline.otio"
        _write_timeline(tl, otio_path)

        result = add_transition(
            timeline=str(otio_path),
            output=str(other_dir / "output.otio"),
            options=_uniform_opts(),
        )
        assert result["ok"] is False
        # Fixed wording must not include the full absolute path of other_dir.
        assert str(other_dir) not in result["error"]["message"]
        assert str(other_dir) not in result["error"].get("hint", "")


# ---------------------------------------------------------------------------
# add_transition — save_timeline failure (SR L-1: INTERNAL / no tmp path)
# ---------------------------------------------------------------------------


class TestSaveTimelineFailure:
    """SR L-1: non-ClipwrightError from save_timeline must be caught with fixed wording."""

    def test_save_timeline_exception_returns_internal_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OTIOError from save_timeline must surface as INTERNAL, not propagate."""
        import opentimelineio as otio_mod

        tl = _two_clip_timeline(tmp_path)
        otio_path = tmp_path / "timeline.otio"
        _write_timeline(tl, otio_path)

        # Monkeypatch save_timeline in the transition module to raise a non-ClipwrightError.
        def _raise_otioc_error(timeline_obj: object, path: str) -> None:
            raise otio_mod.exceptions.OTIOError("cannot write to /tmp/xxx.otio")

        import clipwright_transition.transition as transition_mod

        monkeypatch.setattr(transition_mod, "save_timeline", _raise_otioc_error)

        result = add_transition(
            timeline=str(otio_path),
            output=str(tmp_path / "output.otio"),
            options=_uniform_opts(),
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "INTERNAL"

    def test_save_timeline_exception_uses_fixed_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Error message must be the fixed wording, not the raw exception text."""
        import opentimelineio as otio_mod

        tl = _two_clip_timeline(tmp_path)
        otio_path = tmp_path / "timeline.otio"
        _write_timeline(tl, otio_path)

        fake_tmp = "/tmp/clipwright_abc123.otio"

        def _raise_with_path(timeline_obj: object, path: str) -> None:
            raise otio_mod.exceptions.OTIOError(
                f"Failed to serialize timeline to {fake_tmp}"
            )

        import clipwright_transition.transition as transition_mod

        monkeypatch.setattr(transition_mod, "save_timeline", _raise_with_path)

        result = add_transition(
            timeline=str(otio_path),
            output=str(tmp_path / "output.otio"),
            options=_uniform_opts(),
        )
        assert result["ok"] is False
        # Fixed wording must match the implementation constant.
        assert result["error"]["message"] == "Failed to write the output timeline."
        # Tmp path must NOT appear in either message or hint (CWE-209).
        assert fake_tmp not in result["error"]["message"]
        assert fake_tmp not in result["error"].get("hint", "")

    def test_save_timeline_generic_exception_also_caught(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Any non-ClipwrightError (e.g. PermissionError) is also caught."""
        tl = _two_clip_timeline(tmp_path)
        otio_path = tmp_path / "timeline.otio"
        _write_timeline(tl, otio_path)

        def _raise_permission(timeline_obj: object, path: str) -> None:
            raise PermissionError("Access is denied: /tmp/secret.otio")

        import clipwright_transition.transition as transition_mod

        monkeypatch.setattr(transition_mod, "save_timeline", _raise_permission)

        result = add_transition(
            timeline=str(otio_path),
            output=str(tmp_path / "output.otio"),
            options=_uniform_opts(),
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "INTERNAL"
        # Secret path must not be exposed.
        assert "/tmp/secret.otio" not in result["error"]["message"]
        assert "/tmp/secret.otio" not in result["error"].get("hint", "")
