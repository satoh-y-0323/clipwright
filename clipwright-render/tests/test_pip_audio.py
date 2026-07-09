"""test_pip_audio.py — Red-phase tests for PiP audio mixing (mix_audio=True).

Target symbols (do NOT exist yet — this suite is expected to XFAIL/Red):
  - PipDuckingDirective (BaseModel, clipwright_render.plan)
  - _append_pip_audio_pipe(filter_parts, pip_overlays, audio_map_label,
    has_main_audio, main_dur, bgm_present) -> str (clipwright_render.plan)

Architecture authority: architecture-report-20260709-093022.md, ADR-PIP-9
("bgm と異なり PiP 音声は配置時間窓（start_s〜start_s+duration_s）だけ鳴る").
Reference implementation for the amix/sidechaincompress vocabulary:
_append_bgm_pipe (plan.py:3866-3956).

Key ADR-PIP-9 requirement under test: a naive copy of the BGM pipe would make
PiP audio start sounding at t=0 instead of at its placement time. Several
tests below explicitly assert "adelay=0|0" is NOT present and the correct
non-zero adelay value IS present, to pin this down.

Test isolation:
  - Target symbols are imported guarded by try/except at module scope, and the
    whole module is marked xfail(strict=True) when they are absent, so
    collection of the rest of the render suite is unaffected (same pattern as
    test_image_overlay.py).
  - PipOverlay itself (the real value object) is introduced by the sibling
    task test-pip-render-video / impl-pip-render-video (not yet implemented
    at the time this file was written either — plan.py has no PipOverlay
    class yet). Since this task only targets _append_pip_audio_pipe /
    PipDuckingDirective in isolation, a minimal duck-typed stand-in
    (_FakePipOverlay) is used below instead of importing the real dataclass.
    Field names (start_s / end_s / duration_s / media_start_s) match the "_s"
    seconds-suffix convention already confirmed in the sibling
    tests/test_pip_video.py's own _make_pip_overlay() helper (which mirrors
    ImageOverlay's start_s/end_s naming, plus media_start_s/duration_s that
    PiP needs and image_overlay does not). mix_audio/audio_volume/ducking are
    NOT present on that sibling file's PipOverlay stand-in (video-only scope);
    impl-pip-render-audio is expected to extend the real PipOverlay dataclass
    with these three fields when wiring _append_pip_audio_pipe into build_plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Guard: imports of new symbols are deferred to inside test bodies (per test).
# Module-level sentinel records whether they are available yet.
# ---------------------------------------------------------------------------

_PLAN_HAS_PIP_AUDIO: bool
try:
    from clipwright_render.plan import (  # noqa: F401
        PipDuckingDirective as _PDD,
    )
    from clipwright_render.plan import (  # noqa: F401
        _append_pip_audio_pipe as _APAP,
    )

    _PLAN_HAS_PIP_AUDIO = True
except ImportError:
    _PLAN_HAS_PIP_AUDIO = False

pytestmark = pytest.mark.xfail(
    not _PLAN_HAS_PIP_AUDIO,
    strict=True,
    reason=(
        "_append_pip_audio_pipe / PipDuckingDirective not found in"
        " clipwright_render.plan (PiP audio mixing not implemented yet;"
        " ADR-PIP-9)"
    ),
)


# ---------------------------------------------------------------------------
# Test-local stand-in for the PipOverlay value object (see module docstring).
# ---------------------------------------------------------------------------


@dataclass
class _FakePipOverlay:
    input_index: int
    start_s: float
    duration_s: float
    media_start_s: float
    mix_audio: bool
    audio_volume: float
    ducking: Any


def _make_pip_ducking(**kwargs: Any) -> Any:
    """Build a PipDuckingDirective (mirrors clipwright-bgm's DuckingDirective
    defaults: enabled=False, threshold=0.05, ratio=4.0 — ADR-PIP-4)."""
    from clipwright_render.plan import (  # type: ignore[attr-defined]
        PipDuckingDirective,
    )

    defaults: dict[str, Any] = dict(enabled=False, threshold=0.05, ratio=4.0)
    defaults.update(kwargs)
    return PipDuckingDirective(**defaults)


def _make_pip_overlay(**kwargs: Any) -> _FakePipOverlay:
    defaults: dict[str, Any] = dict(
        input_index=1,
        start_s=2.5,
        duration_s=4.0,
        media_start_s=0.0,
        mix_audio=True,
        audio_volume=1.0,
        ducking=_make_pip_ducking(),
    )
    defaults.update(kwargs)
    return _FakePipOverlay(**defaults)


# ===========================================================================
# Section 1: PipDuckingDirective range constraints (ADR-PIP-4 / ADR-PIP-9)
# ===========================================================================


class TestPipDuckingDirectiveValidation:
    """PipDuckingDirective mirrors clipwright-bgm's DuckingDirective range
    constraints exactly: threshold in (0, 1], ratio in [1, 20]. Defined
    locally in clipwright_render.plan — no cross-satellite import from
    clipwright-bgm (ADR-PIP-4)."""

    def test_default_is_disabled(self) -> None:
        d = _make_pip_ducking()
        assert d.enabled is False

    def test_threshold_zero_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _make_pip_ducking(threshold=0.0)

    def test_threshold_one_accepted_boundary(self) -> None:
        d = _make_pip_ducking(threshold=1.0)
        assert d.threshold == 1.0

    def test_threshold_above_one_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _make_pip_ducking(threshold=1.01)

    def test_ratio_below_one_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _make_pip_ducking(ratio=0.99)

    def test_ratio_one_accepted_boundary(self) -> None:
        d = _make_pip_ducking(ratio=1.0)
        assert d.ratio == 1.0

    def test_ratio_twenty_accepted_boundary(self) -> None:
        d = _make_pip_ducking(ratio=20.0)
        assert d.ratio == 20.0

    def test_ratio_above_twenty_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _make_pip_ducking(ratio=20.01)


# ===========================================================================
# Section 2: mix_audio=False is a strict no-op (backward compatible)
# ===========================================================================


class TestMixAudioFalseNoOp:
    """mix_audio=False (the AddPipOptions default) PiP overlays must not
    affect the audio graph at all: no amix stage is introduced and
    filter_parts/audio_map_label are byte-identical to the no-PiP case."""

    def test_empty_pip_overlays_is_noop(self) -> None:
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_audio_pipe,
        )

        filter_parts: list[str] = ["[0:v]scale=1280:720[outv]"]
        before = list(filter_parts)
        result = _append_pip_audio_pipe(filter_parts, [], "[outa]", True, 10.0, False)
        assert filter_parts == before
        assert result == "[outa]"

    def test_all_mix_audio_false_is_noop(self) -> None:
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_audio_pipe,
        )

        overlays = [
            _make_pip_overlay(mix_audio=False, input_index=1),
            _make_pip_overlay(mix_audio=False, input_index=2),
        ]
        filter_parts: list[str] = []
        result = _append_pip_audio_pipe(
            filter_parts, overlays, "[outa]", True, 10.0, False
        )
        assert filter_parts == []
        assert result == "[outa]"

    def test_mixed_true_false_only_processes_true(self) -> None:
        """A mix of mix_audio=True/False overlays only routes the True ones
        into the audio graph (identified by their own input_index)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_audio_pipe,
        )

        overlays = [
            _make_pip_overlay(mix_audio=False, input_index=1),
            _make_pip_overlay(mix_audio=True, input_index=2),
        ]
        filter_parts: list[str] = []
        _append_pip_audio_pipe(filter_parts, overlays, "[outa]", True, 10.0, False)
        joined = ";".join(filter_parts)
        assert "[2:a]" in joined
        assert "[1:a]" not in joined


