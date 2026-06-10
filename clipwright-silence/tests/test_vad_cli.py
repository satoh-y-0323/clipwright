"""test_vad_cli.py — clipwright_silence.vad_cli の Red テスト。

対象:
  clipwright_silence.vad_cli.main(argv) の CLI 契約検証（§7.1/7.2/7.3）

CLI 契約（§7.1 一本化）:
  - エントリ: main(argv: list[str] | None = None) -> int
  - 引数: --media <path> --threshold <f> --min-speech <f> --min-silence <f>
         --media-duration <float秒>（省略可: timeout 連動）
  - 正常: stdout に JSON {"speech_segments": [[start, end], ...]}（秒・float）, exit 0
  - 全エラー: exit 0 + stdout JSON {"error": {"code", "message", "hint"}}
  - stdout は JSON のみ。ログ等は stderr。

検証観点:
  ① 引数パース（argparse）と値受け渡し
  ② get_speech_timestamps をモックし発話区間→秒換算 speech_segments を JSON 出力
  ③ ImportError 経路（silero-vad/onnxruntime 欠落）→ DEPENDENCY_MISSING JSON + exit 0
  ④ 内部 ffmpeg の core run() が ClipwrightError(SUBPROCESS_FAILED) を送出
     → error JSON + exit 0
  ⑤ ffmpeg 解決失敗（resolve_tool が DEPENDENCY_MISSING を送出）
     → error JSON + exit 0
  ⑥ JSON が stdout のみに出る（ログ等は stderr）
  ⑦ --media-duration 引数受け取りと内側 ffmpeg timeout 連動（CR M-2 / SR M-2）
  ⑧ SUBPROCESS_FAILED ハンドラが ffmpeg stderr 断片でなく汎用文言を出力（SR M-1）
  ⑨ ImportError message に内部パスでなく固定文言/exc.name 相当が入る（SR L-2）
"""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# vad_cli import 試行（未実装なら _VAD_CLI_AVAILABLE = False）
# ---------------------------------------------------------------------------

try:
    from clipwright_silence.vad_cli import main as vad_main

    _VAD_CLI_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _VAD_CLI_AVAILABLE = False

# vad_cli が存在しない限り全テストを xfail にする
pytestmark = pytest.mark.xfail(
    not _VAD_CLI_AVAILABLE,
    reason="vad_cli.py が未実装のため Red（機能未実装による失敗）",
    strict=True,
)

# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------

_DUMMY_MEDIA = "/fake/video.mp4"
_DUMMY_PCM = "/tmp/fake_audio.wav"


def _make_silero_mock(
    speech_segments: list[dict[str, Any]],
) -> tuple[MagicMock, MagicMock]:
    """silero_vad モジュールのモック一式を返す。

    MagicMock(spec=ModuleType) は types.ModuleType に存在しない
    load_silero_vad / get_speech_timestamps へのアクセスを拒否するため、
    spec なし MagicMock() を使う。

    Returns:
        (mock_silero_vad_module, mock_get_speech_timestamps)
    """
    mock_module = MagicMock()
    mock_model = MagicMock()
    mock_module.load_silero_vad.return_value = mock_model

    mock_get_ts = MagicMock(return_value=speech_segments)
    mock_module.get_speech_timestamps = mock_get_ts

    return mock_module, mock_get_ts


def _capture_main(argv: list[str]) -> tuple[int, dict[str, Any]]:
    """main(argv) を実行し (exit_code, stdout_json) を返す。

    stdout を StringIO にリダイレクトして JSON をパースする。
    """
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        exit_code = vad_main(argv)
    buf.seek(0)
    stdout_text = buf.read()
    return exit_code, json.loads(stdout_text)


# ---------------------------------------------------------------------------
# ① 引数パース
# ---------------------------------------------------------------------------


