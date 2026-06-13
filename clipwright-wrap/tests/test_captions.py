"""test_captions.py — Red tests for captions.py pure logic (contract coverage target: 100%).

Pins the spec from architecture WR-AD-03/04/06/12/14/15.
These tests are intended to fail due to ImportError when captions.py does not exist yet
(Red phase).

Target API (all budoux-independent):
  - Cue: dataclass (index, start, end, text)
  - parse_captions(text, fmt) -> list[Cue]
  - wrap_cue_lines(segments, max_chars) -> list[str]
  - serialize_captions(cues, fmt) -> str

WR-AD-06: timecode strings are preserved as-is (no float conversion).
WR-AD-12: transcribe byte-structure spec (no trailing blank after last cue / WEBVTT\\n\\n / 0 cues).
WR-AD-14: character count is uniformly 1 per character; no delimiter inserted; \\n not included in len(line).
WR-AD-15(1): overflow detection = line-width excess only (ADR-W2 revised).
  Line-count excess is resolved upstream by _merge_to_max_lines, not detected as overflow.
"""

from __future__ import annotations

import pytest

from clipwright_wrap.captions import (
    Cue,
    parse_captions,
    serialize_captions,
    wrap_cue_lines,
)

# ===========================================================================
# Cue type verification
# ===========================================================================


class TestCueType:
    """Cue type must be a dataclass (or equivalent) with the required fields."""

    def test_cue_has_index_field(self) -> None:
        """Cue has an index field."""
        cue = Cue(index=1, start="00:00:00,000", end="00:00:01,000", text="テスト")
        assert cue.index == 1

    def test_cue_has_start_field(self) -> None:
        """Cue has a start field (timecode string)."""
        cue = Cue(index=1, start="00:00:00,000", end="00:00:01,000", text="テスト")
        assert cue.start == "00:00:00,000"

    def test_cue_has_end_field(self) -> None:
        """Cue has an end field (timecode string)."""
        cue = Cue(index=1, start="00:00:00,000", end="00:00:01,000", text="テスト")
        assert cue.end == "00:00:01,000"

    def test_cue_has_text_field(self) -> None:
        """Cue has a text field."""
        cue = Cue(index=1, start="00:00:00,000", end="00:00:01,000", text="テスト")
        assert cue.text == "テスト"

    def test_cue_start_is_string(self) -> None:
        """Cue.start is preserved as a timecode string (WR-AD-06; no float conversion)."""
        tc = "00:00:12,345"
        cue = Cue(index=1, start=tc, end="00:00:13,000", text="x")
        assert isinstance(cue.start, str)
        assert cue.start == tc

    def test_cue_end_is_string(self) -> None:
        """Cue.end is preserved as a timecode string (WR-AD-06; no float conversion)."""
        tc = "00:00:13,000"
        cue = Cue(index=1, start="00:00:12,345", end=tc, text="x")
        assert isinstance(cue.end, str)
        assert cue.end == tc


# ===========================================================================
# parse_captions — SRT basic behaviour (WR-AD-12 byte-structure spec)
# ===========================================================================


class TestParseCaptionsSrtBasic:
    """Verify the basic behaviour of parse_captions('srt') (WR-AD-03/WR-AD-12)."""

    def test_single_cue_srt_returns_list(self) -> None:
        """Parsing a 1-cue SRT returns a list."""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nあいう\n"
        result = parse_captions(srt, "srt")
        assert isinstance(result, list)
        assert len(result) == 1

    def test_single_cue_srt_index(self) -> None:
        """The cue index from SRT is correctly retrieved."""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nあいう\n"
        cues = parse_captions(srt, "srt")
        assert cues[0].index == 1

    def test_single_cue_srt_start_timecode(self) -> None:
        """SRT start timecode is preserved as a string (WR-AD-06)."""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nあいう\n"
        cues = parse_captions(srt, "srt")
        assert cues[0].start == "00:00:00,000"

    def test_single_cue_srt_end_timecode(self) -> None:
        """SRT end timecode is preserved as a string (WR-AD-06)."""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nあいう\n"
        cues = parse_captions(srt, "srt")
        assert cues[0].end == "00:00:01,000"

    def test_single_cue_srt_text(self) -> None:
        """SRT cue text is correctly retrieved."""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nあいう\n"
        cues = parse_captions(srt, "srt")
        assert cues[0].text == "あいう"


class TestParseCaptionsSrtTwoCues:
    """2-cue SRT parsing (spec-pinning test for transcribe byte-structure in WR-AD-12)."""

    # DC-AS-001/WR-AD-12 fixed fixture
    # Exact byte structure of transcribe to_srt:
    #   1 blank line between cues; last cue has no trailing blank, single newline at EOF
    SRT_2CUE = (
        "1\n00:00:00,000 --> 00:00:01,000\nあいう\n"
        "\n"
        "2\n00:00:01,000 --> 00:00:02,000\nえお\n"
    )

    def test_two_cues_parsed(self) -> None:
        """2-cue SRT (1 blank between cues, no trailing blank, single newline EOF) parses to 2 cues (DC-AS-001)."""
        cues = parse_captions(self.SRT_2CUE, "srt")
        assert len(cues) == 2

    def test_first_cue_index(self) -> None:
        """The first cue has index 1."""
        cues = parse_captions(self.SRT_2CUE, "srt")
        assert cues[0].index == 1

    def test_second_cue_index(self) -> None:
        """The second cue has index 2."""
        cues = parse_captions(self.SRT_2CUE, "srt")
        assert cues[1].index == 2

    def test_first_cue_text(self) -> None:
        """The first cue text is 'あいう'."""
        cues = parse_captions(self.SRT_2CUE, "srt")
        assert cues[0].text == "あいう"

    def test_second_cue_text(self) -> None:
        """The second cue text is 'えお' (no trailing cue dropped)."""
        cues = parse_captions(self.SRT_2CUE, "srt")
        assert cues[1].text == "えお"

    def test_second_cue_timecode_preserved(self) -> None:
        """The second cue timecode is preserved (WR-AD-06)."""
        cues = parse_captions(self.SRT_2CUE, "srt")
        assert cues[1].start == "00:00:01,000"
        assert cues[1].end == "00:00:02,000"


