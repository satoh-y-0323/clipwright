"""test_analyze.py — analyze.py（loudnorm/volumedetect 実行・ラウドネス測定）のテスト。

モック方針:
  - clipwright_loudness.analyze.resolve_tool を patch して ffmpeg バイナリパスを制御。
  - clipwright_loudness.analyze.run を patch して ffmpeg stderr を制御。
  - 実 ffmpeg バイナリは一切呼ばない。

重要（DC-AS-004 教訓: noise での正規表現フィールド名不一致バグを繰り返さない）:
  実 ffmpeg 8.1.1 の loudnorm print_format=json 出力形式（環境確認済み）:
    [Parsed_loudnorm_0 @ 0x...] ← 空行
    {
    \t"input_i" : "-21.75",
    \t"input_tp" : "-18.06",
    \t"input_lra" : "0.00",
    \t"input_thresh" : "-31.75",
    \t"output_i" : "-14.03",
    \t"output_tp" : "-10.27",
    \t"output_lra" : "0.00",
    \t"output_thresh" : "-24.03",
    \t"normalization_type" : "dynamic",
    \t"target_offset" : "0.03"
    }
  ※ 値は文字列として引用符付きで出力される。"-inf" になる場合もある（無音素材）。

  実 ffmpeg 8.1.1 の volumedetect 出力形式（環境確認済み）:
    [Parsed_volumedetect_0 @ 0x...] n_samples: 132300
    [Parsed_volumedetect_0 @ 0x...] mean_volume: -21.1 dB
    [Parsed_volumedetect_0 @ 0x...] max_volume: -18.1 dB
    [Parsed_volumedetect_0 @ 0x...] histogram_18db: 38400
  ※ "max_volume: <VALUE> dB" 形式。VALUE は負の浮動小数点数。

検証観点:
  (a) loudnorm 正常: stderr 末尾 JSON から input_i/input_tp/input_lra/input_thresh/target_offset 抽出
  (b) peak 正常: volumedetect から max_volume 抽出
  (c) 測定不能（JSON/フィールド欠落）→ measured=None + warning（U-1 確定方針）
  (d) ffmpeg 不在 → DEPENDENCY_MISSING
  (e) 実行失敗 → SUBPROCESS_FAILED（message に stderr 生文字列・絶対パス非混入）
  (f) timeout → SUBPROCESS_TIMEOUT
  (g) subprocess 引数配列・shell=False・timeout・終了コード検査の assert
  (h) track 全体測定コマンド組み立て検証
"""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import pytest
from clipwright.errors import ClipwrightError, ErrorCode

# ===========================================================================
# 実 ffmpeg 出力形式（環境確認済み: ffmpeg 8.1.1 Windows）
# ===========================================================================

# loudnorm print_format=json の正常出力（実機で確認した形式）
_LOUDNORM_STDERR_NORMAL = """\
ffmpeg version 8.1.1 ...
Input #0, ...
[Parsed_loudnorm_0 @ 0000019762f437c0]
{
\t"input_i" : "-21.75",
\t"input_tp" : "-18.06",
\t"input_lra" : "0.00",
\t"input_thresh" : "-31.75",
\t"output_i" : "-14.03",
\t"output_tp" : "-10.27",
\t"output_lra" : "0.00",
\t"output_thresh" : "-24.03",
\t"normalization_type" : "dynamic",
\t"target_offset" : "0.03"
}
size=N/A time=00:00:05.00 ...
"""

# loudnorm: -inf が入るケース（無音素材などで測定不能）
_LOUDNORM_STDERR_INF_VALUES = """\
[Parsed_loudnorm_0 @ 0000019762f437c0]
{
\t"input_i" : "-inf",
\t"input_tp" : "-inf",
\t"input_lra" : "0.00",
\t"input_thresh" : "-70.00",
\t"output_i" : "-inf",
\t"output_tp" : "-inf",
\t"output_lra" : "0.00",
\t"output_thresh" : "-70.00",
\t"normalization_type" : "dynamic",
\t"target_offset" : "inf"
}
"""

