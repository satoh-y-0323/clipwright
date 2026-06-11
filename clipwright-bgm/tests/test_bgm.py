"""test_bgm.py — add_bgm オーケストレーション層の契約面テスト（Red フェーズ）。

モック方針:
  - clipwright_bgm.bgm.inspect_media を monkeypatch して MediaInfo を供給。
    ffprobe を subprocess で直呼びしないことを間接的に検証（ADR-B2-r2）。
  - add_bgm は ffmpeg を呼ばない（OTIO 操作のみ・ADR-B1）。

検証観点:
  5. 正常系: A2 Audio トラックが追加されBGMクリップが配置される。
     source_range = BGM メディア全長（0〜bgm_duration）固定（DC-AS-003・ADR-B2-r2）。
     出力 timeline 新規生成・入力 timeline 不変（非破壊・M5）。
  6. BGM クリップ metadata["clipwright"] に writer BgmDirective 経由で注記が書かれる（ADR-B3/B9-r2）。
  7. 再呼び出し検出（DC-AS-002/AM-005・ADR-B2-r3）:
     kind=='bgm' クリップが既存 → INVALID_INPUT。
     A1 本編音声トラックのみでは弾かれない（正常系を壊さない）。
  8. BGM 尺取得は inspect_media をモックして使う（bgm.py が ffprobe を subprocess 直呼びしない）。
     inspect_media 失敗（ClipwrightError）→ add_bgm が ToolResult エラーに整形（絶対パス非露出）。
  9. BGM 入力拡張子ホワイトリスト（DC-AM-007・ADR-B2-r3）:
     許可外拡張子 → INVALID_INPUT。
     許可リスト = {mp3,wav,m4a,aac,flac,ogg,opus,mp4,mkv,mov,webm}。
  10. bgm が timeline と同一 dir 配下でない → PATH_NOT_ALLOWED。
      bgm 不在 → FILE_NOT_FOUND・basename のみ。
  11. output == 入力 timeline / 既存 output 衝突 → 適切なエラー（非破壊）。
  12. 返り値エンベロープ: ok=True・summary に BGM 配置要点・artifacts に出力 timeline。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.otio_utils import load_timeline, save_timeline

from clipwright_bgm.bgm import add_bgm
from clipwright_bgm.schemas import BgmDirective, BgmOptions
from tests.conftest import BGM_DURATION_SEC, BGM_RATE

# ===========================================================================
# ヘルパー
# ===========================================================================


def _make_simple_timeline() -> otio.schema.Timeline:
    """V1(Video) + A1(Audio) の 2 トラック構成 Timeline を返す。"""
    tl = otio.schema.Timeline(name="test_timeline")
    v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    a1 = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
    tl.tracks.append(v1)
    tl.tracks.append(a1)
    return tl


def _save_timeline_to_file(tl: otio.schema.Timeline, path: Path) -> None:
    """Timeline をファイルに保存するヘルパー。"""
    save_timeline(tl, str(path))


def _get_bgm_clips(tl: otio.schema.Timeline) -> list[otio.schema.Clip]:
    """timeline から kind=='bgm' の Clip を収集して返す。"""
    bgm_clips = []
    for track in tl.tracks:
        if track.kind == otio.schema.TrackKind.Audio:
            for item in track:
                if isinstance(item, otio.schema.Clip):
                    meta = item.metadata.get("clipwright", {})
                    if meta.get("kind") == "bgm":
                        bgm_clips.append(item)
    return bgm_clips


# ===========================================================================
# テスト観点 5: 正常系 - A2 トラック追加・BGM クリップ配置・非破壊
# ===========================================================================


class TestAddBgmNormalCase:
    """add_bgm 正常系: A2 トラック追加・BGM クリップ配置・入力 timeline 不変。"""

    def test_a2_audio_track_is_added(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """add_bgm 後に A2 Audio トラックが timeline に追加されていること。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is True
        out_tl = load_timeline(str(output_path))
        audio_tracks = [
            t for t in out_tl.tracks if t.kind == otio.schema.TrackKind.Audio
        ]
        assert len(audio_tracks) >= 2, "A2 を含む少なくとも 2 本の Audio トラックが必要"
        track_names = [t.name for t in audio_tracks]
        assert "A2" in track_names, "A2 Audio トラックが存在すること"

    def test_bgm_clip_is_placed_in_a2_track(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """A2 トラックに BGM クリップが 1 本配置されていること。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is True
        out_tl = load_timeline(str(output_path))
        bgm_clips = _get_bgm_clips(out_tl)
        assert len(bgm_clips) == 1, "BGM クリップが A2 トラックに 1 本あること"

    def test_source_range_equals_bgm_full_duration(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """BGM クリップの source_range が BGM メディア全長（0〜bgm_duration）であること（DC-AS-003・ADR-B2-r2）。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        out_tl = load_timeline(str(output_path))
        bgm_clips = _get_bgm_clips(out_tl)
        assert len(bgm_clips) == 1
        clip = bgm_clips[0]
        assert clip.source_range is not None
        start_sec = otio.opentime.to_seconds(clip.source_range.start_time)
        duration_sec = otio.opentime.to_seconds(clip.source_range.duration)
        assert start_sec == pytest.approx(0.0), "source_range の開始は 0 秒であること"
        assert duration_sec == pytest.approx(BGM_DURATION_SEC), (
            f"source_range の尺は BGM 全長 {BGM_DURATION_SEC}s であること"
        )

    def test_input_timeline_is_unchanged(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """add_bgm は入力 timeline ファイルを書き換えない（非破壊・M5）。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)
        original_content = timeline_path.read_bytes()

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert timeline_path.read_bytes() == original_content, (
            "入力 timeline ファイルのバイト列が変化している（非破壊違反）"
        )

    def test_output_timeline_is_a_new_file(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """add_bgm は入力 timeline とは別の新規出力ファイルを生成すること。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert output_path.exists(), "出力 timeline ファイルが生成されること"
        assert timeline_path != output_path, "入力と出力は別ファイルであること"


# ===========================================================================
# テスト観点 6: BGM クリップ metadata に writer BgmDirective 経由で注記が書かれること
# ===========================================================================


class TestAddBgmMetadata:
    """BGM クリップ metadata["clipwright"] に BgmDirective 形式の注記が書かれること（ADR-B3/B9-r2）。"""

    def test_clipwright_metadata_exists_on_bgm_clip(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """BGM クリップの metadata に "clipwright" キーが存在すること。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        out_tl = load_timeline(str(output_path))
        bgm_clips = _get_bgm_clips(out_tl)
        assert len(bgm_clips) == 1
        meta = bgm_clips[0].metadata.get("clipwright")
        assert meta is not None, 'BGM クリップに metadata["clipwright"] が存在すること'

    def test_bgm_metadata_tool_field(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """BGM クリップ metadata の tool フィールドが "clipwright-bgm" であること。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        out_tl = load_timeline(str(output_path))
        bgm_clips = _get_bgm_clips(out_tl)
        meta = bgm_clips[0].metadata["clipwright"]
        assert meta["tool"] == "clipwright-bgm"

    def test_bgm_metadata_kind_is_bgm(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """BGM クリップ metadata の kind フィールドが "bgm" であること。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        out_tl = load_timeline(str(output_path))
        bgm_clips = _get_bgm_clips(out_tl)
        meta = bgm_clips[0].metadata["clipwright"]
        assert meta["kind"] == "bgm"

    def test_bgm_metadata_volume_db_matches_options(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """BGM クリップ metadata の volume_db が options の値と一致すること。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-12.0),
            )

        out_tl = load_timeline(str(output_path))
        bgm_clips = _get_bgm_clips(out_tl)
        meta = bgm_clips[0].metadata["clipwright"]
        assert meta["volume_db"] == pytest.approx(-12.0)

    def test_bgm_metadata_fade_fields_match_options(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """BGM クリップ metadata の fade_in/out_sec が options の値と一致すること。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0, fade_in_sec=1.5, fade_out_sec=2.0),
            )

        out_tl = load_timeline(str(output_path))
        bgm_clips = _get_bgm_clips(out_tl)
        meta = bgm_clips[0].metadata["clipwright"]
        assert meta["fade_in_sec"] == pytest.approx(1.5)
        assert meta["fade_out_sec"] == pytest.approx(2.0)

    def test_bgm_metadata_ducking_matches_options(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """BGM クリップ metadata の ducking フィールドが options の値と一致すること。"""
        from clipwright_bgm.schemas import DuckingOptions

        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)
        opts = BgmOptions(
            volume_db=-6.0,
            ducking=DuckingOptions(enabled=True, threshold=0.08, ratio=6.0),
        )

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=opts,
            )

        out_tl = load_timeline(str(output_path))
        bgm_clips = _get_bgm_clips(out_tl)
        meta = bgm_clips[0].metadata["clipwright"]
        assert meta["ducking"]["enabled"] is True
        assert meta["ducking"]["threshold"] == pytest.approx(0.08)
        assert meta["ducking"]["ratio"] == pytest.approx(6.0)

    def test_bgm_metadata_is_valid_bgm_directive(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """BGM クリップ metadata["clipwright"] が BgmDirective として再構築できること（DC-AS-001）。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        out_tl = load_timeline(str(output_path))
        bgm_clips = _get_bgm_clips(out_tl)
        meta = bgm_clips[0].metadata["clipwright"]
        # BgmDirective として再構築できることを確認
        directive = BgmDirective(**meta)
        assert directive.kind == "bgm"
        assert directive.tool == "clipwright-bgm"


# ===========================================================================
# テスト観点 7: 再呼び出し検出（kind=='bgm' クリップ存在 → INVALID_INPUT）
# ===========================================================================


class TestAddBgmDuplicateDetection:
    """再呼び出し検出: kind=='bgm' クリップが既存 → INVALID_INPUT（DC-AS-002/AM-005・ADR-B2-r3）。"""

    def _add_bgm_clip_to_timeline(
        self, tl: otio.schema.Timeline, bgm_path: Path
    ) -> None:
        """timeline に手動で kind=='bgm' クリップを追加するヘルパー（add_bgm 既呼び出し相当）。"""
        a2 = otio.schema.Track(name="A2", kind=otio.schema.TrackKind.Audio)
        ref = otio.schema.ExternalReference(target_url=str(bgm_path))
        source_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, BGM_RATE),
            duration=otio.opentime.RationalTime(BGM_DURATION_SEC * BGM_RATE, BGM_RATE),
        )
        bgm_clip = otio.schema.Clip(
            name=bgm_path.name,
            media_reference=ref,
            source_range=source_range,
            metadata={"clipwright": {"kind": "bgm", "tool": "clipwright-bgm"}},
        )
        a2.append(bgm_clip)
        tl.tracks.append(a2)

    def test_existing_bgm_clip_raises_invalid_input(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """既に kind=='bgm' クリップが存在する timeline → INVALID_INPUT（ADR-B2-r3）。"""
        tl = _make_simple_timeline()
        self._add_bgm_clip_to_timeline(tl, bgm_audio_file)
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_a1_audio_track_only_does_not_trigger_duplicate_error(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """A1 のみ（kind=='bgm' クリップなし）の timeline は再呼び出しエラーにならないこと（正常系を壊さない・ADR-B4-r2）。"""
        # new_timeline が常に A1 を持つため、A1 だけで弾かれてはいけない
        tl = _make_simple_timeline()  # V1 + A1（BGM クリップなし）
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is True, (
            "A1 のみの timeline は BGM なしと判定し正常に処理されること"
        )

    def test_duplicate_error_message_does_not_contain_clip_name(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """再呼び出しエラーの message/hint に既存クリップ名が含まれないこと（SR L-2・固定文言化）。"""
        tl = _make_simple_timeline()
        # name を特徴的な文字列にしてエラーメッセージへの混入を検出できるようにする
        a2 = otio.schema.Track(name="A2", kind=otio.schema.TrackKind.Audio)
        ref = otio.schema.ExternalReference(target_url=str(bgm_audio_file))
        source_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, BGM_RATE),
            duration=otio.opentime.RationalTime(BGM_DURATION_SEC * BGM_RATE, BGM_RATE),
        )
        bgm_clip = otio.schema.Clip(
            name="EXISTING_CLIP_SENTINEL_NAME",  # エラーメッセージへの混入を確認するための値
            media_reference=ref,
            source_range=source_range,
            metadata={"clipwright": {"kind": "bgm", "tool": "clipwright-bgm"}},
        )
        a2.append(bgm_clip)
        tl.tracks.append(a2)
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"
        error_message = result["error"]["message"]
        error_hint = result["error"]["hint"]
        # 既存クリップ名が message/hint に含まれないこと（SR L-2・固定文言化）
        assert "EXISTING_CLIP_SENTINEL_NAME" not in error_message, (
            "再呼び出しエラーの message に既存クリップ名が混入している（SR L-2）"
        )
        assert "EXISTING_CLIP_SENTINEL_NAME" not in error_hint, (
            "再呼び出しエラーの hint に既存クリップ名が混入している（SR L-2）"
        )

    def test_duplicate_detection_is_based_on_kind_not_track_name(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """再呼び出し検出はトラック名 'A2' ではなく kind=='bgm' クリップの存在で判定すること（ADR-B2-r3）。

        トラック名が 'A2' 以外でも kind=='bgm' クリップがあれば INVALID_INPUT になること。
        """
        tl = _make_simple_timeline()
        # トラック名を "BGM_CUSTOM" にして kind=='bgm' クリップを追加
        bgm_track = otio.schema.Track(
            name="BGM_CUSTOM", kind=otio.schema.TrackKind.Audio
        )
        ref = otio.schema.ExternalReference(target_url=str(bgm_audio_file))
        source_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, BGM_RATE),
            duration=otio.opentime.RationalTime(BGM_DURATION_SEC * BGM_RATE, BGM_RATE),
        )
        bgm_clip = otio.schema.Clip(
            name=bgm_audio_file.name,
            media_reference=ref,
            source_range=source_range,
            metadata={"clipwright": {"kind": "bgm", "tool": "clipwright-bgm"}},
        )
        bgm_track.append(bgm_clip)
        tl.tracks.append(bgm_track)
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT", (
            "kind=='bgm' クリップが存在すればトラック名によらず INVALID_INPUT になること"
        )


