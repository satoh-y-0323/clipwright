"""test_loudness.py — loudness.py オーケストレーション層のテスト。

モック方針:
  - clipwright_loudness.loudness.inspect_media を patch して MediaInfo を供給。
  - clipwright_loudness.loudness.measure_loudness を patch して ffmpeg を呼ばない。
  - 実 ffmpeg/ffprobe バイナリは一切呼ばない。

検証観点:
  (a) timeline=None: 新規 timeline・V1/A1 全長 keep clip・loudness 注記を
      timeline-level metadata に格納・save
  (b) timeline 指定: 既存ロード+部分更新で既存注記（denoise 等）保持
  (c) .otio 以外→INVALID_INPUT
  (d) media 不在→FILE_NOT_FOUND（basename）
  (e) output==media/timeline→INVALID_INPUT
  (f) output が media と別dir→INVALID_INPUT
  (g) 映像なし→UNSUPPORTED・音声なし→UNSUPPORTED
  (h) timeline 指定で media≠timeline source→INVALID_INPUT
  (h2) 既存 timeline（V1+A1 正常系）は検証を通る
  (i) 複数source/Video2本→不正
  (j) mode=loudnorm/peak それぞれで target・measured が timeline-level 注記に入る
  (k) U-1: loudnorm で measured 取得不能なら loudness 指示を書かず（既存 metadata に
      loudness を追加しない）warning を返す
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ErrorCode
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_loudness.schemas import DetectLoudnessOptions

# ===========================================================================
# ヘルパー
# ===========================================================================

FPS = 30.0
_TEST_BIT_RATE = 8_000_000  # テスト用ビットレート定数（アサーション対象外）

# loudnorm mode での正常測定結果モック
_FAKE_LOUDNORM_MEASURED = {
    "measured": {
        "input_i": -21.75,
        "input_tp": -18.06,
        "input_lra": 0.0,
        "input_thresh": -31.75,
        "target_offset": 0.03,
    },
    "warnings": [],
}

# peak mode での正常測定結果モック
_FAKE_PEAK_MEASURED = {
    "measured": {
        "max_volume_db": -18.1,
    },
    "warnings": [],
}

# measured=None（測定不能・U-1）のモック
_FAKE_MEASURED_NONE = {
    "measured": None,
    "warnings": [
        "ラウドネス測定値を取得できませんでした。loudness 指示は書き込みません。"
    ],
}


def _make_media_info(
    path: str,
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
    has_video: bool = True,
    has_audio: bool = True,
) -> MediaInfo:
    """テスト用 MediaInfo を構築するヘルパー。"""
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(StreamInfo(index=0, codec_type="video", codec_name="h264"))
    if has_audio:
        streams.append(
            StreamInfo(index=len(streams), codec_type="audio", codec_name="aac")
        )
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=duration_sec * rate, rate=rate),
        streams=streams,
        bit_rate=_TEST_BIT_RATE,
    )


def _make_otio_timeline(
    media_path: Path,
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
    num_video_tracks: int = 1,
    num_audio_tracks: int = 1,
    sources: list[str] | None = None,
) -> otio.schema.Timeline:
    """テスト用 OTIO Timeline を構築するヘルパー。"""
    tl = otio.schema.Timeline(name="test")

    for i in range(num_video_tracks):
        track = otio.schema.Track(name=f"V{i + 1}", kind=otio.schema.TrackKind.Video)
        tl.tracks.append(track)

    for i in range(num_audio_tracks):
        track = otio.schema.Track(name=f"A{i + 1}", kind=otio.schema.TrackKind.Audio)
        tl.tracks.append(track)

    if num_video_tracks > 0:
        v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)

        if sources is None:
            sources = [str(media_path.resolve())]

        source_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, rate),
            duration=otio.opentime.RationalTime(duration_sec * rate, rate),
        )

        for url in sources:
            ref = otio.schema.ExternalReference(target_url=url)
            clip = otio.schema.Clip(
                name=media_path.name,
                media_reference=ref,
                source_range=source_range,
            )
            v1.append(clip)

    return tl


def _save_timeline_to_file(tl: otio.schema.Timeline, path: Path) -> None:
    """Timeline を実ファイルに保存する。"""
    otio.adapters.write_to_file(tl, str(path))


# ===========================================================================
# (a) timeline=None: 新規 timeline 生成
# ===========================================================================


class TestNewTimeline:
    """timeline=None 時に新規 timeline が生成されること。"""

    def test_new_timeline_ok_result(self, tmp_path: Path) -> None:
        """成功エンベロープが返ること。"""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=None,
            )

        assert result["ok"] is True

    def test_new_timeline_otio_file_created(self, tmp_path: Path) -> None:
        """output に .otio ファイルが生成されること。"""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            detect_loudness(
                str(media), str(output), DetectLoudnessOptions(), timeline=None
            )

        assert output.exists(), "output .otio が生成されていない。"

    def test_new_timeline_v1_has_clip(self, tmp_path: Path) -> None:
        """生成 timeline の V1 に clip が1件以上あること。"""
        from clipwright.otio_utils import load_timeline

        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            detect_loudness(
                str(media), str(output), DetectLoudnessOptions(), timeline=None
            )

        tl = load_timeline(str(output))
        v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) >= 1

    def test_new_timeline_has_loudness_metadata_at_timeline_level(
        self, tmp_path: Path
    ) -> None:
        """生成 timeline の timeline-level metadata に loudness 注記があること（ADR-L4）。"""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            detect_loudness(
                str(media), str(output), DetectLoudnessOptions(), timeline=None
            )

        tl = load_timeline(str(output))
        meta = get_clipwright_metadata(tl)
        assert "loudness" in meta, (
            "timeline.metadata['clipwright']['loudness'] がない（ADR-L4）。"
        )
        loudness = meta["loudness"]
        assert loudness["kind"] == "loudness"
        assert loudness["scope"] == "track"

    def test_new_timeline_artifacts_contains_otio(self, tmp_path: Path) -> None:
        """result の artifacts に role=timeline / format=otio が含まれること。"""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            result = detect_loudness(
                str(media), str(output), DetectLoudnessOptions(), timeline=None
            )

        artifacts = result.get("artifacts", [])
        timeline_arts = [
            a
            for a in artifacts
            if (
                (isinstance(a, dict) and a.get("role") == "timeline")
                or (hasattr(a, "role") and a.role == "timeline")
            )
        ]
        assert len(timeline_arts) >= 1


# ===========================================================================
# (b) timeline 指定: 既存 timeline ロード + 部分更新
# ===========================================================================


class TestExistingTimeline:
    """timeline=path 時に既存タイムラインをロードして更新すること。"""

    def test_existing_timeline_loudness_metadata_updated(self, tmp_path: Path) -> None:
        """既存 timeline に loudness 注記が追記されること。"""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        tl = _make_otio_timeline(media)
        timeline_path = tmp_path / "existing.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is True
        out_tl = load_timeline(str(output))
        meta = get_clipwright_metadata(out_tl)
        assert "loudness" in meta

    def test_existing_timeline_other_metadata_preserved(self, tmp_path: Path) -> None:
        """既存 timeline の loudness 以外の注記（denoise など）が保持されること。"""
        from clipwright.otio_utils import (
            get_clipwright_metadata,
            load_timeline,
            set_clipwright_metadata,
        )

        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        tl = _make_otio_timeline(media)
        # 既存 denoise 注記を書き込む
        set_clipwright_metadata(
            tl,
            {
                "denoise": {
                    "kind": "denoise",
                    "backend": "afftdn",
                    "tool": "clipwright-noise",
                    "version": "0.1.0",
                    "params": {"nr": 12.0, "nf": -50.0, "nt": "w"},
                }
            },
        )
        timeline_path = tmp_path / "existing.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        out_tl = load_timeline(str(output))
        meta = get_clipwright_metadata(out_tl)
        # denoise が保持されること
        assert "denoise" in meta, (
            "既存の denoise 注記が loudness 更新で消えてしまった。"
        )


# ===========================================================================
# (c) .otio 以外の拡張子 → INVALID_INPUT
# ===========================================================================


class TestInvalidExtension:
    """output に .otio 以外を指定した場合 INVALID_INPUT が返ること。"""

    @pytest.mark.parametrize("ext", [".mp4", ".json", ".txt", ".otioz", ""])
    def test_non_otio_extension_returns_invalid_input(
        self, tmp_path: Path, ext: str
    ) -> None:
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / f"out{ext}"

        result = detect_loudness(
            str(media), str(output), DetectLoudnessOptions(), timeline=None
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT


# ===========================================================================
# (c2) output の親ディレクトリが存在しない → INVALID_INPUT
# ===========================================================================


class TestOutputParentDirNotFound:
    """output の親ディレクトリが存在しない場合 INVALID_INPUT が返ること。"""

    def test_output_parent_dir_not_exist_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "nonexistent_dir" / "out.otio"

        result = detect_loudness(
            str(media), str(output), DetectLoudnessOptions(), timeline=None
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT
        assert "出力先ディレクトリ" in result["error"]["message"]


# ===========================================================================
# (d) media 不在 → FILE_NOT_FOUND（basename）
# ===========================================================================


class TestMediaNotFound:
    """media ファイルが存在しない場合 FILE_NOT_FOUND が返ること。"""

    def test_missing_media_returns_file_not_found(self, tmp_path: Path) -> None:
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "nonexistent.mp4"
        output = tmp_path / "out.otio"

        result = detect_loudness(
            str(media), str(output), DetectLoudnessOptions(), timeline=None
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND

    def test_missing_media_message_contains_only_basename(self, tmp_path: Path) -> None:
        """FILE_NOT_FOUND の message にディレクトリパスが含まれないこと（DC-GP-005）。"""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "missing_video.mp4"
        output = tmp_path / "out.otio"
        full_dir = str(tmp_path)

        result = detect_loudness(
            str(media), str(output), DetectLoudnessOptions(), timeline=None
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND
        error_msg = result["error"]["message"]
        assert full_dir not in error_msg, (
            f"DC-GP-005: message に絶対ディレクトリパス '{full_dir}' が含まれている。"
        )
        assert "missing_video.mp4" in error_msg


# ===========================================================================
# (e) output == media / output == timeline → INVALID_INPUT
# ===========================================================================


class TestOutputConflict:
    """output が media または timeline と同一パスの場合 INVALID_INPUT が返ること。"""

    def test_output_equals_media_returns_invalid_input(self, tmp_path: Path) -> None:
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        result = detect_loudness(
            str(media), str(media), DetectLoudnessOptions(), timeline=None
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_output_equals_timeline_returns_invalid_input(self, tmp_path: Path) -> None:
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        timeline_path = tmp_path / "timeline.otio"
        timeline_path.write_bytes(b"dummy")

        result = detect_loudness(
            str(media),
            str(timeline_path),
            DetectLoudnessOptions(),
            timeline=str(timeline_path),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT


# ===========================================================================
# (f) output が media と別 dir → INVALID_INPUT
# ===========================================================================


class TestOutputDifferentDir:
    """output が media と異なるディレクトリの場合 INVALID_INPUT が返ること。"""

    def test_output_in_different_dir_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        from clipwright_loudness.loudness import detect_loudness

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "other"
        out_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")
        output = out_dir / "out.otio"

        result = detect_loudness(
            str(media), str(output), DetectLoudnessOptions(), timeline=None
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_output_different_dir_hint_no_absolute_path(self, tmp_path: Path) -> None:
        """同一dir エラーの hint に絶対パスが含まれないこと（CWE-209）。"""
        from clipwright_loudness.loudness import detect_loudness

        media_dir = tmp_path / "media_src_dir"
        media_dir.mkdir()
        out_dir = tmp_path / "output_dir"
        out_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")
        output = out_dir / "out.otio"

        result = detect_loudness(
            str(media), str(output), DetectLoudnessOptions(), timeline=None
        )

        assert result["ok"] is False
        hint = result["error"].get("hint", "")
        assert str(media_dir) not in hint
        assert str(tmp_path) not in hint


# ===========================================================================
# (g) 映像なし → UNSUPPORTED / 音声なし → UNSUPPORTED
# ===========================================================================


class TestStreamRequirements:
    """映像・音声の両方が必要。"""

    def test_no_video_stream_returns_unsupported(self, tmp_path: Path) -> None:
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media), has_video=False, has_audio=True)

        with patch(
            "clipwright_loudness.loudness.inspect_media", return_value=media_info
        ):
            result = detect_loudness(
                str(media), str(output), DetectLoudnessOptions(), timeline=None
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.UNSUPPORTED_OPERATION

    def test_no_audio_stream_returns_unsupported(self, tmp_path: Path) -> None:
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media), has_video=True, has_audio=False)

        with patch(
            "clipwright_loudness.loudness.inspect_media", return_value=media_info
        ):
            result = detect_loudness(
                str(media), str(output), DetectLoudnessOptions(), timeline=None
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.UNSUPPORTED_OPERATION


# ===========================================================================
# (h) timeline 指定で media ≠ timeline source → INVALID_INPUT
# ===========================================================================


class TestTimelineSourceMismatch:
    """timeline の source が media と異なる場合 INVALID_INPUT が返ること。"""

    def test_different_source_returns_invalid_input(self, tmp_path: Path) -> None:
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        other_media = tmp_path / "other_video.mp4"
        other_media.write_bytes(b"other")
        tl = _make_otio_timeline(other_media)
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with patch(
            "clipwright_loudness.loudness.inspect_media", return_value=media_info
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_mismatch_message_contains_basename_only(self, tmp_path: Path) -> None:
        """不一致エラーの message に絶対パスが混入しないこと（DC-GP-005）。"""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        other_media = tmp_path / "other_video.mp4"
        other_media.write_bytes(b"other")
        tl = _make_otio_timeline(other_media)
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        full_dir = str(tmp_path)
        media_info = _make_media_info(str(media))

        with patch(
            "clipwright_loudness.loudness.inspect_media", return_value=media_info
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is False
        error_msg = result["error"]["message"]
        assert full_dir not in error_msg


# ===========================================================================
# (h2) 既存 timeline（V1+A1 正常系）は検証を通る
# ===========================================================================


class TestTimelineSourceMatchPositive:
    """同一 media の timeline ロードで誤 INVALID_INPUT を出さないこと。"""

    def test_same_source_timeline_passes_validation(self, tmp_path: Path) -> None:
        """同一 media の timeline を渡すと通ること（パス正規化比較）。"""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        tl = _make_otio_timeline(media)
        timeline_path = tmp_path / "silence.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is True, (
            f"同一 media の timeline が誤 INVALID_INPUT になった。"
            f" error={result.get('error')}"
        )

    def test_v1_a1_timeline_passes_validation(self, tmp_path: Path) -> None:
        """V1+A1（Video1本 + Audio1本）の timeline は検証を通ること。"""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        tl = _make_otio_timeline(media, num_video_tracks=1, num_audio_tracks=1)
        timeline_path = tmp_path / "v1a1.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is True, (
            f"V1+A1 の正常 timeline が INVALID_INPUT になった。"
            f" error={result.get('error')}"
        )


# ===========================================================================
# (i) 複数 source / Video2本 → 不正
# ===========================================================================


class TestTimelineValidation:
    """timeline の構造検証。"""

    def test_multiple_sources_returns_error(self, tmp_path: Path) -> None:
        """V1 に複数 source の clip が含まれる場合エラーが返ること。"""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        other = tmp_path / "other.mp4"
        tl = _make_otio_timeline(
            media,
            sources=[str(media.resolve()), str(other.resolve())],
        )
        timeline_path = tmp_path / "multi_src.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with patch(
            "clipwright_loudness.loudness.inspect_media", return_value=media_info
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is False
        assert result["error"]["code"] in (
            ErrorCode.UNSUPPORTED_OPERATION,
            ErrorCode.INVALID_INPUT,
        )

    def test_two_video_tracks_returns_invalid_input(self, tmp_path: Path) -> None:
        """Video トラックが2本の timeline は INVALID_INPUT が返ること。"""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        tl = _make_otio_timeline(media, num_video_tracks=2, num_audio_tracks=0)
        timeline_path = tmp_path / "two_video.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with patch(
            "clipwright_loudness.loudness.inspect_media", return_value=media_info
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT


# ===========================================================================
# (j) mode=loudnorm/peak それぞれで target・measured が timeline-level 注記に入る
# ===========================================================================


class TestLoudnessModeMetadata:
    """mode ごとに target・measured が timeline-level metadata に正しく格納されること。"""

    def test_loudnorm_mode_target_in_metadata(self, tmp_path: Path) -> None:
        """loudnorm mode: target（I/TP/LRA）が timeline metadata に入ること。"""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(mode="loudnorm"),
                timeline=None,
            )

        tl = load_timeline(str(output))
        meta = get_clipwright_metadata(tl)
        loudness = meta["loudness"]
        assert loudness["mode"] == "loudnorm"
        assert "target" in loudness
        target = loudness["target"]
        # I/TP/LRA の既定値が含まれること
        assert "i" in target or "I" in target

    def test_loudnorm_mode_measured_in_metadata(self, tmp_path: Path) -> None:
        """loudnorm mode: measured が timeline metadata に入ること。"""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_LOUDNORM_MEASURED,
            ),
        ):
            detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(mode="loudnorm"),
                timeline=None,
            )

        tl = load_timeline(str(output))
        meta = get_clipwright_metadata(tl)
        loudness = meta["loudness"]
        assert loudness.get("measured") is not None, (
            "loudnorm 正常系: measured が timeline metadata に入っていない。"
        )
        measured = loudness["measured"]
        assert "input_i" in measured

    def test_peak_mode_target_in_metadata(self, tmp_path: Path) -> None:
        """peak mode: target（peak_db）が timeline metadata に入ること。"""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_PEAK_MEASURED,
            ),
        ):
            detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(mode="peak"),
                timeline=None,
            )

        tl = load_timeline(str(output))
        meta = get_clipwright_metadata(tl)
        loudness = meta["loudness"]
        assert loudness["mode"] == "peak"
        assert "target" in loudness
        target = loudness["target"]
        assert "peak_db" in target

    def test_peak_mode_measured_in_metadata(self, tmp_path: Path) -> None:
        """peak mode: measured（max_volume_db）が timeline metadata に入ること。"""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_PEAK_MEASURED,
            ),
        ):
            detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(mode="peak"),
                timeline=None,
            )

        tl = load_timeline(str(output))
        meta = get_clipwright_metadata(tl)
        loudness = meta["loudness"]
        assert loudness.get("measured") is not None
        measured = loudness["measured"]
        assert "max_volume_db" in measured


# ===========================================================================
# (k) U-1: loudnorm で measured 取得不能なら loudness 指示を書かず warning
# ===========================================================================


class TestU1MeasuredNone:
    """U-1: measured=None の場合 loudness 指示を timeline metadata に書かず warning を返す。"""

    def test_loudnorm_measured_none_no_loudness_in_metadata(
        self, tmp_path: Path
    ) -> None:
        """measured=None の場合 timeline metadata に loudness キーが追加されないこと（U-1）。"""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_MEASURED_NONE,
            ),
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(mode="loudnorm"),
                timeline=None,
            )

        # ok=True（detect 自体は成功）
        assert result["ok"] is True, (
            "U-1: measured=None でも detect は成功（ok=True）でなければならない。"
        )

        # timeline ファイル自体は生成されること
        assert output.exists(), (
            "U-1: measured=None でも timeline ファイル自体は生成されるべき。"
        )
        # timeline に loudness キーが追加されていないこと
        tl = load_timeline(str(output))
        meta = get_clipwright_metadata(tl)
        assert "loudness" not in meta, (
            "U-1: measured=None の場合 loudness 指示を timeline metadata に"
            "書いてはならない（DC-AM-003）。"
        )

    def test_loudnorm_measured_none_warning_in_result(self, tmp_path: Path) -> None:
        """measured=None の場合 result の warnings に警告が含まれること（U-1）。"""
        from clipwright_loudness.loudness import detect_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_loudness.loudness.inspect_media", return_value=media_info
            ),
            patch(
                "clipwright_loudness.loudness.measure_loudness",
                return_value=_FAKE_MEASURED_NONE,
            ),
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(mode="loudnorm"),
                timeline=None,
            )

        warnings = result.get("warnings", [])
        assert len(warnings) > 0, (
            "U-1: measured=None 時は result.warnings に警告が必要（DC-AM-003）。"
        )


# ===========================================================================
# SR L-2: _load_and_validate_timeline の境界検証（timeline 親dir 外ソース）
# ===========================================================================


class TestTimelineSourceBoundaryCheck:
    """SR L-2: timeline の target_url がタイムライン親ディレクトリ配下にあること。"""

    def test_source_outside_timeline_dir_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """target_url がタイムライン親ディレクトリ外を指す場合 PATH_NOT_ALLOWED が返ること（SR-r2 L-1）。"""
        import opentimelineio as otio

        from clipwright_loudness.loudness import detect_loudness

        # timeline は subdir に保存、source は別ディレクトリを指す
        subdir = tmp_path / "project"
        subdir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        media = subdir / "video.mp4"
        media.write_bytes(b"dummy")

        outside_media = outside_dir / "other.mp4"
        outside_media.write_bytes(b"dummy")

        # V1 に outside_media を指す clip を持つ timeline を subdir に保存
        tl = otio.schema.Timeline(name="test")
        track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        tl.tracks.append(track)
        ref = otio.schema.ExternalReference(target_url=str(outside_media.resolve()))
        source_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0.0, 30.0),
            duration=otio.opentime.RationalTime(300.0, 30.0),
        )
        clip = otio.schema.Clip(
            name="other.mp4",
            media_reference=ref,
            source_range=source_range,
        )
        track.append(clip)
        timeline_path = subdir / "timeline.otio"
        otio.adapters.write_to_file(tl, str(timeline_path))

        output = subdir / "out.otio"
        media_info = _make_media_info(str(media))

        with patch(
            "clipwright_loudness.loudness.inspect_media", return_value=media_info
        ):
            result = detect_loudness(
                str(media),
                str(output),
                DetectLoudnessOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is False
        # 境界外パス: render.py と同じ PATH_NOT_ALLOWED を期待する（SR-r2 L-1）
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED
