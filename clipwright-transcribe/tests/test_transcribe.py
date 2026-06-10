"""test_transcribe.py — transcribe.py オーケストレーションのテスト。

対象 API:
  clipwright_transcribe.transcribe.transcribe_media(
      media: str, output: str, options: TranscribeOptions,
  ) -> dict

モック方針:
  - transcribe.inspect_media を patch して MediaInfo を供給。
  - 軽量フロー検証では transcribe._run_whisper を patch して segments/language を供給。
  - _run_whisper 単体検証では transcribe.resolve_tool / transcribe.run を patch。
  - 実 ffmpeg/whisper バイナリは一切呼ばない。

検証観点（architecture TR-AD-01/03/04/05/08/09/10 / §8 C-3 対応）:
  ① 出力検証（拡張子・親dir・output==media・同一dir）
  ② 入力検証（音声なし=UNSUPPORTED_OPERATION・FILE_NOT_FOUND basename・DC-AS-004 依存不在）
  ③ モデル解決（os.path.isfile・param→env・DC-AS-003）
  ④ OTIO（全尺1clip kind=transcript-source・segment marker on V1・DC-AM-101/001）
  ⑤ DC-GP-003 marker name 短縮・DC-GP-002 0件・DC-AS-005 秒値一貫
  ⑥ SRT/VTT 同basename同dir・artifacts3件・サニタイズ・summary/data
"""

from __future__ import annotations

import os
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_transcribe.captions import Segment
from clipwright_transcribe.schemas import TranscribeOptions
from clipwright_transcribe.transcribe import (
    LANG_AUTO_FLAG,
    WHISPER_BINARY_NAME,
    _resolve_model_path,
    _run_whisper,
    transcribe_media,
)

FPS = 30.0


# ===========================================================================
# ヘルパー
# ===========================================================================


def _make_media_info(
    path: str = "/fake/video.mp4",
    *,
    duration_sec: float | None = 10.0,
    rate: float = FPS,
    has_video: bool = True,
    has_audio: bool = True,
) -> MediaInfo:
    """テスト用 MediaInfo を構築する。"""
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(StreamInfo(index=0, codec_type="video", codec_name="h264"))
    if has_audio:
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


def _seg(start_sec: float, end_sec: float, text: str) -> Segment:
    return {"start_sec": start_sec, "end_sec": end_sec, "text": text}


def _make_paths(tmp_path: Path) -> tuple[str, str, str]:
    """media / output / model のパスを同一一時ディレクトリに作る。

    media と model は実ファイルとして作成する（inspect_media/_run_whisper は
    モックするが、同一dir 検証・モデル isfile 検査を通すため）。
    """
    media = tmp_path / "video.mp4"
    media.write_bytes(b"fake")
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"fake-model")
    output = tmp_path / "out.otio"
    return str(media), str(output), str(model)


def _opts(**kwargs: Any) -> TranscribeOptions:
    return TranscribeOptions(**kwargs)


# ===========================================================================
# ① 出力検証
# ===========================================================================


