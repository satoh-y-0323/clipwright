"""test_detect.py — detect.py オーケストレーションの Red テスト。

対象 API（仮）:
  clipwright_silence.detect.detect_silence(
      media: str,
      output: str,
      options: DetectSilenceOptions,
  ) -> dict

モック方針:
  - clipwright_silence.detect.inspect_media を patch して MediaInfo を供給。
  - clipwright_silence.detect.run を patch して silencedetect stderr を制御。
  - 実 ffmpeg/ffprobe バイナリは一切呼ばない。

検証観点（architecture-report-20260610-141050.md / DC-AS-001〜005 / DC-AM-002/003）:
  ① silencedetect stderr パース（正規表現・行頭一致・`.` 小数点固定・DC-AM-003）
  ② 末尾無音で silence_end 欠落 → total_duration で補完（DC-AM-002）
  ③ KEEP clip 列（V1・source_range rate・target_url・metadata）（DC-AS-001/003）
  ④ 入力検証エラー群（DC-AS-001/002/004）
  ⑤ エンベロープ形式（ok/summary/data/artifacts）
  ⑥ エッジ: 全無音・無音ゼロ
  ⑦ 非破壊・basename のみ（フルパス非露出）
"""

from __future__ import annotations

import math
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_silence.schemas import DetectSilenceOptions

# ===========================================================================
# ヘルパー
# ===========================================================================

FPS = 30.0


def _make_media_info(
    path: str = "/fake/video.mp4",
    *,
    duration_sec: float | None = 10.0,
    rate: float = FPS,
    has_video: bool = True,
    audio_streams: int = 1,
) -> MediaInfo:
    """テスト用 MediaInfo を構築するヘルパー。

    inspect_media のモック戻り値として使用する。
    duration=None の場合は PROBE_FAILED シナリオ用（DC-AS-004）。
    """
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(StreamInfo(index=0, codec_type="video", codec_name="h264"))
    for _i in range(audio_streams):
        streams.append(
            StreamInfo(index=len(streams), codec_type="audio", codec_name="aac")
        )
    duration = (
        RationalTimeModel(value=duration_sec * rate, rate=rate)
        if duration_sec is not None
        else None
    )
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=duration,
        streams=streams,
        bit_rate=8_000_000,
    )


def _make_stderr(
    intervals: list[tuple[float, float]],
    *,
    omit_last_end: bool = False,
) -> str:
    """silence_start / silence_end 行を含む疑似 stderr を生成するヘルパー。

    omit_last_end=True の場合、最後の silence_end を省略して末尾無音を再現
    （DC-AM-002 末尾 silence_end 欠落シナリオ）。
    """
    lines: list[str] = []
    for i, (start, end) in enumerate(intervals):
        lines.append(f"[silencedetect @ 0xabcdef] silence_start: {start:.6f}")
        if not (omit_last_end and i == len(intervals) - 1):
            lines.append(f"[silencedetect @ 0xabcdef] silence_end: {end:.6f} | "
                         f"silence_duration: {end - start:.6f}")
    return "\n".join(lines)


