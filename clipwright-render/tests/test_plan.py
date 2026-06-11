"""test_plan.py — plan.py（純ロジック）の Red テスト。

対象関数:
  - resolve_kept_ranges(timeline) -> list[KeptRange]
  - build_plan(ranges, probe_info, options) -> RenderPlan

plan.py は ffmpeg/ffprobe を一切実行しない純ロジック。
probe 結果（bit_rate/has_video/audio_count）は引数として渡す（DC-AM-007）。
OTIO Timeline はテスト内で直接構築する。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from pydantic import ValidationError

from clipwright_render.plan import ProbeInfo
from clipwright_render.schemas import RenderOptions

if TYPE_CHECKING:
    from clipwright_render.plan import RenderPlan

# ---------------------------------------------------------------------------
# ヘルパー: テスト内 Timeline 構築
# ---------------------------------------------------------------------------

FPS = 30.0
_EPSILON = 1e-6


def _rt(seconds: float, rate: float = FPS) -> otio.opentime.RationalTime:
    """秒を RationalTime に変換するヘルパー。"""
    return otio.opentime.RationalTime(seconds * rate, rate)


def _tr(start: float, duration: float, rate: float = FPS) -> otio.opentime.TimeRange:
    """start 秒・duration 秒の TimeRange を返す。"""
    return otio.opentime.TimeRange(
        start_time=_rt(start, rate),
        duration=_rt(duration, rate),
    )


def _make_clip(
    source: str,
    start: float,
    duration: float,
    rate: float = FPS,
) -> otio.schema.Clip:
    """source_range 付き Clip を生成する。"""
    clip = otio.schema.Clip()
    clip.media_reference = otio.schema.ExternalReference(target_url=source)
    clip.source_range = _tr(start, duration, rate)
    return clip


def _make_timeline_with_clips(
    clips: list[otio.schema.Clip | otio.schema.Gap | otio.schema.Transition],
    track_kind: str = otio.schema.TrackKind.Video,
) -> otio.schema.Timeline:
    """指定クリップを含む単一トラックの Timeline を生成する。"""
    track = otio.schema.Track(kind=track_kind)
    for item in clips:
        track.append(item)
    timeline = otio.schema.Timeline()
    timeline.tracks.append(track)
    return timeline


# ---------------------------------------------------------------------------
# resolve_kept_ranges テスト群
# ---------------------------------------------------------------------------


class TestResolveKeptRanges:
    """resolve_kept_ranges(timeline) の動作検証。"""

    def test_single_clip_returns_one_range(self) -> None:
        """Clip 1 件: (source, source_range) が正しく抽出される（DC-AS-005）。"""
        from clipwright_render.plan import resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        assert len(ranges) == 1
        assert ranges[0].source == "/src/a.mp4"
        assert ranges[0].source_range == _tr(0.0, 5.0)

    def test_multiple_clips_returns_multiple_ranges(self) -> None:
        """Clip 複数件: 全 Clip の (source, source_range) が順序通りに返る。"""
        from clipwright_render.plan import resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/a.mp4", 5.0, 2.0),
            _make_clip("/src/a.mp4", 10.0, 4.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        assert len(ranges) == 3
        assert ranges[1].source_range == _tr(5.0, 2.0)

    def test_gap_is_skipped(self) -> None:
        """Gap はスキップされ、前後の Clip のみが返る（DC-AS-006）。"""
        from clipwright_render.plan import resolve_kept_ranges

        gap = otio.schema.Gap(source_range=_tr(0.0, 2.0))
        clips: list[Any] = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            gap,
            _make_clip("/src/a.mp4", 7.0, 3.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        assert len(ranges) == 2

    def test_transition_raises_unsupported(self) -> None:
        """Transition 含む → UNSUPPORTED_OPERATION（DC-AS-006）。"""
        from clipwright_render.plan import resolve_kept_ranges

        transition = otio.schema.Transition()
        clips: list[Any] = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            transition,
            _make_clip("/src/a.mp4", 3.0, 3.0),
        ]
        tl = _make_timeline_with_clips(clips)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_kept_ranges(tl)
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION

    def test_no_video_track_raises_unsupported(self) -> None:
        """video トラック 0 本 → UNSUPPORTED_OPERATION（architecture §5・DC-AS-002）。

        M-2: video トラックが存在しない場合は「サポートしていない構成」として
        UNSUPPORTED_OPERATION を返す（設計書の INVALID_INPUT から変更）。
        """
        from clipwright_render.plan import resolve_kept_ranges

        # audio トラックのみ含む Timeline（video トラックなし）
        audio_track = otio.schema.Track(kind=otio.schema.TrackKind.Audio)
        audio_track.append(_make_clip("/src/a.mp4", 0.0, 3.0))
        tl = otio.schema.Timeline()
        tl.tracks.append(audio_track)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_kept_ranges(tl)
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION

    def test_two_video_tracks_raises_unsupported(self) -> None:
        """video トラック 2 本以上 → UNSUPPORTED_OPERATION（DC-AS-006）。"""
        from clipwright_render.plan import resolve_kept_ranges

        track1 = otio.schema.Track(kind=otio.schema.TrackKind.Video)
        track1.append(_make_clip("/src/a.mp4", 0.0, 3.0))
        track2 = otio.schema.Track(kind=otio.schema.TrackKind.Video)
        track2.append(_make_clip("/src/a.mp4", 0.0, 3.0))
        tl = otio.schema.Timeline()
        tl.tracks.append(track1)
        tl.tracks.append(track2)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_kept_ranges(tl)
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION

    def test_multiple_sources_returns_ranges_with_each_source(self) -> None:
        """target_url 不一致（複数ソース）→ 各 KeptRange が自分の source を保持する
        （観点1: resolve_kept_ranges は複数ソースを許容・DC-AS-005 旧挙動廃止）。"""
        from clipwright_render.plan import resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 1.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        # Arrange/Act: UNSUPPORTED_OPERATION を送出しないことを確認
        ranges = resolve_kept_ranges(tl)
        # Assert: 各 KeptRange が自分のソースを保持している
        assert len(ranges) == 2
        assert ranges[0].source == "/src/a.mp4"
        assert ranges[1].source == "/src/b.mp4"

    def test_multiple_sources_each_range_preserves_source_range(self) -> None:
        """複数ソースの Clip → 各 KeptRange が自分の source_range を保持する（観点1）。"""
        from clipwright_render.plan import resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 1.0, 4.0),
            _make_clip("/src/b.mp4", 2.0, 3.0),
            _make_clip("/src/a.mp4", 5.0, 1.5),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        assert len(ranges) == 3
        assert ranges[0].source_range == _tr(1.0, 4.0)
        assert ranges[1].source_range == _tr(2.0, 3.0)
        assert ranges[2].source_range == _tr(5.0, 1.5)

    def test_missing_reference_raises_invalid_input(self) -> None:
        """MissingReference → INVALID_INPUT（L-3: データ不正の意味）。

        MissingReference はタイムラインのデータが不正（参照欠落）であることを示す。
        「非対応構成」（UNSUPPORTED_OPERATION）ではなく「データ不正」（INVALID_INPUT）。
        """
        from clipwright_render.plan import resolve_kept_ranges

        clip = otio.schema.Clip()
        clip.media_reference = otio.schema.MissingReference()
        clip.source_range = _tr(0.0, 5.0)
        tl = _make_timeline_with_clips([clip])
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_kept_ranges(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_zero_clips_raises_invalid_input(self) -> None:
        """Clip 0 件（Gap のみ等）→ INVALID_INPUT（DC-AS-005）。"""
        from clipwright_render.plan import resolve_kept_ranges

        gap = otio.schema.Gap(source_range=_tr(0.0, 5.0))
        tl = _make_timeline_with_clips([gap])
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_kept_ranges(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_source_range_times_are_rational_time(self) -> None:
        """source_range は float 秒ではなく RationalTime/TimeRange で保持する。"""
        from clipwright_render.plan import resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 1.5, 3.7)])
        ranges = resolve_kept_ranges(tl)
        sr = ranges[0].source_range
        assert isinstance(sr, otio.opentime.TimeRange)
        assert isinstance(sr.start_time, otio.opentime.RationalTime)

    def test_audio_track_clips_are_ignored(self) -> None:
        """audio トラックの Clip は対象外（先頭 video トラックのみ）。"""
        from clipwright_render.plan import resolve_kept_ranges

        video_track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
        video_track.append(_make_clip("/src/a.mp4", 0.0, 5.0))
        audio_track = otio.schema.Track(kind=otio.schema.TrackKind.Audio)
        audio_track.append(_make_clip("/src/a.mp4", 0.0, 5.0))
        tl = otio.schema.Timeline()
        tl.tracks.append(video_track)
        tl.tracks.append(audio_track)
        ranges = resolve_kept_ranges(tl)
        # 先頭 video トラックの1件のみ
        assert len(ranges) == 1


# ---------------------------------------------------------------------------
# build_plan — trim 座標境界テスト（DC-AS-004）
# ---------------------------------------------------------------------------


class TestBuildPlanTrimCoordinates:
    """build_plan が生成する filter_complex の trim 座標を検証する。"""

    def test_start_zero_duration_float(self) -> None:
        """start=0 の境界値: trim start=0 が filter_complex に含まれる。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        fc = plan.filter_complex
        assert "trim=start=0" in fc or "trim=start=0." in fc

    def test_fractional_start_and_duration(self) -> None:
        """小数 start/duration の座標変換が正しい（小数6桁・DC-AS-004）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        # start=1.5s, duration=3.25s → end=4.75s
        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 1.5, 3.25)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        # trim start は 1.5、end は 4.75 の数値が含まれる
        assert "1.5" in plan.filter_complex
        assert "4.75" in plan.filter_complex

    def test_setpts_reset_present(self) -> None:
        """setpts=PTS-STARTPTS が filter_complex に含まれる（DC-AS-004）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "setpts=PTS-STARTPTS" in plan.filter_complex

    def test_asetpts_reset_present_when_audio(self) -> None:
        """音声ありの場合 asetpts=PTS-STARTPTS が filter_complex に含まれる。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "asetpts=PTS-STARTPTS" in plan.filter_complex


# ---------------------------------------------------------------------------
# build_plan — filter_complex 構造テスト（ADR-1）
# ---------------------------------------------------------------------------


class TestBuildPlanFilterComplex:
    """filter_complex の構造（trim/concat/ラベル）を検証する。"""

    def test_filter_complex_is_single_string(self) -> None:
        """filter_complex は単一文字列（コマンドインジェクション防止・ADR-1）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert isinstance(plan.filter_complex, str)

    def test_single_clip_uses_concat_n1(self) -> None:
        """Clip 1 件でも concat=n=1 を使う（DC-AS-005）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "concat=n=1" in plan.filter_complex

    def test_two_clips_concat_n2(self) -> None:
        """Clip 2 件: concat=n=2 が filter_complex に含まれる（ADR-1）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/a.mp4", 5.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "concat=n=2" in plan.filter_complex

    def test_video_only_concat_v1_a0(self) -> None:
        """映像あり・音声 0: concat=n=N:v=1:a=0（ADR-7）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "v=1:a=0" in plan.filter_complex

    def test_audio1_concat_v1_a1(self) -> None:
        """映像あり・音声 1: concat=n=N:v=1:a=1（ADR-7）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "v=1:a=1" in plan.filter_complex

    def test_audio_multiple_treated_as_one(self) -> None:
        """音声複数: 第1音声のみ採用（v=1,a=1、ADR-7）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=3, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "v=1:a=1" in plan.filter_complex

    def test_outv_label_present(self) -> None:
        """[outv] ラベルが filter_complex に含まれる（ADR-1）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "[outv]" in plan.filter_complex

    def test_outa_label_present_when_audio(self) -> None:
        """音声ありの場合 [outa] ラベルが filter_complex に含まれる（ADR-1）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "[outa]" in plan.filter_complex


# ---------------------------------------------------------------------------
# build_plan — 音声/映像マトリクス（ADR-7/DC-AS-002）
# ---------------------------------------------------------------------------


class TestBuildPlanAudioVideoMatrix:
    """音声/映像構成マトリクスを検証する（ADR-7/DC-AS-002）。"""

    def test_no_video_raises_unsupported(self) -> None:
        """映像なし → UNSUPPORTED_OPERATION（DC-AS-002）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=False, audio_count=1, bit_rate=None)
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, probe, RenderOptions())
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION

    def test_video_no_audio_map_outv_only(self) -> None:
        """映像あり・音声 0: ffmpeg 引数に -map [outv] のみ（ADR-7）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        args = plan.ffmpeg_args
        # -map [outv] が含まれ -map [outa] は含まれない
        assert "[outv]" in " ".join(str(a) for a in args)
        assert "[outa]" not in " ".join(str(a) for a in args)

    def test_video_audio1_map_outv_and_outa(self) -> None:
        """映像あり・音声 1: -map [outv] -map [outa] が両方含まれる（ADR-7）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        args_str = " ".join(str(a) for a in plan.ffmpeg_args)
        assert "[outv]" in args_str
        assert "[outa]" in args_str

    def test_ffmpeg_args_is_list_of_str(self) -> None:
        """ffmpeg_args は list[str]（M-1: str 統一・コマンドインジェクション防止）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert isinstance(plan.ffmpeg_args, list)
        for item in plan.ffmpeg_args:
            assert isinstance(item, str), f"ffmpeg_args の要素が str でない: {item!r}"


# ---------------------------------------------------------------------------
# build_plan — RenderOptions 写像テスト（ADR-1/DC-AM-004）
# ---------------------------------------------------------------------------


class TestBuildPlanRenderOptions:
    """RenderOptions のフィールドが ffmpeg 引数に正しく写像される。"""

    def test_video_codec_mapped(self) -> None:
        """-c:v が ffmpeg_args に含まれる。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(video_codec="libx264"))
        assert "-c:v" in plan.ffmpeg_args
        idx = plan.ffmpeg_args.index("-c:v")
        assert plan.ffmpeg_args[idx + 1] == "libx264"

    def test_audio_codec_mapped(self) -> None:
        """-c:a が ffmpeg_args に含まれる。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(audio_codec="aac"))
        assert "-c:a" in plan.ffmpeg_args
        idx = plan.ffmpeg_args.index("-c:a")
        assert plan.ffmpeg_args[idx + 1] == "aac"

    def test_scale_filter_in_filter_complex_when_width_height(self) -> None:
        """width/height 指定: scale が filter_complex 内統合され -vf 不使用（L-4）。

        -filter_complex と -vf の同時指定で ffmpeg エラーになるため、
        scale は filter_complex 内の concat 出力後に連結する。
        """
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(width=1280, height=720))
        # scale は filter_complex 内に含まれる
        assert "scale=1280:720" in plan.filter_complex
        # -vf は ffmpeg_args に含まれない（filter_complex と競合するため禁止）
        assert "-vf" not in plan.ffmpeg_args
        # [outvscaled] ラベルが filter_complex に含まれる
        assert "[outvscaled]" in plan.filter_complex
        # -map [outvscaled] が ffmpeg_args に含まれる
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outvscaled]" in args_str

    def test_fps_mapped(self) -> None:
        """-r が ffmpeg_args に含まれる。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(fps=60.0))
        assert "-r" in plan.ffmpeg_args

    def test_crf_mapped(self) -> None:
        """-crf が ffmpeg_args に含まれる。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions(crf=23))
        assert "-crf" in plan.ffmpeg_args


# ---------------------------------------------------------------------------
# build_plan — dry_run 概算テスト（ADR-3/DC-AM-005）
# ---------------------------------------------------------------------------


class TestBuildPlanDryRun:
    """dry_run 概算（区間数・尺・概算サイズ・警告）を検証する。"""

    def test_dry_run_segment_count(self) -> None:
        """plan.segment_count が残区間数と一致する（ADR-3）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/a.mp4", 5.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=8_000_000)
        plan = build_plan(ranges, probe, RenderOptions())
        assert plan.segment_count == 2

    def test_dry_run_total_duration(self) -> None:
        """plan.total_duration_seconds が Σduration と一致する（ADR-3）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/a.mp4", 5.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=8_000_000)
        plan = build_plan(ranges, probe, RenderOptions())
        assert abs(plan.total_duration_seconds - 5.0) < _EPSILON

    def test_estimated_size_bytes_with_bit_rate(self) -> None:
        """bit_rate あり: estimated_size_bytes = bit_rate × 尺 / 8（ADR-3）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 10.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=8_000_000)
        plan = build_plan(ranges, probe, RenderOptions())
        # 8Mbps × 10s / 8 = 10_000_000 bytes
        assert plan.estimated_size_bytes == pytest.approx(10_000_000, rel=_EPSILON)

    def test_estimated_size_none_when_no_bit_rate(self) -> None:
        """bit_rate None: estimated_size_bytes が None（ADR-3）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert plan.estimated_size_bytes is None

    def test_no_bit_rate_adds_warning(self) -> None:
        """bit_rate None のとき warnings に警告が追加される（ADR-3）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert len(plan.warnings) > 0

    @pytest.mark.parametrize(
        "options",
        [
            RenderOptions(video_codec="libx264"),
            RenderOptions(width=1280, height=720),
            RenderOptions(fps=60.0),
            RenderOptions(crf=23),
            RenderOptions(audio_codec="aac"),
        ],
    )
    def test_estimate_is_rough_warning_when_options_specified(
        self, options: RenderOptions
    ) -> None:
        """video_codec/width/height/fps/crf のいずれか非 None → 「概算は目安」
        warning が追加される（DC-AM-005）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=8_000_000)
        plan = build_plan(ranges, probe, options)
        assert len(plan.warnings) > 0

    def test_no_extra_warning_without_codec_options(self) -> None:
        """変換オプション非指定・bit_rate あり: warnings が空（DC-AM-005）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=8_000_000)
        plan = build_plan(ranges, probe, RenderOptions())
        assert plan.warnings == []

    def test_plan_has_command_list(self) -> None:
        """plan.ffmpeg_args は予定コマンドのリスト（ADR-3）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert isinstance(plan.ffmpeg_args, list)
        assert len(plan.ffmpeg_args) > 0


# ===========================================================================
# 複数ソース連結拡張テスト（v2 契約・ADR-C1〜C12）
# ===========================================================================


def _make_probe(
    has_video: bool = True,
    audio_count: int = 1,
    bit_rate: int | None = 8_000_000,
    width: int | None = 1920,
    height: int | None = 1080,
    fps: float | None = 30.0,
) -> ProbeInfo:
    """複数ソーステスト用 ProbeInfo ヘルパー。"""
    return ProbeInfo(
        has_video=has_video,
        audio_count=audio_count,
        bit_rate=bit_rate,
        width=width,
        height=height,
        fps=fps,
    )


def _make_source_probes(**overrides: ProbeInfo) -> dict[str, ProbeInfo]:
    """source_url → ProbeInfo の辞書を返すヘルパー。"""
    return dict(overrides)


# ---------------------------------------------------------------------------
# 観点3: ProbeInfo 新フィールド（width / height / fps）保持テスト
# ---------------------------------------------------------------------------


class TestProbeInfoExtendedFields:
    """ProbeInfo の width/height/fps フィールドが保持される（観点3・ADR-C2）。"""

    def test_probe_info_width_height_fps_stored(self) -> None:
        """ProbeInfo(width=1920, height=1080, fps=30.0) → 各値が保持される。"""
        probe = ProbeInfo(
            has_video=True,
            audio_count=1,
            bit_rate=8_000_000,
            width=1920,
            height=1080,
            fps=30.0,
        )
        assert probe.width == 1920
        assert probe.height == 1080
        assert probe.fps == 30.0

    def test_probe_info_new_fields_default_none(self) -> None:
        """width/height/fps を省略したとき default=None（後方互換・ADR-C2）。"""
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        assert probe.width is None
        assert probe.height is None
        assert probe.fps is None

    def test_probe_info_width_none_height_none_fps_set(self) -> None:
        """fps のみ設定・width/height=None の組み合わせが保持される。"""
        probe = ProbeInfo(
            has_video=True,
            audio_count=1,
            bit_rate=None,
            width=None,
            height=None,
            fps=24.0,
        )
        assert probe.fps == 24.0
        assert probe.width is None
        assert probe.height is None


# ---------------------------------------------------------------------------
# 観点4: unique_sources_in_order 単体テスト（ADR-C9-r2）
# ---------------------------------------------------------------------------


class TestUniqueSourcesInOrder:
    """unique_sources_in_order(ranges) → 出現順・重複排除（観点4・ADR-C9-r2）。"""

    def test_single_source_returns_one_element(self) -> None:
        """単一ソースの複数クリップ → リスト1要素（重複排除）。"""
        from clipwright_render.plan import KeptRange, unique_sources_in_order

        ranges = [
            KeptRange(source="/src/a.mp4", source_range=_tr(0.0, 3.0)),
            KeptRange(source="/src/a.mp4", source_range=_tr(5.0, 2.0)),
        ]
        result = unique_sources_in_order(ranges)
        assert result == ["/src/a.mp4"]

    def test_two_sources_preserves_appearance_order(self) -> None:
        """2ソース → 出現順を維持する（a→b の順）。"""
        from clipwright_render.plan import KeptRange, unique_sources_in_order

        ranges = [
            KeptRange(source="/src/a.mp4", source_range=_tr(0.0, 3.0)),
            KeptRange(source="/src/b.mp4", source_range=_tr(0.0, 2.0)),
        ]
        result = unique_sources_in_order(ranges)
        assert result == ["/src/a.mp4", "/src/b.mp4"]

    def test_interleaved_sources_deduplicates_preserves_order(self) -> None:
        """a→b→a→b の出現 → [a, b]（出現順・重複排除）。"""
        from clipwright_render.plan import KeptRange, unique_sources_in_order

        ranges = [
            KeptRange(source="/src/a.mp4", source_range=_tr(0.0, 2.0)),
            KeptRange(source="/src/b.mp4", source_range=_tr(0.0, 2.0)),
            KeptRange(source="/src/a.mp4", source_range=_tr(5.0, 2.0)),
            KeptRange(source="/src/b.mp4", source_range=_tr(5.0, 2.0)),
        ]
        result = unique_sources_in_order(ranges)
        assert result == ["/src/a.mp4", "/src/b.mp4"]

    def test_three_sources_order_preserved(self) -> None:
        """3ソース a→b→c → [a, b, c] の順。"""
        from clipwright_render.plan import KeptRange, unique_sources_in_order

        ranges = [
            KeptRange(source="/src/a.mp4", source_range=_tr(0.0, 1.0)),
            KeptRange(source="/src/b.mp4", source_range=_tr(0.0, 1.0)),
            KeptRange(source="/src/c.mp4", source_range=_tr(0.0, 1.0)),
        ]
        result = unique_sources_in_order(ranges)
        assert result == ["/src/a.mp4", "/src/b.mp4", "/src/c.mp4"]

    def test_render_plan_input_sources_matches_unique_sources_in_order(self) -> None:
        """RenderPlan.input_sources が unique_sources_in_order と同一順になる（ADR-C9-r2）。"""
        from clipwright_render.plan import build_plan, unique_sources_in_order

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 1.0, 2.0),
            _make_clip("/src/a.mp4", 5.0, 1.0),
        ]
        tl = _make_timeline_with_clips(clips)
        from clipwright_render.plan import resolve_kept_ranges

        ranges = resolve_kept_ranges(tl)
        expected_order = unique_sources_in_order(ranges)
        probe_a = _make_probe()
        probe_b = _make_probe()
        source_probes = {"/src/a.mp4": probe_a, "/src/b.mp4": probe_b}
        plan = build_plan(
            ranges,
            probe_a,
            RenderOptions(),
            source_probes=source_probes,
        )
        assert plan.input_sources == expected_order


# ---------------------------------------------------------------------------
# 観点5: 複数ソース経路 filter_complex 文字列（ADR-C1/C5-r2/C7-r2/C11-r2）
# ---------------------------------------------------------------------------


class TestBuildPlanMultiSourceFilterComplex:
    """複数ソース経路の filter_complex 文字列を検証する（観点5・ADR-C1/C5-r2/C7-r2/C11-r2）。"""

    def _build_multi(
        self,
        clips: list[tuple[str, float, float]],
        source_probes: dict[str, ProbeInfo],
        options: RenderOptions | None = None,
        denoise: dict | None = None,
        loudness: dict | None = None,
    ) -> RenderPlan:
        """複数ソース build_plan のテストヘルパー。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clip_objs = [_make_clip(src, start, dur) for src, start, dur in clips]
        tl = _make_timeline_with_clips(clip_objs)
        ranges = resolve_kept_ranges(tl)
        first_source = clips[0][0]
        probe_info = source_probes[first_source]
        return build_plan(
            ranges,
            probe_info,
            options or RenderOptions(),
            denoise=denoise,
            loudness=loudness,
            source_probes=source_probes,
        )

    def test_5a_input_labels_use_source_index(self) -> None:
        """観点5a: ソース index k に基づく [k:v] が filter_complex に含まれる（ADR-C1）。
        同一ソースの複数クリップは同じ index を共有する。"""
        clips = [
            ("/src/a.mp4", 0.0, 3.0),
            ("/src/b.mp4", 0.0, 2.0),
        ]
        source_probes = {
            "/src/a.mp4": _make_probe(width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(width=1920, height=1080, fps=30.0),
        }
        plan = self._build_multi(clips, source_probes)
        fc = plan.filter_complex
        # a.mp4 は index 0、b.mp4 は index 1
        assert "[0:v]" in fc
        assert "[1:v]" in fc

    def test_5a_same_source_multiple_clips_share_index(self) -> None:
        """観点5a: 同一ソースの複数クリップは同じ index を共有する（ADR-C1）。"""
        clips = [
            ("/src/a.mp4", 0.0, 2.0),
            ("/src/b.mp4", 0.0, 2.0),
            ("/src/a.mp4", 5.0, 1.0),
        ]
        source_probes = {
            "/src/a.mp4": _make_probe(width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(width=1280, height=720, fps=30.0),
        }
        plan = self._build_multi(clips, source_probes)
        fc = plan.filter_complex
        # a.mp4（index=0）が2回、b.mp4（index=1）が1回出現（[2:v]は存在しない）
        assert "[0:v]" in fc
        assert "[1:v]" in fc
        assert "[2:v]" not in fc

    def test_5b_per_clip_normalize_chain_contains_fps_scale_pad_setsar(self) -> None:
        """観点5b: 各クリップ前段に fps=/scale=.../pad=.../setsar=1 が含まれる（ADR-C5-r2）。"""
        clips = [
            ("/src/a.mp4", 0.0, 3.0),
            ("/src/b.mp4", 1.0, 2.0),
        ]
        source_probes = {
            "/src/a.mp4": _make_probe(width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(width=1280, height=720, fps=30.0),
        }
        plan = self._build_multi(clips, source_probes)
        fc = plan.filter_complex
        assert "fps=" in fc
        assert "force_original_aspect_ratio=decrease" in fc
        assert "pad=" in fc
        assert "setsar=1" in fc

    def test_5b_target_width_height_are_even(self) -> None:
        """観点5b: target_w/h は偶数（ADR-C4-r2・yuv420p 偶数制約）。"""
        # 奇数解像度のソース → 偶数に丸められた target が pad= に現れる
        clips = [
            ("/src/a.mp4", 0.0, 3.0),
            ("/src/b.mp4", 0.0, 2.0),
        ]
        source_probes = {
            "/src/a.mp4": _make_probe(width=1921, height=1081, fps=30.0),
            "/src/b.mp4": _make_probe(width=1920, height=1080, fps=30.0),
        }
        plan = self._build_multi(clips, source_probes)
        fc = plan.filter_complex
        # 1921→1920、1081→1080 に偶数丸めされた値が pad 式に出現する
        assert "1920" in fc
        assert "1080" in fc
        # 奇数値が target として出現しない（1921 や 1081 はソース解像度であり pad= 後の値ではない）
        # filter には scale=TW:TH の形で偶数が出る
        import re

        # scale=偶数:偶数 のパターン
        scale_matches = re.findall(r"scale=(\d+):(\d+)", fc)
        for w_str, h_str in scale_matches:
            assert int(w_str) % 2 == 0, f"scale width {w_str} は奇数"
            assert int(h_str) % 2 == 0, f"scale height {h_str} は奇数"

    def test_5b_fps_precision_at_least_5_decimal_places(self) -> None:
        """観点5b: fps= の値は小数5桁以上の精度で書かれる（ADR-C2-r2・NTSC fps 対応）。"""
        clips = [
            ("/src/a.mp4", 0.0, 3.0),
            ("/src/b.mp4", 0.0, 2.0),
        ]
        fps_ntsc = 24000 / 1001  # ≒ 23.97602...
        source_probes = {
            "/src/a.mp4": _make_probe(width=1920, height=1080, fps=fps_ntsc),
            "/src/b.mp4": _make_probe(width=1920, height=1080, fps=fps_ntsc),
        }
        plan = self._build_multi(clips, source_probes)
        fc = plan.filter_complex
        import re

        # fps=X.XXXXX の形式で5桁以上の小数が出現する
        fps_matches = re.findall(r"fps=(\d+\.\d+)", fc)
        assert len(fps_matches) > 0, "fps= が filter_complex に含まれない"
        for fps_str in fps_matches:
            decimal_part = fps_str.split(".")[1]
            assert len(decimal_part) >= 5, (
                f"fps={fps_str} の小数桁数が5未満（NTSC 精度不足）"
            )

    def test_5c_aformat_stereo_48000_required_in_audio_chain(self) -> None:
        """観点5c: 音声あり音声ラベルに aformat=sample_rates=48000:channel_layouts=stereo が
        必須挿入される（ADR-C7-r2・DC-AS-002/AM-007）。"""
        clips = [
            ("/src/a.mp4", 0.0, 3.0),
            ("/src/b.mp4", 0.0, 2.0),
        ]
        source_probes = {
            "/src/a.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
        }
        plan = self._build_multi(clips, source_probes)
        fc = plan.filter_complex
        assert "aformat=sample_rates=48000:channel_layouts=stereo" in fc

    def test_5d_concat_label_outv_outa_with_audio(self) -> None:
        """観点5d: concat=n=N:v=1:a=1 と [outv][outa] が含まれる（ADR-C11-r2）。"""
        clips = [
            ("/src/a.mp4", 0.0, 3.0),
            ("/src/b.mp4", 0.0, 2.0),
        ]
        source_probes = {
            "/src/a.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
        }
        plan = self._build_multi(clips, source_probes)
        fc = plan.filter_complex
        assert "concat=n=2:v=1:a=1" in fc
        assert "[outv]" in fc
        assert "[outa]" in fc

    def test_5d_concat_n_equals_clip_count(self) -> None:
        """観点5d: concat=n= がクリップ数と一致する（ADR-C11-r2）。"""
        clips = [
            ("/src/a.mp4", 0.0, 2.0),
            ("/src/b.mp4", 0.0, 2.0),
            ("/src/a.mp4", 5.0, 1.0),
        ]
        source_probes = {
            "/src/a.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
        }
        plan = self._build_multi(clips, source_probes)
        fc = plan.filter_complex
        # クリップ数3
        assert "concat=n=3" in fc


# ---------------------------------------------------------------------------
# 観点6: 出力規格決定（ADR-C4-r2）
# ---------------------------------------------------------------------------


class TestBuildPlanOutputSpec:
    """出力規格決定ロジックを検証する（観点6・ADR-C4-r2）。"""

    def _build_2source(
        self,
        options: RenderOptions,
        probe_a: ProbeInfo | None = None,
        probe_b: ProbeInfo | None = None,
    ) -> RenderPlan:
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        pa = probe_a or _make_probe(width=1920, height=1080, fps=30.0)
        pb = probe_b or _make_probe(width=1280, height=720, fps=30.0)
        clips = [_make_clip("/src/a.mp4", 0.0, 3.0), _make_clip("/src/b.mp4", 0.0, 2.0)]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {"/src/a.mp4": pa, "/src/b.mp4": pb}
        return build_plan(ranges, pa, options, source_probes=source_probes)

    def test_6_both_width_height_specified_uses_options(self) -> None:
        """width/height 両方指定 → その値（偶数）が scale に使われる（ADR-C4-r2）。"""
        plan = self._build_2source(RenderOptions(width=1280, height=720))
        assert "scale=1280:720" in plan.filter_complex

    def test_6_width_only_specified_raises_validation_error(self) -> None:
        """片方のみ（width のみ）指定 → RenderOptions 構築時に pydantic ValidationError。

        width/height はペアで指定するか両方 None でなければならない（厳格拒否）。
        build_plan には到達しない契約なので、RenderOptions 構築レベルで検証する。
        """
        with pytest.raises(ValidationError):
            RenderOptions(width=640)

    def test_6_height_only_specified_raises_validation_error(self) -> None:
        """片方のみ（height のみ）指定 → RenderOptions 構築時に pydantic ValidationError。

        width/height はペアで指定するか両方 None でなければならない（厳格拒否）。
        build_plan には到達しない契約なので、RenderOptions 構築レベルで検証する。
        """
        with pytest.raises(ValidationError):
            RenderOptions(height=480)

    def test_6_no_options_uses_first_source_spec(self) -> None:
        """未指定 → 先頭クリップのソース規格（width/height/fps）を使う（ADR-C4-r2）。"""
        plan = self._build_2source(RenderOptions())
        fc = plan.filter_complex
        # 先頭ソース a.mp4 の 1920x1080 が target になる
        assert "scale=1920:1080" in fc

    def test_6_fps_option_alone_adopted(self) -> None:
        """options.fps のみ指定 → その fps が filter_complex の fps= に使われる（ADR-C4-r2）。"""
        plan = self._build_2source(RenderOptions(fps=60.0))
        fc = plan.filter_complex
        assert "fps=60" in fc or "fps=60." in fc

    def test_6_first_source_width_none_raises_invalid_input(self) -> None:
        """先頭ソースの width=None → INVALID_INPUT（規格決定不能・ADR-C4-r2）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        pa = _make_probe(width=None, height=1080, fps=30.0)
        pb = _make_probe(width=1920, height=1080, fps=30.0)
        clips = [_make_clip("/src/a.mp4", 0.0, 3.0), _make_clip("/src/b.mp4", 0.0, 2.0)]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {"/src/a.mp4": pa, "/src/b.mp4": pb}
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, pa, RenderOptions(), source_probes=source_probes)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_6_first_source_fps_none_raises_invalid_input(self) -> None:
        """先頭ソースの fps=None → INVALID_INPUT（規格決定不能・ADR-C2-r2）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        pa = _make_probe(width=1920, height=1080, fps=None)
        pb = _make_probe(width=1920, height=1080, fps=30.0)
        clips = [_make_clip("/src/a.mp4", 0.0, 3.0), _make_clip("/src/b.mp4", 0.0, 2.0)]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {"/src/a.mp4": pa, "/src/b.mp4": pb}
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(ranges, pa, RenderOptions(), source_probes=source_probes)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT


# ---------------------------------------------------------------------------
# 観点7: 音声混在 anullsrc 補完（ADR-C7-r2）
# ---------------------------------------------------------------------------


class TestBuildPlanAudioMixedAnullsrc:
    """音声なしソースを anullsrc で補完し concat a=1 が成立する（観点7・ADR-C7-r2）。"""

    def test_7_audio_absent_source_generates_anullsrc(self) -> None:
        """音声なしソースのクリップ → filter_complex に 'anullsrc' が含まれる（ADR-C7-r2）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/with_audio.mp4", 0.0, 3.0),
            _make_clip("/src/no_audio.mp4", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/with_audio.mp4": _make_probe(
                audio_count=1, width=1920, height=1080, fps=30.0
            ),
            "/src/no_audio.mp4": _make_probe(
                audio_count=0, width=1920, height=1080, fps=30.0
            ),
        }
        plan = build_plan(
            ranges,
            source_probes["/src/with_audio.mp4"],
            RenderOptions(),
            source_probes=source_probes,
        )
        fc = plan.filter_complex
        assert "anullsrc" in fc

    def test_7_anullsrc_clip_duration_matches_video_duration(self) -> None:
        """anullsrc の atrim=0:DUR は映像と同じ秒尺（ADR-C7-r2・DC-AM-005）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        # 音声なしクリップの duration=2.5 秒
        clips = [
            _make_clip("/src/with_audio.mp4", 0.0, 3.0),
            _make_clip("/src/no_audio.mp4", 0.0, 2.5),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/with_audio.mp4": _make_probe(
                audio_count=1, width=1920, height=1080, fps=30.0
            ),
            "/src/no_audio.mp4": _make_probe(
                audio_count=0, width=1920, height=1080, fps=30.0
            ),
        }
        plan = build_plan(
            ranges,
            source_probes["/src/with_audio.mp4"],
            RenderOptions(),
            source_probes=source_probes,
        )
        fc = plan.filter_complex
        # anullsrc が "atrim=0:2.5" か "atrim=0:2.50000" 等の形で含まれる
        # re.search で境界付き一致（atrim=0:2.5001 等の丸め誤差を誤検知しない）
        import re

        assert "anullsrc" in fc
        assert re.search(r"atrim=0:2\.5(?:0*)?(?:[^0-9]|$)", fc), (
            f"atrim=0:2.5... が filter_complex に含まれない: {fc}"
        )

    def test_7_audio_mixed_concat_a1(self) -> None:
        """音声混在でも concat a=1 が成立する（ADR-C7-r2）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(audio_count=0, width=1920, height=1080, fps=30.0),
        }
        plan = build_plan(
            ranges,
            source_probes["/src/a.mp4"],
            RenderOptions(),
            source_probes=source_probes,
        )
        fc = plan.filter_complex
        assert "concat=n=2:v=1:a=1" in fc


# ---------------------------------------------------------------------------
# 観点8: 全ソース音声なし（DC-GP-002）
# ---------------------------------------------------------------------------


class TestBuildPlanAllAudiolessMultiSource:
    """全ソース音声なし → a=0・映像のみ、denoise/loudness はスキップ（観点8・DC-GP-002）。"""

    def test_8_all_audioless_concat_a0(self) -> None:
        """全ソース audio_count=0 → concat a=0（映像のみ）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a.mp4": _make_probe(audio_count=0, width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(audio_count=0, width=1920, height=1080, fps=30.0),
        }
        plan = build_plan(
            ranges,
            source_probes["/src/a.mp4"],
            RenderOptions(),
            source_probes=source_probes,
        )
        fc = plan.filter_complex
        assert "a=0" in fc
        assert "[outa]" not in fc

    def test_8_all_audioless_with_denoise_skips_filter_adds_warning(self) -> None:
        """全ソース音声なし ＋ denoise 指示 → フィルタ非注入・警告追加（ADR-C11-r2）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a.mp4": _make_probe(audio_count=0, width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(audio_count=0, width=1920, height=1080, fps=30.0),
        }
        denoise = {
            "tool": "clipwright-noise",
            "version": "0.1.0",
            "kind": "denoise",
            "backend": "afftdn",
            "params": {"nr": 12.0, "nf": -40.0, "nt": "w"},
        }
        plan = build_plan(
            ranges,
            source_probes["/src/a.mp4"],
            RenderOptions(),
            denoise=denoise,
            source_probes=source_probes,
        )
        # afftdn は注入されない
        assert "afftdn" not in plan.filter_complex
        # 警告が追加される
        assert len(plan.warnings) > 0

    def test_8_all_audioless_with_loudness_skips_filter_adds_warning(self) -> None:
        """全ソース音声なし ＋ loudness 指示 → フィルタ非注入・警告追加（ADR-C11-r2）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a.mp4": _make_probe(audio_count=0, width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(audio_count=0, width=1920, height=1080, fps=30.0),
        }
        loudness = {
            "tool": "clipwright-loudness",
            "version": "0.1.0",
            "kind": "loudness",
            "mode": "peak",
            "scope": "track",
            "target": {"peak_db": -1.0},
            "measured": {"max_volume_db": -3.0},
        }
        plan = build_plan(
            ranges,
            source_probes["/src/a.mp4"],
            RenderOptions(),
            loudness=loudness,
            source_probes=source_probes,
        )
        # loudnorm/volume は注入されない
        assert "loudnorm" not in plan.filter_complex
        assert "volume=" not in plan.filter_complex
        # 警告が追加される
        assert len(plan.warnings) > 0


