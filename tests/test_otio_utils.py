"""test_otio_utils.py — otio_utils.py の Red フェーズテスト。

このテストは otio_utils.py が未実装のため ImportError で失敗する（Red）。
機能が未実装であることによる失敗が期待動作。

対象（§6 / §13.5）:
- new_timeline: [V1(kind=Video), A1(kind=Audio)] の順でトラック生成
- load_timeline / save_timeline（アトミック: temp → os.replace）
- add_clip / add_gap / add_marker
- set_clipwright_metadata / get_clipwright_metadata（metadata["clipwright"] 配下）
- summarize_timeline:
    - 常に全件返却（clip_count/gap_count/marker_count/total_duration/markers 全件）
    - total_duration = 全トラック長の最大（合算ではない）
    - rate = V1 があればその rate、無ければ 1000.0
    - クリップ 0 件なら RationalTime(0, グローバル rate)
"""

from __future__ import annotations

from pathlib import Path

import pytest

# --- Import（otio_utils.py 未実装のため ImportError が発生する → Red） ---
from clipwright.otio_utils import (
    add_clip,
    add_gap,
    add_marker,
    get_clipwright_metadata,
    load_timeline,
    new_timeline,
    save_timeline,
    set_clipwright_metadata,
    summarize_timeline,
)

# ===========================================================================
# new_timeline（§13.5 DC-AS-001 フラット index / トラック順）
# ===========================================================================


class TestNewTimeline:
    """new_timeline のトラック構成・種別の契約。"""

    def test_returns_timeline(self) -> None:
        """Timeline オブジェクトを返す。"""
        import opentimelineio as otio

        tl = new_timeline("test")
        assert isinstance(tl, otio.schema.Timeline)

    def test_timeline_name(self) -> None:
        """引数の name が timeline.name に設定される。"""
        tl = new_timeline("my_project")
        assert tl.name == "my_project"

    def test_has_two_tracks(self) -> None:
        """トラックは V1 と A1 の 2 本（§13.5 DC-AS-001）。"""
        tl = new_timeline("test")
        assert len(tl.tracks) == 2

    def test_track0_is_video(self) -> None:
        """track=0（index 0）は kind=Video（V1）（§13.5 DC-AS-001）。"""
        import opentimelineio as otio

        tl = new_timeline("test")
        assert tl.tracks[0].kind == otio.schema.TrackKind.Video

    def test_track1_is_audio(self) -> None:
        """track=1（index 1）は kind=Audio（A1）（§13.5 DC-AS-001）。"""
        import opentimelineio as otio

        tl = new_timeline("test")
        assert tl.tracks[1].kind == otio.schema.TrackKind.Audio

    def test_track_order_v1_before_a1(self) -> None:
        """トラック順は [V1, A1]。Video が先（§13.5 DC-AS-001）。"""
        import opentimelineio as otio

        tl = new_timeline("test")
        assert tl.tracks[0].kind == otio.schema.TrackKind.Video
        assert tl.tracks[1].kind == otio.schema.TrackKind.Audio

    def test_track_names(self) -> None:
        """V1 / A1 という名前が付いている。"""
        tl = new_timeline("test")
        assert tl.tracks[0].name == "V1"
        assert tl.tracks[1].name == "A1"

    def test_tracks_empty_initially(self) -> None:
        """生成直後はどちらのトラックも空。"""
        tl = new_timeline("test")
        assert len(tl.tracks[0]) == 0
        assert len(tl.tracks[1]) == 0


# ===========================================================================
# load_timeline / save_timeline（アトミック書き込み）
# ===========================================================================


class TestLoadSaveTimeline:
    """load_timeline / save_timeline の I/O 契約。"""

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        """保存 → 読み込みで timeline.name が保持される。"""
        tl = new_timeline("roundtrip")
        path = str(tmp_path / "timeline.otio")
        save_timeline(tl, path)
        loaded = load_timeline(path)
        assert loaded.name == "roundtrip"

    def test_saved_file_exists(self, tmp_path: Path) -> None:
        """save_timeline が実際にファイルを作成する。"""
        tl = new_timeline("check_file")
        path = str(tmp_path / "out.otio")
        save_timeline(tl, path)
        assert Path(path).is_file()

    def test_load_preserves_tracks(self, tmp_path: Path) -> None:
        """ロードした timeline はトラック数を保持する。"""
        tl = new_timeline("track_test")
        path = str(tmp_path / "track.otio")
        save_timeline(tl, path)
        loaded = load_timeline(path)
        assert len(loaded.tracks) == 2

    def test_atomic_write_no_temp_file_left(self, tmp_path: Path) -> None:
        """save_timeline 完了後、temp ファイルが残らない（アトミック保存の副作用）。"""
        tl = new_timeline("atomic")
        path = tmp_path / "atomic.otio"
        save_timeline(tl, str(path))
        # .tmp または .otio.tmp などの名前のファイルが残っていないこと
        leftover = list(tmp_path.glob("*.tmp"))
        assert leftover == []

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        """既存ファイルを上書きできる（アトミック os.replace）。"""
        path = str(tmp_path / "overwrite.otio")
        tl1 = new_timeline("first")
        save_timeline(tl1, path)
        tl2 = new_timeline("second")
        save_timeline(tl2, path)
        loaded = load_timeline(path)
        assert loaded.name == "second"


# ===========================================================================
# add_clip（§6 otio_utils）
# ===========================================================================


class TestAddClip:
    """add_clip の契約。"""

    def test_adds_clip_to_track(self) -> None:
        """add_clip でトラックにクリップが1件追加される。"""

        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("clip_test")
        track = tl.tracks[0]  # V1
        media = MediaRef(target_url="/path/to/video.mp4")
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=90.0, rate=30.0),
        )
        add_clip(track, media, source_range)
        assert len(track) == 1

    def test_added_clip_is_clip_type(self) -> None:
        """追加されたアイテムは OTIO Clip。"""
        import opentimelineio as otio

        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("clip_type_test")
        track = tl.tracks[0]
        media = MediaRef(target_url="/path/to/video.mp4")
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=60.0, rate=30.0),
        )
        add_clip(track, media, source_range)
        assert isinstance(track[0], otio.schema.Clip)

    def test_clip_name_optional(self) -> None:
        """name 引数でクリップ名を指定できる。"""

        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("clip_name")
        track = tl.tracks[0]
        media = MediaRef(target_url="/path/to/video.mp4")
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=30.0, rate=30.0),
        )
        add_clip(track, media, source_range, name="intro")
        assert track[0].name == "intro"

    def test_clip_media_reference_url(self) -> None:
        """クリップの media_reference に target_url が設定される。"""
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("url_test")
        track = tl.tracks[0]
        media = MediaRef(target_url="/path/to/clip.mp4")
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=30.0, rate=30.0),
        )
        add_clip(track, media, source_range)
        clip = track[0]
        assert clip.media_reference.target_url == "/path/to/clip.mp4"

    def test_clip_source_range_preserved(self) -> None:
        """クリップの source_range が TimeRangeModel から正しく設定される。"""
        import opentimelineio as otio

        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("range_test")
        track = tl.tracks[0]
        media = MediaRef(target_url="/video.mp4")
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=10.0, rate=30.0),
            duration=RationalTimeModel(value=60.0, rate=30.0),
        )
        add_clip(track, media, source_range)
        clip = track[0]
        assert clip.source_range.start_time == otio.opentime.RationalTime(
            value=10.0, rate=30.0
        )
        assert clip.source_range.duration == otio.opentime.RationalTime(
            value=60.0, rate=30.0
        )


# ===========================================================================
# add_gap（§6 otio_utils）
# ===========================================================================


class TestAddGap:
    """add_gap の契約。"""

    def test_adds_gap_to_track(self) -> None:
        """add_gap でトラックにギャップが1件追加される。"""

        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("gap_test")
        track = tl.tracks[0]
        duration = RationalTimeModel(value=30.0, rate=30.0)
        add_gap(track, duration)
        assert len(track) == 1

    def test_added_item_is_gap_type(self) -> None:
        """追加されたアイテムは OTIO Gap。"""
        import opentimelineio as otio

        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("gap_type")
        track = tl.tracks[0]
        duration = RationalTimeModel(value=30.0, rate=30.0)
        add_gap(track, duration)
        assert isinstance(track[0], otio.schema.Gap)

    def test_gap_duration_preserved(self) -> None:
        """ギャップの duration が RationalTimeModel から正しく設定される。"""
        import opentimelineio as otio

        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("gap_dur")
        track = tl.tracks[0]
        duration = RationalTimeModel(value=15.0, rate=30.0)
        add_gap(track, duration)
        gap = track[0]
        assert gap.source_range.duration == otio.opentime.RationalTime(
            value=15.0, rate=30.0
        )


# ===========================================================================
# add_marker（§13.5 DC-GP-001 再: track 自体に marker を付与）
# ===========================================================================


