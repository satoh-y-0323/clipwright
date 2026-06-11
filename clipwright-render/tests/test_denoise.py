"""test_denoise.py — clipwright-render の denoise 適用拡張 Red テスト（DC-AS-005/AS-006/GP-001 B-2）。

対象:
  - build_plan(ranges, probe_info, options, denoise=...) — afftdn 注入・has_audio 分岐・scale 共存
  - render_timeline() — DenoiseDirective 検証・get_clipwright_metadata 読み出し

設計根拠（architecture-report-20260611-090313 §3 / 20260611-092647 §B-2）:
  - backend=afftdn ＋ has_audio=True: concat 後 [outa] に afftdn を注入し map を [outa_dn] に差し替える
  - backend=afftdn ＋ has_audio=False: afftdn を入れず warnings に「音声なしのため denoise スキップ」を追加
  - scale ＋ afftdn 両指定: filter_complex に [outvscaled] と [outa_dn] の両 map を持つ（B-2）
  - backend=deepfilternet: UNSUPPORTED_OPERATION（hint 付き）
  - denoise なし: 既存ロジックと完全同一（後方互換）
  - 不正 directive: INVALID_INPUT（nr 型/範囲外、nt 不正値、未知 backend、params 欠落）

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
# ヘルパー: OTIO 構築
# ---------------------------------------------------------------------------

FPS = 30.0


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
    denoise_directive: dict[str, Any] | None = None,
) -> otio.schema.Timeline:
    """単一 video トラックの Timeline を生成する。

    denoise_directive が指定された場合は set_clipwright_metadata 経由で
    timeline-level metadata に書き込む（CR L-5: otio_utils ヘルパー統一）。
    """
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    for clip in clips:
        track.append(clip)
    tl = otio.schema.Timeline()
    tl.tracks.append(track)
    if denoise_directive is not None:
        from clipwright.otio_utils import get_clipwright_metadata

        existing = get_clipwright_metadata(tl)
        existing["denoise"] = denoise_directive
        set_clipwright_metadata(tl, existing)
    return tl


def _single_range(source: str = "/src/a.mp4") -> list[KeptRange]:
    """1区間の KeptRange リストを返すヘルパー。"""
    from clipwright_render.plan import resolve_kept_ranges

    tl = _make_timeline([_make_clip(source, 0.0, 5.0)])
    return resolve_kept_ranges(tl)


# 有効な afftdn denoise 指示の辞書（テスト共通ベース）
_VALID_AFFTDN_DIRECTIVE: dict[str, Any] = {
    "tool": "clipwright-noise",
    "version": "0.1.0",
    "kind": "denoise",
    "backend": "afftdn",
    "params": {"nr": 12.0, "nf": -50.0, "nt": "w"},
}

# deepfilternet 指示（params は空）
_VALID_DEEPFILTERNET_DIRECTIVE: dict[str, Any] = {
    "tool": "clipwright-noise",
    "version": "0.1.0",
    "kind": "denoise",
    "backend": "deepfilternet",
    "params": {},
}


# ---------------------------------------------------------------------------
# build_plan — afftdn 注入（has_audio=True）
# ---------------------------------------------------------------------------


class TestBuildPlanDenoiseAfftdnWithAudio:
    """build_plan に denoise=afftdn + has_audio=True を渡したとき afftdn が注入される（DC-AS-005/B-2）。"""

    def test_afftdn_present_in_filter_complex(self) -> None:
        """afftdn フィルタ文字列が filter_complex に含まれる。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), denoise=_VALID_AFFTDN_DIRECTIVE
        )
        assert "afftdn" in plan.filter_complex

    def test_afftdn_uses_nr_from_params(self) -> None:
        """afftdn の nr パラメータが params.nr の値と一致する。"""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": 12.0, "nf": -50.0, "nt": "w"},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert "nr=12" in plan.filter_complex

    def test_afftdn_uses_nf_from_params(self) -> None:
        """afftdn の nf パラメータが params.nf の値と一致する。"""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": 12.0, "nf": -50.0, "nt": "w"},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert "nf=-50" in plan.filter_complex

    def test_afftdn_uses_nt_from_params(self) -> None:
        """afftdn の nt パラメータが params.nt の値と一致する。"""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": 12.0, "nf": -50.0, "nt": "w"},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert "nt=w" in plan.filter_complex

    def test_outa_dn_label_in_filter_complex(self) -> None:
        """[outa_dn] ラベルが filter_complex に含まれる（concat 後 [outa] を afftdn に通した出力）。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), denoise=_VALID_AFFTDN_DIRECTIVE
        )
        assert "[outa_dn]" in plan.filter_complex

    def test_audio_map_is_outa_dn(self) -> None:
        """ffmpeg_args の -map が [outa_dn] に差し替えられている（[outa] のままではない）。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), denoise=_VALID_AFFTDN_DIRECTIVE
        )
        args_str = " ".join(plan.ffmpeg_args)
        # [outa_dn] が -map の値として現れる
        assert "[outa_dn]" in args_str
        # 生の [outa] が -map の値として残っていない
        # ("[outa]" は filter_complex 内のラベルとして出現するが ffmpeg_args には [outa_dn] だけ)
        # -map [outa] ではなく -map [outa_dn] になっていることを確認する
        assert "-map [outa_dn]" in args_str or (
            args_str.count("[outa_dn]") >= 1 and "-map [outa]" not in args_str
        )

    def test_afftdn_position_after_concat(self) -> None:
        """afftdn 行は concat 行より後に現れる（B-2 順序固定）。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), denoise=_VALID_AFFTDN_DIRECTIVE
        )
        fc = plan.filter_complex
        concat_pos = fc.index("concat=")
        afftdn_pos = fc.index("afftdn")
        assert afftdn_pos > concat_pos, (
            f"afftdn({afftdn_pos}) は concat({concat_pos}) より後に現れるべき"
        )

    def test_filter_complex_is_single_string(self) -> None:
        """denoise 指示があっても filter_complex は単一文字列（コマンドインジェクション防止）。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), denoise=_VALID_AFFTDN_DIRECTIVE
        )
        assert isinstance(plan.filter_complex, str)


