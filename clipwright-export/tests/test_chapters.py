"""test_chapters.py — Tests for chapters.py pure logic (adapter-independent).

Target functions (all pure, no real ffmpeg/adapter needed):
  - Chapter                                     dataclass (start_sec: float, title: str)
  - serialize_youtube(chapters) -> tuple[str, list[str]]
  - serialize_ffmetadata(chapters, total_duration_ms) -> str
  - _escape_ffmeta(s) -> str
  - _collect_chapters(tl, marker_kind) -> tuple[list[Chapter], float]

Spec source of truth:
  - architecture-report-20260710-161944.md §7 (チャプター生成仕様) and §9.1
    (test_chapters.py item).
  - requirements-report-20260710-161944.md FR-3, AC-6/AC-7/AC-9.

TDD Red: clipwright_export.chapters does not exist yet (implementation not
started), so every test in this module fails at collection time with
ModuleNotFoundError. This is the expected Red state; chapters.py is
adapter-independent (no otio-cmx3600-adapter / otio-fcpx-xml-adapter
dependency) so this Red test does not need to wait for the Wave 0 spike.

Fixtures are defined inline in this file (no conftest.py dependency), per
the task instruction to avoid a writes collision with the sibling
test-timeline task.

Verification aspects:
  (A) _collect_chapters
      (A-1) Markers collected out of insertion order are returned sorted by
            start_time ascending (get_markers itself does not sort).
      (A-2) Only markers matching marker_kind are collected (others ignored).
      (A-3) Chapter.title is the marker name, verbatim when it needs no
            sanitizing.
      (A-4) Title sanitize: newlines/control chars -> single space each,
            then strip surrounding whitespace (§7.3).
      (A-5) Title sanitize: fully-disallowed title falls back to
            "chapter_{n}" (1-based, post-sort index).
      (A-6) Zero matching markers -> ([], duration_sec); duration is still
            computed from the timeline.
      (A-7) duration_sec reflects Timeline.duration().to_seconds().
  (B) serialize_youtube — time formatting (§7.2)
      (B-1) All chapter times < 3600s -> every line uses MM:SS.
      (B-2) Timeline's final chapter time >= 3600s -> every line
            (including earlier, sub-hour ones) uses H:MM:SS.
      (B-3) Milliseconds are floor-truncated (YouTube is second-precision).
  (C) serialize_youtube — YouTube constraint warnings (§7.6, AC-6/AC-7)
      (C-1) AC-6: first chapter at 00:00, >=3 chapters, all intervals >=10s
            -> no warnings.
      (C-2) AC-7 constraint 1: first chapter != 00:00 -> warning with exact
            hint text.
      (C-3) AC-7 constraint 2: < 3 chapters -> warning with exact hint text.
      (C-4) AC-7 constraint 3: an adjacent interval < 10s -> warning
            reporting the violation count N.
      (C-5) constraint 3 counts every short interval, not just the first.
      (C-6) all three constraints are evaluated independently and can fire
            together.
      (C-7) markers are never fabricated/padded (e.g. no synthetic 00:00
            chapter is inserted when constraint 1 fires); line count always
            equals input chapter count.
      (C-8) AC-9 zero chapters: text == "" (no header); constraint 2 (count
            < 3) still fires since constraints 1/3 have no first-element/
            interval to evaluate.
      (C-9) AC-9 single chapter (at 00:00, per §7.7 wording): only
            constraint 2 fires.
  (D) _escape_ffmeta (§7.4, ffmpeg metadata spec: = ; # \\ and newline)
      (D-1) backslash, =, ;, # are each escaped with a leading backslash.
      (D-2) backslash is escaped first so introduced backslashes from other
            replacements are not re-escaped.
      (D-3) newline is defensively escaped too (even though §7.3 sanitizing
            should prevent it from reaching this function in practice).
      (D-4) strings needing no escaping pass through unchanged.
  (E) serialize_ffmetadata (§7.4/§7.5, AC-8)
      (E-1) Output always starts with the ";FFMETADATA1" header line.
      (E-2) Each chapter becomes one [CHAPTER] block with
            TIMEBASE=1/1000, START, END (ms), and title.
      (E-3) START = round(start_sec * 1000).
      (E-4) END of a non-last chapter = START of the next chapter.
      (E-5) END of the last chapter = total_duration_ms.
      (E-6) title values are escaped via _escape_ffmeta.
      (E-7) Zero chapters -> header only (";FFMETADATA1\\n"), no [CHAPTER]
            block.
      (E-8) Single chapter -> END == total_duration_ms.
      (E-9) START >= END fallback: when the (would-be) END is <= START,
            END = START + 1000 instead (ffmpeg rejects START>=END chapters).
            Triggered both when the last chapter's start exceeds
            total_duration_ms, and when two adjacent chapters share the
            same start_sec.
"""

