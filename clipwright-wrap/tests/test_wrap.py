"""test_wrap.py — Tests for the wrap.py orchestration layer.

Target API:
  clipwright_wrap.wrap.wrap_captions(
      input: str, output: str, options: WrapCaptionsOptions,
  ) -> dict[str, Any]

Mocking strategy:
  - subprocess.run (or wrap._run_wrap_cli) is replaced using pytest-mock.
    wrap.py launches sys.executable -m clipwright_wrap.wrap_cli as a subprocess (WR-AD-01).
  - Real budoux and real SRT/VTT file writes are verified in wrap e2e tests.
  - captions.parse_captions / captions.serialize_captions are called for real (pure logic).

Verification aspects (WR-AD-02/07/08/09/11/13/14/15):
  ① Output validation (WR-AD-07/08): extension match, parent dir existence, output==input prohibited
  ② Input validation (WR-AD-09): FILE_NOT_FOUND basename only, INVALID_INPUT on parse failure
  ③ DC-GP-001 language responsibility: WrapCaptionsOptions(language='xx') → ValidationError
  ④ wrap_cli launch (WR-AD-02; DC-AS-007): sys.executable -m wrap_cli, stdin JSON, error key detection
  ⑤ Formatting flow: parse→wrap_cli→wrap_cue_lines→serialize→output write (input unchanged)
  ⑥ WR-AD-15(1) revised / ADR-W1/W2: front-merge convergence; line-count overflow replaced by
     merged_cue_indices; line-width overflow (overflow_width_cue_indices) detected post-merge
  ⑦ WR-AD-13(2)/DC-AM-002 warnings aggregation: line-count warning removed; width-overflow warning
     only; merged_cue_indices and overflow_width_cue_indices in data
  ⑧ WR-AD-13(1)/DC-AS-005 artifacts: dict format, no OTIO generated
  ⑨ Envelope: summary contains cue count/merged count/width-overflow count/language; lightweight data
  ⑩ 0-cue (empty subtitle) defence: ok:True, empty output
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

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
# Import wrap_captions
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

    def test_language_en_accepted_in_options(self) -> None:
        """WrapCaptionsOptions(language='en') is accepted (contract change: en is now valid; T-1)."""
        opts = WrapCaptionsOptions(language="en")
        assert opts.language == "en"

    def test_valid_language_ja_accepted_in_options(self) -> None:
        """WrapCaptionsOptions(language='ja') is accepted."""
        opts = WrapCaptionsOptions(language="ja")
        assert opts.language == "ja"

    @pytest.mark.parametrize("lang", ["ja", "zh-hans", "zh-hant", "th"])
    def test_valid_cjk_languages_accepted(self, lang: str) -> None:
        """All 4 CJK/Thai languages can be constructed without ValidationError."""
        opts = WrapCaptionsOptions(language=lang)
        assert opts.language == lang

    @pytest.mark.parametrize("lang", ["en", "es", "fr", "de", "it", "pt", "nl"])
    def test_valid_latin_languages_accepted(self, lang: str) -> None:
        """All 7 space-delimited Latin languages can be constructed without ValidationError (T-1)."""
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
# ⑥ Front-merge and overflow detection (WR-AD-15(1) revised / ADR-W1/W2)
# ===========================================================================


class TestOverflow:
    """Front-merge replaces line-count overflow; width overflow detected post-merge (WR-AD-15(1) revised)."""

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

    def test_merge_sets_merged_cue_indices(self, tmp_path: Path, mocker: Any) -> None:
        """Cue exceeding max_lines is front-merged and recorded in data.merged_cue_indices (ADR-W1/W2).

        max_lines=2, max_chars=2, 3 segments (['今日は', 'いい', '天気です']) — each segment
        exceeds max_chars=2 individually, so wrap_cue_lines places each on its own line
        (no splitting of oversized segments; WR-AD-14). 3 lines > max_lines=2 →
        front-merge is applied. The cue index must appear in merged_cue_indices (FR-2).
        """
        # With max_chars=2, each segment exceeds max_chars → 3 lines > max_lines=2
        segments = [["今日は", "いい", "天気です"]]
        result = self._run_with_segments(
            tmp_path,
            mocker,
            segments,
            max_chars=2,
            max_lines=2,
        )
        assert result["ok"] is True
        data = result["data"]
        # Cue was merged; index 0 must appear in merged_cue_indices
        assert "merged_cue_indices" in data
        assert 0 in data["merged_cue_indices"]

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
        """When there is no overflow or merge, overflow_width_cue_indices and merged_cue_indices are empty."""
        # max_chars=16, max_lines=2, 1 segment → no overflow, no merge
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
        assert data.get("overflow_width_cue_indices", []) == []
        assert data.get("merged_cue_indices", []) == []

    def test_no_overflow_no_max_lines_warning(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """When overflow count is 0, warnings do not contain a max_lines-related message (DC-GP-002)."""
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

        max_lines=1, 3 segments → front-merge collapses to 1 line, but all text must appear.
        Output cue must have len(text.split('\\n')) <= max_lines=1 (FR-1 convergence pin).
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
        # FR-1 convergence pin: output cue text fits within max_lines=1
        # Extract the cue text block from the SRT (after "1\ntimecode\n")
        lines_in_file = content.strip().splitlines()
        # SRT structure: index, timecode, text line(s), empty line
        # Skip index (line 0) and timecode (line 1); collect text lines
        cue_text_lines = [ln for ln in lines_in_file[2:] if ln.strip()]
        assert len(cue_text_lines) <= 1, (
            f"Expected at most 1 text line after front-merge (max_lines=1), got: {cue_text_lines!r}"
        )


# ===========================================================================
# ⑦ Warnings aggregation (WR-AD-13(2)/DC-AM-002)
# ===========================================================================


class TestWarningsAggregation:
    """Width-overflow warnings only; no line-count warning; index arrays in data (DC-AM-002)."""

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

    def test_no_line_count_warning_multi_cue(self, tmp_path: Path, mocker: Any) -> None:
        """Multi-cue input with line-count excess emits no 'max_lines' warning; all cues in merged_cue_indices (FR-3/DC-GP-002).

        3 cues, each with 3 segments, max_lines=1 → all cues front-merged.
        warnings must not contain any 'max_lines' string.
        All 3 cue indices must appear in merged_cue_indices.
        """
        wrap_captions = _import_wrap_captions()
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
        # No max_lines warning (DC-GP-002: line-count warning removed)
        for w in result.get("warnings", []):
            assert "max_lines" not in w, f"Unexpected max_lines warning found: {w!r}"
        # All 3 cues are merged (FR-3)
        data = result["data"]
        assert set(data.get("merged_cue_indices", [])) == {0, 1, 2}, (
            f"Expected all 3 cue indices in merged_cue_indices, got: {data.get('merged_cue_indices')!r}"
        )


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

    def test_data_has_merged_cue_indices(self, tmp_path: Path, mocker: Any) -> None:
        """data contains merged_cue_indices (list[int]) replacing overflow_cue_indices (ADR-W2/§4)."""
        result = self._run(tmp_path, mocker)
        assert "merged_cue_indices" in result["data"]
        assert isinstance(result["data"]["merged_cue_indices"], list)

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

    wrap.py uses a fixed message string instead of concatenating the ValueError content,
    preventing user-supplied input (timeline_line) from bleeding into message.
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
        """error.message on invalid timecode must be a fixed string (SR M-1 recommended action)."""
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
    """Verify that wrap.py passes text=True, encoding='utf-8' to subprocess.run (CR M-3)."""

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
# New tests: FR-2, DC-AS-003, DC-AS-005 (front-merge design)
# ===========================================================================


class TestFrontMerge:
    """Additional tests for front-merge behaviour (FR-2, DC-AS-003, DC-AS-005)."""

    def _run_with_segments(
        self,
        tmp_path: Path,
        mocker: Any,
        srt_content: str,
        segments: list[list[str]],
        max_chars: int = 16,
        max_lines: int = 2,
    ) -> dict[str, Any]:
        """Helper: run wrap_captions with given SRT and mocked segments."""
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path, srt_content)
        out = str(tmp_path / "output.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok(segments),
                returncode=0,
            ),
        )
        return wrap_captions(inp, out, _opts(max_chars=max_chars, max_lines=max_lines))

    def test_merged_cue_indices_populated(self, tmp_path: Path, mocker: Any) -> None:
        """FR-2: merged_cue_indices contains 0-based indices of merged cues; [] when no merge occurs.

        Case A: 1 cue, 3 segments, max_chars=2, max_lines=2 → each segment exceeds max_chars=2
        so wrap_cue_lines produces 3 lines > max_lines=2 → merged; index 0 in merged_cue_indices.
        Case B: 1 cue, 1 segment, max_lines=2 → 1 line <= 2 → not merged; merged_cue_indices=[].
        """
        # Case A: merge occurs — max_chars=2 forces each segment onto its own line (3 lines > 2)
        result_a = self._run_with_segments(
            tmp_path,
            mocker,
            _srt_1cue("今日はいい天気です。"),
            segments=[["今日は", "いい", "天気です"]],
            max_chars=2,
            max_lines=2,
        )
        assert result_a["ok"] is True
        assert 0 in result_a["data"]["merged_cue_indices"], (
            f"Expected index 0 in merged_cue_indices: {result_a['data']['merged_cue_indices']!r}"
        )

        # Case B: no merge (reuse tmp_path with a fresh output name)
        wrap_captions = _import_wrap_captions()
        inp_b = tmp_path / "input_b.srt"
        inp_b.write_text(_srt_1cue("今日は"), encoding="utf-8")
        out_b = str(tmp_path / "output_b.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok([["今日は"]]),
                returncode=0,
            ),
        )
        result_b = wrap_captions(str(inp_b), out_b, _opts(max_chars=10, max_lines=2))
        assert result_b["ok"] is True
        assert result_b["data"]["merged_cue_indices"] == [], (
            f"Expected empty merged_cue_indices when no merge, got: {result_b['data']['merged_cue_indices']!r}"
        )

    def test_wrapped_count_zero_on_max_lines_1_full_collapse(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """DC-AS-003: max_lines=1 full-collapse cue where front-merge produces unchanged text.

        When 3 segments ['A', 'B', 'C'] with max_chars=2 are collapsed to 1 line by front-merge,
        the resulting single line is 'ABC' (length 3 > max_chars=2 → width overflow).
        The cue index must appear in merged_cue_indices.

        Note: wrapped_count may be > 0 because text changes from 'テキスト1' to 'ABC'.
        The key assertion is that merged_cue_indices contains the cue index.
        """
        result = self._run_with_segments(
            tmp_path,
            mocker,
            _srt_1cue("テキスト"),
            segments=[["A", "B", "C"]],
            max_chars=2,
            max_lines=1,
        )
        assert result["ok"] is True
        data = result["data"]
        # Cue was merged (3 lines → 1 line)
        assert 0 in data["merged_cue_indices"], (
            f"Expected index 0 in merged_cue_indices: {data['merged_cue_indices']!r}"
        )

    def test_merge_induces_width_overflow(self, tmp_path: Path, mocker: Any) -> None:
        """DC-AS-005: max_lines=1 with each segment within max_chars, but merged line exceeds max_chars.

        Segments: ['あいう', 'えおか'] each has 3 chars = max_chars=3 (no individual overflow).
        After front-merge to 1 line: 'あいうえおか' (6 chars > max_chars=3) → width overflow.
        The cue must appear in overflow_width_cue_indices.
        """
        result = self._run_with_segments(
            tmp_path,
            mocker,
            _srt_1cue("あいうえおか"),
            segments=[["あいう", "えおか"]],
            max_chars=3,
            max_lines=1,
        )
        assert result["ok"] is True
        data = result["data"]
        # Merge occurred (2 lines → 1 line)
        assert 0 in data["merged_cue_indices"], (
            f"Expected index 0 in merged_cue_indices: {data['merged_cue_indices']!r}"
        )
        # Merged line 'あいうえおか' (6 chars) > max_chars=3 → width overflow
        assert 0 in data["overflow_width_cue_indices"], (
            f"Expected index 0 in overflow_width_cue_indices: {data['overflow_width_cue_indices']!r}"
        )


# ===========================================================================
# SR-L-4: data index fields must not expose absolute paths or string values
# ===========================================================================


class TestIndexDataFieldsNoPathLeak:
    """Verify that merged_cue_indices and overflow_width_cue_indices contain only int values.

    SR-L-4: data fields that record cue indices must be list[int] and must never
    contain strings (e.g. absolute file paths), regardless of the input.
    """

    def test_index_data_fields_contain_no_paths(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """merged_cue_indices and overflow_width_cue_indices are list[int] with no string elements (SR-L-4).

        Uses max_chars=1, max_lines=1 with multiple segments so that both front-merge
        (3 lines > max_lines=1) and line-width overflow (each segment > max_chars=1)
        occur, ensuring both lists are non-empty.
        """
        wrap_captions = _import_wrap_captions()
        inp = _make_input_srt(tmp_path, _srt_1cue("あいう"))
        out = str(tmp_path / "output.srt")
        mocker.patch(
            "clipwright_wrap.wrap.subprocess.run",
            return_value=MagicMock(
                stdout=_wrap_cli_ok([["あ", "い", "う"]]),
                returncode=0,
            ),
        )
        result: dict[str, Any] = wrap_captions(
            str(inp), out, _opts(max_chars=1, max_lines=1)
        )
        assert result["ok"] is True
        data = result["data"]

        merged = data["merged_cue_indices"]
        width_overflow = data["overflow_width_cue_indices"]

        # Both lists must be non-empty (merge and width overflow both triggered)
        assert len(merged) > 0, (
            f"merged_cue_indices should be non-empty, got: {merged!r}"
        )
        assert len(width_overflow) > 0, (
            f"overflow_width_cue_indices should be non-empty, got: {width_overflow!r}"
        )

        # All elements must be int — no strings (e.g. no absolute paths)
        for x in merged:
            assert isinstance(x, int), (
                f"merged_cue_indices element must be int, got {type(x)}: {x!r}"
            )
        for x in width_overflow:
            assert isinstance(x, int), (
                f"overflow_width_cue_indices element must be int, got {type(x)}: {x!r}"
            )

        # Explicit: no string element in either list
        assert not any(isinstance(x, str) for x in merged), (
            f"merged_cue_indices must not contain strings: {merged!r}"
        )
        assert not any(isinstance(x, str) for x in width_overflow), (
            f"overflow_width_cue_indices must not contain strings: {width_overflow!r}"
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


# ===========================================================================
# T-5: Latin (English) word-wrap end-to-end
# ===========================================================================


class TestLatinWordWrap:
    """T-5: End-to-end wrap_captions with language='en' on an English .srt file.

    Verifies the Latin in-process path:
      - language='en' is accepted by the updated LANGUAGE_PATTERN
      - wrap_captions returns ok=True using in-process whitespace split (no subprocess)
      - Each output line respects max_chars with word-boundary wrapping
      - Single spaces are preserved between words (joiner=' ')
      - No text is truncated (all input words appear in the output)
    """

    # Canonical English fixture: 9 words, 43 chars total — does not fit on one
    # line at max_chars<=15, so word-boundary wrapping is observable.
    _ENGLISH_SRT = (
        "1\n00:00:00,000 --> 00:00:03,000\n"
        "The quick brown fox jumps over the lazy dog\n"
    )

    def _make_input_en(self, tmp_path: Path, name: str = "input_en.srt") -> str:
        """Write the English fixture SRT to tmp_path and return its absolute path."""
        p = tmp_path / name
        p.write_text(self._ENGLISH_SRT, encoding="utf-8")
        return str(p)

    def test_english_srt_wrap_ok_true(self, tmp_path: Path) -> None:
        """wrap_captions with language='en' returns ok=True (T-5, AC-1)."""
        wrap_captions = _import_wrap_captions()
        inp = self._make_input_en(tmp_path, "input_en_ok.srt")
        out = str(tmp_path / "output_en_ok.srt")
        opts = WrapCaptionsOptions(language="en", max_chars=20, max_lines=2)
        result: dict[str, Any] = wrap_captions(inp, out, opts)
        assert result["ok"] is True

    def test_english_srt_data_language_preserved(self, tmp_path: Path) -> None:
        """data.language is 'en' in the result envelope (T-5, AC-1)."""
        wrap_captions = _import_wrap_captions()
        inp = self._make_input_en(tmp_path, "input_en_lang.srt")
        out = str(tmp_path / "output_en_lang.srt")
        opts = WrapCaptionsOptions(language="en", max_chars=20, max_lines=2)
        result: dict[str, Any] = wrap_captions(inp, out, opts)
        assert result["ok"] is True
        assert result["data"]["language"] == "en"

    def test_english_srt_lines_within_max_chars(self, tmp_path: Path) -> None:
        """Each text line in the output is at most max_chars characters (T-5, AC-1 line-length).

        With max_chars=15, 'The quick brown fox...' must be split across multiple lines.
        """
        wrap_captions = _import_wrap_captions()
        max_chars = 15
        inp = self._make_input_en(tmp_path, "input_en_chars.srt")
        out = str(tmp_path / "output_en_chars.srt")
        opts = WrapCaptionsOptions(language="en", max_chars=max_chars, max_lines=3)
        result: dict[str, Any] = wrap_captions(inp, out, opts)
        assert result["ok"] is True

        content = Path(out).read_text(encoding="utf-8")
        for line in content.splitlines():
            stripped = line.strip()
            # Skip SRT structural lines (index number and timecode arrow)
            if stripped and not stripped.isdigit() and "-->" not in stripped:
                assert len(stripped) <= max_chars, (
                    f"Output line exceeds max_chars={max_chars}: {stripped!r}"
                )

    def test_english_srt_word_boundaries_no_concatenation(self, tmp_path: Path) -> None:
        """Adjacent words in the output are never concatenated without a space (T-5, AC-1 word-boundary).

        If joiner is omitted or set to '' for Latin, adjacent words would be written
        as 'quickbrown' etc. — this test guards against that regression.
        """
        wrap_captions = _import_wrap_captions()
        inp = self._make_input_en(tmp_path, "input_en_concat.srt")
        out = str(tmp_path / "output_en_concat.srt")
        opts = WrapCaptionsOptions(language="en", max_chars=15, max_lines=3)
        result: dict[str, Any] = wrap_captions(inp, out, opts)
        assert result["ok"] is True

        content = Path(out).read_text(encoding="utf-8")
        # Every adjacent word-pair from the fixture must NOT appear without a separating space.
        adjacent_no_space = [
            "Thequick",
            "quickbrown",
            "brownfox",
            "foxjumps",
            "jumpsover",
            "overthe",
            "thelazy",
            "lazydog",
        ]
        for bad_pair in adjacent_no_space:
            assert bad_pair not in content, (
                f"Word concatenation without space detected: {bad_pair!r} in output"
            )

    def test_english_srt_no_text_truncation(self, tmp_path: Path) -> None:
        """All nine words from the input fixture appear in the output (no text truncation; T-5, AC-1).

        wrap_captions must not drop any word regardless of max_chars / max_lines settings.
        """
        wrap_captions = _import_wrap_captions()
        inp = self._make_input_en(tmp_path, "input_en_trunc.srt")
        out = str(tmp_path / "output_en_trunc.srt")
        opts = WrapCaptionsOptions(language="en", max_chars=15, max_lines=3)
        result: dict[str, Any] = wrap_captions(inp, out, opts)
        assert result["ok"] is True

        content = Path(out).read_text(encoding="utf-8")
        for word in [
            "The",
            "quick",
            "brown",
            "fox",
            "jumps",
            "over",
            "the",
            "lazy",
            "dog",
        ]:
            assert word in content, (
                f"Input word {word!r} is missing from the output (text truncation detected)"
            )


# ===========================================================================
# T-6: Latin path does not call subprocess.run (budoux non-dependency; AC-4)
# ===========================================================================


class TestLatinNoSubprocess:
    """T-6: wrap_captions with language='en' must not call subprocess.run (AC-4, FR-5, NFR-2).

    The Latin in-process path (text.split()) bypasses budoux entirely. Even when
    budoux is absent from the environment, language='en' must return ok=True.
    """

    _ENGLISH_SRT = "1\n00:00:00,000 --> 00:00:03,000\nHello world\n"

    def test_latin_does_not_call_subprocess_run(
        self, tmp_path: Path, mocker: Any
    ) -> None:
        """subprocess.run is NOT called when language='en' (T-6, AC-4).

        The mock is set up to track calls; if the Latin path inadvertently
        launches a subprocess, the mock records the call and the assertion fails.
        """
        wrap_captions = _import_wrap_captions()
        inp = tmp_path / "latin.srt"
        inp.write_text(self._ENGLISH_SRT, encoding="utf-8")
        out = str(tmp_path / "latin_out.srt")

        mock_run: MagicMock = mocker.patch("clipwright_wrap.wrap.subprocess.run")

        opts = WrapCaptionsOptions(language="en", max_chars=20, max_lines=2)
        result: dict[str, Any] = wrap_captions(str(inp), out, opts)

        assert result["ok"] is True
        mock_run.assert_not_called()

    def test_latin_returns_ok_without_budoux(self, tmp_path: Path) -> None:
        """language='en' returns ok=True even when budoux subprocess is not invoked (T-6, AC-4).

        No mock for subprocess; Latin path is fully in-process and never touches budoux.
        """
        wrap_captions = _import_wrap_captions()
        inp = tmp_path / "latin2.srt"
        inp.write_text(self._ENGLISH_SRT, encoding="utf-8")
        out = str(tmp_path / "latin2_out.srt")

        opts = WrapCaptionsOptions(language="en", max_chars=20, max_lines=2)
        result: dict[str, Any] = wrap_captions(str(inp), out, opts)

        assert result["ok"] is True
        assert result["data"]["language"] == "en"


# ===========================================================================
# T-8: Long single word > max_chars recorded in overflow_width_cue_indices (AC-5)
# ===========================================================================


class TestLatinOverflow:
    """T-8: A single word exceeding max_chars is recorded in overflow_width_cue_indices (AC-5).

    Latin word-wrap must not split words mid-character. An oversized word is
    placed on its own line and flagged as width overflow, not truncated.
    """

    def test_extraordinary_overflow_recorded_not_truncated(
        self, tmp_path: Path
    ) -> None:
        """'Extraordinary' (13 chars) with max_chars=5 → overflow_width_cue_indices=[0] (T-8, AC-5).

        The word must appear in the output; no text is dropped.
        """
        wrap_captions = _import_wrap_captions()
        srt = "1\n00:00:00,000 --> 00:00:01,000\nExtraordinary\n"
        inp = tmp_path / "overflow.srt"
        inp.write_text(srt, encoding="utf-8")
        out = str(tmp_path / "overflow_out.srt")

        opts = WrapCaptionsOptions(language="en", max_chars=5, max_lines=2)
        result: dict[str, Any] = wrap_captions(str(inp), out, opts)

        assert result["ok"] is True
        data = result["data"]
        # Overflow recorded at cue index 0
        assert 0 in data["overflow_width_cue_indices"], (
            f"Expected overflow at index 0, got: {data['overflow_width_cue_indices']!r}"
        )
        # Text is not truncated
        content = Path(out).read_text(encoding="utf-8")
        assert "Extraordinary" in content


# ===========================================================================
# T-9: Multi-space normalisation and empty cue robustness
# ===========================================================================


class TestLatinSpaceNormalisation:
    """T-9: text.split() normalises multiple/leading/trailing spaces; empty cue is safe (§6.2).

    str.split() with no argument collapses consecutive whitespace to a single token
    boundary and strips leading/trailing whitespace. The output uses joiner=' ',
    so output words are always separated by a single space regardless of input spacing.
    """

    def test_multi_space_between_words_normalised_to_single(
        self, tmp_path: Path
    ) -> None:
        """Multiple spaces between words are normalised to a single space in the output (T-9)."""
        wrap_captions = _import_wrap_captions()
        # Input has double-space between words
        srt = "1\n00:00:00,000 --> 00:00:01,000\nhello  world\n"
        inp = tmp_path / "multi_space.srt"
        inp.write_text(srt, encoding="utf-8")
        out = str(tmp_path / "multi_space_out.srt")

        opts = WrapCaptionsOptions(language="en", max_chars=40, max_lines=2)
        result: dict[str, Any] = wrap_captions(str(inp), out, opts)

        assert result["ok"] is True
        content = Path(out).read_text(encoding="utf-8")
        # "hello  world" (double space) must not appear; "hello world" must appear
        assert "hello  world" not in content
        assert "hello" in content
        assert "world" in content

    def test_empty_cue_text_does_not_crash(self, tmp_path: Path) -> None:
        """A cue with empty text produces an empty output cue without exception (T-9)."""
        wrap_captions = _import_wrap_captions()
        # Two-cue SRT: first cue empty, second cue has real text
        srt = (
            "1\n00:00:00,000 --> 00:00:01,000\n\n"
            "\n"
            "2\n00:00:01,000 --> 00:00:02,000\nhello world\n"
        )
        inp = tmp_path / "empty_cue.srt"
        inp.write_text(srt, encoding="utf-8")
        out = str(tmp_path / "empty_cue_out.srt")

        opts = WrapCaptionsOptions(language="en", max_chars=40, max_lines=2)
        result: dict[str, Any] = wrap_captions(str(inp), out, opts)

        assert result["ok"] is True
        content = Path(out).read_text(encoding="utf-8")
        # The non-empty cue's text must appear
        assert "hello world" in content


# ===========================================================================
# T-10: Defensive guard — unsupported language bypassing Pydantic schema
# ===========================================================================


class TestDefensiveLanguageGuard:
    """T-10: Unsupported language passed directly to wrap.py hits INVALID_INPUT guard (AC-3).

    Pydantic rejects 'ko' at schema construction, but wrap._wrap_inner has a
    defensive guard for direct/bypass callers. This test uses model_construct()
    to bypass Pydantic validation and reach the guard.
    """

    _ENGLISH_SRT = "1\n00:00:00,000 --> 00:00:03,000\nHello world\n"

    def test_ko_direct_call_returns_invalid_input(self, tmp_path: Path) -> None:
        """language='ko' bypassing Pydantic → INVALID_INPUT from the defensive guard (T-10, AC-3)."""
        from clipwright_wrap.wrap import wrap_captions

        inp = tmp_path / "ko.srt"
        inp.write_text(self._ENGLISH_SRT, encoding="utf-8")
        out = str(tmp_path / "ko_out.srt")

        # Bypass Pydantic validation to reach the defensive guard in wrap.py
        opts = WrapCaptionsOptions.model_construct(
            language="ko", max_chars=16, max_lines=2
        )
        result: dict[str, Any] = wrap_captions(str(inp), out, opts)

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_ko_hint_lists_accepted_codes(self, tmp_path: Path) -> None:
        """Defensive guard hint enumerates the accepted language codes (T-10, AC-3)."""
        from clipwright_wrap.wrap import wrap_captions

        inp = tmp_path / "ko2.srt"
        inp.write_text(self._ENGLISH_SRT, encoding="utf-8")
        out = str(tmp_path / "ko2_out.srt")

        opts = WrapCaptionsOptions.model_construct(
            language="ko", max_chars=16, max_lines=2
        )
        result: dict[str, Any] = wrap_captions(str(inp), out, opts)

        assert result["ok"] is False
        hint = result["error"].get("hint", "")
        # Hint must list at least one CJK and one Latin code
        assert "ja" in hint
        assert "en" in hint

    def test_ko_hint_exposes_no_path(self, tmp_path: Path) -> None:
        """Defensive guard hint and message expose no file path (T-10, CWE-209)."""
        from clipwright_wrap.wrap import wrap_captions

        inp = tmp_path / "ko3.srt"
        inp.write_text(self._ENGLISH_SRT, encoding="utf-8")
        out = str(tmp_path / "ko3_out.srt")

        opts = WrapCaptionsOptions.model_construct(
            language="ko", max_chars=16, max_lines=2
        )
        result: dict[str, Any] = wrap_captions(str(inp), out, opts)

        assert result["ok"] is False
        error = result["error"]
        # tmp_path components must not appear in message or hint (path non-exposure)
        path_component = tmp_path.name
        assert path_component not in error.get("message", "")
        assert path_component not in error.get("hint", "")