# ===========================================================================
# テスト観点 8: BGM 尺取得は inspect_media 経由・失敗時はエラーに整形
# ===========================================================================


class TestAddBgmInspectMedia:
    """BGM 尺取得は inspect_media 経由であること・失敗時のエラー整形（ADR-B2-r2）。"""

    def test_inspect_media_is_called_for_bgm_duration(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """add_bgm が BGM 尺取得のために inspect_media を呼び出すこと。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch(
            "clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm
        ) as mock_inspect:
            add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        mock_inspect.assert_called_once()
        call_args = mock_inspect.call_args
        # inspect_media に渡されたパスが BGM ファイルのパスであること
        called_path = call_args[0][0] if call_args[0] else call_args[1].get("media", "")
        assert (
            str(bgm_audio_file.name) in called_path
            or str(bgm_audio_file) in called_path
        )

    def test_inspect_media_failure_returns_error_envelope(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
    ) -> None:
        """inspect_media が ClipwrightError を送出したとき add_bgm が ToolResult エラーを返すこと。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        def _raise_inspect_error(*args: Any, **kwargs: Any) -> None:
            raise ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="ffprobe が PATH 上に見つかりません",
                hint="ffprobe をインストールしてください。",
            )

        with patch(
            "clipwright_bgm.bgm.inspect_media", side_effect=_raise_inspect_error
        ):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is False
        assert result["error"]["code"] in (
            "DEPENDENCY_MISSING",
            "INVALID_INPUT",
            "SUBPROCESS_FAILED",
        )

    def test_inspect_media_failure_does_not_expose_absolute_path(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
    ) -> None:
        """inspect_media 失敗時のエラーメッセージに絶対パスが含まれないこと（CWE-209・ADR-B2-r2）。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        def _raise_inspect_error(*args: Any, **kwargs: Any) -> None:
            raise ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message=f"ffprobe が見つかりません: {bgm_audio_file}",
                hint="ffprobe をインストールしてください。",
            )

        with patch(
            "clipwright_bgm.bgm.inspect_media", side_effect=_raise_inspect_error
        ):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is False
        error_message = result["error"]["message"]
        # 絶対パス（tmp_timeline_dir の絶対パス）が含まれていないこと
        assert str(tmp_timeline_dir) not in error_message, (
            "エラーメッセージに絶対パスが露出している（CWE-209）"
        )


# ===========================================================================
# テスト観点 9: BGM 入力拡張子ホワイトリスト（DC-AM-007・ADR-B2-r3）
# ===========================================================================


class TestAddBgmExtensionWhitelist:
    """BGM 入力拡張子ホワイトリスト検証（DC-AM-007・ADR-B2-r3）。"""

    @pytest.mark.parametrize(
        "ext",
        [
            "mp3",
            "wav",
            "m4a",
            "aac",
            "flac",
            "ogg",
            "opus",
            "mp4",
            "mkv",
            "mov",
            "webm",
        ],
    )
    def test_allowed_extension_accepted(
        self,
        tmp_timeline_dir: Path,
        media_info_bgm: Any,
        ext: str,
    ) -> None:
        """許可拡張子 .{ext} の BGM ファイルは受理されること。"""
        bgm_file = tmp_timeline_dir / f"bgm.{ext}"
        bgm_file.write_bytes(b"dummy bgm")
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / f"output_{ext}.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is True, f".{ext} は許可拡張子であること"

    @pytest.mark.parametrize(
        "ext",
        ["txt", "py", "mp3.bak", "avi", "wmv", "exe", "sh"],
    )
    def test_disallowed_extension_returns_invalid_input(
        self,
        tmp_timeline_dir: Path,
        media_info_bgm: Any,
        ext: str,
    ) -> None:
        """許可外拡張子の BGM ファイルは INVALID_INPUT になること。"""
        bgm_file = tmp_timeline_dir / f"bgm.{ext}"
        bgm_file.write_bytes(b"not bgm")
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / f"output_{ext}.otio"
        _save_timeline_to_file(tl, timeline_path)

        result = add_bgm(
            timeline=str(timeline_path),
            bgm=str(bgm_file),
            output=str(output_path),
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"


# ===========================================================================
# テスト観点 10: 境界検証・FILE_NOT_FOUND
# ===========================================================================


class TestAddBgmPathValidation:
    """bgm パス境界検証・ファイル不在検証（ADR-B8・ADR-B10）。"""

    def test_bgm_outside_timeline_dir_returns_path_not_allowed(
        self,
        tmp_timeline_dir: Path,
        media_info_bgm: Any,
        tmp_path: Path,
    ) -> None:
        """bgm が timeline と同一 dir 配下でないとき PATH_NOT_ALLOWED を返すこと（ADR-B8）。"""
        # tmp_path は tmp_timeline_dir の親の tmp_path（別 dir）
        outside_bgm = tmp_path / "outside_bgm.mp3"
        outside_bgm.write_bytes(b"outside bgm")
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        result = add_bgm(
            timeline=str(timeline_path),
            bgm=str(outside_bgm),
            output=str(output_path),
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "PATH_NOT_ALLOWED"

    def test_bgm_file_not_found_returns_file_not_found(
        self,
        tmp_timeline_dir: Path,
        media_info_bgm: Any,
    ) -> None:
        """bgm ファイルが存在しないとき FILE_NOT_FOUND を返すこと（ADR-B10）。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)
        nonexistent_bgm = tmp_timeline_dir / "nonexistent_bgm.mp3"

        result = add_bgm(
            timeline=str(timeline_path),
            bgm=str(nonexistent_bgm),
            output=str(output_path),
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "FILE_NOT_FOUND"

    def test_file_not_found_error_message_contains_basename_only(
        self,
        tmp_timeline_dir: Path,
    ) -> None:
        """FILE_NOT_FOUND のメッセージに絶対パスが含まれないこと（basename のみ・ADR-B10/CWE-209）。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)
        nonexistent_bgm = tmp_timeline_dir / "missing_bgm.mp3"

        result = add_bgm(
            timeline=str(timeline_path),
            bgm=str(nonexistent_bgm),
            output=str(output_path),
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False
        error_message = result["error"]["message"]
        assert str(tmp_timeline_dir) not in error_message, (
            "エラーメッセージに絶対パスが含まれている（CWE-209）"
        )
        assert "missing_bgm.mp3" in error_message, (
            "エラーメッセージに basename が含まれていること"
        )

    def test_timeline_file_not_found_returns_error(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
    ) -> None:
        """入力 timeline ファイルが存在しないとき エラーを返すこと。"""
        nonexistent_timeline = tmp_timeline_dir / "nonexistent_timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"

        result = add_bgm(
            timeline=str(nonexistent_timeline),
            bgm=str(bgm_audio_file),
            output=str(output_path),
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False


# ===========================================================================
# テスト観点 10b: output パス境界検証（timeline ディレクトリ外）
# ===========================================================================


class TestAddBgmOutputPathBoundary:
    """output パスの境界検証: timeline ディレクトリ外への書き出しを禁止（SR L-3）。"""

    def test_output_outside_timeline_dir_returns_path_not_allowed(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
        tmp_path: Path,
    ) -> None:
        """output が timeline ディレクトリ外を指すとき PATH_NOT_ALLOWED を返すこと（SR L-3）。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        _save_timeline_to_file(tl, timeline_path)
        # tmp_path は tmp_timeline_dir の外（親ディレクトリ直下）
        outside_output = tmp_path / "outside_output.otio"

        result = add_bgm(
            timeline=str(timeline_path),
            bgm=str(bgm_audio_file),
            output=str(outside_output),
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "PATH_NOT_ALLOWED", (
            "output が timeline ディレクトリ外のとき PATH_NOT_ALLOWED が返ること（SR L-3）"
        )


# ===========================================================================
# テスト観点 10c: inspect_media が duration=None を返すケース
# ===========================================================================


class TestAddBgmDurationNone:
    """inspect_media が duration=None の MediaInfo を返すとき INVALID_INPUT になること（CR M-2）。"""

    def test_duration_none_returns_invalid_input(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
    ) -> None:
        """inspect_media が duration=None → INVALID_INPUT（AttributeError 非漏洩・CR M-2）。"""
        from clipwright.schemas import MediaInfo, StreamInfo

        media_info_no_duration = MediaInfo(
            path=str(bgm_audio_file),
            container="mp4",
            duration=None,  # 音声ストリームなし等で duration が取得できない場合
            streams=[
                StreamInfo(
                    index=0,
                    codec_type="video",
                    codec_name="h264",
                )
            ],
            bit_rate=1_000_000,
        )
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch(
            "clipwright_bgm.bgm.inspect_media", return_value=media_info_no_duration
        ):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT", (
            "duration=None のとき INVALID_INPUT が返ること（CR M-2・AttributeError 非漏洩）"
        )
        # AttributeError が漏洩していないこと（ok=False エンベロープに収まっていること）
        assert "error" in result


# ===========================================================================
# テスト観点 10d: Path.resolve が OSError を送出するケース（フォールバック確認）
# ===========================================================================


class TestAddBgmOsErrorFallback:
    """Path.resolve が OSError を送出するとき absolute() でフォールバックし
    PATH_NOT_ALLOWED が正しく発動することを確認する（CR M-3）。"""

    def test_check_bgm_within_timeline_dir_oserror_fallback_path_not_allowed(
        self,
        tmp_timeline_dir: Path,
        media_info_bgm: Any,
        tmp_path: Path,
    ) -> None:
        """_check_bgm_within_timeline_dir の OSError フォールバック時に
        bgm が境界外ならば PATH_NOT_ALLOWED を返すこと（CR M-3）。"""
        # timeline ディレクトリ外の BGM ファイルを用意する
        outside_bgm = tmp_path / "outside_bgm.mp3"
        outside_bgm.write_bytes(b"outside bgm")
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        # Path.resolve を monkeypatch して OSError を発生させる

        def mock_resolve(self: Path, strict: bool = False) -> Path:  # type: ignore[override]
            raise OSError("mock resolve failure")

        with patch.object(Path, "resolve", mock_resolve):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(outside_bgm),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == "PATH_NOT_ALLOWED", (
            "OSError フォールバック時も境界外 bgm は PATH_NOT_ALLOWED になること（CR M-3）"
        )


# ===========================================================================
# テスト観点 11: output == 入力 timeline / 既存 output 衝突
# ===========================================================================


class TestAddBgmOutputCollision:
    """output == 入力 timeline / 既存 output 衝突でエラーになること（非破壊・ADR-B10）。"""

    def test_output_same_as_input_timeline_returns_error(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """output == 入力 timeline パスのとき INVALID_INPUT を返すこと（上書き禁止・M5）。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        _save_timeline_to_file(tl, timeline_path)

        result = add_bgm(
            timeline=str(timeline_path),
            bgm=str(bgm_audio_file),
            output=str(timeline_path),  # output == input
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_output_already_exists_returns_error(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """output に既存ファイルが存在するとき INVALID_INPUT を返すこと（上書き禁止）。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)
        # output に先にファイルを作成しておく（衝突状態）
        output_path.write_bytes(b"existing output content")

        result = add_bgm(
            timeline=str(timeline_path),
            bgm=str(bgm_audio_file),
            output=str(output_path),
            options=BgmOptions(volume_db=-6.0),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"


# ===========================================================================
# テスト観点 12: 返り値エンベロープ
# ===========================================================================


class TestAddBgmResultEnvelope:
    """add_bgm 返り値エンベロープの契約確認（ok・summary・artifacts）。"""

    def test_result_ok_is_true(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """正常系で ok=True が返ること。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["ok"] is True

    def test_result_summary_is_nonempty(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """正常系で summary が空でないこと（AI が次の一手を判断できる要点を含む）。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        assert result["summary"]
        assert len(result["summary"]) > 0

    def test_result_artifacts_contains_output_timeline(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """正常系で artifacts に出力 timeline のパスが含まれること。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        artifacts = result.get("artifacts", [])
        assert len(artifacts) >= 1, "artifacts に少なくとも 1 件のエントリがあること"
        artifact_paths = [
            a["path"] if isinstance(a, dict) else a.path for a in artifacts
        ]
        assert any(
            str(output_path) in p or p.endswith("output.otio") for p in artifact_paths
        ), "artifacts に出力 timeline のパスが含まれること"

    def test_result_has_required_envelope_keys(
        self,
        tmp_timeline_dir: Path,
        bgm_audio_file: Path,
        media_info_bgm: Any,
    ) -> None:
        """正常系で ok/summary/data/artifacts/warnings のキーが存在すること（§6.3）。"""
        tl = _make_simple_timeline()
        timeline_path = tmp_timeline_dir / "timeline.otio"
        output_path = tmp_timeline_dir / "output.otio"
        _save_timeline_to_file(tl, timeline_path)

        with patch("clipwright_bgm.bgm.inspect_media", return_value=media_info_bgm):
            result = add_bgm(
                timeline=str(timeline_path),
                bgm=str(bgm_audio_file),
                output=str(output_path),
                options=BgmOptions(volume_db=-6.0),
            )

        for key in ("ok", "summary", "data", "artifacts", "warnings"):
            assert key in result, f"エンベロープに {key!r} キーがないこと"