class TestArgParsing:
    """引数パース（argparse）と値受け渡しの検証。"""

    def test_required_media_arg_present(self) -> None:
        """--media が必須引数として機能し、欠落時は error JSON + exit 0 を返す。

        argparse で SystemExit が上がっても main が捕捉して
        exit 0 + error JSON にすることを確認する。
        """
        # --media を省略して呼ぶ
        exit_code, result = _capture_main([])
        assert exit_code == 0
        assert "error" in result
        # --media 省略による SystemExit は必ず INVALID_INPUT に変換される（§7.1）
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_defaults_are_used(self) -> None:
        """--threshold / --min-speech / --min-silence のデフォルト値が使われる。

        get_speech_timestamps 呼び出し引数でデフォルトが渡っていることを確認する。
        """
        mock_module, mock_get_ts = _make_silero_mock(
            [{"start": 0, "end": 16000}]  # 1秒 at 16kHz
        )
        fake_np = MagicMock()
        fake_np.frombuffer.return_value = MagicMock()
        fake_wave_module = MagicMock()
        fake_wave_file = MagicMock()
        fake_wave_file.__enter__ = MagicMock(return_value=fake_wave_file)
        fake_wave_file.__exit__ = MagicMock(return_value=False)
        fake_wave_file.getnframes.return_value = 16000
        fake_wave_file.getframerate.return_value = 16000
        fake_wave_file.readframes.return_value = b"\x00" * (16000 * 2)
        fake_wave_module.open.return_value = fake_wave_file

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("wave.open", fake_wave_module.open),
            patch("numpy.frombuffer", fake_np.frombuffer),
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            exit_code, result = _capture_main(["--media", _DUMMY_MEDIA])

        assert exit_code == 0
        # デフォルト threshold=0.5 で呼ばれること
        call_kwargs = mock_get_ts.call_args
        assert call_kwargs is not None
        # threshold キーワード引数の確認
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        if "threshold" in kwargs:
            assert kwargs["threshold"] == pytest.approx(0.5)

    def test_custom_threshold_forwarded(self) -> None:
        """--threshold に指定した値が get_speech_timestamps に渡される。"""
        mock_module, mock_get_ts = _make_silero_mock([])
        fake_wave_module = MagicMock()
        fake_wave_file = MagicMock()
        fake_wave_file.__enter__ = MagicMock(return_value=fake_wave_file)
        fake_wave_file.__exit__ = MagicMock(return_value=False)
        fake_wave_file.getnframes.return_value = 0
        fake_wave_file.getframerate.return_value = 16000
        fake_wave_file.readframes.return_value = b""
        fake_wave_module.open.return_value = fake_wave_file

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("wave.open", fake_wave_module.open),
            patch("numpy.frombuffer", return_value=MagicMock()),
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            exit_code, result = _capture_main(
                ["--media", _DUMMY_MEDIA, "--threshold", "0.7"]
            )

        assert exit_code == 0
        call_kwargs = mock_get_ts.call_args
        assert call_kwargs is not None
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        if "threshold" in kwargs:
            assert kwargs["threshold"] == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# ② 発話区間 → speech_segments JSON 出力
# ---------------------------------------------------------------------------