class TestOutputValidation:
    def test_invalid_extension_rejected(self, tmp_path: Path) -> None:
        media = tmp_path / "video.mp4"
        media.write_bytes(b"x")
        result = transcribe_media(str(media), str(tmp_path / "out.srt"), _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_missing_parent_dir_rejected(self, tmp_path: Path) -> None:
        media = tmp_path / "video.mp4"
        media.write_bytes(b"x")
        out = tmp_path / "nope" / "out.otio"
        result = transcribe_media(str(media), str(out), _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_output_equals_media_rejected(self, tmp_path: Path) -> None:
        media = tmp_path / "same.otio"
        media.write_bytes(b"x")
        result = transcribe_media(str(media), str(media), _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_output_different_dir_rejected(self, tmp_path: Path) -> None:
        media = tmp_path / "a" / "video.mp4"
        media.parent.mkdir()
        media.write_bytes(b"x")
        out_dir = tmp_path / "b"
        out_dir.mkdir()
        out = out_dir / "out.otio"
        with patch(
            "clipwright_transcribe.transcribe.inspect_media",
            return_value=_make_media_info(str(media)),
        ):
            result = transcribe_media(str(media), str(out), _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT


# ===========================================================================
# ② 入力検証
# ===========================================================================


class TestInputValidation:
    def test_no_audio_stream_unsupported(self, tmp_path: Path) -> None:
        media, output, _model = _make_paths(tmp_path)
        with patch(
            "clipwright_transcribe.transcribe.inspect_media",
            return_value=_make_media_info(media, has_audio=False, has_video=True),
        ):
            result = transcribe_media(media, output, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.UNSUPPORTED_OPERATION

    def test_audio_only_is_accepted(self, tmp_path: Path) -> None:
        """映像なし・音声のみ素材は受理されること（TR-AD-03）。"""
        media, output, model = _make_paths(tmp_path)
        with (
            patch(
                "clipwright_transcribe.transcribe.inspect_media",
                return_value=_make_media_info(media, has_video=False, has_audio=True),
            ),
            patch(
                "clipwright_transcribe.transcribe._run_whisper",
                return_value=([_seg(0.0, 1.0, "hi")], "en"),
            ),
        ):
            result = transcribe_media(media, output, _opts(model_path=model))
        assert result["ok"] is True

    def test_file_not_found_basename_only(self, tmp_path: Path) -> None:
        """FILE_NOT_FOUND の message は basename のみ（フルパス非露出・TR-AD-09）。"""
        media, output, _model = _make_paths(tmp_path)
        with patch(
            "clipwright_transcribe.transcribe.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"ファイルが見つかりません: {media}",
                hint="パスを確認してください。",
            ),
        ):
            result = transcribe_media(media, output, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND
        # フルパスを含まず basename のみ
        assert media not in result["error"]["message"]
        assert "video.mp4" in result["error"]["message"]

    def test_inspect_media_other_error_reraised(self, tmp_path: Path) -> None:
        """FILE_NOT_FOUND 以外の inspect_media エラーはそのまま伝播すること（L321）。"""
        media, output, _model = _make_paths(tmp_path)
        with patch(
            "clipwright_transcribe.transcribe.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.PROBE_FAILED,
                message="probe 失敗",
                hint="確認してください。",
            ),
        ):
            result = transcribe_media(media, output, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PROBE_FAILED

    def test_duration_none_probe_failed(self, tmp_path: Path) -> None:
        media, output, model = _make_paths(tmp_path)
        with patch(
            "clipwright_transcribe.transcribe.inspect_media",
            return_value=_make_media_info(media, duration_sec=None),
        ):
            result = transcribe_media(media, output, _opts(model_path=model))
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PROBE_FAILED


# ===========================================================================
# ② / ③ 依存・モデル解決（DC-AS-003/004）
# ===========================================================================


class TestDependencyResolution:
    def test_model_missing_dependency_missing(self, tmp_path: Path) -> None:
        """model_path も env も無い → DEPENDENCY_MISSING（DC-AS-003）。"""
        media = tmp_path / "video.mp4"
        media.write_bytes(b"x")
        output = tmp_path / "out.otio"
        with (
            patch(
                "clipwright_transcribe.transcribe.inspect_media",
                return_value=_make_media_info(str(media)),
            ),
            patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("CLIPWRIGHT_WHISPER_MODEL", None)
            result = transcribe_media(str(media), str(output), _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.DEPENDENCY_MISSING

    def test_ffmpeg_missing_dependency_missing(self, tmp_path: Path) -> None:
        """ffmpeg 不在 → DEPENDENCY_MISSING（resolve_tool が送出・DC-AS-004）。"""
        media, output, model = _make_paths(tmp_path)
        with (
            patch(
                "clipwright_transcribe.transcribe.inspect_media",
                return_value=_make_media_info(media),
            ),
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=ClipwrightError(
                    code=ErrorCode.DEPENDENCY_MISSING,
                    message="ffmpeg が見つかりません",
                    hint="ffmpeg を導入してください。",
                ),
            ),
        ):
            result = transcribe_media(media, output, _opts(model_path=model))
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.DEPENDENCY_MISSING

    def test_resolve_model_path_param_priority(self, tmp_path: Path) -> None:
        """model_path（param）が存在すればそれを返すこと。"""
        model = tmp_path / "m.bin"
        model.write_bytes(b"x")
        resolved = _resolve_model_path(_opts(model_path=str(model)))
        assert resolved == str(model)

    def test_resolve_model_path_env_fallback(self, tmp_path: Path) -> None:
        """model_path 未指定時は env CLIPWRIGHT_WHISPER_MODEL を使うこと。"""
        model = tmp_path / "env.bin"
        model.write_bytes(b"x")
        with patch.dict(os.environ, {"CLIPWRIGHT_WHISPER_MODEL": str(model)}):
            resolved = _resolve_model_path(_opts())
        assert resolved == str(model)

    def test_resolve_model_path_missing_raises(self, tmp_path: Path) -> None:
        """param が存在しないファイル・env も無し → DEPENDENCY_MISSING。"""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLIPWRIGHT_WHISPER_MODEL", None)
            with pytest.raises(ClipwrightError) as exc_info:
                _resolve_model_path(_opts(model_path=str(tmp_path / "nope.bin")))
        assert exc_info.value.code == ErrorCode.DEPENDENCY_MISSING


# ===========================================================================
# ④ OTIO 構築（全尺1clip + segment marker）
# ===========================================================================


class TestOtioConstruction:
    def _run(
        self, tmp_path: Path, segments: list[Segment], language: str = "en"
    ) -> tuple[dict[str, Any], otio.schema.Timeline]:
        media, output, model = _make_paths(tmp_path)
        with (
            patch(
                "clipwright_transcribe.transcribe.inspect_media",
                return_value=_make_media_info(media),
            ),
            patch(
                "clipwright_transcribe.transcribe._run_whisper",
                return_value=(segments, language),
            ),
        ):
            result = transcribe_media(media, output, _opts(model_path=model))
        timeline = otio.adapters.read_from_file(output)
        return result, timeline

    def test_full_clip_present(self, tmp_path: Path) -> None:
        """V1 に全尺1clip（kind=transcript-source・start_time=0）が載ること。"""
        result, timeline = self._run(tmp_path, [_seg(0.0, 1.0, "hi")])
        assert result["ok"] is True
        v1 = timeline.tracks[0]
        clips = [c for c in v1 if isinstance(c, otio.schema.Clip)]
        assert len(clips) == 1
        clip = clips[0]
        cw = clip.metadata["clipwright"]
        assert cw["kind"] == "transcript-source"
        assert cw["tool"] == "clipwright-transcribe"
        # source_range.start_time == 0
        assert clip.source_range.start_time.value == pytest.approx(0.0)
        # 全尺（10秒 × 30fps = 300）
        assert clip.source_range.duration.value == pytest.approx(300.0)

    def test_markers_on_v1_track(self, tmp_path: Path) -> None:
        """各セグメントが V1 トラックの marker として付与されること（DC-AM-101）。"""
        segs = [_seg(0.0, 1.2, "Hello"), _seg(1.5, 2.8, "World")]
        _result, timeline = self._run(tmp_path, segs)
        v1 = timeline.tracks[0]
        assert len(v1.markers) == 2

    def test_marker_metadata_caption(self, tmp_path: Path) -> None:
        """marker metadata に kind=caption / text / language が入ること。"""
        _result, timeline = self._run(tmp_path, [_seg(0.0, 1.2, "Hello")], "ja")
        marker = timeline.tracks[0].markers[0]
        cw = marker.metadata["clipwright"]
        assert cw["kind"] == "caption"
        assert cw["text"] == "Hello"
        assert cw["language"] == "ja"

    def test_marker_marked_range_uses_whisper_seconds(self, tmp_path: Path) -> None:
        """marker marked_range が whisper 秒値そのまま（DC-AM-001・RationalTime 比較）。

        start=1.5s・rate=30 → value=45.0。近似比較を避け RationalTime で厳密比較する。
        """
        _result, timeline = self._run(tmp_path, [_seg(1.5, 2.8, "x")])
        marker = timeline.tracks[0].markers[0]
        expected_start = otio.opentime.RationalTime(1.5 * FPS, FPS)
        expected_dur = otio.opentime.RationalTime((2.8 - 1.5) * FPS, FPS)
        assert marker.marked_range.start_time == expected_start
        assert marker.marked_range.duration == expected_dur

    def test_marker_time_matches_srt_seconds(self, tmp_path: Path) -> None:
        """marker 秒値と SRT タイムコードが同一秒値由来であること（DC-AS-005）。"""
        media, output, model = _make_paths(tmp_path)
        with (
            patch(
                "clipwright_transcribe.transcribe.inspect_media",
                return_value=_make_media_info(media),
            ),
            patch(
                "clipwright_transcribe.transcribe._run_whisper",
                return_value=([_seg(1.5, 2.8, "x")], "en"),
            ),
        ):
            transcribe_media(media, output, _opts(model_path=model))
        timeline = otio.adapters.read_from_file(output)
        marker = timeline.tracks[0].markers[0]
        # marker start = 1.5s → SRT "00:00:01,500"
        start_sec = (
            marker.marked_range.start_time.value / marker.marked_range.start_time.rate
        )
        assert start_sec == pytest.approx(1.5)
        srt = Path(output).with_suffix(".srt").read_text(encoding="utf-8")
        assert "00:00:01,500" in srt

    def test_marker_name_truncated(self, tmp_path: Path) -> None:
        """長文セグメントの marker name が先頭40字に短縮され本文は metadata.text（DC-GP-003）。"""
        long_text = "あ" * 60
        _result, timeline = self._run(tmp_path, [_seg(0.0, 1.0, long_text)])
        marker = timeline.tracks[0].markers[0]
        assert len(marker.name) <= 41  # 40字 + 省略記号
        assert marker.name.startswith("あ" * 40)
        assert marker.metadata["clipwright"]["text"] == long_text


# ===========================================================================
# ⑤ DC-GP-002 セグメント0件
# ===========================================================================


class TestZeroSegments:
    def test_zero_segments_envelope(self, tmp_path: Path) -> None:
        media, output, model = _make_paths(tmp_path)
        with (
            patch(
                "clipwright_transcribe.transcribe.inspect_media",
                return_value=_make_media_info(media),
            ),
            patch(
                "clipwright_transcribe.transcribe._run_whisper",
                return_value=([], "en"),
            ),
        ):
            result = transcribe_media(media, output, _opts(model_path=model))
        assert result["ok"] is True
        assert result["warnings"]  # 0件警告
        assert result["data"]["segment_count"] == 0
        timeline = otio.adapters.read_from_file(output)
        v1 = timeline.tracks[0]
        # marker 0・全尺1clip は存在
        assert len(v1.markers) == 0
        assert len([c for c in v1 if isinstance(c, otio.schema.Clip)]) == 1
        # SRT 空・VTT ヘッダのみ
        srt = Path(output).with_suffix(".srt").read_text(encoding="utf-8")
        vtt = Path(output).with_suffix(".vtt").read_text(encoding="utf-8")
        assert srt == ""
        assert vtt.strip() == "WEBVTT"


# ===========================================================================
# ⑥ 出力・エンベロープ
# ===========================================================================


class TestEnvelopeAndOutputs:
    def _run(self, tmp_path: Path) -> tuple[dict[str, Any], str, str]:
        media, output, model = _make_paths(tmp_path)
        segs = [_seg(0.0, 1.2, "Hello"), _seg(1.5, 2.8, "World")]
        with (
            patch(
                "clipwright_transcribe.transcribe.inspect_media",
                return_value=_make_media_info(media),
            ),
            patch(
                "clipwright_transcribe.transcribe._run_whisper",
                return_value=(segs, "en"),
            ),
        ):
            result = transcribe_media(media, output, _opts(model_path=model))
        return result, output, media

    def test_srt_vtt_same_basename_dir(self, tmp_path: Path) -> None:
        result, output, _media = self._run(tmp_path)
        srt = Path(output).with_suffix(".srt")
        vtt = Path(output).with_suffix(".vtt")
        assert srt.exists()
        assert vtt.exists()
        assert srt.parent == Path(output).parent

    def test_artifacts_three(self, tmp_path: Path) -> None:
        result, output, _media = self._run(tmp_path)
        roles = {(a["role"], a["format"]) for a in result["artifacts"]}
        assert ("timeline", "otio") in roles
        assert ("captions", "srt") in roles
        assert ("captions", "vtt") in roles
        assert len(result["artifacts"]) == 3

    def test_summary_contains_language_count_duration(self, tmp_path: Path) -> None:
        result, _output, _media = self._run(tmp_path)
        summary = result["summary"]
        assert "en" in summary
        assert "2" in summary  # セグメント数

    def test_data_lightweight(self, tmp_path: Path) -> None:
        result, _output, _media = self._run(tmp_path)
        data = result["data"]
        assert data["segment_count"] == 2
        assert data["language"] == "en"
        assert "total_duration_seconds" in data
        # 全文セグメントは data に詰めない
        assert "segments" not in data


# ===========================================================================
# _run_whisper アダプタ単体（resolve_tool / run モック）
# ===========================================================================


class TestRunWhisperAdapter:
    def _fake_resolve(self) -> Any:
        def _impl(name: str, env: str | None = None) -> str:
            return f"/bin/{name}"

        return _impl

    def _fake_run_writes_json(self, json_text: str) -> Any:
        """whisper 呼び出し時に <prefix>.json を書く run モックを返す。"""

        def _impl(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "-of" in cmd:
                prefix = cmd[cmd.index("-of") + 1]
                Path(prefix + ".json").write_text(json_text, encoding="utf-8")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        return _impl

    def test_success_returns_segments_and_language(
        self, tmp_path: Path, whisper_sample_json: dict[str, Any]
    ) -> None:
        import json as _json

        model = tmp_path / "m.bin"
        model.write_bytes(b"x")
        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=self._fake_resolve(),
            ),
            patch(
                "clipwright_transcribe.transcribe.run",
                side_effect=self._fake_run_writes_json(
                    _json.dumps(whisper_sample_json)
                ),
            ),
        ):
            segments, language = _run_whisper("video.mp4", _opts(), 10.0, str(model))
        assert len(segments) == 3
        assert language == "en"

    def test_language_auto_flag_when_none(self, tmp_path: Path) -> None:
        """language=None で LANG_AUTO_FLAG が cmd に入ること（DC-AM-002）。"""
        captured: dict[str, list[str]] = {}

        def _capture_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "-of" in cmd:
                captured["whisper"] = cmd
                prefix = cmd[cmd.index("-of") + 1]
                Path(prefix + ".json").write_text(
                    '{"transcription": []}', encoding="utf-8"
                )
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=self._fake_resolve(),
            ),
            patch("clipwright_transcribe.transcribe.run", side_effect=_capture_run),
        ):
            _run_whisper("video.mp4", _opts(language=None), 10.0, "m.bin")
        cmd = captured["whisper"]
        for token in LANG_AUTO_FLAG.split():
            assert token in cmd

    def test_language_explicit_flag(self, tmp_path: Path) -> None:
        captured: dict[str, list[str]] = {}

        def _capture_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "-of" in cmd:
                captured["whisper"] = cmd
                prefix = cmd[cmd.index("-of") + 1]
                Path(prefix + ".json").write_text(
                    '{"transcription": []}', encoding="utf-8"
                )
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=self._fake_resolve(),
            ),
            patch("clipwright_transcribe.transcribe.run", side_effect=_capture_run),
        ):
            _run_whisper("video.mp4", _opts(language="ja"), 10.0, "m.bin")
        cmd = captured["whisper"]
        assert "-l" in cmd
        assert "ja" in cmd
        assert "auto" not in cmd

    def test_initial_prompt_flag(self, tmp_path: Path) -> None:
        captured: dict[str, list[str]] = {}

        def _capture_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "-of" in cmd:
                captured["whisper"] = cmd
                prefix = cmd[cmd.index("-of") + 1]
                Path(prefix + ".json").write_text(
                    '{"transcription": []}', encoding="utf-8"
                )
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=self._fake_resolve(),
            ),
            patch("clipwright_transcribe.transcribe.run", side_effect=_capture_run),
        ):
            _run_whisper("video.mp4", _opts(initial_prompt="clipwright"), 10.0, "m.bin")
        cmd = captured["whisper"]
        assert "--prompt" in cmd
        assert "clipwright" in cmd

    def test_subprocess_failure_sanitized(self, tmp_path: Path) -> None:
        """ffmpeg/whisper の SUBPROCESS_FAILED stderr がサニタイズされること（TR-AD-09）。"""
        leak = "/secret/path/to/model stderr leak"

        def _raise_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=f"コマンドが失敗しました: {leak}",
                hint="確認してください。",
            )

        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=self._fake_resolve(),
            ),
            patch("clipwright_transcribe.transcribe.run", side_effect=_raise_run),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            _run_whisper("video.mp4", _opts(), 10.0, "m.bin")
        assert exc_info.value.code == ErrorCode.SUBPROCESS_FAILED
        assert leak not in exc_info.value.message
        assert "内部サブプロセス" in exc_info.value.message

    def test_whisper_run_failure_sanitized(self, tmp_path: Path) -> None:
        """ffmpeg 成功・whisper run 失敗時もサニタイズされること（L215-216）。"""
        leak = "/secret/whisper stderr"

        def _run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "-of" in cmd:  # whisper 呼び出しで失敗させる
                raise ClipwrightError(
                    code=ErrorCode.SUBPROCESS_TIMEOUT,
                    message=f"timeout: {leak}",
                    hint="確認してください。",
                )
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=self._fake_resolve(),
            ),
            patch("clipwright_transcribe.transcribe.run", side_effect=_run),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            _run_whisper("video.mp4", _opts(), 10.0, "m.bin")
        assert exc_info.value.code == ErrorCode.SUBPROCESS_TIMEOUT
        assert leak not in exc_info.value.message

    def test_sanitize_passthrough_non_subprocess(self) -> None:
        """非サブプロセス系の ClipwrightError はそのまま返ること（L88）。"""
        from clipwright_transcribe.transcribe import _sanitize_subprocess_error

        original = ClipwrightError(
            code=ErrorCode.INVALID_INPUT, message="msg", hint="hint"
        )
        result = _sanitize_subprocess_error(original)
        assert result is original

    def test_json_read_failure_subprocess_failed(self, tmp_path: Path) -> None:
        """whisper が JSON を書かなかった場合 SUBPROCESS_FAILED になること。"""

        def _run_no_json(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            # JSON を書かない
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=self._fake_resolve(),
            ),
            patch("clipwright_transcribe.transcribe.run", side_effect=_run_no_json),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            _run_whisper("video.mp4", _opts(), 10.0, "m.bin")
        assert exc_info.value.code == ErrorCode.SUBPROCESS_FAILED

    def test_whisper_binary_name_constant_used(self, tmp_path: Path) -> None:
        """resolve_tool が WHISPER_BINARY_NAME 定数で呼ばれること（DC-AS-003）。"""
        names: list[str] = []

        def _track_resolve(name: str, env: str | None = None) -> str:
            names.append(name)
            return f"/bin/{name}"

        def _capture_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "-of" in cmd:
                prefix = cmd[cmd.index("-of") + 1]
                Path(prefix + ".json").write_text(
                    '{"transcription": []}', encoding="utf-8"
                )
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=_track_resolve,
            ),
            patch("clipwright_transcribe.transcribe.run", side_effect=_capture_run),
        ):
            _run_whisper("video.mp4", _opts(), 10.0, "m.bin")
        assert WHISPER_BINARY_NAME in names
        assert "ffmpeg" in names