# loudnorm: JSON ブロックが全くない stderr（測定不能）
_LOUDNORM_STDERR_NO_JSON = """\
ffmpeg version 8.1.1 ...
Input #0, ...
Stream #0:0: Audio: aac, 44100 Hz, stereo, fltp, 192 kb/s
size=N/A time=00:00:05.00 ...
"""

# volumedetect の正常出力（実機で確認した形式）
_VOLUMEDETECT_STDERR_NORMAL = """\
ffmpeg version 8.1.1 ...
[Parsed_volumedetect_0 @ 000001fbb2026580] n_samples: 0
[Parsed_volumedetect_0 @ 000001fbb2024c00] n_samples: 132300
[Parsed_volumedetect_0 @ 000001fbb2024c00] mean_volume: -21.1 dB
[Parsed_volumedetect_0 @ 000001fbb2024c00] max_volume: -18.1 dB
[Parsed_volumedetect_0 @ 000001fbb2024c00] histogram_18db: 38400
size=N/A time=00:00:03.00 ...
"""

# volumedetect: max_volume フィールドがない stderr（測定不能）
_VOLUMEDETECT_STDERR_NO_MAX_VOLUME = """\
ffmpeg version 8.1.1 ...
[Parsed_volumedetect_0 @ 000001fbb2024c00] n_samples: 0
size=N/A time=00:00:00.00 ...
"""

_FAKE_FFMPEG = "/usr/local/bin/ffmpeg"


def _fake_resolve(name: str, env_var: str | None = None) -> str:
    """resolve_tool の成功モック: ffmpeg パスを返す。"""
    return _FAKE_FFMPEG


def _make_run_ok(stderr: str) -> Any:
    """run の成功モック（returncode=0, 指定 stderr）を返すクロージャ。"""

    def _impl(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        return CompletedProcess(args=cmd, returncode=0, stdout="", stderr=stderr)

    return _impl


# ===========================================================================
# (a) loudnorm 正常: stderr 末尾 JSON から測定値を抽出（DC-AS-004 教訓）
# ===========================================================================


class TestLoudnormNormal:
    """実 ffmpeg 形式の loudnorm JSON から測定値を正しく抽出できること。

    impl の JSON パース/フィールド名が実形式と一致しなければ失敗し、
    DC-AS-004 相当のバグを Red で捕捉する。
    """

    def test_loudnorm_measured_not_none(self, tmp_path: Path) -> None:
        """正常 loudnorm stderr から measured が None でないこと（DC-AS-004 教訓）。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert result["measured"] is not None, (
            "DC-AS-004 教訓: 実 ffmpeg 形式 loudnorm JSON から measured を抽出できていない。"
            " impl の JSON パース/フィールド名を確認すること。"
        )

    def test_loudnorm_input_i_extracted(self, tmp_path: Path) -> None:
        """input_i が -21.75 として抽出されること。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        measured = result["measured"]
        assert measured is not None
        assert measured["input_i"] == pytest.approx(-21.75, abs=0.01)

    def test_loudnorm_input_tp_extracted(self, tmp_path: Path) -> None:
        """input_tp が -18.06 として抽出されること。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        measured = result["measured"]
        assert measured is not None
        assert measured["input_tp"] == pytest.approx(-18.06, abs=0.01)

    def test_loudnorm_input_lra_extracted(self, tmp_path: Path) -> None:
        """input_lra が 0.0 として抽出されること。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        measured = result["measured"]
        assert measured is not None
        assert measured["input_lra"] == pytest.approx(0.0, abs=0.01)

    def test_loudnorm_input_thresh_extracted(self, tmp_path: Path) -> None:
        """input_thresh が -31.75 として抽出されること。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        measured = result["measured"]
        assert measured is not None
        assert measured["input_thresh"] == pytest.approx(-31.75, abs=0.01)

    def test_loudnorm_target_offset_extracted(self, tmp_path: Path) -> None:
        """target_offset が 0.03 として抽出されること。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        measured = result["measured"]
        assert measured is not None
        assert measured["target_offset"] == pytest.approx(0.03, abs=0.01)

    def test_loudnorm_no_warning_on_success(self, tmp_path: Path) -> None:
        """正常測定時は warnings が空であること。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert result["warnings"] == []


# ===========================================================================
# (b) peak 正常: volumedetect から max_volume 抽出
# ===========================================================================


class TestPeakNormal:
    """実 ffmpeg 形式の volumedetect stderr から max_volume を正しく抽出できること。"""

    def test_peak_measured_not_none(self, tmp_path: Path) -> None:
        """正常 volumedetect stderr から measured が None でないこと。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_VOLUMEDETECT_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(media, mode="peak", target_peak_db=-1.0)

        assert result["measured"] is not None, (
            "volumedetect 正常 stderr から measured を抽出できていない。"
            " 'max_volume: <VALUE> dB' 形式の正規表現を確認すること。"
        )

    def test_peak_max_volume_db_extracted(self, tmp_path: Path) -> None:
        """max_volume_db が -18.1 として抽出されること。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_VOLUMEDETECT_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(media, mode="peak", target_peak_db=-1.0)

        measured = result["measured"]
        assert measured is not None
        assert measured["max_volume_db"] == pytest.approx(-18.1, abs=0.1)

    def test_peak_no_warning_on_success(self, tmp_path: Path) -> None:
        """正常測定時は warnings が空であること。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_VOLUMEDETECT_STDERR_NORMAL),
            ),
        ):
            result = measure_loudness(media, mode="peak", target_peak_db=-1.0)

        assert result["warnings"] == []