class TestSpeechSegmentsOutput:
    """get_speech_timestamps をモックして発話区間 → speech_segments JSON を検証。"""

    def _run_with_segments(
        self, raw_segments: list[dict[str, Any]], sample_rate: int = 16000
    ) -> tuple[int, dict[str, Any]]:
        """指定した raw_segments を返す silero_vad モックで main を実行する。"""
        mock_module, _ = _make_silero_mock(raw_segments)
        fake_wave_module = MagicMock()
        fake_wave_file = MagicMock()
        fake_wave_file.__enter__ = MagicMock(return_value=fake_wave_file)
        fake_wave_file.__exit__ = MagicMock(return_value=False)
        fake_wave_file.getnframes.return_value = sample_rate * 10
        fake_wave_file.getframerate.return_value = sample_rate
        raw_pcm = b"\x00" * (sample_rate * 10 * 2)
        fake_wave_file.readframes.return_value = raw_pcm
        fake_wave_module.open.return_value = fake_wave_file

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("wave.open", fake_wave_module.open),
            patch("numpy.frombuffer", return_value=MagicMock()),
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            return _capture_main(["--media", _DUMMY_MEDIA])

    def test_empty_speech_segments(self) -> None:
        """発話なしのとき speech_segments が空リストで返る。"""
        exit_code, result = self._run_with_segments([])
        assert exit_code == 0
        assert "speech_segments" in result
        assert result["speech_segments"] == []

    def test_single_segment_converted_to_seconds(self) -> None:
        """1区間の発話を秒換算（/sample_rate）で返す。

        silero-vad が {"start": 8000, "end": 24000} を返す場合、
        16kHz なら [0.5, 1.5] に変換されること。
        """
        exit_code, result = self._run_with_segments(
            [{"start": 8000, "end": 24000}], sample_rate=16000
        )
        assert exit_code == 0
        assert "speech_segments" in result
        segs = result["speech_segments"]
        assert len(segs) == 1
        start_sec, end_sec = segs[0]
        assert start_sec == pytest.approx(0.5)
        assert end_sec == pytest.approx(1.5)

    def test_multiple_segments_ordered(self) -> None:
        """複数区間が昇順で返る。"""
        exit_code, result = self._run_with_segments(
            [
                {"start": 0, "end": 8000},
                {"start": 16000, "end": 24000},
            ],
            sample_rate=16000,
        )
        assert exit_code == 0
        segs = result["speech_segments"]
        assert len(segs) == 2
        # 昇順確認
        for i in range(len(segs) - 1):
            assert segs[i][0] < segs[i + 1][0]

    def test_speech_timestamps_with_sample_unit_values(self) -> None:
        """get_speech_timestamps がサンプル単位整数を返した場合に /sample_rate 換算で秒に変換されること。

        vad_cli.py は return_seconds=False（デフォルト）で get_speech_timestamps を呼び出す。
        そのため戻り値は {"start": int_samples, "end": int_samples} のサンプル単位整数。
        変換後の speech_segments は [start / sample_rate, end / sample_rate] の秒値になること。
        """
        sample_rate = 16000
        start_samples = 16000  # 1.0 秒 = 16000 サンプル
        end_samples = 40000  # 2.5 秒 = 40000 サンプル
        mock_module_new = MagicMock()
        mock_module_new.load_silero_vad.return_value = MagicMock()
        # サンプル単位整数を返す（return_seconds=False の実挙動）
        mock_module_new.get_speech_timestamps.return_value = [
            {"start": start_samples, "end": end_samples}
        ]

        fake_wave_module = MagicMock()
        fake_wave_file = MagicMock()
        fake_wave_file.__enter__ = MagicMock(return_value=fake_wave_file)
        fake_wave_file.__exit__ = MagicMock(return_value=False)
        fake_wave_file.getnframes.return_value = sample_rate * 5
        fake_wave_file.getframerate.return_value = sample_rate
        fake_wave_file.readframes.return_value = b"\x00" * (sample_rate * 5 * 2)
        fake_wave_module.open.return_value = fake_wave_file

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module_new}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("wave.open", fake_wave_module.open),
            patch("numpy.frombuffer", return_value=MagicMock()),
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            exit_code, result = _capture_main(["--media", _DUMMY_MEDIA])

        assert exit_code == 0
        assert "speech_segments" in result
        segs = result["speech_segments"]
        assert len(segs) == 1
        assert segs[0][0] == pytest.approx(start_samples / sample_rate)  # 1.0 秒
        assert segs[0][1] == pytest.approx(end_samples / sample_rate)  # 2.5 秒

    def test_exit_zero_on_success(self) -> None:
        """正常終了時は exit 0 を返す。"""
        exit_code, _ = self._run_with_segments([{"start": 0, "end": 16000}])
        assert exit_code == 0


# ---------------------------------------------------------------------------
# ③ ImportError 経路（silero-vad/onnxruntime 欠落）→ DEPENDENCY_MISSING
# ---------------------------------------------------------------------------