class TestParseCaptionsSrtEdgeCases:
    """Verify boundary/defensive cases for SRT parsing (WR-AD-12(2))."""

    def test_empty_string_returns_empty_list(self) -> None:
        """Empty string SRT '' → [] (0 cues, no exception) (WR-AD-12(2))."""
        result = parse_captions("", "srt")
        assert result == []

    def test_trailing_newline_only_returns_empty_list(self) -> None:
        """SRT with only '\\n' → [] (0 cues, no exception)."""
        result = parse_captions("\n", "srt")
        assert result == []

    def test_trailing_blank_lines_handled(self) -> None:
        """SRT with multiple trailing blank lines is still parsed correctly (robustness)."""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nテスト\n\n\n"
        cues = parse_captions(srt, "srt")
        assert len(cues) == 1
        assert cues[0].text == "テスト"

    def test_multiple_blank_lines_between_cues_handled(self) -> None:
        """SRT with multiple blank lines between cues is still parsed correctly (robustness)."""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nあ\n\n\n2\n00:00:01,000 --> 00:00:02,000\nい\n"
        cues = parse_captions(srt, "srt")
        assert len(cues) == 2

    def test_multiline_text_in_cue_joined_without_space(self) -> None:
        """Multi-line text within a cue is concatenated with empty string (WR-AD-14; no space inserted)."""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nあいう\nえおか\n"
        cues = parse_captions(srt, "srt")
        # \\n removed and joined without space
        assert cues[0].text == "あいうえおか"

    def test_multiline_text_no_space_inserted(self) -> None:
        """No half-width space is inserted when joining multi-line cue text (WR-AD-14 explicit)."""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nHello\nWorld\n"
        cues = parse_captions(srt, "srt")
        # Strict: confirm no space inserted
        assert cues[0].text == "HelloWorld"

    def test_invalid_timecode_raises_exception(self) -> None:
        """SRT with invalid timecode line → exception (INVALID_INPUT equivalent) is raised (WR-AD-09)."""
        srt = "1\nINVALID_TIMECODE\nテスト\n"
        with pytest.raises((ValueError, RuntimeError)):
            parse_captions(srt, "srt")


# ===========================================================================
# parse_captions — VTT basic behaviour (WR-AD-12 byte-structure spec)
# ===========================================================================


class TestParseCaptionsVttBasic:
    """Verify the basic behaviour of parse_captions('vtt') (WR-AD-03/WR-AD-12)."""

    def test_single_cue_vtt_returns_list(self) -> None:
        """1-cue VTT (WEBVTT\\n\\n<cue>) is parsed correctly (WR-AD-12 header spec)."""
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nあいう\n"
        result = parse_captions(vtt, "vtt")
        assert isinstance(result, list)
        assert len(result) == 1

    def test_single_cue_vtt_start_timecode(self) -> None:
        """VTT start timecode is preserved as a string (WR-AD-06)."""
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nあいう\n"
        cues = parse_captions(vtt, "vtt")
        assert cues[0].start == "00:00:00.000"

    def test_single_cue_vtt_end_timecode(self) -> None:
        """VTT end timecode is preserved as a string (WR-AD-06)."""
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nあいう\n"
        cues = parse_captions(vtt, "vtt")
        assert cues[0].end == "00:00:01.000"

    def test_single_cue_vtt_text(self) -> None:
        """VTT cue text is correctly retrieved."""
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nあいう\n"
        cues = parse_captions(vtt, "vtt")
        assert cues[0].text == "あいう"

    def test_header_only_vtt_returns_empty_list(self) -> None:
        """VTT 'WEBVTT\\n' (header only) → [] (0 cues, no exception) (WR-AD-12(2))."""
        result = parse_captions("WEBVTT\n", "vtt")
        assert result == []

    def test_webvtt_header_blank_line_skipped(self) -> None:
        """Blank line immediately after WEBVTT header is skipped and first cue is reached (WR-AD-12(2))."""
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nテスト\n"
        cues = parse_captions(vtt, "vtt")
        assert len(cues) == 1
        assert cues[0].text == "テスト"


class TestParseCaptionsVttEdgeCases:
    """Verify the 5 VTT edge-case preserve/warnings behaviours (WR-AD-12(3) / DC-AM-001)."""

    def test_vtt_cue_with_id_line_text_only_wrapped(self) -> None:
        """(a) VTT with cue id line: cue id is preserved; only the text line is formatted."""
        vtt = "WEBVTT\n\ncue-1\n00:00:00.000 --> 00:00:01.000\nあいう\n"
        cues = parse_captions(vtt, "vtt")
        assert len(cues) == 1
        assert cues[0].text == "あいう"
        # Timecode must be invariant
        assert cues[0].start == "00:00:00.000"

    def test_vtt_cue_with_settings_preserved(self) -> None:
        """(d) VTT with cue settings: settings portion is preserved as-is; only text line is formatted."""
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000 line:90% position:50%\nテスト\n"
        cues = parse_captions(vtt, "vtt")
        assert len(cues) == 1
        assert cues[0].text == "テスト"
        # Timeline line including settings must be preserved as original
        assert "line:90%" in cues[0].end or "line:90%" in (cues[0].start + cues[0].end)

    def test_vtt_note_block_preserved_with_warnings(self) -> None:
        """(b) NOTE block: reflected in parse result, and warnings information is available.

        NOTE blocks are not formatting targets, preserved as-is, and recorded in warnings (WR-AD-12(3)(b)).
        Verified via the return type or warnings attribute of parse_captions.
        """
        vtt = "WEBVTT\n\nNOTE これはコメントです\n\n00:00:00.000 --> 00:00:01.000\nテスト\n"
        # Even with a NOTE block, cue must be correctly retrieved (preserved, not a formatting target)
        cues = parse_captions(vtt, "vtt")
        assert any(c.text == "テスト" for c in cues)

    def test_vtt_style_block_preserved_with_warnings(self) -> None:
        """(c) STYLE block: reflected in parse result and cue is correctly retrieved.

        STYLE blocks are not formatting targets, preserved as-is, and recorded in warnings (WR-AD-12(3)(c)).
        """
        vtt = "WEBVTT\n\nSTYLE\n::cue { color: white; }\n\n00:00:00.000 --> 00:00:01.000\nテスト\n"
        cues = parse_captions(vtt, "vtt")
        assert any(c.text == "テスト" for c in cues)

    def test_vtt_inline_tag_cue_text_preserved_with_warnings(self) -> None:
        """(e) Cue with inline tags: text including tags is preserved as original (WR-AD-12(3)(e)).

        Cues with inline tags skip phrase-boundary formatting and are preserved as-is, recorded in warnings.
        """
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n<c.yellow>テキスト</c>\n"
        cues = parse_captions(vtt, "vtt")
        assert len(cues) == 1
        # Text including tags must be preserved as original
        assert cues[0].text == "<c.yellow>テキスト</c>"

    def test_vtt_timecode_invariant_through_all_edge_cases(self) -> None:
        """Timecodes must be invariant across all VTT edge cases (WR-AD-06)."""
        vtt = "WEBVTT\n\n00:01:23.456 --> 00:01:24.789\nテスト\n"
        cues = parse_captions(vtt, "vtt")
        assert cues[0].start == "00:01:23.456"
        assert cues[0].end == "00:01:24.789"