from __future__ import annotations

import opentimelineio as otio
from clipwright.otio_utils import add_clip, add_marker, new_timeline
from clipwright.schemas import MediaRef, RationalTimeModel, TimeRangeModel

from clipwright_export.chapters import (
    Chapter,
    _collect_chapters,
    _escape_ffmeta,
    serialize_ffmetadata,
    serialize_youtube,
)

# ===========================================================================
# Helpers
# ===========================================================================

FPS = 30.0

# Exact warning texts per architecture-report §7.6.
_MSG_FIRST_NOT_ZERO = (
    "YouTube requires the first chapter to start at 00:00. hint: add a "
    "scene_boundary marker at the timeline start, or edit the first line "
    "to 00:00 before pasting."
)
_MSG_TOO_FEW = (
    "YouTube requires at least 3 chapters to show a chapter list. hint: "
    "detect more scene boundaries with clipwright-scene."
)


def _msg_short_interval(n: int) -> str:
    return (
        f"YouTube requires each chapter to be at least 10 seconds long; "
        f"{n} interval(s) are shorter. hint: merge or remove close markers."
    )


def _build_timeline(
    *,
    rate: float = FPS,
    clip_duration_sec: float = 20.0,
    markers: list[tuple[float, str, str]] | None = None,
) -> otio.schema.Timeline:
    """Build a Timeline with a V1 clip (establishes duration) plus markers.

    markers: list of (start_sec, name, kind) attached as zero-duration
    markers on the V1 track — mirrors clipwright-scene's marker attachment
    pattern (add_marker nests {"kind": kind} under
    marker.metadata["clipwright"]).
    """
    tl = new_timeline(name="test")
    v1 = tl.tracks[0]
    add_clip(
        v1,
        media=MediaRef(target_url="/fake/video.mp4"),
        source_range=TimeRangeModel(
            start_time=RationalTimeModel(value=0.0, rate=rate),
            duration=RationalTimeModel(value=clip_duration_sec * rate, rate=rate),
        ),
    )
    for start_sec, name, kind in markers or []:
        add_marker(
            v1,
            marked_range=TimeRangeModel(
                start_time=RationalTimeModel(value=start_sec * rate, rate=rate),
                duration=RationalTimeModel(value=0.0, rate=rate),
            ),
            name=name,
            metadata={"kind": kind},
        )
    return tl


def _parse_ffmeta_chapters(text: str) -> list[dict[str, str]]:
    """Split ffmetadata text into a list of {key: value} dicts per block.

    Robust to exact whitespace/blank-line formatting choices; only requires
    "[CHAPTER]" markers and "KEY=VALUE" lines within each block.
    """
    blocks = text.split("[CHAPTER]")[1:]
    parsed: list[dict[str, str]] = []
    for block in blocks:
        fields: dict[str, str] = {}
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            fields[key] = value
        parsed.append(fields)
    return parsed


# ===========================================================================
# (A) _collect_chapters
# ===========================================================================


