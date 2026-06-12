"""test_e2e.py — clipwright-wrap end-to-end tests.

Uses real budoux (normal dependency, no env gate, always executed) to verify
phrase-boundary line-wrapping for Japanese SRT/VTT end-to-end.

Test categories:
- e2e_1_srt / e2e_1_vtt : real budoux phrase-boundary wrapping (success condition 1/2)
- e2e_2_transcribe      : transcribe→wrap integration (DC-AM-004 primary)
- e2e_zero_srt / e2e_zero_vtt : 0-cue e2e (DC-GP-004)
- e2e_overflow          : overflow warnings (WR-AD-15/DC-AM-003)

wrap_cli now self-configures UTF-8 I/O at main() entry, so no env setup is needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clipwright_wrap.captions import parse_captions
from clipwright_wrap.schemas import WrapCaptionsOptions
from clipwright_wrap.wrap import wrap_captions

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_JA_SRT = FIXTURES_DIR / "sample_ja.srt"
SAMPLE_JA_VTT = FIXTURES_DIR / "sample_ja.vtt"

# ============================================================
# Helpers
# ============================================================


def _run_wrap(
    input_path: Path,
    output_path: Path,
    options: WrapCaptionsOptions,
) -> dict[str, Any]:
    """Call wrap_captions and return the result."""
    return wrap_captions(str(input_path), str(output_path), options)


# ============================================================
# e2e 1: real budoux phrase-boundary wrapping (SRT)
# ============================================================


def test_e2e_1_srt_ok_true(tmp_path: Path) -> None:
    """e2e1-SRT: result['ok'] is True (real budoux launched)."""
    out = tmp_path / "out.srt"
    result = _run_wrap(
        SAMPLE_JA_SRT,
        out,
        WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2),
    )
    assert result["ok"] is True, f"ok should be True, got: {result}"


def test_e2e_1_srt_newline_inserted(tmp_path: Path) -> None:
    """e2e1-SRT: newline \\n is inserted within a cue (WR-AD-14).

    The 2 cues in sample_ja.srt contain Japanese text exceeding max_chars=16.
    Confirms that at least 1 cue has a line break inserted.

    Note: parse_captions joins multiple text lines with empty string (WR-AD-14),
    so line breaks cannot be checked directly from cue.text after parsing.
    Verify via the raw output SRT content (multiple text lines after the timeline line)
    or confirm wrapped_count > 0 (wrap.py tracks line-break insertions).
    """
    out = tmp_path / "out.srt"
    result = _run_wrap(
        SAMPLE_JA_SRT,
        out,
        WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2),
    )
    # Check line-break insertion via wrap.py's wrapped_count (number of cues whose text changed)
    assert result["data"]["wrapped_count"] > 0, (
        f"At least one cue should have been wrapped (got wrapped_count=0): {result['data']}"
    )
    # Verify \n exists in the cue text portion of the raw output SRT
    raw = out.read_text(encoding="utf-8")
    # Extract SRT cue blocks and check whether any have multiple text lines
    blocks = raw.strip().split("\n\n")
    assert any(
        len(block.splitlines()) > 3  # index + timeline + 2 or more text lines
        for block in blocks
    ), "At least one cue block should have multiple text lines after wrap"


def test_e2e_1_srt_line_width_within_max_chars(tmp_path: Path) -> None:
    """e2e1-SRT: each line of non-overflow(b) cues is within max_chars (WR-AD-14).

    Verify the actual text line widths from the raw blocks of the output SRT.
    parse_captions joins multiple text lines, so validate the raw blocks directly.
    For cues not in overflow_width_cue_indices, each text line must satisfy len() <= 16.
    """
    opts = WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2)
    out = tmp_path / "out.srt"
    result = _run_wrap(SAMPLE_JA_SRT, out, opts)
    overflow_width = set(result["data"]["overflow_width_cue_indices"])

    raw = out.read_text(encoding="utf-8")
    blocks = raw.strip().split("\n\n")
    for i, block in enumerate(blocks):
        if i in overflow_width:
            continue  # width overflow due to oversized segment is permitted
        lines = block.splitlines()
        # lines[0] = index, lines[1] = timeline, lines[2:] = text lines
        text_lines = lines[2:] if len(lines) > 2 else []
        for line in text_lines:
            assert len(line) <= opts.max_chars, (
                f"cue[{i}] line {line!r} len={len(line)} > max_chars={opts.max_chars}"
            )


def test_e2e_1_srt_timecodes_unchanged(tmp_path: Path) -> None:
    """e2e1-SRT: timecodes are unchanged from the input (WR-AD-06)."""
    opts = WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2)
    out = tmp_path / "out.srt"
    _run_wrap(SAMPLE_JA_SRT, out, opts)
    original_cues = parse_captions(SAMPLE_JA_SRT.read_text(encoding="utf-8"), "srt")
    output_cues = parse_captions(out.read_text(encoding="utf-8"), "srt")
    assert len(original_cues) == len(output_cues)
    for orig, outp in zip(original_cues, output_cues, strict=True):
        assert orig.start == outp.start, (
            f"start changed: {orig.start!r} -> {outp.start!r}"
        )
        assert orig.end == outp.end, f"end changed: {orig.end!r} -> {outp.end!r}"


def test_e2e_1_srt_artifacts_is_dict(tmp_path: Path) -> None:
    """e2e1-SRT: artifacts is a list of dicts (DC-AS-005)."""
    out = tmp_path / "out.srt"
    result = _run_wrap(
        SAMPLE_JA_SRT,
        out,
        WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2),
    )
    assert isinstance(result["artifacts"], list)
    assert len(result["artifacts"]) == 1
    artifact = result["artifacts"][0]
    assert isinstance(artifact, dict), f"artifact should be dict, got {type(artifact)}"
    assert artifact["role"] == "captions"
    assert artifact["format"] == "srt"


# ============================================================
# e2e 1: real budoux phrase-boundary wrapping (VTT)
# ============================================================


def test_e2e_1_vtt_ok_true(tmp_path: Path) -> None:
    """e2e1-VTT: result['ok'] is True (real budoux launched)."""
    out = tmp_path / "out.vtt"
    result = _run_wrap(
        SAMPLE_JA_VTT,
        out,
        WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2),
    )
    assert result["ok"] is True, f"ok should be True, got: {result}"


def test_e2e_1_vtt_newline_inserted(tmp_path: Path) -> None:
    """e2e1-VTT: newline \\n is inserted within a cue (WR-AD-14).

    Confirm line-break insertion via wrapped_count > 0.
    Also verify that the raw VTT content has multiple lines in the cue text portion.
    """
    out = tmp_path / "out.vtt"
    result = _run_wrap(
        SAMPLE_JA_VTT,
        out,
        WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2),
    )
    assert result["data"]["wrapped_count"] > 0, (
        f"At least one cue should have been wrapped (VTT), got wrapped_count=0: {result['data']}"
    )
    # Verify that line breaks exist in the cue text portion of the raw VTT
    raw = out.read_text(encoding="utf-8")
    # Skip the WEBVTT header and blank lines; check cue blocks
    cue_blocks = [b for b in raw.strip().split("\n\n") if "-->" in b]
    assert any(
        len(block.splitlines()) > 2  # timeline line + 2 or more text lines
        for block in cue_blocks
    ), "At least one VTT cue should have multiple text lines after wrap"


def test_e2e_1_vtt_timecodes_unchanged(tmp_path: Path) -> None:
    """e2e1-VTT: timecodes are unchanged from the input (WR-AD-06)."""
    opts = WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2)
    out = tmp_path / "out.vtt"
    _run_wrap(SAMPLE_JA_VTT, out, opts)
    original_cues = parse_captions(SAMPLE_JA_VTT.read_text(encoding="utf-8"), "vtt")
    output_cues = parse_captions(out.read_text(encoding="utf-8"), "vtt")
    assert len(original_cues) == len(output_cues)
    for orig, outp in zip(original_cues, output_cues, strict=True):
        assert orig.start == outp.start
        assert orig.end == outp.end


def test_e2e_1_vtt_artifacts_is_dict(tmp_path: Path) -> None:
    """e2e1-VTT: artifacts is a list of dicts (DC-AS-005)."""
    out = tmp_path / "out.vtt"
    result = _run_wrap(
        SAMPLE_JA_VTT,
        out,
        WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2),
    )
    artifact = result["artifacts"][0]
    assert isinstance(artifact, dict)
    assert artifact["format"] == "vtt"


# ============================================================
# e2e 2: transcribe→wrap integration (DC-AM-004 primary)
# ============================================================
#
# Import to_srt/to_vtt from clipwright_transcribe.captions,
# generate SRT/VTT from a Japanese Segment list, then pass to wrap.
# When import succeeds, record as "transcribe path".
# (clipwright-transcribe is added as a dev dependency in setup-wrap)

try:
    from clipwright_transcribe.captions import (
        to_srt,
        to_vtt,
    )

    _TRANSCRIBE_IMPORT_OK = True
except ImportError:
    _TRANSCRIBE_IMPORT_OK = False

# Switch fixture SRT/VTT generation method depending on whether transcribe import succeeded
_TRANSCRIBE_SEGMENTS = [
    {
        "start_sec": 0.0,
        "end_sec": 2.0,
        "text": "今日はとてもいい天気なので公園に散歩に行きました。",
    },
    {
        "start_sec": 2.5,
        "end_sec": 5.0,
        "text": "桜の花びらが舞い散り、川沿いの遊歩道を歩きながら春の訪れを感じた。",
    },
]

if _TRANSCRIBE_IMPORT_OK:
    _E2E2_SRT_CONTENT: str = to_srt(_TRANSCRIBE_SEGMENTS)  # type: ignore[arg-type]
    _E2E2_VTT_CONTENT: str = to_vtt(_TRANSCRIBE_SEGMENTS)  # type: ignore[arg-type]
else:
    # Fallback: manually crafted fixture conforming to WR-AD-12 byte structure
    _E2E2_SRT_CONTENT = (
        "1\n00:00:00,000 --> 00:00:02,000\n今日はとてもいい天気なので公園に散歩に行きました。\n"
        "\n"
        "2\n00:00:02,500 --> 00:00:05,000\n桜の花びらが舞い散り、川沿いの遊歩道を歩きながら春の訪れを感じた。\n"
    )
    _E2E2_VTT_CONTENT = (
        "WEBVTT\n"
        "\n"
        "00:00:00.000 --> 00:00:02.000\n今日はとてもいい天気なので公園に散歩に行きました。\n"
        "\n"
        "00:00:02.500 --> 00:00:05.000\n桜の花びらが舞い散り、川沿いの遊歩道を歩きながら春の訪れを感じた。\n"
    )


def test_e2e_2_transcribe_srt_ok(tmp_path: Path) -> None:
    """e2e2-SRT: wrap transcribe to_srt output and get ok:True.

    When _TRANSCRIBE_IMPORT_OK is True: transcribe path (primary).
    When False: manually crafted fixture path (fallback).
    """
    # Write the transcribe output to a file
    in_srt = tmp_path / "transcribe_out.srt"
    in_srt.write_text(_E2E2_SRT_CONTENT, encoding="utf-8")

    out_srt = tmp_path / "wrapped.srt"
    result = _run_wrap(
        in_srt, out_srt, WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2)
    )
    assert result["ok"] is True, f"ok should be True, got: {result}"


def test_e2e_2_transcribe_vtt_ok(tmp_path: Path) -> None:
    """e2e2-VTT: wrap transcribe to_vtt output and get ok:True."""
    in_vtt = tmp_path / "transcribe_out.vtt"
    in_vtt.write_text(_E2E2_VTT_CONTENT, encoding="utf-8")

    out_vtt = tmp_path / "wrapped.vtt"
    result = _run_wrap(
        in_vtt, out_vtt, WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2)
    )
    assert result["ok"] is True, f"ok should be True, got: {result}"


def test_e2e_2_transcribe_srt_wrapped_output(tmp_path: Path) -> None:
    """e2e2-SRT: the wrapped output SRT has ok:True and formatted cues."""
    in_srt = tmp_path / "transcribe_out.srt"
    in_srt.write_text(_E2E2_SRT_CONTENT, encoding="utf-8")
    out_srt = tmp_path / "wrapped.srt"

    result = _run_wrap(
        in_srt, out_srt, WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2)
    )
    assert result["ok"] is True
    assert result["data"]["cue_count"] == 2
    # At least 1 cue has a line break inserted (text exceeds max_chars=16)
    # Verified via wrapped_count (parse_captions joins multiple text lines, so also check raw)
    assert result["data"]["wrapped_count"] > 0, (
        f"Expected wrapped_count > 0 for transcribe output, got: {result['data']}"
    )
    raw = out_srt.read_text(encoding="utf-8")
    blocks = [b for b in raw.strip().split("\n\n") if b.strip()]
    assert any(len(b.splitlines()) > 3 for b in blocks), (
        "Expected multi-line text in at least one cue"
    )


# ============================================================
# DC-GP-004: 0-cue e2e
# ============================================================


def test_e2e_zero_srt_ok_empty(tmp_path: Path) -> None:
    """DC-GP-004-SRT: pass 0-cue SRT (empty string) to wrap → ok:True, empty output, round-trip identical.

    transcribe's 0-cue output is to_srt='' (empty string).
    wrap outputs ok:True with SRT=''.
    """
    in_srt = tmp_path / "empty.srt"
    in_srt.write_text("", encoding="utf-8")

    out_srt = tmp_path / "out_empty.srt"
    result = _run_wrap(
        in_srt, out_srt, WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2)
    )
    assert result["ok"] is True, f"ok should be True for empty SRT, got: {result}"

    # output is empty string (round-trip identical)
    assert out_srt.read_text(encoding="utf-8") == "", (
        "Empty SRT should produce empty output"
    )

    # data has cue_count=0
    assert result["data"]["cue_count"] == 0


def test_e2e_zero_vtt_ok_header_only(tmp_path: Path) -> None:
    """DC-GP-004-VTT: pass 0-cue VTT ('WEBVTT\\n') to wrap → ok:True, 'WEBVTT\\n' output, round-trip identical.

    transcribe's 0-cue output is to_vtt='WEBVTT\\n' (header only).
    wrap outputs ok:True with VTT='WEBVTT\\n'.
    """
    in_vtt = tmp_path / "empty.vtt"
    in_vtt.write_text("WEBVTT\n", encoding="utf-8")

    out_vtt = tmp_path / "out_empty.vtt"
    result = _run_wrap(
        in_vtt, out_vtt, WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2)
    )
    assert result["ok"] is True, f"ok should be True for empty VTT, got: {result}"

    # output is 'WEBVTT\n' (round-trip identical)
    assert out_vtt.read_text(encoding="utf-8") == "WEBVTT\n", (
        "Empty VTT should produce 'WEBVTT\\n' output"
    )

    assert result["data"]["cue_count"] == 0


# ============================================================
# spike verification: budoux API spec (cross-check with fixtures/README.md)
# ============================================================


def test_spike_budoux_parser_load_api() -> None:
    """spike verification: load_default_japanese_parser() is callable (README §2 confirmed)."""
    import budoux

    assert hasattr(budoux, "load_default_japanese_parser"), (
        "budoux.load_default_japanese_parser should exist"
    )
    assert not hasattr(budoux, "load_parser"), (
        "budoux.load_parser should NOT exist (confirmed by spike)"
    )


def test_spike_budoux_parse_returns_list_str() -> None:
    """spike verification: parse() -> list[str] (README §3 confirmed)."""
    import budoux

    parser = budoux.load_default_japanese_parser()
    result = parser.parse("今日はいい天気です。")
    assert isinstance(result, list)
    assert all(isinstance(t, str) for t in result)
    # joining tokens reconstructs the original text (no delimiter)
    assert "".join(result) == "今日はいい天気です。"


def test_spike_budoux_parse_sample_ja() -> None:
    """spike verification: check that sample phrase splitting matches fixtures/README.md §3.

    README sample: parse("今日はとてもいい天気なので公園に散歩に行きました。")
    → ["今日は", "とても", "いい", "天気なので", "公園に", "散歩に", "行きました。"]
    """
    import budoux

    parser = budoux.load_default_japanese_parser()
    result = parser.parse("今日はとてもいい天気なので公園に散歩に行きました。")
    expected = [
        "今日は",
        "とても",
        "いい",
        "天気なので",
        "公園に",
        "散歩に",
        "行きました。",
    ]
    assert result == expected, f"budoux parse mismatch: {result} != {expected}"


def test_spike_budoux_all_languages_loadable() -> None:
    """spike verification: all 4 languages load successfully (README §4 confirmed)."""
    import budoux

    loaders = {
        "ja": budoux.load_default_japanese_parser,
        "zh-hans": budoux.load_default_simplified_chinese_parser,
        "zh-hant": budoux.load_default_traditional_chinese_parser,
        "th": budoux.load_default_thai_parser,
    }
    for lang, loader in loaders.items():
        parser = loader()
        assert parser is not None, f"Failed to load parser for language: {lang}"


def test_spike_budoux_parser_loaders_dict() -> None:
    """spike verification: wrap_cli._PARSER_LOADERS contains all 4 languages (README §2)."""
    from clipwright_wrap.wrap_cli import _PARSER_LOADERS

    assert set(_PARSER_LOADERS.keys()) == {"ja", "zh-hans", "zh-hant", "th"}


# ============================================================
# WR-AD-15/DC-AM-003: overflow warnings
# ============================================================


def test_e2e_overflow_line_count_warning(tmp_path: Path) -> None:
    """WR-AD-15(1)(a): max_chars=4 causes line-count overflow → single aggregated warning in warnings, overflow_cue_indices recorded.

    Wrapping '今日はとてもいい天気なので' with max_chars=4 yields
    segments ['今日は', 'とても', 'いい', '天気なので'] → 4 lines (> max_lines=2).
    """
    # Force overflow with max_chars=4
    text = "今日はとてもいい天気なので"
    in_srt = tmp_path / "overflow.srt"
    in_srt.write_text(
        f"1\n00:00:00,000 --> 00:00:02,000\n{text}\n",
        encoding="utf-8",
    )
    out_srt = tmp_path / "out_overflow.srt"
    opts = WrapCaptionsOptions(language="ja", max_chars=4, max_lines=2)
    result = _run_wrap(in_srt, out_srt, opts)

    assert result["ok"] is True

    # cue[0] is recorded in overflow_cue_indices
    assert 0 in result["data"]["overflow_cue_indices"], (
        f"Expected cue[0] in overflow_cue_indices: {result['data']['overflow_cue_indices']}"
    )

    # warnings contains a single aggregated sentence
    warnings = result.get("warnings", [])
    assert any("max_lines" in w for w in warnings), (
        f"Expected max_lines warning, got: {warnings}"
    )

    # no truncation (text is preserved)
    content = out_srt.read_text(encoding="utf-8")
    assert text in content.replace("\n", ""), (
        "Original text should be preserved (no truncation)"
    )


def test_e2e_overflow_line_width_warning(tmp_path: Path) -> None:
    """WR-AD-15(1)(b): single oversized segment (line-width overflow) → single aggregated warning in warnings, overflow_width_cue_indices recorded.

    '天気なので' (5 chars) is a single segment longer than max_chars=4 → line-width overflow.
    """
    text = "今日はとてもいい天気なので"
    in_srt = tmp_path / "overflow_width.srt"
    in_srt.write_text(
        f"1\n00:00:00,000 --> 00:00:02,000\n{text}\n",
        encoding="utf-8",
    )
    out_srt = tmp_path / "out_overflow_width.srt"
    opts = WrapCaptionsOptions(language="ja", max_chars=4, max_lines=2)
    result = _run_wrap(in_srt, out_srt, opts)

    assert result["ok"] is True

    # cue[0] is recorded in overflow_width_cue_indices
    assert 0 in result["data"]["overflow_width_cue_indices"], (
        f"Expected cue[0] in overflow_width_cue_indices: {result['data']['overflow_width_cue_indices']}"
    )

    # warnings contains a single aggregated sentence about max_chars
    warnings = result.get("warnings", [])
    assert any("max_chars" in w for w in warnings), (
        f"Expected max_chars warning, got: {warnings}"
    )


def test_e2e_overflow_no_truncation(tmp_path: Path) -> None:
    """WR-AD-15(1): no truncation even on overflow (avoid information loss; WR-AD-04).

    All segments of the original text are preserved in the output even on line-count/line-width overflow.
    """
    text = "今日はとてもいい天気なので"
    in_srt = tmp_path / "no_truncation.srt"
    in_srt.write_text(
        f"1\n00:00:00,000 --> 00:00:02,000\n{text}\n",
        encoding="utf-8",
    )
    out_srt = tmp_path / "out_no_truncation.srt"
    opts = WrapCaptionsOptions(language="ja", max_chars=4, max_lines=2)
    _run_wrap(in_srt, out_srt, opts)

    content = out_srt.read_text(encoding="utf-8")
    # rejoining the output text matches the original
    cues = parse_captions(content, "srt")
    assert len(cues) == 1
    rejoined = cues[0].text.replace("\n", "")
    assert rejoined == text, f"Expected '{text}', got '{rejoined}'"