# ===========================================================================
# (c) 測定不能（JSON欠落/-inf値）→ measured=None + warning（U-1 確定）
# ===========================================================================


class TestLoudnormMeasurementFailure:
    """loudnorm で測定不能な場合 measured=None + warning が返ること（U-1）。"""

    def test_loudnorm_no_json_gives_measured_none(self, tmp_path: Path) -> None:
        """JSON ブロックがない stderr → measured=None（U-1）。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_NO_JSON),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert result["measured"] is None, (
            "U-1: loudnorm JSON なし時は measured=None でなければならない。"
        )

    def test_loudnorm_no_json_gives_warning(self, tmp_path: Path) -> None:
        """JSON ブロックがない stderr → warnings に警告が含まれること（U-1）。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_NO_JSON),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert len(result["warnings"]) > 0, "U-1: 測定不能時は warnings に警告が必要。"

    def test_loudnorm_inf_values_gives_measured_none(self, tmp_path: Path) -> None:
        """-inf 値が含まれる JSON → measured=None（無音素材などで測定不能・U-1）。

        loudnorm が "-inf" を返した場合（無音素材）は LoudnormMeasured で
        allow_inf_nan=False により検証エラーになるため measured=None にすること。
        """
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_INF_VALUES),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert result["measured"] is None, (
            "U-1: loudnorm で -inf 値が含まれる場合は measured=None でなければならない。"
        )


class TestPeakMeasurementFailure:
    """volumedetect で測定不能な場合 measured=None + warning が返ること（U-1）。"""

    def test_peak_no_max_volume_gives_measured_none(self, tmp_path: Path) -> None:
        """max_volume フィールドがない stderr → measured=None。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_VOLUMEDETECT_STDERR_NO_MAX_VOLUME),
            ),
        ):
            result = measure_loudness(media, mode="peak", target_peak_db=-1.0)

        assert result["measured"] is None

    def test_peak_no_max_volume_gives_warning(self, tmp_path: Path) -> None:
        """max_volume フィールドがない stderr → warnings に警告が含まれること。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_VOLUMEDETECT_STDERR_NO_MAX_VOLUME),
            ),
        ):
            result = measure_loudness(media, mode="peak", target_peak_db=-1.0)

        assert len(result["warnings"]) > 0


# ===========================================================================
# (d) ffmpeg 不在 → DEPENDENCY_MISSING
# ===========================================================================


