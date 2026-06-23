"""test_e2e_transition.py — In-process e2e tests for add_transition.

Exercises add_transition directly (no MCP transport, no ffprobe/ffmpeg).
Verifies the full observable contract:
  - envelope shape (ok / summary / data / artifacts / warnings)
  - artifact OTIO exists on disk after a successful call
  - metadata["clipwright"]["transition"] canonical form (ascending after_clip_index)
  - non-destructive: input file bytes are unchanged after the call
  - INVALID_INPUT paths: Clip<2, output==input

How to run:
  cd clipwright-transition
  uv run pytest tests/test_e2e_transition.py -v
"""

from __future__ import annotations

import collections.abc
from pathlib import Path

import opentimelineio as otio
import pytest

from clipwright_transition.schemas import (
    AddTransitionOptions,
    BoundaryTransition,
    TransitionSpec,
)
from clipwright_transition.transition import add_transition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uniform_opts(
    transition_type: str = "dissolve",
    duration: float = 0.5,
) -> AddTransitionOptions:
    """Return AddTransitionOptions in uniform mode."""
    return AddTransitionOptions(
        uniform=TransitionSpec(type=transition_type, duration_sec=duration)
    )


def _write_timeline(tl: otio.schema.Timeline, path: Path) -> None:
    otio.adapters.write_to_file(tl, str(path))


# ---------------------------------------------------------------------------
# Normal-path tests
# ---------------------------------------------------------------------------


class TestE2EEnvelopeShape:
    """Verify the full envelope (ok/summary/data/artifacts/warnings) for the happy path."""

    def test_two_clip_uniform_envelope_ok(
        self,
        timeline_file: Path,
        output_otio: Path,
    ) -> None:
        """add_transition with two-clip timeline and uniform mode returns ok=True."""
        result = add_transition(
            timeline=str(timeline_file),
            output=str(output_otio),
            options=_uniform_opts(),
        )

        assert result["ok"] is True
        # summary must mention boundary count / mode
        assert isinstance(result["summary"], str)
        assert len(result["summary"]) > 0
        # data keys
        assert result["data"]["boundary_count"] == 1
        assert result["data"]["mode"] == "uniform"
        assert "output" in result["data"]
        # artifacts: at least one entry with role="timeline" and path
        artifacts = result["artifacts"]
        assert isinstance(artifacts, list)
        assert len(artifacts) >= 1
        artifact = artifacts[0]
        assert artifact["role"] == "timeline"
        assert artifact["path"] == str(output_otio)
        # warnings: key present (may be empty list)
        assert "warnings" in result

    def test_three_clip_uniform_two_boundaries(
        self,
        three_clip_timeline: otio.schema.Timeline,
        tmp_path: Path,
    ) -> None:
        """Three-clip timeline produces two boundaries in uniform mode."""
        input_path = tmp_path / "input3.otio"
        output_path = tmp_path / "output3.otio"
        _write_timeline(three_clip_timeline, input_path)

        result = add_transition(
            timeline=str(input_path),
            output=str(output_path),
            options=_uniform_opts(transition_type="fadeblack", duration=1.0),
        )

        assert result["ok"] is True
        assert result["data"]["boundary_count"] == 2
        assert result["data"]["mode"] == "uniform"


class TestE2EArtifactExists:
    """Verify that the artifact OTIO is written to disk."""

    def test_output_otio_file_exists_after_call(
        self,
        timeline_file: Path,
        output_otio: Path,
    ) -> None:
        """The output .otio artifact must exist and be non-empty after the call."""
        assert not output_otio.exists(), "pre-condition: output must not exist yet"

        result = add_transition(
            timeline=str(timeline_file),
            output=str(output_otio),
            options=_uniform_opts(),
        )

        assert result["ok"] is True
        assert output_otio.exists(), "output .otio was not created"
        assert output_otio.stat().st_size > 0, "output .otio is empty"

    def test_artifact_path_matches_disk_file(
        self,
        timeline_file: Path,
        output_otio: Path,
    ) -> None:
        """Artifact path in result must correspond to the actual file on disk."""
        result = add_transition(
            timeline=str(timeline_file),
            output=str(output_otio),
            options=_uniform_opts(),
        )

        assert result["ok"] is True
        artifact_path = result["artifacts"][0]["path"]
        assert Path(artifact_path).exists(), (
            f"Artifact path reported in result does not exist: {artifact_path}"
        )


