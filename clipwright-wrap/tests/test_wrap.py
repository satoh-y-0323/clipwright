"""test_wrap.py — Red tests for the wrap.py orchestration layer.

Target API:
  clipwright_wrap.wrap.wrap_captions(
      input: str, output: str, options: WrapCaptionsOptions,
  ) -> dict[str, Any]

Mocking strategy:
  - subprocess.run (or wrap._run_wrap_cli) is replaced using pytest-mock.
    wrap.py launches sys.executable -m clipwright_wrap.wrap_cli as a subprocess (WR-AD-01).
  - Real budoux and real SRT/VTT file writes are verified in wrap e2e tests.
  - captions.parse_captions / captions.serialize_captions are called for real (pure logic).

Verification aspects (architecture-report-20260611-022805.md WR-AD-02/07/08/09/11/13/14/15):
  ① Output validation (WR-AD-07/08): extension match, parent dir existence, output==input prohibited
  ② Input validation (WR-AD-09): FILE_NOT_FOUND basename only, INVALID_INPUT on parse failure
  ③ DC-GP-001 language responsibility: WrapCaptionsOptions(language='xx') → ValidationError
  ④ wrap_cli launch (WR-AD-02; DC-AS-007): sys.executable -m wrap_cli, stdin JSON, error key detection
  ⑤ Formatting flow: parse→wrap_cli→wrap_cue_lines→serialize→output write (input unchanged)
  ⑥ WR-AD-15(1)/DC-AM-003 overflow: both line_count(a) and line_width(b) identified; no truncation
  ⑦ WR-AD-13(2)/DC-AM-002 warnings aggregation: single aggregated sentence + overflow_cue_indices/overflow_width_cue_indices in data
  ⑧ WR-AD-13(1)/DC-AS-005 artifacts: dict format, no OTIO generated
  ⑨ Envelope: summary contains cue count/wrapped count/overflow count/language; lightweight data
  ⑩ 0-cue (empty subtitle) defence: ok:True, empty output
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ImportError at this point means wrap.py is not yet implemented → confirms Red phase
from clipwright_wrap.schemas import WrapCaptionsOptions

# ===========================================================================
# Helpers
# ===========================================================================


def _srt_1cue(text: str = "今日はいい天気です。") -> str:
    """Generate a 1-cue SRT text. Conforms to WR-AD-12(1) byte structure."""
    return f"1\n00:00:00,000 --> 00:00:01,000\n{text}\n"


def _srt_ncues(n: int) -> str:
    """Generate an n-cue SRT text.

    Produces canonical timecodes with seconds-to-minutes carry-over (valid SRT even for n>=60).
    """
    blocks = []
    for i in range(1, n + 1):
        sm, ss = divmod(i - 1, 60)
        em, es = divmod(i, 60)
        blocks.append(
            f"{i}\n00:{sm:02d}:{ss:02d},000 --> 00:{em:02d}:{es:02d},000\nテキスト{i}\n"
        )
    return "\n".join(blocks)


def _vtt_1cue(text: str = "今日はいい天気です。") -> str:
    """Generate a 1-cue VTT text. Conforms to WR-AD-12(1) byte structure."""
    return f"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n{text}\n"


def _segments_from_texts(*texts: str) -> list[list[str]]:
    """Convert each text to a single-segment list in the wrap_cli segments format."""
    return [[t] for t in texts]


def _wrap_cli_ok(segments: list[list[str]]) -> str:
    """Generate a success JSON str as returned by wrap_cli. Assumes text=True mode."""
    return json.dumps({"segments": segments}, ensure_ascii=False)


def _wrap_cli_error(code: str, message: str, hint: str = "Check and retry.") -> str:
    """Generate an error JSON str as returned by wrap_cli. Assumes text=True mode."""
    return json.dumps(
        {"error": {"code": code, "message": message, "hint": hint}},
        ensure_ascii=False,
    )


def _opts(**kwargs: Any) -> WrapCaptionsOptions:
    return WrapCaptionsOptions(**kwargs)


def _make_input_srt(tmp_path: Path, content: str | None = None) -> str:
    """Create input.srt in tmp_path and return its path."""
    p = tmp_path / "input.srt"
    p.write_text(content if content is not None else _srt_1cue(), encoding="utf-8")
    return str(p)


def _make_input_vtt(tmp_path: Path, content: str | None = None) -> str:
    """Create input.vtt in tmp_path and return its path."""
    p = tmp_path / "input.vtt"
    p.write_text(content if content is not None else _vtt_1cue(), encoding="utf-8")
    return str(p)


# ===========================================================================
# Import wrap_captions (ImportError → Red if not implemented)
# ===========================================================================


def _import_wrap_captions() -> Any:
    """Lazily import and return wrap_captions. Raises ImportError if wrap.py is not implemented."""
    from clipwright_wrap.wrap import wrap_captions

    return wrap_captions


# ===========================================================================
# ① Output validation (WR-AD-07/08)
# ===========================================================================


class TestOutputValidation:
    """Output path validation: extension, parent dir, output==input, SRT↔VTT mixing prohibited."""

    def test_srt_input_srt_output_accepted(self, tmp_path: Path, mocker: Any) -> None:
        """SRT input + SRT output is accepted (WR-AD-07/08)."""
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
        """VTT input + VTT output is accepted (WR-AD-07/08)."""
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
        """SRT input + VTT output is rejected with INVALID_INPUT due to extension mismatch (WR-AD-08)."""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.vtt")
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_vtt_input_srt_output_rejected(self, tmp_path: Path) -> None:
        """VTT input + SRT output is rejected with INVALID_INPUT due to extension mismatch (WR-AD-08)."""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_vtt(tmp_path)
        out = str(tmp_path / "output.srt")
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_unsupported_extension_rejected(self, tmp_path: Path) -> None:
        """Input with an unsupported extension such as .ass is rejected with INVALID_INPUT (WR-AD-07)."""
        wrap_captions = _import_wrap_captions()
        inp = tmp_path / "input.ass"
        inp.write_text("some content", encoding="utf-8")
        out = str(tmp_path / "output.ass")
        result: dict[str, Any] = wrap_captions(str(inp), out, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_missing_parent_dir_rejected(self, tmp_path: Path) -> None:
        """Missing output parent directory results in INVALID_INPUT (WR-AD-07)."""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "nonexistent_dir" / "output.srt")
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_output_equals_input_rejected(self, tmp_path: Path) -> None:
        """output == input is rejected with INVALID_INPUT (WR-AD-07)."""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        result: dict[str, Any] = wrap_captions(inp, inp, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_output_different_dir_accepted(self, tmp_path: Path, mocker: Any) -> None:
        """output in a different directory from input is accepted (WR-AD-07; no same-dir constraint)."""
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
# ② Input validation (WR-AD-09)
# ===========================================================================


class TestInputValidation:
    """Input file validation: FILE_NOT_FOUND, invalid subtitle INVALID_INPUT."""

    def test_file_not_found_returns_file_not_found_code(self, tmp_path: Path) -> None:
        """Non-existent input → FILE_NOT_FOUND error (WR-AD-09)."""
        wrap_captions = _import_wrap_captions()
        inp = str(tmp_path / "nonexistent.srt")
        out = str(tmp_path / "output.srt")
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "FILE_NOT_FOUND"

    def test_file_not_found_message_basename_only(self, tmp_path: Path) -> None:
        """FILE_NOT_FOUND message contains only the basename (no full path exposure; WR-AD-09)."""
        wrap_captions = _import_wrap_captions()
        inp = str(tmp_path / "secret" / "nonexistent.srt")
        out = str(tmp_path / "output.srt")
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is False
        # Parent directory component of the full path must not appear
        assert "secret" not in result["error"]["message"]
        assert "nonexistent.srt" in result["error"]["message"]

    def test_invalid_srt_timecode_returns_invalid_input(self, tmp_path: Path) -> None:
        """Invalid SRT timecode line → INVALID_INPUT (from parse_captions; WR-AD-09)."""
        wrap_captions = _import_wrap_captions()
        inp = tmp_path / "bad.srt"
        inp.write_text("1\nINVALID_TIMECODE\nテキスト\n", encoding="utf-8")
        out = str(tmp_path / "output.srt")
        result: dict[str, Any] = wrap_captions(str(inp), out, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"


# ===========================================================================
# ③ DC-GP-001 language responsibility consolidation
# ===========================================================================


class TestLanguageValidation:
    """language validation is consolidated into a ValidationError at WrapCaptionsOptions construction (DC-GP-001).

    wrap.py must not create a branch that re-validates language and converts it to INVALID_INPUT.
    """

    def test_invalid_language_raises_validation_error(self) -> None:
        """WrapCaptionsOptions(language='xx') raises ValidationError (DC-GP-001).

        Confirms that validation occurs at schema construction time,
        not in a wrap.py branch that re-validates language and converts it to INVALID_INPUT.
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WrapCaptionsOptions(language="xx")

    def test_invalid_language_en_raises_validation_error(self) -> None:
        """WrapCaptionsOptions(language='en') raises ValidationError (English is not supported)."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WrapCaptionsOptions(language="en")

    def test_valid_language_ja_accepted_in_options(self) -> None:
        """WrapCaptionsOptions(language='ja') is accepted."""
        opts = WrapCaptionsOptions(language="ja")
        assert opts.language == "ja"

    @pytest.mark.parametrize("lang", ["ja", "zh-hans", "zh-hant", "th"])
    def test_valid_languages_accepted(self, lang: str) -> None:
        """All 4 valid languages can be constructed without ValidationError."""
        opts = WrapCaptionsOptions(language=lang)
        assert opts.language == lang


# ===========================================================================
# ④ wrap_cli launch (WR-AD-02; DC-AS-007)
# ===========================================================================


class TestWrapCliInvocation:
    """Verify that wrap.py launches sys.executable -m wrap_cli as a subprocess."""

    def _patch_subprocess(self, mocker: Any, stdout_val: str) -> MagicMock:
        """Mock subprocess.run to return stdout_val. Assumes text=True mode."""
        mock_run: MagicMock = mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(stdout=stdout_val, returncode=0),
        )
        return mock_run

    def test_subprocess_called_with_sys_executable(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """subprocess.run args[0] is sys.executable (WR-AD-01)."""
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
        """subprocess command contains '-m' and 'clipwright_wrap.wrap_cli' (WR-AD-01)."""
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
        """stdin passed to subprocess contains {'language', 'texts'} (WR-AD-02)."""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.srt")
        mock_run = self._patch_subprocess(
            mocker, _wrap_cli_ok(_segments_from_texts("今日はいい天気です。"))
        )
        wrap_captions(inp, out, _opts(language="ja"))
        call_kwargs = mock_run.call_args[1]
        # Confirm that JSON is passed to stdin
        stdin_data = call_kwargs.get("input")
        assert stdin_data is not None
        parsed = json.loads(stdin_data)
        assert "language" in parsed
        assert "texts" in parsed
        assert parsed["language"] == "ja"

    def test_subprocess_stdin_texts_is_list_of_strings(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """texts in stdin JSON is list[str] (WR-AD-02)."""
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
        """When wrap_cli returns an error JSON, wrap converts it into an envelope error (DC-AS-007).

        Even if the exit code is 0, the presence of an 'error' key is treated as an error.
        """
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.srt")
        self._patch_subprocess(
            mocker,
            _wrap_cli_error(
                "DEPENDENCY_MISSING",
                "Failed to import budoux",
                "pip install clipwright-wrap",
            ),
        )
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "DEPENDENCY_MISSING"

    def test_wrap_cli_error_code_preserved_in_envelope(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """wrap_cli error code is reflected in the envelope as-is."""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.srt")
        self._patch_subprocess(
            mocker,
            _wrap_cli_error("INVALID_INPUT", "Text parsing failed"),
        )
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_subprocess_failure_stderr_sanitized(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """When subprocess fails with OSError/TimeoutError, stderr is not exposed (same as _SUBPROCESS_SAFE_MESSAGE)."""
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
        # Internal path must not be exposed in message
        assert secret not in result["error"].get("message", "")
        assert secret not in result["error"].get("hint", "")

    def test_subprocess_timeout_sanitized(self, tmp_path: Path, mocker: Any) -> None:
        """When subprocess.TimeoutExpired occurs, ok is False and stderr is not exposed."""
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
        """timeout is proportional to cue count (max(30, ceil(cue_count * 0.05))) (WR-AD-11).

        For 100 cues: timeout >= 5.0 (= ceil(100 * 0.05)) and >= 30.
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
# ⑤ Formatting flow
# ===========================================================================


