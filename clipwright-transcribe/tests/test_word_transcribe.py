"""test_word_transcribe.py — Tests for transcribe.py wiring + schema (s1-transcribe).

Verifies the word_timestamps feature in transcribe.py / schemas.py:
  - TranscribeOptions.word_timestamps: bool = False (F-T-01 / extra='forbid' maintained)
  - _build_whisper_cmd emits '-ojf' (full JSON with tokens[]) not bare '-oj' (ADR-K2)
  - WhisperRun._fields includes 'words' (architecture §2-1)
  - _transcribe_inner writes <stem>.words.vtt, populates OTIO words metadata,
    and mentions word count in summary when word_timestamps=True (F-T-05 / F-T-06)

Existing tests (test_transcribe.py / test_captions.py / test_word_captions.py) are not
affected — this module is purely additive.  No production source files are edited here.

Coverage:
  F-T-01 (word_timestamps schema) / F-T-05 (artifacts) / F-T-06 (OTIO words metadata)
  AC-2 (regression: word_timestamps=False path unchanged)
  AC-3 (partial: OTIO words present when word_timestamps=True)
  SEC-02 (error basename-only / from None)
  ADR-K2: -ojf flag, identical command for false/true
  ADR-K8: version '0.5.1' in words OTIO metadata (drift guard for REL-01)
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_transcribe.captions import Segment, normalize_segments, to_srt, to_vtt
from clipwright_transcribe.schemas import TranscribeOptions
from clipwright_transcribe.transcribe import (
    WhisperRun,
    _build_whisper_cmd,
    transcribe_media,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FPS = 30.0

# ---------------------------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------------------------

# Plain-dict word segments (mirrors WordSegment TypedDict schema, architecture §2-1).
# Used to mock _run_whisper.words without importing WordSegment from captions.py.
_FAKE_WORD_SEGMENTS: list[dict[str, Any]] = [
    {
        "start_sec": 0.0,
        "end_sec": 1.0,
        "text": "Hello world.",
        "words": [
            {"text": "Hello", "start_sec": 0.0, "end_sec": 0.5},
            {"text": "world.", "start_sec": 0.5, "end_sec": 1.0},
        ],
    }
]

_FAKE_SEGMENTS: list[Segment] = [
    {"start_sec": 0.0, "end_sec": 1.0, "text": "Hello world."}
]

_REGRESSION_SEGMENTS: list[Segment] = [
    {"start_sec": 0.0, "end_sec": 1.0, "text": "Hello."},
    {"start_sec": 1.5, "end_sec": 2.5, "text": "World."},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_media_info(
    path: str = "/fake/video.mp4",
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
) -> MediaInfo:
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=duration_sec * rate, rate=rate),
        streams=[
            StreamInfo(index=0, codec_type="video", codec_name="h264"),
            StreamInfo(index=1, codec_type="audio", codec_name="aac"),
        ],
        bit_rate=8_000_000,
    )


def _make_paths(tmp_path: Path) -> tuple[str, str, str]:
    """Return (media, output, model) paths; media and model are written as real files."""
    media = tmp_path / "video.mp4"
    media.write_bytes(b"fake")
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"fake-model")
    output = tmp_path / "out.otio"
    return str(media), str(output), str(model)


def _opts(**kwargs: Any) -> TranscribeOptions:
    return TranscribeOptions(**kwargs)


def _mock_run_with_words(
    segments: list[Segment] | None = None,
    words: list[dict[str, Any]] | None = None,
) -> Any:
    """Build a SimpleNamespace mimicking WhisperRun with word segments for testing.

    SimpleNamespace lets us set arbitrary word data without relying on the real
    WhisperRun constructor for mocking purposes.
    """
    return SimpleNamespace(
        segments=segments if segments is not None else _FAKE_SEGMENTS,
        language="en",
        backend={"device": "cpu", "detail": "cpu"},
        wall_seconds=1.0,
        words=words if words is not None else _FAKE_WORD_SEGMENTS,
    )


def _mock_run_no_words(segments: list[Segment] | None = None) -> Any:
    """Build a SimpleNamespace mimicking WhisperRun with .words=[] (False path)."""
    return SimpleNamespace(
        segments=segments if segments is not None else _REGRESSION_SEGMENTS,
        language="en",
        backend={"device": "cpu", "detail": "cpu"},
        wall_seconds=1.0,
        words=[],
    )


# ===========================================================================
# 1. Schema: TranscribeOptions.word_timestamps: bool = False
# ===========================================================================


class TestWordTimestampsSchema:
    """TranscribeOptions must accept an additive word_timestamps: bool = False field.

    F-T-01 / architecture §2-1 schemas.py.
    extra="forbid" must be maintained (SR L-1 allowlist policy).
    """

    def test_word_timestamps_default_is_false(self) -> None:
        """TranscribeOptions() default must expose word_timestamps == False.

        F-T-01: word_timestamps is an additive optional field defaulting to False.
        """
        opts = TranscribeOptions()
        # word_timestamps defaults to False (F-T-01)
        assert opts.word_timestamps is False

    def test_word_timestamps_explicit_false(self) -> None:
        """TranscribeOptions(word_timestamps=False) must be accepted by the model.

        F-T-01: explicit False is accepted and round-trips correctly.
        """
        opts = _opts(word_timestamps=False)
        assert opts.word_timestamps is False

    def test_word_timestamps_explicit_true(self) -> None:
        """TranscribeOptions(word_timestamps=True) must be accepted by the model.

        F-T-01: explicit True is accepted and round-trips correctly.
        """
        opts = _opts(word_timestamps=True)
        assert opts.word_timestamps is True

    def test_extra_forbid_still_rejects_unknown_fields(self) -> None:
        """extra='forbid' must still reject completely unrecognised fields.

        Regression guard: adding word_timestamps must not accidentally relax
        the extra='forbid' policy (SR L-1 / architecture §2-1).
        Policy invariant that must hold regardless of implementation.
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TranscribeOptions(totally_unknown_field="x")  # type: ignore[call-arg]


