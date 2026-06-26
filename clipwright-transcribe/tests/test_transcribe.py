"""test_transcribe.py — Tests for the transcribe.py orchestration layer.

Target API:
  clipwright_transcribe.transcribe.transcribe_media(
      media: str, output: str, options: TranscribeOptions,
  ) -> ToolResult

Mock strategy:
  - Patch transcribe.inspect_media to supply MediaInfo.
  - Patch transcribe._run_whisper to supply segments/language for lightweight flow tests.
  - Patch transcribe.resolve_tool / transcribe.run for _run_whisper unit tests.
  - No real ffmpeg/whisper binaries are invoked.

Verification points (architecture TR-AD-01/03/04/05/08/09/10 / §8 C-3):
  ① Output validation (extension, parent dir, output==media, same-dir)
  ② Input validation (no audio=UNSUPPORTED_OPERATION, FILE_NOT_FOUND basename,
     DC-AS-004 missing dependency)
  ③ Model resolution (os.path.isfile, param->env, DC-AS-003)
  ④ OTIO (full-length 1 clip kind=transcript-source, segment marker on V1,
     DC-AM-101/001)
  ⑤ DC-GP-003 marker name truncation, DC-GP-002 zero segments, DC-AS-005 second
     value consistency
  ⑥ SRT/VTT same basename+dir, 3 artifacts, sanitisation, summary/data
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
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo, ToolResult

from clipwright_transcribe.captions import Segment
from clipwright_transcribe.schemas import TranscribeOptions
from clipwright_transcribe.transcribe import (
    _MARKER_NAME_MAX,
    LANG_AUTO_FLAG,
    WHISPER_BINARY_NAME,
    WhisperRun,
    _resolve_model_path,
    _run_whisper,
    transcribe_media,
)

from ._whisper_run import _whisper_run

FPS = 30.0


# ===========================================================================
# Helpers
# ===========================================================================


def _make_media_info(
    path: str = "/fake/video.mp4",
    *,
    duration_sec: float | None = 10.0,
    rate: float = FPS,
    has_video: bool = True,
    has_audio: bool = True,
) -> MediaInfo:
    """Build a MediaInfo for testing."""
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
    """Create media / output / model paths inside the same temporary directory.

    media and model are written as real files (inspect_media/_run_whisper are mocked,
    but the same-dir check and model isfile check still need them to exist).
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
# ① Output validation
# ===========================================================================


class TestOutputValidation:
    def test_invalid_extension_rejected(self, tmp_path: Path) -> None:
        media = tmp_path / "video.mp4"
        media.write_bytes(b"x")
        result = transcribe_media(str(media), str(tmp_path / "out.srt"), _opts())
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT
        # Regression guard: message must use a fixed string (no user input interpolation).
        # If someone re-adds suffix interpolation, the suffix value must NOT appear in the message.
        assert ".srt" not in result.error.message
        assert "Invalid output file extension" in result.error.message

    def test_missing_parent_dir_rejected(self, tmp_path: Path) -> None:
        media = tmp_path / "video.mp4"
        media.write_bytes(b"x")
        out = tmp_path / "nope" / "out.otio"
        result = transcribe_media(str(media), str(out), _opts())
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT

    def test_output_equals_media_rejected(self, tmp_path: Path) -> None:
        media = tmp_path / "same.otio"
        media.write_bytes(b"x")
        result = transcribe_media(str(media), str(media), _opts())
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT

    def test_output_different_dir_allowed(self, tmp_path: Path) -> None:
        """output in a different directory is now allowed (new policy: TR-AD-08 check removed).

        RED: impl still raises INVALID_INPUT at transcribe.py L548-567; must be True after fix.
        """
        media = tmp_path / "a" / "video.mp4"
        media.parent.mkdir()
        media.write_bytes(b"x")
        out_dir = tmp_path / "b"
        out_dir.mkdir()
        model_dir = tmp_path / "models"
        model_dir.mkdir()
        model = model_dir / "ggml-base.bin"
        model.write_bytes(b"fake-model")
        out = out_dir / "out.otio"
        with (
            patch(
                "clipwright_transcribe.transcribe.inspect_media",
                return_value=_make_media_info(str(media)),
            ),
            patch(
                "clipwright_transcribe.transcribe._run_whisper",
                return_value=_whisper_run([]),
            ),
        ):
            result = transcribe_media(
                str(media), str(out), _opts(model_path=str(model))
            )
        # RED: currently INVALID_INPUT — must be True after impl removes same-dir check
        assert result.ok is True


