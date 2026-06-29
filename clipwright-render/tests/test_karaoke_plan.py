"""test_karaoke_plan.py — Red-phase tests for karaoke ASS generation (plan.py)
and SubtitleOptions schema extensions.

ALL tests fail before implementation because:
  - plan.py does not yet export _KaraokeWord / _WordCue / _parse_word_vtt /
    _escape_ass_text / _group_words_into_lines / _karaoke_event_text /
    _build_karaoke_ass  →  ImportError at collection (correct Red failure).
  - SubtitleOptions does not yet have karaoke / highlight_color /
    chars_per_line / max_lines  →  AttributeError / ValidationError at runtime.

Coverage: F-R-01..06 / SEC-03/04 / AC-5/6 / ADR-K3/K5/K6/K7/K8.

Drift guards (must stay in sync with plan.py / schemas.py):
  MAX_WORDS        = 50_000
  MAX_CUES         = 10_000
  highlight_color default  -> None  (caller resolves to #FFFF00)
  chars_per_line default   -> 42
  max_lines default        -> 2
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Plan functions — these do NOT yet exist; import causes collection-level
# ImportError, which is the expected Red failure.
# ---------------------------------------------------------------------------
from clipwright_render.plan import (
    MAX_CUES,
    MAX_WORD_VTT_BYTES,
    MAX_WORDS,
    _KaraokeWord,  # type: ignore[attr-defined]
    _WordCue,  # type: ignore[attr-defined]
    _build_karaoke_ass,  # type: ignore[attr-defined]
    _escape_ass_text,  # type: ignore[attr-defined]
    _group_words_into_lines,  # type: ignore[attr-defined]
    _karaoke_event_text,  # type: ignore[attr-defined]
    _parse_word_vtt,  # type: ignore[attr-defined]
)
from clipwright_render.schemas import SubtitleOptions

# ---------------------------------------------------------------------------
# Drift-guard constants — must match plan.py / plan-report §4
# ---------------------------------------------------------------------------
_MAX_WORDS: int = 50_000
_MAX_CUES: int = 10_000
_DEFAULT_HIGHLIGHT_HEX: str = "#FFFF00"
_DEFAULT_CHARS_PER_LINE: int = 42
_DEFAULT_MAX_LINES: int = 2

# ASS colour for default yellow #FFFF00 (R=FF G=FF B=00 → &H00{BB}{GG}{RR})
_YELLOW_ASS: str = "&H0000FFFF"
# ASS colour for white #FFFFFF
_WHITE_ASS: str = "&H00FFFFFF"

# Canonical fixture (placed by s0-contract-fixture — 2 cues / 10 words)
_FIXTURE_DIR = Path(__file__).parent / "fixtures"
_CANONICAL_VTT = _FIXTURE_DIR / "word_vtt_canonical.vtt"


# ===========================================================================
# Section 1 — SubtitleOptions schema: new karaoke fields (F-R-01/03/05)
# ===========================================================================


class TestSubtitleOptionsKaraokeSchema:
    """Verify SubtitleOptions gains karaoke / highlight_color / chars_per_line /
    max_lines with correct defaults, validation, and extra-forbid maintenance."""

    # --- karaoke ---

    def test_karaoke_default_false(self) -> None:
        # Arrange / Act
        opts = SubtitleOptions(path="/tmp/sub.vtt")

        # Assert — new field default
        assert opts.karaoke is False

    def test_karaoke_true_accepted(self) -> None:
        opts = SubtitleOptions(path="/tmp/sub.vtt", karaoke=True)
        assert opts.karaoke is True

    # --- highlight_color ---

    def test_highlight_color_default_none(self) -> None:
        opts = SubtitleOptions(path="/tmp/sub.vtt")
        assert opts.highlight_color is None

    def test_highlight_color_accepts_valid_hex(self) -> None:
        opts = SubtitleOptions(path="/tmp/sub.vtt", highlight_color="#FF0000")
        assert opts.highlight_color == "#FF0000"

    def test_highlight_color_accepts_lowercase_hex(self) -> None:
        opts = SubtitleOptions(path="/tmp/sub.vtt", highlight_color="#ff8800")
        assert opts.highlight_color == "#ff8800"

    def test_highlight_color_accepts_default_yellow(self) -> None:
        opts = SubtitleOptions(
            path="/tmp/sub.vtt", highlight_color=_DEFAULT_HIGHLIGHT_HEX
        )
        assert opts.highlight_color == _DEFAULT_HIGHLIGHT_HEX

    @pytest.mark.parametrize(
        "bad",
        [
            "red",
            "#ZZZ000",
            "#FFFFF",  # too short
            "#FFFFFFF",  # too long
            "FFFF00",  # missing #
            "",
            "#",
        ],
    )
    def test_highlight_color_rejects_invalid(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            SubtitleOptions(path="/tmp/sub.vtt", highlight_color=bad)

    # --- chars_per_line ---

    def test_chars_per_line_default(self) -> None:
        # Drift guard: must equal _DEFAULT_CHARS_PER_LINE (42)
        opts = SubtitleOptions(path="/tmp/sub.vtt")
        assert opts.chars_per_line == _DEFAULT_CHARS_PER_LINE

    def test_chars_per_line_min_accepted(self) -> None:
        opts = SubtitleOptions(path="/tmp/sub.vtt", chars_per_line=1)
        assert opts.chars_per_line == 1

    def test_chars_per_line_max_accepted(self) -> None:
        opts = SubtitleOptions(path="/tmp/sub.vtt", chars_per_line=200)
        assert opts.chars_per_line == 200

    def test_chars_per_line_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SubtitleOptions(path="/tmp/sub.vtt", chars_per_line=0)

    def test_chars_per_line_over_max_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SubtitleOptions(path="/tmp/sub.vtt", chars_per_line=201)

    # --- max_lines ---

    def test_max_lines_default(self) -> None:
        # Drift guard: must equal _DEFAULT_MAX_LINES (2)
        opts = SubtitleOptions(path="/tmp/sub.vtt")
        assert opts.max_lines == _DEFAULT_MAX_LINES

    def test_max_lines_min_accepted(self) -> None:
        opts = SubtitleOptions(path="/tmp/sub.vtt", max_lines=1)
        assert opts.max_lines == 1

    def test_max_lines_max_accepted(self) -> None:
        opts = SubtitleOptions(path="/tmp/sub.vtt", max_lines=4)
        assert opts.max_lines == 4

    def test_max_lines_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SubtitleOptions(path="/tmp/sub.vtt", max_lines=0)

    def test_max_lines_five_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SubtitleOptions(path="/tmp/sub.vtt", max_lines=5)

    # --- extra="forbid" still maintained ---

    def test_extra_forbid_maintained(self) -> None:
        with pytest.raises(ValidationError):
            SubtitleOptions(path="/tmp/sub.vtt", unknown_field="x")  # type: ignore[call-arg]


# ===========================================================================
# Section 2 — _parse_word_vtt (ADR-K7 / CWE-400 / SEC-03)
# ===========================================================================


class TestParseWordVtt:
    """Parse the canonical fixture and verify error paths."""

    def test_canonical_returns_two_cues(self) -> None:
        # Arrange / Act
        cues = _parse_word_vtt(
            str(_CANONICAL_VTT), max_words=_MAX_WORDS, max_cues=_MAX_CUES
        )

        # Assert — 2 cues from canonical fixture
        assert len(cues) == 2

    def test_canonical_first_cue_timing(self) -> None:
        cues = _parse_word_vtt(
            str(_CANONICAL_VTT), max_words=_MAX_WORDS, max_cues=_MAX_CUES
        )
        c = cues[0]
        assert c.start == pytest.approx(1.0)
        assert c.end == pytest.approx(3.5)

    def test_canonical_first_cue_word_count(self) -> None:
        cues = _parse_word_vtt(
            str(_CANONICAL_VTT), max_words=_MAX_WORDS, max_cues=_MAX_CUES
        )
        # "Hello world this is a test" → 6 words
        assert len(cues[0].words) == 6

    def test_canonical_first_cue_word_texts(self) -> None:
        cues = _parse_word_vtt(
            str(_CANONICAL_VTT), max_words=_MAX_WORDS, max_cues=_MAX_CUES
        )
        texts = [w.text for w in cues[0].words]
        assert texts == ["Hello", "world", "this", "is", "a", "test"]

    def test_canonical_first_cue_word_starts(self) -> None:
        cues = _parse_word_vtt(
            str(_CANONICAL_VTT), max_words=_MAX_WORDS, max_cues=_MAX_CUES
        )
        starts = [w.start for w in cues[0].words]
        assert starts == pytest.approx([1.0, 1.4, 1.9, 2.3, 2.6, 2.9])

    def test_canonical_first_cue_last_word_end_equals_cue_end(self) -> None:
        # ADR-K7: last word.end = cue end (not next inline ts)
        cues = _parse_word_vtt(
            str(_CANONICAL_VTT), max_words=_MAX_WORDS, max_cues=_MAX_CUES
        )
        assert cues[0].words[-1].end == pytest.approx(3.5)

    def test_canonical_first_cue_inner_word_ends(self) -> None:
        # Non-last words: word.end = next word's inline timestamp
        cues = _parse_word_vtt(
            str(_CANONICAL_VTT), max_words=_MAX_WORDS, max_cues=_MAX_CUES
        )
        words = cues[0].words
        # Hello.end = world.start = 1.4
        assert words[0].end == pytest.approx(1.4)
        # world.end = this.start = 1.9
        assert words[1].end == pytest.approx(1.9)

    def test_canonical_second_cue_word_count(self) -> None:
        cues = _parse_word_vtt(
            str(_CANONICAL_VTT), max_words=_MAX_WORDS, max_cues=_MAX_CUES
        )
        # "With karaoke support enabled" → 4 words
        assert len(cues[1].words) == 4

    def test_canonical_second_cue_word_texts(self) -> None:
        cues = _parse_word_vtt(
            str(_CANONICAL_VTT), max_words=_MAX_WORDS, max_cues=_MAX_CUES
        )
        texts = [w.text for w in cues[1].words]
        assert texts == ["With", "karaoke", "support", "enabled"]

    def test_canonical_second_cue_last_word_end_equals_cue_end(self) -> None:
        cues = _parse_word_vtt(
            str(_CANONICAL_VTT), max_words=_MAX_WORDS, max_cues=_MAX_CUES
        )
        assert cues[1].words[-1].end == pytest.approx(6.2)

    def test_cwe400_exceeds_max_words_raises_invalid_input(
        self, tmp_path: Path
    ) -> None:
        # Arrange: canonical has 10 words; set max_words=3 to trigger limit
        vtt = tmp_path / "over_words.vtt"
        vtt.write_text(_CANONICAL_VTT.read_text(encoding="utf-8"), encoding="utf-8")

        # Act / Assert
        with pytest.raises(ClipwrightError) as exc_info:
            _parse_word_vtt(str(vtt), max_words=3, max_cues=_MAX_CUES)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_cwe400_max_words_hint_contains_limit(self, tmp_path: Path) -> None:
        vtt = tmp_path / "over_words.vtt"
        vtt.write_text(_CANONICAL_VTT.read_text(encoding="utf-8"), encoding="utf-8")

        with pytest.raises(ClipwrightError) as exc_info:
            _parse_word_vtt(str(vtt), max_words=3, max_cues=_MAX_CUES)
        # Hint must expose the limit so callers know how to split input
        assert "3" in exc_info.value.hint

    def test_cwe400_exceeds_max_cues_raises_invalid_input(self, tmp_path: Path) -> None:
        # Arrange: canonical has 2 cues; set max_cues=1 to trigger limit
        vtt = tmp_path / "over_cues.vtt"
        vtt.write_text(_CANONICAL_VTT.read_text(encoding="utf-8"), encoding="utf-8")

        with pytest.raises(ClipwrightError) as exc_info:
            _parse_word_vtt(str(vtt), max_words=_MAX_WORDS, max_cues=1)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_all_cues_no_inline_timestamps_raises_invalid_input(
        self, tmp_path: Path
    ) -> None:
        # ADR-K7: a file with no inline timestamps at all is not a word-VTT
        vtt = tmp_path / "no_tags.vtt"
        vtt.write_text(
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:03.000\n"
            "Hello world this is a test\n\n"
            "00:00:04.000 --> 00:00:06.000\n"
            "No timestamps here either\n",
            encoding="utf-8",
        )

        with pytest.raises(ClipwrightError) as exc_info:
            _parse_word_vtt(str(vtt), max_words=_MAX_WORDS, max_cues=_MAX_CUES)
        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_partial_cue_no_tags_emits_warning_not_error(
        self, tmp_path: Path, recwarn: pytest.WarningsChecker
    ) -> None:
        # ADR-K7: only some cues lack inline timestamps → static line + warning
        vtt = tmp_path / "partial_tags.vtt"
        vtt.write_text(
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:03.500\n"
            "<00:00:01.000>Hello <00:00:01.400>world\n\n"
            "00:00:04.000 --> 00:00:06.000\n"
            "No timestamps in this cue\n",
            encoding="utf-8",
        )

        # Should NOT raise — returns list (some cues are static)
        cues = _parse_word_vtt(str(vtt), max_words=_MAX_WORDS, max_cues=_MAX_CUES)
        assert len(cues) == 2
        # At least one Python warning should have been emitted
        assert len(recwarn.list) >= 1


# ===========================================================================
# Section 3 — _escape_ass_text (SEC-04 / AC-5)
# ===========================================================================


class TestEscapeAssText:
    r"""_escape_ass_text must be applied BEFORE \k tag generation (SEC-04)."""

    def test_backslash_escaped(self) -> None:
        assert "\\\\" in _escape_ass_text("\\")

    def test_open_brace_escaped(self) -> None:
        assert "\\{" in _escape_ass_text("{")

    def test_close_brace_escaped(self) -> None:
        assert "\\}" in _escape_ass_text("}")

    def test_newline_removed(self) -> None:
        result = _escape_ass_text("hello\nworld")
        assert "\n" not in result
        assert "\r" not in result

    def test_combined_escaping(self) -> None:
        result = _escape_ass_text("{\\k100}hello\nworld")
        assert "\\{" in result
        assert "\\}" in result
        assert "\\\\" in result
        assert "\n" not in result

    def test_plain_text_unchanged(self) -> None:
        assert _escape_ass_text("Hello world") == "Hello world"


# ===========================================================================
# Section 4 — _group_words_into_lines (F-R-03 / U-3)
# ===========================================================================


def _make_words(texts: list[str], base_start: float = 0.0) -> list[_KaraokeWord]:
    """Construct a list of _KaraokeWord with sequential 1-second intervals."""
    words: list[_KaraokeWord] = []
    for i, text in enumerate(texts):
        words.append(
            _KaraokeWord(
                text=text,
                start=base_start + i,
                end=base_start + i + 1.0,
            )
        )
    return words


class TestGroupWordsIntoLines:
    """Greedy char-budget grouping and max_lines overflow."""

    def test_single_short_line_single_event(self) -> None:
        words = _make_words(["Hi", "there"])
        events = _group_words_into_lines(words, chars_per_line=20, max_lines=2)
        # All words fit in one line → one event, one line
        assert len(events) == 1
        assert len(events[0]) == 1
        assert len(events[0][0]) == 2

    def test_greedy_fill_splits_at_budget(self) -> None:
        # "Hello world" = 11 chars > 10 → "Hello" on line 1, "world" on line 2
        words = _make_words(["Hello", "world"])
        events = _group_words_into_lines(words, chars_per_line=7, max_lines=2)
        # "Hello" (5) fits; "Hello world" (11) > 7 → "world" on next line
        assert len(events) == 1
        line_word_counts = [len(line) for line in events[0]]
        assert line_word_counts == [1, 1]

    def test_max_lines_overflow_starts_new_event(self) -> None:
        # 3 words each needing its own line, max_lines=2 → 2 events
        words = _make_words(["Alpha", "Beta", "Gamma"])
        events = _group_words_into_lines(words, chars_per_line=5, max_lines=2)
        # Each word is 5 chars (fits alone), but 3 lines > max_lines=2
        # → first event has 2 lines, second event has 1 line
        assert len(events) == 2
        # First event must have exactly max_lines lines
        assert len(events[0]) == 2
        # Second event has remaining words
        assert len(events[1]) >= 1


# ===========================================================================
# Section 5 — _karaoke_event_text (F-R-02 / ADR-K5)
# ===========================================================================


class TestKaraokeEventText:
    r"""Build ASS \k dialogue body; verify cs drift-zero and \N line joins."""

    def _cue1_words(self) -> list[_KaraokeWord]:
        """Words from canonical fixture cue 1."""
        return [
            _KaraokeWord(text="Hello", start=1.0, end=1.4),
            _KaraokeWord(text="world", start=1.4, end=1.9),
            _KaraokeWord(text="this", start=1.9, end=2.3),
            _KaraokeWord(text="is", start=2.3, end=2.6),
            _KaraokeWord(text="a", start=2.6, end=2.9),
            _KaraokeWord(text="test", start=2.9, end=3.5),
        ]

    def test_k_tag_count_equals_word_count_cue1(self) -> None:
        # 6 words → 6 \k tags
        line_groups = [self._cue1_words()]
        body = _karaoke_event_text(line_groups, event_start=1.0)
        assert body.count("\\k") == 6

    def test_cs_values_cue1(self) -> None:
        r"""cs per word (cumulative boundary diff, event_start=1.0):
        Hello→40, world→50, this→40, is→30, a→30, test→60.
        """
        line_groups = [self._cue1_words()]
        body = _karaoke_event_text(line_groups, event_start=1.0)
        assert "\\k40" in body  # Hello
        assert "\\k50" in body  # world
        assert "\\k60" in body  # test

    def test_cs_sum_equals_event_duration_cue1(self) -> None:
        r"""ADR-K5 drift-zero guarantee: sum of cs == event_duration_cs.

        Event duration = round(3.5*100) - round(1.0*100) = 350 - 100 = 250 cs.
        """
        line_groups = [self._cue1_words()]
        body = _karaoke_event_text(line_groups, event_start=1.0)
        # Extract all \kN values from body (N is digit sequence)
        import re

        cs_values = [int(m) for m in re.findall(r"\\k(\d+)", body)]
        assert sum(cs_values) == 250  # 350 - 100

    def test_cs_sum_equals_event_duration_cue2(self) -> None:
        r"""Drift-zero check for cue 2.

        Event duration = round(6.2*100) - round(4.0*100) = 620 - 400 = 220 cs.
        """
        cue2_words = [
            _KaraokeWord(text="With", start=4.0, end=4.5),
            _KaraokeWord(text="karaoke", start=4.5, end=5.1),
            _KaraokeWord(text="support", start=5.1, end=5.7),
            _KaraokeWord(text="enabled", start=5.7, end=6.2),
        ]
        line_groups = [cue2_words]
        body = _karaoke_event_text(line_groups, event_start=4.0)
        import re

        cs_values = [int(m) for m in re.findall(r"\\k(\d+)", body)]
        assert sum(cs_values) == 220  # 620 - 400

    def test_multiline_uses_backslash_n_separator(self) -> None:
        r"""Multiple lines are joined with \N (ASS hard line break)."""
        line1 = [_KaraokeWord(text="Hello", start=1.0, end=1.5)]
        line2 = [_KaraokeWord(text="world", start=1.5, end=2.0)]
        line_groups = [line1, line2]
        body = _karaoke_event_text(line_groups, event_start=1.0)
        assert "\\N" in body


# ===========================================================================
# Section 6 — _build_karaoke_ass (F-R-01/03/05/06 / ADR-K3/K6)
# ===========================================================================


def _default_subtitle(path: str = "/tmp/words.vtt") -> SubtitleOptions:
    """SubtitleOptions with karaoke defaults and no custom colours."""
    return SubtitleOptions(path=path, karaoke=True)


def _canonical_cues() -> list[_WordCue]:
    """Two _WordCue objects mirroring the canonical fixture."""
    cue1 = _WordCue(
        start=1.0,
        end=3.5,
        words=[
            _KaraokeWord(text="Hello", start=1.0, end=1.4),
            _KaraokeWord(text="world", start=1.4, end=1.9),
            _KaraokeWord(text="this", start=1.9, end=2.3),
            _KaraokeWord(text="is", start=2.3, end=2.6),
            _KaraokeWord(text="a", start=2.6, end=2.9),
            _KaraokeWord(text="test", start=2.9, end=3.5),
        ],
    )
    cue2 = _WordCue(
        start=4.0,
        end=6.2,
        words=[
            _KaraokeWord(text="With", start=4.0, end=4.5),
            _KaraokeWord(text="karaoke", start=4.5, end=5.1),
            _KaraokeWord(text="support", start=5.1, end=5.7),
            _KaraokeWord(text="enabled", start=5.7, end=6.2),
        ],
    )
    return [cue1, cue2]


class TestBuildKaraokeAss:
    """_build_karaoke_ass assembles a complete ASS document."""

    def test_playresx_equals_frame_width(self) -> None:
        # ADR-K3: PlayResX = frame_w → libass scale = 1 (no counter-scale)
        ass = _build_karaoke_ass(_canonical_cues(), _default_subtitle(), 1920, 1080)
        assert "PlayResX: 1920" in ass

    def test_playresy_equals_frame_height(self) -> None:
        ass = _build_karaoke_ass(_canonical_cues(), _default_subtitle(), 1920, 1080)
        assert "PlayResY: 1080" in ass

    def test_playres_is_frame_not_288(self) -> None:
        # ADR-K3: the old PLAYRES_Y_SRT_DEFAULT=288 must NOT appear in karaoke ASS
        ass = _build_karaoke_ass(_canonical_cues(), _default_subtitle(), 1920, 1080)
        assert "PlayResY: 288" not in ass

    def test_default_highlight_primary_colour_yellow(self) -> None:
        # ADR-K6: highlight_color=None → #FFFF00 → &H0000FFFF
        ass = _build_karaoke_ass(_canonical_cues(), _default_subtitle(), 1920, 1080)
        assert _YELLOW_ASS in ass

    def test_explicit_highlight_colour_applied(self) -> None:
        subtitle = SubtitleOptions(
            path="/tmp/words.vtt",
            karaoke=True,
            highlight_color="#FF0000",  # red → &H000000FF
        )
        ass = _build_karaoke_ass(_canonical_cues(), subtitle, 1920, 1080)
        assert "&H000000FF" in ass

    def test_secondary_colour_white_when_font_color_none(self) -> None:
        # ADR-K6: SecondaryColour = font_color (None → white = &H00FFFFFF)
        ass = _build_karaoke_ass(_canonical_cues(), _default_subtitle(), 1920, 1080)
        assert _WHITE_ASS in ass

    def test_secondary_colour_follows_font_color(self) -> None:
        subtitle = SubtitleOptions(
            path="/tmp/words.vtt",
            karaoke=True,
            font_color="#00FF00",  # green → &H0000FF00
        )
        ass = _build_karaoke_ass(_canonical_cues(), subtitle, 1920, 1080)
        assert "&H0000FF00" in ass

    def test_dialogue_start_matches_first_word(self) -> None:
        # Dialogue Start = first word.start in the screen event
        ass = _build_karaoke_ass(_canonical_cues(), _default_subtitle(), 1920, 1080)
        # Cue 1 first word.start = 1.0 s → ASS format 0:00:01.00
        assert "0:00:01.00" in ass

    def test_dialogue_end_matches_last_word(self) -> None:
        # Dialogue End = last word.end in the screen event
        ass = _build_karaoke_ass(_canonical_cues(), _default_subtitle(), 1920, 1080)
        # Cue 1 last word.end = 3.5 s → ASS format 0:00:03.50
        assert "0:00:03.50" in ass

    def test_ass_contains_webvtt_section_header(self) -> None:
        # ASS must begin with [Script Info]
        ass = _build_karaoke_ass(_canonical_cues(), _default_subtitle(), 1920, 1080)
        assert "[Script Info]" in ass

    def test_ass_contains_v4_styles_section(self) -> None:
        # V4+ Styles section required for \k karaoke
        ass = _build_karaoke_ass(_canonical_cues(), _default_subtitle(), 1920, 1080)
        assert "[V4+ Styles]" in ass

    def test_ass_contains_events_section(self) -> None:
        ass = _build_karaoke_ass(_canonical_cues(), _default_subtitle(), 1920, 1080)
        assert "[Events]" in ass

    def test_ass_has_dialogue_lines(self) -> None:
        # With canonical 2 cues and default chars_per_line=42, both fit in one
        # screen event each → at least 2 Dialogue lines
        ass = _build_karaoke_ass(_canonical_cues(), _default_subtitle(), 1920, 1080)
        dialogue_lines = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
        assert len(dialogue_lines) >= 2


# ===========================================================================
# Section 7 — Drift guards for MAX_WORDS / MAX_CUES (CR-M-2)
# ===========================================================================


class TestConstantDriftGuards:
    """Explicit constant-value assertions to detect silent drift (CR-M-2)."""

    def test_max_words_constant_value(self) -> None:
        """MAX_WORDS must equal 50_000 (CWE-400 guard; ADR-K8)."""
        assert MAX_WORDS == 50_000

    def test_max_cues_constant_value(self) -> None:
        """MAX_CUES must equal 10_000 (CWE-400 guard; ADR-K8)."""
        assert MAX_CUES == 10_000

    def test_max_word_vtt_bytes_constant_value(self) -> None:
        """MAX_WORD_VTT_BYTES must equal 10 MB (OOM guard; SR-M-2)."""
        assert MAX_WORD_VTT_BYTES == 10 * 1024 * 1024


# ===========================================================================
# Section 8 — Non-monotonic inline timestamp handling (CR-H-1 / SR-M-3)
# ===========================================================================


_NON_MONOTONIC_VTT = (
    "WEBVTT\n\n"
    "00:00:01.000 --> 00:00:03.500\n"
    # Inline timestamps: 1.0 → 0.5 (non-monotonic!) → 2.0
    "<00:00:01.000>Hello <00:00:00.500>world <00:00:02.000>test\n"
)


class TestNonMonotonicVtt:
    """Non-monotonic inline timestamps must be clamped with a UserWarning (CR-H-1)."""

    def test_non_monotonic_emits_user_warning(
        self, tmp_path: Path, recwarn: pytest.WarningsChecker
    ) -> None:
        """Non-monotonic inline timestamps emit a UserWarning (CR-H-1 / architecture §5)."""
        vtt = tmp_path / "non_mono.vtt"
        vtt.write_text(_NON_MONOTONIC_VTT, encoding="utf-8")

        _parse_word_vtt(str(vtt), max_words=_MAX_WORDS, max_cues=_MAX_CUES)

        # At least one UserWarning must have been emitted
        user_warnings = [w for w in recwarn.list if issubclass(w.category, UserWarning)]
        assert len(user_warnings) >= 1

    def test_non_monotonic_no_negative_k_in_ass(self, tmp_path: Path) -> None:
        r"""Non-monotonic VTT must produce no negative \k tags (libass undefined behaviour)."""
        vtt = tmp_path / "non_mono.vtt"
        vtt.write_text(_NON_MONOTONIC_VTT, encoding="utf-8")

        cues = _parse_word_vtt(str(vtt), max_words=_MAX_WORDS, max_cues=_MAX_CUES)
        ass = _build_karaoke_ass(cues, _default_subtitle(), 1920, 1080)

        # No negative \k value must appear (\\k followed by an optional minus sign)
        negative_k = re.findall(r"\\k-\d+", ass)
        assert not negative_k, (
            f"Generated ASS contains negative \\k tags: {negative_k!r}\n"
            f"  ASS content snippet: {ass[:500]!r}"
        )


# ===========================================================================
# Section 9 — font_name ASS injection guard (SR-M-1)
# ===========================================================================


class TestFontNameInjectionGuard:
    """font_name with newline / CR must be rejected (SR-M-1 / SEC-04)."""

    def test_font_name_with_lf_rejected(self) -> None:
        r"""font_name containing \n is rejected with ValidationError (SR-M-1)."""
        with pytest.raises(ValidationError):
            SubtitleOptions(
                path="/tmp/sub.vtt",
                font_name="Arial\n[Events]\nDialogue: 0,0:00:01.00,0:00:10.00,Default,,0,0,0,,INJECTED",
            )

    def test_font_name_with_cr_rejected(self) -> None:
        r"""font_name containing \r is rejected with ValidationError (SR-M-1)."""
        with pytest.raises(ValidationError):
            SubtitleOptions(path="/tmp/sub.vtt", font_name="Arial\r[Events]\r")

    def test_font_name_with_crlf_rejected(self) -> None:
        r"""font_name containing \r\n is rejected with ValidationError (SR-M-1)."""
        with pytest.raises(ValidationError):
            SubtitleOptions(path="/tmp/sub.vtt", font_name="Arial\r\n[Events]")

    def test_font_name_without_newlines_accepted(self) -> None:
        """font_name without newlines / control chars is still accepted (regression guard)."""
        opts = SubtitleOptions(path="/tmp/sub.vtt", font_name="Arial")
        assert opts.font_name == "Arial"

    def test_font_name_cjk_without_newlines_accepted(self) -> None:
        """CJK font names (no newlines) must continue to be accepted (regression guard)."""
        opts = SubtitleOptions(path="/tmp/sub.vtt", font_name="ヒラギノ角ゴ ProN")
        assert opts.font_name == "ヒラギノ角ゴ ProN"


# ===========================================================================
# Section 10 — OOM guard: file size limit (SR-M-2)
# ===========================================================================


class TestOversizedVttGuard:
    """word-VTT files exceeding MAX_WORD_VTT_BYTES are rejected before reading (SR-M-2)."""

    def test_oversized_vtt_raises_invalid_input(self, tmp_path: Path) -> None:
        """Mocked stat reporting size > MAX_WORD_VTT_BYTES → INVALID_INPUT before read."""
        vtt = tmp_path / "big.vtt"
        vtt.write_text("WEBVTT\n\n", encoding="utf-8")

        mock_stat_result = MagicMock()
        mock_stat_result.st_size = MAX_WORD_VTT_BYTES + 1

        with patch("pathlib.Path.stat", return_value=mock_stat_result):
            with pytest.raises(ClipwrightError) as exc_info:
                _parse_word_vtt(str(vtt), max_words=_MAX_WORDS, max_cues=_MAX_CUES)

        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_oversized_vtt_hint_mentions_size(self, tmp_path: Path) -> None:
        """Hint for oversized VTT must mention the size limit in MB."""
        vtt = tmp_path / "big.vtt"
        vtt.write_text("WEBVTT\n\n", encoding="utf-8")

        mock_stat_result = MagicMock()
        mock_stat_result.st_size = MAX_WORD_VTT_BYTES + 1

        with patch("pathlib.Path.stat", return_value=mock_stat_result):
            with pytest.raises(ClipwrightError) as exc_info:
                _parse_word_vtt(str(vtt), max_words=_MAX_WORDS, max_cues=_MAX_CUES)

        assert "10" in exc_info.value.hint  # "10 MB" in hint


# ===========================================================================
# Section 11 — CWE-209: OSError / UnicodeDecodeError conversion (SR-M-4)
# ===========================================================================


class TestVttReadErrorHandling:
    """OSError / UnicodeDecodeError from read_text must be converted to ClipwrightError
    with basename only to prevent path disclosure (SR-M-4 / CWE-209)."""

    def test_missing_file_raises_clipwright_error(self, tmp_path: Path) -> None:
        """Non-existent VTT path → ClipwrightError(INVALID_INPUT) not raw OSError."""
        missing = str(tmp_path / "subdir" / "missing.vtt")

        with pytest.raises(ClipwrightError) as exc_info:
            _parse_word_vtt(missing, max_words=_MAX_WORDS, max_cues=_MAX_CUES)

        assert exc_info.value.code == ErrorCode.INVALID_INPUT

    def test_missing_file_message_basename_only(self, tmp_path: Path) -> None:
        """Error message for missing VTT must contain basename only (CWE-209)."""
        missing = str(tmp_path / "subdir" / "missing.vtt")

        with pytest.raises(ClipwrightError) as exc_info:
            _parse_word_vtt(missing, max_words=_MAX_WORDS, max_cues=_MAX_CUES)

        # Absolute path must NOT appear in the message
        assert missing not in exc_info.value.message
        # Basename must appear so caller can identify the file
        assert "missing.vtt" in exc_info.value.message

    def test_unicode_error_raises_clipwright_error(self, tmp_path: Path) -> None:
        """Non-UTF-8 VTT file → ClipwrightError(INVALID_INPUT) not raw UnicodeDecodeError."""
        bad_encoding = tmp_path / "latin1.vtt"
        # Write a file with latin-1 bytes that are invalid UTF-8
        bad_encoding.write_bytes(b"WEBVTT\n\n\x80\x81\x82\n")

        with pytest.raises(ClipwrightError) as exc_info:
            _parse_word_vtt(str(bad_encoding), max_words=_MAX_WORDS, max_cues=_MAX_CUES)

        assert exc_info.value.code == ErrorCode.INVALID_INPUT