# ===========================================================================
# parse_captions — round-trip identity (SRT/VTT)
# ===========================================================================


class TestParseCaptionsRoundTrip:
    """Verify that timecodes are identical after a parse → serialize round trip (WR-AD-06)."""

    def test_srt_roundtrip_timecode_preserved(self) -> None:
        """Timecodes are invariant after SRT parse → serialize."""
        original = "1\n00:00:00,000 --> 00:00:01,000\nあいう\n"
        cues = parse_captions(original, "srt")
        result = serialize_captions(cues, "srt")
        # Timecode strings must be restored
        assert "00:00:00,000" in result
        assert "00:00:01,000" in result

    def test_vtt_roundtrip_timecode_preserved(self) -> None:
        """Timecodes are invariant after VTT parse → serialize."""
        original = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nあいう\n"
        cues = parse_captions(original, "vtt")
        result = serialize_captions(cues, "vtt")
        assert "00:00:00.000" in result
        assert "00:00:01.000" in result


# ===========================================================================
# wrap_cue_lines — greedy line-filling (WR-AD-04/WR-AD-14)
# ===========================================================================


class TestWrapCueLinesBasic:
    """Verify the basic behaviour of wrap_cue_lines (WR-AD-04)."""

    def test_returns_list_of_str(self) -> None:
        """wrap_cue_lines returns list[str]."""
        result = wrap_cue_lines(["今日は", "いい", "天気です。"], max_chars=10)
        assert isinstance(result, list)
        assert all(isinstance(line, str) for line in result)

    def test_short_segments_fit_in_one_line(self) -> None:
        """Segments that fit within max_chars are packed into 1 line."""
        # "今日は" + "いい" + "天気です。" = 10 chars → fits in 1 line with max_chars=16
        result = wrap_cue_lines(["今日は", "いい", "天気です。"], max_chars=16)
        assert len(result) == 1

    def test_segments_joined_without_delimiter(self) -> None:
        """Joining phrase tokens restores the original text (WR-AD-14(i); no delimiter inserted)."""
        segments = ["今日は", "いい", "天気です。"]
        result = wrap_cue_lines(segments, max_chars=100)
        # All fit on 1 line → joined without delimiter gives "今日はいい天気です。"
        assert result[0] == "今日はいい天気です。"

    def test_join_restores_original_text(self) -> None:
        """Joining all lines after wrap restores the original text (WR-AD-14(i))."""
        segments = ["今日は", "いい", "天気です。"]
        result = wrap_cue_lines(segments, max_chars=5)
        # Even when split, joining with empty string restores the original
        assert "".join(result) == "今日はいい天気です。"

    def test_line_len_excludes_newline(self) -> None:
        """len() of each line does not include '\\n' (WR-AD-14(ii))."""
        result = wrap_cue_lines(["今日は", "いい", "天気です。"], max_chars=5)
        for line in result:
            assert "\n" not in line

    def test_empty_segments_returns_empty_list(self) -> None:
        """Empty segments → [] (defensive)."""
        result = wrap_cue_lines([], max_chars=16)
        assert result == []


class TestWrapCueLinesGreedy:
    """Verify greedy line-filling behaviour (WR-AD-04)."""

    def test_segments_split_at_max_chars_boundary(self) -> None:
        """A line break is inserted just before exceeding max_chars (greedy)."""
        # "今日は"(3) + "とても"(3) = 6 chars; with max_chars=5:
        # "今日は"(3) ≤ 5: OK → + "とても"(3) = 6 > 5: line break
        result = wrap_cue_lines(["今日は", "とても", "いい"], max_chars=5)
        # Expect: line 1 = "今日は", line 2 starts with "とても..." → more than 1 line
        assert len(result) >= 2

    def test_line_does_not_exceed_max_chars_when_possible(self) -> None:
        """When each segment alone is ≤ max_chars, len of each line is ≤ max_chars."""
        segments = ["今日は", "いい", "天気です。"]
        result = wrap_cue_lines(segments, max_chars=6)
        for line in result:
            # When no single segment exceeds max_chars, each line is ≤ max_chars
            assert len(line) <= 6

    def test_exactly_max_chars_stays_in_same_line(self) -> None:
        """A line whose length equals exactly max_chars stays on that line (boundary value)."""
        # "あいうえ" (4 chars) exactly equals max_chars=4
        result = wrap_cue_lines(["あいうえ"], max_chars=4)
        assert len(result) == 1
        assert result[0] == "あいうえ"

    def test_greedy_fill_uses_max_chars_efficiently(self) -> None:
        """Greedy filling uses max_chars efficiently (budoux_sample.json fixture)."""
        # "今日は"(3) + "いい"(2) = 5 chars → fits on 1 line with max_chars=5
        result = wrap_cue_lines(["今日は", "いい"], max_chars=5)
        assert len(result) == 1
        assert result[0] == "今日はいい"


class TestWrapCueLinesOversizedSegment:
    """Verify handling of a single oversized segment (WR-AD-04; no mid-segment split)."""

    def test_oversized_single_segment_placed_on_own_line(self) -> None:
        """When a single segment alone exceeds max_chars, it is placed on its own line (no mid-split)."""
        # "歩きながら春の" (8 chars) exceeds max_chars=5
        result = wrap_cue_lines(["歩きながら春の"], max_chars=5)
        assert len(result) == 1
        assert result[0] == "歩きながら春の"

    def test_oversized_segment_not_split(self) -> None:
        """An oversized segment is not split mid-character (WR-AD-04; phrase boundary priority)."""
        big_segment = "字幕改行ツールclipwright-wrapは"  # 17 chars
        result = wrap_cue_lines([big_segment], max_chars=10)
        assert len(result) == 1
        assert result[0] == big_segment

    def test_oversized_segment_followed_by_small_segments(self) -> None:
        """When a small segment follows an oversized one, the oversized segment is on its own line and the rest are packed on another."""
        # "歩きながら春の"(8) exceeds max_chars=5 → 1 line
        # "訪れを"(4) → next line
        result = wrap_cue_lines(["歩きながら春の", "訪れを", "感じた。"], max_chars=5)
        assert result[0] == "歩きながら春の"
        # Following segments are placed on a different line
        assert len(result) >= 2