# ---------------------------------------------------------------------------
# build_plan — afftdn + has_audio=False（DC-AS-005）
# ---------------------------------------------------------------------------


class TestBuildPlanDenoiseAfftdnNoAudio:
    """has_audio=False ＋ denoise 指示 → afftdn 非注入 ＋ warnings（DC-AS-005）。"""

    def test_afftdn_not_in_filter_complex_when_no_audio(self) -> None:
        """音声なしのとき afftdn が filter_complex に含まれない。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), denoise=_VALID_AFFTDN_DIRECTIVE
        )
        assert "afftdn" not in plan.filter_complex

    def test_outa_dn_not_in_ffmpeg_args_when_no_audio(self) -> None:
        """音声なしのとき [outa_dn] が ffmpeg_args に含まれない。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), denoise=_VALID_AFFTDN_DIRECTIVE
        )
        assert "[outa_dn]" not in " ".join(plan.ffmpeg_args)

    def test_warning_added_when_no_audio(self) -> None:
        """音声なし ＋ denoise 指示 → warnings に「denoise スキップ」メッセージが追加される。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(
            ranges, probe, RenderOptions(), denoise=_VALID_AFFTDN_DIRECTIVE
        )
        assert len(plan.warnings) > 0
        warning_text = " ".join(plan.warnings)
        # denoise スキップを示す何らかのテキストが含まれる
        assert any(
            kw in warning_text.lower()
            for kw in ("denoise", "skip", "スキップ", "音声なし")
        )


# ---------------------------------------------------------------------------
# build_plan — scale ＋ afftdn 両指定（B-2）
# ---------------------------------------------------------------------------


class TestBuildPlanDenoiseWithScale:
    """scale ＋ afftdn 両指定時に [outvscaled] と [outa_dn] の両 map を持つ（B-2）。"""

    def test_both_outvscaled_and_outa_dn_in_ffmpeg_args(self) -> None:
        """scale ＋ afftdn 指定: ffmpeg_args に [outvscaled] と [outa_dn] が共存する。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(width=1280, height=720),
            denoise=_VALID_AFFTDN_DIRECTIVE,
        )
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outvscaled]" in args_str, (
            "scale 指定時は [outvscaled] が ffmpeg_args に必要"
        )
        assert "[outa_dn]" in args_str, (
            "afftdn 適用時は [outa_dn] が ffmpeg_args に必要"
        )

    def test_scale_in_filter_complex_with_afftdn(self) -> None:
        """scale ＋ afftdn: filter_complex に scale と afftdn の両方が含まれる。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(width=1280, height=720),
            denoise=_VALID_AFFTDN_DIRECTIVE,
        )
        assert "scale=1280:720" in plan.filter_complex
        assert "afftdn" in plan.filter_complex

    def test_no_vf_in_ffmpeg_args_with_afftdn_and_scale(self) -> None:
        """-vf は ffmpeg_args に含まれない（filter_complex と競合するため禁止）。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(width=1280, height=720),
            denoise=_VALID_AFFTDN_DIRECTIVE,
        )
        assert "-vf" not in plan.ffmpeg_args


