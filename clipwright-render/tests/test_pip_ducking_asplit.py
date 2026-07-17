"""test_pip_ducking_asplit.py — characterisation tests pinning the pre-refactor
behaviour of _append_pip_audio_pipe's asplit/ducking wiring (FR-4, ADR-PIP-9).

Purpose: `_append_pip_audio_pipe` (clipwright_render.plan) duplicates an
identical asplit + amix-input wiring block for its "main" and "bgm" ducking
sidechain sources. architecture-report-20260717-163916.md §6 plans to extract
this duplicated logic into a new helper, `_append_ducking_asplit`, as a pure
refactor (no behaviour change).

These tests are NOT a Red-phase TDD suite. They are a regression guard:
each case below pins the exact `filter_parts` list and returned audio-map
label that `_append_pip_audio_pipe` currently produces (captured by running
the pre-refactor code directly), so that after the `_append_ducking_asplit`
extraction lands, an unmodified run of this file proves the extraction did
not change observable behaviour. All 7 cases are expected to pass against
today's (pre-extraction) plan.py.

Case matrix (architecture-report §6.5, all axes: has_main_audio / bgm_present
/ PiP composition):
  1. (F, F, PiP x1 ducking=0): N=1 early return, asplit not reached.
  2. (T, F, PiP x1 ducking=0): main plugged in directly (no asplit) + amix.
  3. (T, F, PiP x1 ducking=1): main asplit (main_mix + main_sc_0) + sidechaincompress.
  4. (F, T, PiP x1 ducking=1): bgm asplit (bgm_mix + bgm_sc_0) + sidechaincompress.
  5. (T, T, PiP x1 ducking=1): main asplit; bgm folded into main, not re-added.
  6. (T, F, PiP x2 ducking=2): main asplit with 2 sidechain outputs + 2x sidechaincompress.
  7. (F, T, PiP x2 ducking=1 + non-ducking=1): bgm asplit (bgm_sc_0) + 1x
     sidechaincompress + 1 direct (non-ducking) PiP branch.

Golden literals below were captured by directly invoking
_append_pip_audio_pipe with the inputs described per case, before the
_append_ducking_asplit extraction was made (see architecture-report
§6.5 DoD). They are pinned verbatim — do not "clean up" or recompute them
by hand.
"""

from __future__ import annotations

from typing import Any

from clipwright_render.plan import (
    PipDuckingDirective,
    PipOverlay,
    _append_pip_audio_pipe,
)

_MAIN_DUR = 10.0


def _make_pip(**overrides: Any) -> PipOverlay:
    """Build a real PipOverlay with fixed audio-relevant defaults (mirrors
    test_pip_ffmpeg_execution.py's _make_default_pip_overlay helper)."""
    ducking = overrides.pop("ducking", None)
    if ducking is None:
        ducking = PipDuckingDirective(enabled=False)
    defaults: dict[str, Any] = dict(
        media_path="pip.mp4",
        media_start_s=0.0,
        duration_s=4.0,
        start_s=2.5,
        end_s=6.5,
        x="(W-w)/2",
        y="(H-h)/2",
        scale=0.3,
        opacity=1.0,
        fade_in_s=0.0,
        fade_out_s=0.0,
        input_index=1,
        mix_audio=True,
        audio_volume=1.0,
        ducking=ducking,
    )
    defaults.update(overrides)
    return PipOverlay(**defaults)


