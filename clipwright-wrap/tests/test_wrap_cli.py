"""test_wrap_cli.py — Red tests for wrap_cli.py (small BudouX phrase-segmentation CLI).

Target: clipwright_wrap.wrap_cli.main(argv)
I/O contract (WR-AD-02):
  - stdin: JSON {"language": "ja", "texts": ["cue1", ...]}
  - stdout: JSON {"segments": [["segment1", ...], ...]}
  - On error, stdout: {"error": {"code", "message", "hint"}}
  - Always return 0 (errors are not communicated via exit code)
  - stdout contains JSON only (no progress/log output mixed in)

budoux is mocked via pytest-mock (real budoux is used in e2e tests).
DC-AS-002: parser is loaded exactly once, outside the texts loop.
DC-AS-003: error JSON is constructed by hand (no ClipwrightError / ffmpeg-derived except).
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any
from unittest.mock import MagicMock, call

import pytest

# Obtain the module object depending on whether wrap_cli exists.
# Before implementation this import fails, causing collection-time failure for the whole test.
# Rather than skip via pytestmark, each test imports individually at runtime to fail explicitly.


# ---------------------------------------------------------------------------
# Helper: run main() with mocked stdin/stdout and return the stdout JSON
# ---------------------------------------------------------------------------


def _run_main(
    argv: list[str] | None,
    stdin_data: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    loader_map: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], int]:
    """Run wrap_cli.main(argv) with a stdin JSON payload and return (stdout_json, return_code).

    stdout is redirected to StringIO and the JSON is parsed before returning.
    When loader_map is provided, _PARSER_LOADERS is replaced with that map.
    """
    import clipwright_wrap.wrap_cli as wrap_cli_mod  # ImportError → Red if not implemented

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
# Fixture: budoux parser mock
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_parser() -> MagicMock:
    """Mock for a budoux parser whose parse() returns [text] (single-segment dummy).

    parse(text) returns [text] for each call.
    """
    p = MagicMock()
    p.parse.side_effect = lambda text: [text]
    return p


@pytest.fixture
def mock_parser_with_segments() -> MagicMock:
    """Parser mock that returns real segments from budoux_sample.json.

    Returns segments in the order corresponding to the texts list.
    """
    import json as _json
    from pathlib import Path

    fixtures = Path(__file__).parent / "fixtures" / "budoux_sample.json"
    sample = _json.loads(fixtures.read_text(encoding="utf-8"))
    segs_map: dict[str, list[str]] = dict(
        zip(sample["texts"], sample["segments"], strict=True)
    )

    p = MagicMock()
    # CR L-4: raise KeyError instead of silent fallback for texts not in fixture,
    # so that a bug in the test side (passing an unregistered text) is caught immediately.
    p.parse.side_effect = lambda text: segs_map[text]
    return p


# ---------------------------------------------------------------------------
# Test group 1: Success path — stdout JSON and segments structure
# ---------------------------------------------------------------------------


class TestWrapCliNormal:
    """Success path: main() processes stdin JSON and returns a segments JSON to stdout."""

    def test_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """main() always returns 0."""
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
        """stdout contains JSON only (no progress/log output mixed in)."""
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
        """On success: stdout JSON contains the 'segments' key."""
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
        """The number of elements in segments matches the number of elements in texts."""
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
        """Each element of segments is list[str]."""
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
        """parser.parse() is called once for each element of texts."""
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
        """Expected segments from budoux_sample.json are returned (using conftest fixture)."""
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
        """When texts is an empty list, segments is an empty list."""
        result, rc = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": []},
            monkeypatch=monkeypatch,
            loader_map={"ja": lambda: mock_parser},
        )
        assert rc == 0
        assert result.get("segments") == []


# ---------------------------------------------------------------------------
# Test group 2: DC-AS-002 — parser is loaded exactly once, outside the texts loop
# ---------------------------------------------------------------------------


class TestParserLoadOnceDcAs002:
    """DC-AS-002: the parser load function is called exactly once regardless of the number of texts."""

    def test_parser_load_called_once_for_single_cue(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """When texts has 1 element, the parser load function is called exactly once."""
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
        """When texts has multiple elements, the parser load function is still called exactly once (cue-count-independent)."""
        texts = ["cue1", "cue2", "cue3", "cue4", "cue5"]
        loader = MagicMock(return_value=mock_parser)
        _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": texts},
            monkeypatch=monkeypatch,
            loader_map={"ja": loader},
        )
        loader.assert_called_once()  # even with 5 cues, load is called once

    def test_parser_load_called_once_for_10_cues(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """When texts has 10 elements, the parser load function is still called exactly once."""
        texts = [f"cue{i}のテキスト" for i in range(10)]
        loader = MagicMock(return_value=mock_parser)
        _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": texts},
            monkeypatch=monkeypatch,
            loader_map={"ja": loader},
        )
        loader.assert_called_once()
        # parse is called once per cue (10 times)
        assert mock_parser.parse.call_count == 10


# ---------------------------------------------------------------------------
# Test group 3: language → parser selection
# ---------------------------------------------------------------------------


class TestLanguageParserSelection:
    """The loader function corresponding to the language value is selected."""

    @pytest.mark.parametrize(
        "language",
        ["ja", "zh-hans", "zh-hant", "th"],
    )
    def test_correct_loader_called_for_language(
        self, monkeypatch: pytest.MonkeyPatch, language: str
    ) -> None:
        """The loader for the specified language is called; loaders for other languages are not called."""
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
        # The loader for the specified language was called
        mock_parsers[language].assert_called_once()
        # Loaders for other languages were not called
        for lang, loader in mock_parsers.items():
            if lang != language:
                loader.assert_not_called()

    def test_parser_loaders_dict_has_ja(self) -> None:
        """_PARSER_LOADERS["ja"] key exists and is callable."""
        import clipwright_wrap.wrap_cli as wrap_cli_mod

        assert "ja" in wrap_cli_mod._PARSER_LOADERS
        assert callable(wrap_cli_mod._PARSER_LOADERS["ja"])

    def test_parser_loaders_dict_has_zh_hans(self) -> None:
        """_PARSER_LOADERS["zh-hans"] exists and is callable."""
        import clipwright_wrap.wrap_cli as wrap_cli_mod

        assert "zh-hans" in wrap_cli_mod._PARSER_LOADERS
        assert callable(wrap_cli_mod._PARSER_LOADERS["zh-hans"])

    def test_parser_loaders_dict_has_zh_hant(self) -> None:
        """_PARSER_LOADERS["zh-hant"] exists and is callable."""
        import clipwright_wrap.wrap_cli as wrap_cli_mod

        assert "zh-hant" in wrap_cli_mod._PARSER_LOADERS
        assert callable(wrap_cli_mod._PARSER_LOADERS["zh-hant"])

    def test_parser_loaders_dict_has_th(self) -> None:
        """_PARSER_LOADERS["th"] exists and is callable."""
        import clipwright_wrap.wrap_cli as wrap_cli_mod

        assert "th" in wrap_cli_mod._PARSER_LOADERS
        assert callable(wrap_cli_mod._PARSER_LOADERS["th"])


# ---------------------------------------------------------------------------
# Test group 4: Error paths — DC-AS-003 / WR-AD-09
# ---------------------------------------------------------------------------


class TestWrapCliErrors:
    """Error paths: error JSON is hand-constructed, stdout only, return 0."""

    # --- DEPENDENCY_MISSING: budoux ImportError ---

    def test_dependency_missing_on_budoux_import_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """budoux ImportError → stdout {"error": {"code": "DEPENDENCY_MISSING", ...}} return 0."""
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
        """DEPENDENCY_MISSING hint must not contain internal path information from str(exc) (fixed hint)."""
        exc_msg = "internal/secret/path/budoux/__init__.py not found"
        result, _ = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": ["テキスト"]},
            monkeypatch=monkeypatch,
            loader_map={"ja": MagicMock(side_effect=ImportError(exc_msg))},
        )
        # str(exc) content must not be exposed in hint/message
        assert exc_msg not in result["error"].get("hint", "")
        assert exc_msg not in result["error"].get("message", "")

    def test_dependency_missing_no_segments_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On DEPENDENCY_MISSING, the 'segments' key must not be present."""
        result, _ = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": ["テキスト"]},
            monkeypatch=monkeypatch,
            loader_map={"ja": MagicMock(side_effect=ImportError("no budoux"))},
        )
        assert "segments" not in result

    # --- INVALID_INPUT: malformed stdin JSON ---

    def test_invalid_input_on_malformed_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Malformed stdin JSON → stdout {"error": {"code": "INVALID_INPUT", ...}} return 0."""
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
        """stdin JSON missing 'language' key → INVALID_INPUT."""
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
        """stdin JSON missing 'texts' key → INVALID_INPUT."""
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
        """texts is not a list (e.g. str) → INVALID_INPUT."""
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
        """Unsupported language → INVALID_INPUT (key not in _PARSER_LOADERS)."""
        result, rc = _run_main(
            argv=None,
            stdin_data={"language": "ko", "texts": ["テキスト"]},
            monkeypatch=monkeypatch,
            loader_map={"ja": lambda: mock_parser},
        )
        assert rc == 0
        assert result["error"]["code"] == "INVALID_INPUT"

    # --- SR M-2: invalid language error must not expose input value or internal dict keys ---

    def test_invalid_language_message_does_not_contain_input_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SR M-2: error message for invalid language must not contain the input language value.

        The current implementation exposes the input value via f"unsupported language: {language!r}".
        This requires changing to the fixed string "Unsupported language specified".
        """
        malicious_lang = "xx'; DROP TABLE users; --"
        result, rc = _run_main(
            argv=None,
            stdin_data={"language": malicious_lang, "texts": ["テキスト"]},
            monkeypatch=monkeypatch,
            loader_map={"ja": lambda: MagicMock()},
        )
        assert rc == 0
        assert result["error"]["code"] == "INVALID_INPUT"
        # Input value must not appear in message (fixed string only)
        assert malicious_lang not in result["error"]["message"]
        assert repr(malicious_lang) not in result["error"]["message"]
        # Fixed string must be present
        assert "Unsupported language specified" in result["error"]["message"]

    def test_invalid_language_hint_does_not_expose_parser_loaders_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SR M-2: hint for invalid language must not expose _PARSER_LOADERS.keys() expansion.

        The current implementation exposes the internal dict dynamically via
        f"language is one of {list(_PARSER_LOADERS.keys())} ...".
        This requires changing to the fixed string "ja / zh-hans / zh-hant / th".
        """
        result, rc = _run_main(
            argv=None,
            stdin_data={"language": "ko", "texts": ["テキスト"]},
            monkeypatch=monkeypatch,
            loader_map={"ja": lambda: MagicMock(), "zh-hans": lambda: MagicMock()},
        )
        assert rc == 0
        assert result["error"]["code"] == "INVALID_INPUT"
        hint = result["error"]["hint"]
        # dict key expansion format (["ja", "zh-hans", ...]) must not appear
        assert "['" not in hint
        assert "']" not in hint
        # Fixed enumeration must be present
        assert "ja" in hint
        assert "zh-hans" in hint
        assert "zh-hant" in hint
        assert "th" in hint

    # --- SR L-3: type check when texts elements are not str ---

    def test_invalid_input_on_texts_with_non_str_elements(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """SR L-3: when texts contains non-str elements, INVALID_INPUT is returned.

        The current implementation has no type check, causing AttributeError from
        parser.parse(None) → INTERNAL error. Adding a type check for texts elements is required.
        """
        result, rc = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": [None, 1, []]},
            monkeypatch=monkeypatch,
            loader_map={"ja": lambda: mock_parser},
        )
        assert rc == 0
        # Must not fall through to INTERNAL via AttributeError/TypeError
        assert result["error"]["code"] == "INVALID_INPUT"
        assert "texts" in result["error"]["message"]

    def test_invalid_input_on_texts_with_mixed_str_and_non_str(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """SR L-3: INVALID_INPUT is returned even when texts is a mixed list of str and non-str."""
        result, rc = _run_main(
            argv=None,
            stdin_data={
                "language": "ja",
                "texts": ["有効なテキスト", 42, "別テキスト"],
            },
            monkeypatch=monkeypatch,
            loader_map={"ja": lambda: mock_parser},
        )
        assert rc == 0
        assert result["error"]["code"] == "INVALID_INPUT"

    # --- CR L-2: DEPENDENCY_MISSING when _PARSER_LOADERS is empty ---

    def test_dependency_missing_when_parser_loaders_is_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CR L-2: when budoux is not installed and _PARSER_LOADERS={}, DEPENDENCY_MISSING is returned.

        The current implementation returns INVALID_INPUT via "language not in _PARSER_LOADERS".
        When _PARSER_LOADERS is empty at the start of main(), DEPENDENCY_MISSING should be returned.
        """
        result, rc = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": ["テキスト"]},
            monkeypatch=monkeypatch,
            loader_map={},  # simulate budoux not installed
        )
        # also confirmed via monkeypatch.setattr (_run_main's loader_map takes precedence)
        assert rc == 0
        # Must be DEPENDENCY_MISSING, not INVALID_INPUT
        assert result["error"]["code"] == "DEPENDENCY_MISSING"
        # install hint must be present
        assert "hint" in result["error"]
        assert (
            "install" in result["error"]["hint"].lower()
            or "pip" in result["error"]["hint"].lower()
            or "clipwright-wrap" in result["error"]["hint"]
        )

    def test_dependency_missing_when_parser_loaders_is_empty_no_invalid_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CR L-2: when _PARSER_LOADERS={}, must not fall through to INVALID_INPUT."""
        result, rc = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": ["テキスト"]},
            monkeypatch=monkeypatch,
            loader_map={},
        )
        assert rc == 0
        assert result["error"]["code"] != "INVALID_INPUT"

    # --- INTERNAL: unexpected exception ---

    def test_internal_error_on_unexpected_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When parser.parse() raises RuntimeError → stdout {"error": {"code": "INTERNAL"}} return 0."""
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
        """On INTERNAL error: stdout must not contain a traceback (stderr only)."""
        broken_parser = MagicMock()
        broken_parser.parse.side_effect = RuntimeError("crash detail")
        result, _ = _run_main(
            argv=None,
            stdin_data={"language": "ja", "texts": ["テキスト"]},
            monkeypatch=monkeypatch,
            loader_map={"ja": MagicMock(return_value=broken_parser)},
        )
        # stdout is JSON only (no traceback mixed in)
        assert "Traceback" not in str(result)
        assert "RuntimeError" not in result["error"].get("message", "")
        assert "RuntimeError" not in result["error"].get("hint", "")

    def test_internal_error_hint_is_fixed_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """INTERNAL hint must be a fixed string (str(exc) not exposed)."""
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

    # --- Common format verification for all error cases ---

    def test_error_json_has_code_message_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On error: the error object has all three keys — code / message / hint."""
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
        """Confirm that wrap_cli contains no ClipwrightError except clause.

        wrap_cli does not call ffmpeg, so code that catches ClipwrightError
        must not be present (DC-AS-003 compliance check).
        """
        import inspect

        import clipwright_wrap.wrap_cli as wrap_cli_mod

        source = inspect.getsource(wrap_cli_mod)
        # No line that excepts ClipwrightError must exist
        assert "except ClipwrightError" not in source

    def test_no_ffmpeg_references_in_wrap_cli(self) -> None:
        """wrap_cli must contain no ffmpeg-related references (DC-AS-003)."""
        import inspect

        import clipwright_wrap.wrap_cli as wrap_cli_mod

        source = inspect.getsource(wrap_cli_mod)
        assert "ffmpeg" not in source.lower()
        assert "resolve_tool" not in source
        assert "from clipwright.process" not in source


# ---------------------------------------------------------------------------
# Test group 5: stdout contains JSON only (no progress/log output mixed in)
# ---------------------------------------------------------------------------


class TestStdoutJsonOnly:
    """stdout contains exactly one JSON object. No multiple lines or extra strings."""

    def test_stdout_single_json_object(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """stdout is a single object parseable as JSON."""
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
        # Confirm no extra newlines or log lines mixed in
        # JSON is exactly 1 line
        lines = [ln for ln in raw_output.splitlines() if ln.strip()]
        assert len(lines) == 1
        parsed = json.loads(raw_output)
        assert isinstance(parsed, dict)

    def test_no_extra_output_before_json(
        self, monkeypatch: pytest.MonkeyPatch, mock_parser: MagicMock
    ) -> None:
        """The first character of stdout is '{' (no extra string before the JSON)."""
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