# ===========================================================================
# 2. Command: _build_whisper_cmd must emit -ojf (spike §2-2 / ADR-K2)
# ===========================================================================


class TestBuildWhisperCmdOjf:
    """-ojf (--output-json-full) must replace bare -oj in _build_whisper_cmd.

    spike-whisper-word.md §2-2: '-oj' output omits tokens[]; '-ojf' includes them.
    ADR-K2 first candidate: tokens[] are obtained from a single run; the command
    is identical for word_timestamps=false and word_timestamps=true.
    """

    def test_ojf_flag_in_default_command(self) -> None:
        """-ojf must appear as a command-list element with default TranscribeOptions.

        ADR-K2: '-ojf' (full JSON) replaces bare '-oj' to include tokens[].
        """
        opts = TranscribeOptions()
        cmd = _build_whisper_cmd("whisper", "m.bin", "audio.wav", "/tmp/p", opts)
        assert "-ojf" in cmd, f"Expected '-ojf' in command but got: {cmd}"

    def test_bare_oj_not_in_command(self) -> None:
        """Bare '-oj' (without 'f') must NOT appear as a command-list element.

        ADR-K2: '-ojf' is the sole JSON flag; bare '-oj' must not be present.
        """
        opts = TranscribeOptions()
        cmd = _build_whisper_cmd("whisper", "m.bin", "audio.wav", "/tmp/p", opts)
        assert "-oj" not in cmd, (
            f"Bare '-oj' found in command; must be replaced with '-ojf': {cmd}"
        )

    def test_same_command_for_word_timestamps_false_and_true(self) -> None:
        """word_timestamps=False and True must produce identical command arrays.

        ADR-K2 first-candidate structural guarantee: tokens[] are extracted from the
        single -ojf JSON; no extra whisper run is needed for word output.
        """
        opts_false = _opts(word_timestamps=False)
        opts_true = _opts(word_timestamps=True)
        cmd_false = _build_whisper_cmd("w", "m.bin", "a.wav", "/p", opts_false)
        cmd_true = _build_whisper_cmd("w", "m.bin", "a.wav", "/p", opts_true)
        assert cmd_false == cmd_true, (
            "ADR-K2: command arrays must be identical for false/true.\n"
            f"  false: {cmd_false}\n"
            f"  true:  {cmd_true}"
        )