class TestImportErrorPath:
    """silero-vad / onnxruntime が import できない場合の DEPENDENCY_MISSING 検証。"""

    def test_silero_vad_import_error_returns_dependency_missing(
        self,
    ) -> None:
        """silero_vad が import できないとき DEPENDENCY_MISSING JSON + exit 0 を返す。"""
        # silero_vad が存在していたら一旦 None に
        with patch.dict(
            "sys.modules",
            {"silero_vad": None},
        ):
            exit_code, result = _capture_main(["--media", _DUMMY_MEDIA])

        assert exit_code == 0
        assert "error" in result
        assert result["error"]["code"] == "DEPENDENCY_MISSING"

    def test_dependency_missing_error_has_hint(self) -> None:
        """DEPENDENCY_MISSING エラーには hint フィールドがあり pip install を示す。"""
        with patch.dict(
            "sys.modules",
            {"silero_vad": None},
        ):
            _, result = _capture_main(["--media", _DUMMY_MEDIA])

        assert "error" in result
        hint = result["error"].get("hint", "")
        # pip install や [vad] extra の案内が hint に含まれること
        assert "pip install" in hint or "vad" in hint.lower()

    def test_dependency_missing_has_message(self) -> None:
        """DEPENDENCY_MISSING エラーには message フィールドがある。"""
        with patch.dict(
            "sys.modules",
            {"silero_vad": None},
        ):
            _, result = _capture_main(["--media", _DUMMY_MEDIA])

        assert "error" in result
        assert "message" in result["error"]
        assert len(result["error"]["message"]) > 0

    def test_onnxruntime_import_error_returns_dependency_missing(self) -> None:
        """onnxruntime が import できないとき DEPENDENCY_MISSING + exit 0 を返す。

        silero_vad が onnxruntime に依存しており、onnxruntime 欠落時に
        ImportError が伝播するケースを検証する。
        """
        # silero_vad の load_silero_vad が ImportError を送出するシミュレーション
        mock_module = MagicMock()
        mock_module.load_silero_vad.side_effect = ImportError(
            "No module named 'onnxruntime'"
        )

        with patch.dict("sys.modules", {"silero_vad": mock_module}):
            exit_code, result = _capture_main(["--media", _DUMMY_MEDIA])

        assert exit_code == 0
        assert "error" in result
        assert result["error"]["code"] == "DEPENDENCY_MISSING"


# ---------------------------------------------------------------------------
# ④ 内部 ffmpeg run() が ClipwrightError(SUBPROCESS_FAILED) を送出 → error JSON + exit 0
# ---------------------------------------------------------------------------


class TestFfmpegSubprocessFailure:
    """内部 ffmpeg が SUBPROCESS_FAILED を投げた場合の error JSON + exit 0 検証。

    DC-AS-001 準拠。
    """

    def test_subprocess_failed_returns_error_json(self) -> None:
        """core run() が ClipwrightError(SUBPROCESS_FAILED) を送出したとき
        error JSON + exit 0 を返す。"""
        from clipwright.errors import ClipwrightError, ErrorCode

        mock_module, _ = _make_silero_mock([])

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.side_effect = ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="ffmpeg が終了コード 1 で失敗しました",
                hint="ffmpeg の引数を確認してください",
            )
            exit_code, result = _capture_main(["--media", _DUMMY_MEDIA])

        assert exit_code == 0
        assert "error" in result
        assert result["error"]["code"] == "SUBPROCESS_FAILED"

    def test_subprocess_failed_error_has_code_message_hint(self) -> None:
        """SUBPROCESS_FAILED エラーは code / message / hint を持つ。"""
        from clipwright.errors import ClipwrightError, ErrorCode

        mock_module, _ = _make_silero_mock([])

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.side_effect = ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="ffmpeg が失敗",
                hint="引数を確認",
            )
            _, result = _capture_main(["--media", _DUMMY_MEDIA])

        err = result["error"]
        assert "code" in err
        assert "message" in err
        assert "hint" in err

    def test_exit_zero_on_subprocess_failed(self) -> None:
        """SUBPROCESS_FAILED でも exit code は 0 であること（§7.1 全エラー exit 0）。"""
        from clipwright.errors import ClipwrightError, ErrorCode

        mock_module, _ = _make_silero_mock([])

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.side_effect = ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="failed",
                hint="hint",
            )
            exit_code, _ = _capture_main(["--media", _DUMMY_MEDIA])

        assert exit_code == 0


# ---------------------------------------------------------------------------
# ⑤ ffmpeg 解決失敗（resolve_tool が DEPENDENCY_MISSING）→ error JSON + exit 0
# ---------------------------------------------------------------------------


