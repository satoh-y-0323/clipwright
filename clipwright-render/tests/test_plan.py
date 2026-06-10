"""test_plan.py — plan.py（純ロジック）の Red テスト。

対象関数:
  - resolve_kept_ranges(timeline) -> list[KeptRange]
  - build_plan(ranges, probe_info, options) -> RenderPlan

plan.py は ffmpeg/ffprobe を一切実行しない純ロジック。
probe 結果（bit_rate/has_video/audio_count）は引数として渡す（DC-AM-007）。
OTIO Timeline はテスト内で直接構築する。
"""

from __future__ import annotations

from typing import Any

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode

from clipwright_render.plan import ProbeInfo
from clipwright_render.schemas import RenderOptions

# ---------------------------------------------------------------------------
# ヘルパー: テスト内 Timeline 構築
# ---------------------------------------------------------------------------

FPS = 30.0


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

    def test_multiple_sources_raises_unsupported(self) -> None:
        """target_url 不一致（複数ソース）→ UNSUPPORTED_OPERATION（DC-AS-005）。"""
        from clipwright_render.plan import resolve_kept_ranges

        clips = [
            _make_clip("/src/a.mp4", 0.0, 3.0),
            _make_clip("/src/b.mp4", 0.0, 3.0),
        ]
        tl = _make_timeline_with_clips(clips)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_kept_ranges(tl)
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION

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
        assert abs(plan.total_duration_seconds - 5.0) < 1e-6

    def test_estimated_size_bytes_with_bit_rate(self) -> None:
        """bit_rate あり: estimated_size_bytes = bit_rate × 尺 / 8（ADR-3）。"""
        from clipwright_render.plan import build_plan, resolve_kept_ranges

        tl = _make_timeline_with_clips([_make_clip("/src/a.mp4", 0.0, 10.0)])
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=8_000_000)
        plan = build_plan(ranges, probe, RenderOptions())
        # 8Mbps × 10s / 8 = 10_000_000 bytes
        assert plan.estimated_size_bytes == pytest.approx(10_000_000, rel=1e-6)

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