# ===========================================================================
# Section 3: single PiP audio branch timing (ADR-PIP-9 point 1)
# ===========================================================================


class TestSinglePipAudioBranchTiming:
    """The PiP audio branch is trimmed from its own source window and then
    delayed/padded so it only sounds during its placement window
    (start_s .. start_s+duration_s) — ADR-PIP-9. A branch that starts
    sounding at t=0 instead of start_s is the exact bug this ADR calls out
    ("素朴に bgm パターンを複製すると PiP 音声が t=0 から鳴ってしまう")."""

    def test_branch_references_its_own_input_index(self) -> None:
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_audio_pipe,
        )

        o = _make_pip_overlay(input_index=3)
        filter_parts: list[str] = []
        _append_pip_audio_pipe(filter_parts, [o], "[outa]", True, 10.0, False)
        joined = ";".join(filter_parts)
        assert "[3:a]" in joined

    def test_branch_trims_source_read_window(self) -> None:
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_audio_pipe,
        )

        o = _make_pip_overlay(media_start_s=1.5, duration_s=4.0)
        filter_parts: list[str] = []
        _append_pip_audio_pipe(filter_parts, [o], "[outa]", True, 10.0, False)
        joined = ";".join(filter_parts)
        assert "trim=start=1.5:duration=4" in joined
        assert "asetpts=PTS-STARTPTS" in joined

    def test_branch_delays_to_placement_start_not_zero(self) -> None:
        """adelay must equal start_s*1000 ms for both stereo channels — NOT
        0 (the t=0 bug this task exists to guard against)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_audio_pipe,
        )

        o = _make_pip_overlay(start_s=2.5)
        filter_parts: list[str] = []
        _append_pip_audio_pipe(filter_parts, [o], "[outa]", True, 10.0, False)
        joined = ";".join(filter_parts)
        assert "adelay=2500|2500" in joined
        assert "adelay=0|0" not in joined

    def test_branch_delay_scales_with_start_s(self) -> None:
        """Different start_s values must produce different, correctly
        scaled adelay values (guards against a hard-coded/copy-pasted delay)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_audio_pipe,
        )

        o = _make_pip_overlay(start_s=0.75)
        filter_parts: list[str] = []
        _append_pip_audio_pipe(filter_parts, [o], "[outa]", True, 10.0, False)
        joined = ";".join(filter_parts)
        assert "adelay=750|750" in joined

    def test_branch_pads_and_trims_to_main_duration(self) -> None:
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_audio_pipe,
        )

        o = _make_pip_overlay()
        filter_parts: list[str] = []
        _append_pip_audio_pipe(filter_parts, [o], "[outa]", True, 12.0, False)
        joined = ";".join(filter_parts)
        assert "apad" in joined
        assert "atrim=0:12" in joined

    def test_branch_stage_ordering(self) -> None:
        """trim -> asetpts -> adelay -> apad -> atrim, in that exact order
        (ADR-PIP-9 point 1)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_audio_pipe,
        )

        o = _make_pip_overlay(media_start_s=0.5, duration_s=3.0, start_s=1.0)
        filter_parts: list[str] = []
        _append_pip_audio_pipe(filter_parts, [o], "[outa]", True, 10.0, False)
        joined = ";".join(filter_parts)
        i_trim = joined.find("trim=start=")
        i_setpts = joined.find("asetpts=PTS-STARTPTS")
        i_adelay = joined.find("adelay=")
        i_apad = joined.find("apad")
        i_atrim = joined.find("atrim=0:")
        assert -1 not in (i_trim, i_setpts, i_adelay, i_apad, i_atrim), (
            f"one or more expected stages missing: {joined!r}"
        )
        assert i_trim < i_setpts < i_adelay < i_apad < i_atrim


# ===========================================================================
# Section 4: audio_volume is applied as a volume filter
# ===========================================================================


class TestAudioVolumeApplied:
    def test_default_volume_one_present(self) -> None:
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_audio_pipe,
        )

        o = _make_pip_overlay(audio_volume=1.0)
        filter_parts: list[str] = []
        _append_pip_audio_pipe(filter_parts, [o], "[outa]", True, 10.0, False)
        joined = ";".join(filter_parts)
        assert "volume=1" in joined

    def test_custom_volume_value_applied(self) -> None:
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_audio_pipe,
        )

        o = _make_pip_overlay(audio_volume=2.5)
        filter_parts: list[str] = []
        _append_pip_audio_pipe(filter_parts, [o], "[outa]", True, 10.0, False)
        joined = ";".join(filter_parts)
        assert "volume=2.5" in joined


# ===========================================================================
# Section 5: multiple PiP -> single amix + single trailing alimiter
# (ADR-PIP-9 point 2)
# ===========================================================================


class TestMultiPipAmixAlimiter:
    """Multiple mix_audio=True PiPs are combined in ONE amix stage with ONE
    trailing alimiter — never a per-pip amix/alimiter chain. N =
    main(if has_main_audio) + bgm(if bgm_present) + count(mix_audio pips), per
    the plan-report literal formula ("メイン + bgm(あれば) + 各 PiP の合計")."""

    @pytest.mark.parametrize(
        "has_main_audio,bgm_present,n_pips,expected_n",
        [
            (True, False, 2, 3),
            (True, True, 2, 4),
            (False, True, 3, 4),
            (True, False, 1, 2),
        ],
    )
    def test_amix_input_count(
        self,
        has_main_audio: bool,
        bgm_present: bool,
        n_pips: int,
        expected_n: int,
    ) -> None:
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_audio_pipe,
        )

        overlays = [
            _make_pip_overlay(input_index=10 + i, mix_audio=True) for i in range(n_pips)
        ]
        filter_parts: list[str] = []
        _append_pip_audio_pipe(
            filter_parts, overlays, "[outa]", has_main_audio, 10.0, bgm_present
        )
        joined = ";".join(filter_parts)
        assert f"amix=inputs={expected_n}:normalize=0" in joined

    def test_alimiter_appears_exactly_once(self) -> None:
        """Never a multi-stage alimiter chain — exactly one alimiter at the
        final stage, regardless of how many PiP branches feed into it."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_audio_pipe,
        )

        overlays = [
            _make_pip_overlay(input_index=10 + i, mix_audio=True) for i in range(3)
        ]
        filter_parts: list[str] = []
        _append_pip_audio_pipe(filter_parts, overlays, "[outa]", True, 10.0, True)
        joined = ";".join(filter_parts)
        assert joined.count("alimiter") == 1
        assert "alimiter=limit=1.0" in joined

    def test_single_pip_no_main_no_bgm_skips_amix(self) -> None:
        """N=1 (the PiP branch is the only audio source) — no amix/alimiter
        is needed, mirroring _append_bgm_pipe's has_main_audio=False
        standalone path (plan.py:3927-3930)."""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_audio_pipe,
        )

        o = _make_pip_overlay(input_index=5, mix_audio=True)
        filter_parts: list[str] = []
        result = _append_pip_audio_pipe(filter_parts, [o], "[outa]", False, 10.0, False)
        joined = ";".join(filter_parts)
        assert "amix" not in joined
        assert "alimiter" not in joined
        assert result != "[outa]"


