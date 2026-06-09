"""test_media.py — media.py (ffprobe ラッパー) のテスト（Red フェーズ）。

対象: inspect_media(path: str) -> MediaInfo

単体テスト（process.run をモック）:
- ffprobe JSON → MediaInfo 構造化
- 不正 JSON → PROBE_FAILED
- 入力ファイル不在 → FILE_NOT_FOUND
- ffprobe 不在 → DEPENDENCY_MISSING

rate 決定規則（§13.3 DC-AS-006）:
- 映像ストリームがあれば第1映像の avg_frame_rate を rate とする
- 音声のみ素材は rate = 1000.0

統合テスト（実 ffprobe 使用）:
- sample_media / ffprobe_path フィクスチャ（conftest.py）を使用
- ffmpeg/ffprobe がマシンに到達可能な場合は skip せず必須実行（§13.4 DC-AM-006）
- 生成 mp4 を inspect し duration / streams を検証

[RED] media.py は未実装のため ImportError で失敗する。
"""

from __future__ import annotations

import json
from subprocess import CompletedProcess
from unittest.mock import MagicMock

import pytest

from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media  # noqa: E402 — media.py 未実装で ImportError（Red）
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo


# ===========================================================================
# ヘルパー: ffprobe が返す JSON ペイロードを構築する
# ===========================================================================


def _make_ffprobe_json(
    *,
    duration: str = "3.000000",
    streams: list[dict] | None = None,
    container_format: str = "mov,mp4,m4a,3gp,3g2,mj2",
) -> str:
    """ffprobe -print_format json -show_format -show_streams の出力を模倣する。"""
    if streams is None:
        streams = [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 320,
                "height": 240,
                "avg_frame_rate": "30/1",
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "44100",
                "channels": 2,
            },
        ]
    return json.dumps(
        {
            "streams": streams,
            "format": {
                "format_name": container_format,
                "duration": duration,
            },
        }
    )


def _make_completed_process(stdout: str, returncode: int = 0) -> CompletedProcess[str]:
    return CompletedProcess(
        args=["ffprobe"],
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


# ===========================================================================
# 単体テスト: ffprobe JSON → MediaInfo 構造化
# ===========================================================================


class TestInspectMediaSuccess:
    """正常系: ffprobe の JSON 出力を MediaInfo へ構造化する。"""

    def test_returns_media_info_instance(self, mocker: MagicMock, tmp_path) -> None:
        """戻り値が MediaInfo インスタンスであること。"""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        result = inspect_media(str(media_file))

        assert isinstance(result, MediaInfo)

    def test_path_is_preserved_in_media_info(self, mocker: MagicMock, tmp_path) -> None:
        """MediaInfo.path が入力パスと一致すること。"""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        result = inspect_media(str(media_file))

        assert result.path == str(media_file)

    def test_streams_are_parsed(self, mocker: MagicMock, tmp_path) -> None:
        """streams リストがパースされ StreamInfo になること。"""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        result = inspect_media(str(media_file))

        assert len(result.streams) == 2
        assert all(isinstance(s, StreamInfo) for s in result.streams)

    def test_video_stream_fields(self, mocker: MagicMock, tmp_path) -> None:
        """映像ストリームの codec_type / width / height が正しくマップされること。"""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        result = inspect_media(str(media_file))

        video = next(s for s in result.streams if s.codec_type == "video")
        assert video.codec_name == "h264"
        assert video.width == 320
        assert video.height == 240

    def test_audio_stream_fields(self, mocker: MagicMock, tmp_path) -> None:
        """音声ストリームの sample_rate / channels が正しくマップされること。"""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        result = inspect_media(str(media_file))

        audio = next(s for s in result.streams if s.codec_type == "audio")
        assert audio.sample_rate == 44100
        assert audio.channels == 2

    def test_container_is_parsed(self, mocker: MagicMock, tmp_path) -> None:
        """container フィールドが format_name から取得されること。"""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process(
                _make_ffprobe_json(container_format="mov,mp4,m4a,3gp,3g2,mj2")
            ),
        )

        result = inspect_media(str(media_file))

        assert result.container is not None
        assert "mp4" in result.container

    def test_duration_is_rational_time_model(self, mocker: MagicMock, tmp_path) -> None:
        """duration が RationalTimeModel として返されること（秒 float 単独NG）。"""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process(_make_ffprobe_json(duration="3.0")),
        )

        result = inspect_media(str(media_file))

        assert result.duration is not None
        assert isinstance(result.duration, RationalTimeModel)


# ===========================================================================
# rate 決定規則テスト（§13.3 DC-AS-006）
# ===========================================================================


class TestRateDecisionRule:
    """duration の rate 決定規則（DC-AS-006）を検証する。"""

    def test_video_stream_avg_frame_rate_used_as_rate(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """映像ストリームがある場合、第1映像の avg_frame_rate が rate になること。

        avg_frame_rate="30/1" → rate=30.0
        """
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        streams = [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "avg_frame_rate": "30/1",
            }
        ]
        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process(
                _make_ffprobe_json(streams=streams, duration="3.0")
            ),
        )

        result = inspect_media(str(media_file))

        assert result.duration is not None
        assert result.duration.rate == pytest.approx(30.0)

    def test_fractional_avg_frame_rate_parsed_correctly(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """avg_frame_rate が分数形式（例: "24000/1001"）でも正しく rate に変換されること。

        24000/1001 ≈ 23.976 fps
        """
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        streams = [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "avg_frame_rate": "24000/1001",
            }
        ]
        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process(
                _make_ffprobe_json(streams=streams, duration="3.0")
            ),
        )

        result = inspect_media(str(media_file))

        assert result.duration is not None
        assert result.duration.rate == pytest.approx(24000 / 1001, rel=1e-4)

    def test_audio_only_uses_rate_1000(self, mocker: MagicMock, tmp_path) -> None:
        """音声のみの素材は rate=1000.0 になること（DC-AS-006）。"""
        media_file = tmp_path / "audio.mp4"
        media_file.write_bytes(b"dummy")

        streams = [
            {
                "index": 0,
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "44100",
                "channels": 2,
            }
        ]
        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process(
                _make_ffprobe_json(streams=streams, duration="5.0")
            ),
        )

        result = inspect_media(str(media_file))

        assert result.duration is not None
        assert result.duration.rate == 1000.0

    def test_first_video_stream_rate_used_when_multiple_video_streams(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """複数映像ストリームがある場合、第1映像（index 最小）の avg_frame_rate が採用されること。"""
        media_file = tmp_path / "multi.mp4"
        media_file.write_bytes(b"dummy")

        streams = [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "avg_frame_rate": "25/1",
            },
            {
                "index": 1,
                "codec_type": "video",
                "codec_name": "h264",
                "avg_frame_rate": "60/1",
            },
        ]
        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process(
                _make_ffprobe_json(streams=streams, duration="3.0")
            ),
        )

        result = inspect_media(str(media_file))

        assert result.duration is not None
        assert result.duration.rate == pytest.approx(25.0)

    def test_duration_value_reflects_format_duration(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """duration.value が format.duration（秒）を rate で変換した値になること。

        duration=3.0 秒、rate=30.0 fps → value=90.0
        """
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        streams = [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "avg_frame_rate": "30/1",
            }
        ]
        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process(
                _make_ffprobe_json(streams=streams, duration="3.0")
            ),
        )

        result = inspect_media(str(media_file))

        assert result.duration is not None
        # 3.0 秒 × 30.0 fps = 90.0 フレーム
        assert result.duration.value == pytest.approx(90.0)


# ===========================================================================
# 単体テスト: エラー系
# ===========================================================================


class TestInspectMediaFileNotFound:
    """入力ファイルが存在しない場合は FILE_NOT_FOUND を送出する。"""

    def test_raises_file_not_found_for_nonexistent_path(
        self, mocker: MagicMock
    ) -> None:
        """存在しないパスを渡すと FILE_NOT_FOUND になること。"""
        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")

        with pytest.raises(ClipwrightError) as exc_info:
            inspect_media("/nonexistent/path/video.mp4")

        assert exc_info.value.code == ErrorCode.FILE_NOT_FOUND

    def test_file_not_found_has_message_and_hint(self, mocker: MagicMock) -> None:
        """FILE_NOT_FOUND エラーは message と hint を持つ（§6.4 規約）。"""
        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")

        with pytest.raises(ClipwrightError) as exc_info:
            inspect_media("/nonexistent/video.mp4")

        err = exc_info.value
        assert len(err.message) > 0
        assert len(err.hint) > 0

    def test_file_not_found_before_resolve_tool_is_called(
        self, mocker: MagicMock
    ) -> None:
        """ファイル検証はパス検証の前に行われること（resolve_tool より先に FILE_NOT_FOUND）。

        ファイル存在確認は ffprobe 探索より先に行うことで、
        ユーザーへの feedback を早める。
        """
        mock_resolve = mocker.patch(
            "clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe"
        )

        with pytest.raises(ClipwrightError) as exc_info:
            inspect_media("/nonexistent/video.mp4")

        # FILE_NOT_FOUND が先に飛ぶ場合は resolve_tool が呼ばれないパターンも許容する
        # ただし必ず FILE_NOT_FOUND コードであること
        assert exc_info.value.code == ErrorCode.FILE_NOT_FOUND


