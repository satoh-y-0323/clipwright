"""test_media_nle.py — Red-phase tests for core NLE probe extension (FR-1 / ADR-NI-2).

Target additions (not yet implemented):
  - schemas.StreamInfo.channel_layout: str | None
  - schemas.StreamInfo.start_timecode: str | None (raw per-stream tag value)
  - schemas.MediaInfo.start_timecode: str | None (resolved value)
  - media._parse_ffprobe_json: resolve start_timecode (format.tags priority,
    then streams[].tags walk in stream order, casefold key match on
    "timecode") and parse channel_layout for each stream
  - server.clipwright_inspect_media: expose start_timecode / streams[].channel_layout
    in the `data` payload so the calling AI agent can observe them

Design references:
  - requirements-report-20260715-190935.md FR-1 / AC-1
  - architecture-report-20260715-191151.md ADR-NI-2 / §9 ADR-NI-12
  - spike-report-nle-probe.md (fixture values are the source of truth)

Fixtures: tests/fixtures/ffprobe/{mov_tc,mov_no_timecode,mxf_tc,drop_frame,
audio_8x1ch}.json — fixed by spike-probe from real ffprobe output. Read-only;
do not edit. One case (uppercase "TIMECODE" key casefold match) has no
corresponding fixture file, so its ffprobe JSON is constructed inline below.

Most tests here are expected to FAIL until FR-1 is implemented, because the
new schema fields do not exist yet (getattr(..., "MISSING") sentinel makes
this an explicit AssertionError rather than a silent AttributeError). One
test (TestRateDerivationGuarantee) pins already-correct existing behaviour as
a regression guard and is expected to stay green (ADR-NI-12 confirmation,
not a new Red case).
"""

from __future__ import annotations

import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import pytest

from clipwright.media import inspect_media
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ffprobe"

# Sentinel distinguishing "attribute does not exist yet" from an actual None
# value, so a missing field produces a clear AssertionError instead of
# collapsing into a false pass.
_MISSING = "__FIELD_NOT_YET_IMPLEMENTED__"


def _load_fixture(name: str) -> str:
    """Read a spike-fixed ffprobe fixture JSON file verbatim (never edit)."""
    return (_FIXTURE_DIR / name).read_text(encoding="utf-8")


def _make_completed_process(stdout: str) -> CompletedProcess[str]:
    return CompletedProcess(args=["ffprobe"], returncode=0, stdout=stdout, stderr="")


def _inspect_fixture(mocker: MagicMock, tmp_path: Path, fixture_name: str) -> MediaInfo:
    """Run inspect_media with process.run mocked to return a fixture's JSON."""
    media_file = tmp_path / "video.mov"
    media_file.write_bytes(b"dummy")
    mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
    mocker.patch(
        "clipwright.process.run",
        return_value=_make_completed_process(_load_fixture(fixture_name)),
    )
    return inspect_media(str(media_file))


# ===========================================================================
# 1. MediaInfo.start_timecode resolution (AC-1 / ADR-NI-2)
# ===========================================================================