class TestCollectChapters:
    def test_sorts_by_start_time_ascending(self) -> None:
        """(A-1) get_markers does not sort; _collect_chapters must."""
        tl = _build_timeline(
            markers=[
                (10.0, "C", "scene_boundary"),
                (2.0, "A", "scene_boundary"),
                (7.0, "B", "scene_boundary"),
            ]
        )
        chapters, _duration_sec = _collect_chapters(tl, "scene_boundary")
        assert [c.start_sec for c in chapters] == [2.0, 7.0, 10.0]
        assert [c.title for c in chapters] == ["A", "B", "C"]

    def test_filters_by_marker_kind(self) -> None:
        """(A-2) Markers of a different kind are excluded."""
        tl = _build_timeline(
            markers=[
                (2.0, "A", "scene_boundary"),
                (5.0, "cap1", "caption"),
            ]
        )
        chapters, _duration_sec = _collect_chapters(tl, "scene_boundary")
        assert len(chapters) == 1
        assert chapters[0].title == "A"

    def test_title_is_marker_name_verbatim(self) -> None:
        """(A-3) A clean marker name passes through unchanged."""
        tl = _build_timeline(markers=[(0.0, "scene_1", "scene_boundary")])
        chapters, _duration_sec = _collect_chapters(tl, "scene_boundary")
        assert chapters[0].title == "scene_1"

    def test_sanitizes_newlines_and_control_chars(self) -> None:
        """(A-4) \\n and control chars each become a single space.

        Uses \\x01 rather than \\x00: the OTIO C++ binding truncates marker
        names at an embedded null byte (Marker(name=...) construction time),
        so a \\x00 fixture would never reach _collect_chapters intact. \\x01
        passes through OTIO unmodified and still exercises the same
        control-char-to-space branch in _sanitize_title.
        """
        tl = _build_timeline(markers=[(0.0, "Hello\nWorld\x01Foo", "scene_boundary")])
        chapters, _duration_sec = _collect_chapters(tl, "scene_boundary")
        assert chapters[0].title == "Hello World Foo"

    def test_sanitize_strips_surrounding_whitespace(self) -> None:
        """(A-4) Result is stripped of leading/trailing whitespace."""
        tl = _build_timeline(markers=[(0.0, "\n Padded \r", "scene_boundary")])
        chapters, _duration_sec = _collect_chapters(tl, "scene_boundary")
        assert chapters[0].title == "Padded"

    def test_fully_disallowed_title_falls_back_to_chapter_n(self) -> None:
        """(A-5) Empty-after-sanitize title -> "chapter_{n}" (1-based)."""
        tl = _build_timeline(
            markers=[
                (0.0, "\n\x00\r", "scene_boundary"),
                (5.0, "\x01\x02", "scene_boundary"),
            ]
        )
        chapters, _duration_sec = _collect_chapters(tl, "scene_boundary")
        assert chapters[0].title == "chapter_1"
        assert chapters[1].title == "chapter_2"

    def test_zero_matching_markers_returns_empty_list_and_duration(self) -> None:
        """(A-6) No matches -> ([], duration_sec); duration still computed."""
        tl = _build_timeline(clip_duration_sec=20.0, markers=[])
        chapters, duration_sec = _collect_chapters(tl, "scene_boundary")
        assert chapters == []
        assert duration_sec == 20.0

    def test_duration_reflects_timeline_duration(self) -> None:
        """(A-7) duration_sec == Timeline.duration().to_seconds()."""
        tl = _build_timeline(clip_duration_sec=45.5, markers=[(0.0, "A", "x")])
        _chapters, duration_sec = _collect_chapters(tl, "x")
        assert duration_sec == tl.duration().to_seconds()
        assert duration_sec == 45.5


# ===========================================================================
# (B) serialize_youtube — time formatting
# ===========================================================================


class TestSerializeYoutubeTimeFormat:
    def test_mm_ss_below_one_hour_boundary(self) -> None:
        """(B-1) Final chapter at 3599s -> every line uses MM:SS."""
        chapters = [
            Chapter(start_sec=0.0, title="Intro"),
            Chapter(start_sec=1800.0, title="Middle"),
            Chapter(start_sec=3599.0, title="End"),
        ]
        text, warnings = serialize_youtube(chapters)
        assert text.splitlines() == [
            "00:00 Intro",
            "30:00 Middle",
            "59:59 End",
        ]
        assert warnings == []

    def test_hh_mm_ss_at_one_hour_boundary(self) -> None:
        """(B-2) Final chapter at exactly 3600s -> every line uses H:MM:SS,
        including chapters that individually are under an hour."""
        chapters = [
            Chapter(start_sec=0.0, title="A"),
            Chapter(start_sec=1800.0, title="B"),
            Chapter(start_sec=3600.0, title="C"),
        ]
        text, warnings = serialize_youtube(chapters)
        assert text.splitlines() == [
            "0:00:00 A",
            "0:30:00 B",
            "1:00:00 C",
        ]
        assert warnings == []

    def test_floor_truncates_fractional_seconds(self) -> None:
        """(B-3) YouTube is second-precision; ms are floor-truncated."""
        chapters = [
            Chapter(start_sec=0.0, title="A"),
            Chapter(start_sec=15.9, title="B"),
            Chapter(start_sec=90.7, title="C"),
        ]
        text, _warnings = serialize_youtube(chapters)
        assert text.splitlines() == [
            "00:00 A",
            "00:15 B",
            "01:30 C",
        ]


# ===========================================================================
# (C) serialize_youtube — YouTube constraint warnings
# ===========================================================================


