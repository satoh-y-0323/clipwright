"""test_loudness.py — clipwright-render の loudness 適用拡張 Red テスト（ADR-L5/L5b/L6）。

対象:
  - build_plan(ranges, probe_info, options, denoise=..., loudness=...) — loudnorm/peak 注入
  - audio map 終端ラベルの累積連鎖（ADR-L5b・DC-AM-001）
  - render_timeline() — LoudnessDirective 検証・get_clipwright_metadata 読み出し

設計根拠（architecture-report-20260611-114314 §3.3）:
  - ADR-L5: denoise → loudness の順で当てる。filter 注入順は afftdn の後ろに loudnorm を連結。
  - ADR-L5b: audio map 終端ラベルは累積パイプ型ヘルパーで一元解決する（DC-AM-001）。
    [outa] → (denoise あり → [outa_dn]) → (track loudness あり → [outa_ln])
  - ADR-L6: loudness 指示なし → 既存と完全同一（後方互換）。
  - DC-AM-002: peak + denoise 併用時は warning（測定タイミングずれ）。
  - 不正 directive → INVALID_INPUT（target 範囲外・mode 不正・scope 不正・measured 欠落・inf/nan）。
  - has_audio=False + loudness → filter に非混入 + warnings。
  - scale + loudness 両指定時は [outvscaled] と [outa_ln] の両 map を持つ。

probe は ProbeInfo を直接構築し build_plan を純ロジックとして呼ぶ。
render_timeline のシステムテストは timeline-level metadata への書き込み→読み出し経路を検証する。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.otio_utils import set_clipwright_metadata

from clipwright_render.plan import KeptRange, ProbeInfo
from clipwright_render.schemas import RenderOptions

# ---------------------------------------------------------------------------
# ヘルパー: OTIO 構築（test_denoise.py と同型）
# ---------------------------------------------------------------------------

FPS = 30.0
# テスト用ダミー bit_rate（アサーション対象外。定数化で将来の MediaInfo スキーマ変更に対応しやすくする）
_TEST_BIT_RATE = 8_000_000


def _rt(seconds: float, rate: float = FPS) -> otio.opentime.RationalTime:
    return otio.opentime.RationalTime(seconds * rate, rate)


def _tr(start: float, duration: float, rate: float = FPS) -> otio.opentime.TimeRange:
    return otio.opentime.TimeRange(
        start_time=_rt(start, rate),
        duration=_rt(duration, rate),
    )


def _make_clip(source: str, start: float, duration: float) -> otio.schema.Clip:
    clip = otio.schema.Clip()
    clip.media_reference = otio.schema.ExternalReference(target_url=source)
    clip.source_range = _tr(start, duration)
    return clip


def _make_timeline(
    clips: list[otio.schema.Clip],
    loudness_directive: dict[str, Any] | None = None,
    denoise_directive: dict[str, Any] | None = None,
) -> otio.schema.Timeline:
    """単一 video トラックの Timeline を生成する。

    loudness_directive/denoise_directive が指定された場合は
    set_clipwright_metadata 経由で timeline-level metadata に書き込む。
    """
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    for clip in clips:
        track.append(clip)
    tl = otio.schema.Timeline()
    tl.tracks.append(track)

    from clipwright.otio_utils import get_clipwright_metadata

    meta: dict[str, Any] = get_clipwright_metadata(tl)
    if denoise_directive is not None:
        meta["denoise"] = denoise_directive
    if loudness_directive is not None:
        meta["loudness"] = loudness_directive
    set_clipwright_metadata(tl, meta)
    return tl


def _single_range(source: str = "/src/a.mp4") -> list[KeptRange]:
    """1区間の KeptRange リストを返すヘルパー。"""
    from clipwright_render.plan import resolve_kept_ranges

    tl = _make_timeline([_make_clip(source, 0.0, 5.0)])
    return resolve_kept_ranges(tl)


# ---------------------------------------------------------------------------
# テスト用 LoudnessDirective 定義
# ---------------------------------------------------------------------------

# loudnorm mode（linear 適用に必要な measured 付き）
_VALID_LOUDNORM_DIRECTIVE: dict[str, Any] = {
    "tool": "clipwright-loudness",
    "version": "0.1.0",
    "kind": "loudness",
    "mode": "loudnorm",
    "scope": "track",
    "target": {"i": -14.0, "tp": -1.0, "lra": 11.0},
    "measured": {
        "input_i": -20.73,
        "input_tp": -7.68,
        "input_lra": 0.10,
        "input_thresh": -30.73,
        "target_offset": 0.03,
    },
}

# peak mode
_VALID_PEAK_DIRECTIVE: dict[str, Any] = {
    "tool": "clipwright-loudness",
    "version": "0.1.0",
    "kind": "loudness",
    "mode": "peak",
    "scope": "track",
    "target": {"peak_db": -1.0},
    "measured": {"max_volume_db": -7.68},
}

# denoise（afftdn）指示（test_denoise.py と同型）
_VALID_AFFTDN_DIRECTIVE: dict[str, Any] = {
    "tool": "clipwright-noise",
    "version": "0.1.0",
    "kind": "denoise",
    "backend": "afftdn",
    "params": {"nr": 12.0, "nf": -50.0, "nt": "w"},
}


# ---------------------------------------------------------------------------
# build_plan — loudnorm 注入（has_audio=True）
# ---------------------------------------------------------------------------


class TestBuildPlanLoudnormWithAudio:
    """build_plan に loudness=loudnorm + has_audio=True を渡したとき loudnorm が注入される（ADR-L5）。"""

    def test_loudnorm_present_in_filter_complex(self) -> None:
        """loudnorm フィルタ文字列が filter_complex に含まれる。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert "loudnorm" in plan.filter_complex

    def test_loudnorm_target_i_in_filter_complex(self) -> None:
        """I=-14 が filter_complex の loudnorm パラメータに含まれる。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert "I=-14" in plan.filter_complex

    def test_loudnorm_target_tp_in_filter_complex(self) -> None:
        """TP=-1 が filter_complex の loudnorm パラメータに含まれる。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert "TP=-1" in plan.filter_complex

    def test_loudnorm_linear_true_in_filter_complex(self) -> None:
        """linear=true が filter_complex に含まれる（ADR-L5 二段適用の要件）。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert "linear=true" in plan.filter_complex

    def test_loudnorm_measured_i_in_filter_complex(self) -> None:
        """measured_I が filter_complex に含まれる（linear 二段適用の核心）。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert "measured_I=" in plan.filter_complex

    def test_loudnorm_measured_tp_in_filter_complex(self) -> None:
        """measured_TP が filter_complex に含まれる。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert "measured_TP=" in plan.filter_complex

    def test_loudnorm_measured_lra_in_filter_complex(self) -> None:
        """measured_LRA が filter_complex に含まれる。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert "measured_LRA=" in plan.filter_complex

    def test_loudnorm_measured_thresh_in_filter_complex(self) -> None:
        """measured_thresh が filter_complex に含まれる。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert "measured_thresh=" in plan.filter_complex

    def test_outa_ln_label_in_filter_complex(self) -> None:
        """[outa_ln] ラベルが filter_complex に含まれる（concat 後 loudnorm 出力）。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert "[outa_ln]" in plan.filter_complex

    def test_audio_map_is_outa_ln(self) -> None:
        """ffmpeg_args の -map が [outa_ln] に差し替えられている。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa_ln]" in args_str
        assert "-map [outa]" not in args_str

    def test_loudnorm_position_after_concat(self) -> None:
        """loudnorm 行は concat 行より後に現れる（ADR-L5 順序）。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        fc = plan.filter_complex
        concat_pos = fc.index("concat=")
        loudnorm_pos = fc.index("loudnorm")
        assert loudnorm_pos > concat_pos, (
            f"loudnorm({loudnorm_pos}) は concat({concat_pos}) より後に現れるべき"
        )

    def test_filter_complex_is_single_string_with_loudnorm(self) -> None:
        """loudness 指示があっても filter_complex は単一文字列（コマンドインジェクション防止）。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_LOUDNORM_DIRECTIVE
        )
        assert isinstance(plan.filter_complex, str)


# ---------------------------------------------------------------------------
# build_plan — peak 注入
# ---------------------------------------------------------------------------


class TestBuildPlanPeakMode:
    """build_plan に loudness=peak を渡したとき volume フィルタが注入される。"""

    def test_volume_filter_present_in_filter_complex(self) -> None:
        """volume フィルタ文字列が filter_complex に含まれる。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_PEAK_DIRECTIVE
        )
        assert "volume=" in plan.filter_complex

    def test_peak_audio_map_replaced(self) -> None:
        """peak モードでも audio map が適切なラベルに差し替えられている。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), loudness=_VALID_PEAK_DIRECTIVE
        )
        args_str = " ".join(plan.ffmpeg_args)
        # [outa] のままでないこと（何らかのラベルに差し替えられている）
        assert "-map [outa]" not in args_str

    def test_peak_and_denoise_adds_warning(self) -> None:
        """peak + denoise 併用時に warning が追加される（DC-AM-002）。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(),
            denoise=_VALID_AFFTDN_DIRECTIVE,
            loudness=_VALID_PEAK_DIRECTIVE,
        )
        warning_text = " ".join(plan.warnings)
        assert len(plan.warnings) > 0
        assert any(
            kw in warning_text
            for kw in ("peak", "denoise", "warning", "測定", "ずれ", "警告")
        )