# ===========================================================================
# 3. WhisperRun.words field
# ===========================================================================


class TestWhisperRunWordsField:
    """WhisperRun (NamedTuple) must have a .words field (architecture §2-1).

    architecture §2-1: words: list[WordSegment] added additively to WhisperRun.
    """

    def test_whisper_run_has_words_field(self) -> None:
        """'words' must be in WhisperRun._fields (architecture §2-1).

        words: list[WordSegment] is added additively to the NamedTuple.
        """
        assert "words" in WhisperRun._fields, (
            f"WhisperRun._fields={WhisperRun._fields!r} — 'words' field is absent"
        )


# ===========================================================================
# 4. word_timestamps=True: integration (words.vtt artifact + OTIO + summary)
# ===========================================================================


class TestWordTimestampsTrueIntegration:
    """_transcribe_inner must emit words.vtt, include it in artifacts, update summary,
    and store words in OTIO metadata when word_timestamps=True.

    F-T-05 / F-T-06 / AC-3 (partial).
    _run_whisper is mocked (no real whisper binary required).
    """

    def _run_with_words(
        self,
        tmp_path: Path,
        words: list[dict[str, Any]] | None = None,
    ) -> tuple[Any, Path]:
        media, output, model = _make_paths(tmp_path)
        with (
            patch(
                "clipwright_transcribe.transcribe.inspect_media",
                return_value=_make_media_info(media),
            ),
            patch(
                "clipwright_transcribe.transcribe._run_whisper",
                return_value=_mock_run_with_words(words=words),
            ),
        ):
            result = transcribe_media(
                media,
                output,
                _opts(model_path=model, word_timestamps=True),
            )
        return result, Path(output)

    def test_words_vtt_file_written(self, tmp_path: Path) -> None:
        """<stem>.words.vtt must be written to output_path.parent (F-T-05)."""
        result, output_path = self._run_with_words(tmp_path)
        assert result.ok is True, f"transcribe_media failed: {result.error}"
        words_vtt = output_path.parent / (output_path.stem + ".words.vtt")
        assert words_vtt.exists(), (
            f"Expected {words_vtt.name} to be written but file does not exist"
        )

    def test_words_vtt_in_artifacts(self, tmp_path: Path) -> None:
        """Artifacts list must include words.vtt when word_timestamps=True (F-T-05)."""
        result, output_path = self._run_with_words(tmp_path)
        assert result.ok is True, f"transcribe_media failed: {result.error}"
        artifact_paths = [a.path for a in result.artifacts]
        expected = str(output_path.parent / (output_path.stem + ".words.vtt"))
        assert expected in artifact_paths, (
            f"words.vtt not in artifacts.\n"
            f"  Expected: {expected}\n"
            f"  Got: {artifact_paths}"
        )

    def test_four_artifacts_when_word_timestamps_true(self, tmp_path: Path) -> None:
        """Exactly 4 artifacts must be present when word_timestamps=True.

        Expected: timeline (otio) + captions (srt) + captions (vtt) + words-captions (vtt).
        """
        result, _ = self._run_with_words(tmp_path)
        assert result.ok is True, f"transcribe_media failed: {result.error}"
        assert len(result.artifacts) == 4, (
            f"Expected 4 artifacts but got {len(result.artifacts)}: "
            f"{[a.path for a in result.artifacts]}"
        )

    def test_summary_contains_word_count(self, tmp_path: Path) -> None:
        """Summary must mention word count when word_timestamps=True (F-T-05).

        _FAKE_WORD_SEGMENTS contains 2 words total.
        """
        result, _ = self._run_with_words(tmp_path)
        assert result.ok is True, f"transcribe_media failed: {result.error}"
        summary = result.summary
        assert summary is not None
        assert "word" in summary.lower(), (
            f"Expected word count in summary but got: {summary!r}"
        )

    def test_otio_clip_has_words_metadata(self, tmp_path: Path) -> None:
        """OTIO source clip must have metadata['clipwright']['words'] when True (F-T-06).

        architecture §2-1: words stored under metadata['clipwright']['words'] on the
        source clip (or as a dedicated marker — check clip first, then markers).
        """
        result, output_path = self._run_with_words(tmp_path)
        assert result.ok is True, f"transcribe_media failed: {result.error}"
        timeline = otio.adapters.read_from_file(str(output_path))
        v1 = timeline.tracks[0]
        clips = [c for c in v1 if isinstance(c, otio.schema.Clip)]
        assert len(clips) == 1, "Expected exactly one clip on V1 track"
        cw = clips[0].metadata.get("clipwright", {})
        has_words_on_clip = "words" in cw
        # Fallback: check markers
        has_words_on_marker = any(
            "words" in m.metadata.get("clipwright", {}) for m in v1.markers
        )
        assert has_words_on_clip or has_words_on_marker, (
            "metadata['clipwright']['words'] not found on source clip or any marker.\n"
            f"  Clip clipwright metadata: {cw!r}"
        )

    def test_otio_clip_words_metadata_version(self, tmp_path: Path) -> None:
        """metadata['clipwright']['version'] must be '0.5.1' in OTIO (drift guard REL-01).

        ADR-K8: version field in clipwright OTIO metadata must match the released
        package version. This drift guard catches a version bump in pyproject.toml
        that is not reflected in __version__ (and thus not propagated to OTIO output).
        """
        result, output_path = self._run_with_words(tmp_path)
        assert result.ok is True, f"transcribe_media failed: {result.error}"
        timeline = otio.adapters.read_from_file(str(output_path))
        v1 = timeline.tracks[0]
        clips = [c for c in v1 if isinstance(c, otio.schema.Clip)]
        assert len(clips) == 1, "Expected exactly one clip on V1 track"
        cw = clips[0].metadata.get("clipwright", {})
        assert cw.get("version") == "0.5.1", (
            f"metadata['clipwright']['version'] must be '0.5.1' (drift guard REL-01), "
            f"got {cw.get('version')!r}"
        )


