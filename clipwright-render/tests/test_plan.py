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

from clipwright_render.plan import BgmClip, ProbeInfo
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


# ===========================================================================
# BGM ミックス拡張テスト（ADR-B4-r2/B5-r2/B5-r3/B6-r2/B9-r3）
# ===========================================================================
# 実 ffmpeg 確認済み構文（2026-06-11）:
# - -stream_loop -1 + atrim=0:{main_dur} → 5秒出力 OK
# - [N:a]aformat=48000:stereo,atrim=0:{d},asetpts=PTS-STARTPTS,volume={v}dB[bgm] → OK
# - [main_fmt][bgm]amix=inputs=2:normalize=0,alimiter=limit=1.0[outa_bgm] → OK
# - sidechaincompress: BGM=第1入力・本編=第2(サイドチェーン) → OK
# - afade=t=in:st=0:d={d}, afade=t=out:st={st}:d={d} → OK
# - 本編無音 + BGM単独系統（amixなし） → 出力に音声1ストリーム OK
# ===========================================================================

# ---------------------------------------------------------------------------
# BGM テスト用ヘルパー定数
# ---------------------------------------------------------------------------

_VALID_BGM_DIRECTIVE: dict = {
    "tool": "clipwright-bgm",
    "version": "0.1.0",
    "kind": "bgm",
    "volume_db": -6.0,
    "fade_in_sec": 0.0,
    "fade_out_sec": 0.0,
    "ducking": {"enabled": False, "threshold": 0.05, "ratio": 4.0},
}

_VALID_BGM_DIRECTIVE_WITH_FADE: dict = {
    "tool": "clipwright-bgm",
    "version": "0.1.0",
    "kind": "bgm",
    "volume_db": -6.0,
    "fade_in_sec": 1.0,
    "fade_out_sec": 1.5,
    "ducking": {"enabled": False, "threshold": 0.05, "ratio": 4.0},
}

_VALID_BGM_DIRECTIVE_DUCKING: dict = {
    "tool": "clipwright-bgm",
    "version": "0.1.0",
    "kind": "bgm",
    "volume_db": -6.0,
    "fade_in_sec": 0.0,
    "fade_out_sec": 0.0,
    "ducking": {"enabled": True, "threshold": 0.05, "ratio": 4.0},
}


def _make_bgm_clip(
    bgm_source: str = "/proj/bgm.mp3",
    directive: dict | None = None,
    timeline_duration_sec: float = 5.0,
) -> BgmClip:
    """BGM テスト用 BgmClip を構築するヘルパー。"""
    from pydantic import TypeAdapter

    from clipwright_render.plan import (  # type: ignore[attr-defined]
        BgmClip,
        BgmDirective,
    )

    d = directive or _VALID_BGM_DIRECTIVE
    bgm_dir = TypeAdapter(BgmDirective).validate_python(d)
    source_range = _tr(0.0, timeline_duration_sec)
    return BgmClip(
        source=bgm_source,
        source_range=source_range,
        directive=bgm_dir,
    )


def _make_single_source_timeline_with_audio(
    source: str = "/src/a.mp4",
    duration: float = 5.0,
) -> otio.schema.Timeline:
    """単一ソース・音声あり Timeline を返すヘルパー。"""
    video_track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    video_track.append(_make_clip(source, 0.0, duration))
    tl = otio.schema.Timeline()
    tl.tracks.append(video_track)
    return tl


def _make_bgm_otio_timeline(
    bgm_source: str = "/proj/bgm.mp3",
    directive: dict | None = None,
    main_source: str = "/src/a.mp4",
    main_duration: float = 5.0,
) -> otio.schema.Timeline:
    """BGM クリップを A2 トラックに含む Timeline を返すヘルパー（resolve_bgm テスト用）。"""
    d = directive or _VALID_BGM_DIRECTIVE
    # V1 video トラック
    video_track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    video_track.append(_make_clip(main_source, 0.0, main_duration))
    # A1 本編音声トラック（kind!="bgm" クリップ）
    audio_track_a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    clip_a1 = _make_clip(main_source, 0.0, main_duration)
    audio_track_a1.append(clip_a1)
    # A2 BGM トラック（kind=="bgm" クリップ）
    audio_track_a2 = otio.schema.Track(name="A2", kind=otio.schema.TrackKind.Audio)
    clip_bgm = otio.schema.Clip()
    clip_bgm.media_reference = otio.schema.ExternalReference(target_url=bgm_source)
    clip_bgm.source_range = _tr(0.0, main_duration)
    clip_bgm.metadata["clipwright"] = d
    audio_track_a2.append(clip_bgm)
    tl = otio.schema.Timeline()
    tl.tracks.append(video_track)
    tl.tracks.append(audio_track_a1)
    tl.tracks.append(audio_track_a2)
    return tl


# ---------------------------------------------------------------------------
# 観点1: BgmDirective reader-strict バリデーション（ADR-B9-r2/B9-r3）
# ---------------------------------------------------------------------------