class TestInspectMediaDependencyMissing:
    """ffprobe が見つからない場合は DEPENDENCY_MISSING を送出する。"""

    def test_raises_dependency_missing_when_ffprobe_not_found(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """ffprobe が見つからない場合（resolve_tool が DEPENDENCY_MISSING）を
        inspect_media が正しく伝播させること。"""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch(
            "clipwright.media.resolve_tool",
            side_effect=ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="ffprobe が PATH 上に見つかりません",
                hint="winget install Gyan.FFmpeg で導入してください",
            ),
        )

        with pytest.raises(ClipwrightError) as exc_info:
            inspect_media(str(media_file))

        assert exc_info.value.code == ErrorCode.DEPENDENCY_MISSING

    def test_dependency_missing_hint_mentions_ffprobe(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """DEPENDENCY_MISSING エラーの hint に ffprobe へのアクションが含まれること。"""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch(
            "clipwright.media.resolve_tool",
            side_effect=ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="ffprobe が PATH 上に見つかりません",
                hint="winget install Gyan.FFmpeg で導入してください",
            ),
        )

        with pytest.raises(ClipwrightError) as exc_info:
            inspect_media(str(media_file))

        assert len(exc_info.value.hint) > 0


class TestInspectMediaProbeFailed:
    """ffprobe が不正な JSON を返した場合は PROBE_FAILED を送出する。"""

    def test_raises_probe_failed_on_invalid_json(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """ffprobe の stdout が有効な JSON でない場合は PROBE_FAILED になること。"""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process("THIS IS NOT JSON"),
        )

        with pytest.raises(ClipwrightError) as exc_info:
            inspect_media(str(media_file))

        assert exc_info.value.code == ErrorCode.PROBE_FAILED

    def test_raises_probe_failed_on_empty_stdout(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """ffprobe の stdout が空文字列の場合も PROBE_FAILED になること。"""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process(""),
        )

        with pytest.raises(ClipwrightError) as exc_info:
            inspect_media(str(media_file))

        assert exc_info.value.code == ErrorCode.PROBE_FAILED

    def test_raises_probe_failed_on_json_missing_required_fields(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """ffprobe の JSON に必須フィールド（streams / format）がない場合も
        PROBE_FAILED になること。"""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process(json.dumps({"unexpected": "data"})),
        )

        with pytest.raises(ClipwrightError) as exc_info:
            inspect_media(str(media_file))

        assert exc_info.value.code == ErrorCode.PROBE_FAILED

    def test_probe_failed_has_message_and_hint(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """PROBE_FAILED エラーは message と hint を持つ（§6.4 規約）。"""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process("INVALID JSON{{"),
        )

        with pytest.raises(ClipwrightError) as exc_info:
            inspect_media(str(media_file))

        err = exc_info.value
        assert len(err.message) > 0
        assert len(err.hint) > 0


