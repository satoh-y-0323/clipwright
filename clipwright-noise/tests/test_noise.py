"""test_noise.py — noise.py オーケストレーション層のテスト。

モック方針:
  - clipwright_noise.noise.inspect_media を patch して MediaInfo を供給。
  - clipwright_noise.noise.measure_noise を patch して astats を呼ばない。
  - 実 ffmpeg/ffprobe バイナリは一切呼ばない。

検証観点（v3 設計 §1.1 / DC-AS-002 / B-4 / B-5 / DC-GP-003 / DC-GP-005）:
  (a) timeline=None: 新規 timeline・V1 全長 keep clip（target_url=絶対パス）・denoise 注記・save
  (b) timeline 指定: 既存ロード + 部分更新で既存注記保持
  (c) .otio 以外の拡張子 → INVALID_INPUT
  (d) media 不在 → FILE_NOT_FOUND（basename のみ・DC-GP-005）
  (e) output==media → INVALID_INPUT / output==timeline → INVALID_INPUT
  (f) output が media と別 dir → INVALID_INPUT（DC-AS-002）
  (g) 映像なし → UNSUPPORTED / 音声なし → UNSUPPORTED
  (h) timeline 指定で media ≠ timeline source → INVALID_INPUT
  (h2) silence 由来相当の実 timeline をロードして同一 media を渡すと通る正常系（B-4）
  (i) timeline が複数 source → UNSUPPORTED / Video2 本 → INVALID_INPUT
  (i2) V1+A1 の正常 timeline は通る正常系（B-5）
  (j) backend=deepfilternet → params={} 注記 + warning に「render 適用は未対応」
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ErrorCode
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_noise.schemas import DetectNoiseOptions

# ===========================================================================
# ヘルパー
# ===========================================================================

FPS = 30.0
_FAKE_MEASURE_RESULT = {
    "params": {"nr": 12.0, "nf": -50.0, "nt": "w"},
    "measured_noise_floor_db": -50.0,
    "warnings": [],
}
_FAKE_MEASURE_RESULT_DFN = {
    "params": {},
    "measured_noise_floor_db": -50.0,
    "warnings": [],
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
        bit_rate=8_000_000,
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
    """テスト用 OTIO Timeline を構築するヘルパー。

    sources が指定された場合は複数ソースの clip を V1 に追加する。
    sources=None の場合は media_path.resolve() 1件の clip を追加する。
    """
    tl = otio.schema.Timeline(name="test")

    # Video トラックを num_video_tracks 本追加
    for i in range(num_video_tracks):
        track = otio.schema.Track(name=f"V{i + 1}", kind=otio.schema.TrackKind.Video)
        tl.tracks.append(track)

    # Audio トラックを num_audio_tracks 本追加
    for i in range(num_audio_tracks):
        track = otio.schema.Track(name=f"A{i + 1}", kind=otio.schema.TrackKind.Audio)
        tl.tracks.append(track)

    # V1 に clip を追加
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
        from clipwright_noise.noise import detect_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch(
                "clipwright_noise.noise.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=None,
            )

        assert result["ok"] is True

    def test_new_timeline_otio_file_created(self, tmp_path: Path) -> None:
        """output に .otio ファイルが生成されること。"""
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            detect_noise(str(media), str(output), DetectNoiseOptions(), timeline=None)

        assert output.exists(), "output .otio が生成されていない。"

    def test_new_timeline_v1_has_clip(self, tmp_path: Path) -> None:
        """生成 timeline の V1 に clip が1件以上あること。"""
        from clipwright.otio_utils import load_timeline

        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            detect_noise(str(media), str(output), DetectNoiseOptions(), timeline=None)

        tl = load_timeline(str(output))
        v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) >= 1

    def test_new_timeline_clip_target_url_is_absolute(self, tmp_path: Path) -> None:
        """V1 の clip target_url が媒体ファイルの絶対パスであること（DC-AS-002）。"""
        from clipwright.otio_utils import load_timeline

        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            detect_noise(str(media), str(output), DetectNoiseOptions(), timeline=None)

        tl = load_timeline(str(output))
        v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        abs_media = str(media.resolve())
        for clip in clips:
            assert isinstance(clip.media_reference, otio.schema.ExternalReference)
            # target_url が絶対パスを含むこと（resolve()比較）
            ref_path = Path(clip.media_reference.target_url)
            try:
                resolved = str(ref_path.resolve())
            except OSError:
                resolved = str(ref_path.absolute())
            assert resolved == abs_media

    def test_new_timeline_has_denoise_metadata(self, tmp_path: Path) -> None:
        """生成 timeline の metadata["clipwright"]["denoise"] が設定されること。"""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            detect_noise(str(media), str(output), DetectNoiseOptions(), timeline=None)

        tl = load_timeline(str(output))
        meta = get_clipwright_metadata(tl)
        assert "denoise" in meta, "timeline.metadata['clipwright']['denoise'] がない。"
        denoise = meta["denoise"]
        assert denoise["kind"] == "denoise"
        assert denoise["backend"] == "afftdn"

    def test_new_timeline_artifacts_contains_otio(self, tmp_path: Path) -> None:
        """result の artifacts に role=timeline / format=otio が含まれること。"""
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            result = detect_noise(
                str(media), str(output), DetectNoiseOptions(), timeline=None
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

    def test_existing_timeline_denoise_metadata_updated(self, tmp_path: Path) -> None:
        """既存 timeline に denoise 注記が追記されること。"""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        # silence が生成したような timeline を作成
        tl = _make_otio_timeline(media)
        timeline_path = tmp_path / "existing.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is True
        out_tl = load_timeline(str(output))
        meta = get_clipwright_metadata(out_tl)
        assert "denoise" in meta

    def test_existing_timeline_other_metadata_preserved(self, tmp_path: Path) -> None:
        """既存 timeline の denoise 以外の注記が保持されること。"""
        from clipwright.otio_utils import (
            get_clipwright_metadata,
            load_timeline,
            set_clipwright_metadata,
        )

        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        tl = _make_otio_timeline(media)
        # 既存注記を書き込む（silence が生成した silence_intervals など）
        set_clipwright_metadata(tl, {"silence_intervals": [{"start": 2.0, "end": 4.0}]})
        timeline_path = tmp_path / "existing.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_path),
            )

        out_tl = load_timeline(str(output))
        meta = get_clipwright_metadata(out_tl)
        # silence_intervals が保持されること
        assert "silence_intervals" in meta, (
            "既存の silence_intervals が denoise 更新で消えてしまった。"
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
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / f"out{ext}"

        result = detect_noise(
            str(media), str(output), DetectNoiseOptions(), timeline=None
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT


# ===========================================================================
# (d) media 不在 → FILE_NOT_FOUND（basename のみ・DC-GP-005）
# ===========================================================================


class TestMediaNotFound:
    """media ファイルが存在しない場合 FILE_NOT_FOUND が返ること。"""

    def test_missing_media_returns_file_not_found(self, tmp_path: Path) -> None:
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "nonexistent.mp4"
        output = tmp_path / "out.otio"

        result = detect_noise(
            str(media), str(output), DetectNoiseOptions(), timeline=None
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND

    def test_missing_media_message_contains_only_basename(self, tmp_path: Path) -> None:
        """FILE_NOT_FOUND の message にディレクトリパスが含まれないこと（DC-GP-005）。"""
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "missing_video.mp4"
        output = tmp_path / "out.otio"
        full_dir = str(tmp_path)

        result = detect_noise(
            str(media), str(output), DetectNoiseOptions(), timeline=None
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
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        # output と media が同じパス（ただし拡張子を .otio にするとメディアと別）
        # 別名で同じパスにリダイレクトするシナリオ
        media = tmp_path / "video.otio"
        media.write_bytes(b"dummy")

        result = detect_noise(
            str(media), str(media), DetectNoiseOptions(), timeline=None
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_output_equals_timeline_returns_invalid_input(self, tmp_path: Path) -> None:
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        timeline_path = tmp_path / "timeline.otio"
        timeline_path.write_bytes(b"dummy")

        # output == timeline
        result = detect_noise(
            str(media),
            str(timeline_path),  # output = timeline
            DetectNoiseOptions(),
            timeline=str(timeline_path),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT


# ===========================================================================
# (f) output が media と別 dir → INVALID_INPUT（DC-AS-002）
# ===========================================================================


class TestOutputDifferentDir:
    """output が media と異なるディレクトリの場合 INVALID_INPUT が返ること（DC-AS-002）。"""

    def test_output_in_different_dir_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        from clipwright_noise.noise import detect_noise

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "other"
        out_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")
        output = out_dir / "out.otio"

        result = detect_noise(
            str(media), str(output), DetectNoiseOptions(), timeline=None
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_output_different_dir_hint_does_not_contain_absolute_path(
        self, tmp_path: Path
    ) -> None:
        """同一dir エラーの hint に絶対パスが含まれないこと（SR-M-2・CWE-209）。"""
        from clipwright_noise.noise import detect_noise

        media_dir = tmp_path / "media_src_dir"
        media_dir.mkdir()
        out_dir = tmp_path / "output_dir"
        out_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy")
        output = out_dir / "out.otio"

        result = detect_noise(
            str(media), str(output), DetectNoiseOptions(), timeline=None
        )

        assert result["ok"] is False
        hint = result["error"].get("hint", "")
        # hint に絶対ディレクトリパスが含まれないこと（CWE-209）
        assert str(media_dir) not in hint, (
            f"SR-M-2: hint にメディアディレクトリの絶対パス '{media_dir}' が含まれている。"
        )
        assert str(tmp_path) not in hint, (
            f"SR-M-2: hint に tmp_path '{tmp_path}' が含まれている。"
        )


# ===========================================================================
# (g) 映像なし → UNSUPPORTED / 音声なし → UNSUPPORTED
# ===========================================================================


class TestStreamRequirements:
    """映像・音声の両方が必要（ADR-N8 / DC-AS-003）。"""

    def test_no_video_stream_returns_unsupported(self, tmp_path: Path) -> None:
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media), has_video=False, has_audio=True)

        with patch("clipwright_noise.noise.inspect_media", return_value=media_info):
            result = detect_noise(
                str(media), str(output), DetectNoiseOptions(), timeline=None
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.UNSUPPORTED_OPERATION

    def test_no_audio_stream_returns_unsupported(self, tmp_path: Path) -> None:
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media), has_video=True, has_audio=False)

        with patch("clipwright_noise.noise.inspect_media", return_value=media_info):
            result = detect_noise(
                str(media), str(output), DetectNoiseOptions(), timeline=None
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.UNSUPPORTED_OPERATION


# ===========================================================================
# (h) timeline 指定で media ≠ timeline source → INVALID_INPUT
# ===========================================================================


class TestTimelineSourceMismatch:
    """timeline の source が media と異なる場合 INVALID_INPUT が返ること（DC-AM-003）。"""

    def test_different_source_returns_invalid_input(self, tmp_path: Path) -> None:
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        # 別ファイルを source にした timeline を作成
        other_media = tmp_path / "other_video.mp4"
        other_media.write_bytes(b"other")
        tl = _make_otio_timeline(other_media)
        timeline_path = tmp_path / "timeline.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with patch("clipwright_noise.noise.inspect_media", return_value=media_info):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_mismatch_message_contains_basename_only(self, tmp_path: Path) -> None:
        """不一致エラーの message に絶対パスが混入しないこと（DC-GP-005）。"""
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

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

        with patch("clipwright_noise.noise.inspect_media", return_value=media_info):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is False
        error_msg = result["error"]["message"]
        assert full_dir not in error_msg


# ===========================================================================
# (h2) silence 由来の実 timeline + 同一 media → 正常系（B-4）
# ===========================================================================


class TestTimelineSourceMatchPositive:
    """同一 media の timeline ロードで誤 INVALID_INPUT を出さないこと（B-4）。"""

    def test_same_source_timeline_passes_validation(self, tmp_path: Path) -> None:
        """silence 由来相当の実 OTIO timeline に同一 media を渡すと通ること（B-4）。

        パス正規化比較（Path.resolve()）で誤判定しないことを検証する。
        """
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        # media.resolve() の絶対パスを source にした timeline を作成
        tl = _make_otio_timeline(media)
        timeline_path = tmp_path / "silence.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is True, (
            f"B-4: 同一 media の timeline が誤 INVALID_INPUT になった。"
            f" error={result.get('error')}"
        )


# ===========================================================================
# (i) 複数 source → UNSUPPORTED / Video2 本 → INVALID_INPUT
# ===========================================================================


class TestTimelineValidation:
    """timeline の構造検証（DC-AM-004 / B-5）。"""

    def test_multiple_sources_returns_unsupported(self, tmp_path: Path) -> None:
        """V1 に複数 source の clip が含まれる場合 UNSUPPORTED_OPERATION が返ること。"""
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        # 複数 source（2 つの異なる target_url）
        other = tmp_path / "other.mp4"
        tl = _make_otio_timeline(
            media,
            sources=[str(media.resolve()), str(other.resolve())],
        )
        timeline_path = tmp_path / "multi_src.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with patch("clipwright_noise.noise.inspect_media", return_value=media_info):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is False
        # 複数 source は UNSUPPORTED_OPERATION か INVALID_INPUT
        assert result["error"]["code"] in (
            ErrorCode.UNSUPPORTED_OPERATION,
            ErrorCode.INVALID_INPUT,
        )

    def test_two_video_tracks_returns_invalid_input(self, tmp_path: Path) -> None:
        """Video トラックが2本の timeline は INVALID_INPUT が返ること（B-5）。"""
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        tl = _make_otio_timeline(media, num_video_tracks=2, num_audio_tracks=0)
        timeline_path = tmp_path / "two_video.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with patch("clipwright_noise.noise.inspect_media", return_value=media_info):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT


# ===========================================================================
# (i2) V1+A1 の正常 timeline → 通る正常系（B-5）
# ===========================================================================


class TestV1A1TimelinePositive:
    """V1+A1（Video1本 + Audio1本）の timeline は検証を通ること（B-5）。"""

    def test_v1_a1_timeline_passes_validation(self, tmp_path: Path) -> None:
        """silence 由来の V1+A1 timeline が通ること（Audio トラックは許容）。"""
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        # V1+A1（Video1本 + Audio1本）
        tl = _make_otio_timeline(media, num_video_tracks=1, num_audio_tracks=1)
        timeline_path = tmp_path / "v1a1.otio"
        _save_timeline_to_file(tl, timeline_path)

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is True, (
            f"B-5: V1+A1 の正常 timeline が INVALID_INPUT になった。"
            f" error={result.get('error')}"
        )


# ===========================================================================
# (i3) V1 空 timeline → 全長 clip が追加されて renderable になること（CR-M-1）
# ===========================================================================


class TestEmptyV1Timeline:
    """既存 timeline の V1 が空の場合、全長 keep clip を追加して ok=True になること（CR-M-1）。

    render が resolve_kept_ranges で「Clip が0件」を INVALID_INPUT で弾かないよう、
    _load_and_validate_timeline が _add_full_clip 相当で全長 clip を補完する。
    """

    def test_empty_v1_timeline_adds_clip_and_succeeds(self, tmp_path: Path) -> None:
        """V1 が空の既存 timeline を渡すと全長 clip が追加されて ok=True になること。"""
        from clipwright.otio_utils import load_timeline

        from clipwright_noise.noise import detect_noise

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        # V1 が空の timeline を手動生成（clip なし）
        empty_tl = otio.schema.Timeline(name="empty")
        v1 = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
        empty_tl.tracks.append(v1)
        timeline_path = tmp_path / "empty_v1.otio"
        otio.adapters.write_to_file(empty_tl, str(timeline_path))

        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT,
            ),
        ):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(),
                timeline=str(timeline_path),
            )

        assert result["ok"] is True, (
            f"CR-M-1: V1 空の timeline が INVALID_INPUT になった。"
            f" error={result.get('error')}"
        )

        # 出力 timeline の V1 に clip が追加されていること
        out_tl = load_timeline(str(output))
        out_v1 = next(t for t in out_tl.tracks if t.kind == otio.schema.TrackKind.Video)
        out_clips = [it for it in out_v1 if isinstance(it, otio.schema.Clip)]
        assert len(out_clips) >= 1, (
            "CR-M-1: V1 空の timeline に全長 clip が追加されていない。"
        )


# ===========================================================================
# (j) backend=deepfilternet → params={} 注記 + warning（DC-GP-003）
# ===========================================================================


class TestDeepfilternetBackend:
    """backend=deepfilternet 選択時に params={} 注記と warning が出ること（DC-GP-003）。"""

    def test_deepfilternet_sets_empty_params_in_metadata(self, tmp_path: Path) -> None:
        """denoise 注記の params が {} であること（DC-AM-002）。"""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT_DFN,
            ),
        ):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(backend="deepfilternet"),
                timeline=None,
            )

        assert result["ok"] is True
        tl = load_timeline(str(output))
        meta = get_clipwright_metadata(tl)
        assert "denoise" in meta
        assert meta["denoise"]["params"] == {}, (
            "DC-AM-002: deepfilternet の params は {} でなければならない。"
        )

    def test_deepfilternet_warning_mentions_render_unsupported(
        self, tmp_path: Path
    ) -> None:
        """warnings に「render 適用は未対応」旨が含まれること（DC-GP-003）。"""
        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT_DFN,
            ),
        ):
            result = detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(backend="deepfilternet"),
                timeline=None,
            )

        warnings = result.get("warnings", [])
        assert len(warnings) > 0, (
            "DC-GP-003: deepfilternet 選択時は warnings が空であってはならない。"
        )
        warning_text = " ".join(warnings)
        # 「render 適用は未対応」または類似の文言が含まれること
        assert any(
            kw in warning_text for kw in ["render", "未対応", "afftdn", "将来"]
        ), f"DC-GP-003: warnings に render 未対応の旨が含まれない: {warnings}"

    def test_deepfilternet_backend_stored_in_metadata(self, tmp_path: Path) -> None:
        """denoise 注記の backend が "deepfilternet" であること。"""
        from clipwright.otio_utils import get_clipwright_metadata, load_timeline

        from clipwright_noise.noise import detect_noise
        from clipwright_noise.schemas import DetectNoiseOptions

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        output = tmp_path / "out.otio"
        media_info = _make_media_info(str(media))

        with (
            patch("clipwright_noise.noise.inspect_media", return_value=media_info),
            patch(
                "clipwright_noise.noise.measure_noise",
                return_value=_FAKE_MEASURE_RESULT_DFN,
            ),
        ):
            detect_noise(
                str(media),
                str(output),
                DetectNoiseOptions(backend="deepfilternet"),
                timeline=None,
            )

        tl = load_timeline(str(output))
        meta = get_clipwright_metadata(tl)
        assert meta["denoise"]["backend"] == "deepfilternet"