class TestSerializeYoutubeConstraints:
    def test_ac6_no_violations(self) -> None:
        """(C-1) First at 00:00, >=3 chapters, all intervals >=10s."""
        chapters = [
            Chapter(start_sec=0.0, title="A"),
            Chapter(start_sec=15.0, title="B"),
            Chapter(start_sec=30.0, title="C"),
        ]
        _text, warnings = serialize_youtube(chapters)
        assert warnings == []

    def test_ac7_first_chapter_not_at_zero(self) -> None:
        """(C-2) Only constraint 1 fires (count and intervals are fine)."""
        chapters = [
            Chapter(start_sec=5.0, title="A"),
            Chapter(start_sec=20.0, title="B"),
            Chapter(start_sec=35.0, title="C"),
        ]
        _text, warnings = serialize_youtube(chapters)
        assert warnings == [_MSG_FIRST_NOT_ZERO]

    def test_ac7_fewer_than_three_chapters(self) -> None:
        """(C-3) Only constraint 2 fires (first is 00:00, interval is fine)."""
        chapters = [
            Chapter(start_sec=0.0, title="A"),
            Chapter(start_sec=20.0, title="B"),
        ]
        _text, warnings = serialize_youtube(chapters)
        assert warnings == [_MSG_TOO_FEW]

    def test_ac7_interval_shorter_than_ten_seconds(self) -> None:
        """(C-4) Only constraint 3 fires; N=1 short interval reported."""
        chapters = [
            Chapter(start_sec=0.0, title="A"),
            Chapter(start_sec=5.0, title="B"),
            Chapter(start_sec=20.0, title="C"),
        ]
        _text, warnings = serialize_youtube(chapters)
        assert warnings == [_msg_short_interval(1)]

    def test_interval_violation_counts_all_short_intervals(self) -> None:
        """(C-5) N reflects every short interval, not just the first."""
        chapters = [
            Chapter(start_sec=0.0, title="A"),
            Chapter(start_sec=3.0, title="B"),
            Chapter(start_sec=6.0, title="C"),
            Chapter(start_sec=20.0, title="D"),
        ]
        _text, warnings = serialize_youtube(chapters)
        assert warnings == [_msg_short_interval(2)]

    def test_all_three_violations_reported_independently(self) -> None:
        """(C-6) Constraints are independent and can fire together, in
        constraint-1/2/3 order."""
        chapters = [
            Chapter(start_sec=5.0, title="A"),
            Chapter(start_sec=8.0, title="B"),
        ]
        _text, warnings = serialize_youtube(chapters)
        assert warnings == [
            _MSG_FIRST_NOT_ZERO,
            _MSG_TOO_FEW,
            _msg_short_interval(1),
        ]

    def test_violation_does_not_fabricate_or_pad_chapters(self) -> None:
        """(C-7) No synthetic 00:00 chapter is inserted; line count is
        exactly the input chapter count even when constraint 1 fires."""
        chapters = [
            Chapter(start_sec=5.0, title="A"),
            Chapter(start_sec=20.0, title="B"),
            Chapter(start_sec=35.0, title="C"),
        ]
        text, _warnings = serialize_youtube(chapters)
        assert len(text.splitlines()) == len(chapters)
        assert text.splitlines()[0] == "00:05 A"

    def test_ac9_zero_chapters(self) -> None:
        """(C-8) Empty text; constraint 2 (count<3) still fires since 0<3
        even though there is no first element/interval to evaluate."""
        text, warnings = serialize_youtube([])
        assert text == ""
        assert warnings == [_MSG_TOO_FEW]

    def test_ac9_single_chapter_at_zero(self) -> None:
        """(C-9) §7.7: a lone chapter at 00:00 triggers only constraint 2
        (no "first != 00:00" violation, no interval to evaluate)."""
        chapters = [Chapter(start_sec=0.0, title="Solo")]
        text, warnings = serialize_youtube(chapters)
        assert text == "00:00 Solo"
        assert warnings == [_MSG_TOO_FEW]


# ===========================================================================
# (D) _escape_ffmeta
# ===========================================================================


class TestEscapeFfmeta:
    def test_escapes_backslash_equals_semicolon_hash(self) -> None:
        """(D-1)/(D-2) backslash escaped first so introduced backslashes
        from other substitutions are not re-escaped."""
        assert _escape_ffmeta("a=b;c#d\\e") == "a\\=b\\;c\\#d\\\\e"

    def test_escapes_newline_defensively(self) -> None:
        """(D-3) Defensive newline handling per §7.4."""
        assert _escape_ffmeta("a\nb") == "a\\\nb"

    def test_passthrough_when_nothing_needs_escaping(self) -> None:
        """(D-4)"""
        assert _escape_ffmeta("Plain Title") == "Plain Title"