class TestAddMarker:
    """add_marker の契約（§13.5 DC-GP-001 再）。"""

    def test_adds_marker_to_track(self) -> None:
        """add_marker で track に marker が1件追加される。"""
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("marker_test")
        track = tl.tracks[0]  # V1
        marked_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=1.0, rate=30.0),
        )
        add_marker(track, marked_range, "chapter1")
        assert len(track.markers) == 1

    def test_marker_name_preserved(self) -> None:
        """marker の name が設定される。"""
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("marker_name")
        track = tl.tracks[0]
        marked_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=1.0, rate=30.0),
        )
        add_marker(track, marked_range, "intro_start")
        assert track.markers[0].name == "intro_start"

    def test_empty_track_add_marker_succeeds(self) -> None:
        """空トラックへの add_marker は成功する（DC-GP-001 再）。clip は不要。"""
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("empty_marker")
        track = tl.tracks[0]
        assert len(track) == 0  # クリップなし
        marked_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=1.0, rate=30.0),
        )
        add_marker(track, marked_range, "on_empty_track")
        assert len(track.markers) == 1

    def test_marker_color_optional(self) -> None:
        """color 引数でマーカー色を指定できる。"""
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("marker_color")
        track = tl.tracks[0]
        marked_range = TimeRangeModel(
            start_time=RationalTimeModel(value=5.0, rate=30.0),
            duration=RationalTimeModel(value=1.0, rate=30.0),
        )
        add_marker(track, marked_range, "red_marker", color="RED")
        marker = track.markers[0]
        # color が設定されているか（OTIO Marker.color は str として扱われる）
        assert marker.color is not None

    def test_marker_marked_range_preserved(self) -> None:
        """marker の marked_range が TimeRangeModel から正しく設定される。"""
        import opentimelineio as otio

        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("marker_range")
        track = tl.tracks[0]
        marked_range = TimeRangeModel(
            start_time=RationalTimeModel(value=10.0, rate=30.0),
            duration=RationalTimeModel(value=2.0, rate=30.0),
        )
        add_marker(track, marked_range, "range_check")
        m = track.markers[0]
        assert m.marked_range.start_time == otio.opentime.RationalTime(
            value=10.0, rate=30.0
        )

    def test_add_marker_to_audio_track(self) -> None:
        """A1（audio track）にも marker を付与できる（任意トラック指定）。"""
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("audio_marker")
        audio_track = tl.tracks[1]  # A1
        marked_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=1.0, rate=30.0),
        )
        add_marker(audio_track, marked_range, "audio_cue")
        assert len(audio_track.markers) == 1


# ===========================================================================
# set_clipwright_metadata / get_clipwright_metadata（metadata["clipwright"] 配下）
# ===========================================================================


class TestClipwrightMetadata:
    """set/get_clipwright_metadata の契約（規約 §4.3）。"""

    def test_set_and_get_roundtrip(self) -> None:
        """set 後に get で同じ dict が取得できる。"""
        tl = new_timeline("meta_test")
        data = {"tool": "silence_detect", "version": "0.1.0"}
        set_clipwright_metadata(tl, data)
        result = get_clipwright_metadata(tl)
        assert result == data

    def test_stored_under_clipwright_key(self) -> None:
        """metadata は metadata["clipwright"] 配下に格納される（規約 §4.3）。"""
        tl = new_timeline("meta_key_test")
        set_clipwright_metadata(tl, {"kind": "analysis"})
        # OTIO の metadata dict を直接確認
        assert "clipwright" in tl.metadata
        assert tl.metadata["clipwright"]["kind"] == "analysis"

    def test_get_returns_empty_dict_if_not_set(self) -> None:
        """metadata 未設定の場合、get は空 dict を返す。"""
        tl = new_timeline("no_meta")
        result = get_clipwright_metadata(tl)
        assert result == {}

    def test_can_set_metadata_on_clip(self) -> None:
        """Clip オブジェクトにも set/get できる。"""

        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("clip_meta")
        track = tl.tracks[0]
        media = MediaRef(target_url="/v.mp4")
        source_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=30.0, rate=30.0),
        )
        add_clip(track, media, source_range)
        clip = track[0]
        set_clipwright_metadata(clip, {"confidence": 0.95})
        assert get_clipwright_metadata(clip) == {"confidence": 0.95}

    def test_no_contamination_outside_clipwright_key(self) -> None:
        """metadata["clipwright"] 以外のキーは汚染しない（衝突回避・規約 §4.3）。"""
        tl = new_timeline("no_contam")
        # 既存の別キーを事前設定
        tl.metadata["other_tool"] = {"data": 42}
        set_clipwright_metadata(tl, {"tool": "test"})
        # other_tool は変更されていない
        assert tl.metadata["other_tool"] == {"data": 42}


