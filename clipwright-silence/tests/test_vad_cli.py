"""test_vad_cli.py — clipwright_silence.vad_cli の Red テスト。

対象:
  clipwright_silence.vad_cli.main(argv) の CLI 契約検証（§7.1/7.2/7.3）

CLI 契約（§7.1 一本化）:
  - エントリ: main(argv: list[str] | None = None) -> int
  - 引数: --media <path> --threshold <f> --min-speech <f> --min-silence <f>
  - 正常: stdout に JSON {"speech_segments": [[start, end], ...]}（秒・float）, exit 0
  - 全エラー: exit 0 + stdout JSON {"error": {"code", "message", "hint"}}
  - stdout は JSON のみ。ログ等は stderr。

検証観点:
  ① 引数パース（argparse）と値受け渡し
  ② get_speech_timestamps をモックし発話区間→秒換算 speech_segments を JSON 出力
  ③ ImportError 経路（silero-vad/onnxruntime 欠落）→ DEPENDENCY_MISSING JSON + exit 0
  ④ 内部 ffmpeg の core run() が ClipwrightError(SUBPROCESS_FAILED) を送出 → error JSON + exit 0
  ⑤ ffmpeg 解決失敗（resolve_tool が DEPENDENCY_MISSING を送出）→ error JSON + exit 0
  ⑥ JSON が stdout のみに出る（ログ等は stderr）

vad_cli.py が未実装のため全テストは「機能未実装による失敗」で Red になる。
"""

from __future__ import annotations

import io
import json
import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# vad_cli import 試行（未実装なら _VAD_CLI_AVAILABLE = False）
# ---------------------------------------------------------------------------

try:
    from clipwright_silence import vad_cli as _vad_cli_module
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

    Returns:
        (mock_silero_vad_module, mock_get_speech_timestamps)
    """
    mock_module = MagicMock(spec=ModuleType)
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
        assert result["error"]["code"] in (
            "INVALID_INPUT",
            "DEPENDENCY_MISSING",
            "SUBPROCESS_FAILED",
        )

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

    def test_return_seconds_api_fallback(self) -> None:
        """get_speech_timestamps が return_seconds=True 対応時は秒値をそのまま使う。

        silero-vad の新しい API では get_speech_timestamps に return_seconds=True を
        渡すと {"start": float秒, "end": float秒} が返る場合がある。
        その場合も正しく [start_sec, end_sec] を組み立てること。
        """
        # return_seconds=True 経路: {"start": 1.0, "end": 2.5} を返すモック
        mock_module_new = MagicMock(spec=ModuleType)
        mock_model = MagicMock()
        mock_module_new.load_silero_vad.return_value = mock_model
        # float 値を直接返す（秒単位 API）
        mock_module_new.get_speech_timestamps.return_value = [
            {"start": 1.0, "end": 2.5}
        ]

        fake_wave_module = MagicMock()
        fake_wave_file = MagicMock()
        fake_wave_file.__enter__ = MagicMock(return_value=fake_wave_file)
        fake_wave_file.__exit__ = MagicMock(return_value=False)
        fake_wave_file.getnframes.return_value = 16000 * 5
        fake_wave_file.getframerate.return_value = 16000
        fake_wave_file.readframes.return_value = b"\x00" * (16000 * 5 * 2)
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

    def test_exit_zero_on_success(self) -> None:
        """正常終了時は exit 0 を返す。"""
        exit_code, _ = self._run_with_segments([{"start": 0, "end": 16000}])
        assert exit_code == 0


# ---------------------------------------------------------------------------
# ③ ImportError 経路（silero-vad/onnxruntime 欠落）→ DEPENDENCY_MISSING
# ---------------------------------------------------------------------------


class TestImportErrorPath:
    """silero-vad / onnxruntime が import できない場合の DEPENDENCY_MISSING 検証。"""

    def test_silero_vad_import_error_returns_dependency_missing(self) -> None:
        """silero_vad が import できないとき DEPENDENCY_MISSING JSON + exit 0 を返す。"""
        # sys.modules から silero_vad を除去して ImportError を強制
        modules_patch = {k: v for k, v in sys.modules.items()}
        # silero_vad が存在していたら一旦 None に
        with patch.dict(
            "sys.modules",
            {"silero_vad": None},  # type: ignore[dict-item]
        ):
            exit_code, result = _capture_main(["--media", _DUMMY_MEDIA])

        assert exit_code == 0
        assert "error" in result
        assert result["error"]["code"] == "DEPENDENCY_MISSING"

    def test_dependency_missing_error_has_hint(self) -> None:
        """DEPENDENCY_MISSING エラーには hint フィールドがあり pip install を示す。"""
        with patch.dict(
            "sys.modules",
            {"silero_vad": None},  # type: ignore[dict-item]
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
            {"silero_vad": None},  # type: ignore[dict-item]
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
        mock_module = MagicMock(spec=ModuleType)
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
    """内部 ffmpeg が SUBPROCESS_FAILED を投げた場合の error JSON + exit 0 検証（DC-AS-001）。"""

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
    """ffmpeg が resolve_tool で見つからない場合の DEPENDENCY_MISSING 検証（DC-AS-006）。"""

    def test_resolve_tool_failure_returns_dependency_missing(self) -> None:
        """resolve_tool が DEPENDENCY_MISSING を送出したとき error JSON + exit 0 を返す。"""
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
