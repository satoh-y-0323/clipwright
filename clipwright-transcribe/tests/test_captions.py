"""test_captions.py — Red tests for the captions.py pure logic layer
(target: 100% contract coverage).

Fixes the specification from architecture TR-AD-02/06/07 and DC-GP-002/DC-AS-005.
This file is intended to fail at import when captions.py does not exist, thereby
signalling that the feature is not yet implemented (Red test suite).

Note (DC-GP-001-R):
  100% contract coverage is measured against the hypothetical spike fixture
  (whisper_sample.json). Until the real binary is confirmed via e2e, schema
  correctness against actual whisper output is unverified.
"""

from __future__ import annotations

from typing import Any

import pytest

from clipwright_transcribe.captions import (
    Segment,
    normalize_segments,
    to_srt,
    to_vtt,
)

# ===========================================================================
# normalize_segments — basic behaviour
# ===========================================================================


class TestNormalizeSegmentsBasic:
    """Verify basic behaviour of normalize_segments (fixture-based)."""

    def test_normalize_returns_list(self, whisper_sample_json: dict[str, Any]) -> None:
        """normalize_segments returns a list."""
        result = normalize_segments(whisper_sample_json)
        assert isinstance(result, list)

    def test_normalize_returns_correct_count(
        self, whisper_sample_json: dict[str, Any]
    ) -> None:
        """The fixture's 3 segments are correctly normalised."""
        result = normalize_segments(whisper_sample_json)
        assert len(result) == 3

    def test_first_segment_start_sec(self, whisper_sample_json: dict[str, Any]) -> None:
        """offsets.from=0ms -> start_sec=0.0 s (TR-AD-07)."""
        result = normalize_segments(whisper_sample_json)
        assert result[0]["start_sec"] == pytest.approx(0.0)

    def test_first_segment_end_sec(self, whisper_sample_json: dict[str, Any]) -> None:
        """offsets.to=1200ms -> end_sec=1.2 s (TR-AD-07)."""
        result = normalize_segments(whisper_sample_json)
        assert result[0]["end_sec"] == pytest.approx(1.2)

    def test_second_segment_start_sec(
        self, whisper_sample_json: dict[str, Any]
    ) -> None:
        """offsets.from=1500ms -> start_sec=1.5 s."""
        result = normalize_segments(whisper_sample_json)
        assert result[1]["start_sec"] == pytest.approx(1.5)

    def test_second_segment_end_sec(self, whisper_sample_json: dict[str, Any]) -> None:
        """offsets.to=2800ms -> end_sec=2.8 s."""
        result = normalize_segments(whisper_sample_json)
        assert result[1]["end_sec"] == pytest.approx(2.8)

    def test_third_segment_start_sec(self, whisper_sample_json: dict[str, Any]) -> None:
        """offsets.from=3000ms -> start_sec=3.0 s."""
        result = normalize_segments(whisper_sample_json)
        assert result[2]["start_sec"] == pytest.approx(3.0)

    def test_third_segment_end_sec(self, whisper_sample_json: dict[str, Any]) -> None:
        """offsets.to=4500ms -> end_sec=4.5 s."""
        result = normalize_segments(whisper_sample_json)
        assert result[2]["end_sec"] == pytest.approx(4.5)

    def test_segment_text_stripped(self, whisper_sample_json: dict[str, Any]) -> None:
        """text has leading/trailing whitespace stripped (whisper output often has a
        leading space)."""
        result = normalize_segments(whisper_sample_json)
        # fixture text is " Hello world." -> "Hello world."
        assert result[0]["text"] == "Hello world."

    def test_all_segments_have_required_keys(
        self, whisper_sample_json: dict[str, Any]
    ) -> None:
        """All segments contain the start_sec / end_sec / text keys."""
        result = normalize_segments(whisper_sample_json)
        for seg in result:
            assert "start_sec" in seg
            assert "end_sec" in seg
            assert "text" in seg


# ===========================================================================
# normalize_segments — defensive filtering
# ===========================================================================