class TestStartTimecodeResolution:
    """AC-1: MediaInfo.start_timecode resolved per ADR-NI-2 rules.

    Rule: format.tags priority, then streams[].tags walk in stream order,
    casefold key match on "timecode". The raw string value is preserved
    as-is (drop-frame ';' punctuation untouched); RationalTime conversion is
    nle_interop's responsibility, not media.py's (ADR-NI-2 parsing/policy
    separation).
    """

    def test_mov_tmcd_stream_tag_resolves_start_timecode(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        """MOV: format.tags has no timecode; video/data stream tags do.

        Expected: streams[].tags walk resolves "01:00:00:00" (ADR-NI-2 MOV path).
        Currently fails: MediaInfo.start_timecode does not exist (not yet implemented).
        """
        result = _inspect_fixture(mocker, tmp_path, "mov_tc.json")
        assert getattr(result, "start_timecode", _MISSING) == "01:00:00:00"

    def test_mxf_format_tag_resolves_start_timecode(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        """MXF: timecode is present in format.tags only (not in stream tags).

        Expected: format.tags priority resolves "01:00:00:00" (ADR-NI-2 MXF path).
        Currently fails: MediaInfo.start_timecode does not exist (not yet implemented).
        """
        result = _inspect_fixture(mocker, tmp_path, "mxf_tc.json")
        assert getattr(result, "start_timecode", _MISSING) == "01:00:00:00"

    def test_drop_frame_timecode_raw_value_preserved(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        """Drop-frame punctuation (';' vs ':') must be preserved verbatim; no
        DF-aware reparsing happens at this layer (nle_interop's job).

        Currently fails: MediaInfo.start_timecode does not exist (not yet implemented).
        """
        result = _inspect_fixture(mocker, tmp_path, "drop_frame.json")
        assert getattr(result, "start_timecode", _MISSING) == "01:00:00;00"

    def test_no_timecode_tag_resolves_to_none(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        """No timecode tag anywhere -> start_timecode is None (fallback safe).

        Currently fails: the attribute does not exist at all yet, so the
        _MISSING sentinel (not None) is what getattr returns today.
        """
        result = _inspect_fixture(mocker, tmp_path, "mov_no_timecode.json")
        assert getattr(result, "start_timecode", _MISSING) is None

    def test_multi_audio_data_stream_tag_resolves_start_timecode(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        """8x1ch fixture: timecode is present only on the trailing tmcd data
        stream (video/audio streams carry no timecode tag); the stream-tag
        walk must scan all streams, not just the first video stream.

        Currently fails: MediaInfo.start_timecode does not exist (not yet implemented).
        """
        result = _inspect_fixture(mocker, tmp_path, "audio_8x1ch.json")
        assert getattr(result, "start_timecode", _MISSING) == "01:00:00:00"

    def test_uppercase_timecode_key_matched_via_casefold(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        """Casefold key match: format.tags key "TIMECODE" (uppercase) must
        resolve the same as lowercase "timecode" (ADR-NI-2). No fixture file
        covers this case, so the ffprobe JSON is constructed inline here.

        Currently fails: MediaInfo.start_timecode does not exist (not yet implemented).
        """
        media_file = tmp_path / "video.mov"
        media_file.write_bytes(b"dummy")
        payload = json.dumps(
            {
                "format": {"tags": {"TIMECODE": "01:00:00:00"}},
                "streams": [
                    {
                        "index": 0,
                        "codec_type": "video",
                        "codec_name": "h264",
                        "avg_frame_rate": "25/1",
                        "duration": "2.000000",
                    }
                ],
            }
        )
        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(payload),
        )

        result = inspect_media(str(media_file))

        assert getattr(result, "start_timecode", _MISSING) == "01:00:00:00"


# ===========================================================================
# 2. StreamInfo.channel_layout parsing (AC-1 / DC-AS-006)
# ===========================================================================


class TestChannelLayoutParsing:
    """AC-1: StreamInfo.channel_layout populated for audio streams; None for
    non-audio streams and for MXF audio (channel_layout: null observed in the
    spike, per spike-report §1 "Important divergence").
    """

    def test_mov_audio_stream_channel_layout_mono(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        """Currently fails: StreamInfo.channel_layout does not exist (not yet
        implemented)."""
        result = _inspect_fixture(mocker, tmp_path, "mov_tc.json")
        audio = next(s for s in result.streams if s.codec_type == "audio")
        assert getattr(audio, "channel_layout", _MISSING) == "mono"

    def test_mov_video_stream_channel_layout_is_none(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        """Currently fails: the attribute does not exist yet (_MISSING sentinel)."""
        result = _inspect_fixture(mocker, tmp_path, "mov_tc.json")
        video = next(s for s in result.streams if s.codec_type == "video")
        assert getattr(video, "channel_layout", _MISSING) is None

    def test_mxf_audio_stream_channel_layout_null_stays_none(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        """MXF may not populate channel_layout (spike-report divergence from MOV).

        Currently fails: StreamInfo.channel_layout does not exist (not yet
        implemented).
        """
        result = _inspect_fixture(mocker, tmp_path, "mxf_tc.json")
        audio = next(s for s in result.streams if s.codec_type == "audio")
        assert getattr(audio, "channel_layout", _MISSING) is None

    def test_audio_8x1ch_all_eight_streams_have_mono_layout(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        """8x1ch fixture: each of the 8 audio streams reports channel_layout=="mono".

        Currently fails: StreamInfo.channel_layout does not exist (not yet
        implemented).
        """
        result = _inspect_fixture(mocker, tmp_path, "audio_8x1ch.json")
        audio_streams = [s for s in result.streams if s.codec_type == "audio"]
        assert len(audio_streams) == 8
        for s in audio_streams:
            assert getattr(s, "channel_layout", _MISSING) == "mono"


# ===========================================================================
# 3. StreamInfo.start_timecode raw per-stream value preservation (AC-1)
# ===========================================================================


class TestStreamStartTimecodeRawPreservation:
    """AC-1: StreamInfo.start_timecode holds the raw per-stream tag value
    (diagnostic field, distinct from MediaInfo.start_timecode's resolved,
    format-priority value).
    """

    def test_mov_video_stream_carries_raw_start_timecode(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        """Currently fails: StreamInfo.start_timecode does not exist (not yet
        implemented)."""
        result = _inspect_fixture(mocker, tmp_path, "mov_tc.json")
        video = next(s for s in result.streams if s.codec_type == "video")
        assert getattr(video, "start_timecode", _MISSING) == "01:00:00:00"

    def test_mov_data_stream_carries_raw_start_timecode(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        """The tmcd data stream (codec_type == "data") also carries its own
        raw tag value, independent of the video stream's tag.

        Currently fails: StreamInfo.start_timecode does not exist (not yet
        implemented).
        """
        result = _inspect_fixture(mocker, tmp_path, "mov_tc.json")
        data_stream = next(s for s in result.streams if s.codec_type == "data")
        assert getattr(data_stream, "start_timecode", _MISSING) == "01:00:00:00"

    def test_mov_audio_stream_without_tag_is_none(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        """Currently fails: the attribute does not exist yet (_MISSING sentinel)."""
        result = _inspect_fixture(mocker, tmp_path, "mov_tc.json")
        audio = next(s for s in result.streams if s.codec_type == "audio")
        assert getattr(audio, "start_timecode", _MISSING) is None

    def test_drop_frame_video_stream_raw_value_has_semicolon(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        """Currently fails: StreamInfo.start_timecode does not exist (not yet
        implemented)."""
        result = _inspect_fixture(mocker, tmp_path, "drop_frame.json")
        video = next(s for s in result.streams if s.codec_type == "video")
        assert getattr(video, "start_timecode", _MISSING) == "01:00:00;00"


# ===========================================================================
# 4. Backward compatibility: additive-only fields default to None (NFR-1)
# ===========================================================================


class TestBackwardCompatibleDefaults:
    """FR-1: new fields are additive; sources without tags/channel_layout keys
    keep working unchanged, and direct schema construction without the new
    kwargs must still default them to None.
    """

    def test_stream_without_tags_key_defaults_new_fields_to_none(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        """Existing fixture-style JSON (no "tags" key at all on any stream)
        must not raise; new fields simply default to None.

        Currently fails: the new fields do not exist yet (_MISSING sentinel),
        even though today's parser tolerates the tags-less JSON shape fine.
        """
        media_file = tmp_path / "video.mp4"
        media_file.write_bytes(b"dummy")
        payload = json.dumps(
            {
                "format": {
                    "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                    "duration": "3.0",
                },
                "streams": [
                    {
                        "index": 0,
                        "codec_type": "video",
                        "codec_name": "h264",
                        "avg_frame_rate": "30/1",
                    },
                    {
                        "index": 1,
                        "codec_type": "audio",
                        "codec_name": "aac",
                        "sample_rate": "44100",
                        "channels": 2,
                    },
                ],
            }
        )
        mocker.patch("clipwright.process.resolve_tool", return_value="/usr/bin/ffprobe")
        mocker.patch(
            "clipwright.process.run",
            return_value=_make_completed_process(payload),
        )

        result = inspect_media(str(media_file))

        assert getattr(result, "start_timecode", _MISSING) is None
        for s in result.streams:
            assert getattr(s, "channel_layout", _MISSING) is None
            assert getattr(s, "start_timecode", _MISSING) is None

    def test_media_info_schema_field_defaults_to_none(self) -> None:
        """schemas.MediaInfo must accept construction without start_timecode
        and default it to None (pure schema-level check, no ffprobe involved).

        Currently fails: the field does not exist on MediaInfo yet.
        """
        info = MediaInfo(path="x.mp4", container=None, duration=None, streams=[])
        assert getattr(info, "start_timecode", _MISSING) is None

    def test_stream_info_schema_fields_default_to_none(self) -> None:
        """schemas.StreamInfo must accept construction without
        channel_layout / start_timecode and default both to None.

        Currently fails: neither field exists on StreamInfo yet.
        """
        info = StreamInfo(index=0, codec_type="audio")
        assert getattr(info, "channel_layout", _MISSING) is None
        assert getattr(info, "start_timecode", _MISSING) is None


# ===========================================================================
# 5. clipwright_inspect_media exposes the new fields (architecture §5 #4)
# ===========================================================================


class TestServerExposesNleFields:
    """architecture-report §5 #4: clipwright_inspect_media exposes
    start_timecode / streams[].channel_layout in its `data` payload so the
    calling AI agent can observe them without reading MediaInfo internals.

    In-process call (server._inspect_media patched), following the existing
    test_server.py TestInspectMedia pattern.
    """

    def test_data_contains_start_timecode(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        """Currently fails: MediaInfo has no start_timecode field to carry the
        value, and server.py's data dict does not add a "start_timecode" key
        yet (both FR-1 / architecture §5 #4 not yet implemented).
        """
        from clipwright.server import clipwright_inspect_media

        media_file = tmp_path / "video.mov"
        media_file.write_bytes(b"dummy")

        fake_media_info = MediaInfo(
            path=str(media_file),
            container="mov,mp4,m4a,3gp,3g2,mj2",
            duration=RationalTimeModel(value=50.0, rate=25.0),
            streams=[StreamInfo(index=0, codec_type="video")],
            **{"start_timecode": "01:00:00:00"},  # ignored until FR-1 lands
        )

        with patch("clipwright.server._inspect_media", return_value=fake_media_info):
            result = clipwright_inspect_media(path=str(media_file))

        data = result["data"]
        assert data.get("start_timecode") == "01:00:00:00"

    def test_data_streams_contain_channel_layout(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        """Currently fails: StreamInfo has no channel_layout field to carry
        the value, so it cannot appear in the serialized streams list
        (FR-1 not yet implemented).
        """
        from clipwright.server import clipwright_inspect_media

        media_file = tmp_path / "audio.mov"
        media_file.write_bytes(b"dummy")

        audio_stream = StreamInfo(
            index=1,
            codec_type="audio",
            channels=1,
            **{"channel_layout": "mono"},  # ignored until FR-1 lands
        )
        fake_media_info = MediaInfo(
            path=str(media_file),
            container="mov,mp4,m4a,3gp,3g2,mj2",
            duration=RationalTimeModel(value=50.0, rate=25.0),
            streams=[audio_stream],
        )

        with patch("clipwright.server._inspect_media", return_value=fake_media_info):
            result = clipwright_inspect_media(path=str(media_file))

        data = result["data"]
        stream_data = data["streams"][0]
        assert stream_data.get("channel_layout") == "mono"


# ===========================================================================
# 6. Rate derivation guarantee (ADR-NI-12 / DC-AM-003)
# ===========================================================================


class TestRateDerivationGuarantee:
    """ADR-NI-12: MediaInfo.duration.rate must exactly equal the first video
    stream's avg_frame_rate (fps_num/fps_den), since resolve_start_time (in
    the follow-up nle_interop module) uses duration.rate as the from_timecode
    rate with no other rate source.

    This pins ALREADY-CORRECT existing behaviour in media.py's rate-selection
    loop as a regression guard for the ADR-NI-12 assumption; it is expected to
    stay green (not a new Red case), matching the "duration.rate が
    avg_frame_rate と一致する" fixture assertion requested for this task.
    """

    @pytest.mark.parametrize(
        "fixture_name, expected_rate",
        [
            ("mov_tc.json", 25.0),
            ("mxf_tc.json", 25.0),
            ("audio_8x1ch.json", 25.0),
            ("drop_frame.json", 30000 / 1001),
        ],
        ids=["mov_25fps", "mxf_25fps", "audio_8x1ch_25fps", "drop_frame_ntsc_exact"],
    )
    def test_duration_rate_matches_avg_frame_rate_exact_value(
        self,
        mocker: MagicMock,
        tmp_path: Path,
        fixture_name: str,
        expected_rate: float,
    ) -> None:
        result = _inspect_fixture(mocker, tmp_path, fixture_name)
        assert result.duration is not None
        assert result.duration.rate == pytest.approx(expected_rate, rel=1e-9)

    def test_drop_frame_rate_is_not_rounded_to_2997(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        """Regression guard: 30000/1001 must not collapse to the rounded 29.97
        decimal, which otio.opentime.from_timecode rejects per spike-report
        AC-14 ("SMPTE timecode does not support this rate"). This pins the
        precise-fraction requirement distinctly from the parametrized
        exact-value check above.
        """
        result = _inspect_fixture(mocker, tmp_path, "drop_frame.json")
        assert result.duration is not None
        assert result.duration.rate != pytest.approx(29.97, abs=1e-6)
        assert result.duration.rate == pytest.approx(30000 / 1001, rel=1e-9)