# ===========================================================================
# 5. AC-2 regression: word_timestamps=False leaves SRT/VTT/OTIO unchanged
# ===========================================================================


class TestWordTimestampsFalseRegression:
    """word_timestamps=False must produce unchanged segment output and no words.vtt.

    AC-2 / architecture §2-1: "既定 false 時は上記ブロック非到達 -> SRT/VTT/OTIO・artifacts
    いずれも従来どおり".  The False path must be byte-identical to pre-feature behaviour.
    """

    def _run_false(self, tmp_path: Path) -> tuple[Any, Path]:
        media, output, model = _make_paths(tmp_path)
        with (
            patch(
                "clipwright_transcribe.transcribe.inspect_media",
                return_value=_make_media_info(media),
            ),
            patch(
                "clipwright_transcribe.transcribe._run_whisper",
                return_value=_mock_run_no_words(),
            ),
        ):
            result = transcribe_media(
                media,
                output,
                _opts(model_path=model, word_timestamps=False),
            )
        return result, Path(output)

    def test_no_words_vtt_artifact_when_false(self, tmp_path: Path) -> None:
        """words.vtt must NOT be in artifacts when word_timestamps=False (AC-2)."""
        result, output_path = self._run_false(tmp_path)
        assert result.ok is True, f"transcribe_media failed: {result.error}"
        artifact_paths = [a.path for a in result.artifacts]
        words_vtt = str(output_path.parent / (output_path.stem + ".words.vtt"))
        assert words_vtt not in artifact_paths, (
            "words.vtt must not appear in artifacts when word_timestamps=False (AC-2)"
        )

    def test_words_vtt_file_not_written_when_false(self, tmp_path: Path) -> None:
        """<stem>.words.vtt file must NOT exist on disk when word_timestamps=False."""
        result, output_path = self._run_false(tmp_path)
        assert result.ok is True, f"transcribe_media failed: {result.error}"
        words_vtt = output_path.parent / (output_path.stem + ".words.vtt")
        assert not words_vtt.exists(), (
            f"{words_vtt.name} must not be written when word_timestamps=False (AC-2)"
        )

    def test_exactly_three_artifacts_when_false(self, tmp_path: Path) -> None:
        """Artifacts must be exactly [timeline, srt, vtt] (3 total) when False (AC-2)."""
        result, _ = self._run_false(tmp_path)
        assert result.ok is True, f"transcribe_media failed: {result.error}"
        assert len(result.artifacts) == 3, (
            f"Expected 3 artifacts (otio + srt + vtt) when word_timestamps=False "
            f"but got {len(result.artifacts)}: {[a.path for a in result.artifacts]}"
        )

    def test_command_same_for_false_as_for_true(self) -> None:
        """_build_whisper_cmd must produce the same array for False as for True.

        Core ADR-K2 first-candidate guarantee: segment JSON (-ojf) is shared;
        word extraction is post-run; the whisper invocation itself is invariant.
        """
        opts_false = _opts(word_timestamps=False)
        opts_true = _opts(word_timestamps=True)
        cmd_false = _build_whisper_cmd("w", "m.bin", "a.wav", "/p", opts_false)
        cmd_true = _build_whisper_cmd("w", "m.bin", "a.wav", "/p", opts_true)
        assert cmd_false == cmd_true, (
            "ADR-K2: word_timestamps=false/true must produce identical command.\n"
            f"  false: {cmd_false}\n"
            f"  true:  {cmd_true}"
        )