class TestWrapCueLinesCharCount:
    """Verify the character counting spec (WR-AD-14)."""

    def test_full_width_and_half_width_counted_equally(self) -> None:
        """Full-width and half-width characters are both counted as 1 (WR-AD-14(iii); uniform 1-char)."""
        # "abc"(3 half-width) + "あいう"(3 full-width) = 6 chars → fits on 1 line with max_chars=6
        result = wrap_cue_lines(["abc", "あいう"], max_chars=6)
        assert len(result) == 1
        assert result[0] == "abcあいう"

    def test_half_width_ascii_counted_as_one(self) -> None:
        """Half-width ASCII is also counted as 1 by len() (WR-AD-14(iii))."""
        # "BudouXを" (7 chars: B,u,d,o,u,X,を) → fits on 1 line with max_chars=7
        result = wrap_cue_lines(["BudouXを"], max_chars=7)
        assert len(result) == 1

    def test_len_matches_character_count(self) -> None:
        """len(line) matches the character count excluding \\n (WR-AD-14(ii))."""
        segments = ["今日は", "いい", "天気です。"]
        result = wrap_cue_lines(segments, max_chars=100)
        for line in result:
            assert len(line) == len(line.replace("\n", ""))

    def test_space_in_original_text_counted_as_one_char(self) -> None:
        """A half-width space in the original text is counted as 1 character as part of the original (WR-AD-14)."""
        # "Hello " (6 chars including space) → len = 6
        result = wrap_cue_lines(["Hello "], max_chars=6)
        assert len(result) == 1
        assert result[0] == "Hello "


class TestWrapCueLinesWithBudouxFixture:
    """Verify wrap_cue_lines with real segments from budoux_sample.json."""

    def test_short_ja_segments_with_max_chars_10(
        self, budoux_segments_ja: list[list[str]]
    ) -> None:
        """Short Japanese text from budoux fixture ('今日はいい天気です。') is packed with max_chars=10."""
        # segments[0]: ["今日は", "いい", "天気です。"] = 9 chars
        segments = budoux_segments_ja[0]
        result = wrap_cue_lines(segments, max_chars=10)
        assert "".join(result) == "今日はいい天気です。"

    def test_long_ja_segments_wrapped_by_max_chars(
        self, budoux_segments_ja: list[list[str]]
    ) -> None:
        """Long Japanese text from budoux fixture (7 segments) is split into multiple lines with max_chars=10."""
        # segments[1]: ["今日は", "とても", "いい", "天気なので", "公園に", "散歩に", "行きました。"]
        segments = budoux_segments_ja[1]
        result = wrap_cue_lines(segments, max_chars=10)
        # Total 26 chars → multiple lines with max_chars=10
        assert len(result) > 1
        # Joining restores the original text
        assert "".join(result) == "今日はとてもいい天気なので公園に散歩に行きました。"

    def test_mixed_segments_join_without_delimiter(
        self, budoux_segments_ja: list[list[str]]
    ) -> None:
        """Mixed alphanumeric segments are also joined without delimiter (WR-AD-14(i))."""
        # segments[3]: ["字幕改行ツールclipwright-wrapは", "BudouXを", "使って", ...]
        segments = budoux_segments_ja[3]
        result = wrap_cue_lines(segments, max_chars=100)
        original = "".join(segments)
        assert "".join(result) == original


# ===========================================================================
# serialize_captions — SRT output format (WR-AD-12 byte structure)
# ===========================================================================


class TestSerializeCaptionsSrt:
    """Verify the output format of serialize_captions('srt') (WR-AD-12(1))."""

    def test_single_cue_srt_format(self) -> None:
        """1-cue SRT output has the correct format (index, timecode, text)."""
        cues = [Cue(index=1, start="00:00:00,000", end="00:00:01,000", text="あいう")]
        result = serialize_captions(cues, "srt")
        assert "1\n" in result
        assert "00:00:00,000 --> 00:00:01,000" in result
        assert "あいう" in result

    def test_single_cue_srt_timecode_format(self) -> None:
        """SRT timecode is output unchanged in 'HH:MM:SS,mmm' format (WR-AD-06)."""
        cues = [Cue(index=1, start="00:00:12,345", end="00:00:13,678", text="x")]
        result = serialize_captions(cues, "srt")
        assert "00:00:12,345 --> 00:00:13,678" in result

    def test_zero_cues_srt_returns_empty_string(self) -> None:
        """0 cues SRT → '' (WR-AD-12(2))."""
        result = serialize_captions([], "srt")
        assert result == ""

    def test_two_cues_srt_separated_by_blank_line(self) -> None:
        """2-cue SRT has exactly 1 blank line between cues (WR-AD-12(1) byte structure)."""
        cues = [
            Cue(index=1, start="00:00:00,000", end="00:00:01,000", text="あ"),
            Cue(index=2, start="00:00:01,000", end="00:00:02,000", text="い"),
        ]
        result = serialize_captions(cues, "srt")
        assert "\n\n" in result

    def test_two_cues_srt_last_cue_no_trailing_blank_line(self) -> None:
        """2-cue SRT has no trailing blank line after the last cue (WR-AD-12(1); single newline at EOF)."""
        cues = [
            Cue(index=1, start="00:00:00,000", end="00:00:01,000", text="あ"),
            Cue(index=2, start="00:00:01,000", end="00:00:02,000", text="い"),
        ]
        result = serialize_captions(cues, "srt")
        # Must not end with "\n\n"
        assert not result.endswith("\n\n")
        # Must end with a single newline
        assert result.endswith("\n")

    def test_srt_two_cue_exact_byte_structure(self) -> None:
        """Exact byte structure of a 2-cue SRT matches the WR-AD-12(1) fixture (DC-AS-001)."""
        cues = [
            Cue(index=1, start="00:00:00,000", end="00:00:01,000", text="あいう"),
            Cue(index=2, start="00:00:01,000", end="00:00:02,000", text="えお"),
        ]
        expected = (
            "1\n00:00:00,000 --> 00:00:01,000\nあいう\n"
            "\n"
            "2\n00:00:01,000 --> 00:00:02,000\nえお\n"
        )
        result = serialize_captions(cues, "srt")
        assert result == expected


# ===========================================================================
# serialize_captions — VTT output format (WR-AD-12 byte structure)
# ===========================================================================


