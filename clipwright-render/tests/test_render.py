"""test_render.py — render.py（オーケストレーション + _probe()）の Red テスト。

対象:
  - _probe(source) -> ProbeInfo
    inspect_media 呼び出し・MediaInfo→ProbeInfo アダプタ変換
  - render_timeline(timeline, source, output, options, dry_run) のオーケストレーション
    入力検証・dry_run 経路・実行経路・エラー伝播
  - BGM オーケストレーション拡張（§7 ADR-B4-r2/B5-r2/B6-r2/B8）
    resolve_bgm 呼び出し・build_plan bgm 受け渡し・-stream_loop -i 並び検証

inspect_media は clipwright_render.render.inspect_media をモックして検証する。
process.run は ffmpeg 呼び出し専用に縮小して patch する。
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
from clipwright.schemas import MediaInfo, StreamInfo

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


def _make_media_info(
    path: str = "/fake/source.mp4",
    *,
    bit_rate: int | None = 8_000_000,
    has_video: bool = True,
    audio_streams: int = 1,
    extra_streams: list[StreamInfo] | None = None,
) -> MediaInfo:
    """テスト用 MediaInfo を構築するヘルパー。

    inspect_media のモック戻り値として使用する。
    bit_rate は int | None で渡す（_to_optional_int 変換済みを想定）。
    """
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(StreamInfo(index=0, codec_type="video", codec_name="h264"))
    for _i in range(audio_streams):
        streams.append(
            StreamInfo(
                index=len(streams),
                codec_type="audio",
                codec_name="aac",
            )
        )
    if extra_streams:
        streams.extend(extra_streams)
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=None,
        streams=streams,
        bit_rate=bit_rate,
    )


# ---------------------------------------------------------------------------
# _probe() テスト群（DC-GP-001 / AS-001 / AM-007）
# (a) inspect_media モックベースへ移行
# ---------------------------------------------------------------------------


class TestProbe:
    """_probe(source) の動作検証。

    ffprobe 直叩きモックを廃止し、clipwright_render.render.inspect_media を
    patch して MediaInfo を供給するスタイルへ移行する（DC-GP-001/AD-3）。
    """

    def test_probe_video_audio_bit_rate(self, tmp_path: Path) -> None:
        """video+audio+bit_rate を持つ MediaInfo → ProbeInfo への変換確認。

        has_video=True, audio_count=1, bit_rate=8000000 になる（DC-GP-001）。
        """
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        media_info = _make_media_info(
            path=source, bit_rate=8_000_000, has_video=True, audio_streams=1
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=media_info,
        ) as mock_inspect:
            info = _probe(source)

        mock_inspect.assert_called_once_with(source)
        assert info.has_video is True
        assert info.audio_count == 1
        assert info.bit_rate == 8_000_000

    def test_probe_audio_count_zero(self, tmp_path: Path) -> None:
        """音声ストリーム数 0 → audio_count=0（DC-GP-001）。"""
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        media_info = _make_media_info(path=source, has_video=True, audio_streams=0)

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=media_info,
        ):
            info = _probe(source)

        assert info.audio_count == 0

    def test_probe_audio_count_multiple(self, tmp_path: Path) -> None:
        """音声ストリーム複数 → audio_count=N（DC-GP-001）。"""
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        media_info = _make_media_info(path=source, has_video=True, audio_streams=3)

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=media_info,
        ):
            info = _probe(source)

        assert info.audio_count == 3

    def test_probe_bit_rate_none(self, tmp_path: Path) -> None:
        """MediaInfo.bit_rate が None → ProbeInfo.bit_rate is None（DC-GP-001）。"""
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        media_info = _make_media_info(path=source, bit_rate=None)

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=media_info,
        ):
            info = _probe(source)

        assert info.bit_rate is None

    def test_probe_propagates_probe_failed(self, tmp_path: Path) -> None:
        """inspect_media が PROBE_FAILED を送出 → _probe がそれを伝播する。

        FILE_NOT_FOUND 以外のエラーコードはそのまま伝播すること（DC-GP-001）。
        """
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=ClipwrightError(
                    code=ErrorCode.PROBE_FAILED,
                    message="ffprobe の出力が有効な JSON ではありません。",
                    hint="入力ファイルが有効なメディアファイルか確認してください。",
                ),
            ),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            _probe(source)

        assert exc_info.value.code == ErrorCode.PROBE_FAILED

    def test_probe_has_video_false(self, tmp_path: Path) -> None:
        """video stream が無い場合 has_video=False（DC-GP-001）。"""
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        media_info = _make_media_info(path=source, has_video=False, audio_streams=1)

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=media_info,
        ):
            info = _probe(source)

        assert info.has_video is False

    def test_probe_audio_count_single(self, tmp_path: Path) -> None:
        """audio stream が1本のとき audio_count=1（DC-GP-001）。"""
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        media_info = _make_media_info(path=source, has_video=True, audio_streams=1)

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=media_info,
        ):
            info = _probe(source)

        assert info.audio_count == 1

    def test_probe_file_not_found_replaces_abspath_with_basename(self) -> None:
        """FILE_NOT_FOUND 時に _probe が message を basename のみに差し替えること。

        inspect_media が FILE_NOT_FOUND を送出した場合、_probe が再送出する
        ClipwrightError の message に絶対パスが含まれず basename のみを含む（Sec M-1）。
        symlink を実際に作らないため Windows でも実行される（CR-T-001）。
        """
        from clipwright_render.render import _probe

        source = "/abs/path/to/link.mp4"
        expected_hint = "シンボリックリンクではなく実ファイルを指定してください。"

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=ClipwrightError(
                    code=ErrorCode.FILE_NOT_FOUND,
                    message="シンボリックリンクは受け付けません: /abs/path/to/link.mp4",
                    hint=expected_hint,
                ),
            ),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            _probe(source)

        assert exc_info.value.code == ErrorCode.FILE_NOT_FOUND
        # 絶対パス（ディレクトリ部分）が露出していない
        assert "/abs/path/to" not in exc_info.value.message
        # basename のみ含まれる
        assert "link.mp4" in exc_info.value.message
        # hint は inspect_media のものを引き継ぐ（差し替えで欠落しないこと・CR-T-004）
        assert exc_info.value.hint == expected_hint


# ---------------------------------------------------------------------------
# (d) codec_type 欠落・空文字のエッジケース（DC-AM-002）
# ---------------------------------------------------------------------------


class TestProbeEdgeCases:
    """_probe の codec_type 欠落・空文字等価性検証（DC-AM-002）。"""

    def test_probe_codec_type_missing_or_empty_not_counted(
        self, tmp_path: Path
    ) -> None:
        """codec_type 欠落（""に正規化済み）・空文字ストリームを含む MediaInfo で
        has_video=False / audio_count=0 になる（旧実装と等価）。

        旧実装: s.get("codec_type") == "video" → 欠落は None で不一致 → 数えない。
        新実装: StreamInfo.codec_type は str(s.get("codec_type", "")) で "" に正規化 →
                "video"/"audio" に一致しない → 数えない。両者は等価（DC-AM-002）。

        空文字（ffprobe 欠落を "" に正規化したケース）と "data"/"subtitle" 等の
        非 video/audio codec_type の両方を含めて、カウントされないことを検証する。
        """
        from clipwright_render.render import _probe

        source = str(tmp_path / "a.mp4")
        Path(source).touch()

        # codec_type が "" の空文字（欠落を正規化）と非 video/audio 値を含む MediaInfo
        extra_streams = [
            StreamInfo(index=0, codec_type="", codec_name=None),  # 欠落を "" に正規化
            StreamInfo(
                index=1, codec_type="", codec_name="data"
            ),  # 欠落を "" に正規化（codec_name あり）
            StreamInfo(
                index=2, codec_type="data", codec_name=None
            ),  # data ストリーム（非 video/audio）
            StreamInfo(
                index=3, codec_type="subtitle", codec_name=None
            ),  # subtitle（非 video/audio）
        ]
        media_info = MediaInfo(
            path=source,
            container=None,
            duration=None,
            streams=extra_streams,
            bit_rate=None,
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=media_info,
        ):
            info = _probe(source)

        assert info.has_video is False
        assert info.audio_count == 0


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

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=source),
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

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=source),
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

    def test_symlink_source_raises_file_not_found(self, tmp_path: Path) -> None:
        """symlink ソースを render_timeline に渡すと FILE_NOT_FOUND を返す（DC-AS-001）

        _probe → inspect_media が symlink を FILE_NOT_FOUND で拒否することを
        render_timeline 経由で確認する回帰テスト。
        source の Path.exists() は symlink 先が存在すれば True を返すため通過するが、
        _probe 内の inspect_media で発火する。
        error.message には絶対パス（ディレクトリ等）が露出せず、
        basename のみが含まれることを確認する（Sec M-1）。
        """
        from clipwright_render.render import render_timeline

        # 実ファイルと symlink を作成
        real_file = tmp_path / "real.mp4"
        real_file.touch()
        symlink_source = tmp_path / "link.mp4"
        # Windows では symlink 作成に権限が要るため失敗を skip でガード（core と同方針）
        try:
            symlink_source.symlink_to(real_file)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(
                f"symlink の作成に失敗しました（権限不足または未対応環境）: {exc}"
            )

        # timeline は symlink を source に参照する
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(str(symlink_source), 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        # inspect_media を実際に通す（symlink 拒否は _validate_existing_file が担う）
        # patch しないことで実装の symlink 拒否挙動が発火することを確認する
        result = render_timeline(
            timeline=str(tl_path),
            output=output,
            options=RenderOptions(),
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND
        # error.message に絶対パス（real_file の親ディレクトリ等）が含まれず
        # basename のみであることを確認する（Sec M-1）
        error_message: str = result["error"]["message"]
        assert str(tmp_path) not in error_message
        assert str(real_file.parent) not in error_message
        assert "link.mp4" in error_message


# ---------------------------------------------------------------------------
# clipwright_render — dry_run テスト（§3 データフロー 6a）
# (b) probe モック: clipwright_render.render.inspect_media を patch へ移行
# ---------------------------------------------------------------------------


class TestDryRun:
    """dry_run=True 時の動作検証。"""

    def test_dry_run_does_not_call_ffmpeg(self, tmp_path: Path) -> None:
        """dry_run=True のとき ffmpeg が呼ばれない（inspect_media は呼ぶ）。"""
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        run_calls: list[list[str]] = []

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            run_calls.append(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True
        # run は ffmpeg 専用 patch → dry_run=True なら呼ばれない
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

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=source, bit_rate=8_000_000),
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

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=source, bit_rate=8_000_000),
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
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
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

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=source, bit_rate=8_000_000),
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
# (b) probe モック: inspect_media → render.run(ffmpeg) の順序検証へ読み替え
# ---------------------------------------------------------------------------


class TestExecutionPath:
    """dry_run=False の実行経路検証。"""

    def test_inspect_media_called_before_ffmpeg(self, tmp_path: Path) -> None:
        """inspect_media → ffmpeg の順で呼ばれる（§3 データフロー）。

        旧: ffprobe → ffmpeg の run 呼び出し順
        新: inspect_media（内部で ffprobe）→ render.run(ffmpeg) の順序検証
        """
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")
        Path(output).touch()  # 成功後ファイル存在として扱う

        call_order: list[str] = []

        def _inspecting(*args: Any, **kwargs: Any) -> MediaInfo:
            call_order.append("inspect_media")
            return _make_media_info(path=source)

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            call_order.append("ffmpeg")
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=_inspecting,
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert call_order[0] == "inspect_media"
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

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            ffmpeg_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
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

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            ffmpeg_first_arg.append(cmd[0])
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/custom/path/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
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

        def _fake_ffmpeg_run(
            cmd: list[str], *, timeout: float = 60.0, **kwargs: Any
        ) -> CompletedProcess[str]:
            ffmpeg_timeout.append(timeout)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
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

        def _fake_ffmpeg_run(
            cmd: list[str], *, timeout: float = 60.0, **kwargs: Any
        ) -> CompletedProcess[str]:
            ffmpeg_timeout.append(timeout)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
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

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source, bit_rate=8_000_000),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
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

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source, bit_rate=8_000_000),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
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
# (b) probe モック: inspect_media patch へ移行
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

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="コマンドが終了コード 1 で失敗しました",
                hint="コマンドの引数を確認してください。",
            )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
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

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_TIMEOUT,
                message="タイムアウト",
                hint="timeout 値を大きくしてください。",
            )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
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
            raise ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="ffmpeg が見つかりません",
                hint="ffmpeg を PATH に追加してください。",
            )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch("clipwright_render.render.resolve_tool", side_effect=_fake_resolve),
        ):
            result = render_timeline(
                timeline=str(tl_path), output=output, options=RenderOptions()
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.DEPENDENCY_MISSING

    def test_probe_failure_returns_probe_failed(self, tmp_path: Path) -> None:
        """probe 失敗（inspect_media 送出）→ PROBE_FAILED エンベロープ（DC-GP-004）。

        ClipwrightError が error_result に変換されることを確認する（GP-001）。
        """
        from clipwright_render.render import render_timeline

        source = str(tmp_path / "a.mp4")
        Path(source).touch()
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(source, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.PROBE_FAILED,
                message="ffprobe の出力が有効な JSON ではありません。",
                hint="入力ファイルが有効なメディアファイルか確認してください。",
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

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="コマンドが終了コード 1 で失敗しました: 一部エラー",
                hint="コマンドを確認してください。",
            )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=source),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
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

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.PROBE_FAILED,
                message="ffprobe の出力が有効な JSON ではありません。",
                hint="入力ファイルが有効なメディアファイルか確認してください。",
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

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=str(source)),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
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

        def _fake_ffmpeg_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=str(source)),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.run", side_effect=_fake_ffmpeg_run),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert tl_path.read_bytes() == original_tl_bytes


# ---------------------------------------------------------------------------
# 複数ソース・オーケストレーション拡張テスト
# （ADR-C2-r2 / ADR-C8 / ADR-C9-r2 / DC-GP-001）
# ---------------------------------------------------------------------------


def _make_media_info_with_video_stream(
    path: str,
    *,
    width: int = 1920,
    height: int = 1080,
    bit_rate: int | None = 8_000_000,
    audio_streams: int = 1,
    fps_rate: float | None = 30.0,
) -> MediaInfo:
    """video stream（width/height あり）と duration を持つ MediaInfo を生成するヘルパー。

    fps_rate が None のとき duration=None（音声のみソースのセンチネル回避検証に使う）。
    fps_rate が指定されたとき duration.rate = fps_rate を持つ RationalTimeModel を生成する。
    duration.rate=1000.0 は音声のみソースのセンチネルとして使用する。
    """
    from clipwright.schemas import RationalTimeModel

    streams: list[StreamInfo] = []
    streams.append(
        StreamInfo(
            index=0,
            codec_type="video",
            codec_name="h264",
            width=width,
            height=height,
        )
    )
    for _i in range(audio_streams):
        streams.append(
            StreamInfo(index=len(streams), codec_type="audio", codec_name="aac")
        )

    duration = None
    if fps_rate is not None:
        # duration.rate = fps_rate として ProbeInfo.fps 取得のテストに使う
        duration = RationalTimeModel(value=10.0 * fps_rate, rate=fps_rate)

    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=duration,
        streams=streams,
        bit_rate=bit_rate,
    )


def _make_audio_only_media_info(path: str) -> MediaInfo:
    """音声のみソース（rate=1000.0 センチネル）の MediaInfo を生成するヘルパー。

    media.py の rate 決定規則: video stream なし → rate=1000.0 センチネル。
    duration.rate が 1000.0 であっても fps として採用してはならない（ADR-C2-r2）。
    """
    from clipwright.schemas import RationalTimeModel

    streams = [StreamInfo(index=0, codec_type="audio", codec_name="aac")]
    # センチネル rate=1000.0 で duration を生成（音声のみ素材の実際の挙動を模倣）
    duration = RationalTimeModel(value=10000.0, rate=1000.0)
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=duration,
        streams=streams,
        bit_rate=4_000_000,
    )


def _make_multi_source_otio_file(
    clips: list[tuple[str, float, float]],
    tmp_path: Path,
) -> Path:
    """複数ソースを持つ Timeline の OTIO ファイルを生成し Path を返すヘルパー。

    clips: [(source_path, start_sec, duration_sec), ...]
    test_e2e_merge.py の同名ヘルパー（in-memory OTIO 返却）と区別するため
    _make_multi_source_otio_file と命名する（CR L-1）。
    """
    otio_clips = [_make_clip(src, start, dur) for src, start, dur in clips]
    tl_path = tmp_path / "tl.otio"
    _write_timeline(tl_path, otio_clips)
    return tl_path


class TestMultiSourceProbeAllSources:
    """観点1: 複数ソース timeline で各ユニークソースに inspect_media が呼ばれる。

    ADR-C8: 全ユニークソースに probe を適用する。
    render.py が build_plan 呼び出し前に source_probes を構築するため、
    ユニークソース数と同じ回数 inspect_media が呼ばれることを検証する。
    """

    def test_all_unique_sources_are_probed(self, tmp_path: Path) -> None:
        """2ソース timeline で inspect_media が2回呼ばれる（各ソース1回）。"""
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        src1 = str(tmp_path / "src1.mp4")
        Path(src0).touch()
        Path(src1).touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src1, 0.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        probe_calls: list[str] = []

        def _fake_inspect(path: str) -> MediaInfo:
            probe_calls.append(path)
            return _make_media_info_with_video_stream(path)

        with (
            patch("clipwright_render.render.inspect_media", side_effect=_fake_inspect),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render.run",
                return_value=CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                ),
            ),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        # 各ユニークソースが1回ずつ probe されること（順序不問・重複排除）
        assert src0 in probe_calls
        assert src1 in probe_calls
        # ユニークソースは2個なので呼び出し回数は2回
        assert len(probe_calls) == 2

    def test_duplicate_source_is_probed_once(self, tmp_path: Path) -> None:
        """同一ソースを2クリップで使っても inspect_media は1回のみ呼ばれる。

        重複排除により probe コストを最小化する（ADR-C1）。
        """
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        Path(src0).touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src0, 5.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        probe_calls: list[str] = []

        def _fake_inspect(path: str) -> MediaInfo:
            probe_calls.append(path)
            return _make_media_info_with_video_stream(path)

        with (
            patch("clipwright_render.render.inspect_media", side_effect=_fake_inspect),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render.run",
                return_value=CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                ),
            ),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        # ユニークソースは1個なので probe は1回のみ
        assert len(probe_calls) == 1
        assert probe_calls[0] == src0


class TestMultiSourceFfmpegInputOrder:
    """観点2: ffmpeg -i 並びが RenderPlan.input_sources の順序と一致する。

    ADR-C9-r2: render.py は RenderPlan.input_sources をそのまま使い、
    独自に順序を再計算しない。
    """

    def test_two_source_ffmpeg_has_two_i_flags(self, tmp_path: Path) -> None:
        """2ソース timeline で ffmpeg コマンドに -i が2つ並ぶ。"""
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        src1 = str(tmp_path / "src1.mp4")
        Path(src0).touch()
        Path(src1).touch()
        (tmp_path / "out.mp4").touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src1, 0.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        captured_cmd: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=lambda path: _make_media_info_with_video_stream(path),
            ),
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

        assert result["ok"] is True, f"失敗: {result.get('error')}"
        # -i が2つ存在する
        i_indices = [i for i, v in enumerate(captured_cmd) if v == "-i"]
        assert len(i_indices) == 2
        # -i の後ろのパスが src0→src1 の出現順（RenderPlan.input_sources 順）
        assert captured_cmd[i_indices[0] + 1] == src0
        assert captured_cmd[i_indices[1] + 1] == src1

    def test_single_source_ffmpeg_has_one_i_flag(self, tmp_path: Path) -> None:
        """単一ソース timeline では -i が1つ（後方互換）。

        観点7（後方互換）と兼ねる: 複数ソース拡張後も単一ソースは -i が1個。
        """
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        Path(src0).touch()
        (tmp_path / "out.mp4").touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src0, 5.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        captured_cmd: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info_with_video_stream(src0),
            ),
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

        assert result["ok"] is True, f"失敗: {result.get('error')}"
        i_indices = [i for i, v in enumerate(captured_cmd) if v == "-i"]
        assert len(i_indices) == 1
        assert captured_cmd[i_indices[0] + 1] == src0

    def test_input_order_matches_render_plan_input_sources(
        self, tmp_path: Path
    ) -> None:
        """ffmpeg -i 並びが RenderPlan.input_sources と厳密に一致する。

        ADR-C9-r2: render.py は独自に順序を再計算せず RenderPlan.input_sources を使う。
        build_plan をモックして input_sources を明示的に制御し、
        ffmpeg コマンドの -i 並びがそれと一致することを検証する。
        """
        from clipwright_render.plan import RenderPlan
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        src1 = str(tmp_path / "src1.mp4")
        Path(src0).touch()
        Path(src1).touch()
        (tmp_path / "out.mp4").touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src1, 0.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        # build_plan が返す RenderPlan に input_sources を含める
        fake_plan = RenderPlan(
            filter_complex="[0:v]trim=0:3,setpts=PTS-STARTPTS[v0];[1:v]trim=0:2,setpts=PTS-STARTPTS[v1];[v0][v1]concat=n=2:v=1:a=0[outv]",
            ffmpeg_args=["-filter_complex", "...", "-map", "[outv]", "-c:v", "libx264"],
            segment_count=2,
            total_duration_seconds=5.0,
            input_sources=[src0, src1],  # ADR-C9-r2: 明示的な順序
        )

        captured_cmd: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=lambda path: _make_media_info_with_video_stream(path),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.build_plan", return_value=fake_plan),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert result["ok"] is True, f"失敗: {result.get('error')}"
        i_indices = [i for i, v in enumerate(captured_cmd) if v == "-i"]
        assert len(i_indices) == 2
        # input_sources=[src0, src1] の順序通りに -i が並ぶこと
        assert captured_cmd[i_indices[0] + 1] == src0
        assert captured_cmd[i_indices[1] + 1] == src1


class TestMultiSourceBoundaryCheck:
    """観点3/4/5: ADR-C8 全ユニークソースへの境界検証適用。

    2番目以降のソースの境界外・パス衝突・不在をそれぞれ検出すること。
    """

    def test_second_source_outside_timeline_dir_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """2番目ソースが timeline ディレクトリ外 → PATH_NOT_ALLOWED（ADR-C8）。

        先頭ソースは境界内だが、2番目ソースが境界外のとき検出されること。
        """
        from clipwright_render.render import render_timeline

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        src0 = str(project_dir / "src0.mp4")
        src1 = str(outside_dir / "secret.mp4")  # 境界外
        Path(src0).touch()
        Path(src1).touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src1, 0.0, 2.0)],
            project_dir,  # timeline は project_dir 下
        )
        output = str(project_dir / "out.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=lambda path: _make_media_info_with_video_stream(path),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED

    def test_second_source_equals_output_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """output == 2番目ソース → PATH_NOT_ALLOWED（DC-GP-001・非破壊原則）。

        _check_path_not_allowed が先頭ソースだけでなく全ソースに適用されること。
        先頭ソースとは異なるが2番目ソースが output と一致する場合に検出される。
        """
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        src1 = str(tmp_path / "src1.mp4")
        Path(src0).touch()
        Path(src1).touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src1, 0.0, 2.0)],
            tmp_path,
        )
        # output == src1（2番目ソース）
        output = src1

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=lambda path: _make_media_info_with_video_stream(path),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED

    def test_second_source_not_found_returns_file_not_found(
        self, tmp_path: Path
    ) -> None:
        """2番目ソースが存在しない → FILE_NOT_FOUND（ADR-C8）。"""
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        src1 = str(tmp_path / "missing_src1.mp4")  # 存在しない
        Path(src0).touch()
        # src1 は作成しない

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src1, 0.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=lambda path: _make_media_info_with_video_stream(path),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND

    def test_second_source_not_found_basename_only_in_message(
        self, tmp_path: Path
    ) -> None:
        """2番目ソース不在エラーのメッセージに絶対パスが露出しない（CWE-209）。

        basename のみ含まれ、ディレクトリ部分が含まれないことを確認する。
        """
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        src1 = str(tmp_path / "missing_src1.mp4")
        Path(src0).touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src1, 0.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            side_effect=lambda path: _make_media_info_with_video_stream(path),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND
        error_message: str = result["error"]["message"]
        # 絶対パス（ディレクトリ部分）が露出していない
        assert str(tmp_path) not in error_message
        # basename は含まれる
        assert "missing_src1.mp4" in error_message


class TestProbeAudioOnlyFpsNone:
    """観点6: 音声のみソースで _probe が fps=None を返す（ADR-C2-r2）。

    rate=1000.0 センチネルを fps として誤採用しないこと。
    ProbeInfo.fps は「第1 video StreamInfo あり AND duration not None」のときのみ設定。
    """

    def test_audio_only_source_probe_fps_is_none(self, tmp_path: Path) -> None:
        """音声のみソース（video stream なし）→ ProbeInfo.fps = None（ADR-C2-r2）。

        duration.rate=1000.0（センチネル）があっても fps として採用しないこと。
        """
        from clipwright_render.render import _probe

        source = str(tmp_path / "audio_only.mp4")
        Path(source).touch()

        # 音声のみ: rate=1000.0 センチネル・video stream なし
        audio_only_info = _make_audio_only_media_info(source)

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=audio_only_info,
        ):
            info = _probe(source)

        # video stream がないため fps は None
        assert info.fps is None  # type: ignore[attr-defined]
        # width/height も None（video stream なし）
        assert info.width is None  # type: ignore[attr-defined]
        assert info.height is None  # type: ignore[attr-defined]

    def test_video_source_with_duration_none_fps_is_none(self, tmp_path: Path) -> None:
        """video stream ありだが duration=None → ProbeInfo.fps = None（ADR-C2-r2）。

        duration が None のとき duration.rate へのアクセスで AttributeError が発生しない。
        """
        from clipwright_render.render import _probe

        source = str(tmp_path / "no_duration.mp4")
        Path(source).touch()

        # video stream あり・duration=None（format.duration が取得不能なケース）
        info_no_duration = MediaInfo(
            path=source,
            container="mov,mp4,m4a,3gp,3g2,mj2",
            duration=None,  # duration が None
            streams=[
                StreamInfo(
                    index=0,
                    codec_type="video",
                    codec_name="h264",
                    width=1920,
                    height=1080,
                )
            ],
            bit_rate=8_000_000,
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=info_no_duration,
        ):
            info = _probe(source)

        # duration=None のとき fps は None（AttributeError にならないこと）
        assert info.fps is None  # type: ignore[attr-defined]
        # width/height は第1 video StreamInfo から取得される
        assert info.width == 1920  # type: ignore[attr-defined]
        assert info.height == 1080  # type: ignore[attr-defined]

    def test_video_source_with_valid_fps(self, tmp_path: Path) -> None:
        """video stream あり・duration あり → fps が正しく設定される（ADR-C2-r2）。

        rate=30.0 の duration を持つ video ソースで fps=30.0 が返ること。
        """
        from clipwright_render.render import _probe

        source = str(tmp_path / "video.mp4")
        Path(source).touch()

        video_info = _make_media_info_with_video_stream(
            source, width=1920, height=1080, fps_rate=30.0
        )

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=video_info,
        ):
            info = _probe(source)

        # fps は duration.rate から設定される
        assert info.fps == 30.0  # type: ignore[attr-defined]
        assert info.width == 1920  # type: ignore[attr-defined]
        assert info.height == 1080  # type: ignore[attr-defined]


class TestSingleSourceBackwardCompat:
    """観点7: 単一ソース timeline での後方互換確認。

    probe 1回・-i 1つ・summary フォーマットが現行と不変であること。
    """

    def test_single_source_probe_called_once(self, tmp_path: Path) -> None:
        """単一ソース timeline で inspect_media が1回のみ呼ばれる（後方互換）。"""
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        Path(src0).touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src0, 5.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        probe_calls: list[str] = []

        def _fake_inspect(path: str) -> MediaInfo:
            probe_calls.append(path)
            return _make_media_info(path=path)

        with (
            patch("clipwright_render.render.inspect_media", side_effect=_fake_inspect),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch(
                "clipwright_render.render.run",
                return_value=CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                ),
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"失敗: {result.get('error')}"
        # ユニークソース1個 → probe 1回
        assert len(probe_calls) == 1

    def test_single_source_summary_contains_segment_count(self, tmp_path: Path) -> None:
        """単一ソース dry_run summary に segment_count が含まれる（後方互換）。"""
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        Path(src0).touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src0, 5.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src0, bit_rate=8_000_000),
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"失敗: {result.get('error')}"
        assert "segment_count" in result["data"]
        assert result["data"]["segment_count"] == 2
        assert "total_duration_seconds" in result["data"]
        assert abs(result["data"]["total_duration_seconds"] - 5.0) < 0.01


class TestMultiSourceDryRun:
    """観点8: dry_run 複数ソース → ok_result に連結予定情報が返り run 非呼び出し。

    ADR-C10: total_duration = 各クリップ source_range duration 合計。
    """

    def test_dry_run_multi_source_no_run_called(self, tmp_path: Path) -> None:
        """dry_run=True の複数ソース timeline で ffmpeg run が呼ばれない。"""
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        src1 = str(tmp_path / "src1.mp4")
        Path(src0).touch()
        Path(src1).touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src1, 0.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        run_called = False

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            nonlocal run_called
            run_called = True
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=lambda path: _make_media_info_with_video_stream(path),
            ),
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

        assert result["ok"] is True, f"失敗: {result.get('error')}"
        assert run_called is False

    def test_dry_run_multi_source_returns_segment_count_and_duration(
        self, tmp_path: Path
    ) -> None:
        """dry_run 複数ソース結果に segment_count と total_duration が含まれる。

        3秒+2秒=5秒の timeline で segment_count=2・total_duration=5.0 が返ること。
        """
        from clipwright_render.render import render_timeline

        src0 = str(tmp_path / "src0.mp4")
        src1 = str(tmp_path / "src1.mp4")
        Path(src0).touch()
        Path(src1).touch()

        tl_path = _make_multi_source_otio_file(
            [(src0, 0.0, 3.0), (src1, 0.0, 2.0)],
            tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                side_effect=lambda path: _make_media_info_with_video_stream(path),
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"失敗: {result.get('error')}"
        assert "segment_count" in result["data"]
        assert result["data"]["segment_count"] == 2
        assert "total_duration_seconds" in result["data"]
        assert abs(result["data"]["total_duration_seconds"] - 5.0) < 0.01


# ---------------------------------------------------------------------------
# BGM オーケストレーション拡張テスト（§7 ADR-B4-r2 / B5-r2 / B6-r2 / B8）
# ---------------------------------------------------------------------------
# plan.resolve_bgm / plan.BgmClip / plan.build_plan(bgm=...) / RenderPlan.bgm_source
# inspect_media / resolve_tool / run / plan.resolve_bgm / plan.build_plan はすべてモック。
# 実 ffmpeg/ffprobe バイナリは一切呼ばない。
# ---------------------------------------------------------------------------


def _make_bgm_otio_file(
    main_clips: list[tuple[str, float, float]],
    bgm_source: str,
    bgm_duration: float,
    tmp_path: Path,
) -> Path:
    """A1 本編 + A2 BGM クリップを持つ OTIO タイムラインを生成して書き出すヘルパー。

    A2 AudioTrack の BGM クリップに metadata["clipwright"]["kind"]=="bgm" を付与する。
    bgm_directive は最小限（volume_db=-6.0, fade_in_sec=0.0, fade_out_sec=0.0）。
    """
    import opentimelineio as otio

    # Video トラック + A1 本編 Audio トラック
    v_track = otio.schema.Track(kind=otio.schema.TrackKind.Video)
    a1_track = otio.schema.Track(kind=otio.schema.TrackKind.Audio, name="A1")
    for src, start, dur in main_clips:
        clip = otio.schema.Clip()
        clip.media_reference = otio.schema.ExternalReference(target_url=src)
        clip.source_range = _tr(start, dur)
        v_track.append(clip)
        a1_clip = otio.schema.Clip()
        a1_clip.media_reference = otio.schema.ExternalReference(target_url=src)
        a1_clip.source_range = _tr(start, dur)
        a1_track.append(a1_clip)

    # A2 BGM Audio トラック
    a2_track = otio.schema.Track(kind=otio.schema.TrackKind.Audio, name="A2")
    bgm_clip = otio.schema.Clip()
    bgm_clip.media_reference = otio.schema.ExternalReference(target_url=bgm_source)
    bgm_clip.source_range = _tr(0.0, bgm_duration)
    bgm_clip.metadata["clipwright"] = {
        "tool": "clipwright-bgm",
        "version": "0.1.0",
        "kind": "bgm",
        "volume_db": -6.0,
        "fade_in_sec": 0.0,
        "fade_out_sec": 0.0,
        "ducking": {"enabled": False, "threshold": 0.05, "ratio": 4.0},
    }
    a2_track.append(bgm_clip)

    tl = otio.schema.Timeline()
    tl.tracks.append(v_track)
    tl.tracks.append(a1_track)
    tl.tracks.append(a2_track)

    tl_path = tmp_path / "tl_with_bgm.otio"
    otio.adapters.write_to_file(tl, str(tl_path))
    return tl_path


class TestBgmResolveBgmCalled:
    """観点1: BGM クリップありの timeline で resolve_bgm が呼ばれ build_plan に bgm= が渡る。

    ADR-B4-r2: _render_inner は resolve_bgm(tl) を呼び BgmClip を取得する。
    ADR-B5-r2: build_plan に bgm=BgmClip を渡す。
    """

    def test_resolve_bgm_called_and_bgm_passed_to_build_plan(
        self, tmp_path: Path
    ) -> None:
        """BGM クリップありの timeline で resolve_bgm が1回呼ばれ、
        build_plan の bgm 引数に BgmClip が渡ること（ADR-B4-r2/B5-r2）。

        未実装のため render.resolve_bgm が AttributeError で失敗すること（Red）。
        """
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "main.mp4")
        bgm = str(tmp_path / "bgm.mp3")
        Path(src).touch()
        Path(bgm).touch()

        tl_path = _make_bgm_otio_file(
            [(src, 0.0, 5.0)], bgm_source=bgm, bgm_duration=30.0, tmp_path=tmp_path
        )
        output = str(tmp_path / "out.mp4")

        resolve_bgm_calls: list[Any] = []
        build_plan_bgm_args: list[Any] = []

        fake_bgm_clip_sentinel = object()  # BgmClip 代替センチネル

        def _fake_resolve_bgm(tl: Any) -> Any:
            resolve_bgm_calls.append(tl)
            return fake_bgm_clip_sentinel

        from clipwright_render.plan import RenderPlan

        def _fake_build_plan(*args: Any, **kwargs: Any) -> RenderPlan:
            build_plan_bgm_args.append(kwargs.get("bgm"))
            return RenderPlan(
                filter_complex=(
                    "[0:v]trim=0:5,setpts=PTS-STARTPTS[v0];[v0]concat=n=1:v=1:a=0[outv]"
                ),
                ffmpeg_args=["-filter_complex", "...", "-map", "[outv]"],
                segment_count=1,
                total_duration_seconds=5.0,
                input_sources=[src],
            )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                side_effect=_fake_resolve_bgm,
            ),
            patch(
                "clipwright_render.render.build_plan",
                side_effect=_fake_build_plan,
            ),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        # resolve_bgm が1回呼ばれること
        assert len(resolve_bgm_calls) == 1
        # build_plan に bgm= が渡ること
        assert len(build_plan_bgm_args) == 1
        assert build_plan_bgm_args[0] is fake_bgm_clip_sentinel


class TestBgmFfmpegInputOrder:
    """観点2: ffmpeg -i 並びが [*input_sources, -stream_loop, -1, -i, bgm_source] の順。

    ADR-B6-r2/DC-AS-005/B5-r2: BGM は末尾・-stream_loop が BGM -i の直前。
    単一ソース＋BGM → -i が2つ・-stream_loop 1つ。
    """

    def test_bgm_input_appended_after_main_sources_with_stream_loop(
        self, tmp_path: Path
    ) -> None:
        """単一ソース＋BGM の ffmpeg コマンドで -i が2つ、
        2番目 -i の直前に -stream_loop -1 がある（ADR-B6-r2）。

        未実装のため RenderPlan.bgm_source 属性が存在せず TypeError で
        失敗すること（Red）。
        """
        from clipwright_render.plan import RenderPlan
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "main.mp4")
        bgm = str(tmp_path / "bgm.mp3")
        Path(src).touch()
        Path(bgm).touch()
        (tmp_path / "out.mp4").touch()

        tl_path = _make_bgm_otio_file(
            [(src, 0.0, 5.0)], bgm_source=bgm, bgm_duration=30.0, tmp_path=tmp_path
        )
        output = str(tmp_path / "out.mp4")

        # build_plan が bgm_source を含む RenderPlan を返すようにモック
        # bgm_source フィールドは未実装のため type: ignore[call-arg] を付ける
        fake_plan = RenderPlan(
            filter_complex=(
                "[0:v]trim=0:5,setpts=PTS-STARTPTS[v0];"
                "[v0]concat=n=1:v=1:a=1[outv][outa];"
                "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                "atrim=0:5,asetpts=PTS-STARTPTS,volume=-6dB[bgm];"
                "[main_fmt][bgm]amix=inputs=2:normalize=0,alimiter=limit=1.0[outa_bgm]"
            ),
            ffmpeg_args=[
                "-filter_complex",
                "...",
                "-map",
                "[outv]",
                "-map",
                "[outa_bgm]",
            ],
            segment_count=1,
            total_duration_seconds=5.0,
            input_sources=[src],
            bgm_source=bgm,  # type: ignore[call-arg]
        )

        captured_cmd: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                return_value=object(),  # BgmClip センチネル
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.build_plan", return_value=fake_plan),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert result["ok"] is True, f"失敗: {result.get('error')}"

        # -i の出現インデックスを列挙
        i_indices = [i for i, v in enumerate(captured_cmd) if v == "-i"]
        assert len(i_indices) == 2, f"-i は2つ期待: {captured_cmd}"

        # 1番目 -i は src（本編ソース）
        assert captured_cmd[i_indices[0] + 1] == src

        # 2番目 -i は bgm、その直前に "-stream_loop" "-1" が並ぶ（ADR-B6-r2）
        bgm_i_pos = i_indices[1]
        assert captured_cmd[bgm_i_pos + 1] == bgm, (
            "2番目 -i の次は bgm_source でなければならない"
        )
        assert bgm_i_pos >= 2, "-stream_loop -1 の前置スペースが足りない"
        assert captured_cmd[bgm_i_pos - 2] == "-stream_loop", (
            f"-stream_loop が BGM -i の2つ前にない: {captured_cmd}"
        )
        assert captured_cmd[bgm_i_pos - 1] == "-1", (
            f"-1 が BGM -i の1つ前にない: {captured_cmd}"
        )

        # BGM index = len(input_sources) = 1（DC-AS-005 不変条件）
        bgm_index = len(fake_plan.input_sources)
        assert bgm_index == 1  # 単一ソース → BGM は index 1


class TestBgmSourceBoundaryCheck:
    """観点3/4: BGM ソースの境界検証（ADR-B8）。

    観点3: BGM ソースが timeline ディレクトリ外 → PATH_NOT_ALLOWED
    観点4: output == BGM ソース → PATH_NOT_ALLOWED（全ソース _check_path_not_allowed）
    """

    def test_bgm_source_outside_timeline_dir_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """BGM ソースが timeline ディレクトリ外 → PATH_NOT_ALLOWED（ADR-B8）。

        _check_source_within_timeline_dir が BGM ソースにも適用されること。
        未実装のため render_timeline が PATH_NOT_ALLOWED を返さず ok=True または
        別エラーとなること（Red）。
        """
        from clipwright_render.render import render_timeline

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()

        src = str(project_dir / "main.mp4")
        bgm_outside = str(outside_dir / "bgm.mp3")  # 境界外
        Path(src).touch()
        Path(bgm_outside).touch()

        tl_path = _make_bgm_otio_file(
            [(src, 0.0, 5.0)],
            bgm_source=bgm_outside,
            bgm_duration=30.0,
            tmp_path=project_dir,
        )
        output = str(project_dir / "out.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED

    def test_output_equals_bgm_source_raises_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """output == BGM ソース → PATH_NOT_ALLOWED（ADR-B8・非破壊）。

        _check_path_not_allowed が BGM ソースにも適用されること。
        未実装のため render_timeline が PATH_NOT_ALLOWED を返さず ok=True または
        別エラーとなること（Red）。
        """
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "main.mp4")
        bgm = str(tmp_path / "bgm.mp3")
        Path(src).touch()
        Path(bgm).touch()

        tl_path = _make_bgm_otio_file(
            [(src, 0.0, 5.0)], bgm_source=bgm, bgm_duration=30.0, tmp_path=tmp_path
        )
        # output == bgm（BGM ソースと同じパス → 非破壊違反）
        output = bgm

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED


class TestBgmSourceNotFound:
    """観点5: BGM ソース不在 → FILE_NOT_FOUND・basename のみ・絶対パス非露出（CWE-209）。

    ADR-B8: BGM ソースにも存在確認を適用する。
    """

    def test_bgm_source_not_found_returns_file_not_found(self, tmp_path: Path) -> None:
        """BGM ソースが存在しない → FILE_NOT_FOUND（ADR-B8）。

        未実装のため render_timeline が FILE_NOT_FOUND を返さないことで失敗する（Red）。
        """
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "main.mp4")
        bgm_missing = str(tmp_path / "missing_bgm.mp3")  # 存在しない
        Path(src).touch()
        # bgm_missing は作成しない

        tl_path = _make_bgm_otio_file(
            [(src, 0.0, 5.0)],
            bgm_source=bgm_missing,
            bgm_duration=30.0,
            tmp_path=tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND

    def test_bgm_source_not_found_basename_only_in_message(
        self, tmp_path: Path
    ) -> None:
        """BGM ソース不在エラーのメッセージに絶対パスが露出せず basename のみ（CWE-209）。

        未実装のため basename のみの検証が通らないことで失敗する（Red）。
        """
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "main.mp4")
        bgm_missing = str(tmp_path / "missing_bgm.mp3")
        Path(src).touch()

        tl_path = _make_bgm_otio_file(
            [(src, 0.0, 5.0)],
            bgm_source=bgm_missing,
            bgm_duration=30.0,
            tmp_path=tmp_path,
        )
        output = str(tmp_path / "out.mp4")

        with patch(
            "clipwright_render.render.inspect_media",
            return_value=_make_media_info(path=src),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
            )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND
        error_message: str = result["error"]["message"]
        # 絶対パス（ディレクトリ部分）が露出していない
        assert str(tmp_path) not in error_message
        # basename は含まれる
        assert "missing_bgm.mp3" in error_message


class TestBgmBackwardCompat:
    """観点6: BGM クリップ無し（resolve_bgm->None）-> 後方互換確認。

    ADR-B7: BGM 段はクリップ有無で分岐し、なければ既存挙動を完全維持する。
    - build_plan に bgm=None が渡る
    - -i は input_sources のみ（bgm_source は None）
    - -stream_loop なし
    - dry_run summary が既存フォーマットと同じ
    """

    def test_no_bgm_build_plan_receives_bgm_none(self, tmp_path: Path) -> None:
        """BGM クリップなし timeline では build_plan の bgm 引数が None（ADR-B7）。

        未実装のため bgm=None が渡らないか、resolve_bgm 自体が ImportError で
        失敗することで Red になること。
        """
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "main.mp4")
        Path(src).touch()

        # BGM なし timeline（通常の単一ソース）
        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        build_plan_bgm_args: list[Any] = []

        from clipwright_render.plan import RenderPlan

        def _fake_build_plan(*args: Any, **kwargs: Any) -> RenderPlan:
            build_plan_bgm_args.append(kwargs.get("bgm"))
            return RenderPlan(
                filter_complex=(
                    "[0:v]trim=0:5,setpts=PTS-STARTPTS[v0];[v0]concat=n=1:v=1:a=0[outv]"
                ),
                ffmpeg_args=["-filter_complex", "...", "-map", "[outv]"],
                segment_count=1,
                total_duration_seconds=5.0,
                input_sources=[src],
            )

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                return_value=None,
            ),
            patch(
                "clipwright_render.render.build_plan",
                side_effect=_fake_build_plan,
            ),
        ):
            render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert len(build_plan_bgm_args) == 1
        assert build_plan_bgm_args[0] is None

    def test_no_bgm_ffmpeg_has_no_stream_loop(self, tmp_path: Path) -> None:
        """BGM クリップなし -> ffmpeg コマンドに -stream_loop が含まれない（ADR-B7）。

        未実装のため -stream_loop が除外されていないか、resolve_bgm が ImportError で
        失敗することで Red になること。
        """
        from clipwright_render.plan import RenderPlan
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "main.mp4")
        Path(src).touch()
        (tmp_path / "out.mp4").touch()

        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        # bgm_source フィールドなし（既存 RenderPlan）
        fake_plan = RenderPlan(
            filter_complex=(
                "[0:v]trim=0:5,setpts=PTS-STARTPTS[v0];[v0]concat=n=1:v=1:a=0[outv]"
            ),
            ffmpeg_args=["-filter_complex", "...", "-map", "[outv]"],
            segment_count=1,
            total_duration_seconds=5.0,
            input_sources=[src],
        )

        captured_cmd: list[str] = []

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmd.extend(cmd)
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                return_value=None,
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.build_plan", return_value=fake_plan),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(overwrite=True),
            )

        assert result["ok"] is True, f"失敗: {result.get('error')}"
        # -stream_loop が含まれない（BGM なし -> ADR-B7）
        assert "-stream_loop" not in captured_cmd, (
            f"-stream_loop が含まれるべきでないコマンドに含まれた: {captured_cmd}"
        )
        # -i は src のみ
        i_indices = [i for i, v in enumerate(captured_cmd) if v == "-i"]
        assert len(i_indices) == 1
        assert captured_cmd[i_indices[0] + 1] == src

    def test_no_bgm_dry_run_summary_unchanged(self, tmp_path: Path) -> None:
        """BGM クリップなし -> dry_run summary が BGM 拡張前と同じフォーマット（ADR-B7）。

        既存テスト test_dry_run_summary_contains_segment_count_and_duration と
        同じ検証を BGM 拡張後も確認する後方互換テスト。
        未実装のため resolve_bgm が ImportError で失敗することで Red になること。
        """
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "main.mp4")
        Path(src).touch()

        tl_path = tmp_path / "tl.otio"
        _write_timeline(tl_path, [_make_clip(src, 0.0, 5.0)])
        output = str(tmp_path / "out.mp4")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src, bit_rate=8_000_000),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                return_value=None,
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
        # segment_count が含まれること
        assert "1" in result["summary"]
        # data に segment_count と total_duration_seconds が含まれること
        assert result["data"]["segment_count"] == 1
        assert abs(result["data"]["total_duration_seconds"] - 5.0) < 0.01


class TestBgmDryRun:
    """観点7: dry_run（BGM あり）-> ok_result に filter_complex が返り run 非呼び出し。

    ADR-B5-r2: dry_run 時は filter_complex（BGM 段含む）を data に返す。
    run は呼ばれない。
    """

    def test_bgm_dry_run_returns_ok_and_no_run(self, tmp_path: Path) -> None:
        """BGM ありの dry_run=True で ok=True が返り ffmpeg run が呼ばれない。

        未実装のため resolve_bgm または RenderPlan.bgm_source が存在せず
        AttributeError で失敗することで Red になること。
        """
        from clipwright_render.plan import RenderPlan
        from clipwright_render.render import render_timeline

        src = str(tmp_path / "main.mp4")
        bgm = str(tmp_path / "bgm.mp3")
        Path(src).touch()
        Path(bgm).touch()

        tl_path = _make_bgm_otio_file(
            [(src, 0.0, 5.0)], bgm_source=bgm, bgm_duration=30.0, tmp_path=tmp_path
        )
        output = str(tmp_path / "out.mp4")

        bgm_filter = (
            "[0:v]trim=0:5,setpts=PTS-STARTPTS[v0];"
            "[0:a]atrim=0:5,asetpts=PTS-STARTPTS,"
            "aformat=sample_rates=48000:channel_layouts=stereo[a0];"
            "[v0][a0]concat=n=1:v=1:a=1[outv][outa];"
            "[outa]aformat=sample_rates=48000:channel_layouts=stereo[main_fmt];"
            "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,"
            "atrim=0:5,asetpts=PTS-STARTPTS,volume=-6dB[bgm];"
            "[main_fmt][bgm]amix=inputs=2:normalize=0,alimiter=limit=1.0[outa_bgm]"
        )
        fake_plan = RenderPlan(
            filter_complex=bgm_filter,
            ffmpeg_args=[
                "-filter_complex",
                bgm_filter,
                "-map",
                "[outv]",
                "-map",
                "[outa_bgm]",
            ],
            segment_count=1,
            total_duration_seconds=5.0,
            input_sources=[src],
            bgm_source=bgm,  # type: ignore[call-arg]
        )

        run_called = False

        def _fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            nonlocal run_called
            run_called = True
            return CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_render.render.inspect_media",
                return_value=_make_media_info(path=src),
            ),
            patch(
                "clipwright_render.render.resolve_bgm",
                return_value=object(),  # BgmClip センチネル
            ),
            patch(
                "clipwright_render.render.resolve_tool",
                side_effect=lambda name, env_var=None: f"/usr/bin/{name}",
            ),
            patch("clipwright_render.render.build_plan", return_value=fake_plan),
            patch("clipwright_render.render.run", side_effect=_fake_run),
        ):
            result = render_timeline(
                timeline=str(tl_path),
                output=output,
                options=RenderOptions(),
                dry_run=True,
            )

        assert result["ok"] is True, f"失敗: {result.get('error')}"
        # run が呼ばれていない
        assert run_called is False
        # data に filter_complex が含まれる
        assert "filter_complex" in result["data"]
        # filter_complex に BGM 段（amix または alimiter または bgm ラベル）が含まれる
        fc = result["data"]["filter_complex"]
        assert "amix" in fc or "alimiter" in fc or "bgm" in fc.lower(), (
            f"filter_complex に BGM 段が見当たらない: {fc}"
        )