# ---------------------------------------------------------------------------
# 観点9: has_video 混在 → UNSUPPORTED_OPERATION（DC-GP-004・ADR-C12）
# ---------------------------------------------------------------------------


class TestBuildPlanHasVideoMixed:
    """複数ソースのいずれかが has_video=False → UNSUPPORTED_OPERATION（観点9・ADR-C12）。"""

    def test_9_second_source_no_video_raises_unsupported(self) -> None:
        """2番目ソースが has_video=False → UNSUPPORTED_OPERATION（ADR-C12）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b_audio_only.mp3", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a.mp4": _make_probe(
                has_video=True, width=1920, height=1080, fps=30.0
            ),
            "/src/b_audio_only.mp3": _make_probe(
                has_video=False, audio_count=1, width=None, height=None, fps=None
            ),
        }
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(
                ranges,
                source_probes["/src/a.mp4"],
                RenderOptions(),
                source_probes=source_probes,
            )
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION
        # hint に basename が含まれる
        assert "b_audio_only" in exc_info.value.hint
        # message / hint に絶対パスが露出しない（SR L-2: CWE-209 情報漏洩防止）
        assert "/src/" not in exc_info.value.message
        assert "/src/" not in exc_info.value.hint

    def test_9_first_source_no_video_raises_unsupported(self) -> None:
        """先頭ソースが has_video=False でも UNSUPPORTED_OPERATION（ADR-C12）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a_audio_only.mp3", 0.0, 2.0),
            _make_clip("/src/b.mp4", 0.0, 3.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a_audio_only.mp3": _make_probe(
                has_video=False, audio_count=1, width=None, height=None, fps=None
            ),
            "/src/b.mp4": _make_probe(
                has_video=True, width=1920, height=1080, fps=30.0
            ),
        }
        with pytest.raises(ClipwrightError) as exc_info:
            build_plan(
                ranges,
                source_probes["/src/a_audio_only.mp3"],
                RenderOptions(),
                source_probes=source_probes,
            )
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION


# ---------------------------------------------------------------------------
# 観点10: 後方互換（単一ソース経路・ADR-C3）
# ---------------------------------------------------------------------------


class TestBuildPlanSingleSourceBackwardCompat:
    """単一ソース経路が複数ソース拡張後も filter_complex 不変（観点10・ADR-C3）。"""

    def test_10_single_source_filter_complex_unchanged(self) -> None:
        """source_probes 未指定（単一ソース）→ 既存単一ソース filter_complex と完全一致（ADR-C3）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/a.mp4", 5.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=8_000_000)

        # source_probes 未指定
        plan_no_sp = build_plan(ranges, probe, RenderOptions())
        # source_probes=None 明示
        plan_sp_none = build_plan(ranges, probe, RenderOptions(), source_probes=None)
        # source_probes にユニークソース1個だけ
        plan_sp_single = build_plan(
            ranges, probe, RenderOptions(), source_probes={"/src/a.mp4": probe}
        )

        # 3パターンで filter_complex が一致
        assert plan_no_sp.filter_complex == plan_sp_none.filter_complex
        assert plan_no_sp.filter_complex == plan_sp_single.filter_complex

    def test_10_single_source_no_aformat_in_filter_complex(self) -> None:
        """単一ソース経路では aformat が filter_complex に含まれない（ADR-C3 後方互換）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=8_000_000)
        plan = build_plan(ranges, probe, RenderOptions())
        # 単一ソース経路では音声規格統一フィルタは不要
        assert "aformat" not in plan.filter_complex

    def test_10_single_source_no_fps_scale_pad_per_clip(self) -> None:
        """単一ソース経路では per-clip fps/scale/pad が含まれない（ADR-C3 後方互換）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 5.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=8_000_000)
        plan = build_plan(ranges, probe, RenderOptions())
        fc = plan.filter_complex
        # 単一ソース経路には fps= / pad= / setsar= が含まれない（per-clip 規格統一なし）
        assert "fps=" not in fc
        assert "pad=" not in fc
        assert "setsar" not in fc

    def test_10_single_source_input_sources_has_one_element(self) -> None:
        """単一ソース経路で RenderPlan.input_sources が1要素（ADR-C9-r2）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/a.mp4", 5.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=8_000_000)
        plan = build_plan(ranges, probe, RenderOptions())
        assert plan.input_sources == ["/src/a.mp4"]