class TestBgmDirectiveValidation:
    """BgmDirective の reader-strict バリデーションを検証する（観点1・ADR-B9-r2）。"""

    def test_1_valid_directive_accepts_normal_values(self) -> None:
        """正常値の BgmDirective が構築できる（観点1）。"""
        from clipwright_render.plan import BgmDirective  # type: ignore[attr-defined]

        d = BgmDirective(**_VALID_BGM_DIRECTIVE)
        assert d.volume_db == -6.0
        assert d.fade_in_sec == 0.0
        assert d.fade_out_sec == 0.0
        assert d.kind == "bgm"

    def test_1_invalid_kind_raises(self) -> None:
        """kind が "bgm" 以外 → ValidationError（観点1）。"""
        from pydantic import ValidationError

        from clipwright_render.plan import BgmDirective  # type: ignore[attr-defined]

        bad = dict(_VALID_BGM_DIRECTIVE, kind="noise")
        with pytest.raises(ValidationError):
            BgmDirective(**bad)

    def test_1_negative_fade_in_raises(self) -> None:
        """fade_in_sec が負 → ValidationError（ge=0 制約・ADR-B9-r3）。"""
        from pydantic import ValidationError

        from clipwright_render.plan import BgmDirective  # type: ignore[attr-defined]

        bad = dict(_VALID_BGM_DIRECTIVE, fade_in_sec=-0.1)
        with pytest.raises(ValidationError):
            BgmDirective(**bad)

    def test_1_negative_fade_out_raises(self) -> None:
        """fade_out_sec が負 → ValidationError（ge=0 制約・ADR-B9-r3）。"""
        from pydantic import ValidationError

        from clipwright_render.plan import BgmDirective  # type: ignore[attr-defined]

        bad = dict(_VALID_BGM_DIRECTIVE, fade_out_sec=-0.1)
        with pytest.raises(ValidationError):
            BgmDirective(**bad)

    def test_1_tool_over_64_chars_raises(self) -> None:
        """tool が max_length=64 超 → ValidationError（ADR-B9-r2）。"""
        from pydantic import ValidationError

        from clipwright_render.plan import BgmDirective  # type: ignore[attr-defined]

        bad = dict(_VALID_BGM_DIRECTIVE, tool="x" * 65)
        with pytest.raises(ValidationError):
            BgmDirective(**bad)

    def test_1_version_over_64_chars_raises(self) -> None:
        """version が max_length=64 超 → ValidationError（ADR-B9-r2）。"""
        from pydantic import ValidationError

        from clipwright_render.plan import BgmDirective  # type: ignore[attr-defined]

        bad = dict(_VALID_BGM_DIRECTIVE, version="v" * 65)
        with pytest.raises(ValidationError):
            BgmDirective(**bad)

    def test_1_unknown_key_raises_forbidden_extra(self) -> None:
        """未知キー → reader-strict（forbid extra）で ValidationError（観点1）。"""
        from pydantic import ValidationError

        from clipwright_render.plan import BgmDirective  # type: ignore[attr-defined]

        bad = dict(_VALID_BGM_DIRECTIVE, unknown_field="evil")
        with pytest.raises(ValidationError):
            BgmDirective(**bad)

    def test_1_inf_volume_db_raises(self) -> None:
        """volume_db が inf → allow_inf_nan=False で ValidationError（観点1）。"""
        import math

        from pydantic import ValidationError

        from clipwright_render.plan import BgmDirective  # type: ignore[attr-defined]

        bad = dict(_VALID_BGM_DIRECTIVE, volume_db=math.inf)
        with pytest.raises(ValidationError):
            BgmDirective(**bad)

    def test_1_nan_volume_db_raises(self) -> None:
        """volume_db が nan → allow_inf_nan=False で ValidationError（観点1）。"""
        import math

        from pydantic import ValidationError

        from clipwright_render.plan import BgmDirective  # type: ignore[attr-defined]

        bad = dict(_VALID_BGM_DIRECTIVE, volume_db=math.nan)
        with pytest.raises(ValidationError):
            BgmDirective(**bad)


# ---------------------------------------------------------------------------
# 観点2: resolve_bgm（ADR-B4-r2）
# ---------------------------------------------------------------------------


class TestResolveBgm:
    """resolve_bgm の挙動を検証する（観点2・ADR-B4-r2）。"""

    def test_2_single_bgm_clip_returns_bgm_clip(self) -> None:
        """kind=="bgm" クリップ 1件 → BgmClip を返す（ADR-B4-r2）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            BgmClip,
            resolve_bgm,
        )

        tl = _make_bgm_otio_timeline()
        result = resolve_bgm(tl)
        assert isinstance(result, BgmClip)
        assert result.source == "/proj/bgm.mp3"

    def test_2_no_bgm_clip_returns_none(self) -> None:
        """kind=="bgm" クリップ 0件 → None（後方互換・ADR-B4-r2）。"""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        # BGMトラックなし: V1 + A1 のみ
        tl = _make_single_source_timeline_with_audio()
        result = resolve_bgm(tl)
        assert result is None

    def test_2_two_bgm_clips_raises_unsupported(self) -> None:
        """kind=="bgm" クリップ 2件以上 → UNSUPPORTED_OPERATION（ADR-B4-r2）。"""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        # A2 と A3 に BGM クリップを置く
        tl = _make_bgm_otio_timeline()  # A2 に 1件追加済み
        # A3 にもう1件追加
        audio_track_a3 = otio.schema.Track(name="A3", kind=otio.schema.TrackKind.Audio)
        clip_bgm2 = otio.schema.Clip()
        clip_bgm2.media_reference = otio.schema.ExternalReference(
            target_url="/proj/bgm2.mp3"
        )
        clip_bgm2.source_range = _tr(0.0, 5.0)
        clip_bgm2.metadata["clipwright"] = _VALID_BGM_DIRECTIVE
        audio_track_a3.append(clip_bgm2)
        tl.tracks.append(audio_track_a3)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION

    def test_2_a1_with_main_audio_and_one_bgm_does_not_raise_unsupported(self) -> None:
        """A1 本編音声（kind!="bgm"）+ A2 BGM 1件 → UNSUPPORTED にならない（ADR-B4-r2 DC-AS-002）。

        Audio トラックが2本あっても BGM クリップが1件なら正常。
        複数 Audio トラック数では判定しない（A1 本編常在の誤検出回避）。
        """
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            BgmClip,
            resolve_bgm,
        )

        tl = _make_bgm_otio_timeline()  # V1 + A1(本編) + A2(BGM)
        # Audio トラックは 2本だが BGM クリップは 1件
        result = resolve_bgm(tl)
        assert isinstance(result, BgmClip)

    def test_2_bgm_clip_source_preserved(self) -> None:
        """resolve_bgm が返す BgmClip の source が正しいパス（観点2）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            BgmClip,
            resolve_bgm,
        )

        tl = _make_bgm_otio_timeline(bgm_source="/music/track.mp3")
        result = resolve_bgm(tl)
        assert isinstance(result, BgmClip)
        assert result.source == "/music/track.mp3"

    def test_2_bgm_clip_directive_volume_preserved(self) -> None:
        """resolve_bgm が返す BgmClip の directive.volume_db が正しい値（観点2）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            BgmClip,
            resolve_bgm,
        )

        d = dict(_VALID_BGM_DIRECTIVE, volume_db=-12.0)
        tl = _make_bgm_otio_timeline(directive=d)
        result = resolve_bgm(tl)
        assert isinstance(result, BgmClip)
        assert result.directive.volume_db == -12.0

    def test_2_bgm_only_in_a2_but_a1_has_normal_clips(self) -> None:
        """A1 に kind 未設定の通常クリップ + A2 に kind=="bgm" → 1件正常検出（ADR-B4-r2）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            BgmClip,
            resolve_bgm,
        )

        # A1 は metadata なし通常クリップ
        tl = _make_bgm_otio_timeline()
        result = resolve_bgm(tl)
        assert isinstance(result, BgmClip)


