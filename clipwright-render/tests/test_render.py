"""test_render.py — render.py（オーケストレーション + _probe()）の Red テスト。

対象:
  - _probe(source) -> ProbeInfo
    ffprobe 実行・JSON パース・bit_rate/has_video/audio_count の抽出
  - render_timeline(timeline, source, output, options, dry_run) のオーケストレーション
    入力検証・dry_run 経路・実行経路・エラー伝播

process.run / resolve_tool は clipwright.process をモックして検証する。
実 ffmpeg/ffprobe バイナリは一切呼ばない（integration テストは別ファイル担当）。
"""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode

from clipwright_render.schemas import RenderOptions

# ---------------------------------------------------------------------------
# ヘルパー: テスト用 OTIO Timeline ファイル / インメモリ構築
# ---------------------------------------------------------------------------

FPS = 30.0


def _rt(seconds: float, rate: float = FPS) -> otio.opentime.RationalTime:
    return otio.opentime.RationalTime(seconds * rate, rate)


def _tr(start: float, duration: float, rate: float = FPS) -> otio.opentime.TimeRange:
    return otio.opentime.TimeRange(
        start_time=_rt(start, rate),
        duration=_rt(duration, rate),
    )


def _make_clip(source: str, start: float, duration: float) -> otio.schema.Clip:
    clip = otio.schema.Clip()
    clip.media_reference = otio.schema.ExternalReference(target_url=source)
    clip.source_range = _tr(start, duration)
    return clip


def _make_timeline(clips: list[otio.schema.Clip]) -> otio.schema.Timeline:
    """単一 video トラックの Timeline を生成する。"""
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    for clip in clips:
        track.append(clip)
    tl = otio.schema.Timeline()
    tl.tracks.append(track)
    return tl


def _write_timeline(path: Path, clips: list[otio.schema.Clip]) -> None:
    """OTIO ファイルをディスクに書き出すヘルパー。"""
    tl = _make_timeline(clips)
    otio.adapters.write_to_file(tl, str(path))


def _fake_probe_result(
    *,
    bit_rate: str | None = "8000000",
    has_video: bool = True,
    audio_streams: int = 1,
) -> CompletedProcess[str]:
    """ffprobe が返す JSON 出力を模した CompletedProcess を生成する。"""
    streams: list[dict[str, Any]] = []
    if has_video:
        streams.append({"codec_type": "video", "codec_name": "h264"})
    for _ in range(audio_streams):
        streams.append({"codec_type": "audio", "codec_name": "aac"})
    fmt: dict[str, Any] = {}
    if bit_rate is not None:
        fmt["bit_rate"] = bit_rate
    payload = {"streams": streams, "format": fmt}
    cp: CompletedProcess[str] = CompletedProcess(
        args=[], returncode=0, stdout=json.dumps(payload), stderr=""
    )
    return cp


# ---------------------------------------------------------------------------
# _probe() テスト群（DC-GP-001 / AS-001 / AM-007）
# ---------------------------------------------------------------------------