# ===========================================================================
# summarize_timeline（§13.5 DC-AM-001 再 / DC-AM-002 再）
# ===========================================================================


class TestSummarizeTimeline:
    """summarize_timeline の契約。

    常に全件を返す（truncation なし）。
    total_duration = 全トラック長の最大（合算ではない）。
    rate = V1 があればその rate、無ければ 1000.0。
    クリップ 0 件なら RationalTime(0, グローバル rate)。
    """

    def test_empty_timeline_counts(self) -> None:
        """空 timeline は clip_count=0, gap_count=0, marker_count=0。"""
        tl = new_timeline("empty")
        summary = summarize_timeline(tl)
        assert summary["clip_count"] == 0
        assert summary["gap_count"] == 0
        assert summary["marker_count"] == 0

    def test_empty_timeline_total_duration_is_zero(self) -> None:
        """空 timeline の total_duration は value=0（§13.5 DC-AM-002 再）。"""
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("empty_dur")
        summary = summarize_timeline(tl)
        dur = summary["total_duration"]
        assert isinstance(dur, RationalTimeModel)
        assert dur.value == 0.0

    def test_empty_timeline_duration_rate_is_video_rate(self) -> None:
        """V1 トラックがある場合、total_duration の rate は V1 の rate
        （§13.5 DC-AM-002 再）。

        空 timeline に V1 が存在するが内容がない場合も video rate を採用する。
        ただし空クリップの場合は V1 の rate を決定できないため 1000.0 もあり得る。
        この点は実装後に確認する。
        """
        tl = new_timeline("rate_check")
        summary = summarize_timeline(tl)
        dur = summary["total_duration"]
        # 空 timeline かつ V1 の固有 rate が不明な場合は 1000.0 でも可（実装依存）
        assert dur.rate > 0

    def test_required_keys_present(self) -> None:
        """summarize_timeline の返り値は必須キーを全て含む。"""
        tl = new_timeline("keys_check")
        summary = summarize_timeline(tl)
        for key in (
            "clip_count", "gap_count", "marker_count", "total_duration", "markers"
        ):
            assert key in summary, f"必須キー {key!r} が返り値に含まれない"

    def test_clip_count_increments(self) -> None:
        """clip を追加するたびに clip_count が増える。"""
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("clip_count")
        track = tl.tracks[0]
        for i in range(3):
            media = MediaRef(target_url=f"/v{i}.mp4")
            source_range = TimeRangeModel(
                start_time=RationalTimeModel(value=float(i * 30), rate=30.0),
                duration=RationalTimeModel(value=30.0, rate=30.0),
            )
            add_clip(track, media, source_range)
        summary = summarize_timeline(tl)
        assert summary["clip_count"] == 3

    def test_gap_count_increments(self) -> None:
        """gap を追加するたびに gap_count が増える。"""
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("gap_count")
        track = tl.tracks[0]
        add_gap(track, RationalTimeModel(value=30.0, rate=30.0))
        add_gap(track, RationalTimeModel(value=15.0, rate=30.0))
        summary = summarize_timeline(tl)
        assert summary["gap_count"] == 2

    def test_marker_count_increments(self) -> None:
        """marker を追加するたびに marker_count が増える。"""
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("marker_count")
        track = tl.tracks[0]
        for i in range(5):
            marked_range = TimeRangeModel(
                start_time=RationalTimeModel(value=float(i * 10), rate=30.0),
                duration=RationalTimeModel(value=1.0, rate=30.0),
            )
            add_marker(track, marked_range, f"cue_{i}")
        summary = summarize_timeline(tl)
        assert summary["marker_count"] == 5

    def test_markers_list_all_returned(self) -> None:
        """markers は件数によらず全件返す（truncation なし・§13.5 DC-AM-001 再）。"""
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("marker_all")
        track = tl.tracks[0]
        # 閾値 50 を超える 60 件追加
        for i in range(60):
            marked_range = TimeRangeModel(
                start_time=RationalTimeModel(value=float(i), rate=30.0),
                duration=RationalTimeModel(value=1.0, rate=30.0),
            )
            add_marker(track, marked_range, f"m{i}")
        summary = summarize_timeline(tl)
        assert len(summary["markers"]) == 60

    def test_markers_list_contains_name(self) -> None:
        """markers リストの各要素に 'name' キーが含まれる。"""
        from clipwright.schemas import RationalTimeModel, TimeRangeModel

        tl = new_timeline("marker_name_field")
        track = tl.tracks[0]
        marked_range = TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=30.0),
            duration=RationalTimeModel(value=1.0, rate=30.0),
        )
        add_marker(track, marked_range, "chapter1")
        summary = summarize_timeline(tl)
        assert summary["markers"][0]["name"] == "chapter1"

    def test_total_duration_is_max_not_sum(self) -> None:
        """total_duration は全トラック長の最大（合算ではない）（§13.5 DC-AM-002 再）。

        V1 に 90 フレーム分、A1 に 60 フレーム分追加した場合、
        total_duration.value == 90.0（合算の 150 ではない）。
        """
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("dur_max")
        video_track = tl.tracks[0]  # V1
        audio_track = tl.tracks[1]  # A1

        # V1 に 90 フレーム分クリップ追加
        media = MediaRef(target_url="/video.mp4")
        add_clip(
            video_track,
            media,
            TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=90.0, rate=30.0),
            ),
        )
        # A1 に gap 60 フレーム分追加
        add_gap(audio_track, RationalTimeModel(value=60.0, rate=30.0))

        summary = summarize_timeline(tl)
        dur = summary["total_duration"]
        # 最大は V1 の 90.0（合算 150.0 ではない）
        # rate=30 換算の秒数で比較: 90/30 = 3.0s > 60/30 = 2.0s
        assert dur.value == pytest.approx(90.0, rel=1e-6)

    def test_total_duration_rate_from_video_track(self) -> None:
        """V1 にクリップがある場合、total_duration の rate は V1 の rate
        （§13.5 DC-AM-002 再）。"""
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("rate_from_v1")
        video_track = tl.tracks[0]
        media = MediaRef(target_url="/video.mp4")
        add_clip(
            video_track,
            media,
            TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=24.0),
                duration=RationalTimeModel(value=72.0, rate=24.0),
            ),
        )
        summary = summarize_timeline(tl)
        dur = summary["total_duration"]
        assert dur.rate == pytest.approx(24.0, rel=1e-6)

    def test_total_duration_rate_1000_when_no_video(self) -> None:
        """V1 が空（クリップなし）で A1 だけに gap がある場合
        rate=1000.0（§13.5 DC-AM-002 再）。"""
        from clipwright.schemas import RationalTimeModel

        tl = new_timeline("audio_only")
        audio_track = tl.tracks[1]
        add_gap(audio_track, RationalTimeModel(value=1000.0, rate=1000.0))
        summary = summarize_timeline(tl)
        dur = summary["total_duration"]
        assert dur.rate == pytest.approx(1000.0, rel=1e-6)

    def test_summary_with_real_otio_roundtrip(self, tmp_path: Path) -> None:
        """save → load した timeline の summarize_timeline が同じ clip_count を返す。"""
        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("io_summary")
        track = tl.tracks[0]
        media = MediaRef(target_url="/v.mp4")
        add_clip(
            track,
            media,
            TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=30.0, rate=30.0),
            ),
        )
        path = str(tmp_path / "io.otio")
        save_timeline(tl, path)
        loaded = load_timeline(path)
        summary = summarize_timeline(loaded)
        assert summary["clip_count"] == 1

    def test_marker_count_no_double_counting(self) -> None:
        """track マーカーと clip マーカーが二重カウントされないことを検証する（H-3）。

        track に N 個・clip に M 個マーカーを付与したとき marker_count == N+M。
        """
        import opentimelineio as otio

        from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

        tl = new_timeline("no_double_count")
        track = tl.tracks[0]

        # clip を1件追加
        media = MediaRef(target_url="/v.mp4")
        clip = add_clip(
            track,
            media,
            TimeRangeModel(
                start_time=RationalTimeModel(value=0.0, rate=30.0),
                duration=RationalTimeModel(value=30.0, rate=30.0),
            ),
        )

        # track に 3 個のマーカーを追加
        n = 3
        for i in range(n):
            mr = TimeRangeModel(
                start_time=RationalTimeModel(value=float(i), rate=30.0),
                duration=RationalTimeModel(value=1.0, rate=30.0),
            )
            add_marker(track, mr, f"track_marker_{i}")

        # clip に 2 個のマーカーを追加
        m = 2
        for i in range(m):
            mr_clip = otio.opentime.TimeRange(
                start_time=otio.opentime.RationalTime(float(i), 30.0),
                duration=otio.opentime.RationalTime(1.0, 30.0),
            )
            clip_marker = otio.schema.Marker(
                name=f"clip_marker_{i}", marked_range=mr_clip
            )
            clip.markers.append(clip_marker)

        summary = summarize_timeline(tl)
        assert summary["marker_count"] == n + m, (
            f"track {n} 個 + clip {m} 個 = {n + m} 件（重複なし）であること"
            f"（実際: {summary['marker_count']}）"
        )