# ---------------------------------------------------------------------------
# 観点3・4: build_plan(bgm=BgmClip) → audio_map_label / RenderPlan.bgm_source / BGM index
# ---------------------------------------------------------------------------


class TestBuildPlanBgmOutputLabels:
    """build_plan(bgm=...) が正しい audio_map_label と RenderPlan フィールドを返す（観点3・4）。"""

    def _build_with_bgm(
        self,
        bgm_source: str = "/proj/bgm.mp3",
        directive: dict | None = None,
        audio_count: int = 1,
    ) -> RenderPlan:  # type: ignore[name-defined]
        """単一ソース + BGM の build_plan ヘルパー。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=audio_count, bit_rate=None)
        bgm_clip = _make_bgm_clip(bgm_source=bgm_source, directive=directive)
        return build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]

    def test_3_audio_map_label_is_outa_bgm(self) -> None:
        """bgm=BgmClip → audio_map_label == [outa_bgm]（観点3）。"""
        plan = self._build_with_bgm()
        args_str = " ".join(plan.ffmpeg_args)
        assert "[outa_bgm]" in args_str

    def test_3_bgm_source_set_in_render_plan(self) -> None:
        """build_plan(bgm=...) → RenderPlan.bgm_source == bgm.source（観点3）。"""
        plan = self._build_with_bgm(bgm_source="/proj/bgm.mp3")
        assert plan.bgm_source == "/proj/bgm.mp3"  # type: ignore[attr-defined]

    def test_3_bgm_source_none_when_bgm_not_provided(self) -> None:
        """bgm=None → RenderPlan.bgm_source is None（後方互換・ADR-B7）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert plan.bgm_source is None  # type: ignore[attr-defined]

    def test_4_bgm_index_equals_len_input_sources(self) -> None:
        """BGM filter の入力ラベルが [len(input_sources):a]（DC-AS-005・観点4）。"""
        plan = self._build_with_bgm()
        # 単一ソース経路: input_sources=1件 → BGM index=1 → [1:a]
        expected_label = f"[{len(plan.input_sources)}:a]"
        assert expected_label in plan.filter_complex

    def test_4_bgm_source_not_in_input_sources(self) -> None:
        """bgm_source は input_sources に含まれない（DC-AS-005・観点4）。"""
        plan = self._build_with_bgm(bgm_source="/proj/bgm.mp3")
        assert plan.bgm_source not in plan.input_sources  # type: ignore[attr-defined]

    def test_4_bgm_index_two_sources(self) -> None:
        """2本編ソース + BGM → BGM index=2（[2:a] が filter に含まれる・DC-AS-005）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        clips = [_make_clip("/src/a.mp4", 0.0, 3.0), _make_clip("/src/b.mp4", 0.0, 2.0)]
        tl = _make_timeline_with_clips(clips)
        ranges = resolve_kept_ranges(tl)
        source_probes = {
            "/src/a.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
            "/src/b.mp4": _make_probe(audio_count=1, width=1920, height=1080, fps=30.0),
        }
        bgm_clip = _make_bgm_clip(bgm_source="/proj/bgm.mp3")
        plan = build_plan(
            ranges,
            source_probes["/src/a.mp4"],
            RenderOptions(),
            source_probes=source_probes,
            bgm=bgm_clip,  # type: ignore[call-arg]
        )
        # 2ソース → BGM index=2
        assert "[2:a]" in plan.filter_complex
        assert plan.bgm_source not in plan.input_sources  # type: ignore[attr-defined]
        assert len(plan.input_sources) == 2


# ---------------------------------------------------------------------------
# 観点5: BGM filter 文字列（実機確認済み構文・ADR-B5-r3/B6-r2）
# ---------------------------------------------------------------------------


class TestBuildPlanBgmFilterComplex:
    """BGM filter_complex の文字列構成を検証する（観点5・ADR-B5-r3/B6-r2）。"""

    def _build_with_bgm_fc(
        self,
        directive: dict | None = None,
        audio_count: int = 1,
        bgm_source: str = "/proj/bgm.mp3",
    ) -> tuple[str, RenderPlan]:  # type: ignore[name-defined]
        """filter_complex 文字列と RenderPlan を返す単一ソース BGM テストヘルパー。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=audio_count, bit_rate=None)
        bgm_clip = _make_bgm_clip(bgm_source=bgm_source, directive=directive)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        return plan.filter_complex, plan

    def test_5_bgm_filter_has_aformat_48000_stereo(self) -> None:
        """BGM 側に aformat=sample_rates=48000:channel_layouts=stereo が含まれる（DC-AS-007）。"""
        fc, _ = self._build_with_bgm_fc()
        assert "aformat=sample_rates=48000:channel_layouts=stereo" in fc

    def test_5_bgm_filter_has_atrim_main_dur(self) -> None:
        """BGM filter に atrim=0:{main_dur} が含まれる（ADR-B6-r2・-stream_loop + atrim）。"""
        fc, _ = self._build_with_bgm_fc()
        # main_dur=5.0（1クリップ duration=5.0）
        assert "atrim=0:5" in fc

    def test_5_bgm_filter_has_volume_db(self) -> None:
        """BGM filter に volume={db}dB が含まれる（観点5）。"""
        fc, _ = self._build_with_bgm_fc()
        assert "volume=-6dB" in fc or "volume=-6.0dB" in fc

    def test_5_bgm_filter_has_asetpts(self) -> None:
        """BGM filter に asetpts=PTS-STARTPTS が含まれる（観点5）。"""
        fc, _ = self._build_with_bgm_fc()
        assert "asetpts=PTS-STARTPTS" in fc

    def test_5_main_fmt_aformat_present(self) -> None:
        """本編側に aformat=sample_rates=48000:channel_layouts=stereo が含まれる（DC-AS-007）。

        単一ソース経路でも amix 入力規格統一のため本編側 aformat は必須（ADR-B5-r3）。
        """
        fc, _ = self._build_with_bgm_fc()
        # [main_fmt] が作られ、aformat が含まれる
        assert "main_fmt" in fc
        assert "aformat=sample_rates=48000:channel_layouts=stereo" in fc

    def test_5_amix_inputs2_normalize0_present(self) -> None:
        """amix=inputs=2:normalize=0 が含まれる（ADR-B5-r3）。"""
        fc, _ = self._build_with_bgm_fc()
        assert "amix=inputs=2:normalize=0" in fc

    def test_5_alimiter_present(self) -> None:
        """alimiter=limit=1.0 が含まれる（DC-AM-001・クリッピング対策）。"""
        fc, _ = self._build_with_bgm_fc()
        assert "alimiter=limit=1.0" in fc

    def test_5_outa_bgm_label_present(self) -> None:
        """[outa_bgm] ラベルが filter_complex に含まれる（観点5）。"""
        fc, _ = self._build_with_bgm_fc()
        assert "[outa_bgm]" in fc

    def test_5_aloop_not_present(self) -> None:
        """aloop は filter_complex に含まれない（ADR-B6-r2・aloop 廃止）。"""
        fc, _ = self._build_with_bgm_fc()
        assert "aloop" not in fc