# ---------------------------------------------------------------------------
# 観点11: 複数ソース経路 _append_audio_pipe 適用テスト（DC-GP-005 / plan.py:739,741,961）
# ---------------------------------------------------------------------------

# 有効な afftdn denoise 指示（複数ソーステスト用）
_VALID_AFFTDN_DIRECTIVE: dict = {
    "tool": "clipwright-noise",
    "version": "0.1.0",
    "kind": "denoise",
    "backend": "afftdn",
    "params": {"nr": 12.0, "nf": -50.0, "nt": "w"},
}

# 有効な peak loudness 指示（複数ソーステスト用）
_VALID_PEAK_DIRECTIVE: dict = {
    "tool": "clipwright-loudness",
    "version": "0.1.0",
    "kind": "loudness",
    "mode": "peak",
    "scope": "track",
    "target": {"peak_db": -1.0},
    "measured": {"max_volume_db": -7.68},
}

# 有効な loudnorm 指示（複数ソーステスト用）
_VALID_LOUDNORM_DIRECTIVE: dict = {
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


class TestBuildPlanMultiSourceAudioPipe:
    """複数ソース経路の _append_audio_pipe 適用を検証する（観点11・DC-GP-005）。

    plan.py:739/741: 複数ソース ＋ 音声あり ＋ denoise/loudness で
    audio map 終端ラベルが [outa_dn] / [outa_ln] になることを確認する。
    plan.py:961: 複数ソース ＋ loudness で測定値ずれ警告が追加されることを確認する。
    """

    def _build_multi_with_audio(
        self,
        denoise: dict | None = None,
        loudness: dict | None = None,
    ) -> object:
        """複数ソース（a.mp4 / b.mp4）＋ 音声あり で build_plan を呼ぶヘルパー。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 2.0),
        ]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
        }
        probe_a = source_probes["/src/a.mp4"]
        return build_plan(
            ranges,
            probe_a,
            RenderOptions(),
            denoise=denoise,
            loudness=loudness,
            source_probes=source_probes,
        )

    def test_11a_multi_source_with_audio_and_denoise_injects_afftdn(self) -> None:
        """複数ソース ＋ 音声あり ＋ denoise → afftdn が filter_complex に注入される（plan.py:739-741）。"""
        plan = self._build_multi_with_audio(denoise=_VALID_AFFTDN_DIRECTIVE)
        assert "afftdn" in plan.filter_complex

    def test_11b_multi_source_with_audio_and_denoise_audio_map_label_outa_dn(
        self,
    ) -> None:
        """複数ソース ＋ 音声あり ＋ denoise → audio map 終端ラベルが [outa_dn]（plan.py:741）。"""
        plan = self._build_multi_with_audio(denoise=_VALID_AFFTDN_DIRECTIVE)
        # -map [outa_dn] が ffmpeg_args に含まれる（list[str] 直接比較）
        assert "[outa_dn]" in plan.ffmpeg_args
        # -map [outa] は含まれない（[outa_dn] に差し替えられている）
        assert "[outa]" not in plan.ffmpeg_args

    def test_11c_multi_source_with_audio_and_loudness_injects_loudnorm(self) -> None:
        """複数ソース ＋ 音声あり ＋ loudnorm → loudnorm が filter_complex に注入される（plan.py:739）。"""
        plan = self._build_multi_with_audio(loudness=_VALID_LOUDNORM_DIRECTIVE)
        assert "loudnorm" in plan.filter_complex

    def test_11d_multi_source_with_audio_and_loudness_audio_map_label_outa_ln(
        self,
    ) -> None:
        """複数ソース ＋ 音声あり ＋ loudness → audio map 終端ラベルが [outa_ln]（plan.py:739）。"""
        plan = self._build_multi_with_audio(loudness=_VALID_PEAK_DIRECTIVE)
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa_ln]" in args_str

    def test_11e_multi_source_with_audio_and_peak_loudness_adds_measurement_warning(
        self,
    ) -> None:
        """複数ソース（ユニークソース ≥ 2） ＋ loudness → 測定値ずれ警告が warnings に含まれる（plan.py:961）。"""
        plan = self._build_multi_with_audio(loudness=_VALID_PEAK_DIRECTIVE)
        # ADR-C11-r2 の測定値ずれ警告テキストを確認する
        warning_text = " ".join(plan.warnings)
        assert (
            "複数ソース合体" in warning_text
            or "measured" in warning_text
            or "ずれ" in warning_text
        )

    def test_11f_multi_source_with_audio_and_loudnorm_adds_measurement_warning(
        self,
    ) -> None:
        """複数ソース ＋ loudnorm でも測定値ずれ警告が含まれる（plan.py:961）。"""
        plan = self._build_multi_with_audio(loudness=_VALID_LOUDNORM_DIRECTIVE)
        warning_text = " ".join(plan.warnings)
        assert "複数ソース合体" in warning_text or "ずれ" in warning_text