# ===========================================================================
# (E) serialize_ffmetadata
# ===========================================================================


class TestSerializeFfmetadata:
    def test_header_present(self) -> None:
        """(E-1)"""
        text = serialize_ffmetadata([], total_duration_ms=0)
        assert text.startswith(";FFMETADATA1\n")

    def test_chapter_blocks_start_end_title(self) -> None:
        """(E-2)/(E-3)/(E-4)/(E-5) Three chapters; last END = total duration."""
        chapters = [
            Chapter(start_sec=0.0, title="Intro"),
            Chapter(start_sec=5.5, title="Middle"),
            Chapter(start_sec=12.0, title="End"),
        ]
        text = serialize_ffmetadata(chapters, total_duration_ms=20000)
        parsed = _parse_ffmeta_chapters(text)
        assert len(parsed) == 3
        assert parsed[0]["TIMEBASE"] == "1/1000"
        assert parsed[0]["START"] == "0"
        assert parsed[0]["END"] == "5500"
        assert parsed[0]["title"] == "Intro"
        assert parsed[1]["START"] == "5500"
        assert parsed[1]["END"] == "12000"
        assert parsed[1]["title"] == "Middle"
        assert parsed[2]["START"] == "12000"
        assert parsed[2]["END"] == "20000"
        assert parsed[2]["title"] == "End"

    def test_start_ms_rounds_from_seconds(self) -> None:
        """(E-3) START = round(start_sec * 1000); pick a non-.5 fraction to
        avoid Python banker's-rounding ambiguity."""
        chapters = [
            Chapter(start_sec=1.2346, title="A"),
            Chapter(start_sec=3.0, title="B"),
        ]
        text = serialize_ffmetadata(chapters, total_duration_ms=5000)
        parsed = _parse_ffmeta_chapters(text)
        assert parsed[0]["START"] == "1235"

    def test_escapes_title_special_chars(self) -> None:
        """(E-6)"""
        chapters = [Chapter(start_sec=0.0, title="a=b;c#d\\e")]
        text = serialize_ffmetadata(chapters, total_duration_ms=1000)
        parsed = _parse_ffmeta_chapters(text)
        assert parsed[0]["title"] == _escape_ffmeta("a=b;c#d\\e")
        assert parsed[0]["title"] == "a\\=b\\;c\\#d\\\\e"

    def test_zero_chapters_header_only(self) -> None:
        """(E-7)"""
        text = serialize_ffmetadata([], total_duration_ms=0)
        assert text == ";FFMETADATA1\n"

    def test_single_chapter_end_is_total_duration(self) -> None:
        """(E-8)"""
        chapters = [Chapter(start_sec=5.0, title="Only")]
        text = serialize_ffmetadata(chapters, total_duration_ms=20000)
        parsed = _parse_ffmeta_chapters(text)
        assert len(parsed) == 1
        assert parsed[0]["START"] == "5000"
        assert parsed[0]["END"] == "20000"
        assert parsed[0]["title"] == "Only"

    def test_start_gte_end_fallback_when_last_chapter_exceeds_duration(
        self,
    ) -> None:
        """(E-9) Last chapter's START (5000ms) exceeds total_duration_ms
        (3000ms) -> END = START + 1000 instead of the (invalid) duration."""
        chapters = [
            Chapter(start_sec=0.0, title="A"),
            Chapter(start_sec=5.0, title="END"),
        ]
        text = serialize_ffmetadata(chapters, total_duration_ms=3000)
        parsed = _parse_ffmeta_chapters(text)
        assert parsed[0]["START"] == "0"
        assert parsed[0]["END"] == "5000"
        assert parsed[1]["START"] == "5000"
        assert parsed[1]["END"] == "6000"

    def test_start_gte_end_fallback_when_duplicate_timestamps(self) -> None:
        """(E-9) Two adjacent chapters share the same start_sec -> the
        computed END (== next START) equals START -> fallback applies to
        the earlier one only."""
        chapters = [
            Chapter(start_sec=0.0, title="A"),
            Chapter(start_sec=0.0, title="B"),
            Chapter(start_sec=10.0, title="C"),
        ]
        text = serialize_ffmetadata(chapters, total_duration_ms=20000)
        parsed = _parse_ffmeta_chapters(text)
        assert parsed[0]["START"] == "0"
        assert parsed[0]["END"] == "1000"
        assert parsed[1]["START"] == "0"
        assert parsed[1]["END"] == "10000"
        assert parsed[2]["START"] == "10000"
        assert parsed[2]["END"] == "20000"