class TestE2EDirectiveCanonicalForm:
    """Verify metadata["clipwright"]["transition"] structure and ascending order."""

    def test_two_clip_directive_canonical_form(
        self,
        timeline_file: Path,
        output_otio: Path,
    ) -> None:
        """Directive in output OTIO must be the expanded per-boundary form."""
        result = add_transition(
            timeline=str(timeline_file),
            output=str(output_otio),
            options=_uniform_opts(transition_type="fade", duration=0.3),
        )
        assert result["ok"] is True

        out_tl = otio.adapters.read_from_file(str(output_otio))
        meta = out_tl.metadata.get("clipwright", {})
        assert isinstance(meta, collections.abc.Mapping)
        tr = meta.get("transition")
        assert tr is not None, "transition key not found in metadata['clipwright']"
        # Required keys
        assert tr["kind"] == "transition"
        assert tr["tool"] == "clipwright_add_transition"
        assert "version" in tr
        transitions = list(tr["transitions"])
        assert len(transitions) == 1  # 2 clips -> 1 boundary
        entry = transitions[0]
        assert entry["after_clip_index"] == 0
        assert entry["type"] == "fade"
        assert entry["duration_sec"] == pytest.approx(0.3)

    def test_three_clip_directive_ascending_order(
        self,
        three_clip_timeline: otio.schema.Timeline,
        tmp_path: Path,
    ) -> None:
        """Transition list must be sorted ascending by after_clip_index."""
        input_path = tmp_path / "input3.otio"
        output_path = tmp_path / "output3.otio"
        _write_timeline(three_clip_timeline, input_path)

        result = add_transition(
            timeline=str(input_path),
            output=str(output_path),
            options=_uniform_opts(transition_type="dissolve", duration=0.5),
        )
        assert result["ok"] is True

        out_tl = otio.adapters.read_from_file(str(output_path))
        meta = out_tl.metadata.get("clipwright", {})
        transitions = list(meta["transition"]["transitions"])
        indices = [t["after_clip_index"] for t in transitions]
        assert indices == sorted(indices), (
            f"Transitions not in ascending order: {indices}"
        )
        assert indices == [0, 1], (
            f"Expected boundaries at 0 and 1 for 3-clip timeline, got {indices}"
        )

    def test_per_boundary_directive_ascending_order(
        self,
        three_clip_timeline: otio.schema.Timeline,
        tmp_path: Path,
    ) -> None:
        """per_boundary mode: directive must be sorted ascending (regardless of input order)."""
        input_path = tmp_path / "input3_pb.otio"
        output_path = tmp_path / "output3_pb.otio"
        _write_timeline(three_clip_timeline, input_path)

        # Provide boundaries in reverse order; result must be sorted.
        opts = AddTransitionOptions(
            per_boundary=[
                BoundaryTransition(
                    after_clip_index=1, type="fadewhite", duration_sec=0.4
                ),
                BoundaryTransition(
                    after_clip_index=0, type="dissolve", duration_sec=0.6
                ),
            ]
        )
        result = add_transition(
            timeline=str(input_path),
            output=str(output_path),
            options=opts,
        )
        assert result["ok"] is True

        out_tl = otio.adapters.read_from_file(str(output_path))
        meta = out_tl.metadata.get("clipwright", {})
        transitions = list(meta["transition"]["transitions"])
        indices = [t["after_clip_index"] for t in transitions]
        assert indices == sorted(indices), (
            f"per_boundary transitions not ascending: {indices}"
        )


class TestE2ENonDestructive:
    """Verify that the input file is byte-identical before and after the call."""

    def test_input_bytes_unchanged(
        self,
        timeline_file: Path,
        output_otio: Path,
    ) -> None:
        """Input OTIO file must not be modified on disk (non-destructive)."""
        original_bytes = timeline_file.read_bytes()

        result = add_transition(
            timeline=str(timeline_file),
            output=str(output_otio),
            options=_uniform_opts(),
        )
        assert result["ok"] is True

        assert timeline_file.read_bytes() == original_bytes, (
            "Input OTIO file was modified by add_transition (destructive write detected)"
        )

    def test_three_clip_input_bytes_unchanged(
        self,
        three_clip_timeline: otio.schema.Timeline,
        tmp_path: Path,
    ) -> None:
        """Non-destructive check for three-clip timeline."""
        input_path = tmp_path / "input3_nd.otio"
        output_path = tmp_path / "output3_nd.otio"
        _write_timeline(three_clip_timeline, input_path)
        original_bytes = input_path.read_bytes()

        result = add_transition(
            timeline=str(input_path),
            output=str(output_path),
            options=_uniform_opts(),
        )
        assert result["ok"] is True
        assert input_path.read_bytes() == original_bytes, (
            "Three-clip input OTIO was modified (destructive)"
        )


# ---------------------------------------------------------------------------
# INVALID_INPUT paths
# ---------------------------------------------------------------------------


class TestE2EInvalidInputClipCount:
    """INVALID_INPUT when the timeline has fewer than 2 clips."""

    def test_single_clip_timeline_returns_invalid_input(
        self,
        tmp_path: Path,
    ) -> None:
        """A one-clip timeline must return ok=False with code=INVALID_INPUT."""
        # Build a single-clip timeline
        tl = otio.schema.Timeline(name="one_clip")
        track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        track.append(
            otio.schema.Clip(
                name="solo",
                media_reference=otio.schema.ExternalReference(
                    target_url=str(tmp_path / "solo.mp4")
                ),
                source_range=otio.opentime.TimeRange(
                    start_time=otio.opentime.RationalTime(0, 30),
                    duration=otio.opentime.RationalTime(300, 30),
                ),
            )
        )
        tl.tracks.append(track)
        input_path = tmp_path / "single_clip.otio"
        output_path = tmp_path / "output_single.otio"
        _write_timeline(tl, input_path)

        result = add_transition(
            timeline=str(input_path),
            output=str(output_path),
            options=_uniform_opts(),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"


class TestE2EOutputEqualsInput:
    """INVALID_INPUT when output path == input timeline path."""

    def test_output_same_as_input_returns_invalid_input(
        self,
        timeline_file: Path,
    ) -> None:
        """Passing the same path for both timeline and output must return INVALID_INPUT."""
        result = add_transition(
            timeline=str(timeline_file),
            output=str(timeline_file),
            options=_uniform_opts(),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"