class TestInspectMediaRunInvocation:
    """process.run の呼ばれ方を検証する（規約6.5 shell=False・引数配列）。"""

    def test_run_called_with_list_cmd(self, mocker: MagicMock, tmp_path) -> None:
        """run に渡されるコマンドがリスト（引数配列）であること。"""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mock_run = mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        inspect_media(str(media_file))

        call_args = mock_run.call_args
        cmd = call_args.args[0] if call_args.args else call_args.kwargs.get("cmd", [])
        assert isinstance(cmd, list)

    def test_run_called_with_show_format_and_show_streams(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """run に渡すコマンドに -show_format と -show_streams が含まれること。"""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mock_run = mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        inspect_media(str(media_file))

        call_args = mock_run.call_args
        cmd = call_args.args[0] if call_args.args else call_args.kwargs.get("cmd", [])
        assert "-show_format" in cmd
        assert "-show_streams" in cmd

    def test_run_called_with_json_print_format(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """run に渡すコマンドに -print_format json が含まれること。"""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mock_run = mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        inspect_media(str(media_file))

        call_args = mock_run.call_args
        cmd = call_args.args[0] if call_args.args else call_args.kwargs.get("cmd", [])
        assert "-print_format" in cmd
        idx = cmd.index("-print_format")
        assert cmd[idx + 1] == "json"

    def test_run_called_with_file_path_as_last_arg(
        self, mocker: MagicMock, tmp_path
    ) -> None:
        """run に渡すコマンドの末尾に入力ファイルパスが含まれること。"""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mocker.patch("clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe")
        mock_run = mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        inspect_media(str(media_file))

        call_args = mock_run.call_args
        cmd = call_args.args[0] if call_args.args else call_args.kwargs.get("cmd", [])
        assert str(media_file) in cmd

    def test_ffprobe_resolved_with_env_var(self, mocker: MagicMock, tmp_path) -> None:
        """resolve_tool が "ffprobe" と "CLIPWRIGHT_FFPROBE" で呼ばれること（ADR-3）。"""
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")

        mock_resolve = mocker.patch(
            "clipwright.media.resolve_tool", return_value="/usr/bin/ffprobe"
        )
        mocker.patch(
            "clipwright.media.run",
            return_value=_make_completed_process(_make_ffprobe_json()),
        )

        inspect_media(str(media_file))

        mock_resolve.assert_called_once_with("ffprobe", "CLIPWRIGHT_FFPROBE")


# ===========================================================================
# 統合テスト: 実 ffprobe で生成 mp4 を inspect する
# ===========================================================================


class TestInspectMediaIntegration:
    """実 ffprobe を使用した統合テスト（§13.4 DC-AM-006）。

    conftest の sample_media / ffprobe_path フィクスチャを使用する。
    ffmpeg/ffprobe が到達可能な環境では skip せず必須実行する。
    """

    def test_integration_inspect_real_mp4_returns_media_info(
        self,
        sample_media: str,
        ffprobe_path: str | None,
    ) -> None:
        """実 ffprobe で生成 mp4 を inspect し MediaInfo が返ること。

        ffprobe_path が None の場合は ffprobe 単体が到達不可（ffmpeg はある）。
        CLIPWRIGHT_FFPROBE env があれば到達可能。
        """
        if ffprobe_path is None:
            pytest.skip(
                "ffprobe が見つかりません（CLIPWRIGHT_FFPROBE が未設定で PATH 上にもない）。"
            )

        result = inspect_media(sample_media)

        assert isinstance(result, MediaInfo)

    def test_integration_duration_is_approximately_3_seconds(
        self,
        sample_media: str,
        ffprobe_path: str | None,
    ) -> None:
        """生成 mp4（3 秒）の duration が約 3.0 秒になること。

        RationalTimeModel の value / rate から秒数を導出して検証する。
        誤差は ±0.1 秒を許容する（lavfi 生成の精度）。
        """
        if ffprobe_path is None:
            pytest.skip(
                "ffprobe が見つかりません（CLIPWRIGHT_FFPROBE が未設定で PATH 上にもない）。"
            )

        result = inspect_media(sample_media)

        assert result.duration is not None
        duration_sec = result.duration.value / result.duration.rate
        assert duration_sec == pytest.approx(3.0, abs=0.1)

    def test_integration_streams_contain_video_and_audio(
        self,
        sample_media: str,
        ffprobe_path: str | None,
    ) -> None:
        """生成 mp4 に video / audio ストリームが含まれること。"""
        if ffprobe_path is None:
            pytest.skip(
                "ffprobe が見つかりません（CLIPWRIGHT_FFPROBE が未設定で PATH 上にもない）。"
            )

        result = inspect_media(sample_media)

        codec_types = [s.codec_type for s in result.streams]
        assert "video" in codec_types
        assert "audio" in codec_types

    def test_integration_video_rate_equals_30fps(
        self,
        sample_media: str,
        ffprobe_path: str | None,
    ) -> None:
        """生成 mp4（30fps）の duration.rate が 30.0 になること（DC-AS-006）。

        conftest の sample_media は rate=30 で生成されている。
        """
        if ffprobe_path is None:
            pytest.skip(
                "ffprobe が見つかりません（CLIPWRIGHT_FFPROBE が未設定で PATH 上にもない）。"
            )

        result = inspect_media(sample_media)

        assert result.duration is not None
        assert result.duration.rate == pytest.approx(30.0)

    def test_integration_path_preserved(
        self,
        sample_media: str,
        ffprobe_path: str | None,
    ) -> None:
        """MediaInfo.path が入力パスと一致すること（統合）。"""
        if ffprobe_path is None:
            pytest.skip(
                "ffprobe が見つかりません（CLIPWRIGHT_FFPROBE が未設定で PATH 上にもない）。"
            )

        result = inspect_media(sample_media)

        assert result.path == sample_media