# ===========================================================================
# ② Input validation
# ===========================================================================


class TestInputValidation:
    def test_no_audio_stream_unsupported(self, tmp_path: Path) -> None:
        media, output, _model = _make_paths(tmp_path)
        with patch(
            "clipwright_transcribe.transcribe.inspect_media",
            return_value=_make_media_info(media, has_audio=False, has_video=True),
        ):
            result = transcribe_media(media, output, _opts())
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.UNSUPPORTED_OPERATION

    def test_audio_only_is_accepted(self, tmp_path: Path) -> None:
        """Video-less, audio-only sources are accepted (TR-AD-03)."""
        media, output, model = _make_paths(tmp_path)
        with (
            patch(
                "clipwright_transcribe.transcribe.inspect_media",
                return_value=_make_media_info(media, has_video=False, has_audio=True),
            ),
            patch(
                "clipwright_transcribe.transcribe._run_whisper",
                return_value=_whisper_run([_seg(0.0, 1.0, "hi")]),
            ),
        ):
            result = transcribe_media(media, output, _opts(model_path=model))
        assert result.ok is True

    def test_file_not_found_basename_only(self, tmp_path: Path) -> None:
        """FILE_NOT_FOUND message exposes only the basename, not the full path
        (TR-AD-09)."""
        media, output, _model = _make_paths(tmp_path)
        with patch(
            "clipwright_transcribe.transcribe.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"File not found: {media}",
                hint="Check that the path is correct.",
            ),
        ):
            result = transcribe_media(media, output, _opts())
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.FILE_NOT_FOUND
        # Full path must not appear; basename must appear.
        assert media not in result.error.message
        assert "video.mp4" in result.error.message

    def test_inspect_media_other_error_reraised(self, tmp_path: Path) -> None:
        """Non-FILE_NOT_FOUND errors from inspect_media propagate unchanged (L321)."""
        media, output, _model = _make_paths(tmp_path)
        with patch(
            "clipwright_transcribe.transcribe.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.PROBE_FAILED,
                message="probe failed",
                hint="Check the input.",
            ),
        ):
            result = transcribe_media(media, output, _opts())
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.PROBE_FAILED

    def test_duration_none_probe_failed(self, tmp_path: Path) -> None:
        media, output, model = _make_paths(tmp_path)
        with patch(
            "clipwright_transcribe.transcribe.inspect_media",
            return_value=_make_media_info(media, duration_sec=None),
        ):
            result = transcribe_media(media, output, _opts(model_path=model))
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.PROBE_FAILED


# ===========================================================================
# ② / ③ Dependency and model resolution (DC-AS-003/004)
# ===========================================================================


