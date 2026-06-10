"""test_wrap_cli.py — wrap_cli.py（budoux 文節分割小 CLI）の Red テスト。

テスト対象: clipwright_wrap.wrap_cli.main(argv)
I/O 契約 (WR-AD-02):
  - stdin: JSON {"language": "ja", "texts": ["cue1", ...]}
  - stdout: JSON {"segments": [["文節1", ...], ...]}
  - エラー時 stdout: {"error": {"code", "message", "hint"}}
  - 常に return 0（exit code でエラーを伝えない）
  - stdout は JSON のみ（進捗・ログ混入なし）

budoux は pytest-mock でモック（実 budoux は e2e で使用）。
DC-AS-002: parser ロードは texts ループ外で 1 回のみ。
DC-AS-003: error JSON は手書き構築（ClipwrightError / ffmpeg 由来 except なし）。
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any
from unittest.mock import MagicMock, call

import pytest

# wrap_cli が存在するかどうかでモジュールオブジェクトを取得
# 実装前はこの import が失敗し、テスト全体が収集時に失敗する。
# 収集は成功させつつ、実行時に失敗させるため、pytestmark で skip せず
# 各テストで個別に import して失敗させる方針とする。


# ---------------------------------------------------------------------------
# ヘルパー: main() を stdin/stdout モックで実行して stdout JSON を取得する
# ---------------------------------------------------------------------------


def _run_main(
    argv: list[str] | None,
    stdin_data: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    loader_map: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], int]:
    """wrap_cli.main(argv) を stdin JSON 付きで実行し、(stdout_json, return_code) を返す。

    stdout は StringIO にリダイレクトし JSON をパースして返す。
    loader_map が指定された場合は _PARSER_LOADERS をそのマップで差し替える。
    """
    import clipwright_wrap.wrap_cli as wrap_cli_mod  # 実装なければ ImportError で Red

    stdin_payload = json.dumps(stdin_data, ensure_ascii=False)
    fake_stdin = io.StringIO(stdin_payload)
    fake_stdout = io.StringIO()

    monkeypatch.setattr(sys, "stdin", fake_stdin)
    monkeypatch.setattr(sys, "stdout", fake_stdout)

    if loader_map is not None:
        monkeypatch.setattr(wrap_cli_mod, "_PARSER_LOADERS", loader_map)

    rc = wrap_cli_mod.main(argv if argv is not None else [])
    output = fake_stdout.getvalue()

    parsed: dict[str, Any] = json.loads(output)
    return parsed, rc


# ---------------------------------------------------------------------------
# フィクスチャ: budoux parser モック
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_parser() -> MagicMock:
    """budoux parser の parse() を返すモック。

    parse(text) は呼び出しごとに [text] を返す（1文節として返すダミー）。
    """
    p = MagicMock()
    p.parse.side_effect = lambda text: [text]
    return p


@pytest.fixture
def mock_parser_with_segments() -> MagicMock:
    """budoux_sample.json の実セグメントを返す parser モック。

    tests = ["今日はいい天気です。", ...] に対応した順番で segments を返す。
    """
    import json as _json
    from pathlib import Path

    fixtures = Path(__file__).parent / "fixtures" / "budoux_sample.json"
    sample = _json.loads(fixtures.read_text(encoding="utf-8"))
    segs_map: dict[str, list[str]] = dict(zip(sample["texts"], sample["segments"], strict=True))

    p = MagicMock()
    p.parse.side_effect = lambda text: segs_map.get(text, [text])
    return p


# ---------------------------------------------------------------------------
# テスト群 1: 正常系 — stdout JSON・segments 構造
# ---------------------------------------------------------------------------


class TestWrapCliNormal:
    """正常系: main() が stdin JSON を処理して stdout に segments JSON を返す。"""

    def test_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """main() は常に 0 を返す。"""
        _, rc = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": ["今日はいい天気です。"]},
            monkeypatch=monkeypatch,
            loader_map={"ja": lambda: mock_parser},
        )
        assert rc == 0

    def test_stdout_is_valid_json(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """stdout は JSON のみ（進捗・ログ混入なし）。"""
        result, _ = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": ["今日はいい天気です。"]},
            monkeypatch=monkeypatch,
            loader_map={"ja": lambda: mock_parser},
        )
        assert isinstance(result, dict)

    def test_segments_key_present(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """正常時: stdout JSON に 'segments' キーがある。"""
        result, _ = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": ["今日はいい天気です。"]},
            monkeypatch=monkeypatch,
            loader_map={"ja": lambda: mock_parser},
        )
        assert "segments" in result

    def test_segments_length_matches_texts(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """segments の要素数は texts の要素数と一致する。"""
        texts = ["cue1", "cue2", "cue3"]
        result, _ = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": texts},
            monkeypatch=monkeypatch,
            loader_map={"ja": lambda: mock_parser},
        )
        assert len(result["segments"]) == len(texts)

    def test_segments_each_is_list_of_str(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """segments の各要素は list[str] である。"""
        result, _ = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": ["今日はいい天気です。"]},
            monkeypatch=monkeypatch,
            loader_map={"ja": lambda: mock_parser},
        )
        for seg in result["segments"]:
            assert isinstance(seg, list)
            for token in seg:
                assert isinstance(token, str)

    def test_parse_called_for_each_text(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """parser.parse() が texts の各要素に対して 1 回ずつ呼ばれる。"""
        texts = ["cue1のテキスト", "cue2のテキスト", "cue3のテキスト"]
        _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": texts},
            monkeypatch=monkeypatch,
            loader_map={"ja": lambda: mock_parser},
        )
        assert mock_parser.parse.call_count == len(texts)
        mock_parser.parse.assert_has_calls([call(t) for t in texts], any_order=False)

    def test_segments_match_budoux_sample(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_parser_with_segments: MagicMock,
        budoux_segments_ja: list[list[str]],
    ) -> None:
        """budoux_sample.json の期待セグメントが返される（conftest fixture 活用）。"""
        import json as _json
        from pathlib import Path

        fixtures = Path(__file__).parent / "fixtures" / "budoux_sample.json"
        sample = _json.loads(fixtures.read_text(encoding="utf-8"))
        texts = sample["texts"]
        expected_segments = sample["segments"]

        result, _ = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": texts},
            monkeypatch=monkeypatch,
            loader_map={"ja": lambda: mock_parser_with_segments},
        )
        assert result["segments"] == expected_segments

    def test_empty_texts_returns_empty_segments(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """texts が空リストのとき segments は空リスト。"""
        result, rc = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": []},
            monkeypatch=monkeypatch,
            loader_map={"ja": lambda: mock_parser},
        )
        assert rc == 0
        assert result.get("segments") == []


# ---------------------------------------------------------------------------
# テスト群 2: DC-AS-002 — parser ロードは texts ループ外で 1 回のみ
# ---------------------------------------------------------------------------


class TestParserLoadOnceDcAs002:
    """DC-AS-002: parser ロード関数が texts 数に依らず 1 回だけ呼ばれる。"""

    def test_parser_load_called_once_for_single_cue(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """texts が 1 件のとき parser ロード関数は 1 回だけ呼ばれる。"""
        loader = MagicMock(return_value=mock_parser)
        _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": ["一件だけ"]},
            monkeypatch=monkeypatch,
            loader_map={"ja": loader},
        )
        loader.assert_called_once()

    def test_parser_load_called_once_for_multiple_cues(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """texts が複数件のとき parser ロード関数は 1 回だけ呼ばれる（cue 数非依存）。"""
        texts = ["cue1", "cue2", "cue3", "cue4", "cue5"]
        loader = MagicMock(return_value=mock_parser)
        _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": texts},
            monkeypatch=monkeypatch,
            loader_map={"ja": loader},
        )
        loader.assert_called_once()  # cue 数 5 でもロードは 1 回

    def test_parser_load_called_once_for_10_cues(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """texts が 10 件のとき parser ロード関数は依然として 1 回だけ。"""
        texts = [f"cue{i}のテキスト" for i in range(10)]
        loader = MagicMock(return_value=mock_parser)
        _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": texts},
            monkeypatch=monkeypatch,
            loader_map={"ja": loader},
        )
        loader.assert_called_once()
        # parse は 10 回呼ばれる（cue ごと）
        assert mock_parser.parse.call_count == 10


# ---------------------------------------------------------------------------
# テスト群 3: language → parser 選択
# ---------------------------------------------------------------------------


class TestLanguageParserSelection:
    """language 値によって対応する parser ロード関数が選ばれる。"""

    @pytest.mark.parametrize(
        "language",
        ["ja", "zh-hans", "zh-hant", "th"],
    )
    def test_correct_loader_called_for_language(
        self, monkeypatch: pytest.MonkeyPatch, language: str
    ) -> None:
        """language に対応するロード関数が呼ばれる（他のロード関数は呼ばれない）。"""
        mock_parsers: dict[str, MagicMock] = {
            lang: MagicMock(
                return_value=MagicMock(parse=MagicMock(return_value=["トークン"]))
            )
            for lang in ["ja", "zh-hans", "zh-hant", "th"]
        }
        _run_main(
            argv=None,
            stdin_data={"language": language, "texts": ["テキスト"]},
            monkeypatch=monkeypatch,
            loader_map=mock_parsers,
        )
        # 指定言語のローダーが呼ばれた
        mock_parsers[language].assert_called_once()
        # 他の言語のローダーは呼ばれない
        for lang, loader in mock_parsers.items():
            if lang != language:
                loader.assert_not_called()

    def test_parser_loaders_dict_has_ja(self) -> None:
        """_PARSER_LOADERS["ja"] のキーが存在し、呼び出し可能オブジェクトである。"""
        import clipwright_wrap.wrap_cli as wrap_cli_mod

        assert "ja" in wrap_cli_mod._PARSER_LOADERS
        assert callable(wrap_cli_mod._PARSER_LOADERS["ja"])

    def test_parser_loaders_dict_has_zh_hans(self) -> None:
        """_PARSER_LOADERS["zh-hans"] が存在し呼び出し可能。"""
        import clipwright_wrap.wrap_cli as wrap_cli_mod

        assert "zh-hans" in wrap_cli_mod._PARSER_LOADERS
        assert callable(wrap_cli_mod._PARSER_LOADERS["zh-hans"])

    def test_parser_loaders_dict_has_zh_hant(self) -> None:
        """_PARSER_LOADERS["zh-hant"] が存在し呼び出し可能。"""
        import clipwright_wrap.wrap_cli as wrap_cli_mod

        assert "zh-hant" in wrap_cli_mod._PARSER_LOADERS
        assert callable(wrap_cli_mod._PARSER_LOADERS["zh-hant"])

    def test_parser_loaders_dict_has_th(self) -> None:
        """_PARSER_LOADERS["th"] が存在し呼び出し可能。"""
        import clipwright_wrap.wrap_cli as wrap_cli_mod

        assert "th" in wrap_cli_mod._PARSER_LOADERS
        assert callable(wrap_cli_mod._PARSER_LOADERS["th"])


# ---------------------------------------------------------------------------
# テスト群 4: エラー系 — DC-AS-003 / WR-AD-09
# ---------------------------------------------------------------------------


class TestWrapCliErrors:
    """エラー系: error JSON は手書き構築・stdout のみ・return 0。"""

    # --- DEPENDENCY_MISSING: budoux ImportError ---

    def test_dependency_missing_on_budoux_import_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """budoux ImportError → stdout {"error": {"code": "DEPENDENCY_MISSING", ...}} return 0。"""
        result, rc = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": ["テキスト"]},
            monkeypatch=monkeypatch,
            loader_map={"ja": MagicMock(side_effect=ImportError("budoux not found"))},
        )
        assert rc == 0
        assert "error" in result
        assert result["error"]["code"] == "DEPENDENCY_MISSING"
        assert "message" in result["error"]
        assert "hint" in result["error"]

    def test_dependency_missing_hint_no_str_exc(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DEPENDENCY_MISSING の hint に str(exc) の内部パス情報が含まれない（固定 hint）。"""
        exc_msg = "internal/secret/path/budoux/__init__.py not found"
        result, _ = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": ["テキスト"]},
            monkeypatch=monkeypatch,
            loader_map={"ja": MagicMock(side_effect=ImportError(exc_msg))},
        )
        # str(exc) の内容が hint/message に露出しないことを確認
        assert exc_msg not in result["error"].get("hint", "")
        assert exc_msg not in result["error"].get("message", "")

    def test_dependency_missing_no_segments_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DEPENDENCY_MISSING 時は 'segments' キーが存在しない。"""
        result, _ = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": ["テキスト"]},
            monkeypatch=monkeypatch,
            loader_map={"ja": MagicMock(side_effect=ImportError("no budoux"))},
        )
        assert "segments" not in result

    # --- INVALID_INPUT: 不正 stdin JSON ---

    def test_invalid_input_on_malformed_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """不正な stdin JSON → stdout {"error": {"code": "INVALID_INPUT", ...}} return 0。"""
        import clipwright_wrap.wrap_cli as wrap_cli_mod

        fake_stdin = io.StringIO("{not valid json}")
        fake_stdout = io.StringIO()
        monkeypatch.setattr(sys, "stdin", fake_stdin)
        monkeypatch.setattr(sys, "stdout", fake_stdout)

        rc = wrap_cli_mod.main([])
        result: dict[str, Any] = json.loads(fake_stdout.getvalue())

        assert rc == 0
        assert "error" in result
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_invalid_input_on_missing_language_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """stdin JSON に 'language' キーがない → INVALID_INPUT。"""
        import clipwright_wrap.wrap_cli as wrap_cli_mod

        fake_stdin = io.StringIO(json.dumps({"texts": ["テキスト"]}))
        fake_stdout = io.StringIO()
        monkeypatch.setattr(sys, "stdin", fake_stdin)
        monkeypatch.setattr(sys, "stdout", fake_stdout)

        rc = wrap_cli_mod.main([])
        result = json.loads(fake_stdout.getvalue())

        assert rc == 0
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_invalid_input_on_missing_texts_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """stdin JSON に 'texts' キーがない → INVALID_INPUT。"""
        import clipwright_wrap.wrap_cli as wrap_cli_mod

        fake_stdin = io.StringIO(json.dumps({"language": "ja"}))
        fake_stdout = io.StringIO()
        monkeypatch.setattr(sys, "stdin", fake_stdin)
        monkeypatch.setattr(sys, "stdout", fake_stdout)

        rc = wrap_cli_mod.main([])
        result = json.loads(fake_stdout.getvalue())

        assert rc == 0
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_invalid_input_on_texts_not_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """texts が list でない（str 等） → INVALID_INPUT。"""
        import clipwright_wrap.wrap_cli as wrap_cli_mod

        fake_stdin = io.StringIO(json.dumps({"language": "ja", "texts": "文字列"}))
        fake_stdout = io.StringIO()
        monkeypatch.setattr(sys, "stdin", fake_stdin)
        monkeypatch.setattr(sys, "stdout", fake_stdout)

        rc = wrap_cli_mod.main([])
        result = json.loads(fake_stdout.getvalue())

        assert rc == 0
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_invalid_input_on_unknown_language(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """対応外 language → INVALID_INPUT（_PARSER_LOADERS にキーなし）。"""
        result, rc = _run_main(
            argv=None,
            stdin_data={"language": "ko", "texts": ["テキスト"]},
            monkeypatch=monkeypatch,
            loader_map={"ja": lambda: mock_parser},
        )
        assert rc == 0
        assert result["error"]["code"] == "INVALID_INPUT"

    # --- INTERNAL: 想定外例外 ---

    def test_internal_error_on_unexpected_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """parser.parse() が RuntimeError を投げた場合 → stdout {"error": {"code": "INTERNAL"}} return 0。"""
        broken_parser = MagicMock()
        broken_parser.parse.side_effect = RuntimeError("unexpected crash")
        result, rc = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": ["テキスト"]},
            monkeypatch=monkeypatch,
            loader_map={"ja": MagicMock(return_value=broken_parser)},
        )
        assert rc == 0
        assert "error" in result
        assert result["error"]["code"] == "INTERNAL"

    def test_internal_error_no_traceback_in_stdout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """INTERNAL エラー時: stdout に traceback が混入しない（stderr 限定）。"""
        broken_parser = MagicMock()
        broken_parser.parse.side_effect = RuntimeError("crash detail")
        result, _ = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": ["テキスト"]},
            monkeypatch=monkeypatch,
            loader_map={"ja": MagicMock(return_value=broken_parser)},
        )
        # stdout は JSON のみ（traceback が混入していない）
        assert "Traceback" not in str(result)
        assert "RuntimeError" not in result["error"].get("message", "")
        assert "RuntimeError" not in result["error"].get("hint", "")

    def test_internal_error_hint_is_fixed_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """INTERNAL の hint は固定文言（str(exc) 非露出）。"""
        broken_parser = MagicMock()
        exc_detail = "internal/secret/path/detail"
        broken_parser.parse.side_effect = RuntimeError(exc_detail)
        result, _ = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": ["テキスト"]},
            monkeypatch=monkeypatch,
            loader_map={"ja": MagicMock(return_value=broken_parser)},
        )
        assert exc_detail not in result["error"].get("hint", "")
        assert exc_detail not in result["error"].get("message", "")

    # --- エラー時のフォーマット共通検証 ---

    def test_error_json_has_code_message_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """エラー時: error オブジェクトが code / message / hint の3キーを持つ。"""
        import clipwright_wrap.wrap_cli as wrap_cli_mod

        fake_stdin = io.StringIO("{invalid}")
        fake_stdout = io.StringIO()
        monkeypatch.setattr(sys, "stdin", fake_stdin)
        monkeypatch.setattr(sys, "stdout", fake_stdout)

        wrap_cli_mod.main([])
        result = json.loads(fake_stdout.getvalue())

        error = result["error"]
        assert "code" in error
        assert "message" in error
        assert "hint" in error

    def test_no_clipwright_error_except_path(self) -> None:
        """wrap_cli には ClipwrightError の except 節が存在しない前提を確認。

        wrap_cli は ffmpeg を呼ばないため ClipwrightError を捕捉するコードが
        混入していてはならない（DC-AS-003 遵守確認）。
        """
        import inspect

        import clipwright_wrap.wrap_cli as wrap_cli_mod

        source = inspect.getsource(wrap_cli_mod)
        # ClipwrightError を except する行が存在しないことを検証
        assert "except ClipwrightError" not in source

    def test_no_ffmpeg_references_in_wrap_cli(self) -> None:
        """wrap_cli に ffmpeg 関連の参照が存在しない（DC-AS-003）。"""
        import inspect

        import clipwright_wrap.wrap_cli as wrap_cli_mod

        source = inspect.getsource(wrap_cli_mod)
        assert "ffmpeg" not in source.lower()
        assert "resolve_tool" not in source
        assert "from clipwright.process" not in source


# ---------------------------------------------------------------------------
# テスト群 5: stdout は JSON のみ（進捗・ログ混入なし）
# ---------------------------------------------------------------------------


class TestStdoutJsonOnly:
    """stdout は JSON 1 オブジェクトのみ。複数行・余計な文字列が含まれない。"""

    def test_stdout_single_json_object(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """stdout が JSON としてパース可能な単一オブジェクトである。"""
        import clipwright_wrap.wrap_cli as wrap_cli_mod

        fake_stdin = io.StringIO(json.dumps({"language": "ja", "texts": ["テキスト"]}))
        fake_stdout = io.StringIO()
        monkeypatch.setattr(sys, "stdin", fake_stdin)
        monkeypatch.setattr(sys, "stdout", fake_stdout)
        monkeypatch.setattr(
            wrap_cli_mod, "_PARSER_LOADERS", {"ja": lambda: mock_parser}
        )

        wrap_cli_mod.main([])

        raw_output = fake_stdout.getvalue().strip()
        # 余計な改行・ログ行が混入していないことを確認
        # JSON は1行のみ
        lines = [ln for ln in raw_output.splitlines() if ln.strip()]
        assert len(lines) == 1
        parsed = json.loads(raw_output)
        assert isinstance(parsed, dict)

    def test_no_extra_output_before_json(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """stdout の最初の文字が '{' である（JSON 前に余計な文字列がない）。"""
        import clipwright_wrap.wrap_cli as wrap_cli_mod

        fake_stdin = io.StringIO(json.dumps({"language": "ja", "texts": ["テキスト"]}))
        fake_stdout = io.StringIO()
        monkeypatch.setattr(sys, "stdin", fake_stdin)
        monkeypatch.setattr(sys, "stdout", fake_stdout)
        monkeypatch.setattr(
            wrap_cli_mod, "_PARSER_LOADERS", {"ja": lambda: mock_parser}
        )

        wrap_cli_mod.main([])

        raw_output = fake_stdout.getvalue()
        assert raw_output.strip().startswith("{")