class TestPipDuckingAsplitCharacterisation:
    """Pin filter_parts + returned label for each (has_main_audio,
    bgm_present, PiP composition) combination in architecture-report §6.5."""

    def test_case1_no_main_no_bgm_single_pip_no_ducking(self) -> None:
        """N=1 early return path: asplit is never reached."""
        pips = [_make_pip(input_index=1, ducking=PipDuckingDirective(enabled=False))]
        filter_parts: list[str] = []

        label = _append_pip_audio_pipe(
            filter_parts, pips, "[outa]", False, _MAIN_DUR, False
        )

        assert filter_parts == [
            "[1:a]atrim=start=0:duration=4,asetpts=PTS-STARTPTS,"
            "adelay=2500|2500,apad,atrim=0:10,volume=1[pip0_audio]",
        ]
        assert label == "[pip0_audio]"

    def test_case2_main_only_single_pip_no_ducking(self) -> None:
        """main plugged directly into amix (no asplit) since ducking=0."""
        pips = [_make_pip(input_index=1, ducking=PipDuckingDirective(enabled=False))]
        filter_parts: list[str] = []

        label = _append_pip_audio_pipe(
            filter_parts, pips, "[outa]", True, _MAIN_DUR, False
        )

        assert filter_parts == [
            "[1:a]atrim=start=0:duration=4,asetpts=PTS-STARTPTS,"
            "adelay=2500|2500,apad,atrim=0:10,volume=1[pip0_audio]",
            "[outa]aformat=sample_rates=48000:channel_layouts=stereo[main_pip_fmt]",
            "[main_pip_fmt][pip0_audio]amix=inputs=2:normalize=0,"
            "alimiter=limit=1.0[outa_pip]",
        ]
        assert label == "[outa_pip]"

    def test_case3_main_only_single_pip_ducking_enabled(self) -> None:
        """main asplit into main_mix + main_sc_0, single sidechaincompress."""
        pips = [
            _make_pip(
                input_index=1,
                ducking=PipDuckingDirective(enabled=True, threshold=0.05, ratio=4.0),
            )
        ]
        filter_parts: list[str] = []

        label = _append_pip_audio_pipe(
            filter_parts, pips, "[outa]", True, _MAIN_DUR, False
        )

        assert filter_parts == [
            "[1:a]atrim=start=0:duration=4,asetpts=PTS-STARTPTS,"
            "adelay=2500|2500,apad,atrim=0:10,volume=1[pip0_audio]",
            "[outa]aformat=sample_rates=48000:channel_layouts=stereo[main_pip_fmt]",
            "[main_pip_fmt]asplit[main_mix][main_sc_0]",
            "[pip0_audio][main_sc_0]sidechaincompress=threshold=0.05:ratio=4"
            "[pip0_duck]",
            "[main_mix][pip0_duck]amix=inputs=2:normalize=0,"
            "alimiter=limit=1.0[outa_pip]",
        ]
        assert label == "[outa_pip]"

    def test_case4_bgm_only_single_pip_ducking_enabled(self) -> None:
        """bgm asplit into bgm_mix + bgm_sc_0, single sidechaincompress."""
        pips = [
            _make_pip(
                input_index=1,
                ducking=PipDuckingDirective(enabled=True, threshold=0.05, ratio=4.0),
            )
        ]
        filter_parts: list[str] = []

        label = _append_pip_audio_pipe(
            filter_parts, pips, "[outa_bgm]", False, _MAIN_DUR, True
        )

        assert filter_parts == [
            "[1:a]atrim=start=0:duration=4,asetpts=PTS-STARTPTS,"
            "adelay=2500|2500,apad,atrim=0:10,volume=1[pip0_audio]",
            "[outa_bgm]asplit[bgm_mix][bgm_sc_0]",
            "[pip0_audio][bgm_sc_0]sidechaincompress=threshold=0.05:ratio=4[pip0_duck]",
            "[bgm_mix][pip0_duck]amix=inputs=2:normalize=0,"
            "alimiter=limit=1.0[outa_pip]",
        ]
        assert label == "[outa_pip]"

    def test_case5_main_and_bgm_single_pip_ducking_enabled(self) -> None:
        """main asplit; bgm is folded into main (already outa_bgm) and is
        NOT re-added as a separate amix input (CR-NEW no-double-mix guard)."""
        pips = [
            _make_pip(
                input_index=1,
                ducking=PipDuckingDirective(enabled=True, threshold=0.05, ratio=4.0),
            )
        ]
        filter_parts: list[str] = []

        label = _append_pip_audio_pipe(
            filter_parts, pips, "[outa_bgm]", True, _MAIN_DUR, True
        )

        assert filter_parts == [
            "[1:a]atrim=start=0:duration=4,asetpts=PTS-STARTPTS,"
            "adelay=2500|2500,apad,atrim=0:10,volume=1[pip0_audio]",
            "[outa_bgm]aformat=sample_rates=48000:channel_layouts=stereo[main_pip_fmt]",
            "[main_pip_fmt]asplit[main_mix][main_sc_0]",
            "[pip0_audio][main_sc_0]sidechaincompress=threshold=0.05:ratio=4"
            "[pip0_duck]",
            "[main_mix][pip0_duck]amix=inputs=2:normalize=0,"
            "alimiter=limit=1.0[outa_pip]",
        ]
        assert label == "[outa_pip]"

    def test_case6_main_only_two_pips_both_ducking_enabled(self) -> None:
        """main asplit with 2 sidechain outputs (main_sc_0, main_sc_1) and
        2x sidechaincompress; ducking_idx increments across both PiPs."""
        pips = [
            _make_pip(
                input_index=1,
                ducking=PipDuckingDirective(enabled=True, threshold=0.05, ratio=4.0),
            ),
            _make_pip(
                input_index=2,
                ducking=PipDuckingDirective(enabled=True, threshold=0.1, ratio=6.0),
            ),
        ]
        filter_parts: list[str] = []

        label = _append_pip_audio_pipe(
            filter_parts, pips, "[outa]", True, _MAIN_DUR, False
        )

        assert filter_parts == [
            "[1:a]atrim=start=0:duration=4,asetpts=PTS-STARTPTS,"
            "adelay=2500|2500,apad,atrim=0:10,volume=1[pip0_audio]",
            "[2:a]atrim=start=0:duration=4,asetpts=PTS-STARTPTS,"
            "adelay=2500|2500,apad,atrim=0:10,volume=1[pip1_audio]",
            "[outa]aformat=sample_rates=48000:channel_layouts=stereo[main_pip_fmt]",
            "[main_pip_fmt]asplit[main_mix][main_sc_0][main_sc_1]",
            "[pip0_audio][main_sc_0]sidechaincompress=threshold=0.05:ratio=4"
            "[pip0_duck]",
            "[pip1_audio][main_sc_1]sidechaincompress=threshold=0.1:ratio=6[pip1_duck]",
            "[main_mix][pip0_duck][pip1_duck]amix=inputs=3:normalize=0,"
            "alimiter=limit=1.0[outa_pip]",
        ]
        assert label == "[outa_pip]"

    def test_case7_bgm_only_two_pips_mixed_ducking(self) -> None:
        """bgm asplit (only bgm_sc_0, since only 1 of 2 PiPs ducks) + 1x
        sidechaincompress + 1 direct (non-ducking) PiP branch in amix."""
        pips = [
            _make_pip(
                input_index=1,
                ducking=PipDuckingDirective(enabled=True, threshold=0.05, ratio=4.0),
            ),
            _make_pip(input_index=2, ducking=PipDuckingDirective(enabled=False)),
        ]
        filter_parts: list[str] = []

        label = _append_pip_audio_pipe(
            filter_parts, pips, "[outa_bgm]", False, _MAIN_DUR, True
        )

        assert filter_parts == [
            "[1:a]atrim=start=0:duration=4,asetpts=PTS-STARTPTS,"
            "adelay=2500|2500,apad,atrim=0:10,volume=1[pip0_audio]",
            "[2:a]atrim=start=0:duration=4,asetpts=PTS-STARTPTS,"
            "adelay=2500|2500,apad,atrim=0:10,volume=1[pip1_audio]",
            "[outa_bgm]asplit[bgm_mix][bgm_sc_0]",
            "[pip0_audio][bgm_sc_0]sidechaincompress=threshold=0.05:ratio=4[pip0_duck]",
            "[bgm_mix][pip0_duck][pip1_audio]amix=inputs=3:normalize=0,"
            "alimiter=limit=1.0[outa_pip]",
        ]
        assert label == "[outa_pip]"