class TestDependencyResolution:
    def test_model_missing_dependency_missing(self, tmp_path: Path) -> None:
        """No model_path and no env -> DEPENDENCY_MISSING (DC-AS-003)."""
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
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.DEPENDENCY_MISSING

    def test_ffmpeg_missing_dependency_missing(self, tmp_path: Path) -> None:
        """ffmpeg absent -> DEPENDENCY_MISSING (raised by resolve_tool; DC-AS-004)."""
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
                    message="ffmpeg not found",
                    hint="Install ffmpeg.",
                ),
            ),
        ):
            result = transcribe_media(media, output, _opts(model_path=model))
        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.DEPENDENCY_MISSING

    def test_resolve_model_path_param_priority(self, tmp_path: Path) -> None:
        """model_path (param) is returned when the file exists."""
        model = tmp_path / "m.bin"
        model.write_bytes(b"x")
        resolved = _resolve_model_path(_opts(model_path=str(model)))
        assert resolved == str(model)

    def test_resolve_model_path_env_fallback(self, tmp_path: Path) -> None:
        """Falls back to env CLIPWRIGHT_WHISPER_MODEL when model_path is not set."""
        model = tmp_path / "env.bin"
        model.write_bytes(b"x")
        with patch.dict(os.environ, {"CLIPWRIGHT_WHISPER_MODEL": str(model)}):
            resolved = _resolve_model_path(_opts())
        assert resolved == str(model)

    def test_resolve_model_path_missing_raises(self, tmp_path: Path) -> None:
        """Non-existent param file and no env -> DEPENDENCY_MISSING."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLIPWRIGHT_WHISPER_MODEL", None)
            with pytest.raises(ClipwrightError) as exc_info:
                _resolve_model_path(_opts(model_path=str(tmp_path / "nope.bin")))
        assert exc_info.value.code == ErrorCode.DEPENDENCY_MISSING


# ===========================================================================
# ④ OTIO construction (full-length 1 clip + segment markers)
# ===========================================================================


class TestOtioConstruction:
    def _run(
        self, tmp_path: Path, segments: list[Segment], language: str = "en"
    ) -> tuple[ToolResult, otio.schema.Timeline]:
        media, output, model = _make_paths(tmp_path)
        with (
            patch(
                "clipwright_transcribe.transcribe.inspect_media",
                return_value=_make_media_info(media),
            ),
            patch(
                "clipwright_transcribe.transcribe._run_whisper",
                return_value=_whisper_run(segments, language=language),
            ),
        ):
            result = transcribe_media(media, output, _opts(model_path=model))
        timeline = otio.adapters.read_from_file(output)
        return result, timeline

    def test_full_clip_present(self, tmp_path: Path) -> None:
        """V1 contains a full-length single clip (kind=transcript-source,
        start_time=0)."""
        result, timeline = self._run(tmp_path, [_seg(0.0, 1.0, "hi")])
        assert result.ok is True
        v1 = timeline.tracks[0]
        clips = [c for c in v1 if isinstance(c, otio.schema.Clip)]
        assert len(clips) == 1
        clip = clips[0]
        cw = clip.metadata["clipwright"]
        assert cw["kind"] == "transcript-source"
        assert cw["tool"] == "clipwright-transcribe"
        # source_range.start_time == 0
        assert clip.source_range.start_time.value == pytest.approx(0.0)
        # Full duration (10s × 30fps = 300)
        assert clip.source_range.duration.value == pytest.approx(300.0)

    def test_markers_on_v1_track(self, tmp_path: Path) -> None:
        """Each segment is attached as a marker on the V1 track (DC-AM-101)."""
        segs = [_seg(0.0, 1.2, "Hello"), _seg(1.5, 2.8, "World")]
        _result, timeline = self._run(tmp_path, segs)
        v1 = timeline.tracks[0]
        assert len(v1.markers) == 2

    def test_marker_metadata_caption(self, tmp_path: Path) -> None:
        """Marker metadata contains kind=caption, text, and language."""
        _result, timeline = self._run(tmp_path, [_seg(0.0, 1.2, "Hello")], "ja")
        marker = timeline.tracks[0].markers[0]
        cw = marker.metadata["clipwright"]
        assert cw["kind"] == "caption"
        assert cw["text"] == "Hello"
        assert cw["language"] == "ja"

    def test_marker_marked_range_uses_whisper_seconds(self, tmp_path: Path) -> None:
        """marker.marked_range uses whisper second values directly (DC-AM-001,
        RationalTime comparison).

        start=1.5s, rate=30 -> value=45.0. Strict RationalTime comparison avoids
        float approximation.
        """
        _result, timeline = self._run(tmp_path, [_seg(1.5, 2.8, "x")])
        marker = timeline.tracks[0].markers[0]
        expected_start = otio.opentime.RationalTime(1.5 * FPS, FPS)
        expected_dur = otio.opentime.RationalTime((2.8 - 1.5) * FPS, FPS)
        assert marker.marked_range.start_time == expected_start
        assert marker.marked_range.duration == expected_dur

    def test_marker_time_matches_srt_seconds(self, tmp_path: Path) -> None:
        """Marker second values and SRT timecodes share the same origin (DC-AS-005)."""
        media, output, model = _make_paths(tmp_path)
        with (
            patch(
                "clipwright_transcribe.transcribe.inspect_media",
                return_value=_make_media_info(media),
            ),
            patch(
                "clipwright_transcribe.transcribe._run_whisper",
                return_value=_whisper_run([_seg(1.5, 2.8, "x")]),
            ),
        ):
            transcribe_media(media, output, _opts(model_path=model))
        timeline = otio.adapters.read_from_file(output)
        marker = timeline.tracks[0].markers[0]
        # marker start = 1.5s -> SRT "00:00:01,500"
        start_sec = (
            marker.marked_range.start_time.value / marker.marked_range.start_time.rate
        )
        assert start_sec == pytest.approx(1.5)
        srt = Path(output).with_suffix(".srt").read_text(encoding="utf-8")
        assert "00:00:01,500" in srt

    def test_marker_name_truncated(self, tmp_path: Path) -> None:
        """Long-text segment marker name is truncated to 40 chars; full text is in
        metadata.text (DC-GP-003)."""
        # Japanese characters are used intentionally to verify multi-byte length
        # counting (ii: test data — do not translate).
        long_text = "あ" * 60
        _result, timeline = self._run(tmp_path, [_seg(0.0, 1.0, long_text)])
        marker = timeline.tracks[0].markers[0]
        assert len(marker.name) <= _MARKER_NAME_MAX + 1  # +1 for ellipsis character
        assert marker.name.startswith("あ" * 40)
        assert marker.metadata["clipwright"]["text"] == long_text


# ===========================================================================
# ⑤ DC-GP-002 Zero segments
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
                return_value=_whisper_run([]),
            ),
        ):
            result = transcribe_media(media, output, _opts(model_path=model))
        assert result.ok is True
        assert result.warnings  # zero-segment warning present
        assert result.data["segment_count"] == 0
        timeline = otio.adapters.read_from_file(output)
        v1 = timeline.tracks[0]
        # 0 markers, but the full-length clip is present
        assert len(v1.markers) == 0
        assert len([c for c in v1 if isinstance(c, otio.schema.Clip)]) == 1
        # SRT empty, VTT header only
        srt = Path(output).with_suffix(".srt").read_text(encoding="utf-8")
        vtt = Path(output).with_suffix(".vtt").read_text(encoding="utf-8")
        assert srt == ""
        assert vtt.strip() == "WEBVTT"


# ===========================================================================
# ⑥ Outputs and envelope
# ===========================================================================


class TestEnvelopeAndOutputs:
    def _run(self, tmp_path: Path) -> tuple[ToolResult, str, str]:
        media, output, model = _make_paths(tmp_path)
        segs = [_seg(0.0, 1.2, "Hello"), _seg(1.5, 2.8, "World")]
        with (
            patch(
                "clipwright_transcribe.transcribe.inspect_media",
                return_value=_make_media_info(media),
            ),
            patch(
                "clipwright_transcribe.transcribe._run_whisper",
                return_value=_whisper_run(segs),
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
        roles = {(a.role, a.format) for a in result.artifacts}
        assert ("timeline", "otio") in roles
        assert ("captions", "srt") in roles
        assert ("captions", "vtt") in roles
        assert len(result.artifacts) == 3

    def test_summary_contains_language_count_duration(self, tmp_path: Path) -> None:
        result, _output, _media = self._run(tmp_path)
        summary = result.summary
        assert summary is not None
        assert "en" in summary
        assert "2" in summary  # segment count

    def test_data_lightweight(self, tmp_path: Path) -> None:
        result, _output, _media = self._run(tmp_path)
        data = result.data
        assert data["segment_count"] == 2
        assert data["language"] == "en"
        assert "total_duration_seconds" in data
        # Full segment list must not be embedded in data
        assert "segments" not in data


# ===========================================================================
# _run_whisper adapter unit tests (resolve_tool / run mocked)
# ===========================================================================


class TestRunWhisperAdapter:
    def _fake_resolve(self) -> Any:
        def _impl(name: str, env_var: str | None = None) -> str:
            return f"/bin/{name}"

        return _impl

    def _fake_run_writes_json(self, json_text: str) -> Any:
        """Return a run mock that writes <prefix>.json when called for whisper."""

        def _impl(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "-of" in cmd:
                prefix = cmd[cmd.index("-of") + 1]
                Path(prefix + ".json").write_text(json_text, encoding="utf-8")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        return _impl

    def _make_capture_run(
        self,
        captured: dict[str, list[str]],
        json_body: str = '{"transcription": []}',
    ) -> Any:
        """Return a run mock that captures the whisper command and writes <prefix>.json
        (CR L-4 DRY)."""

        def _impl(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "-of" in cmd:
                captured["whisper"] = cmd
                prefix = cmd[cmd.index("-of") + 1]
                Path(prefix + ".json").write_text(json_body, encoding="utf-8")
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        return _impl

    def test_success_returns_segments_and_language(
        self, tmp_path: Path, whisper_sample_json: dict[str, Any]
    ) -> None:
        # Depends on hypothetical fixture (fixtures/README.md, whisper_sample.json)
        # result.language=='en'. Replace with real values after e2e (DC-GP-001-R).
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
            run = _run_whisper("video.mp4", _opts(), 10.0, str(model))
        assert len(run.segments) == 3
        assert run.language == "en"

    def test_language_auto_flag_when_none(self, tmp_path: Path) -> None:
        """language=None causes each LANG_AUTO_FLAG token to appear in cmd (DC-AM-002).

        LANG_AUTO_FLAG is list[str]; verifies that "-l" and "auto" appear in order.
        """
        captured: dict[str, list[str]] = {}
        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=self._fake_resolve(),
            ),
            patch(
                "clipwright_transcribe.transcribe.run",
                side_effect=self._make_capture_run(captured),
            ),
        ):
            _run_whisper("video.mp4", _opts(language=None), 10.0, "m.bin")
        cmd = captured["whisper"]
        # LANG_AUTO_FLAG must be list[str] (CR M-1 / SR M-2)
        assert LANG_AUTO_FLAG == ["-l", "auto"]
        for token in LANG_AUTO_FLAG:
            assert token in cmd

    def test_language_explicit_flag(self, tmp_path: Path) -> None:
        captured: dict[str, list[str]] = {}
        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=self._fake_resolve(),
            ),
            patch(
                "clipwright_transcribe.transcribe.run",
                side_effect=self._make_capture_run(captured),
            ),
        ):
            _run_whisper("video.mp4", _opts(language="ja"), 10.0, "m.bin")
        cmd = captured["whisper"]
        assert "-l" in cmd
        assert "ja" in cmd
        assert "auto" not in cmd

    def test_initial_prompt_flag(self, tmp_path: Path) -> None:
        captured: dict[str, list[str]] = {}
        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=self._fake_resolve(),
            ),
            patch(
                "clipwright_transcribe.transcribe.run",
                side_effect=self._make_capture_run(captured),
            ),
        ):
            _run_whisper("video.mp4", _opts(initial_prompt="clipwright"), 10.0, "m.bin")
        cmd = captured["whisper"]
        assert "--prompt" in cmd
        assert "clipwright" in cmd

    def test_subprocess_failure_sanitized(self, tmp_path: Path) -> None:
        """ffmpeg/whisper SUBPROCESS_FAILED stderr is sanitised (TR-AD-09)."""
        leak = "/secret/path/to/model stderr leak"

        def _raise_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=f"Command failed: {leak}",
                hint="Check the input.",
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
        assert "internal subprocess" in exc_info.value.message

    def test_whisper_run_failure_sanitized(self, tmp_path: Path) -> None:
        """ffmpeg success + whisper run failure is also sanitised (L215-216)."""
        leak = "/secret/whisper stderr"

        def _run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "-of" in cmd:  # fail on the whisper invocation
                raise ClipwrightError(
                    code=ErrorCode.SUBPROCESS_TIMEOUT,
                    message=f"timeout: {leak}",
                    hint="Check the input.",
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
        """Non-subprocess ClipwrightError is returned unchanged (L88)."""
        from clipwright_transcribe.transcribe import _sanitize_subprocess_error

        original = ClipwrightError(
            code=ErrorCode.INVALID_INPUT, message="msg", hint="hint"
        )
        result = _sanitize_subprocess_error(original)
        assert result is original

    def test_json_read_failure_subprocess_failed(self, tmp_path: Path) -> None:
        """When whisper produces no JSON, SUBPROCESS_FAILED is raised (SR L-3).

        The message must not contain any path (no path exposure; from None chain).
        """

        def _run_no_json(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            # Do not write JSON
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
        err = exc_info.value
        assert err.code == ErrorCode.SUBPROCESS_FAILED
        # message must not contain path fragments (SR L-3 path non-exposure)
        assert "/" not in err.message
        assert "\\" not in err.message

    def test_whisper_binary_name_constant_used(self, tmp_path: Path) -> None:
        """resolve_tool is called with the WHISPER_BINARY_NAME constant (DC-AS-003)."""
        names: list[str] = []

        def _track_resolve(name: str, env_var: str | None = None) -> str:
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

    def test_returns_whisper_run_type(self, tmp_path: Path) -> None:
        """_run_whisper returns a WhisperRun instance (not a plain tuple)."""
        model = tmp_path / "m.bin"
        model.write_bytes(b"x")
        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=self._fake_resolve(),
            ),
            patch(
                "clipwright_transcribe.transcribe.run",
                side_effect=self._fake_run_writes_json('{"transcription": []}'),
            ),
        ):
            result = _run_whisper("video.mp4", _opts(), 10.0, str(model))
        assert isinstance(result, WhisperRun)

    def test_wall_seconds_non_negative(self, tmp_path: Path) -> None:
        """WhisperRun.wall_seconds is >= 0 (time.monotonic difference; DC-AM-001)."""
        model = tmp_path / "m.bin"
        model.write_bytes(b"x")
        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=self._fake_resolve(),
            ),
            patch(
                "clipwright_transcribe.transcribe.run",
                side_effect=self._fake_run_writes_json('{"transcription": []}'),
            ),
        ):
            result = _run_whisper("video.mp4", _opts(), 10.0, str(model))
        # Allow zero: on very fast machines the monotonic delta may be 0.0.
        # Do NOT change to > 0 — that would make the test flaky on fast hosts.
        assert result.wall_seconds >= 0

    def test_backend_device_cuda_from_stderr(self, tmp_path: Path) -> None:
        """_detect_backend is driven at mock boundary: CUDA stderr -> backend["device"]
        == "cuda" (ADR-7', DC-GP-002)."""
        model = tmp_path / "m.bin"
        model.write_bytes(b"x")
        cuda_stderr = "ggml_cuda_init: found 1 CUDA devices"

        def _run_cuda_stderr(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "-of" in cmd:
                prefix = cmd[cmd.index("-of") + 1]
                Path(prefix + ".json").write_text(
                    '{"transcription": []}', encoding="utf-8"
                )
            return CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=cuda_stderr
            )

        with (
            patch(
                "clipwright_transcribe.transcribe.resolve_tool",
                side_effect=self._fake_resolve(),
            ),
            patch(
                "clipwright_transcribe.transcribe.run",
                side_effect=_run_cuda_stderr,
            ),
        ):
            result = _run_whisper("video.mp4", _opts(), 10.0, str(model))
        assert result.backend["device"] == "cuda"


# ===========================================================================
# Backend detection unit tests
# ===========================================================================


class TestDetectBackend:
    """Unit tests for _detect_backend(whisper_json, whisper_stderr).

    Verifies stderr-only detection logic per ADR-1' (v2 AUTHORITY):
    - CPU paths are authoritative (verified against real whisper.cpp v1.8.6 CPU build).
    - CUDA/Metal are best-effort (sourced from whisper.cpp known init messages).
    - Lines containing "use gpu" / "gpu_device" are excluded before matching
      (F3 trap: these appear on CPU builds too and must NOT trigger "cuda").
    - Any exception is caught and "unknown" is returned (NFR-3).
    """

    @pytest.mark.parametrize(
        "whisper_json, stderr, expected_device",
        [
            # CPU authoritative — "no GPU found" result line (real whisper.cpp v1.8.6)
            (
                {},
                "whisper_backend_init_gpu: no GPU found",
                "cpu",
            ),
            # CPU authoritative — "device 0: CPU" result line (alternate CPU output)
            (
                {},
                "whisper_backend_init_gpu: device 0: CPU (type: 0)",
                "cpu",
            ),
            # use gpu TRAP (F3) — request params printed even on CPU; no result line
            # => must NOT be detected as cuda (regression guard)
            (
                {},
                (
                    "whisper_init_with_params_no_state: use gpu    = 1\n"
                    "whisper_init_with_params_no_state: gpu_device = 0"
                ),
                "unknown",
            ),
            # use gpu TRAP + CPU result line — exclude param lines, match result line
            (
                {},
                (
                    "whisper_init_with_params_no_state: use gpu    = 1\n"
                    "whisper_backend_init_gpu: no GPU found"
                ),
                "cpu",
            ),
            # CUDA best-effort — ggml_cuda_init line
            (
                {},
                "ggml_cuda_init: found 1 CUDA devices",
                "cuda",
            ),
            # Metal best-effort — ggml_metal_init line
            (
                {},
                "ggml_metal_init: allocating",
                "metal",
            ),
            # Empty stderr => unknown
            (
                {},
                "",
                "unknown",
            ),
            # None stderr => unknown (exception non-propagation)
            (
                {},
                None,
                "unknown",
            ),
            # Non-dict whisper_json (list) => unknown (exception non-propagation)
            (
                [],
                "whisper_backend_init_gpu: no GPU found",
                "unknown",
            ),
        ],
        ids=[
            "cpu_no_gpu_found",
            "cpu_device0_cpu",
            "use_gpu_trap_no_result_line_must_be_unknown_not_cuda",
            "use_gpu_trap_plus_cpu_result",
            "cuda_best_effort",
            "metal_best_effort",
            "empty_stderr",
            "none_stderr",
            "non_dict_json",
        ],
    )
    def test_detect_backend_device(
        self,
        whisper_json: Any,
        stderr: str | None,
        expected_device: str,
    ) -> None:
        from clipwright_transcribe.transcribe import _detect_backend

        result = _detect_backend(whisper_json, stderr)
        assert result["device"] == expected_device

    def test_use_gpu_trap_must_not_be_cuda(self) -> None:
        """Explicit regression guard: use gpu = 1 alone must NOT produce 'cuda'."""
        from clipwright_transcribe.transcribe import _detect_backend

        stderr = (
            "whisper_init_with_params_no_state: use gpu    = 1\n"
            "whisper_init_with_params_no_state: gpu_device = 0"
        )
        result = _detect_backend({}, stderr)
        assert result["device"] != "cuda", (
            "use gpu/gpu_device lines are CPU request params — "
            "must not be misdetected as cuda"
        )

    def test_detect_backend_returns_device_and_detail_keys(self) -> None:
        """Return value always contains 'device' and 'detail' keys."""
        from clipwright_transcribe.transcribe import _detect_backend

        result = _detect_backend({}, "")
        assert "device" in result
        assert "detail" in result

    def test_detect_backend_no_exception_on_broken_input(self) -> None:
        """Any broken input must not propagate an exception (NFR-3)."""
        from clipwright_transcribe.transcribe import _detect_backend

        # Passing an integer as whisper_json and a non-string as stderr
        result = _detect_backend(42, object())  # type: ignore[arg-type]
        assert result["device"] == "unknown"

    def test_device_init_beyond_64kb_boundary_not_detected(self) -> None:
        """Device init line placed after the 64KB scan limit is not detected (SR2-R-002).

        _STDERR_SCAN_MAX_CHARS = 65536. Padding the first 65536+ chars with
        irrelevant content and placing the CUDA init line in the tail ensures
        the scan window excludes it, so the result must be "unknown".
        """
        from clipwright_transcribe.transcribe import (
            _STDERR_SCAN_MAX_CHARS,
            _detect_backend,
        )

        # Fill the scan window with harmless content, then append the init line
        padding = "x " * (_STDERR_SCAN_MAX_CHARS + 10)
        stderr = padding + "ggml_cuda_init: found 1 CUDA devices"
        result = _detect_backend({}, stderr)
        assert result["device"] == "unknown", (
            "Device init line beyond the 64KB scan limit must not be detected "
            f"(_STDERR_SCAN_MAX_CHARS={_STDERR_SCAN_MAX_CHARS})"
        )

    def test_cpu_result_within_64kb_boundary_detected(self) -> None:
        """CPU result line within the 64KB scan window is correctly detected (SR2-R-002).

        Places a CPU init line at the very beginning of stderr (well within 64KB),
        confirming the scan window picks it up.
        """
        from clipwright_transcribe.transcribe import _detect_backend

        stderr = "whisper_backend_init_gpu: no GPU found"
        result = _detect_backend({}, stderr)
        assert result["device"] == "cpu", (
            "CPU result line within the scan window must be detected as 'cpu'"
        )


class TestSanitizeDetail:
    """Unit tests for _sanitize_detail(raw) — CWE-209 path/control-char stripping.

    Per ADR-2' (v2 AUTHORITY): _sanitize_detail is a defense-in-depth layer.
    In production, detail contains only fixed device labels (no raw stderr).
    These tests verify the sanitisation function itself with synthetic inputs
    that mirror real whisper.cpp stderr (F4 real string confirmed).
    """

    def test_path_tokens_removed(self) -> None:
        """Tokens containing '/' must be stripped (CWE-209 path exposure)."""
        from clipwright_transcribe.transcribe import _sanitize_detail

        # Mirrors real whisper.cpp stderr line (F4):
        # "loading model from 'C:/Users/.../ggml-large-v3-turbo.bin'"
        raw = "loading model from 'C:/Users/user/ggml-large-v3-turbo.bin'"
        result = _sanitize_detail(raw)
        assert "/" not in result
        assert "\\" not in result

    def test_backslash_path_tokens_removed(self) -> None:
        """Tokens containing '\\' (Windows paths) must also be stripped."""
        from clipwright_transcribe.transcribe import _sanitize_detail

        raw = r"model path: C:\Users\user\ggml-large-v3-turbo.bin"
        result = _sanitize_detail(raw)
        assert "\\" not in result

    def test_control_chars_stripped(self) -> None:
        """Control characters \\x00–\\x1f and \\x7f must be removed."""
        from clipwright_transcribe.transcribe import _sanitize_detail

        raw = "CUDA\x00\x01\x1f\x7f"
        result = _sanitize_detail(raw)
        for ch in result:
            assert ord(ch) >= 0x20 and ord(ch) != 0x7F, (
                f"Control character U+{ord(ch):04X} found in sanitised output"
            )

    def test_length_capped_at_80(self) -> None:
        """Output must not exceed 80 characters."""
        from clipwright_transcribe.transcribe import _sanitize_detail

        raw = "x" * 200
        result = _sanitize_detail(raw)
        assert len(result) <= 80

    def test_normal_label_unchanged(self) -> None:
        """Safe fixed labels (CUDA / Metal / cpu) pass through unchanged."""
        from clipwright_transcribe.transcribe import _sanitize_detail

        for label in ("CUDA", "Metal", "cpu", ""):
            result = _sanitize_detail(label)
            assert result == label.strip()


class TestComputeRealtimeFactor:
    """Unit tests for _compute_realtime_factor(total_duration_sec, wall_seconds).

    Per ADR-4' (v2 AUTHORITY): returns float | None.
    wall <= 0 must return None — NOT 0.0 (DC-AM-001).
    """

    def test_normal_calculation(self) -> None:
        """Realtime factor = total / wall, rounded to 2 decimal places."""
        from clipwright_transcribe.transcribe import _compute_realtime_factor

        result = _compute_realtime_factor(10.0, 2.0)
        assert result == pytest.approx(5.0)

    def test_rounding_two_decimal_places(self) -> None:
        """Result is rounded to 2 decimal places."""
        from clipwright_transcribe.transcribe import _compute_realtime_factor

        # 10.0 / 3.0 = 3.333... -> 3.33
        result = _compute_realtime_factor(10.0, 3.0)
        assert result == pytest.approx(3.33)

    def test_wall_zero_returns_none(self) -> None:
        """wall_seconds == 0.0 must return None, not 0.0 (DC-AM-001)."""
        from clipwright_transcribe.transcribe import _compute_realtime_factor

        result = _compute_realtime_factor(10.0, 0.0)
        assert result is None, "wall=0 must return None, not 0.0"

    def test_wall_negative_returns_none(self) -> None:
        """wall_seconds < 0 must return None (DC-AM-001 guard)."""
        from clipwright_transcribe.transcribe import _compute_realtime_factor

        result = _compute_realtime_factor(10.0, -1.0)
        assert result is None, "wall<0 must return None, not 0.0"

    def test_wall_zero_is_not_zero_float(self) -> None:
        """Explicit regression: wall=0 must not produce the value 0.0."""
        from clipwright_transcribe.transcribe import _compute_realtime_factor

        result = _compute_realtime_factor(5.0, 0.0)
        assert result != 0.0, (
            "Returning 0.0 for wall=0 causes AI to misread it as '0x realtime'; "
            "None is required (DC-AM-001)"
        )

    def test_total_zero_returns_none(self) -> None:
        """total_duration_sec == 0.0 must return None (degenerate input guard)."""
        from clipwright_transcribe.transcribe import _compute_realtime_factor

        result = _compute_realtime_factor(0.0, 2.0)
        assert result is None, "total=0 must return None"

    def test_total_negative_returns_none(self) -> None:
        """total_duration_sec < 0 must return None (degenerate input guard)."""
        from clipwright_transcribe.transcribe import _compute_realtime_factor

        result = _compute_realtime_factor(-1.0, 2.0)
        assert result is None, "total<0 must return None"

    # --- nan/inf guards (SR2-R-001) ---

    def test_wall_nan_returns_none(self) -> None:
        """wall_seconds=nan must return None (isfinite guard; SR2-R-001)."""
        from clipwright_transcribe.transcribe import _compute_realtime_factor

        result = _compute_realtime_factor(10.0, float("nan"))
        assert result is None, "wall=nan must return None"

    def test_wall_inf_returns_none(self) -> None:
        """wall_seconds=inf must return None (isfinite guard; SR2-R-001)."""
        from clipwright_transcribe.transcribe import _compute_realtime_factor

        result = _compute_realtime_factor(10.0, float("inf"))
        assert result is None, "wall=inf must return None"

    def test_total_nan_returns_none(self) -> None:
        """total_duration_sec=nan must return None (isfinite guard; SR2-R-001)."""
        from clipwright_transcribe.transcribe import _compute_realtime_factor

        result = _compute_realtime_factor(float("nan"), 2.0)
        assert result is None, "total=nan must return None"

    def test_total_inf_returns_none(self) -> None:
        """total_duration_sec=inf must return None (isfinite guard; SR2-R-001)."""
        from clipwright_transcribe.transcribe import _compute_realtime_factor

        result = _compute_realtime_factor(float("inf"), 2.0)
        assert result is None, "total=inf must return None"