# ---------------------------------------------------------------------------
# 観点6: fade_in/out=0 では afade が入らない（ADR-B9-r3・DC-AM-003）
# ---------------------------------------------------------------------------


class TestBuildPlanBgmFade:
    """afade の注入条件を検証する（観点6・ADR-B9-r3/DC-AM-003）。"""

    def test_6_fade_in_zero_no_afade_in(self) -> None:
        """fade_in_sec=0.0 → afade=t=in が filter_complex に含まれない（DC-AM-003）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        d = dict(_VALID_BGM_DIRECTIVE, fade_in_sec=0.0)
        bgm_clip = _make_bgm_clip(directive=d)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "afade=t=in" not in plan.filter_complex

    def test_6_fade_out_zero_no_afade_out(self) -> None:
        """fade_out_sec=0.0 → afade=t=out が filter_complex に含まれない（DC-AM-003）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        d = dict(_VALID_BGM_DIRECTIVE, fade_out_sec=0.0)
        bgm_clip = _make_bgm_clip(directive=d)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "afade=t=out" not in plan.filter_complex

    def test_6_fade_in_positive_afade_in_present(self) -> None:
        """fade_in_sec > 0 → afade=t=in が filter_complex に含まれる（DC-AM-003）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        d = dict(_VALID_BGM_DIRECTIVE, fade_in_sec=1.0)
        bgm_clip = _make_bgm_clip(directive=d)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "afade=t=in" in plan.filter_complex

    def test_6_fade_out_positive_afade_out_present(self) -> None:
        """fade_out_sec > 0 → afade=t=out が filter_complex に含まれる（DC-AM-003）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        d = dict(_VALID_BGM_DIRECTIVE, fade_out_sec=1.5)
        bgm_clip = _make_bgm_clip(directive=d)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "afade=t=out" in plan.filter_complex


# ---------------------------------------------------------------------------
# 観点7: ducking ON/OFF（ADR-B5-r3・DC-AS-006）
# ---------------------------------------------------------------------------