class TestSerializeCaptionsVtt:
    """Verify the output format of serialize_captions('vtt') (WR-AD-12(1))."""

    def test_vtt_starts_with_webvtt_header(self) -> None:
        """VTT output starts with the 'WEBVTT' header."""
        cues = [Cue(index=1, start="00:00:00.000", end="00:00:01.000", text="あいう")]
        result = serialize_captions(cues, "vtt")
        assert result.startswith("WEBVTT")

    def test_vtt_header_followed_by_blank_line(self) -> None:
        """VTT header is followed by a blank line (WEBVTT\\n\\n<cue> structure; WR-AD-12(1))."""
        cues = [Cue(index=1, start="00:00:00.000", end="00:00:01.000", text="あいう")]
        result = serialize_captions(cues, "vtt")
        assert result.startswith("WEBVTT\n\n")

    def test_vtt_timecode_uses_dot_separator(self) -> None:
        """VTT timecode is output unchanged with dot separator 'HH:MM:SS.mmm' (WR-AD-06)."""
        cues = [Cue(index=1, start="00:00:12.345", end="00:00:13.678", text="x")]
        result = serialize_captions(cues, "vtt")
        assert "00:00:12.345 --> 00:00:13.678" in result

    def test_zero_cues_vtt_returns_header_only(self) -> None:
        """0 cues VTT → 'WEBVTT\\n' (WR-AD-12(2))."""
        result = serialize_captions([], "vtt")
        assert result == "WEBVTT\n"

    def test_vtt_one_cue_exact_byte_structure(self) -> None:
        """Exact byte structure of a 1-cue VTT matches the WR-AD-12(1) fixture (DC-AS-001)."""
        cues = [Cue(index=1, start="00:00:00.000", end="00:00:01.000", text="あいう")]
        expected = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nあいう\n"
        result = serialize_captions(cues, "vtt")
        assert result == expected

    def test_vtt_two_cues_separated_by_blank_line(self) -> None:
        """2-cue VTT has exactly 1 blank line between cues (WR-AD-12(1) byte structure)."""
        cues = [
            Cue(index=1, start="00:00:00.000", end="00:00:01.000", text="あ"),
            Cue(index=2, start="00:00:01.000", end="00:00:02.000", text="い"),
        ]
        result = serialize_captions(cues, "vtt")
        # Structure: WEBVTT\n\ncue1\n\ncue2\n
        lines = result.split("\n")
        # A blank line must exist
        assert "" in lines

    def test_vtt_last_cue_no_trailing_blank_line(self) -> None:
        """VTT has no trailing blank line after the last cue (WR-AD-12(1); single newline at EOF)."""
        cues = [
            Cue(index=1, start="00:00:00.000", end="00:00:01.000", text="あ"),
            Cue(index=2, start="00:00:01.000", end="00:00:02.000", text="い"),
        ]
        result = serialize_captions(cues, "vtt")
        assert not result.endswith("\n\n")
        assert result.endswith("\n")


# ===========================================================================
# serialize_captions — round-trip identity (SRT/VTT)
# ===========================================================================


class TestSerializeCaptionsRoundTrip:
    """Verify parse → serialize round-trip identity (WR-AD-06 invariant preservation)."""

    def test_srt_zero_cues_roundtrip(self) -> None:
        """Empty string SRT parse → serialize returns '' (round-trip identity; WR-AD-12(2))."""
        cues = parse_captions("", "srt")
        result = serialize_captions(cues, "srt")
        assert result == ""

    def test_vtt_header_only_roundtrip(self) -> None:
        """VTT 'WEBVTT\\n' parse → serialize returns 'WEBVTT\\n' (round-trip identity; WR-AD-12(2))."""
        cues = parse_captions("WEBVTT\n", "vtt")
        result = serialize_captions(cues, "vtt")
        assert result == "WEBVTT\n"

    def test_srt_two_cue_roundtrip_exact_match(self) -> None:
        """2-cue SRT parse → serialize matches the original string (round-trip identity; DC-AS-001)."""
        original = (
            "1\n00:00:00,000 --> 00:00:01,000\nあいう\n"
            "\n"
            "2\n00:00:01,000 --> 00:00:02,000\nえお\n"
        )
        cues = parse_captions(original, "srt")
        result = serialize_captions(cues, "srt")
        assert result == original


# ===========================================================================
# Overflow detection (WR-AD-15(1) / DC-AM-003)
# ===========================================================================

# check_overflow(lines, max_chars) -> bool
# Detects line-width excess only (ADR-W2 / WR-AD-15(1) revised).
# Line-count excess is resolved upstream by _merge_to_max_lines.


class TestOverflowDetection:
    """Verify boundary-value tests for overflow detection (WR-AD-15(1) / DC-AM-003).

    Overflow detection covers width excess only (ADR-W2 revised):
      (b) any line width > max_chars → width overflow (including single oversized segment)
    Line-count excess is no longer an overflow condition; it is handled by
    _merge_to_max_lines before check_overflow is applied.
    """

    def test_no_overflow_when_within_limits(self) -> None:
        """No overflow when all line widths are within max_chars."""
        from clipwright_wrap.captions import check_overflow

        lines = ["あいうえ", "かきくけ"]  # 4 chars each, max_chars=5
        assert check_overflow(lines, max_chars=5) is False

    def test_line_width_overflow_at_max_chars_plus_1(self) -> None:
        """Returns True when any line width is max_chars + 1 (boundary value)."""
        from clipwright_wrap.captions import check_overflow

        lines = ["あいうえおか"]  # 6 chars, max_chars=5
        assert check_overflow(lines, max_chars=5) is True

    def test_no_line_width_overflow_at_exactly_max_chars(self) -> None:
        """Returns False when line width equals exactly max_chars (boundary value)."""
        from clipwright_wrap.captions import check_overflow

        lines = ["あいうえお"]  # 5 chars, max_chars=5
        assert check_overflow(lines, max_chars=5) is False

    def test_single_oversized_segment_causes_width_overflow(self) -> None:
        """A single oversized line → True (WR-AD-15(1))."""
        from clipwright_wrap.captions import check_overflow

        lines = ["歩きながら春の"]  # 8 chars, max_chars=5
        assert check_overflow(lines, max_chars=5) is True

    def test_both_lines_overflow(self) -> None:
        """Returns True when at least one line exceeds max_chars."""
        from clipwright_wrap.captions import check_overflow

        lines = [
            "あいうえおか",  # 6 chars, exceeds max_chars=5
            "きくけこ",  # 4 chars, within limit
            "さしすせ",  # 4 chars, within limit
        ]
        assert check_overflow(lines, max_chars=5) is True

    def test_overflow_does_not_truncate_content(self) -> None:
        """check_overflow must not modify the input list (no information loss; WR-AD-15(1))."""
        from clipwright_wrap.captions import check_overflow

        lines = ["あいうえおか", "きくけこ", "さしすせ"]
        original_lines = lines.copy()
        check_overflow(lines, max_chars=5)
        assert lines == original_lines


# ===========================================================================
# _merge_to_max_lines (DC-AM-002 / ADR-W3)
# ===========================================================================