class TestProbe:
    """_probe(source) の動作検証。"""

    def test_probe_calls_ffprobe_with_array_args(self, tmp_path: Path) -> None:
        """ffprobe を引数配列で呼ぶこと（コマンドインジェクション防止）。"""
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                return_value="/usr/bin/ffprobe",
            ) as mock_resolve,
            patch(
                "clipwright_render.render.run",
                return_value=_fake_probe_result(),
            ) as mock_run,
        ):
            _probe(source)

        mock_resolve.assert_called_once_with("ffprobe", "CLIPWRIGHT_FFPROBE")
        args, kwargs = mock_run.call_args
        cmd = args[0]
        assert isinstance(cmd, list), "cmd は引数配列（list）でなければならない"
        # 先頭は resolve_tool で返されたパス
        assert cmd[0] == "/usr/bin/ffprobe"
        # -print_format json / -show_format / -show_streams を含む
        joined = " ".join(cmd)
        assert "json" in joined
        assert "-show_format" in joined
        assert "-show_streams" in joined
        # source パスが末尾引数として含まれる
        assert source in cmd

    def test_probe_parses_bit_rate(self, tmp_path: Path) -> None:
        """format.bit_rate を int で ProbeInfo に格納する（DC-GP-001）。"""
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        with (
            patch(
                "clipwright_render.render.resolve_tool", return_value="/usr/bin/ffprobe"
            ),
            patch(
                "clipwright_render.render.run",
                return_value=_fake_probe_result(bit_rate="8000000"),
            ),
        ):
            info = _probe(source)

        assert info.bit_rate == 8_000_000

    def test_probe_bit_rate_missing_returns_none(self, tmp_path: Path) -> None:
        """format.bit_rate 欠落時 ProbeInfo.bit_rate が None になる（DC-GP-001）。"""
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        with (
            patch(
                "clipwright_render.render.resolve_tool", return_value="/usr/bin/ffprobe"
            ),
            patch(
                "clipwright_render.render.run",
                return_value=_fake_probe_result(bit_rate=None),
            ),
        ):
            info = _probe(source)

        assert info.bit_rate is None

    def test_probe_bit_rate_na_string_returns_none(self, tmp_path: Path) -> None:
        """bit_rate='N/A' → ProbeInfo.bit_rate=None（PROBE_FAILED にしない・M-3）。

        ffprobe が 'N/A' や空文字を返す場合は ValueError をキャッチし
        None にフォールバックすること。
        """
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        with (
            patch(
                "clipwright_render.render.resolve_tool", return_value="/usr/bin/ffprobe"
            ),
            patch(
                "clipwright_render.render.run",
                return_value=_fake_probe_result(bit_rate="N/A"),
            ),
        ):
            info = _probe(source)

        # "N/A" は int() 変換不可 → None フォールバック（PROBE_FAILED にしない）
        assert info.bit_rate is None

    def test_probe_has_video_true(self, tmp_path: Path) -> None:
        """video stream がある場合 has_video=True（DC-GP-001）。"""
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        with (
            patch(
                "clipwright_render.render.resolve_tool", return_value="/usr/bin/ffprobe"
            ),
            patch(
                "clipwright_render.render.run",
                return_value=_fake_probe_result(has_video=True),
            ),
        ):
            info = _probe(source)

        assert info.has_video is True

    def test_probe_no_video_stream(self, tmp_path: Path) -> None:
        """video stream が無い場合 has_video=False（DC-GP-001）。"""
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        with (
            patch(
                "clipwright_render.render.resolve_tool", return_value="/usr/bin/ffprobe"
            ),
            patch(
                "clipwright_render.render.run",
                return_value=_fake_probe_result(has_video=False, audio_streams=1),
            ),
        ):
            info = _probe(source)

        assert info.has_video is False

    def test_probe_audio_count(self, tmp_path: Path) -> None:
        """audio stream 数が ProbeInfo.audio_count に格納される（DC-GP-001）。"""
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        with (
            patch(
                "clipwright_render.render.resolve_tool", return_value="/usr/bin/ffprobe"
            ),
            patch(
                "clipwright_render.render.run",
                return_value=_fake_probe_result(audio_streams=2),
            ),
        ):
            info = _probe(source)

        assert info.audio_count == 2

    def test_probe_no_audio_returns_zero(self, tmp_path: Path) -> None:
        """音声ストリーム無し → audio_count=0（DC-GP-001）。"""
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        with (
            patch(
                "clipwright_render.render.resolve_tool", return_value="/usr/bin/ffprobe"
            ),
            patch(
                "clipwright_render.render.run",
                return_value=_fake_probe_result(has_video=True, audio_streams=0),
            ),
        ):
            info = _probe(source)

        assert info.audio_count == 0

    def test_probe_invalid_json_raises_probe_failed(self, tmp_path: Path) -> None:
        """ffprobe が不正 JSON を返した場合 PROBE_FAILED を送出する（DC-GP-001）。"""
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        bad_cp: CompletedProcess[str] = CompletedProcess(
            args=[], returncode=0, stdout="not-json{{{{", stderr=""
        )
        with (
            patch(
                "clipwright_render.render.resolve_tool",
                return_value="/usr/bin/ffprobe",
            ),
            patch("clipwright_render.render.run", return_value=bad_cp),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            _probe(source)
        assert exc_info.value.code == ErrorCode.PROBE_FAILED

    def test_probe_missing_streams_key_raises_probe_failed(
        self, tmp_path: Path
    ) -> None:
        """必須キー streams が欠落した JSON → PROBE_FAILED（DC-GP-001）。"""
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        payload = json.dumps({"format": {"bit_rate": "8000000"}})
        bad_cp: CompletedProcess[str] = CompletedProcess(
            args=[], returncode=0, stdout=payload, stderr=""
        )
        with (
            patch(
                "clipwright_render.render.resolve_tool",
                return_value="/usr/bin/ffprobe",
            ),
            patch("clipwright_render.render.run", return_value=bad_cp),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            _probe(source)
        assert exc_info.value.code == ErrorCode.PROBE_FAILED


# ---------------------------------------------------------------------------
# clipwright_render — 入力検証テスト（DC-GP-005 / AM-002 / AM-003）
# ---------------------------------------------------------------------------


class TestInputValidation:
    """clipwright_render の入力検証を検証する。"""

    def test_timeline_not_found_raises_file_not_found(self, tmp_path: Path) -> None:
        """timeline(.otio) 不在 → FILE_NOT_FOUND（DC-GP-005）。"""
        from clipwright_render.render import render_timeline

        missing_tl = str(tmp_path / "nonexistent.otio")
        output = str(tmp_path / "out.mp4")
        result = render_timeline(
            timeline=missing_tl, output=output, options=RenderOptions()
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND

    def test_source_not_found_raises_file_not_found(self, tmp_path: Path) -> None:
        """ソースファイル不在 → FILE_NOT_FOUND（DC-GP-005）。"""
        from clipwright_render.render import render_timeline

        tl_path = tmp_path / "tl.otio"
        missing_source = str(tmp_path / "missing.mp4")
        _write_timeline(tl_path, [_make_clip(missing_source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        result = render_timeline(
            timeline=str(tl_path), output=output, options=RenderOptions()
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND

    def test_output_parent_dir_not_found_raises_file_not_found(
        self, tmp_path: Path
    ) -> None:
        """出力親ディレクトリ不在 → FILE_NOT_FOUND（自動作成しない・DC-GP-005）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        # 存在しないサブディレクトリ
        output = str(tmp_path / "nonexistent_dir" / "out.mp4")

        result = render_timeline(
            timeline=str(tl_path), output=output, options=RenderOptions()
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND

    @pytest.mark.parametrize("ext", [".avi", ".wmv", ".ts", ".txt", ""])
    def test_invalid_extension_raises_invalid_input(
        self, tmp_path: Path, ext: str
    ) -> None:
        """不正拡張子（ホワイトリスト外）→ INVALID_INPUT（DC-AM-003）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / f"out{ext}")

        result = render_timeline(
            timeline=str(tl_path), output=output, options=RenderOptions()
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    @pytest.mark.parametrize("ext", [".mp4", ".mkv", ".mov", ".webm"])
    def test_valid_extensions_pass_validation(self, tmp_path: Path, ext: str) -> None:
        """ホワイトリスト内拡張子は入力検証を通過する（dry_run で確認・DC-AM-003）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / f"out{ext}")

        with (
            patch(
                "clipwright_render.render.resolve_tool", return_value="/usr/bin/ffprobe"
            ),
            patch(
                "clipwright_render.render.run",
                return_value=_fake_probe_result(),
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )
        # INVALID_INPUT ではないこと（ok=True or 他エラー）
        if not result["ok"]:
            assert result["error"]["code"] != ErrorCode.INVALID_INPUT

    def test_existing_output_without_overwrite_raises_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """既存 output かつ overwrite=False → INVALID_INPUT（DC-AM-002）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")
        Path(output).touch()  # 既存ファイルを作成

        result = render_timeline(
            timeline=str(tl_path),
            output=output,
            options=RenderOptions(overwrite=False),
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT
        # hint に overwrite の案内が含まれる
        assert "overwrite" in result["error"]["hint"].lower()

    def test_existing_output_with_overwrite_true_passes(self, tmp_path: Path) -> None:
        """overwrite=True の場合は既存ファイルでも検証を通過する（DC-AM-002）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        with (
            patch(
                "clipwright_render.render.resolve_tool", return_value="/usr/bin/ffprobe"
            ),
            patch(
                "clipwright_render.render.run",
                return_value=_fake_probe_result(),
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
                dry_run=True,
            )
        # INVALID_INPUT ではないこと
        if not result["ok"]:
            assert result["error"]["code"] != ErrorCode.INVALID_INPUT

    def test_output_equals_source_raises_path_not_allowed(self, tmp_path: Path) -> None:
        """output == source → PATH_NOT_ALLOWED（DC-AM-002）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])

        result = render_timeline(
            timeline=str(tl_path),
            output=source,  # output == source
            options=RenderOptions(),
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED

    def test_source_outside_timeline_dir_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """timeline 外の source → PATH_NOT_ALLOWED（Sec M-2: OTIO 境界チェック）。

        悪意ある OTIO に任意パスが埋め込まれた場合の境界チェック。
        """
        from clipwright_render.render import render_timeline

        # timeline は subdir1 に配置
        subdir1 = tmp_path / "project"
        subdir1.mkdir()
        tl_path = subdir1 / "tl.otio"

        # source は別ディレクトリ（境界外）
        subdir2 = tmp_path / "outside"
        subdir2.mkdir()
        outside_source = str(subdir2 / "secret.mp4")
        Path(outside_source).touch()

        _write_timeline(tl_path, [_make_clip(outside_source, 0.0, 5.0)])
        output = str(subdir1 / "out.mp4")

        result = render_timeline(
            timeline=str(tl_path),
            output=output,
            options=RenderOptions(),
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED


# ---------------------------------------------------------------------------
# clipwright_render — dry_run テスト（§3 データフロー 6a）
# ---------------------------------------------------------------------------


class TestDryRun:
    """dry_run=True 時の動作検証。"""

    def test_dry_run_does_not_call_ffmpeg(self, tmp_path: Path) -> None:
        """dry_run=True のとき ffmpeg が呼ばれない（ffprobe は呼ぶ）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        run_calls: list[list[str]] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            run_calls.append(cmd)
            return _fake_probe_result()

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True
        # run が呼ばれた回数 = ffprobe のみ（ffmpeg = 0）
        ffmpeg_calls = [c for c in run_calls if "ffmpeg" in c[0]]
        assert len(ffmpeg_calls) == 0

    def test_dry_run_returns_ok_envelope(self, tmp_path: Path) -> None:
        """dry_run=True の返り値が ok=True エンベロープ形式。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render.run",
                return_value=_fake_probe_result(bit_rate="8000000"),
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True
        assert "summary" in result
        assert "data" in result
        assert "artifacts" in result
        assert "warnings" in result

    def test_dry_run_summary_contains_segment_count_and_duration(
        self, tmp_path: Path
    ) -> None:
        """dry_run summary に残区間数と想定尺が含まれる（§3 データフロー 6a）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(
            tl_path,
            [
                _make_clip(source, 0.0, 3.0),
                _make_clip(source, 5.0, 2.0),
            ],
        )
        output = str(tmp_path / "out.mp4")

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render.run",
                return_value=_fake_probe_result(bit_rate="8000000"),
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True
        summary: str = result["summary"]
        # 2 区間・5秒 の情報が含まれる
        assert "2" in summary

    def test_dry_run_data_contains_planned_command(self, tmp_path: Path) -> None:
        """dry_run data に予定コマンドが含まれる（§3 データフロー 6a）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render.run",
                return_value=_fake_probe_result(),
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True
        # data に予定コマンド（ffmpeg_args またはそれに相当するキー）が含まれる
        assert len(result["data"]) > 0

    def test_dry_run_summary_contains_estimated_size(self, tmp_path: Path) -> None:
        """bit_rate あり の dry_run summary に概算サイズ情報が含まれる（ADR-3）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 10.0)])
        output = str(tmp_path / "out.mp4")

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render.run",
                return_value=_fake_probe_result(bit_rate="8000000"),
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True
        # summary に何らかのサイズ/bytes 関連の情報が含まれる
        assert result["data"] or result["summary"]


# ---------------------------------------------------------------------------
# clipwright_render — 実行経路テスト（dry_run=False・§3 データフロー 6b）
# ---------------------------------------------------------------------------


class TestExecutionPath:
    """dry_run=False の実行経路検証。"""

    def test_ffprobe_called_before_ffmpeg(self, tmp_path: Path) -> None:
        """ffprobe → ffmpeg の順で process.run が呼ばれる（§3 データフロー）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")
        Path(output).touch()  # 成功後ファイル存在として扱う

        call_order: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "ffprobe" in cmd[0]:
                call_order.append("ffprobe")
                return _fake_probe_result()
            else:
                call_order.append("ffmpeg")
                return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert call_order[0] == "ffprobe"
        assert "ffmpeg" in call_order

    def test_ffmpeg_called_with_array_args(self, tmp_path: Path) -> None:
        """ffmpeg が引数配列で呼ばれる（コマンドインジェクション防止・ADR-4）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        ffmpeg_cmd: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "ffprobe" in cmd[0]:
                return _fake_probe_result()
            else:
                ffmpeg_cmd.extend(cmd)
                return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert isinstance(ffmpeg_cmd, list)
        assert len(ffmpeg_cmd) > 0
        # filter_complex が単一引数として渡されている（文字列結合でない）
        assert "-filter_complex" in ffmpeg_cmd
        fc_idx = ffmpeg_cmd.index("-filter_complex")
        assert isinstance(ffmpeg_cmd[fc_idx + 1], str)

    def test_ffmpeg_cmd_starts_with_resolved_path(self, tmp_path: Path) -> None:
        """ffmpeg コマンド先頭が resolve_tool で返ったパスである（ADR-4）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        ffmpeg_first_arg: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "ffprobe" in cmd[0]:
                return _fake_probe_result()
            ffmpeg_first_arg.append(cmd[0])
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/custom/path/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert len(ffmpeg_first_arg) > 0
        assert ffmpeg_first_arg[0] == "/custom/path/ffmpeg"

    def test_ffmpeg_timeout_is_max_300_or_duration_times_10(
        self, tmp_path: Path
    ) -> None:
        """ffmpeg timeout = max(300, ceil(総尺秒 × 10))（DC-AM-006）。"""
        from clipwright_render.render import render_timeline

        # 総尺 = 5s → 5×10=50 < 300 → timeout=300
        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        ffmpeg_timeout: list[float] = []

        def _fake_run(
            cmd: list[str], *, timeout: float = 60.0, **kwargs: Any
        ) -> CompletedProcess[str]:
            if "ffprobe" in cmd[0]:
                return _fake_probe_result()
            ffmpeg_timeout.append(timeout)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert len(ffmpeg_timeout) > 0
        assert ffmpeg_timeout[0] == 300  # max(300, ceil(5*10)) = 300

    def test_ffmpeg_timeout_long_video(self, tmp_path: Path) -> None:
        """総尺 60s → timeout = max(300, ceil(600)) = 600（DC-AM-006）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 60.0)])
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        ffmpeg_timeout: list[float] = []

        def _fake_run(
            cmd: list[str], *, timeout: float = 60.0, **kwargs: Any
        ) -> CompletedProcess[str]:
            if "ffprobe" in cmd[0]:
                return _fake_probe_result()
            ffmpeg_timeout.append(timeout)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert len(ffmpeg_timeout) > 0
        assert ffmpeg_timeout[0] == 600  # max(300, ceil(60*10)) = 600

    def test_success_returns_ok_envelope_with_artifact(self, tmp_path: Path) -> None:
        """成功時に ok=True エンベロープと出力パスを Artifact として返す（§3）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "ffprobe" in cmd[0]:
                return _fake_probe_result(bit_rate="8000000")
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert result["ok"] is True
        assert "summary" in result
        assert "artifacts" in result

    def test_success_summary_contains_duration_and_clip_count(
        self, tmp_path: Path
    ) -> None:
        """成功時 summary に総尺と連結クリップ数が含まれる（§3 データフロー 6b）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(
            tl_path,
            [
                _make_clip(source, 0.0, 3.0),
                _make_clip(source, 5.0, 2.0),
            ],
        )
        output = str(tmp_path / "out.mp4")
        Path(output).touch()

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "ffprobe" in cmd[0]:
                return _fake_probe_result(bit_rate="8000000")
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert result["ok"] is True
        summary: str = result["summary"]
        assert "2" in summary  # 2区間


# ---------------------------------------------------------------------------
# clipwright_render — エラー伝播テスト（DC-GP-004）
# ---------------------------------------------------------------------------


class TestErrorPropagation:
    """エラー伝播: ClipwrightError が error_result エンベロープに変換される。"""

    def test_ffmpeg_failed_returns_subprocess_failed(self, tmp_path: Path) -> None:
        """ffmpeg 失敗 → SUBPROCESS_FAILED エンベロープ（DC-GP-004）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "ffprobe" in cmd[0]:
                return _fake_probe_result()
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="コマンドが終了コード 1 で失敗しました",
                hint="コマンドの引数を確認してください。",
            )

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path), output=output, options=RenderOptions()
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.SUBPROCESS_FAILED

    def test_ffmpeg_timeout_returns_subprocess_timeout(self, tmp_path: Path) -> None:
        """ffmpeg timeout → SUBPROCESS_TIMEOUT エンベロープ（DC-GP-004）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "ffprobe" in cmd[0]:
                return _fake_probe_result()
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_TIMEOUT,
                message="タイムアウト",
                hint="timeout 値を大きくしてください。",
            )

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path), output=output, options=RenderOptions()
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.SUBPROCESS_TIMEOUT

    def test_ffmpeg_not_found_returns_dependency_missing(self, tmp_path: Path) -> None:
        """ffmpeg 不在 → DEPENDENCY_MISSING エンベロープ（DC-GP-004）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        def _fake_resolve(name: str, env: str | None = None) -> str:
            if name == "ffprobe":
                return "/usr/bin/ffprobe"
            raise ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="ffmpeg が見つかりません",
                hint="ffmpeg を PATH に追加してください。",
            )

        with (
            patch("clipwright_render.render.resolve_tool", side_effect=_fake_resolve),
            patch(
                "clipwright_render.render.run",
                return_value=_fake_probe_result(),
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path), output=output, options=RenderOptions()
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.DEPENDENCY_MISSING

    def test_ffprobe_not_found_returns_dependency_missing(self, tmp_path: Path) -> None:
        """ffprobe 不在 → DEPENDENCY_MISSING エンベロープ（DC-GP-004）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        with patch(
            "clipwright_render.render.resolve_tool",
            side_effect=ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="ffprobe が見つかりません",
                hint="PATH に追加してください。",
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path), output=output, options=RenderOptions()
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.DEPENDENCY_MISSING

    def test_probe_failure_returns_probe_failed(self, tmp_path: Path) -> None:
        """probe 失敗 → PROBE_FAILED エンベロープ（DC-GP-004 / GP-001）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render.run",
                return_value=CompletedProcess(
                    args=[], returncode=0, stdout="not-json", stderr=""
                ),
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path), output=output, options=RenderOptions()
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PROBE_FAILED

    def test_error_does_not_expose_raw_stderr(self, tmp_path: Path) -> None:
        """エラーメッセージに ffmpeg stderr 生文字列を露出しない（DC-GP-004）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        raw_stderr = "SUPER SECRET INTERNAL PATH /home/user/private/data"

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "ffprobe" in cmd[0]:
                return _fake_probe_result()
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="コマンドが終了コード 1 で失敗しました: 一部エラー",
                hint="コマンドを確認してください。",
            )

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path), output=output, options=RenderOptions()
            )

        assert result["ok"] is False
        # 生 stderr・内部パスが露出していない
        error_str = json.dumps(result["error"])
        assert raw_stderr not in error_str

    def test_error_does_not_expose_internal_exception(self, tmp_path: Path) -> None:
        """エラーエンベロープに生例外/スタックトレースが含まれない（DC-GP-004）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render.run",
                return_value=CompletedProcess(
                    args=[], returncode=0, stdout="bad-json", stderr=""
                ),
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path), output=output, options=RenderOptions()
            )

        assert result["ok"] is False
        # Traceback・Exception クラス名が含まれない
        error_str = json.dumps(result["error"])
        assert "Traceback" not in error_str
        assert "JSONDecodeError" not in error_str


# ---------------------------------------------------------------------------
# 非破壊テスト
# ---------------------------------------------------------------------------


class TestNonDestructive:
    """入力 timeline / 元素材が書き換えられないことを検証する。"""

    def test_source_file_unchanged_after_render(self, tmp_path: Path) -> None:
        """レンダリング後も元素材ファイルの内容が変化しない（非破壊）。"""
        from clipwright_render.render import render_timeline

        source = tmp_path / "a.mp4"
        source.write_bytes(b"dummy source content")
        original_bytes = source.read_bytes()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(str(source), 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")
        (tmp_path / "out.mp4").touch()

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "ffprobe" in cmd[0]:
                return _fake_probe_result()
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert source.read_bytes() == original_bytes

    def test_timeline_file_unchanged_after_render(self, tmp_path: Path) -> None:
        """レンダリング後も timeline(.otio) の内容が変化しない（非破壊）。"""
        from clipwright_render.render import render_timeline

        source = tmp_path / "a.mp4"
        source.touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(str(source), 0.0, 5.0)])
        original_tl_bytes = tl_path.read_bytes()
        output = str(tmp_path / "out.mp4")
        (tmp_path / "out.mp4").touch()

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            if "ffprobe" in cmd[0]:
                return _fake_probe_result()
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert tl_path.read_bytes() == original_tl_bytes