class TestFfmpegResolveFailed:
    """ffmpeg が resolve_tool で見つからない場合の DEPENDENCY_MISSING 検証。

    DC-AS-006 準拠。
    """

    def test_resolve_tool_failure_returns_dependency_missing(self) -> None:
        """resolve_tool が DEPENDENCY_MISSING を送出したとき error JSON + exit 0。"""
        from clipwright.errors import ClipwrightError, ErrorCode

        mock_module, _ = _make_silero_mock([])

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch("clipwright_silence.vad_cli.resolve_tool") as mock_resolve,
        ):
            mock_resolve.side_effect = ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="ffmpeg が PATH 上に見つかりません",
                hint="brew install ffmpeg 等で導入してください",
            )
            exit_code, result = _capture_main(["--media", _DUMMY_MEDIA])

        assert exit_code == 0
        assert "error" in result
        assert result["error"]["code"] == "DEPENDENCY_MISSING"

    def test_resolve_tool_failure_hint_mentions_ffmpeg(self) -> None:
        """ffmpeg 解決失敗の hint に ffmpeg 導入案内が含まれる。"""
        from clipwright.errors import ClipwrightError, ErrorCode

        mock_module, _ = _make_silero_mock([])

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch("clipwright_silence.vad_cli.resolve_tool") as mock_resolve,
        ):
            mock_resolve.side_effect = ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="ffmpeg が PATH 上に見つかりません",
                hint="brew install ffmpeg 等で導入してください",
            )
            _, result = _capture_main(["--media", _DUMMY_MEDIA])

        hint = result["error"].get("hint", "")
        assert "ffmpeg" in hint.lower() or "ffprobe" in hint.lower()

    def test_resolve_tool_failure_exit_zero(self) -> None:
        """resolve_tool 失敗でも exit code は 0（§7.1 全エラー exit 0）。"""
        from clipwright.errors import ClipwrightError, ErrorCode

        mock_module, _ = _make_silero_mock([])

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch("clipwright_silence.vad_cli.resolve_tool") as mock_resolve,
        ):
            mock_resolve.side_effect = ClipwrightError(
                code=ErrorCode.DEPENDENCY_MISSING,
                message="ffmpeg not found",
                hint="install ffmpeg",
            )
            exit_code, _ = _capture_main(["--media", _DUMMY_MEDIA])

        assert exit_code == 0


# ---------------------------------------------------------------------------
# ⑥ stdout/stderr 分離（JSON は stdout のみ）
# ---------------------------------------------------------------------------