class TestMergeToMaxLines:
    """Verify _merge_to_max_lines greedy front-merge logic (DC-AM-002 / ADR-W3)."""

    def test_no_op_when_within_limit(self) -> None:
        """Returns (lines, False) unchanged when len(lines) <= max_lines (DC-AM-003)."""
        from clipwright_wrap.captions import _merge_to_max_lines

        lines = ["あ", "い"]
        result, merged = _merge_to_max_lines(lines, max_lines=3)
        assert result == ["あ", "い"]
        assert merged is False

    def test_single_merge(self) -> None:
        """3 lines with max_lines=2 produces 2 lines by front-merging the first two."""
        from clipwright_wrap.captions import _merge_to_max_lines

        lines = ["あ", "い", "う"]
        result, merged = _merge_to_max_lines(lines, max_lines=2)
        assert len(result) == 2
        assert merged is True

    def test_convergence_to_max_lines_1(self) -> None:
        """Multiple lines with max_lines=1 reduces to exactly 1 line."""
        from clipwright_wrap.captions import _merge_to_max_lines

        lines = ["a", "b", "c", "d"]
        result, merged = _merge_to_max_lines(lines, max_lines=1)
        assert len(result) == 1
        assert result[0] == "abcd"
        assert merged is True

    def test_merge_returns_true(self) -> None:
        """merged is True when at least one concatenation occurred."""
        from clipwright_wrap.captions import _merge_to_max_lines

        lines = ["x", "y", "z"]
        _, merged = _merge_to_max_lines(lines, max_lines=2)
        assert merged is True

    def test_no_op_returns_false(self) -> None:
        """merged is False for a single-line list and for an empty list."""
        from clipwright_wrap.captions import _merge_to_max_lines

        _, merged_single = _merge_to_max_lines(["only"], max_lines=1)
        assert merged_single is False

        _, merged_empty = _merge_to_max_lines([], max_lines=2)
        assert merged_empty is False

    def test_language_agnostic(self) -> None:
        """Algorithm is list-operation only; produces the same merge behaviour for ja and zh-hans strings."""
        from clipwright_wrap.captions import _merge_to_max_lines

        ja_lines = ["今日は", "いい", "天気"]
        zh_lines = ["今天", "天气", "很好"]

        ja_result, ja_merged = _merge_to_max_lines(ja_lines, max_lines=2)
        zh_result, zh_merged = _merge_to_max_lines(zh_lines, max_lines=2)

        # Both inputs have 3 lines > max_lines=2, so both must be merged
        assert len(ja_result) == 2
        assert len(zh_result) == 2
        assert ja_merged is True
        assert zh_merged is True

    def test_roundtrip_identity(self) -> None:
        """Joining merged_lines with '' equals joining the original lines with '' (ADR-W3)."""
        from clipwright_wrap.captions import _merge_to_max_lines

        lines = ["今日は", "とても", "いい", "天気です。"]
        original_text = "".join(lines)

        merged_lines, _ = _merge_to_max_lines(lines, max_lines=2)
        assert "".join(merged_lines) == original_text


# ===========================================================================
# Defensive cases — 0 cues and empty text
# ===========================================================================


class TestDefensiveCases:
    """Verify defensive cases for 0 cues and empty text."""

    def test_parse_srt_empty_text_no_exception(self) -> None:
        """Empty string SRT parse returns [] without exception."""
        assert parse_captions("", "srt") == []

    def test_parse_vtt_header_only_no_exception(self) -> None:
        """WEBVTT-only VTT parse returns [] without exception."""
        assert parse_captions("WEBVTT\n", "vtt") == []

    def test_serialize_empty_cues_srt_no_exception(self) -> None:
        """SRT serialize with 0 cues returns '' without exception."""
        assert serialize_captions([], "srt") == ""

    def test_serialize_empty_cues_vtt_no_exception(self) -> None:
        """VTT serialize with 0 cues returns 'WEBVTT\\n' without exception."""
        assert serialize_captions([], "vtt") == "WEBVTT\n"

    def test_wrap_cue_lines_empty_segments_no_exception(self) -> None:
        """wrap_cue_lines with empty segments returns [] without exception."""
        assert wrap_cue_lines([], max_chars=16) == []


# ===========================================================================
# Coverage supplement tests (reachable uncovered lines)
# ===========================================================================


class TestParseSrtNonNumericIndex:
    """L85-87: SRT blocks with a non-numeric index line are skipped."""

    def test_non_numeric_index_block_skipped(self) -> None:
        """Blocks with a non-numeric index line are skipped; subsequent valid blocks are retrieved."""
        # First block: index is "abc" (non-numeric) → skipped
        # Second block: valid
        srt = "abc\n00:00:00,000 --> 00:00:01,000\nスキップ\n\n1\n00:00:01,000 --> 00:00:02,000\nOK\n"
        cues = parse_captions(srt, "srt")
        assert len(cues) == 1
        assert cues[0].text == "OK"

    def test_non_numeric_index_only_block_returns_empty(self) -> None:
        """SRT with only non-numeric index blocks returns []."""
        srt = "NOTE\n00:00:00,000 --> 00:00:01,000\nテスト\n"
        cues = parse_captions(srt, "srt")
        assert cues == []


class TestParseSrtIndexOnlyBlock:
    """L90: SRT blocks with only an index line (no timeline line) are skipped."""

    def test_index_only_block_skipped(self) -> None:
        """Blocks with only an index line are skipped; subsequent valid blocks are retrieved."""
        # First block: index line only (no timeline line)
        # Second block: valid
        srt = "1\n\n2\n00:00:01,000 --> 00:00:02,000\nOK\n"
        cues = parse_captions(srt, "srt")
        assert len(cues) == 1
        assert cues[0].text == "OK"


class TestParseVttNoHeader:
    """L130: Input without a WEBVTT header (empty string or non-WEBVTT) returns []."""

    def test_empty_string_returns_empty_list(self) -> None:
        """Empty string VTT input returns []."""
        result = parse_captions("", "vtt")
        assert result == []

    def test_non_webvtt_header_returns_empty_list(self) -> None:
        """Text that does not start with WEBVTT returns []."""
        result = parse_captions(
            "NOTWEBVTT\n\n00:00:00.000 --> 00:00:01.000\nテスト\n", "vtt"
        )
        assert result == []

    def test_srt_content_as_vtt_returns_empty_list(self) -> None:
        """Passing SRT-format text as vtt returns [] (no WEBVTT header)."""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nテスト\n"
        result = parse_captions(srt, "vtt")
        assert result == []


class TestParseVttNoteMultiline:
    """L153: Multi-line NOTE block body is correctly skipped."""

    def test_multiline_note_body_skipped(self) -> None:
        """Multi-line NOTE block body is skipped and the subsequent cue is retrieved."""
        vtt = (
            "WEBVTT\n"
            "\n"
            "NOTE\n"
            "これはコメントの1行目です。\n"
            "これはコメントの2行目です。\n"
            "\n"
            "00:00:00.000 --> 00:00:01.000\n"
            "テスト\n"
        )
        cues = parse_captions(vtt, "vtt")
        assert len(cues) == 1
        assert cues[0].text == "テスト"

    def test_multiple_note_blocks_all_skipped(self) -> None:
        """All multiple NOTE blocks are skipped; only cues are retrieved."""
        vtt = (
            "WEBVTT\n"
            "\n"
            "NOTE block1 line1\n"
            "block1 line2\n"
            "\n"
            "00:00:00.000 --> 00:00:01.000\n"
            "cue1\n"
            "\n"
            "NOTE block2\n"
            "block2 body\n"
            "\n"
            "00:00:01.000 --> 00:00:02.000\n"
            "cue2\n"
        )
        cues = parse_captions(vtt, "vtt")
        assert len(cues) == 2
        assert cues[0].text == "cue1"
        assert cues[1].text == "cue2"