# ---------------------------------------------------------------------------
# build_plan — backend=deepfilternet → UNSUPPORTED_OPERATION
# ---------------------------------------------------------------------------


class TestBuildPlanDenoiseDeepfilternet:
    """backend=deepfilternet → UNSUPPORTED_OPERATION（hint 付き）。"""

    def test_deepfilternet_raises_unsupported(self) -> None:
        """deepfilternet → ClipwrightError(UNSUPPORTED_OPERATION)。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(
                ranges, probe, RenderOptions(), denoise=_VALID_DEEPFILTERNET_DIRECTIVE
            )
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION

    def test_deepfilternet_error_has_hint(self) -> None:
        """deepfilternet エラーには hint（代替案を示す）が含まれる。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(
                ranges, probe, RenderOptions(), denoise=_VALID_DEEPFILTERNET_DIRECTIVE
            )
        assert exc_info.value.hint, "hint が空であってはならない"
        # hint は実質的な代替案（afftdn への切替 or 将来版）を示すこと（NR-L-3）
        assert "afftdn" in exc_info.value.hint or "将来" in exc_info.value.hint


# ---------------------------------------------------------------------------
# build_plan — denoise=None（後方互換）
# ---------------------------------------------------------------------------


class TestBuildPlanDenoiseNone:
    """denoise=None のとき既存ロジックと完全同一（後方互換保証）。"""

    def test_no_afftdn_without_denoise(self) -> None:
        """denoise=None: afftdn が filter_complex に含まれない。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        # denoise 引数なしで呼ぶ（既存インターフェース）
        plan = build_plan(ranges, probe, RenderOptions())
        assert "afftdn" not in plan.filter_complex

    def test_no_outa_dn_without_denoise(self) -> None:
        """denoise=None: [outa_dn] が filter_complex / ffmpeg_args に含まれない。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "[outa_dn]" not in plan.filter_complex
        assert "[outa_dn]" not in " ".join(plan.ffmpeg_args)

    def test_audio_map_is_outa_without_denoise(self) -> None:
        """denoise=None: 音声あり時の audio map は [outa] のまま（後方互換）。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa]" in args_str

    def test_explicit_none_denoise_same_as_omitted(self) -> None:
        """denoise=None 明示と省略が同一の filter_complex を生成する。"""
        from clipwright_render.plan import build_plan

        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan_omitted = build_plan(ranges, probe, RenderOptions())
        plan_explicit_none = build_plan(ranges, probe, RenderOptions(), denoise=None)
        assert plan_omitted.filter_complex == plan_explicit_none.filter_complex
        assert plan_omitted.ffmpeg_args == plan_explicit_none.ffmpeg_args


# ---------------------------------------------------------------------------
# build_plan — 不正 denoise directive → INVALID_INPUT（DC-AS-006）
# ---------------------------------------------------------------------------


class TestBuildPlanDenoiseInvalidDirective:
    """不正な denoise 指示は INVALID_INPUT（DC-AS-006 厳格検証）。"""

    def test_nr_as_string_raises_invalid_input(self) -> None:
        """params.nr が文字列 → INVALID_INPUT。"""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": "bad", "nf": -50.0, "nt": "w"},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_nr_out_of_range_raises_invalid_input(self) -> None:
        """params.nr が範囲外（>97）→ INVALID_INPUT（AfftdnParams ge=0.01 le=97）。"""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": 100.0, "nf": -50.0, "nt": "w"},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_nr_zero_raises_invalid_input(self) -> None:
        """params.nr=0.0（ge=0.01 未満）→ INVALID_INPUT。"""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": 0.0, "nf": -50.0, "nt": "w"},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_nt_invalid_value_raises_invalid_input(self) -> None:
        """params.nt が Literal["w","v"] 以外 → INVALID_INPUT。"""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": 12.0, "nf": -50.0, "nt": "x"},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_unknown_backend_raises_invalid_input(self) -> None:
        """未知の backend → INVALID_INPUT（Literal 検証失敗）。"""
        from clipwright_render.plan import build_plan

        directive = {**_VALID_AFFTDN_DIRECTIVE, "backend": "unknown_backend"}
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_missing_params_raises_invalid_input(self) -> None:
        """params キーが存在しない → INVALID_INPUT。"""
        from clipwright_render.plan import build_plan

        directive = {
            "tool": "clipwright-noise",
            "version": "0.1.0",
            "kind": "denoise",
            "backend": "afftdn",
            # params フィールドなし
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_nf_out_of_range_raises_invalid_input(self) -> None:
        """params.nf が範囲外（>-20）→ INVALID_INPUT（AfftdnParams ge=-80 le=-20）。"""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": 12.0, "nf": -10.0, "nt": "w"},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_measured_noise_floor_inf_raises_invalid_input(self) -> None:
        """measured_noise_floor_db=inf → INVALID_INPUT（SR L-3: inf/nan 排除）。"""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "measured_noise_floor_db": float("inf"),
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_measured_noise_floor_nan_raises_invalid_input(self) -> None:
        """measured_noise_floor_db=nan → INVALID_INPUT（SR L-3: inf/nan 排除）。"""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "measured_noise_floor_db": float("nan"),
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_error_message_does_not_contain_exc_detail(self) -> None:
        """不正 directive のエラーメッセージに ValidationError の詳細が含まれない（SR M-1）。"""
        from clipwright_render.plan import build_plan

        directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": "INJECTED_SENSITIVE_VALUE", "nf": -50.0, "nt": "w"},
        }
        ranges = _single_range()
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions(), denoise=directive)
        # 入力値がエラーメッセージに混入していないことを確認
        assert "INJECTED_SENSITIVE_VALUE" not in exc_info.value.message
        # 例外チェーンが切断されている（from None）
        assert exc_info.value.__cause__ is None


# ---------------------------------------------------------------------------
# render_timeline — DenoiseDirective 検証・get_clipwright_metadata 読み出し
# ---------------------------------------------------------------------------


class TestRenderTimelineDenoiseDirective:
    """render_timeline が timeline metadata から DenoiseDirective を読み出し build_plan に渡す経路。"""

    def _write_timeline_with_denoise(
        self,
        tmp_path: Path,
        denoise_directive: dict[str, Any] | None,
        source_name: str = "source.mp4",
    ) -> tuple[Path, Path, Path]:
        """OTIO ファイルを tmp_path に書き出す。

        Returns:
            (timeline_path, source_path, output_path) のタプル。
        """
        source_path = tmp_path / source_name
        source_path.write_bytes(b"fake")  # ファイル存在確認を通す

        tl = _make_timeline(
            [_make_clip(str(source_path), 0.0, 5.0)],
            denoise_directive=denoise_directive,
        )
        timeline_path = tmp_path / "timeline.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        output_path = tmp_path / "out.mp4"
        return timeline_path, source_path, output_path

    def test_render_reads_denoise_from_metadata_and_passes_to_build_plan(
        self, tmp_path: Path
    ) -> None:
        """render_timeline が timeline metadata の denoise を読み出し build_plan に渡す。

        build_plan が denoise=afftdn を受け取ったとき filter_complex に afftdn が
        含まれることを dry_run で確認する（実 ffmpeg は呼ばない）。
        """
        from clipwright.schemas import MediaInfo, StreamInfo

        from clipwright_render.render import render_timeline

        timeline_path, source_path, output_path = self._write_timeline_with_denoise(
            tmp_path, _VALID_AFFTDN_DIRECTIVE
        )

        fake_info = MediaInfo(
            path=str(source_path),
            container="mov,mp4,m4a,3gp,3g2,mj2",
            duration=None,
            streams=[
                StreamInfo(index=0, codec_type="video", codec_name="h264"),
                StreamInfo(index=1, codec_type="audio", codec_name="aac"),
            ],
            bit_rate=8_000_000,
        )

        with patch("clipwright_render.render.inspect_media", return_value=fake_info):
            result = render_timeline(
                str(timeline_path),
                str(output_path),
                RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"dry_run が失敗した: {result}"
        fc = result["data"]["filter_complex"]
        assert "afftdn" in fc, f"afftdn が filter_complex に含まれていない: {fc}"

    def test_render_no_denoise_metadata_backward_compatible(
        self, tmp_path: Path
    ) -> None:
        """denoise メタデータなしの timeline は後方互換で既存ロジックと同一。"""
        from clipwright.schemas import MediaInfo, StreamInfo

        from clipwright_render.render import render_timeline

        timeline_path, source_path, output_path = self._write_timeline_with_denoise(
            tmp_path,
            None,  # denoise なし
        )

        fake_info = MediaInfo(
            path=str(source_path),
            container="mov,mp4,m4a,3gp,3g2,mj2",
            duration=None,
            streams=[
                StreamInfo(index=0, codec_type="video", codec_name="h264"),
                StreamInfo(index=1, codec_type="audio", codec_name="aac"),
            ],
            bit_rate=8_000_000,
        )

        with patch("clipwright_render.render.inspect_media", return_value=fake_info):
            result = render_timeline(
                str(timeline_path),
                str(output_path),
                RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"後方互換テストが失敗した: {result}"
        fc = result["data"]["filter_complex"]
        assert "afftdn" not in fc, f"afftdn が誤って含まれている: {fc}"

    def test_render_invalid_denoise_directive_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """不正な denoise 指示（nr が文字列）→ ok=False / code=INVALID_INPUT。"""
        from clipwright.schemas import MediaInfo, StreamInfo

        from clipwright_render.render import render_timeline

        bad_directive = {
            **_VALID_AFFTDN_DIRECTIVE,
            "params": {"nr": "bad", "nf": -50.0, "nt": "w"},
        }
        timeline_path, source_path, output_path = self._write_timeline_with_denoise(
            tmp_path, bad_directive
        )

        fake_info = MediaInfo(
            path=str(source_path),
            container="mov,mp4,m4a,3gp,3g2,mj2",
            duration=None,
            streams=[
                StreamInfo(index=0, codec_type="video", codec_name="h264"),
                StreamInfo(index=1, codec_type="audio", codec_name="aac"),
            ],
            bit_rate=8_000_000,
        )

        with patch("clipwright_render.render.inspect_media", return_value=fake_info):
            result = render_timeline(
                str(timeline_path),
                str(output_path),
                RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value

    def test_render_deepfilternet_directive_returns_unsupported(
        self, tmp_path: Path
    ) -> None:
        """deepfilternet 指示 → ok=False / code=UNSUPPORTED_OPERATION。"""
        from clipwright.schemas import MediaInfo, StreamInfo

        from clipwright_render.render import render_timeline

        timeline_path, source_path, output_path = self._write_timeline_with_denoise(
            tmp_path, _VALID_DEEPFILTERNET_DIRECTIVE
        )

        fake_info = MediaInfo(
            path=str(source_path),
            container="mov,mp4,m4a,3gp,3g2,mj2",
            duration=None,
            streams=[
                StreamInfo(index=0, codec_type="video", codec_name="h264"),
                StreamInfo(index=1, codec_type="audio", codec_name="aac"),
            ],
            bit_rate=8_000_000,
        )

        with patch("clipwright_render.render.inspect_media", return_value=fake_info):
            result = render_timeline(
                str(timeline_path),
                str(output_path),
                RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.UNSUPPORTED_OPERATION.value