# ===========================================================================
# Section 6: per-PiP ducking (ADR-PIP-9 point 3)
# ===========================================================================


class TestDuckingSinglePip:
    """ducking.enabled=True routes the PiP branch through asplit +
    sidechaincompress before joining amix — mirrors _append_bgm_pipe's
    ducking-ON branch (plan.py:3940-3949), reusing the same threshold/ratio
    range vocabulary (threshold (0,1], ratio [1,20])."""

    def test_ducking_off_no_sidechaincompress(self) -> None:
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_audio_pipe,
        )

        o = _make_pip_overlay(ducking=_make_pip_ducking(enabled=False))
        filter_parts: list[str] = []
        _append_pip_audio_pipe(filter_parts, [o], "[outa]", True, 10.0, False)
        joined = ";".join(filter_parts)
        assert "sidechaincompress" not in joined

    def test_ducking_on_sidechaincompress_present_with_correct_params(self) -> None:
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_audio_pipe,
        )

        o = _make_pip_overlay(
            ducking=_make_pip_ducking(enabled=True, threshold=0.3, ratio=6.0)
        )
        filter_parts: list[str] = []
        _append_pip_audio_pipe(filter_parts, [o], "[outa]", True, 10.0, False)
        joined = ";".join(filter_parts)
        assert "sidechaincompress=threshold=0.3:ratio=6" in joined

    def test_ducking_on_stage_ordering_asplit_before_sidechain_before_amix(
        self,
    ) -> None:
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_audio_pipe,
        )

        o = _make_pip_overlay(ducking=_make_pip_ducking(enabled=True))
        filter_parts: list[str] = []
        _append_pip_audio_pipe(filter_parts, [o], "[outa]", True, 10.0, False)
        joined = ";".join(filter_parts)
        i_asplit = joined.find("asplit")
        i_sc = joined.find("sidechaincompress")
        i_amix = joined.find("amix=inputs=")
        assert -1 not in (i_asplit, i_sc, i_amix), f"missing stage: {joined!r}"
        assert i_asplit < i_sc < i_amix


# ===========================================================================
# Section 7: bgm + PiP ducking coexist without a broken filtergraph
# (ADR-PIP-9 closing note)
# ===========================================================================


class TestBgmAndPipDuckingCombined:
    """When bgm is present (bgm_present=True) AND a PiP also has ducking
    enabled, the resulting filtergraph must still be constructible as a
    multi-stage chain — it must not raise, and each PiP's own audio branch
    must still be individually reachable in the output."""

    def test_bgm_present_plus_ducking_pip_builds_without_raising(self) -> None:
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            _append_pip_audio_pipe,
        )

        overlays = [
            _make_pip_overlay(
                input_index=7,
                ducking=_make_pip_ducking(enabled=True, threshold=0.2, ratio=5.0),
            ),
            _make_pip_overlay(input_index=8, ducking=_make_pip_ducking(enabled=False)),
        ]
        filter_parts: list[str] = []
        result = _append_pip_audio_pipe(
            filter_parts, overlays, "[outa_bgm]", True, 10.0, True
        )
        joined = ";".join(filter_parts)
        assert "sidechaincompress" in joined
        assert "[7:a]" in joined
        assert "[8:a]" in joined
        assert result != "[outa_bgm]"
        assert result.startswith("[") and result.endswith("]")