class TestBuildPlanBgmDucking:
    """ducking ON/OFF の filter 生成を検証する（観点7・ADR-B5-r3/DC-AS-006）。"""

    def test_7_ducking_off_no_sidechaincompress(self) -> None:
        """ducking.enabled=False → sidechaincompress が filter に含まれない（観点7）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip(directive=_VALID_BGM_DIRECTIVE)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "sidechaincompress" not in plan.filter_complex

    def test_7_ducking_on_sidechaincompress_present(self) -> None:
        """ducking.enabled=True → sidechaincompress が filter_complex に含まれる（観点7）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip(directive=_VALID_BGM_DIRECTIVE_DUCKING)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "sidechaincompress" in plan.filter_complex

    def test_7_ducking_on_threshold_ratio_in_filter(self) -> None:
        """ducking ON → threshold=0.05:ratio=4.0 が filter に含まれる（DC-AS-006）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip(directive=_VALID_BGM_DIRECTIVE_DUCKING)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        fc = plan.filter_complex
        assert "threshold=0.05" in fc
        assert "ratio=4.0" in fc or "ratio=4" in fc

    def test_7_ducking_on_bgm_is_first_input_of_sidechaincompress(self) -> None:
        """ducking ON: [bgm][main_sc]sidechaincompress の順序（BGM=第1入力・DC-AS-006）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip(directive=_VALID_BGM_DIRECTIVE_DUCKING)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        fc = plan.filter_complex
        # [bgm] が sidechaincompress より前に出現（[bgm]...[main_sc]sidechaincompress の順）
        bgm_pos = fc.find("[bgm]")
        sc_pos = fc.find("sidechaincompress")
        assert bgm_pos != -1 and sc_pos != -1
        assert bgm_pos < sc_pos, (
            f"[bgm] (pos={bgm_pos}) が sidechaincompress (pos={sc_pos}) より後にある"
        )

    def test_7_ducking_on_asplit_present(self) -> None:
        """ducking ON → asplit が filter_complex に含まれる（本編を2分岐・DC-AS-006）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip(directive=_VALID_BGM_DIRECTIVE_DUCKING)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "asplit" in plan.filter_complex

    def test_7_ducking_on_outa_bgm_in_ffmpeg_args(self) -> None:
        """ducking ON でも audio_map_label == [outa_bgm]（観点7）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip(directive=_VALID_BGM_DIRECTIVE_DUCKING)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "[outa_bgm]" in plan.ffmpeg_args


# ---------------------------------------------------------------------------
# 観点8: 既存音声パイプ後段に BGM 段が乗る（denoise/loudness との連携）
# ---------------------------------------------------------------------------


class TestBuildPlanBgmAfterAudioPipe:
    """denoise/loudness 後段に BGM 段が乗り、終端ラベルを正しく参照する（観点8）。"""

    def _build_with_denoise_and_bgm(self) -> RenderPlan:  # type: ignore[name-defined]
        """denoise + BGM の build_plan ヘルパー。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip()
        return build_plan(
            ranges,
            probe,
            RenderOptions(),
            denoise=_VALID_AFFTDN_DIRECTIVE,
            bgm=bgm_clip,  # type: ignore[call-arg]
        )

    def _build_with_loudness_and_bgm(self) -> RenderPlan:  # type: ignore[name-defined]
        """loudness + BGM の build_plan ヘルパー。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip()
        return build_plan(
            ranges,
            probe,
            RenderOptions(),
            loudness=_VALID_PEAK_DIRECTIVE,
            bgm=bgm_clip,  # type: ignore[call-arg]
        )

    def test_8_denoise_then_bgm_audio_map_label_outa_bgm(self) -> None:
        """denoise あり + BGM → audio_map_label == [outa_bgm]（観点8）。"""
        plan = self._build_with_denoise_and_bgm()
        assert "[outa_bgm]" in plan.ffmpeg_args

    def test_8_denoise_then_bgm_afftdn_present_in_filter(self) -> None:
        """denoise あり + BGM → afftdn が filter_complex に含まれる（観点8）。"""
        plan = self._build_with_denoise_and_bgm()
        assert "afftdn" in plan.filter_complex

    def test_8_denoise_then_bgm_main_fmt_uses_outa_dn(self) -> None:
        """denoise あり + BGM → [main_fmt] は [outa_dn] を aformat した系統（観点8）。

        BGM 段の本編入力は denoise 終端 [outa_dn] を aformat したものになる。
        [outa_dn] または [outa_dn] 後に aformat されたラベルが main_fmt として
        filter_complex に現れることを確認する。
        """
        plan = self._build_with_denoise_and_bgm()
        fc = plan.filter_complex
        # [outa_dn] が filter に含まれる
        assert "[outa_dn]" in fc
        # [main_fmt] が filter に含まれる（aformat の先頭入力として [outa_dn] 参照）
        assert "main_fmt" in fc
        # [outa_dn] が main_fmt の前に出現する（正しい接続順）
        dn_pos = fc.find("[outa_dn]")
        fmt_pos = fc.find("main_fmt")
        assert dn_pos < fmt_pos, (
            f"[outa_dn] (pos={dn_pos}) が main_fmt (pos={fmt_pos}) より後"
        )

    def test_8_loudness_then_bgm_audio_map_label_outa_bgm(self) -> None:
        """loudness あり + BGM → audio_map_label == [outa_bgm]（観点8）。"""
        plan = self._build_with_loudness_and_bgm()
        assert "[outa_bgm]" in plan.ffmpeg_args

    def test_8_loudness_then_bgm_outa_ln_present_in_filter(self) -> None:
        """loudness あり + BGM → [outa_ln] が filter_complex に含まれる（観点8）。"""
        plan = self._build_with_loudness_and_bgm()
        assert "[outa_ln]" in plan.filter_complex


# ---------------------------------------------------------------------------
# 観点9: 後方互換（BGM なしで既存 filter_complex と完全一致・ADR-B7）
# ---------------------------------------------------------------------------