class TestFfmpegNotFound:
    """ffmpeg が resolve できない場合に DEPENDENCY_MISSING が発生する。"""

    def test_dependency_missing_when_ffmpeg_not_found_loudnorm(
        self, tmp_path: Path
    ) -> None:
        """loudnorm モードで resolve_tool が DEPENDENCY_MISSING を送出すると伝播すること。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        def _fail_resolve(name: str, env_var: str | None = None) -> str:
            raise ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message=f"{name} が PATH 上に見つかりません。",
                hint=f"{name} をインストールして PATH に追加してください。",
            )

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fail_resolve
            ),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert exc_info.value.code == ErrorCode.DEPENDENCY_MISSING

    def test_dependency_missing_when_ffmpeg_not_found_peak(
        self, tmp_path: Path
    ) -> None:
        """peak モードでも同様に DEPENDENCY_MISSING が伝播すること。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        def _fail_resolve(name: str, env_var: str | None = None) -> str:
            raise ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message=f"{name} が見つかりません。",
                hint="インストールしてください。",
            )

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fail_resolve
            ),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            measure_loudness(media, mode="peak", target_peak_db=-1.0)

        assert exc_info.value.code == ErrorCode.DEPENDENCY_MISSING


# ===========================================================================
# (e) 実行失敗 → SUBPROCESS_FAILED（message に stderr 生文字列・絶対パス非混入）
# ===========================================================================