def _fake_run_ok(stderr: str) -> Any:
    """run の成功モックを返すクロージャを作る。"""
    def _impl(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        return CompletedProcess(
            args=cmd, returncode=0, stdout="", stderr=stderr
        )
    return _impl


def _opts(
    silence_threshold_db: float = -30.0,
    min_silence_duration: float = 0.5,
    padding: float = 0.0,
    min_keep_duration: float = 0.0,
) -> DetectSilenceOptions:
    return DetectSilenceOptions(
        silence_threshold_db=silence_threshold_db,
        min_silence_duration=min_silence_duration,
        padding=padding,
        min_keep_duration=min_keep_duration,
    )


# ===========================================================================
# ① silencedetect stderr パース（DC-AM-003）
# ===========================================================================


class TestStderrParsing:
    """silencedetect stderr のパース観点（DC-AM-003）。

    正規表現・行頭一致・小数点固定 / 端数・複数桁・複数区間を網羅する。
    """

    def test_parse_single_interval(self, tmp_path: Path) -> None:
        """silence_start/end 1 区間をパースして KEEP が期待通りに生成される。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(2.0, 5.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        assert result["data"]["silence_count"] == 1

    def test_parse_fractional_seconds(self, tmp_path: Path) -> None:
        """端数秒（例: 2.123456）が正しくパースされる（DC-AM-003）。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(2.123456, 5.654321)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        assert result["data"]["silence_count"] == 1

    def test_parse_multiple_intervals(self, tmp_path: Path) -> None:
        """複数区間が正しくパースされる（DC-AM-003）。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # 3 区間
        intervals = [(1.0, 2.0), (4.5, 5.5), (8.0, 9.0)]
        stderr = _make_stderr(intervals)
        media_info = _make_media_info(path=media, duration_sec=12.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        assert result["data"]["silence_count"] == 3

    def test_parse_large_value_seconds(self, tmp_path: Path) -> None:
        """複数桁秒（例: 120.5）が正しくパースされる（DC-AM-003）。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(120.5, 135.25)])
        media_info = _make_media_info(path=media, duration_sec=300.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        assert result["data"]["silence_count"] == 1

    def test_non_silence_lines_ignored(self, tmp_path: Path) -> None:
        """silence_start/end 以外の行はパース結果に影響しない（DC-AM-003）。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # 無関係な行を混入させる
        stderr = (
            "[ffmpeg version 6.0] noise=-30dB\n"
            "[silencedetect @ 0xabcdef] silence_start: 3.000000\n"
            "frame=100 fps=25 q=0.0 size=N/A time=00:00:10.00 bitrate=N/A\n"
            "[silencedetect @ 0xabcdef] silence_end: 7.000000 | "
            "silence_duration: 4.000000\n"
        )
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        assert result["data"]["silence_count"] == 1


# ===========================================================================
# ② 末尾 silence_end 欠落 → total_duration 補完（DC-AM-002）
# ===========================================================================


class TestTrailingSilenceCompletion:
    """末尾無音で silence_end が欠落した場合に total_duration まで補完される
    （DC-AM-002）。
    """

    def test_missing_trailing_silence_end_is_completed(self, tmp_path: Path) -> None:
        """末尾 silence_end 欠落 → total_duration=10.0 まで補完して KEEP から除外。

        silence_start=7.0 のみで silence_end なし。
        total_duration=10.0 → 無音区間は (7.0, 10.0) と補完される。
        KEEP は (0.0, 7.0) の 1 区間になるはず。
        """
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # silence_end なし
        stderr = "[silencedetect @ 0xabcdef] silence_start: 7.000000\n"
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        # 補完により無音 1 区間として認識される
        assert result["data"]["silence_count"] == 1
        # KEEP は末尾無音を除いた部分のみ（1 clip）
        assert result["data"]["keep_count"] == 1

    def test_only_silence_start_no_end_keeps_before_start(
        self, tmp_path: Path
    ) -> None:
        """silence_start=3.0 のみ（silence_end なし）→ KEEP は (0, 3.0) のみ。

        補完後の無音: (3.0, total_duration=10.0)
        KEEP: (0.0, 3.0) の 1 区間。
        """
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = "[silencedetect @ 0xabcdef] silence_start: 3.000000\n"
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        assert result["data"]["keep_count"] == 1

    def test_mixed_complete_and_incomplete_silence(self, tmp_path: Path) -> None:
        """通常区間と末尾欠落区間が混在しても正しく集計される（DC-AM-002）。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # 区間1は完全, 区間2は silence_end 欠落
        stderr = (
            "[silencedetect @ 0xabcdef] silence_start: 2.000000\n"
            "[silencedetect @ 0xabcdef] silence_end: 4.000000 | "
            "silence_duration: 2.000000\n"
            "[silencedetect @ 0xabcdef] silence_start: 8.000000\n"
        )
        media_info = _make_media_info(path=media, duration_sec=12.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        # 2 区間（補完含む）
        assert result["data"]["silence_count"] == 2


# ===========================================================================
# ③ KEEP clip 列の OTIO 検証（DC-AS-001/003/AD-4）
# ===========================================================================


class TestKeepClipOtio:
    """生成 timeline.otio の V1 トラックに keep-clip 列が正しく積まれること。

    source_range.rate = inspect_media MediaInfo.duration.rate（DC-AS-003）。
    target_url = media の絶対パス（DC-AS-001）。
    metadata["clipwright"] = {tool, version, kind:"keep"}。
    """

    def test_v1_track_has_keep_clips(self, tmp_path: Path) -> None:
        """V1 トラックに clip が1件以上存在すること（AD-4）。"""
        from clipwright.otio_utils import load_timeline
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(3.0, 7.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0, rate=FPS)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        assert v1.kind == otio.schema.TrackKind.Video
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) > 0

    def test_source_range_rate_matches_media_info_duration_rate(
        self, tmp_path: Path
    ) -> None:
        """source_range.rate が MediaInfo.duration.rate と一致すること（DC-AS-003）。"""
        from clipwright.otio_utils import load_timeline
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # rate=25.0 で構築（FPS と異なる値でテスト）
        custom_rate = 25.0
        stderr = _make_stderr([(2.0, 6.0)])
        media_info = _make_media_info(
            path=media, duration_sec=10.0, rate=custom_rate
        )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        for clip in clips:
            assert clip.source_range is not None
            assert clip.source_range.start_time.rate == pytest.approx(custom_rate)
            assert clip.source_range.duration.rate == pytest.approx(custom_rate)

    def test_source_range_value_encodes_seconds_times_rate(
        self, tmp_path: Path
    ) -> None:
        """source_range.start_time.value = start_sec * rate（DC-AS-003）。

        KEEP (0.0, 3.0) の場合、rate=30 なら start_time.value=0, duration.value=90。
        """
        from clipwright.otio_utils import load_timeline
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # 無音 (3,10) → KEEP (0, 3)
        stderr = _make_stderr([(3.0, 10.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0, rate=30.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) == 1
        clip = clips[0]
        # KEEP (0.0, 3.0), rate=30 → value=0.0, duration.value=90.0
        assert clip.source_range.start_time.value == pytest.approx(0.0)
        assert clip.source_range.duration.value == pytest.approx(90.0)

    def test_target_url_is_absolute_path_of_media(self, tmp_path: Path) -> None:
        """clip の target_url が media の絶対パスであること（DC-AS-001）。"""
        from clipwright.otio_utils import load_timeline
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(3.0, 7.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        abs_media = str(Path(media).resolve())
        for clip in clips:
            assert isinstance(clip.media_reference, otio.schema.ExternalReference)
            assert clip.media_reference.target_url == abs_media

    def test_clip_metadata_has_clipwright_key(self, tmp_path: Path) -> None:
        """clip.metadata["clipwright"] に tool/version/kind="keep" が含まれる。"""
        from clipwright.otio_utils import load_timeline
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(3.0, 7.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        for clip in clips:
            cw = clip.metadata.get("clipwright")
            assert cw is not None, "metadata['clipwright'] が設定されていない"
            assert cw.get("tool") == "clipwright-silence"
            assert "version" in cw
            assert cw.get("kind") == "keep"

    def test_clip_count_matches_keep_count(self, tmp_path: Path) -> None:
        """V1 の clip 数が data["keep_count"] と一致する。"""
        from clipwright.otio_utils import load_timeline
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # 無音 2 区間 → KEEP 3 区間
        stderr = _make_stderr([(2.0, 3.0), (6.0, 7.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clip_count = sum(1 for it in v1 if isinstance(it, otio.schema.Clip))
        assert clip_count == result["data"]["keep_count"]


# ===========================================================================
# ④ 入力検証エラー群（DC-AS-001/002/004）
# ===========================================================================


class TestInputValidation:
    """入力検証エラーを検証する（DC-AS-001/002/004）。"""

    def test_audio_stream_missing_returns_unsupported_operation(
        self, tmp_path: Path
    ) -> None:
        """音声ストリーム無し → UNSUPPORTED_OPERATION（DC-AS-002）。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(
            path=media, duration_sec=10.0, has_video=True, audio_streams=0
        )

        with patch(
            "clipwright_silence.detect.inspect_media",
            return_value=media_info,
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.UNSUPPORTED_OPERATION

    def test_video_stream_missing_returns_unsupported_operation(
        self, tmp_path: Path
    ) -> None:
        """映像ストリーム無し → UNSUPPORTED_OPERATION（DC-AS-002）。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(
            path=media, duration_sec=10.0, has_video=False, audio_streams=1
        )

        with patch(
            "clipwright_silence.detect.inspect_media",
            return_value=media_info,
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.UNSUPPORTED_OPERATION

    def test_duration_none_returns_probe_failed(self, tmp_path: Path) -> None:
        """MediaInfo.duration が None → PROBE_FAILED（DC-AS-004）。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(
            path=media, duration_sec=None
        )

        with patch(
            "clipwright_silence.detect.inspect_media",
            return_value=media_info,
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PROBE_FAILED

    def test_ffmpeg_not_found_returns_dependency_missing(
        self, tmp_path: Path
    ) -> None:
        """ffmpeg 不在 → DEPENDENCY_MISSING（AD-1・DC-GP-004）。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        def _fake_resolve(name: str, env_var: str | None = None) -> str:
            raise ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="ffmpeg が見つかりません",
                hint="ffmpeg を PATH に追加してください。",
            )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=_fake_resolve,
            ),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.DEPENDENCY_MISSING

    def test_inspect_media_file_not_found_propagates(
        self, tmp_path: Path
    ) -> None:
        """inspect_media が FILE_NOT_FOUND を送出 → エンベロープに伝播する。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "nonexistent.mp4")
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_silence.detect.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"ファイルが見つかりません: {Path(media).name}",
                hint="有効なメディアファイルのパスを指定してください。",
            ),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND

    def test_symlink_media_propagates_file_not_found(
        self, tmp_path: Path
    ) -> None:
        """symlink media → inspect_media 由来の FILE_NOT_FOUND が伝播する。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "link.mp4")
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_silence.detect.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"シンボリックリンクは受け付けません: {Path(media).name}",
                hint="実ファイルを指定してください。",
            ),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND

    def test_output_in_different_dir_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """output が media と異なるディレクトリ → INVALID_INPUT（DC-AS-001）。"""
        from clipwright_silence.detect import detect_silence

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "other"
        out_dir.mkdir()
        media = str(media_dir / "video.mp4")
        Path(media).touch()
        output = str(out_dir / "out.otio")

        with patch(
            "clipwright_silence.detect.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_output_invalid_extension_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """output 拡張子が .otio 以外 → INVALID_INPUT（AD-5）。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.mp4")  # .otio でない

        with patch(
            "clipwright_silence.detect.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_output_parent_dir_not_found_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """output 親ディレクトリ不在 → INVALID_INPUT or FILE_NOT_FOUND（AD-5）。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "nonexistent_dir" / "out.otio")

        with patch(
            "clipwright_silence.detect.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        assert result["error"]["code"] in (
            ErrorCode.INVALID_INPUT,
            ErrorCode.FILE_NOT_FOUND,
        )


# ===========================================================================
# ⑤ エンベロープ形式
# ===========================================================================


class TestEnvelope:
    """成功エンベロープの形式検証（§6.3 / architecture §返り値エンベロープ）。"""

    def test_success_envelope_has_required_keys(self, tmp_path: Path) -> None:
        """成功時に ok/summary/data/artifacts/warnings が揃う。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(2.0, 5.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        assert "summary" in result
        assert "data" in result
        assert "artifacts" in result
        assert "warnings" in result

    def test_data_has_required_fields(self, tmp_path: Path) -> None:
        """data に silence_count / total_silence_seconds / keep_count /
        total_keep_seconds が含まれる。
        """
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(2.0, 5.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        data = result["data"]
        assert "silence_count" in data
        assert "total_silence_seconds" in data
        assert "keep_count" in data
        assert "total_keep_seconds" in data

    def test_artifacts_contains_timeline_otio(self, tmp_path: Path) -> None:
        """artifacts に role="timeline" / format="otio" の成果物が1件含まれる。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(2.0, 5.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        artifacts = result["artifacts"]
        assert len(artifacts) >= 1
        # artifacts は dict または Artifact モデルのいずれか
        timeline_artifacts = [
            a for a in artifacts
            if (
                (isinstance(a, dict) and a.get("role") == "timeline")
                or (hasattr(a, "role") and a.role == "timeline")
            )
        ]
        assert len(timeline_artifacts) == 1

    def test_data_counts_match_silence_intervals(self, tmp_path: Path) -> None:
        """data の silence_count が実際の無音区間数と一致する。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        intervals = [(1.0, 2.0), (4.0, 5.0), (7.0, 8.0)]
        stderr = _make_stderr(intervals)
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        assert result["data"]["silence_count"] == 3
        assert result["data"]["keep_count"] == 4

    def test_total_silence_seconds_approx(self, tmp_path: Path) -> None:
        """total_silence_seconds が無音区間の合計秒に近い値を持つ。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # 無音: 2-5 (3s), 7-8 (1s) = 計4s
        stderr = _make_stderr([(2.0, 5.0), (7.0, 8.0)])
        media_info = _make_media_info(path=media, duration_sec=12.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        total_silence = result["data"]["total_silence_seconds"]
        assert total_silence == pytest.approx(4.0, abs=0.01)

    def test_summary_is_non_empty_string(self, tmp_path: Path) -> None:
        """summary が空でない文字列であること（§6.3 規約）。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(2.0, 5.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        assert isinstance(result["summary"], str)
        assert len(result["summary"]) > 0


# ===========================================================================
# ⑥ エッジ: 全無音 / 無音ゼロ
# ===========================================================================


class TestEdgeCases:
    """全無音・無音ゼロのエッジケース。"""

    def test_all_silence_returns_ok_with_empty_v1_and_warning(
        self, tmp_path: Path
    ) -> None:
        """全無音 → ok=True + warning + V1 空（AD-3 §2 / 設計方針: エラーにしない）。"""
        from clipwright.otio_utils import load_timeline
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # 全尺が無音
        stderr = _make_stderr([(0.0, 10.0)])
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        # warning に "残す区間がない" 旨が含まれること
        assert len(result["warnings"]) > 0
        # V1 に clip が 0 件
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) == 0
        assert result["data"]["keep_count"] == 0

    def test_no_silence_returns_single_full_clip(self, tmp_path: Path) -> None:
        """無音ゼロ → 全尺1clip（AD-3 §2）。"""
        from clipwright.otio_utils import load_timeline
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        # stderr に silence 行なし
        media_info = _make_media_info(path=media, duration_sec=10.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_run_ok(""),
            ),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        assert result["data"]["silence_count"] == 0
        assert result["data"]["keep_count"] == 1
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) == 1

    def test_no_silence_clip_covers_full_duration(self, tmp_path: Path) -> None:
        """無音ゼロの1clip が全尺をカバーすること。rate=30.0, total=10.0s。"""
        from clipwright.otio_utils import load_timeline
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0, rate=30.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_run_ok(""),
            ),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) == 1
        clip = clips[0]
        # 全尺 10.0s を rate=30.0 で表現 → duration.value = 300.0
        assert clip.source_range.duration.value == pytest.approx(300.0)


# ===========================================================================
# ⑦ 非破壊・フルパス非露出（Sec M-1 / AD-4）
# ===========================================================================


class TestNonDestructiveAndPathSafety:
    """非破壊・フルパス非露出（basename のみ・ffmpeg 生 stderr 非露出）。"""

    def test_media_file_unchanged_after_detect(self, tmp_path: Path) -> None:
        """detect 後も media ファイルの内容が変化しない（非破壊）。"""
        from clipwright_silence.detect import detect_silence

        media_path = tmp_path / "video.mp4"
        media_path.write_bytes(b"dummy content")
        original = media_path.read_bytes()
        output = str(tmp_path / "out.otio")
        stderr = _make_stderr([(2.0, 5.0)])
        media_info = _make_media_info(path=str(media_path), duration_sec=10.0)

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_fake_run_ok(stderr)),
        ):
            detect_silence(str(media_path), output, _opts())

        assert media_path.read_bytes() == original

    def test_error_message_does_not_expose_directory_path(
        self, tmp_path: Path
    ) -> None:
        """エラー message にディレクトリパスが含まれない（basename のみ・Sec M-1）。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        output = str(tmp_path / "out.otio")
        full_dir = str(tmp_path)

        with patch(
            "clipwright_silence.detect.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"ファイルが見つかりません: {Path(media).name}",
                hint="有効なメディアファイルのパスを指定してください。",
            ),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        error_msg = result["error"]["message"]
        assert full_dir not in error_msg

    def test_error_message_does_not_expose_raw_stderr(
        self, tmp_path: Path
    ) -> None:
        """エラー message に ffmpeg 生 stderr が含まれない（Sec M-1）。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        raw_secret = "INTERNAL_SECRET_PATH /home/user/private"
        media_info = _make_media_info(path=media, duration_sec=10.0)

        def _fake_run_fail(
            cmd: list[str], **kwargs: Any
        ) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="コマンドが終了コード 1 で失敗しました",
                hint="コマンドを確認してください。",
            )

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_silence.detect.run",
                side_effect=_fake_run_fail,
            ),
        ):
            result = detect_silence(media, output, _opts())

        assert result["ok"] is False
        error_msg = result["error"]["message"]
        assert raw_secret not in error_msg

    def test_ffmpeg_called_with_list_not_shell_string(
        self, tmp_path: Path
    ) -> None:
        """run に渡すコマンドが list[str] であること（shell=False 相当・規約§6.5）。"""
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        captured_cmds: list[list[str]] = []

        def _capture_run(
            cmd: list[str], **kwargs: Any
        ) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_capture_run),
        ):
            detect_silence(media, output, _opts())

        assert len(captured_cmds) >= 1
        for cmd in captured_cmds:
            assert isinstance(cmd, list), "run に list でないコマンドが渡された"
            for arg in cmd:
                assert isinstance(arg, str), "コマンド引数に非 str が含まれる"

    def test_ffmpeg_timeout_uses_max_60_or_duration_times_2(
        self, tmp_path: Path
    ) -> None:
        """timeout = max(60, ceil(total_duration * 2)) で run が呼ばれる（AD-3 設計）。

        total_duration=10.0s → max(60, ceil(20)) = 60。
        """
        from clipwright_silence.detect import detect_silence

        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")
        media_info = _make_media_info(path=media, duration_sec=10.0)

        captured_timeouts: list[float] = []

        def _capture_run(
            cmd: list[str], *, timeout: float = 60.0, **kwargs: Any
        ) -> CompletedProcess[str]:
            captured_timeouts.append(timeout)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_silence.detect.inspect_media",
                return_value=media_info,
            ),
            patch(
                "clipwright_silence.detect.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_silence.detect.run", side_effect=_capture_run),
        ):
            detect_silence(media, output, _opts())

        assert len(captured_timeouts) >= 1
        # total=10s → max(60, ceil(10*2))=max(60,20)=60
        assert captured_timeouts[0] == pytest.approx(
            max(60, math.ceil(10.0 * 2)), abs=1
        )