# ===========================================================================
# 6. normalize_segments regression — -ojf JSON is safe for segment output
# ===========================================================================


class TestNormalizeSegmentsOjfRegressionGuard:
    """Regression guard: normalize_segments on -ojf JSON must produce correct segments.

    -ojf adds tokens[] to each transcription item but leaves offsets/text unchanged.
    This confirms that switching _build_whisper_cmd from -oj to -ojf (s1-transcribe-impl)
    does NOT alter the SRT/VTT output consumed by render (AC-2 structural guarantee).

    Fixture: whisper_word_sample.json (spike §7, -ojf format, 3 known segments).
    These tests use only captions.normalize_segments / to_srt / to_vtt — all
    implemented — serving as regression guards.
    """

    @pytest.fixture(scope="class")
    def whisper_word_json(self) -> dict[str, Any]:
        path = FIXTURES_DIR / "whisper_word_sample.json"
        with path.open(encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
        return data

    def test_normalize_segments_count_three(
        self, whisper_word_json: dict[str, Any]
    ) -> None:
        """whisper_word_sample.json yields exactly 3 segments from normalize_segments."""
        segments = normalize_segments(whisper_word_json)
        assert len(segments) == 3

    def test_segment_texts_present_and_stripped(
        self, whisper_word_json: dict[str, Any]
    ) -> None:
        """normalize_segments strips leading space; all 3 segment texts are present."""
        segments = normalize_segments(whisper_word_json)
        texts = [s["text"] for s in segments]
        assert "Okay." in texts
        assert "I'm going to go." in texts
        assert "Oh, I'm going to come." in texts

    def test_srt_contains_all_segment_texts(
        self, whisper_word_json: dict[str, Any]
    ) -> None:
        """to_srt on -ojf fixture contains all 3 segment texts."""
        segments = normalize_segments(whisper_word_json)
        srt = to_srt(segments)
        assert "Okay." in srt
        assert "I'm going to go." in srt
        assert "Oh, I'm going to come." in srt

    def test_vtt_contains_all_segment_texts(
        self, whisper_word_json: dict[str, Any]
    ) -> None:
        """to_vtt on -ojf fixture contains all 3 segment texts."""
        segments = normalize_segments(whisper_word_json)
        vtt = to_vtt(segments)
        assert "Okay." in vtt
        assert "I'm going to go." in vtt
        assert "Oh, I'm going to come." in vtt

    def test_srt_uses_comma_millisecond_separator(
        self, whisper_word_json: dict[str, Any]
    ) -> None:
        """SRT timecodes use ',' separator (DC-AS-005 / format consistency)."""
        import re

        segments = normalize_segments(whisper_word_json)
        srt = to_srt(segments)
        assert re.search(r"\d{2}:\d{2}:\d{2},\d{3}", srt), (
            "SRT must use ',' as millisecond separator"
        )

    def test_vtt_uses_dot_millisecond_separator(
        self, whisper_word_json: dict[str, Any]
    ) -> None:
        """VTT timecodes use '.' separator (DC-AS-005 / WebVTT spec)."""
        import re

        segments = normalize_segments(whisper_word_json)
        vtt = to_vtt(segments)
        assert re.search(r"\d{2}:\d{2}:\d{2}\.\d{3}", vtt), (
            "VTT must use '.' as millisecond separator"
        )


# ===========================================================================
# 7. SEC-02: word error must expose basename only (from None)
# ===========================================================================


class TestWordErrorSanitization:
    """SEC-02 / CWE-209: new word-related errors must follow the same sanitisation
    rules as existing subprocess errors (_sanitize_subprocess_error precedent).

    Error messages must contain only the basename — no absolute paths, no whisper
    stderr fragments.  The exception chain must use 'from None' to suppress context.

    These tests inject failures in the word-output path and verify the error envelope.
    """

    def test_word_error_message_no_absolute_path_leak(self, tmp_path: Path) -> None:
        """An error in the word-VTT write step must not expose the absolute path.

        Injects OSError on Path.write_text to simulate a word-VTT write failure and
        verifies that the resulting error message contains only the basename (SEC-02).
        """
        media, output, model = _make_paths(tmp_path)
        absolute_path = str(tmp_path / "out.words.vtt")

        original_write_text = Path.write_text  # noqa: F841

        def _raise_on_words_vtt(self_path: Path, *args: Any, **kwargs: Any) -> None:
            if "words.vtt" in str(self_path):
                raise OSError(f"Cannot write to {absolute_path}")
            return original_write_text(self_path, *args, **kwargs)

        with (
            patch(
                "clipwright_transcribe.transcribe.inspect_media",
                return_value=_make_media_info(media),
            ),
            patch(
                "clipwright_transcribe.transcribe._run_whisper",
                return_value=_mock_run_with_words(),
            ),
            patch.object(Path, "write_text", _raise_on_words_vtt),
        ):
            result = transcribe_media(
                media,
                output,
                _opts(model_path=model, word_timestamps=True),
            )

        # If an error was produced (not ok), verify path sanitisation.
        # If ok is True, the write succeeded; verify the artifact was actually written
        # to confirm the word_timestamps path was exercised.
        if result.ok is False and result.error is not None:
            assert absolute_path not in result.error.message, (
                "Absolute path must not be exposed in error message (SEC-02/CWE-209).\n"
                f"  Leaked path: {absolute_path}\n"
                f"  Message: {result.error.message!r}"
            )
        else:
            # ok=True: enforce that words.vtt was actually written.
            words_vtt = Path(output).parent / (Path(output).stem + ".words.vtt")
            assert words_vtt.exists(), (
                "result.ok=True but words.vtt not found — "
                "word_timestamps=True must produce the artifact"
            )
