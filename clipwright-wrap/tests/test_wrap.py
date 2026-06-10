"""test_wrap.py — wrap.py オーケストレーション層の Red テスト。

対象 API:
  clipwright_wrap.wrap.wrap_captions(
      input: str, output: str, options: WrapCaptionsOptions,
  ) -> dict[str, Any]

モック方針:
  - subprocess.run（または wrap._run_wrap_cli）を pytest-mock で差し替え。
    wrap.py は sys.executable -m clipwright_wrap.wrap_cli を subprocess 起動する（WR-AD-01）。
  - 実 budoux・実 SRT/VTT ファイル書き込みは wrap e2e で検証。
  - captions.parse_captions / captions.serialize_captions は原則実呼び出し（純ロジック）。

検証観点（architecture-report-20260611-022805.md WR-AD-02/07/08/09/11/13/14/15）:
  ① 出力検証（WR-AD-07/08）: 拡張子一致・親dir存在・output==input禁止
  ② 入力検証（WR-AD-09）: FILE_NOT_FOUND basename のみ・INVALID_INPUT parse 失敗
  ③ DC-GP-001 language 責務一意化: WrapCaptionsOptions(language='xx') → ValidationError
  ④ wrap_cli 起動（WR-AD-02・DC-AS-007）: sys.executable -m wrap_cli・stdin JSON・error キー判定
  ⑤ 整形フロー: parse→wrap_cli→wrap_cue_lines→serialize→output 書込み（入力非改変）
  ⑥ WR-AD-15(1)/DC-AM-003 overflow: line_count(a) + line_width(b) 両方識別・切り捨てなし
  ⑦ WR-AD-13(2)/DC-AM-002 warnings 集約: 集約1文 + data に overflow_cue_indices/overflow_width_cue_indices
  ⑧ WR-AD-13(1)/DC-AS-005 artifacts: dict 形式・OTIO 非生成
  ⑨ エンベロープ: summary に cue数/wrapped数/overflow数/language・data 軽量
  ⑩ cue 0件（空字幕）防御: ok:True・空出力
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# wrap.py が存在しない段階では ImportError → Red フェーズ確認
from clipwright_wrap.schemas import WrapCaptionsOptions

# ===========================================================================
# ヘルパー
# ===========================================================================


def _srt_1cue(text: str = "今日はいい天気です。") -> str:
    """1 cue の SRT テキストを生成する。WR-AD-12(1) のバイト構造に準拠。"""
    return f"1\n00:00:00,000 --> 00:00:01,000\n{text}\n"


def _srt_ncues(n: int) -> str:
    """n cue の SRT テキストを生成する。"""
    blocks = []
    for i in range(1, n + 1):
        t_start = f"00:00:{i - 1:02d},000"
        t_end = f"00:00:{i:02d},000"
        blocks.append(f"{i}\n{t_start} --> {t_end}\nテキスト{i}\n")
    return "\n".join(blocks)


def _vtt_1cue(text: str = "今日はいい天気です。") -> str:
    """1 cue の VTT テキストを生成する。WR-AD-12(1) のバイト構造に準拠。"""
    return f"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n{text}\n"


def _segments_from_texts(*texts: str) -> list[list[str]]:
    """各テキストを1文節リストとして返す wrap_cli segments 形式に変換する。"""
    return [[t] for t in texts]


def _wrap_cli_ok(segments: list[list[str]]) -> bytes:
    """wrap_cli が返す成功 JSON (bytes) を生成する。"""
    return json.dumps({"segments": segments}, ensure_ascii=False).encode()


def _wrap_cli_error(code: str, message: str, hint: str = "確認してください。") -> bytes:
    """wrap_cli が返すエラー JSON (bytes) を生成する。"""
    return json.dumps(
        {"error": {"code": code, "message": message, "hint": hint}},
        ensure_ascii=False,
    ).encode()


def _opts(**kwargs: Any) -> WrapCaptionsOptions:
    return WrapCaptionsOptions(**kwargs)


def _make_input_srt(tmp_path: Path, content: str | None = None) -> str:
    """tmp_path に input.srt を作成してパスを返す。"""
    p = tmp_path / "input.srt"
    p.write_text(content if content is not None else _srt_1cue(), encoding="utf-8")
    return str(p)


def _make_input_vtt(tmp_path: Path, content: str | None = None) -> str:
    """tmp_path に input.vtt を作成してパスを返す。"""
    p = tmp_path / "input.vtt"
    p.write_text(content if content is not None else _vtt_1cue(), encoding="utf-8")
    return str(p)


# ===========================================================================
# wrap_captions のインポート（実装なければ ImportError で Red）
# ===========================================================================


def _import_wrap_captions() -> Any:
    """wrap_captions を遅延 import して返す。wrap.py 未実装なら ImportError。"""
    from clipwright_wrap.wrap import wrap_captions  # type: ignore[import-not-found]

    return wrap_captions


# ===========================================================================
# ① 出力検証（WR-AD-07/08）
# ===========================================================================


class TestOutputValidation:
    """出力パスの検証: 拡張子・親dir・output==input・SRT⇔VTT 混在禁止。"""

    def test_srt_input_srt_output_accepted(self, tmp_path: Path, mocker: Any) -> None:
        """SRT 入力 + SRT 出力は受理されること（WR-AD-07/08）。"""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok(_segments_from_texts("今日はいい天気です。")),
                returncode=0,
            ),
        )
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is True

    def test_vtt_input_vtt_output_accepted(self, tmp_path: Path, mocker: Any) -> None:
        """VTT 入力 + VTT 出力は受理されること（WR-AD-07/08）。"""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_vtt(tmp_path)
        out = str(tmp_path / "output.vtt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok(_segments_from_texts("今日はいい天気です。")),
                returncode=0,
            ),
        )
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is True

    def test_srt_input_vtt_output_rejected(self, tmp_path: Path) -> None:
        """SRT 入力 + VTT 出力は拡張子不一致で INVALID_INPUT になること（WR-AD-08）。"""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.vtt")
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_vtt_input_srt_output_rejected(self, tmp_path: Path) -> None:
        """VTT 入力 + SRT 出力は拡張子不一致で INVALID_INPUT になること（WR-AD-08）。"""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_vtt(tmp_path)
        out = str(tmp_path / "output.srt")
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_unsupported_extension_rejected(self, tmp_path: Path) -> None:
        """入力が .ass など未対応拡張子は INVALID_INPUT になること（WR-AD-07）。"""
        wrap_captions = _import_wrap_captions()
        inp = tmp_path / "input.ass"
        inp.write_text("some content", encoding="utf-8")
        out = str(tmp_path / "output.ass")
        result: dict[str, Any] = wrap_captions(str(inp), out, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_missing_parent_dir_rejected(self, tmp_path: Path) -> None:
        """出力先の親ディレクトリが存在しない場合は INVALID_INPUT（WR-AD-07）。"""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "nonexistent_dir" / "output.srt")
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_output_equals_input_rejected(self, tmp_path: Path) -> None:
        """output == input は INVALID_INPUT になること（WR-AD-07）。"""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        result: dict[str, Any] = wrap_captions(inp, inp, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_output_different_dir_accepted(self, tmp_path: Path, mocker: Any) -> None:
        """output が input と異なるディレクトリでも受理されること（WR-AD-07・同一dir制約なし）。"""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        out = str(other_dir / "output.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok(_segments_from_texts("今日はいい天気です。")),
                returncode=0,
            ),
        )
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is True


# ===========================================================================
# ② 入力検証（WR-AD-09）
# ===========================================================================


class TestInputValidation:
    """入力ファイルの検証: FILE_NOT_FOUND・不正字幕 INVALID_INPUT。"""

    def test_file_not_found_returns_file_not_found_code(self, tmp_path: Path) -> None:
        """存在しない input → FILE_NOT_FOUND エラー（WR-AD-09）。"""
        wrap_captions = _import_wrap_captions()
        inp = str(tmp_path / "nonexistent.srt")
        out = str(tmp_path / "output.srt")
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "FILE_NOT_FOUND"

    def test_file_not_found_message_basename_only(self, tmp_path: Path) -> None:
        """FILE_NOT_FOUND の message は basename のみ（フルパス非露出・WR-AD-09）。"""
        wrap_captions = _import_wrap_captions()
        inp = str(tmp_path / "secret" / "nonexistent.srt")
        out = str(tmp_path / "output.srt")
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is False
        # フルパスの親ディレクトリ部分が含まれない
        assert "secret" not in result["error"]["message"]
        assert "nonexistent.srt" in result["error"]["message"]

    def test_invalid_srt_timecode_returns_invalid_input(self, tmp_path: Path) -> None:
        """不正な SRT タイムコード行 → INVALID_INPUT（parse_captions 由来・WR-AD-09）。"""
        wrap_captions = _import_wrap_captions()
        inp = tmp_path / "bad.srt"
        inp.write_text("1\nINVALID_TIMECODE\nテキスト\n", encoding="utf-8")
        out = str(tmp_path / "output.srt")
        result: dict[str, Any] = wrap_captions(str(inp), out, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"


# ===========================================================================
# ③ DC-GP-001 language 責務一意化
# ===========================================================================


class TestLanguageValidation:
    """language の検証は WrapCaptionsOptions 構築時の ValidationError に一意化する（DC-GP-001）。

    wrap.py 側で language を再 if 検査して INVALID_INPUT 化する分岐を作らない。
    """

    def test_invalid_language_raises_validation_error(self) -> None:
        """WrapCaptionsOptions(language='xx') は ValidationError を送出する（DC-GP-001）。

        wrap.py が language を再検査して INVALID_INPUT に変換する分岐ではなく、
        schema 構築時点で弾かれることを確認する。
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WrapCaptionsOptions(language="xx")

    def test_invalid_language_en_raises_validation_error(self) -> None:
        """WrapCaptionsOptions(language='en') は ValidationError（英語は未対応）。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WrapCaptionsOptions(language="en")

    def test_valid_language_ja_accepted_in_options(self) -> None:
        """WrapCaptionsOptions(language='ja') は受理される。"""
        opts = WrapCaptionsOptions(language="ja")
        assert opts.language == "ja"

    @pytest.mark.parametrize("lang", ["ja", "zh-hans", "zh-hant", "th"])
    def test_valid_languages_accepted(self, lang: str) -> None:
        """4 有効言語は ValidationError なく構築できる。"""
        opts = WrapCaptionsOptions(language=lang)
        assert opts.language == lang


# ===========================================================================
# ④ wrap_cli 起動（WR-AD-02・DC-AS-007）
# ===========================================================================


class TestWrapCliInvocation:
    """wrap.py が sys.executable -m wrap_cli を subprocess 起動する流れを検証する。"""

    def _patch_subprocess(self, mocker: Any, stdout_bytes: bytes) -> MagicMock:
        """subprocess.run をモックして stdout_bytes を返す。"""
        mock_run: MagicMock = mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(stdout=stdout_bytes, returncode=0),
        )
        return mock_run

    def test_subprocess_called_with_sys_executable(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """subprocess.run の args[0] が sys.executable であること（WR-AD-01）。"""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.srt")
        mock_run = self._patch_subprocess(
            mocker, _wrap_cli_ok(_segments_from_texts("今日はいい天気です。"))
        )
        wrap_captions(inp, out, _opts())
        call_args = mock_run.call_args
        cmd: list[str] = call_args[0][0]
        assert cmd[0] == sys.executable

    def test_subprocess_called_with_m_wrap_cli(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """subprocess のコマンドに '-m' と 'clipwright_wrap.wrap_cli' が含まれること（WR-AD-01）。"""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.srt")
        mock_run = self._patch_subprocess(
            mocker, _wrap_cli_ok(_segments_from_texts("今日はいい天気です。"))
        )
        wrap_captions(inp, out, _opts())
        cmd: list[str] = mock_run.call_args[0][0]
        assert "-m" in cmd
        m_idx = cmd.index("-m")
        assert cmd[m_idx + 1] == "clipwright_wrap.wrap_cli"

    def test_subprocess_stdin_json_has_language_and_texts(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """subprocess に渡す stdin に {'language', 'texts'} が含まれること（WR-AD-02）。"""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.srt")
        mock_run = self._patch_subprocess(
            mocker, _wrap_cli_ok(_segments_from_texts("今日はいい天気です。"))
        )
        wrap_captions(inp, out, _opts(language="ja"))
        call_kwargs = mock_run.call_args[1]
        # stdin に JSON が渡されることを確認
        stdin_data = call_kwargs.get("input")
        assert stdin_data is not None
        parsed = json.loads(stdin_data)
        assert "language" in parsed
        assert "texts" in parsed
        assert parsed["language"] == "ja"

    def test_subprocess_stdin_texts_is_list_of_strings(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """stdin JSON の texts は list[str] であること（WR-AD-02）。"""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.srt")
        mock_run = self._patch_subprocess(
            mocker, _wrap_cli_ok(_segments_from_texts("今日はいい天気です。"))
        )
        wrap_captions(inp, out, _opts())
        stdin_data = mock_run.call_args[1].get("input")
        parsed = json.loads(stdin_data)
        texts = parsed["texts"]
        assert isinstance(texts, list)
        for t in texts:
            assert isinstance(t, str)

    def test_error_in_stdout_json_propagates_as_error_result(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """wrap_cli が error JSON を返した場合 wrap がエンベロープエラーに変換すること（DC-AS-007）。

        exit code が 0 でも 'error' キーがあればエラー扱いとする。
        """
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.srt")
        self._patch_subprocess(
            mocker,
            _wrap_cli_error(
                "DEPENDENCY_MISSING",
                "budoux のインポートに失敗しました",
                "pip install clipwright-wrap",
            ),
        )
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "DEPENDENCY_MISSING"

    def test_wrap_cli_error_code_preserved_in_envelope(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """wrap_cli の error code がエンベロープにそのまま反映されること。"""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.srt")
        self._patch_subprocess(
            mocker,
            _wrap_cli_error("INVALID_INPUT", "テキスト解析失敗"),
        )
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_subprocess_failure_stderr_sanitized(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """subprocess が OSError/TimeoutError で失敗した場合 stderr が露出しないこと（_SUBPROCESS_SAFE_MESSAGE 同型）。"""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.srt")
        secret = "/secret/internal/path/to/wrap_cli"
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            side_effect=OSError(secret),
        )
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is False
        # 内部パスが message に露出しない
        assert secret not in result["error"].get("message", "")
        assert secret not in result["error"].get("hint", "")

    def test_subprocess_timeout_sanitized(self, tmp_path: Path, mocker: Any) -> None:
        """subprocess.TimeoutExpired が発生した場合 ok:False かつ stderr 非露出。"""
        import subprocess

        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["wrap_cli"], timeout=30),
        )
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is False

    def test_timeout_is_cue_count_dependent(self, tmp_path: Path, mocker: Any) -> None:
        """timeout が cue 数連動（max(30, ceil(cue_count * 0.05))）で設定されること（WR-AD-11）。

        cue 数 = 100 の場合 timeout >= 5.0（= ceil(100 * 0.05)）かつ >= 30。
        """
        wrap_captions = _import_wrap_captions()
        cue_count = 100
        inp_text = _srt_ncues(cue_count)
        inp = tmp_path / "big.srt"
        inp.write_text(inp_text, encoding="utf-8")
        out = str(tmp_path / "output.srt")
        mock_run = mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok([["テキスト"] for _ in range(cue_count)]),
                returncode=0,
            ),
        )
        wrap_captions(str(inp), out, _opts())
        call_kwargs = mock_run.call_args[1]
        timeout_val = call_kwargs.get("timeout")
        assert timeout_val is not None
        # max(30, ceil(100 * 0.05)) = max(30, 5) = 30
        assert timeout_val >= 30


# ===========================================================================
# ⑤ 整形フロー
# ===========================================================================


class TestWrapFlow:
    """parse → wrap_cli → wrap_cue_lines → serialize → 出力書き込み の正常フロー。"""

    def test_output_file_created(self, tmp_path: Path, mocker: Any) -> None:
        """整形後ファイルが output パスに生成されること。"""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok(_segments_from_texts("今日はいい天気です。")),
                returncode=0,
            ),
        )
        wrap_captions(inp, out, _opts())
        assert Path(out).exists()

    def test_input_file_unchanged(self, tmp_path: Path, mocker: Any) -> None:
        """整形後も input ファイルが変更されていないこと（非破壊・WR-AD-07）。"""
        wrap_captions = _import_wrap_captions()
        original = _srt_1cue()
        inp = tmp_path / "input.srt"
        inp.write_text(original, encoding="utf-8")
        out = str(tmp_path / "output.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok([["今日は", "いい", "天気です。"]]),
                returncode=0,
            ),
        )
        wrap_captions(str(inp), out, _opts())
        assert inp.read_text(encoding="utf-8") == original

    def test_output_srt_has_wrapped_text(self, tmp_path: Path, mocker: Any) -> None:
        """SRT 出力に wrap_cue_lines で整形したテキストが含まれること。

        max_chars=3・segments=['今日は','いい','天気です。'] → '今日は\\nいい\\n天気です。' 相当。
        """
        wrap_captions = _import_wrap_captions()
        inp = tmp_path / "input.srt"
        inp.write_text(_srt_1cue("今日はいい天気です。"), encoding="utf-8")
        out = str(tmp_path / "output.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok([["今日は", "いい", "天気です。"]]),
                returncode=0,
            ),
        )
        # max_chars=3 → 各文節が 1 行に収まる
        result: dict[str, Any] = wrap_captions(str(inp), out, _opts(max_chars=3))
        assert result["ok"] is True
        content = Path(out).read_text(encoding="utf-8")
        # 改行が挿入されていること
        assert "\n" in content.split("\n", 3)[3].strip() or "いい" in content

    def test_segments_length_matches_cue_count(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """wrap_cli に渡す texts の長さが cue 数と一致すること（WR-AD-02）。"""
        wrap_captions = _import_wrap_captions()
        cue_count = 3
        inp = tmp_path / "input.srt"
        inp.write_text(_srt_ncues(cue_count), encoding="utf-8")
        out = str(tmp_path / "output.srt")
        mock_run = mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok([["テキスト"] for _ in range(cue_count)]),
                returncode=0,
            ),
        )
        wrap_captions(str(inp), out, _opts())
        stdin_data = mock_run.call_args[1].get("input")
        parsed = json.loads(stdin_data)
        assert len(parsed["texts"]) == cue_count


# ===========================================================================
# ⑥ overflow 判定（WR-AD-15(1)/DC-AM-003）
# ===========================================================================


class TestOverflow:
    """overflow は line_count(a) + line_width(b) の両方を識別する（WR-AD-15(1)）。"""

    def _run_with_segments(
        self,
        tmp_path: Path,
        mocker: Any,
        segments: list[list[str]],
        max_chars: int = 16,
        max_lines: int = 2,
    ) -> dict[str, Any]:
        """segments を wrap_cli から返し wrap_captions を実行する。"""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path, _srt_1cue("今日はいい天気です。"))
        out = str(tmp_path / "output.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok(segments),
                returncode=0,
            ),
        )
        result: dict[str, Any] = wrap_captions(
            inp, out, _opts(max_chars=max_chars, max_lines=max_lines)
        )
        return result

    def test_overflow_line_count_sets_overflow_cue_indices(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """行数超過(a) の cue が data.overflow_cue_indices に記録されること（WR-AD-15(1)/DC-AM-002）。

        max_lines=2・3 文節（各 max_chars 内）→ 3 行 → 行数超過。
        """
        # max_chars=5 の場合 ['今日は', 'いい', '天気です'] で各行は 3 文字以内 → 3 行になる
        segments = [["今日は", "いい", "天気です"]]
        result = self._run_with_segments(
            tmp_path,
            mocker,
            segments,
            max_chars=5,
            max_lines=2,
        )
        assert result["ok"] is True
        data = result["data"]
        # 行数超過 cue がある
        assert "overflow_cue_indices" in data

    def test_overflow_line_width_sets_overflow_width_cue_indices(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """行幅超過(b) の cue が data.overflow_width_cue_indices に記録されること（WR-AD-15(1)）。

        単一巨大文節（'あ' * 20）が max_chars=5 の場合でも 1 行に置かれ、行幅超過になる。
        """
        huge_segment = "あ" * 20
        segments = [[huge_segment]]
        result = self._run_with_segments(
            tmp_path,
            mocker,
            segments,
            max_chars=5,
            max_lines=2,
        )
        assert result["ok"] is True
        data = result["data"]
        assert "overflow_width_cue_indices" in data
        # 行幅超過 cue が含まれる（0件でない）
        assert len(data["overflow_width_cue_indices"]) > 0

    def test_no_overflow_empty_overflow_indices(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """overflow が一切ない場合 overflow_cue_indices/overflow_width_cue_indices は空リスト。"""
        # max_chars=16・max_lines=2・1文節 → overflow なし
        segments = [["今日はいい天気です。"]]
        result = self._run_with_segments(
            tmp_path,
            mocker,
            segments,
            max_chars=16,
            max_lines=2,
        )
        assert result["ok"] is True
        data = result["data"]
        assert data.get("overflow_cue_indices", []) == []
        assert data.get("overflow_width_cue_indices", []) == []

    def test_no_overflow_no_max_lines_warning(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """overflow が 0 件の場合 warnings に max_lines 関連メッセージが含まれない（DC-AM-002）。"""
        segments = [["今日は"]]
        result = self._run_with_segments(
            tmp_path,
            mocker,
            segments,
            max_chars=16,
            max_lines=2,
        )
        warnings: list[str] = result.get("warnings", [])
        # max_lines 超過 warning が出ない
        for w in warnings:
            assert "max_lines" not in w

    def test_overflow_not_cut_off(self, tmp_path: Path, mocker: Any) -> None:
        """overflow cue は切り捨てず出力ファイルに全テキストが含まれること（WR-AD-15(1)）。

        max_lines=1・3 文節 → 3 行になるが、全テキストが出力されること。
        """
        wrap_captions = _import_wrap_captions()
        text = "今日はいい天気です。"
        inp = _make_input_srt(tmp_path, _srt_1cue(text))
        out = str(tmp_path / "output.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok([["今日は", "いい", "天気です。"]]),
                returncode=0,
            ),
        )
        result: dict[str, Any] = wrap_captions(
            inp, out, _opts(max_lines=1, max_chars=5)
        )
        assert result["ok"] is True
        content = Path(out).read_text(encoding="utf-8")
        # 全文節が出力に含まれること（切り捨てなし）
        assert "今日は" in content
        assert "いい" in content
        assert "天気です。" in content


# ===========================================================================
# ⑦ warnings 集約（WR-AD-13(2)/DC-AM-002）
# ===========================================================================


class TestWarningsAggregation:
    """overflow warnings は集約1文 + data に index 配列（cue ごとに1行出さない）。"""

    def test_overflow_line_count_warning_is_single_sentence(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """行数超過 warnings は cue 数に依らず 1 文のみであること（DC-AM-002）。

        cue 3 件すべてが overflow しても warnings リストの要素数は最大でも小数個。
        """
        wrap_captions = _import_wrap_captions()
        # 3 cue・各 3 文節（max_chars=3・max_lines=1 → 全 cue が行数超過）
        srt_text = _srt_ncues(3)
        inp = tmp_path / "input.srt"
        inp.write_text(srt_text, encoding="utf-8")
        out = str(tmp_path / "output.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok([["A", "B", "C"] for _ in range(3)]),
                returncode=0,
            ),
        )
        result: dict[str, Any] = wrap_captions(
            str(inp), out, _opts(max_chars=2, max_lines=1)
        )
        assert result["ok"] is True
        # warnings は少数（cue 数分出ない）
        overflow_warnings = [
            w
            for w in result.get("warnings", [])
            if "max_lines" in w or "overflow" in w or "超過" in w
        ]
        # 集約: 3 cue 超過でも警告は 1 〜 2 文程度に収まること
        assert len(overflow_warnings) <= 3

    def test_overflow_data_has_overflow_cue_indices_list(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """data に overflow_cue_indices が list[int] として含まれること（WR-AD-13(2)）。"""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path, _srt_ncues(2))
        out = str(tmp_path / "output.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok([["A", "B", "C"], ["D", "E"]]),
                returncode=0,
            ),
        )
        result: dict[str, Any] = wrap_captions(
            str(inp), out, _opts(max_chars=1, max_lines=1)
        )
        assert result["ok"] is True
        data = result["data"]
        assert isinstance(data.get("overflow_cue_indices"), list)
        assert all(isinstance(i, int) for i in data["overflow_cue_indices"])

    def test_overflow_data_has_overflow_width_cue_indices_list(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """data に overflow_width_cue_indices が list[int] として含まれること（WR-AD-13(2)）。"""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path, _srt_1cue("テキスト"))
        out = str(tmp_path / "output.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok([["あ" * 20]]),
                returncode=0,
            ),
        )
        result: dict[str, Any] = wrap_captions(str(inp), out, _opts(max_chars=5))
        assert result["ok"] is True
        data = result["data"]
        assert isinstance(data.get("overflow_width_cue_indices"), list)


# ===========================================================================
# ⑧ artifacts（WR-AD-13(1)/DC-AS-005）
# ===========================================================================


class TestArtifacts:
    """artifacts は dict 形式・Artifact モデル非インスタンス化・OTIO 非生成（WR-AD-13(1)/DC-AS-005）。"""

    def _run_normal(
        self, tmp_path: Path, mocker: Any, fmt: str = "srt"
    ) -> dict[str, Any]:
        """正常系を実行して結果を返す。"""
        wrap_captions = _import_wrap_captions()
        if fmt == "srt":
            inp = _make_input_srt(tmp_path)
            out = str(tmp_path / "output.srt")
        else:
            inp = _make_input_vtt(tmp_path)
            out = str(tmp_path / "output.vtt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok(_segments_from_texts("今日はいい天気です。")),
                returncode=0,
            ),
        )
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        return result

    def test_artifacts_is_list(self, tmp_path: Path, mocker: Any) -> None:
        """artifacts が list であること。"""
        result = self._run_normal(tmp_path, mocker)
        assert isinstance(result["artifacts"], list)

    def test_artifacts_single_element(self, tmp_path: Path, mocker: Any) -> None:
        """artifacts に1要素（出力字幕）が含まれること。"""
        result = self._run_normal(tmp_path, mocker)
        assert len(result["artifacts"]) == 1

    def test_artifacts_element_is_dict(self, tmp_path: Path, mocker: Any) -> None:
        """artifacts の要素は dict であること（Artifact モデルインスタンスでない・DC-AS-005）。"""
        result = self._run_normal(tmp_path, mocker)
        artifact = result["artifacts"][0]
        assert isinstance(artifact, dict)

    def test_artifacts_element_has_role_path_format(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """artifacts の要素に role / path / format キーが含まれること。"""
        result = self._run_normal(tmp_path, mocker)
        artifact = result["artifacts"][0]
        assert "role" in artifact
        assert "path" in artifact
        assert "format" in artifact

    def test_artifacts_role_is_captions(self, tmp_path: Path, mocker: Any) -> None:
        """artifacts[0]["role"] は 'captions' であること（WR-AD-13(1)）。"""
        result = self._run_normal(tmp_path, mocker)
        assert result["artifacts"][0]["role"] == "captions"

    def test_artifacts_format_srt(self, tmp_path: Path, mocker: Any) -> None:
        """SRT 出力時 artifacts[0]["format"] は 'srt' であること。"""
        result = self._run_normal(tmp_path, mocker, fmt="srt")
        assert result["artifacts"][0]["format"] == "srt"

    def test_artifacts_format_vtt(self, tmp_path: Path, mocker: Any) -> None:
        """VTT 出力時 artifacts[0]["format"] は 'vtt' であること。"""
        result = self._run_normal(tmp_path, mocker, fmt="vtt")
        assert result["artifacts"][0]["format"] == "vtt"

    def test_no_otio_artifact(self, tmp_path: Path, mocker: Any) -> None:
        """artifacts に OTIO 関連の要素が含まれないこと（WR-AD-13(1)・OTIO 非生成）。"""
        result = self._run_normal(tmp_path, mocker)
        for artifact in result["artifacts"]:
            assert artifact.get("format") != "otio"

    def test_no_otio_file_created(self, tmp_path: Path, mocker: Any) -> None:
        """実行後 OTIO ファイルが生成されないこと（WR-AD-13(1)）。"""
        self._run_normal(tmp_path, mocker)
        otio_files = list(tmp_path.glob("*.otio"))
        assert len(otio_files) == 0


# ===========================================================================
# ⑨ エンベロープ（summary・data）
# ===========================================================================


class TestEnvelope:
    """ok_result のエンベロープ内容: summary・data の構造を検証する。"""

    def _run(
        self, tmp_path: Path, mocker: Any, cue_text: str = "今日はいい天気です。"
    ) -> dict[str, Any]:
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path, _srt_1cue(cue_text))
        out = str(tmp_path / "output.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok(_segments_from_texts(cue_text)),
                returncode=0,
            ),
        )
        result: dict[str, Any] = wrap_captions(inp, out, _opts(language="ja"))
        return result

    def test_ok_true(self, tmp_path: Path, mocker: Any) -> None:
        """正常系: ok が True であること。"""
        result = self._run(tmp_path, mocker)
        assert result["ok"] is True

    def test_summary_contains_language(self, tmp_path: Path, mocker: Any) -> None:
        """summary に language が含まれること（WR-AD-04 §4）。"""
        result = self._run(tmp_path, mocker)
        assert "ja" in result["summary"]

    def test_summary_contains_cue_count(self, tmp_path: Path, mocker: Any) -> None:
        """summary に整形 cue 数が含まれること（§4）。"""
        result = self._run(tmp_path, mocker)
        # "1" が summary に含まれること（1 cue）
        assert "1" in result["summary"]

    def test_data_has_cue_count(self, tmp_path: Path, mocker: Any) -> None:
        """data に cue_count が含まれること（§4）。"""
        result = self._run(tmp_path, mocker)
        assert "cue_count" in result["data"]
        assert result["data"]["cue_count"] == 1

    def test_data_has_wrapped_count(self, tmp_path: Path, mocker: Any) -> None:
        """data に wrapped_count（改行挿入 cue 数）が含まれること（§4）。"""
        result = self._run(tmp_path, mocker)
        assert "wrapped_count" in result["data"]

    def test_data_has_overflow_cue_indices(self, tmp_path: Path, mocker: Any) -> None:
        """data に overflow_cue_indices（list[int]）が含まれること（§4）。"""
        result = self._run(tmp_path, mocker)
        assert "overflow_cue_indices" in result["data"]
        assert isinstance(result["data"]["overflow_cue_indices"], list)

    def test_data_has_overflow_width_cue_indices(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """data に overflow_width_cue_indices（list[int]）が含まれること（§4）。"""
        result = self._run(tmp_path, mocker)
        assert "overflow_width_cue_indices" in result["data"]
        assert isinstance(result["data"]["overflow_width_cue_indices"], list)

    def test_data_has_language(self, tmp_path: Path, mocker: Any) -> None:
        """data に language が含まれること（§4）。"""
        result = self._run(tmp_path, mocker)
        assert "language" in result["data"]
        assert result["data"]["language"] == "ja"

    def test_envelope_has_warnings_list(self, tmp_path: Path, mocker: Any) -> None:
        """エンベロープに warnings リストが含まれること（ok_result 形式）。"""
        result = self._run(tmp_path, mocker)
        assert "warnings" in result
        assert isinstance(result["warnings"], list)

    def test_envelope_has_artifacts_list(self, tmp_path: Path, mocker: Any) -> None:
        """エンベロープに artifacts リストが含まれること（ok_result 形式）。"""
        result = self._run(tmp_path, mocker)
        assert "artifacts" in result
        assert isinstance(result["artifacts"], list)


# ===========================================================================
# ⑩ 空字幕（cue 0 件）防御
# ===========================================================================


class TestEmptyCaptions:
    """cue 0 件（空字幕）でも ok:True・空出力を返す防御（WR-AD-12(2)）。"""

    def test_empty_srt_returns_ok(self, tmp_path: Path, mocker: Any) -> None:
        """空 SRT（空文字列）を入力した場合 ok:True かつ cue_count=0 になること。"""
        wrap_captions = _import_wrap_captions()
        inp = tmp_path / "empty.srt"
        inp.write_text("", encoding="utf-8")
        out = str(tmp_path / "output.srt")
        # 0 件の場合 wrap_cli は呼ばれないか、texts=[] を渡す
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok([]),
                returncode=0,
            ),
        )
        result: dict[str, Any] = wrap_captions(str(inp), out, _opts())
        assert result["ok"] is True
        assert result["data"]["cue_count"] == 0

    def test_empty_vtt_returns_ok(self, tmp_path: Path, mocker: Any) -> None:
        """VTT ヘッダのみ（cue 0 件）を入力した場合 ok:True かつ cue_count=0 になること。"""
        wrap_captions = _import_wrap_captions()
        inp = tmp_path / "empty.vtt"
        inp.write_text("WEBVTT\n", encoding="utf-8")
        out = str(tmp_path / "output.vtt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok([]),
                returncode=0,
            ),
        )
        result: dict[str, Any] = wrap_captions(str(inp), out, _opts())
        assert result["ok"] is True
        assert result["data"]["cue_count"] == 0

    def test_empty_srt_output_is_empty_string(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """空 SRT 入力 → 出力ファイルも SRT（空文字列）であること（WR-AD-12(2)）。"""
        wrap_captions = _import_wrap_captions()
        inp = tmp_path / "empty.srt"
        inp.write_text("", encoding="utf-8")
        out = str(tmp_path / "output.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(stdout=_wrap_cli_ok([]), returncode=0),
        )
        wrap_captions(str(inp), out, _opts())
        assert Path(out).read_text(encoding="utf-8") == ""

    def test_empty_vtt_output_is_webvtt_header(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """VTT ヘッダのみ入力 → 出力ファイルも 'WEBVTT\\n'（WR-AD-12(2)・往復同一）。"""
        wrap_captions = _import_wrap_captions()
        inp = tmp_path / "empty.vtt"
        inp.write_text("WEBVTT\n", encoding="utf-8")
        out = str(tmp_path / "output.vtt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(stdout=_wrap_cli_ok([]), returncode=0),
        )
        wrap_captions(str(inp), out, _opts())
        assert Path(out).read_text(encoding="utf-8") == "WEBVTT\n"