# ---------------------------------------------------------------------------
# build_plan — denoise + loudnorm 共存（ADR-L5/L5b）
# ---------------------------------------------------------------------------


class TestBuildPlanDenoiseAndLoudnorm:
    """denoise + loudnorm 共存時の filter_complex 連鎖と audio map 終端ラベル検証（ADR-L5b・DC-AM-001）。"""

    def test_afftdn_before_loudnorm_in_filter_complex(self) -> None:
        """afftdn が loudnorm より前に現れる（denoise → loudnorm の順序・ADR-L5）。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(),
            denoise=_VALID_AFFTDN_DIRECTIVE,
            loudness=_VALID_LOUDNORM_DIRECTIVE,
        )
        fc = plan.filter_complex
        afftdn_pos = fc.index("afftdn")
        loudnorm_pos = fc.index("loudnorm")
        assert afftdn_pos < loudnorm_pos, (
            f"afftdn({afftdn_pos}) は loudnorm({loudnorm_pos}) より前に現れるべき"
        )

    def test_outa_dn_feeds_loudnorm_chain(self) -> None:
        """[outa_dn] が loudnorm フィルタの入力ラベルとして現れる（累積連鎖 ADR-L5b）。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(),
            denoise=_VALID_AFFTDN_DIRECTIVE,
            loudness=_VALID_LOUDNORM_DIRECTIVE,
        )
        fc = plan.filter_complex
        # [outa_dn]loudnorm または [outa_dn]... loudnorm の形式であることを確認
        assert "[outa_dn]" in fc
        # [outa_dn] の後に loudnorm が続く（連鎖）
        dn_pos = fc.index("[outa_dn]")
        ln_pos = fc.index("loudnorm")
        assert dn_pos < ln_pos, "[outa_dn] は loudnorm より前に現れるべき（連鎖入力）"

    def test_audio_map_terminal_is_outa_ln_when_both(self) -> None:
        """denoise + loudnorm 共存時の audio map 終端は [outa_ln]（ADR-L5b）。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(),
            denoise=_VALID_AFFTDN_DIRECTIVE,
            loudness=_VALID_LOUDNORM_DIRECTIVE,
        )
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa_ln]" in args_str
        assert "-map [outa]" not in args_str
        assert "-map [outa_dn]" not in args_str


# ---------------------------------------------------------------------------
# build_plan — audio map 終端ラベルの網羅検証（{denoise}×{loudness}×{scale}）
# ---------------------------------------------------------------------------


class TestAudioMapTerminalLabel:
    """{denoise 有無}×{loudness 有無}×{scale 有無} の組み合わせで終端 map ラベルを網羅検証（ADR-L5b）。

    denoise=True は afftdn、loudness=True は loudnorm、scale=True は width/height 指定を意味する。
    """

    def _plan(
        self,
        denoise: bool = False,
        loudness: bool = False,
        scale: bool = False,
        audio_count: int = 1,
    ) -> Any:
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=audio_count, bit_rate=None)
        opts = RenderOptions(width=1280, height=720) if scale else RenderOptions()
        return build_plan(
            ranges,
            probe,
            opts,
            denoise=_VALID_AFFTDN_DIRECTIVE if denoise else None,
            loudness=_VALID_LOUDNORM_DIRECTIVE if loudness else None,
        )

    def test_no_denoise_no_loudness_map_outa(self) -> None:
        """denoise なし・loudness なし → audio map は [outa]（後方互換）。"""
        plan = self._plan(denoise=False, loudness=False)
        args_str = " ".join(plan.ffmpeg_args)
        assert "-map [outa]" in args_str

    def test_denoise_only_map_outa_dn(self) -> None:
        """denoise あり・loudness なし → audio map は [outa_dn]。"""
        plan = self._plan(denoise=True, loudness=False)
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa_dn]" in args_str
        assert "-map [outa]" not in args_str

    def test_loudness_only_map_outa_ln(self) -> None:
        """denoise なし・loudness あり → audio map は [outa_ln]。"""
        plan = self._plan(denoise=False, loudness=True)
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa_ln]" in args_str
        assert "-map [outa]" not in args_str

    def test_denoise_and_loudness_map_outa_ln(self) -> None:
        """denoise あり・loudness あり → audio map は [outa_ln]（累積終端）。"""
        plan = self._plan(denoise=True, loudness=True)
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa_ln]" in args_str
        assert "-map [outa]" not in args_str
        assert "-map [outa_dn]" not in args_str

    def test_scale_and_loudness_has_outvscaled_and_outa_ln(self) -> None:
        """scale + loudness 両指定 → [outvscaled] と [outa_ln] が ffmpeg_args に共存する。"""
        plan = self._plan(denoise=False, loudness=True, scale=True)
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outvscaled]" in args_str, "scale 指定時は [outvscaled] が必要"
        assert "[outa_ln]" in args_str, "loudness 指定時は [outa_ln] が必要"

    def test_all_three_has_outvscaled_and_outa_ln(self) -> None:
        """denoise + scale + loudness すべて → [outvscaled] と [outa_ln] が ffmpeg_args に共存する。"""
        plan = self._plan(denoise=True, loudness=True, scale=True)
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outvscaled]" in args_str
        assert "[outa_ln]" in args_str
        assert "-map [outa]" not in args_str
        assert "-map [outa_dn]" not in args_str

    def test_no_audio_no_loudnorm_in_filter(self) -> None:
        """has_audio=False + loudness → loudnorm が filter_complex に混入しない。"""
        plan = self._plan(denoise=False, loudness=True, audio_count=0)
        assert "loudnorm" not in plan.filter_complex

    def test_no_audio_loudness_adds_warning(self) -> None:
        """has_audio=False + loudness → warnings に追加される。"""
        plan = self._plan(denoise=False, loudness=True, audio_count=0)
        warning_text = " ".join(plan.warnings)
        assert len(plan.warnings) > 0
        assert any(
            kw in warning_text for kw in ("loudness", "音声", "skip", "スキップ")
        )


# ---------------------------------------------------------------------------
# build_plan — loudness なし（後方互換）
# ---------------------------------------------------------------------------


class TestBuildPlanLoudnessNone:
    """loudness=None のとき既存ロジックと完全同一（後方互換保証・ADR-L6）。"""

    def test_no_loudnorm_without_loudness(self) -> None:
        """loudness=None: loudnorm が filter_complex に含まれない。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "loudnorm" not in plan.filter_complex

    def test_no_outa_ln_without_loudness(self) -> None:
        """loudness=None: [outa_ln] が filter_complex / ffmpeg_args に含まれない。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "[outa_ln]" not in plan.filter_complex
        assert "[outa_ln]" not in " ".join(plan.ffmpeg_args)

    def test_audio_map_is_outa_without_loudness(self) -> None:
        """loudness=None: 音声あり時の audio map は [outa] のまま（後方互換）。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa]" in args_str

    def test_explicit_none_same_as_omitted(self) -> None:
        """loudness=None 明示と省略が同一の filter_complex を生成する（ADR-L6）。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan_omitted = build_plan(ranges, probe, RenderOptions())
        plan_explicit_none = build_plan(ranges, probe, RenderOptions(), loudness=None)
        assert plan_omitted.filter_complex == plan_explicit_none.filter_complex
        assert plan_omitted.ffmpeg_args == plan_explicit_none.ffmpeg_args


# ---------------------------------------------------------------------------
# build_plan — 不正 LoudnessDirective → INVALID_INPUT
# ---------------------------------------------------------------------------


class TestBuildPlanLoudnessInvalidDirective:
    """不正な loudness 指示は INVALID_INPUT。"""

    def test_target_i_out_of_range_raises_invalid_input(self) -> None:
        """target.i が範囲外（> -5）→ INVALID_INPUT。"""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_LOUDNORM_DIRECTIVE,
            "target": {"i": -3.0, "tp": -1.0, "lra": 11.0},  # i > -5 は範囲外
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_target_i_too_low_raises_invalid_input(self) -> None:
        """target.i が範囲外（< -70）→ INVALID_INPUT。"""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_LOUDNORM_DIRECTIVE,
            "target": {"i": -75.0, "tp": -1.0, "lra": 11.0},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_target_tp_out_of_range_raises_invalid_input(self) -> None:
        """target.tp が範囲外（> 0）→ INVALID_INPUT。"""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_LOUDNORM_DIRECTIVE,
            "target": {"i": -14.0, "tp": 1.0, "lra": 11.0},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_target_lra_out_of_range_raises_invalid_input(self) -> None:
        """target.lra が範囲外（> 50）→ INVALID_INPUT。"""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_LOUDNORM_DIRECTIVE,
            "target": {"i": -14.0, "tp": -1.0, "lra": 55.0},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_invalid_mode_raises_invalid_input(self) -> None:
        """mode が loudnorm/peak 以外 → INVALID_INPUT。"""
        from clipwright_render.plan import build_plan

        directive = {**_VALID_LOUDNORM_DIRECTIVE, "mode": "unknown_mode"}
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_invalid_scope_raises_invalid_input(self) -> None:
        """scope が track 以外（per_clip）→ INVALID_INPUT（per_clip は今回スコープ外）。"""
        from clipwright_render.plan import build_plan

        directive = {**_VALID_LOUDNORM_DIRECTIVE, "scope": "per_clip"}
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_loudnorm_missing_measured_raises_invalid_input(self) -> None:
        """loudnorm で measured が None → INVALID_INPUT（linear 適用に必須）。"""
        from clipwright_render.plan import build_plan

        directive = {**_VALID_LOUDNORM_DIRECTIVE, "measured": None}
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_measured_input_i_inf_raises_invalid_input(self) -> None:
        """measured.input_i=inf → INVALID_INPUT（allow_inf_nan=False）。"""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_LOUDNORM_DIRECTIVE,
            "measured": {
                **_VALID_LOUDNORM_DIRECTIVE["measured"],
                "input_i": float("inf"),
            },
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_measured_input_i_nan_raises_invalid_input(self) -> None:
        """measured.input_i=nan → INVALID_INPUT（allow_inf_nan=False）。"""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_LOUDNORM_DIRECTIVE,
            "measured": {
                **_VALID_LOUDNORM_DIRECTIVE["measured"],
                "input_i": float("nan"),
            },
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_peak_target_out_of_range_raises_invalid_input(self) -> None:
        """peak mode で peak_db > 0 → INVALID_INPUT。"""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_PEAK_DIRECTIVE,
            "target": {"peak_db": 3.0},  # > 0 は範囲外
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_error_message_not_contain_sensitive_value(self) -> None:
        """不正 directive のエラーメッセージに入力値が混入しない（SR M-1）。"""
        from clipwright_render.plan import build_plan

        directive = {**_VALID_LOUDNORM_DIRECTIVE, "mode": "INJECTED_SENSITIVE_VALUE"}
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), loudness=directive)
        assert "INJECTED_SENSITIVE_VALUE" not in exc_info.value.message


# ---------------------------------------------------------------------------
# render_timeline — LoudnessDirective 検証・get_clipwright_metadata 読み出し
# ---------------------------------------------------------------------------


class TestRenderTimelineLoudnessDirective:
    """render_timeline が timeline metadata から LoudnessDirective を読み出し build_plan に渡す経路。"""

    def _write_timeline_with_loudness(
        self,
        tmp_path: Path,
        loudness_directive: dict[str, Any] | None,
        denoise_directive: dict[str, Any] | None = None,
        source_name: str = "source.mp4",
    ) -> tuple[Path, Path, Path]:
        """OTIO ファイルを tmp_path に書き出す。"""
        source_path = tmp_path / source_name
        source_path.write_bytes(b"fake")

        tl = _make_timeline(
            [_make_clip(str(source_path), 0.0, 5.0)],
            loudness_directive=loudness_directive,
            denoise_directive=denoise_directive,
        )
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        output_path = tmp_path / "out.mp4"
        return timeline_path, source_path, output_path

    def _fake_media_info(self, source_path: Path) -> Any:
        from clipwright.schemas import MediaInfo, StreamInfo

        return MediaInfo(
            path=str(source_path),
            container="mov,mp4,m4a,3gp,3g2,mj2",
            duration=None,
            streams=[
                StreamInfo(index=0, codec_type="video", codec_name="h264"),
                StreamInfo(index=1, codec_type="audio", codec_name="aac"),
            ],
            bit_rate=_TEST_BIT_RATE,
        )

    def test_render_reads_loudnorm_from_metadata_and_injects(
        self, tmp_path: Path
    ) -> None:
        """render_timeline が timeline metadata の loudness を読み出し filter_complex に loudnorm を注入する。"""
        from clipwright_render.render import render_timeline

        timeline_path, source_path, output_path = self._write_timeline_with_loudness(
            tmp_path, _VALID_LOUDNORM_DIRECTIVE
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=self._fake_media_info(source_path),
        ):
            result = render_timeline(
                str(timeline_path),
                str(output_path),
                RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"dry_run が失敗した: {result}"
        fc = result["data"]["filter_complex"]
        assert "loudnorm" in fc, f"loudnorm が filter_complex に含まれていない: {fc}"
        assert "linear=true" in fc, (
            f"linear=true が filter_complex に含まれていない: {fc}"
        )

    def test_render_no_loudness_metadata_backward_compatible(
        self, tmp_path: Path
    ) -> None:
        """loudness メタデータなしの timeline は後方互換で既存ロジックと同一。"""
        from clipwright_render.render import render_timeline

        timeline_path, source_path, output_path = self._write_timeline_with_loudness(
            tmp_path, None
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=self._fake_media_info(source_path),
        ):
            result = render_timeline(
                str(timeline_path),
                str(output_path),
                RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"後方互換テストが失敗した: {result}"
        fc = result["data"]["filter_complex"]
        assert "loudnorm" not in fc, f"loudnorm が誤って含まれている: {fc}"

    def test_render_invalid_loudness_directive_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """不正な loudness 指示（mode が不正）→ ok=False / code=INVALID_INPUT。"""
        from clipwright_render.render import render_timeline

        bad_directive = {**_VALID_LOUDNORM_DIRECTIVE, "mode": "bad_mode"}
        timeline_path, source_path, output_path = self._write_timeline_with_loudness(
            tmp_path, bad_directive
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=self._fake_media_info(source_path),
        ):
            result = render_timeline(
                str(timeline_path),
                str(output_path),
                RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value

    def test_render_loudness_scope_per_clip_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """scope=per_clip → ok=False / code=INVALID_INPUT（per_clip は今回スコープ外）。"""
        from clipwright_render.render import render_timeline

        bad_directive = {**_VALID_LOUDNORM_DIRECTIVE, "scope": "per_clip"}
        timeline_path, source_path, output_path = self._write_timeline_with_loudness(
            tmp_path, bad_directive
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=self._fake_media_info(source_path),
        ):
            result = render_timeline(
                str(timeline_path),
                str(output_path),
                RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value

    def test_render_denoise_and_loudnorm_both_present(self, tmp_path: Path) -> None:
        """denoise + loudnorm 両指定時に filter_complex に afftdn と loudnorm が共存する（ADR-L5）。"""
        from clipwright_render.render import render_timeline

        timeline_path, source_path, output_path = self._write_timeline_with_loudness(
            tmp_path,
            loudness_directive=_VALID_LOUDNORM_DIRECTIVE,
            denoise_directive=_VALID_AFFTDN_DIRECTIVE,
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=self._fake_media_info(source_path),
        ):
            result = render_timeline(
                str(timeline_path),
                str(output_path),
                RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"dry_run が失敗した: {result}"
        fc = result["data"]["filter_complex"]
        assert "afftdn" in fc, f"afftdn が filter_complex に含まれていない: {fc}"
        assert "loudnorm" in fc, f"loudnorm が filter_complex に含まれていない: {fc}"