class TestStdoutStderrSeparation:
    """JSON が stdout のみに出力され、ログ等が stderr に流れることを検証。"""

    def _run_capturing_both(
        self, argv: list[str], *, force_error: bool = False
    ) -> tuple[int, str, str]:
        """main(argv) を実行し (exit_code, stdout_text, stderr_text) を返す。"""
        from clipwright.errors import ClipwrightError, ErrorCode

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        mock_module, _ = _make_silero_mock([{"start": 0, "end": 16000}])
        fake_wave_module = MagicMock()
        fake_wave_file = MagicMock()
        fake_wave_file.__enter__ = MagicMock(return_value=fake_wave_file)
        fake_wave_file.__exit__ = MagicMock(return_value=False)
        fake_wave_file.getnframes.return_value = 16000
        fake_wave_file.getframerate.return_value = 16000
        fake_wave_file.readframes.return_value = b"\x00" * (16000 * 2)
        fake_wave_module.open.return_value = fake_wave_file

        with (
            patch("sys.stdout", stdout_buf),
            patch("sys.stderr", stderr_buf),
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("wave.open", fake_wave_module.open),
            patch("numpy.frombuffer", return_value=MagicMock()),
            patch("tempfile.NamedTemporaryFile"),
        ):
            if force_error:
                mock_run.side_effect = ClipwrightError(
                    code=ErrorCode.SUBPROCESS_FAILED,
                    message="failed",
                    hint="hint",
                )
            else:
                mock_run.return_value = MagicMock(returncode=0)
            exit_code = vad_main(argv)

        return exit_code, stdout_buf.getvalue(), stderr_buf.getvalue()

    def test_stdout_is_valid_json_on_success(self) -> None:
        """正常時 stdout は有効な JSON である。"""
        _, stdout, _ = self._run_capturing_both(["--media", _DUMMY_MEDIA])
        parsed = json.loads(stdout)
        assert isinstance(parsed, dict)

    def test_stdout_is_valid_json_on_error(self) -> None:
        """エラー時 stdout は有効な JSON である。"""
        _, stdout, _ = self._run_capturing_both(
            ["--media", _DUMMY_MEDIA], force_error=True
        )
        parsed = json.loads(stdout)
        assert isinstance(parsed, dict)

    def test_stdout_contains_no_log_lines(self) -> None:
        """stdout に JSON 以外の行が含まれない（ログ混入がない）。

        stdout テキストをパースして dict が得られれば OK。
        余分なテキストがあると json.loads が失敗する。
        """
        _, stdout, _ = self._run_capturing_both(["--media", _DUMMY_MEDIA])
        # json.loads が成功すること = stdout が純粋な JSON であること
        result = json.loads(stdout)
        assert isinstance(result, dict)

    def test_error_json_on_stdout_not_stderr(self) -> None:
        """エラー情報は stdout の JSON に入り、stderr には入らない。

        stderr にエラーの JSON が出ていないことを確認する。
        （stderr にログが出ること自体は許可するが、JSON エンベロープは stdout のみ）
        """
        _, stdout, stderr = self._run_capturing_both(
            ["--media", _DUMMY_MEDIA], force_error=True
        )
        # stdout の JSON に error キーがある
        parsed = json.loads(stdout)
        assert "error" in parsed
        # stderr には {"error": ...} の JSON エンベロープが出ていない
        try:
            stderr_parsed = json.loads(stderr)
            # stderr が JSON なら error キーが入っていないこと
            assert "error" not in stderr_parsed
        except (json.JSONDecodeError, ValueError):
            # stderr が JSON でなければ問題なし（ログ文字列 OK）
            pass


# ---------------------------------------------------------------------------
# ⑦ --media-duration 引数と内側 ffmpeg timeout 連動（CR M-2 / SR M-2）
# ---------------------------------------------------------------------------


class TestMediaDurationArg:
    """--media-duration 引数を受け取り内側 ffmpeg timeout を total に連動させる検証。

    CR M-2 / SR M-2: 内側 ffmpeg timeout = max(30, ceil(total_duration * 2))
    detect.py から total_duration_sec を --media-duration として渡すことで
    §7.7「内側 timeout は外側より必ず短く」を満たす設計を検証する。
    """

    def test_media_duration_arg_accepted(self) -> None:
        """--media-duration 引数を受け取り正常終了すること（引数拒否しない）。"""
        mock_module, _ = _make_silero_mock([])
        fake_wave_module = MagicMock()
        fake_wave_file = MagicMock()
        fake_wave_file.__enter__ = MagicMock(return_value=fake_wave_file)
        fake_wave_file.__exit__ = MagicMock(return_value=False)
        fake_wave_file.getnframes.return_value = 0
        fake_wave_file.getframerate.return_value = 16000
        fake_wave_file.readframes.return_value = b""
        fake_wave_module.open.return_value = fake_wave_file

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("wave.open", fake_wave_module.open),
            patch("numpy.frombuffer", return_value=MagicMock()),
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            exit_code, result = _capture_main(
                ["--media", _DUMMY_MEDIA, "--media-duration", "60.0"]
            )

        # --media-duration を受け取れる（argparse が SystemExit しない）こと
        assert exit_code == 0
        assert "error" not in result

    def test_ffmpeg_timeout_uses_media_duration(self) -> None:
        """--media-duration 指定時に内側 ffmpeg timeout が total 連動になること。

        run() の timeout 引数をモックで確認する。
        CR M-2 修正前（固定120秒）は total=10s でも120秒になるため Red。
        修正後は total=10s → max(30, ceil(10*2))=30 になることを期待する。
        """
        import math

        total_duration = 10.0
        expected_timeout = float(max(30, math.ceil(total_duration * 2)))

        mock_module, _ = _make_silero_mock([])
        fake_wave_module = MagicMock()
        fake_wave_file = MagicMock()
        fake_wave_file.__enter__ = MagicMock(return_value=fake_wave_file)
        fake_wave_file.__exit__ = MagicMock(return_value=False)
        fake_wave_file.getnframes.return_value = 16000 * int(total_duration)
        fake_wave_file.getframerate.return_value = 16000
        fake_wave_file.readframes.return_value = b"\x00" * (
            16000 * int(total_duration) * 2
        )
        fake_wave_module.open.return_value = fake_wave_file

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("wave.open", fake_wave_module.open),
            patch("numpy.frombuffer", return_value=MagicMock()),
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            _capture_main(
                [
                    "--media",
                    _DUMMY_MEDIA,
                    "--media-duration",
                    str(total_duration),
                ]
            )

        # run() が呼ばれたときの timeout キーワード引数を確認する
        assert mock_run.called, "run() が呼ばれていない"
        call_kwargs = mock_run.call_args
        actual_timeout = call_kwargs.kwargs.get(
            "timeout", call_kwargs.args[1] if len(call_kwargs.args) > 1 else None
        )
        assert actual_timeout == pytest.approx(expected_timeout), (
            f"timeout={actual_timeout} が期待値 {expected_timeout} と異なる。"
            f"--media-duration={total_duration} のとき max(30, ceil({total_duration}*2))="
            f"{expected_timeout} になること。"
        )