class TestBuildPlanBgmBackwardCompat:
    """bgm=None のとき既存 filter_complex が変わらない（観点9・ADR-B7）。"""

    def test_9_bgm_none_filter_complex_unchanged(self) -> None:
        """bgm=None → filter_complex が BGM 段なし従来形式と完全一致（ADR-B7）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        # BGM なし（デフォルト）
        plan_no_bgm = build_plan(ranges, probe, RenderOptions())
        # bgm=None 明示
        plan_bgm_none = build_plan(ranges, probe, RenderOptions(), bgm=None)  # type: ignore[call-arg]
        assert plan_no_bgm.filter_complex == plan_bgm_none.filter_complex

    def test_9_bgm_none_no_outa_bgm_in_filter(self) -> None:
        """bgm=None → [outa_bgm] が filter_complex に含まれない（ADR-B7）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "[outa_bgm]" not in plan.filter_complex

    def test_9_bgm_none_bgm_source_is_none(self) -> None:
        """bgm=None → RenderPlan.bgm_source is None（ADR-B7）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert plan.bgm_source is None  # type: ignore[attr-defined]

    def test_9_bgm_none_no_alimiter_in_filter(self) -> None:
        """bgm=None → alimiter が filter_complex に含まれない（BGM 段なし確認・ADR-B7）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        plan = build_plan(ranges, probe, RenderOptions())
        assert "alimiter" not in plan.filter_complex


# ---------------------------------------------------------------------------
# 観点10: 本編無音（has_main_audio=False）+ BGM（ADR-B5-r2・DC-AS-004）
# ---------------------------------------------------------------------------


class TestBuildPlanBgmNoMainAudio:
    """本編無音 + BGM → BGM 単独系統で has_audio_output=True（観点10・ADR-B5-r2）。"""

    def _build_no_main_audio_with_bgm(self) -> RenderPlan:  # type: ignore[name-defined]
        """本編音声なし（audio_count=0）+ BGM の build_plan ヘルパー。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        bgm_clip = _make_bgm_clip()
        return build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]

    def test_10_no_main_audio_with_bgm_has_audio_map(self) -> None:
        """本編無音 + BGM → -map [outa_bgm] が ffmpeg_args に含まれる（観点10）。"""
        plan = self._build_no_main_audio_with_bgm()
        assert "[outa_bgm]" in plan.ffmpeg_args

    def test_10_no_main_audio_with_bgm_no_amix(self) -> None:
        """本編無音 + BGM → amix が filter_complex に含まれない（BGM 単独系統・ADR-B5-r2）。"""
        plan = self._build_no_main_audio_with_bgm()
        # 本編音声がないため amix は不要（BGM が唯一の音声）
        assert "amix" not in plan.filter_complex

    def test_10_no_main_audio_with_bgm_concat_a0(self) -> None:
        """本編無音 + BGM → concat は a=0（映像のみ concat・ADR-B5-r2）。"""
        plan = self._build_no_main_audio_with_bgm()
        assert "a=0" in plan.filter_complex

    def test_10_no_main_audio_with_bgm_outa_bgm_in_filter(self) -> None:
        """本編無音 + BGM → [outa_bgm] が filter_complex に含まれる（ADR-B5-r2）。"""
        plan = self._build_no_main_audio_with_bgm()
        assert "[outa_bgm]" in plan.filter_complex


# ---------------------------------------------------------------------------
# 観点11: denoise/loudness スキップ警告は has_main_audio=False で出る（DC-AM-004）
# ---------------------------------------------------------------------------


class TestBuildPlanBgmAudioWarnings:
    """denoise/loudness スキップ警告の出現条件を検証する（観点11・DC-AM-004）。"""

    def test_11_no_main_audio_with_denoise_adds_warning(self) -> None:
        """has_main_audio=False + denoise → スキップ警告が出る（DC-AM-004）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        bgm_clip = _make_bgm_clip()
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(),
            denoise=_VALID_AFFTDN_DIRECTIVE,
            bgm=bgm_clip,  # type: ignore[call-arg]
        )
        warning_text = " ".join(plan.warnings)
        assert "denoise" in warning_text or "スキップ" in warning_text

    def test_11_no_main_audio_with_loudness_adds_warning(self) -> None:
        """has_main_audio=False + loudness → スキップ警告が出る（DC-AM-004）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=0, bit_rate=None)
        bgm_clip = _make_bgm_clip()
        plan = build_plan(
            ranges,
            probe,
            RenderOptions(),
            loudness=_VALID_PEAK_DIRECTIVE,
            bgm=bgm_clip,  # type: ignore[call-arg]
        )
        warning_text = " ".join(plan.warnings)
        assert "loudness" in warning_text or "スキップ" in warning_text

    def test_11_has_main_audio_with_bgm_no_skip_warning(self) -> None:
        """has_main_audio=True + BGM → denoise/loudness スキップ警告は出ない（DC-AM-004）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip()
        # denoise/loudness なし・BGM ありで警告がないこと
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        warning_text = " ".join(plan.warnings)
        # BGM あっても音声スキップ警告は出ない（has_main_audio=True）
        assert "denoise スキップ" not in warning_text
        assert "loudness スキップ" not in warning_text


# ===========================================================================
# レビュー指摘テスト（CR L-1/L-2/M-1、SR M-1/I-1/M-3）
# ===========================================================================


# ---------------------------------------------------------------------------
# 観点12: resolve_bgm の ValidationError パス（CR L-2/M-1）
# ---------------------------------------------------------------------------