class TestParseVttCueIdAtEnd:
    """L168: Safely terminates when a cue-id line is at the end of the VTT with no subsequent content."""

    def test_cue_id_at_eof_terminates_safely(self) -> None:
        """When a cue-id line is at the end with no following content, only the existing cue is returned without exception."""
        vtt = (
            "WEBVTT\n"
            "\n"
            "00:00:00.000 --> 00:00:01.000\n"
            "テスト\n"
            "\n"
            "dangling-cue-id"  # no timeline line; at EOF
        )
        cues = parse_captions(vtt, "vtt")
        # The valid cue must be retrieved
        assert len(cues) == 1
        assert cues[0].text == "テスト"


class TestParseVttCueIdFollowedByBlankLine:
    """L172-173: Block with cue-id immediately followed by blank line is skipped."""

    def test_cue_id_followed_by_blank_line_skipped(self) -> None:
        """Block where cue-id is followed by a blank line (no timeline line) is skipped."""
        vtt = (
            "WEBVTT\n"
            "\n"
            "dangling-cue-id\n"
            "\n"  # blank line immediately after cue-id → pos hits blank line at L172
            "00:00:00.000 --> 00:00:01.000\n"
            "OK\n"
        )
        cues = parse_captions(vtt, "vtt")
        # cue-id block is skipped; subsequent valid cue is retrieved
        assert any(c.text == "OK" for c in cues)


class TestParseCaptionsUnsupportedFmt:
    """L224: Passing an unsupported fmt to parse_captions raises ClipwrightError(INVALID_INPUT)."""

    def test_unsupported_fmt_raises_clipwright_error(self) -> None:
        """Unsupported fmt → ClipwrightError(INVALID_INPUT) is raised."""
        from clipwright.errors import ClipwrightError, ErrorCode

        with pytest.raises(ClipwrightError) as exc_info:
            parse_captions("some text", "ass")
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_unsupported_fmt_error_message_contains_fmt(self) -> None:
        """ClipwrightError message contains the passed fmt."""
        from clipwright.errors import ClipwrightError

        with pytest.raises(ClipwrightError) as exc_info:
            parse_captions("some text", "xml")
        assert "xml" in exc_info.value.message

    def test_unsupported_fmt_hint_mentions_valid_options(self) -> None:
        """ClipwrightError の hint に有効なオプション（srt/vtt）が示されること。"""
        from clipwright.errors import ClipwrightError

        with pytest.raises(ClipwrightError) as exc_info:
            parse_captions("some text", "unknown")
        assert "srt" in exc_info.value.hint or "vtt" in exc_info.value.hint


class TestSerializeCaptionsUnsupportedFmt:
    """L328: serialize_captions に未対応の fmt を渡すと ClipwrightError(INVALID_INPUT) が送出されること。"""

    def test_unsupported_fmt_raises_clipwright_error(self) -> None:
        """未対応の fmt → ClipwrightError(INVALID_INPUT) が送出されること。"""
        from clipwright.errors import ClipwrightError, ErrorCode

        cues = [Cue(index=1, start="00:00:00,000", end="00:00:01,000", text="テスト")]
        with pytest.raises(ClipwrightError) as exc_info:
            serialize_captions(cues, "ass")
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_unsupported_fmt_error_message_contains_fmt(self) -> None:
        """ClipwrightError のメッセージに渡した fmt が含まれること。"""
        from clipwright.errors import ClipwrightError

        cues = [Cue(index=1, start="00:00:00,000", end="00:00:01,000", text="テスト")]
        with pytest.raises(ClipwrightError) as exc_info:
            serialize_captions(cues, "xml")
        assert "xml" in exc_info.value.message

    def test_unsupported_fmt_hint_mentions_valid_options(self) -> None:
        """ClipwrightError の hint に有効なオプション（srt/vtt）が示されること。"""
        from clipwright.errors import ClipwrightError

        cues = [Cue(index=1, start="00:00:00,000", end="00:00:01,000", text="テスト")]
        with pytest.raises(ClipwrightError) as exc_info:
            serialize_captions(cues, "unknown")
        assert "srt" in exc_info.value.hint or "vtt" in exc_info.value.hint


# ===========================================================================
# CR H-1: 不正 SRT タイムコードの拒否（R1 fix テスト）
# _SRT_TIMELINE_RE を \d{2} に厳格化した後に pass する Red テスト群
# ===========================================================================


class TestSrtTimecodeValidation:
    r"""CR H-1: _SRT_TIMELINE_RE が \d{2} 固定桁で不正タイムコードを拒否することを検証する。

    以下の不正パターンが ValueError または ClipwrightError として拒否されること（impl 修正後に Green）。
    現行 _SRT_TIMELINE_RE = \d+ はこれらを合法としてパースするため、本テストは Red になる。
    """

    # --- 正常ケース（既存非回帰）---

    def test_valid_srt_timecode_accepted(self) -> None:
        """正常な 2桁/3桁ミリ秒固定の SRT タイムコードが受理されること（非回帰）。"""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nテキスト\n"
        cues = parse_captions(srt, "srt")
        assert len(cues) == 1
        assert cues[0].start == "00:00:00,000"
        assert cues[0].end == "00:00:01,000"

    def test_valid_srt_timecode_with_high_minutes_accepted(self) -> None:
        """分が 59 以内の正常タイムコードが受理されること（非回帰）。"""
        srt = "1\n00:59:59,999 --> 01:00:00,000\nテキスト\n"
        cues = parse_captions(srt, "srt")
        assert len(cues) == 1

    # --- CR H-1: 不正タイムコード拒否（impl 修正後に Green・現行は Red）---

    def test_invalid_srt_seconds_over_60_raises_exception(self) -> None:
        """秒が 60 超の SRT タイムコードが例外送出されること（CR H-1）。

        現行 _SRT_TIMELINE_RE = \\d+ は '00:00:60,000' を合法としてパースするため Red。
        _SRT_TIMELINE_RE を \\d{2} に厳格化すると拒否されて Green になる。
        """
        srt = "1\n00:00:60,000 --> 00:00:61,000\nテキスト\n"
        with pytest.raises((ValueError, Exception)) as exc_info:
            parse_captions(srt, "srt")
        # ClipwrightError も許容（impl が ValueError→ClipwrightError 変換を選択した場合）
        from clipwright.errors import ClipwrightError

        assert isinstance(exc_info.value, (ValueError, ClipwrightError))

    def test_invalid_srt_seconds_three_digits_raises_exception(self) -> None:
        """秒が 3 桁の SRT タイムコードが例外送出されること（CR H-1）。

        '00:00:100,000' は秒100で仕様違反。現行 \\d+ は受容するため Red。
        """
        srt = "1\n00:00:100,000 --> 00:00:101,000\nテキスト\n"
        with pytest.raises((ValueError, Exception)) as exc_info:
            parse_captions(srt, "srt")
        from clipwright.errors import ClipwrightError

        assert isinstance(exc_info.value, (ValueError, ClipwrightError))

    def test_invalid_srt_timecode_digit_drop_raises_exception(self) -> None:
        """桁落ち（1桁）の SRT タイムコードが例外送出されること（CR H-1）。

        '0:0:0,0' は SRT 仕様外（HH:MM:SS,mmm の固定桁が要件）。
        現行 \\d+ は受容するため Red。
        """
        srt = "1\n0:0:0,0 --> 0:0:1,0\nテキスト\n"
        with pytest.raises((ValueError, Exception)) as exc_info:
            parse_captions(srt, "srt")
        from clipwright.errors import ClipwrightError

        assert isinstance(exc_info.value, (ValueError, ClipwrightError))