class TestSubprocessFailed:
    """ffmpeg 実行失敗時に SUBPROCESS_FAILED が発生し、message に秘密を含まないこと。"""

    def test_subprocess_failed_loudnorm(self, tmp_path: Path) -> None:
        """loudnorm で run が SUBPROCESS_FAILED を送出すると伝播すること。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        def _fail_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="ffmpeg コマンドが終了コード 1 で失敗しました。",
                hint="ffmpeg のバージョンや引数を確認してください。",
            )

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_fail_run),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert exc_info.value.code == ErrorCode.SUBPROCESS_FAILED

    def test_subprocess_failed_message_no_absolute_path(self, tmp_path: Path) -> None:
        """SUBPROCESS_FAILED の message に絶対ディレクトリパスが含まれないこと。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        def _fail_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            # media の絶対パスを含む message を送出 — impl がそのまま再送出すると
            # 絶対パスが外部に漏れる。measure_loudness はパスを公開してはならない。
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=f"ffmpeg failed for {media}",
                hint="確認してください。",
            )

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_fail_run),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert str(tmp_path) not in exc_info.value.message

    def test_subprocess_failed_peak(self, tmp_path: Path) -> None:
        """peak モードでも SUBPROCESS_FAILED が伝播すること。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        def _fail_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="ffmpeg コマンドが失敗しました。",
                hint="確認してください。",
            )

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_fail_run),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            measure_loudness(media, mode="peak", target_peak_db=-1.0)

        assert exc_info.value.code == ErrorCode.SUBPROCESS_FAILED


# ===========================================================================
# (f) timeout → SUBPROCESS_TIMEOUT
# ===========================================================================


class TestSubprocessTimeout:
    """ffmpeg 実行が timeout した場合 SUBPROCESS_TIMEOUT が発生すること。"""

    def test_subprocess_timeout_loudnorm(self, tmp_path: Path) -> None:
        """loudnorm で run が SUBPROCESS_TIMEOUT を送出すると伝播すること。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        def _timeout_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_TIMEOUT,
                message="ffmpeg コマンドがタイムアウトしました。",
                hint="タイムアウト値を増やすか、短いメディアで試してください。",
            )

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_timeout_run),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert exc_info.value.code == ErrorCode.SUBPROCESS_TIMEOUT

    def test_subprocess_timeout_peak(self, tmp_path: Path) -> None:
        """peak モードでも SUBPROCESS_TIMEOUT が伝播すること。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        def _timeout_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_TIMEOUT,
                message="タイムアウト。",
                hint="hint",
            )

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_timeout_run),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            measure_loudness(media, mode="peak", target_peak_db=-1.0)

        assert exc_info.value.code == ErrorCode.SUBPROCESS_TIMEOUT


# ===========================================================================
# (g) subprocess 引数配列・shell=False・timeout・終了コード検査の assert
# ===========================================================================


class TestSubprocessContract:
    """run に渡す引数の形式・timeout・呼び出し検証（コーディング規約 §6.5）。"""

    def test_run_called_with_list_not_string_loudnorm(self, tmp_path: Path) -> None:
        """loudnorm: run に渡すコマンドが list[str] であること（shell=False 相当）。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[Any] = []

        def _capture(cmd: Any, **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert len(captured_cmds) >= 1
        cmd = captured_cmds[0]
        assert isinstance(cmd, list), f"cmd が list でない: {type(cmd)}"
        for arg in cmd:
            assert isinstance(arg, str), f"コマンド引数が str でない: {arg!r}"

    def test_run_called_with_list_not_string_peak(self, tmp_path: Path) -> None:
        """peak: run に渡すコマンドが list[str] であること（shell=False 相当）。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[Any] = []

        def _capture(cmd: Any, **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(media, mode="peak", target_peak_db=-1.0)

        cmd = captured_cmds[0]
        assert isinstance(cmd, list)

    def test_run_cmd_starts_with_ffmpeg_binary_loudnorm(self, tmp_path: Path) -> None:
        """loudnorm: run の第1引数が resolve_tool で得た ffmpeg バイナリパスであること。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        cmd = captured_cmds[0]
        assert cmd[0] == _FAKE_FFMPEG

    def test_run_called_with_timeout_loudnorm(self, tmp_path: Path) -> None:
        """loudnorm: run に timeout キーワード引数が渡されること。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_kwargs: list[dict[str, Any]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_kwargs.append(kwargs)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert len(captured_kwargs) >= 1
        kwargs = captured_kwargs[0]
        assert "timeout" in kwargs, "run に timeout 引数が渡されていない。"
        assert isinstance(kwargs["timeout"], (int, float))
        assert kwargs["timeout"] > 0

    def test_run_called_with_timeout_peak(self, tmp_path: Path) -> None:
        """peak: run に timeout キーワード引数が渡されること。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_kwargs: list[dict[str, Any]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_kwargs.append(kwargs)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(media, mode="peak", target_peak_db=-1.0)

        kwargs = captured_kwargs[0]
        assert "timeout" in kwargs
        assert kwargs["timeout"] > 0

    def test_run_cmd_includes_null_output_loudnorm(self, tmp_path: Path) -> None:
        """loudnorm: run のコマンドに -f null - が含まれること（出力不要）。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        cmd = captured_cmds[0]
        assert "null" in cmd, f"コマンドに 'null' が含まれない: {cmd}"

    def test_run_cmd_includes_null_output_peak(self, tmp_path: Path) -> None:
        """peak: run のコマンドに -f null - が含まれること（出力不要）。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(media, mode="peak", target_peak_db=-1.0)

        cmd = captured_cmds[0]
        assert "null" in cmd


# ===========================================================================
# (h) track 全体測定コマンド組み立て検証
# ===========================================================================


class TestTrackMeasurementCommand:
    """track 全体測定（メディア第1音声全体）のコマンド組み立て検証（ADR-L7）。"""

    def test_loudnorm_command_contains_loudnorm_filter(self, tmp_path: Path) -> None:
        """loudnorm コマンドに 'loudnorm' フィルタが含まれること（ADR-L1）。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        cmd = captured_cmds[0]
        cmd_str = " ".join(cmd)
        assert "loudnorm" in cmd_str, (
            f"コマンドに 'loudnorm' フィルタが含まれない: {cmd_str}"
        )

    def test_loudnorm_command_contains_print_format_json(self, tmp_path: Path) -> None:
        """loudnorm コマンドに 'print_format=json' が含まれること（ADR-L1）。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        cmd = captured_cmds[0]
        cmd_str = " ".join(cmd)
        assert "print_format=json" in cmd_str, (
            f"コマンドに 'print_format=json' が含まれない: {cmd_str}"
        )

    def test_loudnorm_command_contains_target_i(self, tmp_path: Path) -> None:
        """loudnorm コマンドに target I 値が含まれること（I=-14）。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        cmd = captured_cmds[0]
        cmd_str = " ".join(cmd)
        assert "I=-14" in cmd_str, (
            f"コマンドに I=-14 (target LUFS) が含まれない: {cmd_str}"
        )

    def test_loudnorm_command_contains_media_path(self, tmp_path: Path) -> None:
        """loudnorm コマンドにメディアパスが含まれること。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        cmd = captured_cmds[0]
        assert str(media) in cmd, f"コマンドにメディアパスが含まれない: {cmd}"

    def test_peak_command_contains_volumedetect_filter(self, tmp_path: Path) -> None:
        """peak コマンドに 'volumedetect' フィルタが含まれること（ADR-L2）。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")
        captured_cmds: list[list[str]] = []

        def _capture(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
            captured_cmds.append(cmd)
            return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch("clipwright_loudness.analyze.run", side_effect=_capture),
        ):
            measure_loudness(media, mode="peak", target_peak_db=-1.0)

        cmd = captured_cmds[0]
        cmd_str = " ".join(cmd)
        assert "volumedetect" in cmd_str, (
            f"コマンドに 'volumedetect' フィルタが含まれない: {cmd_str}"
        )


# ===========================================================================
# H-1 回帰: 先頭に余計な {} ブロックがある場合も末尾 loudnorm JSON を取得できること
# ===========================================================================


# 先頭に余計な {} ブロックが含まれる stderr（H-1 で問題となったケース）
_LOUDNORM_STDERR_WITH_LEADING_BRACE = """\
ffmpeg version 8.1.1 ...
Input #0, {} format ...
{}
[Parsed_loudnorm_0 @ 0000019762f437c0]
{
\t"input_i" : "-21.75",
\t"input_tp" : "-18.06",
\t"input_lra" : "0.00",
\t"input_thresh" : "-31.75",
\t"output_i" : "-14.03",
\t"output_tp" : "-10.27",
\t"output_lra" : "0.00",
\t"output_thresh" : "-24.03",
\t"normalization_type" : "dynamic",
\t"target_offset" : "0.03"
}
size=N/A time=00:00:05.00 ...
"""


class TestLoudnormLeadingBraceRegression:
    """H-1 回帰: stderr 先頭に {} ブロックがあっても末尾 loudnorm JSON を取得できること。

    re.search（先頭一致）から re.findall（全候補から末尾探索）への変更の回帰テスト。
    """

    def test_loudnorm_with_leading_brace_measured_not_none(
        self, tmp_path: Path
    ) -> None:
        """先頭に余計な {} ブロックがある stderr から measured が None でないこと（H-1）。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_WITH_LEADING_BRACE),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        assert result["measured"] is not None, (
            "H-1 回帰: 先頭の {} ブロックにより末尾 loudnorm JSON が取りこぼされた。"
            " re.findall + reversed による末尾探索を確認すること。"
        )

    def test_loudnorm_with_leading_brace_input_i_extracted(
        self, tmp_path: Path
    ) -> None:
        """先頭 {} ブロックがあっても input_i が -21.75 として抽出されること（H-1）。"""
        from clipwright_loudness.analyze import measure_loudness

        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy")

        with (
            patch(
                "clipwright_loudness.analyze.resolve_tool", side_effect=_fake_resolve
            ),
            patch(
                "clipwright_loudness.analyze.run",
                side_effect=_make_run_ok(_LOUDNORM_STDERR_WITH_LEADING_BRACE),
            ),
        ):
            result = measure_loudness(
                media, mode="loudnorm", target_i=-14.0, target_tp=-1.0, target_lra=11.0
            )

        measured = result["measured"]
        assert measured is not None
        assert measured["input_i"] == pytest.approx(-21.75, abs=0.01)