class TestResolveBgmValidationError:
    """resolve_bgm に不正 metadata を持つ Timeline を渡したとき INVALID_INPUT が送出される（CR L-2/M-1）。"""

    def test_12_volume_db_string_raises_invalid_input(self) -> None:
        """volume_db が文字列型 → resolve_bgm が INVALID_INPUT を送出する（CR L-2）。"""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(_VALID_BGM_DIRECTIVE, volume_db="not_a_number")
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_12_missing_required_tool_field_raises_invalid_input(self) -> None:
        """必須フィールド tool 欠落（kind="bgm" は残す） → resolve_bgm が INVALID_INPUT を送出する（CR L-2）。

        kind="bgm" は存在するため BGM クリップとして収集されるが、
        tool フィールド欠落で BgmDirective バリデーションが失敗する。
        """
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = {k: v for k, v in _VALID_BGM_DIRECTIVE.items() if k != "tool"}
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_12_unknown_extra_field_raises_invalid_input(self) -> None:
        """未知フィールド（extra=forbid） → resolve_bgm が INVALID_INPUT を送出する（CR M-1）。"""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(_VALID_BGM_DIRECTIVE, unknown_evil_field="x")
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_12_volume_db_inf_raises_invalid_input(self) -> None:
        """volume_db=inf → resolve_bgm が INVALID_INPUT を送出する（CR L-2）。"""
        import math

        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(_VALID_BGM_DIRECTIVE, volume_db=math.inf)
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT


# ---------------------------------------------------------------------------
# 観点13: DuckingDirective の inf/nan・範囲外バリデーション（SR M-1）
# ---------------------------------------------------------------------------