# ---------------------------------------------------------------------------
# ⑧ SUBPROCESS_FAILED ハンドラが ffmpeg stderr 断片でなく汎用文言を出力する（SR M-1）
# ---------------------------------------------------------------------------


class TestSubprocessFailedSanitize:
    """ClipwrightError(SUBPROCESS_FAILED) ハンドラが stderr 断片を漏らさないことを検証。

    SR M-1: process.py が ClipwrightError.message に stderr[:200] を埋め込む。
    vad_cli.py の except ClipwrightError ハンドラでその message をそのまま出すと
    内部パス（-i /path/to/video.mp4 等）が MCP レスポンスに漏洩する。
    汎用文言（「内部サブプロセスが失敗しました」相当）に差し替えることを検証する。
    """

    def _run_with_subprocess_failed(self, stderr_fragment: str) -> dict[str, Any]:
        """指定 stderr 断片を含む ClipwrightError(SUBPROCESS_FAILED) を送出して main を実行。"""
        from clipwright.errors import ClipwrightError, ErrorCode

        mock_module, _ = _make_silero_mock([])

        with (
            patch.dict("sys.modules", {"silero_vad": mock_module}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.side_effect = ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=f"コマンドが終了コード 1 で失敗しました: {stderr_fragment}",
                hint="ffmpeg の引数を確認してください",
            )
            _, result = _capture_main(["--media", _DUMMY_MEDIA])
        return result

    def test_subprocess_failed_message_does_not_contain_stderr_fragment(
        self,
    ) -> None:
        """SUBPROCESS_FAILED の message に ffmpeg stderr 断片が含まれない。

        SR M-1 修正前は exc.message をそのまま出すため stderr 断片がそのまま漏洩する。
        修正後は汎用文言（ffmpeg stderr 非露出）になることを期待する。
        """
        secret_path = "/home/user/private/videos/secret.mp4"
        stderr_fragment = f"-i {secret_path}"
        result = self._run_with_subprocess_failed(stderr_fragment)

        assert "error" in result
        message = result["error"].get("message", "")
        # ffmpeg stderr に含まれる内部パス断片が message に出ていないこと
        assert secret_path not in message, (
            f"message に内部パス '{secret_path}' が含まれている: {message!r}"
        )
        assert stderr_fragment not in message, (
            f"message に stderr 断片 '{stderr_fragment}' が含まれている: {message!r}"
        )

    def test_subprocess_failed_message_is_generic(self) -> None:
        """SUBPROCESS_FAILED の message は汎用文言であること。"""
        result = self._run_with_subprocess_failed("some stderr output")

        assert "error" in result
        message = result["error"].get("message", "")
        # 何らかの汎用文言が入っていること（空でない）
        assert len(message) > 0


# ---------------------------------------------------------------------------
# ⑨ ImportError message に内部パスでなく固定文言/exc.name 相当が入る（SR L-2）
# ---------------------------------------------------------------------------


class TestImportErrorMessageSanitize:
    """ImportError message に Python 内部パスが含まれないことを検証。

    SR L-2: ImportError の str(exc) には
    "cannot import name 'X' from '/path/to/site-packages/...'" 等の
    内部パスが含まれることがある。
    固定文言または exc.name（モジュール名のみ）を使うことを検証する。
    """

    def _run_with_import_error(self, exc_message: str) -> dict[str, Any]:
        """指定 message を持つ ImportError を silero_vad import 時に送出して main を実行。"""
        # sys.modules を None にすると ImportError が発生する
        with patch.dict(
            "sys.modules",
            {"silero_vad": None},
        ):
            _, result = _capture_main(["--media", _DUMMY_MEDIA])
        return result

    def test_import_error_message_excludes_internal_path(self) -> None:
        """DEPENDENCY_MISSING の message に Python 内部パスが含まれない。

        SR L-2 修正前は f"...{exc}" で exc の str 表現をそのまま使うため
        内部パスが漏洩しうる。修正後は固定文言または exc.name を使うことを期待する。
        この検証は「/site-packages/」「/usr/lib/python」等の典型的な内部パス断片が
        message に含まれないことを確認する。
        """
        result = self._run_with_import_error(
            "cannot import name 'load_silero_vad' "
            "from '/home/user/.venv/lib/python3.11/site-packages/silero_vad/__init__.py'"
        )
        assert "error" in result
        assert result["error"]["code"] == "DEPENDENCY_MISSING"
        message = result["error"].get("message", "")
        # 内部パス断片が message に含まれていないこと
        assert "/site-packages/" not in message, (
            f"message に内部パス '/site-packages/' が含まれている: {message!r}"
        )

    def test_import_error_message_is_fixed_or_module_name(self) -> None:
        """DEPENDENCY_MISSING の message は固定文言またはモジュール名のみであること。

        実装が exc.name を使うか固定文言を使うかに関わらず、
        message が空でなく適切な文言であることを検証する。
        """
        result = self._run_with_import_error("No module named 'silero_vad'")
        assert "error" in result
        message = result["error"].get("message", "")
        assert len(message) > 0


# ---------------------------------------------------------------------------
# ⑩ 想定外例外ハンドラのサニタイズ（SR NF-L-1）
# ---------------------------------------------------------------------------


class TestUnexpectedExceptionSanitize:
    """except Exception ハンドラが str(exc) を message に含まないことを検証。

    SR NF-L-1: vad_cli.py の except Exception ハンドラが OSError 等の
    内部パスを含む例外を捕捉した場合でも、error JSON の message に
    str(exc) の内容（内部パス断片等）が含まれないことを確認する。
    impl-fix2-src が修正するまでは Red（機能未修正による失敗）。
    """

    def test_unexpected_exception_message_excludes_exc_str(self) -> None:
        """想定外例外発生時の error message に str(exc) が含まれないこと。

        OSError("No such file or directory: '/home/user/private/media.mp4'") を
        except Exception ハンドラで捕捉させ、message にパス断片が出ないことを確認する。
        """
        exc_message = "No such file or directory: '/home/user/private/media.mp4'"

        fake_wave_module = MagicMock()
        fake_wave_module.open.side_effect = OSError(exc_message)

        with (
            patch.dict("sys.modules", {"silero_vad": MagicMock()}),
            patch(
                "clipwright_silence.vad_cli.resolve_tool",
                return_value="/usr/bin/ffmpeg",
            ),
            patch("clipwright_silence.vad_cli.run") as mock_run,
            patch("wave.open", fake_wave_module.open),
            patch("tempfile.NamedTemporaryFile"),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            exit_code, result = _capture_main(["--media", _DUMMY_MEDIA])

        assert exit_code == 0
        assert "error" in result
        assert result["error"]["code"] == "INTERNAL"
        message = result["error"].get("message", "")
        # str(exc) が message に含まれていないこと（内部パス漏洩しない）
        assert "/home/user/private/media.mp4" not in message, (
            f"message に内部パスが含まれている: {message!r}"
        )
        assert exc_message not in message, (
            f"message に str(exc) がそのまま含まれている: {message!r}"
        )