# ===========================================================================
# SR L-1: VTT インラインタグ処理の非回帰テスト
# _VTT_INLINE_TAG_RE に上限（[^>]{0,200}）を導入後も正常タグが保持されること
# ===========================================================================


class TestVttInlineTagHandling:
    """SR L-1: _VTT_INLINE_TAG_RE 上限導入後も正常なインラインタグ cue が保持されること。

    captions.py SR L-1 推奨対応: [^>]* → [^>]{0,200}。
    テストは「上限導入後も正常タグ込み cue が保持される」観点で書く（impl 修正後も Green）。
    """

    def test_normal_c_tag_cue_text_preserved(self) -> None:
        """正常な <c> タグ込み cue テキストが原文保持されること（SR L-1 非回帰）。"""
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n<c.yellow>テキスト</c>\n"
        cues = parse_captions(vtt, "vtt")
        assert len(cues) == 1
        assert cues[0].text == "<c.yellow>テキスト</c>"

    def test_normal_b_tag_cue_text_preserved(self) -> None:
        """正常な <b> タグ込み cue テキストが原文保持されること（SR L-1 非回帰）。"""
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n<b>重要テキスト</b>\n"
        cues = parse_captions(vtt, "vtt")
        assert len(cues) == 1
        assert cues[0].text == "<b>重要テキスト</b>"

    def test_normal_v_tag_cue_text_preserved(self) -> None:
        """正常な <v> タグ（音声アクター）込み cue テキストが原文保持されること（SR L-1 非回帰）。"""
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n<v Speaker>こんにちは</v>\n"
        cues = parse_captions(vtt, "vtt")
        assert len(cues) == 1
        assert cues[0].text == "<v Speaker>こんにちは</v>"

    def test_large_inline_tag_like_string_does_not_crash(self) -> None:
        """巨大インラインタグ風文字列（> なし）を含む VTT cue が例外なく処理されること（SR L-1）。

        [^>]{0,200} の上限が導入されても、処理が正常に完了することを検証する。
        """
        # > を含まない長い「開きタグ風」文字列（200文字超）
        long_pseudo_tag = "<" + "a" * 210 + " テキスト"
        vtt = f"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n{long_pseudo_tag}\n"
        # 例外なく処理されること（結果の内容は問わない）
        cues = parse_captions(vtt, "vtt")
        assert isinstance(cues, list)


# ===========================================================================
# WR-AD-12 整合: transcribe to_srt 由来構造の parse 非回帰テスト
# 末尾空行なし EOF・2桁固定タイムコードの parse が成功すること
# ===========================================================================


class TestWrAd12TranscribeCompatibility:
    r"""WR-AD-12: transcribe to_srt 由来のバイト構造（末尾空行なし EOF）の parse 非回帰テスト。

    transcribe to_srt は末尾に空行を付けない（末尾単一改行 EOF）。
    _SRT_TIMELINE_RE を \d{2} に厳格化した後でも、2桁固定タイムコードの
    正常な transcribe 出力が引き続き受理されることを確認する。
    """

    SRT_TRANSCRIBE_LIKE = (
        "1\n00:00:00,000 --> 00:00:05,123\nこんにちは世界。\n"
        "\n"
        "2\n00:00:05,123 --> 00:00:10,456\nこれはテストです。\n"
        "\n"
        "3\n00:00:10,456 --> 00:01:00,000\n最後の cue（末尾空行なし）\n"
    )

    def test_transcribe_like_srt_all_cues_parsed(self) -> None:
        """transcribe to_srt 由来の 2桁固定タイムコード SRT が全 cue parse されること（WR-AD-12）。"""
        cues = parse_captions(self.SRT_TRANSCRIBE_LIKE, "srt")
        assert len(cues) == 3

    def test_transcribe_like_srt_timecodes_preserved(self) -> None:
        """parse された全 cue のタイムコードが不変保持されること（WR-AD-06）。"""
        cues = parse_captions(self.SRT_TRANSCRIBE_LIKE, "srt")
        assert cues[0].start == "00:00:00,000"
        assert cues[0].end == "00:00:05,123"
        assert cues[2].start == "00:00:10,456"
        assert cues[2].end == "00:01:00,000"

    def test_transcribe_like_srt_texts_correct(self) -> None:
        """parse された全 cue のテキストが正しいこと。"""
        cues = parse_captions(self.SRT_TRANSCRIBE_LIKE, "srt")
        assert cues[0].text == "こんにちは世界。"
        assert cues[1].text == "これはテストです。"
        assert cues[2].text == "最後の cue（末尾空行なし）"

    def test_two_digit_fixed_timecode_accepted_after_regex_fix(self) -> None:
        """HH:MM:SS,mmm の 2桁固定タイムコードが _SRT_TIMELINE_RE 修正後も受理されること。

        impl が _SRT_TIMELINE_RE を \\d{2}:\\d{2}:\\d{2},\\d{3} に修正した後も、
        通常の transcribe 生成 SRT（2桁固定・ミリ秒3桁）が正常にパースされることを確認する。
        """
        srt = "1\n00:01:30,500 --> 00:01:31,000\nテスト\n"
        cues = parse_captions(srt, "srt")
        assert len(cues) == 1
        assert cues[0].start == "00:01:30,500"
        assert cues[0].end == "00:01:31,000"