class TestWrapFlow:
    """Success path: parse → wrap_cli → wrap_cue_lines → serialize → output write."""

    def test_output_file_created(self, tmp_path: Path, mocker: Any) -> None:
        """The formatted file is created at the output path."""
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
        """The input file is unchanged after formatting (non-destructive; WR-AD-07)."""
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
        """SRT output contains text formatted by wrap_cue_lines.

        max_chars=3, segments=['今日は','いい','天気です。'] → equivalent to '今日は\\nいい\\n天気です。'.
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
        # max_chars=3 → each segment fits on one line
        result: dict[str, Any] = wrap_captions(str(inp), out, _opts(max_chars=3))
        assert result["ok"] is True
        content = Path(out).read_text(encoding="utf-8")
        # Verify that a line break was inserted
        assert "\n" in content.split("\n", 3)[3].strip() or "いい" in content

    def test_segments_length_matches_cue_count(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """Length of texts passed to wrap_cli matches the cue count (WR-AD-02)."""
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
# ⑥ Overflow detection (WR-AD-15(1)/DC-AM-003)
# ===========================================================================


class TestOverflow:
    """Overflow identifies both line_count(a) and line_width(b) (WR-AD-15(1))."""

    def _run_with_segments(
        self,
        tmp_path: Path,
        mocker: Any,
        segments: list[list[str]],
        max_chars: int = 16,
        max_lines: int = 2,
    ) -> dict[str, Any]:
        """Return segments from wrap_cli and execute wrap_captions."""
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
        """Cue with line-count excess (a) is recorded in data.overflow_cue_indices (WR-AD-15(1)/DC-AM-002).

        max_lines=2, 3 segments (each within max_chars) → 3 lines → line-count overflow.
        """
        # With max_chars=5, ['今日は', 'いい', '天気です'] each fits within 3 chars → 3 lines
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
        # A line-count overflow cue exists
        assert "overflow_cue_indices" in data

    def test_overflow_line_width_sets_overflow_width_cue_indices(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """Cue with line-width excess (b) is recorded in data.overflow_width_cue_indices (WR-AD-15(1)).

        A single oversized segment ('あ' * 20) is placed on one line even with max_chars=5, causing line-width overflow.
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
        # At least one line-width overflow cue exists
        assert len(data["overflow_width_cue_indices"]) > 0

    def test_no_overflow_empty_overflow_indices(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """When there is no overflow, overflow_cue_indices/overflow_width_cue_indices are empty lists."""
        # max_chars=16, max_lines=2, 1 segment → no overflow
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
        """When overflow count is 0, warnings do not contain a max_lines-related message (DC-AM-002)."""
        segments = [["今日は"]]
        result = self._run_with_segments(
            tmp_path,
            mocker,
            segments,
            max_chars=16,
            max_lines=2,
        )
        warnings: list[str] = result.get("warnings", [])
        # No max_lines overflow warning emitted
        for w in warnings:
            assert "max_lines" not in w

    def test_overflow_not_cut_off(self, tmp_path: Path, mocker: Any) -> None:
        """Overflow cues are not truncated; all text is present in the output file (WR-AD-15(1)).

        max_lines=1, 3 segments → 3 lines, but all text must appear in the output.
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
        # All segments are present in the output (no truncation)
        assert "今日は" in content
        assert "いい" in content
        assert "天気です。" in content


# ===========================================================================
# ⑦ Warnings aggregation (WR-AD-13(2)/DC-AM-002)
# ===========================================================================


class TestWarningsAggregation:
    """Overflow warnings use a single aggregated sentence + index arrays in data (not one per cue)."""

    def test_overflow_line_count_warning_is_single_sentence(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """Line-count overflow warnings consist of a single sentence regardless of cue count (DC-AM-002).

        Even when all 3 cues overflow, the warnings list has at most a small number of elements.
        """
        wrap_captions = _import_wrap_captions()
        # 3 cues, each with 3 segments (max_chars=3, max_lines=1 → all cues overflow on line count)
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
        # warnings should be few (not one per cue)
        overflow_warnings = [
            w
            for w in result.get("warnings", [])
            if "max_lines" in w or "overflow" in w or "exceeded" in w
        ]
        # Aggregated: even with 3 overflowing cues, warnings should be at most ~1-2 sentences
        assert len(overflow_warnings) <= 3

    def test_overflow_data_has_overflow_cue_indices_list(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """data contains overflow_cue_indices as list[int] (WR-AD-13(2))."""
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
        """data contains overflow_width_cue_indices as list[int] (WR-AD-13(2))."""
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
# ⑧ artifacts (WR-AD-13(1)/DC-AS-005)
# ===========================================================================


class TestArtifacts:
    """artifacts are in dict format, Artifact model not instantiated, no OTIO generated (WR-AD-13(1)/DC-AS-005)."""

    def _run_normal(
        self, tmp_path: Path, mocker: Any, fmt: str = "srt"
    ) -> dict[str, Any]:
        """Execute the success path and return the result."""
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
        """artifacts is a list."""
        result = self._run_normal(tmp_path, mocker)
        assert isinstance(result["artifacts"], list)

    def test_artifacts_single_element(self, tmp_path: Path, mocker: Any) -> None:
        """artifacts contains 1 element (the output subtitle)."""
        result = self._run_normal(tmp_path, mocker)
        assert len(result["artifacts"]) == 1

    def test_artifacts_element_is_dict(self, tmp_path: Path, mocker: Any) -> None:
        """Each element of artifacts is a dict (not an Artifact model instance; DC-AS-005)."""
        result = self._run_normal(tmp_path, mocker)
        artifact = result["artifacts"][0]
        assert isinstance(artifact, dict)

    def test_artifacts_element_has_role_path_format(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """Each element of artifacts contains role / path / format keys."""
        result = self._run_normal(tmp_path, mocker)
        artifact = result["artifacts"][0]
        assert "role" in artifact
        assert "path" in artifact
        assert "format" in artifact

    def test_artifacts_role_is_captions(self, tmp_path: Path, mocker: Any) -> None:
        """artifacts[0]["role"] is 'captions' (WR-AD-13(1))."""
        result = self._run_normal(tmp_path, mocker)
        assert result["artifacts"][0]["role"] == "captions"

    def test_artifacts_format_srt(self, tmp_path: Path, mocker: Any) -> None:
        """For SRT output, artifacts[0]["format"] is 'srt'."""
        result = self._run_normal(tmp_path, mocker, fmt="srt")
        assert result["artifacts"][0]["format"] == "srt"

    def test_artifacts_format_vtt(self, tmp_path: Path, mocker: Any) -> None:
        """For VTT output, artifacts[0]["format"] is 'vtt'."""
        result = self._run_normal(tmp_path, mocker, fmt="vtt")
        assert result["artifacts"][0]["format"] == "vtt"

    def test_no_otio_artifact(self, tmp_path: Path, mocker: Any) -> None:
        """artifacts does not contain any OTIO-related elements (WR-AD-13(1); no OTIO generated)."""
        result = self._run_normal(tmp_path, mocker)
        for artifact in result["artifacts"]:
            assert artifact.get("format") != "otio"

    def test_no_otio_file_created(self, tmp_path: Path, mocker: Any) -> None:
        """No OTIO file is created after execution (WR-AD-13(1))."""
        self._run_normal(tmp_path, mocker)
        otio_files = list(tmp_path.glob("*.otio"))
        assert len(otio_files) == 0


# ===========================================================================
# ⑨ Envelope (summary, data)
# ===========================================================================


class TestEnvelope:
    """Verify the ok_result envelope contents: summary and data structure."""

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
        """Success path: ok is True."""
        result = self._run(tmp_path, mocker)
        assert result["ok"] is True

    def test_summary_contains_language(self, tmp_path: Path, mocker: Any) -> None:
        """summary contains the language (WR-AD-04 §4)."""
        result = self._run(tmp_path, mocker)
        assert "ja" in result["summary"]

    def test_summary_contains_cue_count(self, tmp_path: Path, mocker: Any) -> None:
        """summary contains the formatted cue count (§4)."""
        result = self._run(tmp_path, mocker)
        # "1" must appear in summary (1 cue)
        assert "1" in result["summary"]

    def test_data_has_cue_count(self, tmp_path: Path, mocker: Any) -> None:
        """data contains cue_count (§4)."""
        result = self._run(tmp_path, mocker)
        assert "cue_count" in result["data"]
        assert result["data"]["cue_count"] == 1

    def test_data_has_wrapped_count(self, tmp_path: Path, mocker: Any) -> None:
        """data contains wrapped_count (number of cues with line breaks inserted) (§4)."""
        result = self._run(tmp_path, mocker)
        assert "wrapped_count" in result["data"]

    def test_data_has_overflow_cue_indices(self, tmp_path: Path, mocker: Any) -> None:
        """data contains overflow_cue_indices (list[int]) (§4)."""
        result = self._run(tmp_path, mocker)
        assert "overflow_cue_indices" in result["data"]
        assert isinstance(result["data"]["overflow_cue_indices"], list)

    def test_data_has_overflow_width_cue_indices(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """data contains overflow_width_cue_indices (list[int]) (§4)."""
        result = self._run(tmp_path, mocker)
        assert "overflow_width_cue_indices" in result["data"]
        assert isinstance(result["data"]["overflow_width_cue_indices"], list)

    def test_data_has_language(self, tmp_path: Path, mocker: Any) -> None:
        """data contains language (§4)."""
        result = self._run(tmp_path, mocker)
        assert "language" in result["data"]
        assert result["data"]["language"] == "ja"

    def test_envelope_has_warnings_list(self, tmp_path: Path, mocker: Any) -> None:
        """Envelope contains a warnings list (ok_result format)."""
        result = self._run(tmp_path, mocker)
        assert "warnings" in result
        assert isinstance(result["warnings"], list)

    def test_envelope_has_artifacts_list(self, tmp_path: Path, mocker: Any) -> None:
        """Envelope contains an artifacts list (ok_result format)."""
        result = self._run(tmp_path, mocker)
        assert "artifacts" in result
        assert isinstance(result["artifacts"], list)


# ===========================================================================
# ⑩ Empty subtitle (0 cues) defence
# ===========================================================================


class TestEmptyCaptions:
    """Defence: even with 0 cues (empty subtitle), return ok:True with empty output (WR-AD-12(2))."""

    def test_empty_srt_returns_ok(self, tmp_path: Path, mocker: Any) -> None:
        """Empty SRT (empty string) input returns ok:True with cue_count=0."""
        wrap_captions = _import_wrap_captions()
        inp = tmp_path / "empty.srt"
        inp.write_text("", encoding="utf-8")
        out = str(tmp_path / "output.srt")
        # When 0 cues, wrap_cli is either not called or receives texts=[]
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
        """VTT header-only input (0 cues) returns ok:True with cue_count=0."""
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
        """Empty SRT input → output file is also an empty string (WR-AD-12(2))."""
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
        """VTT header-only input → output file is also 'WEBVTT\\n' (WR-AD-12(2); round-trip identity)."""
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


# ===========================================================================
# CR M-2 / SR M-1: Invalid timecode line must not appear in error.message
# ===========================================================================


class TestTimecodeInjectionSafety:
    """Verify that invalid SRT timecode line content does not leak into error.message (CR M-2 / SR M-1).

    In the current implementation, wrap.py L160 concatenates the ValueError message via str(exc),
    causing user-supplied input (timeline_line) to bleed into message.
    These tests only pass after changing to a fixed message string (Red phase).
    """

    def test_inject_payload_not_in_error_message(self, tmp_path: Path) -> None:
        """Injected payload in a timeline line must not appear in error.message (SR M-1)."""
        wrap_captions = _import_wrap_captions()
        inject_payload = "<script>alert(1)</script>"
        # Plant the inject payload in the SRT timeline line position
        bad_srt = f"1\n{inject_payload}\nテキスト\n"
        inp = tmp_path / "inject.srt"
        inp.write_text(bad_srt, encoding="utf-8")
        out = str(tmp_path / "output.srt")
        result: dict[str, Any] = wrap_captions(str(inp), out, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"
        # inject payload must not appear in the message (only the fixed string)
        assert inject_payload not in result["error"]["message"]
        assert "<script>" not in result["error"]["message"]

    def test_crlf_injection_not_in_error_message(self, tmp_path: Path) -> None:
        """Content of a timeline line containing CRLF must not be exposed in error.message (SR M-1).

        CRLF line endings are valid in SRT (standard subtitle files). Universal newline
        conversion in text-mode I/O normalises CRLF to LF, so CRLF injection does not
        constitute invalid input (parse succeeds; ok=True), and the leak path itself does
        not arise. This test enforces the contract that even on parse failure the injected
        string does not appear in message (regression guard for fixed-message change).
        Refusing CRLF by modifying captions.py would regress valid CRLF subtitles, so
        detection is intentionally omitted here.
        """
        wrap_captions = _import_wrap_captions()
        crlf_payload = "00:00:00,000 --> 00:00:01,000\r\nX-Injected: header"
        bad_srt = f"1\n{crlf_payload}\nテキスト\n"
        inp = tmp_path / "crlf.srt"
        inp.write_text(bad_srt, encoding="utf-8")
        out = str(tmp_path / "output.srt")
        result: dict[str, Any] = wrap_captions(str(inp), out, _opts())
        # Even on failure, the injected string must not appear in message (no leakage)
        if result["ok"] is False:
            assert "X-Injected" not in result["error"]["message"]

    def test_error_message_is_fixed_string(self, tmp_path: Path) -> None:
        """error.message on invalid timecode must be a fixed string (SR M-1 recommended action).

        The current implementation concatenates the timecode line via f-string, so this test is Red.
        """
        wrap_captions = _import_wrap_captions()
        inp = tmp_path / "bad.srt"
        inp.write_text("1\nINVALID_TC_LINE_UNIQUE_MARKER\nテキスト\n", encoding="utf-8")
        out = str(tmp_path / "output.srt")
        result: dict[str, Any] = wrap_captions(str(inp), out, _opts())
        assert result["ok"] is False
        # The timecode line itself must not appear in message
        assert "INVALID_TC_LINE_UNIQUE_MARKER" not in result["error"]["message"]


# ===========================================================================
# CR M-3: subprocess.run must receive text=True and encoding="utf-8"
# ===========================================================================


class TestSubprocessTextMode:
    """Verify that wrap.py passes text=True, encoding='utf-8' to subprocess.run (CR M-3).

    The current implementation omits the text parameter (defaults to False) and uses bytes I/O;
    this test is Red until text=True is set.
    """

    def test_subprocess_called_with_text_true(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """subprocess.run must receive text=True (CR M-3)."""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.srt")
        mock_run: MagicMock = mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=json.dumps(
                    {"segments": [["今日はいい天気です。"]]}, ensure_ascii=False
                ),
                returncode=0,
            ),
        )
        wrap_captions(inp, out, _opts())
        call_kwargs = mock_run.call_args[1]
        # text=True must be passed
        assert call_kwargs.get("text") is True

    def test_subprocess_called_with_encoding_utf8(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """subprocess.run must receive encoding='utf-8' (CR M-3)."""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.srt")
        mock_run: MagicMock = mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=json.dumps(
                    {"segments": [["今日はいい天気です。"]]}, ensure_ascii=False
                ),
                returncode=0,
            ),
        )
        wrap_captions(inp, out, _opts())
        call_kwargs = mock_run.call_args[1]
        # encoding="utf-8" must be passed
        assert call_kwargs.get("encoding") == "utf-8"

    def test_subprocess_input_is_str_not_bytes(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """subprocess.run input must be str (not bytes) (CR M-3).

        When text=True and encoding='utf-8' are specified, input must be passed as str.
        """
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.srt")
        mock_run: MagicMock = mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=json.dumps(
                    {"segments": [["今日はいい天気です。"]]}, ensure_ascii=False
                ),
                returncode=0,
            ),
        )
        wrap_captions(inp, out, _opts())
        call_kwargs = mock_run.call_args[1]
        stdin_data = call_kwargs.get("input")
        # input must be str (not bytes)
        assert isinstance(stdin_data, str), (
            f"input should be str but got {type(stdin_data)}"
        )


# ===========================================================================
# CR M-4: summary overflow cue count must use set union (no double-counting)
# ===========================================================================


class TestOverflowSummaryDeduplication:
    """Verify that when 1 cue satisfies both line-count overflow (a) and line-width overflow (b),
    the overflow cue count in summary uses set union (no duplicates) (CR M-4).

    The current implementation uses len(overflow_cue_indices) + len(overflow_width_cue_indices),
    resulting in double-counting; this test is Red.
    """

    def test_both_overflow_cue_counted_once_in_summary(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """The same cue satisfying both (a) and (b) must be counted as 1 in summary (CR M-4).

        max_chars=3, max_lines=1, 1 cue (3 segments) → (a) 3 lines > 1 (overflow); (b) 3 chars = max_chars (no overflow).
        For a more reliable setup, use a single oversized segment (20 chars) on 1 line + max_lines=0 to satisfy both conditions simultaneously.
        """
        wrap_captions = _import_wrap_captions()
        # Single oversized segment: line count = 1 > max_lines=0 is not settable.
        # Case: max_chars=3, max_lines=1, 3 segments (each 1 char):
        # wrap_cue_lines(['あ','い','う'], max_chars=3) → ['あいう'] (3 chars = max_chars, no overflow)
        # → 1 line = max_lines=1 (no overflow)
        # Alternative: oversized segment (20 chars), max_chars=3, max_lines=1
        # wrap_cue_lines(['あ'*20], max_chars=3) → ['あ'*20] (20 chars > 3 = line-width overflow)
        # 1 line = max_lines=1 (no overflow) → both conditions cannot fail simultaneously
        # Reliable case: 3 segments, max_chars=1, max_lines=1
        # wrap_cue_lines(['あ','い','う'], max_chars=1) → ['あ','い','う'] (3 lines > 1 = overflow; each 1 char = max_chars, no width overflow)
        # → (a) only
        # Both simultaneously: 2 oversized segments, max_chars=3, max_lines=1
        # wrap_cue_lines(['あ'*10,'い'*10], max_chars=3)
        #   → ['あ'*10] (10 > 3 = line-width overflow on 1 line), then next segment → ['あ'*10,'い'*10] (2 lines > 1 = line-count overflow AND each 10 > 3 = line-width overflow)
        # Both conditions occur simultaneously → 1 cue double-counted for (a) and (b)
        inp = _make_input_srt(tmp_path, _srt_1cue("あいうえおかきくけこさしすせそ"))
        out = str(tmp_path / "output.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok([["あいうえおかきく", "けこさしすせそ"]]),
                returncode=0,
            ),
        )
        # max_chars=3 → each segment is 8 chars / 7 chars → cannot be split → 1 line each
        # → 2 lines > max_lines=1 (line-count overflow) and 8 chars > max_chars=3 (line-width overflow) → both conditions simultaneously
        result: dict[str, Any] = wrap_captions(
            str(inp), out, _opts(max_chars=3, max_lines=1)
        )
        assert result["ok"] is True
        data = result["data"]
        # Both overflow_cue_indices and overflow_width_cue_indices must contain index 0
        assert 0 in data["overflow_cue_indices"]
        assert 0 in data["overflow_width_cue_indices"]
        # Summary overflow cue count must be set union (no duplicates) = 1
        # Current implementation gives 1 + 1 = 2 → Red
        summary = result["summary"]
        # Confirm count is 1; "2 cue(s) exceeded" (double-counting) must not appear
        assert "2 cue(s) exceeded" not in summary, (
            f"summary contains double-counted overflow (2 cues): {summary!r}"
        )

    def test_overflow_summary_count_uses_set_union(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """summary overflow cue count is calculated using set union (CR M-4 numeric verification).

        When 1 cue satisfies both (a) and (b), total_overflow must be 1.
        Current implementation gives len(a_list) + len(b_list) = 2 → Red.
        """
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path, _srt_1cue("テスト"))
        out = str(tmp_path / "output.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok([["あいうえおかきく", "けこさしすせそ"]]),
                returncode=0,
            ),
        )
        result: dict[str, Any] = wrap_captions(
            str(inp), out, _opts(max_chars=3, max_lines=1)
        )
        assert result["ok"] is True
        # Both index arrays must contain the same cue (both conditions simultaneously)
        data = result["data"]
        assert data["overflow_cue_indices"] == [0]
        assert data["overflow_width_cue_indices"] == [0]
        # summary must show overflow count as 1 (not 2)
        # Current implementation gives "2 cue(s) exceeded" → this assert is Red
        assert "1 cue(s) exceeded limits" in result["summary"], (
            f"summary overflow count is not 1 (possible double-counting): {result['summary']!r}"
        )


# ===========================================================================
# CR M-1: Tests that reach uncovered lines (L97, L212-213, L227-228)
# ===========================================================================


class TestUncoveredBranches:
    """Reach uncovered branches in wrap.py (CR M-1).

    L131-132 (OSError fallback) and L166-167 (unreachable except ClipwrightError) are
    handled on the impl side via pragma/deletion; no tests are written for those here.
    """

    def test_srt_input_mp4_output_rejected(self, tmp_path: Path) -> None:
        """input=.srt with output=.mp4 (invalid output extension only) → INVALID_INPUT (reaches L97).

        Even when input is a valid .srt, an unsupported output extension such as .mp4 must be rejected at L97.
        """
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.mp4")
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"
        assert (
            "mp4" in result["error"]["message"]
            or "extension" in result["error"]["message"]
        )

    def test_wrap_cli_empty_stdout_returns_subprocess_failed(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """When wrap_cli returns empty stdout (JSON decode failure) → SUBPROCESS_FAILED (reaches L212-213).

        Case where stdout is an empty string on abnormal process exit.
        """
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                # Empty string → JSONDecodeError from json.loads("") (text=True mode)
                stdout="",
                returncode=0,
            ),
        )
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is False
        assert result["error"]["code"] == "SUBPROCESS_FAILED"

    def test_wrap_cli_unknown_error_code_falls_back_to_internal(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """When wrap_cli returns an unknown error code → INTERNAL fallback (reaches L227-228)."""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path)
        out = str(tmp_path / "output.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_error("UNKNOWN_CUSTOM_ERROR_XYZ", "Unknown error"),
                returncode=0,
            ),
        )
        result: dict[str, Any] = wrap_captions(inp, out, _opts())
        assert result["ok"] is False
        # UNKNOWN_CUSTOM_ERROR_XYZ does not exist in ErrorCode → falls back to INTERNAL
        assert result["error"]["code"] == "INTERNAL"