class TestNormalizeSegmentsFiltering:
    """Verify that invalid and degenerate segments are removed."""

    def test_empty_text_segment_removed(self) -> None:
        """Segments with empty text are removed (DC-GP-002 supplement)."""
        data = {
            "transcription": [
                {"offsets": {"from": 0, "to": 1000}, "text": ""},
                {"offsets": {"from": 1000, "to": 2000}, "text": "Hello"},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "Hello"

    def test_whitespace_only_text_segment_removed(self) -> None:
        """Segments with whitespace-only text are removed."""
        data = {
            "transcription": [
                {"offsets": {"from": 0, "to": 1000}, "text": "   "},
                {"offsets": {"from": 1000, "to": 2000}, "text": " Hello "},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "Hello"

    def test_degenerate_segment_start_equals_end_removed(self) -> None:
        """Degenerate intervals where start_sec == end_sec are removed."""
        data = {
            "transcription": [
                {"offsets": {"from": 1000, "to": 1000}, "text": "degenerate"},
                {"offsets": {"from": 1000, "to": 2000}, "text": "valid"},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "valid"

    def test_degenerate_segment_start_greater_than_end_removed(self) -> None:
        """Degenerate intervals where start_sec > end_sec are removed."""
        data = {
            "transcription": [
                {"offsets": {"from": 2000, "to": 1000}, "text": "reversed"},
                {"offsets": {"from": 1000, "to": 2000}, "text": "valid"},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "valid"

    def test_missing_offsets_key_segment_removed(self) -> None:
        """Segments missing the offsets key are removed (defensive)."""
        data = {
            "transcription": [
                {"text": "no offsets"},
                {"offsets": {"from": 0, "to": 1000}, "text": "valid"},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "valid"

    def test_missing_from_key_segment_removed(self) -> None:
        """Segments missing offsets.from are removed (defensive)."""
        data = {
            "transcription": [
                {"offsets": {"to": 1000}, "text": "no from"},
                {"offsets": {"from": 0, "to": 1000}, "text": "valid"},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "valid"

    def test_missing_to_key_segment_removed(self) -> None:
        """Segments missing offsets.to are removed (defensive)."""
        data = {
            "transcription": [
                {"offsets": {"from": 0}, "text": "no to"},
                {"offsets": {"from": 0, "to": 1000}, "text": "valid"},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "valid"

    def test_missing_text_key_segment_removed(self) -> None:
        """Segments missing the text key are removed (defensive)."""
        data = {
            "transcription": [
                {"offsets": {"from": 0, "to": 1000}},
                {"offsets": {"from": 1000, "to": 2000}, "text": "valid"},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "valid"

    def test_non_dict_entry_segment_removed(self) -> None:
        """Non-dict transcription entries are removed (defensive)."""
        data = {
            "transcription": [
                "not a dict",
                {"offsets": {"from": 0, "to": 1000}, "text": "valid"},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "valid"

    def test_non_numeric_offsets_segment_removed(self) -> None:
        """Segments where offsets.from/to cannot be converted to float are removed
        (defensive)."""
        data = {
            "transcription": [
                {"offsets": {"from": "abc", "to": 1000}, "text": "bad offset"},
                {"offsets": {"from": 0, "to": 1000}, "text": "valid"},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "valid"

    def test_non_list_transcription_returns_empty(self) -> None:
        """When transcription is not a list, an empty list is returned (defensive)."""
        result = normalize_segments({"transcription": "not a list"})
        assert result == []

    def test_empty_transcription_list_returns_empty(self) -> None:
        """An empty transcription list returns an empty list (DC-GP-002)."""
        data: dict[str, Any] = {"transcription": []}
        result = normalize_segments(data)
        assert result == []

    def test_missing_transcription_key_returns_empty(self) -> None:
        """A missing transcription key returns an empty list (defensive)."""
        result = normalize_segments({})
        assert result == []


# ===========================================================================
# DC-GP-002 — Zero segments
# ===========================================================================


class TestDCGP002ZeroSegments:
    """Verify behaviour of each function when segments is empty (DC-GP-002)."""

    def test_normalize_segments_empty_returns_empty_list(self) -> None:
        """normalize_segments with zero-entry input returns an empty list."""
        result = normalize_segments({"transcription": []})
        assert result == []

    def test_to_srt_empty_segments_returns_empty_string(self) -> None:
        """to_srt with zero-entry input returns an empty string."""
        result = to_srt([])
        assert result == ""

    def test_to_vtt_empty_segments_returns_header_only(self) -> None:
        """to_vtt with zero-entry input returns only the "WEBVTT" header."""
        result = to_vtt([])
        assert result.strip() == "WEBVTT"


# ===========================================================================
# to_srt — timecodes and output format
# ===========================================================================


class TestToSrt:
    """Verify to_srt format, index, and timecodes."""

    def _make_segment(self, start_sec: float, end_sec: float, text: str) -> Segment:
        return {"start_sec": start_sec, "end_sec": end_sec, "text": text}

    def test_single_segment_index_starts_at_1(self) -> None:
        """SRT index starts at 1."""
        segments = [self._make_segment(0.0, 1.0, "Hello")]
        srt = to_srt(segments)
        lines = srt.strip().splitlines()
        assert lines[0] == "1"

    def test_single_segment_timecode_format(self) -> None:
        """SRT timecode is in HH:MM:SS,mmm format (TR-AD-07)."""
        segments = [self._make_segment(0.0, 1.5, "Hello")]
        srt = to_srt(segments)
        lines = srt.strip().splitlines()
        # Line 2 is the timecode line: 00:00:00,000 --> 00:00:01,500
        assert "-->" in lines[1]
        start, end = lines[1].split(" --> ")
        # Verify HH:MM:SS,mmm pattern
        import re

        pattern = r"^\d{2}:\d{2}:\d{2},\d{3}$"
        assert re.match(pattern, start), f"SRT start timecode format error: {start}"
        assert re.match(pattern, end), f"SRT end timecode format error: {end}"

    def test_timecode_zero_second(self) -> None:
        """start_sec=0.0 -> '00:00:00,000' (boundary value)."""
        segments = [self._make_segment(0.0, 0.5, "Zero")]
        srt = to_srt(segments)
        assert "00:00:00,000" in srt

    def test_timecode_hour_rollover(self) -> None:
        """Second values over 60 minutes roll over to the hours field (boundary
        value)."""
        segments = [self._make_segment(3661.0, 3662.5, "Rollover")]
        srt = to_srt(segments)
        # 3661 s = 1 h 1 m 1 s
        assert "01:01:01,000" in srt

    def test_timecode_milliseconds_precision(self) -> None:
        """Millisecond part is formatted correctly."""
        segments = [self._make_segment(1.234, 2.567, "Millis")]
        srt = to_srt(segments)
        assert "00:00:01,234" in srt
        assert "00:00:02,567" in srt

    def test_multiple_segments_sequential_index(self) -> None:
        """Multiple segments receive sequential indices."""
        segments = [
            self._make_segment(0.0, 1.0, "First"),
            self._make_segment(1.5, 2.5, "Second"),
            self._make_segment(3.0, 4.0, "Third"),
        ]
        srt = to_srt(segments)
        lines = [line for line in srt.splitlines() if line.strip().isdigit()]
        assert lines == ["1", "2", "3"]

    def test_multiple_segments_blank_line_separator(self) -> None:
        """Multiple segments are separated by blank lines."""
        segments = [
            self._make_segment(0.0, 1.0, "First"),
            self._make_segment(1.5, 2.5, "Second"),
        ]
        srt = to_srt(segments)
        # Blank line must exist
        assert "\n\n" in srt

    def test_segment_text_in_output(self) -> None:
        """Segment text appears in the SRT output."""
        segments = [self._make_segment(0.0, 1.0, "Hello world")]
        srt = to_srt(segments)
        assert "Hello world" in srt

    def test_fixture_based_srt_output(
        self, whisper_sample_json: dict[str, Any]
    ) -> None:
        """normalize_segments -> to_srt pipeline works end-to-end with the fixture."""
        segments = normalize_segments(whisper_sample_json)
        srt = to_srt(segments)
        assert len(srt) > 0
        assert "1" in srt
        assert "-->" in srt


# ===========================================================================
# to_vtt — timecodes and output format
# ===========================================================================


class TestToVtt:
    """Verify to_vtt format, header, and timecodes."""

    def _make_segment(self, start_sec: float, end_sec: float, text: str) -> Segment:
        return {"start_sec": start_sec, "end_sec": end_sec, "text": text}

    def test_output_starts_with_webvtt_header(self) -> None:
        """VTT output starts with the 'WEBVTT' header."""
        segments = [self._make_segment(0.0, 1.0, "Hello")]
        vtt = to_vtt(segments)
        assert vtt.startswith("WEBVTT")

    def test_timecode_format_uses_dot_separator(self) -> None:
        """VTT timecode uses HH:MM:SS.mmm format (dot separator; TR-AD-07)."""
        segments = [self._make_segment(0.0, 1.5, "Hello")]
        vtt = to_vtt(segments)
        import re

        pattern = r"\d{2}:\d{2}:\d{2}\.\d{3}"
        assert re.search(pattern, vtt), f"VTT timecode format error: {vtt}"

    def test_timecode_zero_second(self) -> None:
        """start_sec=0.0 -> '00:00:00.000' (boundary value)."""
        segments = [self._make_segment(0.0, 0.5, "Zero")]
        vtt = to_vtt(segments)
        assert "00:00:00.000" in vtt

    def test_timecode_hour_rollover(self) -> None:
        """Second values over 60 minutes roll over to the hours field (boundary
        value)."""
        segments = [self._make_segment(3661.0, 3662.5, "Rollover")]
        vtt = to_vtt(segments)
        assert "01:01:01.000" in vtt

    def test_timecode_milliseconds_precision(self) -> None:
        """Millisecond part is formatted correctly."""
        segments = [self._make_segment(1.234, 2.567, "Millis")]
        vtt = to_vtt(segments)
        assert "00:00:01.234" in vtt
        assert "00:00:02.567" in vtt

    def test_segment_text_in_output(self) -> None:
        """Segment text appears in the VTT output."""
        segments = [self._make_segment(0.0, 1.0, "Hello world")]
        vtt = to_vtt(segments)
        assert "Hello world" in vtt

    def test_arrow_separator_present(self) -> None:
        """The --> separator appears between timecodes."""
        segments = [self._make_segment(0.0, 1.0, "Hello")]
        vtt = to_vtt(segments)
        assert "-->" in vtt

    def test_fixture_based_vtt_output(
        self, whisper_sample_json: dict[str, Any]
    ) -> None:
        """normalize_segments -> to_vtt pipeline works end-to-end with the fixture."""
        segments = normalize_segments(whisper_sample_json)
        vtt = to_vtt(segments)
        assert vtt.startswith("WEBVTT")
        assert "-->" in vtt


# ===========================================================================
# DC-AS-005 — SRT and VTT timecode consistency
# ===========================================================================


class TestTimecodeConsistency:
    """Verify that SRT and VTT derive from the same second value (DC-AS-005, pure
    logic side)."""

    def _make_segment(self, start_sec: float, end_sec: float, text: str) -> Segment:
        return {"start_sec": start_sec, "end_sec": end_sec, "text": text}

    def test_srt_and_vtt_share_same_second_values(self) -> None:
        """SRT and VTT share the same HH:MM:SS integer part."""
        segments = [self._make_segment(1.234, 2.567, "Consistent")]
        srt = to_srt(segments)
        vtt = to_vtt(segments)
        # SRT: 00:00:01,234 -> HH:MM:SS part "00:00:01"
        # VTT: 00:00:01.234 -> HH:MM:SS part "00:00:01"
        srt_hms_start = "00:00:01"
        vtt_hms_start = "00:00:01"
        assert srt_hms_start in srt
        assert vtt_hms_start in vtt

    def test_srt_comma_and_vtt_dot_differ_only_in_separator(self) -> None:
        """Only the millisecond separator differs between SRT (comma) and VTT (dot);
        the values are identical."""
        segments = [self._make_segment(0.0, 1.5, "Test")]
        srt = to_srt(segments)
        vtt = to_vtt(segments)
        # SRT: 00:00:00,000 --> 00:00:01,500
        # VTT: 00:00:00.000 --> 00:00:01.500
        assert "00:00:00,000" in srt
        assert "00:00:01,500" in srt
        assert "00:00:00.000" in vtt
        assert "00:00:01.500" in vtt

    def test_fixture_srt_and_vtt_timecodes_consistent(
        self, whisper_sample_json: dict[str, Any]
    ) -> None:
        """SRT/VTT timecodes are consistent for fixture-derived segments."""
        segments = normalize_segments(whisper_sample_json)
        srt = to_srt(segments)
        vtt = to_vtt(segments)

        # First segment: start=0.0s -> "00:00:00"
        assert "00:00:00,000" in srt
        assert "00:00:00.000" in vtt

        # First segment: end=1.2s -> "00:00:01,200" / "00:00:01.200"
        assert "00:00:01,200" in srt
        assert "00:00:01.200" in vtt

    def test_millisecond_rounding_consistent_between_srt_and_vtt(self) -> None:
        """Millisecond values are identical between SRT and VTT (rounding
        consistency)."""
        segments = [self._make_segment(1.001, 2.999, "Rounding")]
        srt = to_srt(segments)
        vtt = to_vtt(segments)
        # ms part: SRT "001" / VTT "001" must match
        assert "00:00:01,001" in srt
        assert "00:00:01.001" in vtt
        assert "00:00:02,999" in srt
        assert "00:00:02.999" in vtt


# ===========================================================================
# CR L-8 — Documenting _format_timecode rounding behaviour (locking round-half-up
# spec)
# ===========================================================================


class TestFormatTimecodeRounding:
    """Lock the round-half-up behaviour of _format_timecode with boundary values
    (CR L-8).

    The implementation uses int(round(total_seconds * 1000)), making round-half-up
    the correct specification. The docstring had an incorrect "truncation" note (CR
    L-8); this test class pins the spec via boundary values before that docstring fix
    (impl-captions-fix).

    All tests below are boundary values where round-half-up passes and truncation
    fails. Since the implementation already uses round(), they are currently Green.
    They remain Green after the docstring fix (impl-captions-fix) to guarantee spec
    alignment.
    """

    def _make_segment(self, start_sec: float, end_sec: float, text: str) -> Segment:
        return {"start_sec": start_sec, "end_sec": end_sec, "text": text}

    def test_round_up_at_0_5ms_boundary(self) -> None:
        """1.9999 s (fractional part > 0.9 ms) rounds up to 2000 ms.

        1.9999 * 1000 = 1999.9 -> round(1999.9) = 2000 -> "00:00:02,000"
        Truncation (int(1999.9) = 1999) would give "00:00:01,999", uniquely
        distinguishing the two behaviours.
        """
        segments = [self._make_segment(0.0, 1.9999, "RoundUp")]
        srt = to_srt(segments)
        assert "00:00:02,000" in srt, (
            f"Expected round-half-up to 2000 ms (truncation gives 1999 ms): {srt}"
        )

    def test_round_up_at_0_5ms_boundary_vtt(self) -> None:
        """1.9999 s also rounds up to 2000 ms in VTT output.

        Confirms that SRT and VTT derive the same ms value (DC-AS-005 non-regression).
        """
        segments = [self._make_segment(0.0, 1.9999, "RoundUpVtt")]
        vtt = to_vtt(segments)
        assert "00:00:02.000" in vtt, (
            f"Expected round-half-up to 2000 ms (truncation gives 1999 ms): {vtt}"
        )

    def test_srt_and_vtt_same_ms_at_rounding_boundary(self) -> None:
        """SRT and VTT produce the same ms value for 1.9999 s (DC-AS-005
        non-regression).

        Only the separator differs (SRT=","  VTT="."); ms values must match.
        """
        segments = [self._make_segment(0.0, 1.9999, "Consistency")]
        srt = to_srt(segments)
        vtt = to_vtt(segments)
        assert "00:00:02,000" in srt, f"SRT round-half-up 2000 ms: {srt}"
        assert "00:00:02.000" in vtt, f"VTT round-half-up 2000 ms: {vtt}"

    def test_minute_rollover_at_rounding_boundary(self) -> None:
        """59.9996 s (fractional part > 0.6 ms) rounds up to 60000 ms -> minute
        rollover.

        59.9996 * 1000 = 59999.6 -> round(59999.6) = 60000 ms = 1 min 0 s 0 ms
        Truncation (int(59999.6) = 59999 ms = 59 s 999 ms) gives "00:00:59,999".
        """
        segments = [self._make_segment(0.0, 59.9996, "MinuteRollover")]
        srt = to_srt(segments)
        assert "00:01:00,000" in srt, (
            f"Expected minute rollover (00:01:00,000) from round-half-up: {srt}"
        )

    def test_hour_rollover_at_rounding_boundary(self) -> None:
        """3599.9996 s (fractional part > 0.6 ms) rounds up to hour rollover.

        3599.9996 * 1000 = 3599999.6 -> round(3599999.6) = 3600000 ms = 1 h 0 m 0 s
        Truncation gives "00:59:59,999".
        """
        segments = [self._make_segment(0.0, 3599.9996, "HourRollover")]
        srt = to_srt(segments)
        assert "01:00:00,000" in srt, (
            f"Expected hour rollover (01:00:00,000) from round-half-up: {srt}"
        )