class TestResolveBgmDuckingDirectiveValidation:
    """DuckingDirective に inf/nan・範囲外値を持つ Timeline を resolve_bgm に渡したとき INVALID_INPUT が送出される（SR M-1）。"""

    def test_13_ducking_threshold_inf_raises_invalid_input(self) -> None:
        """ducking.threshold=inf → resolve_bgm が INVALID_INPUT を送出する（SR M-1）。"""
        import math

        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(
            _VALID_BGM_DIRECTIVE,
            ducking={"enabled": True, "threshold": math.inf, "ratio": 4.0},
        )
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_13_ducking_threshold_nan_raises_invalid_input(self) -> None:
        """ducking.threshold=nan → resolve_bgm が INVALID_INPUT を送出する（SR M-1）。"""
        import math

        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(
            _VALID_BGM_DIRECTIVE,
            ducking={"enabled": False, "threshold": math.nan, "ratio": 4.0},
        )
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_13_ducking_threshold_zero_raises_invalid_input(self) -> None:
        """ducking.threshold=0.0（gt=0.0 制約違反） → resolve_bgm が INVALID_INPUT を送出する（SR M-1）。"""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(
            _VALID_BGM_DIRECTIVE,
            ducking={"enabled": False, "threshold": 0.0, "ratio": 4.0},
        )
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_13_ducking_threshold_over_one_raises_invalid_input(self) -> None:
        """ducking.threshold=1.1（le=1.0 制約違反） → resolve_bgm が INVALID_INPUT を送出する（SR M-1）。"""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(
            _VALID_BGM_DIRECTIVE,
            ducking={"enabled": False, "threshold": 1.1, "ratio": 4.0},
        )
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_13_ducking_ratio_zero_raises_invalid_input(self) -> None:
        """ducking.ratio=0.9（ge=1.0 制約違反） → resolve_bgm が INVALID_INPUT を送出する（SR M-1）。"""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(
            _VALID_BGM_DIRECTIVE,
            ducking={"enabled": False, "threshold": 0.05, "ratio": 0.9},
        )
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_13_ducking_ratio_over_twenty_raises_invalid_input(self) -> None:
        """ducking.ratio=20.1（le=20.0 制約違反） → resolve_bgm が INVALID_INPUT を送出する（SR M-1）。"""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(
            _VALID_BGM_DIRECTIVE,
            ducking={"enabled": False, "threshold": 0.05, "ratio": 20.1},
        )
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_13_ducking_ratio_nan_raises_invalid_input(self) -> None:
        """ducking.ratio=nan → resolve_bgm が INVALID_INPUT を送出する（SR M-1）。"""
        import math

        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(
            _VALID_BGM_DIRECTIVE,
            ducking={"enabled": False, "threshold": 0.05, "ratio": math.nan},
        )
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_13_valid_ducking_defaults_resolve_ok(self) -> None:
        """既定値 threshold=0.05/ratio=4.0 → resolve_bgm が正常終了する（SR M-1 正常系）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            BgmClip,
            resolve_bgm,
        )

        tl = _make_bgm_otio_timeline(directive=_VALID_BGM_DIRECTIVE)
        result = resolve_bgm(tl)
        assert isinstance(result, BgmClip)
        assert result.directive.ducking.threshold == 0.05
        assert result.directive.ducking.ratio == 4.0


# ---------------------------------------------------------------------------
# 観点14: BgmDirective.volume_db 範囲外バリデーション（SR I-1）
# ---------------------------------------------------------------------------


class TestBgmDirectiveVolumeDbRange:
    """BgmDirective.volume_db の範囲外値が resolve_bgm で INVALID_INPUT になることを検証する（SR I-1）。"""

    def test_14_volume_db_too_low_raises_invalid_input(self) -> None:
        """volume_db=-200（ge=-60.0 制約違反） → resolve_bgm が INVALID_INPUT を送出する（SR I-1）。"""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(_VALID_BGM_DIRECTIVE, volume_db=-200.0)
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_14_volume_db_too_high_raises_invalid_input(self) -> None:
        """volume_db=100（le=20.0 制約違反） → resolve_bgm が INVALID_INPUT を送出する（SR I-1）。"""
        from clipwright_render.plan import resolve_bgm  # type: ignore[attr-defined]

        bad_directive = dict(_VALID_BGM_DIRECTIVE, volume_db=100.0)
        tl = _make_bgm_otio_timeline(directive=bad_directive)
        with pytest.raises(ClipwrightError) as exc_info:
            resolve_bgm(tl)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_14_volume_db_boundary_low_ok(self) -> None:
        """volume_db=-60.0（境界値・ge=-60 ちょうど） → resolve_bgm が正常終了する（SR I-1 正常系）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            BgmClip,
            resolve_bgm,
        )

        boundary_directive = dict(_VALID_BGM_DIRECTIVE, volume_db=-60.0)
        tl = _make_bgm_otio_timeline(directive=boundary_directive)
        result = resolve_bgm(tl)
        assert isinstance(result, BgmClip)
        assert result.directive.volume_db == -60.0

    def test_14_volume_db_boundary_high_ok(self) -> None:
        """volume_db=20.0（境界値・le=20 ちょうど） → resolve_bgm が正常終了する（SR I-1 正常系）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            BgmClip,
            resolve_bgm,
        )

        boundary_directive = dict(_VALID_BGM_DIRECTIVE, volume_db=20.0)
        tl = _make_bgm_otio_timeline(directive=boundary_directive)
        result = resolve_bgm(tl)
        assert isinstance(result, BgmClip)
        assert result.directive.volume_db == 20.0


# ---------------------------------------------------------------------------
# 観点15: fade_out_sec/fade_in_sec > main_dur ガード（SR M-3）
# ---------------------------------------------------------------------------


class TestBuildPlanBgmFadeGuard:
    """fade_out_sec/fade_in_sec が本編尺を超える場合に INVALID_INPUT が送出されることを検証する（SR M-3）。"""

    def _build_with_fade(
        self,
        fade_in_sec: float = 0.0,
        fade_out_sec: float = 0.0,
        main_duration: float = 5.0,
    ) -> None:
        """指定 fade 設定で build_plan を実行するヘルパー（例外伝播を呼び出し側でハンドル）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        d = dict(
            _VALID_BGM_DIRECTIVE, fade_in_sec=fade_in_sec, fade_out_sec=fade_out_sec
        )
        tl = _make_single_source_timeline_with_audio(duration=main_duration)
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip(directive=d, timeline_duration_sec=main_duration)
        build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]

    def test_15_fade_out_exceeds_main_dur_raises_invalid_input(self) -> None:
        """fade_out_sec > main_dur → build_plan が INVALID_INPUT を送出する（SR M-3）。"""
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_fade(fade_out_sec=10.0, main_duration=5.0)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_15_fade_in_exceeds_main_dur_raises_invalid_input(self) -> None:
        """fade_in_sec > main_dur → build_plan が INVALID_INPUT を送出する（SR M-3）。"""
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_fade(fade_in_sec=6.0, main_duration=5.0)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_15_fade_out_error_message_contains_fade_out(self) -> None:
        """fade_out_sec 超過エラーの message に "fade_out" が含まれる（NR-L-3: どちらが超過したか区別）。"""
        # Arrange / Act
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_fade(fade_out_sec=10.0, main_duration=5.0)
        # Assert: fade_out_sec 超過であることが message から識別できる
        assert exc_info.value.code == ErrorCode.INVALID_INPUT
        assert "fade_out" in exc_info.value.message

    def test_15_fade_in_error_message_contains_fade_in(self) -> None:
        """fade_in_sec 超過エラーの message に "fade_in" が含まれる（NR-L-3: どちらが超過したか区別）。"""
        # Arrange / Act
        with pytest.raises(ClipwrightError) as exc_info:
            self._build_with_fade(fade_in_sec=6.0, main_duration=5.0)
        # Assert: fade_in_sec 超過であることが message から識別できる
        assert exc_info.value.code == ErrorCode.INVALID_INPUT
        assert "fade_in" in exc_info.value.message

    def test_15_fade_out_equals_main_dur_ok(self) -> None:
        """fade_out_sec == main_dur（ちょうど） → build_plan が正常終了する（SR M-3 境界値）。"""
        self._build_with_fade(fade_out_sec=5.0, main_duration=5.0)

    def test_15_fade_in_equals_main_dur_ok(self) -> None:
        """fade_in_sec == main_dur（ちょうど） → build_plan が正常終了する（SR M-3 境界値）。"""
        self._build_with_fade(fade_in_sec=5.0, main_duration=5.0)

    def test_15_fade_zero_is_ok(self) -> None:
        """fade_in_sec=0・fade_out_sec=0 → build_plan が正常終了し afade が含まれない（従来動作）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        d = dict(_VALID_BGM_DIRECTIVE, fade_in_sec=0.0, fade_out_sec=0.0)
        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip(directive=d)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "afade" not in plan.filter_complex

    def test_15_fade_out_within_main_dur_ok_afade_out_present(self) -> None:
        """fade_out_sec < main_dur → build_plan が正常終了し afade=t=out が含まれる（従来動作）。"""
        from clipwright_render.plan import (  # type: ignore[attr-defined]
            build_plan,
            resolve_kept_ranges,
        )

        d = dict(_VALID_BGM_DIRECTIVE, fade_out_sec=2.0)
        tl = _make_single_source_timeline_with_audio()
        ranges = resolve_kept_ranges(tl)
        probe = ProbeInfo(has_video=True, audio_count=1, bit_rate=None)
        bgm_clip = _make_bgm_clip(directive=d)
        plan = build_plan(ranges, probe, RenderOptions(), bgm=bgm_clip)  # type: ignore[call-arg]
        assert "afade=t=out" in plan.filter_complex
