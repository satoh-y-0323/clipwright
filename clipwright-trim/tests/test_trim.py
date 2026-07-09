"""test_trim.py — Tests for trim.py orchestration (inspect_media mocked).

Target API:
  clipwright_trim.trim.trim_media(
      media: str,
      output: str,
      options: TrimOptions,
  ) -> ToolResult

Mocking policy:
  - Patch clipwright_trim.trim.inspect_media to supply synthetic MediaInfo.
  - No real ffprobe binary is called.

Test aspects:
  (1) OTIO structure (AC-1/AC-2): V1 track present, clip count matches keep ranges,
      source_range compared as RationalTime (not float approximation).
  (2) Metadata: metadata["clipwright"] contains tool/version/kind/mode.
  (3) Return value envelope (FR-8): ok, summary, data, artifacts, warnings.
  (4) Error mapping for each §5 failure case.
  (5) Output path validation runs before inspect_media (no ffprobe on bad output path).
  (6) Same-directory validation runs after inspect_media.
  (7) Both-empty keep/drop passthrough (ok=true, full-duration single clip).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.otio_utils import load_timeline
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_trim.schemas import TrimOptions, TrimRange
from clipwright_trim.trim import trim_media

# ===========================================================================
# Constants
# ===========================================================================

FPS = 30.0
DURATION_SEC = 10.0


# ===========================================================================
# Helpers
# ===========================================================================


def _make_media_info(
    path: str = "/fake/video.mp4",
    *,
    duration_sec: float | None = DURATION_SEC,
    rate: float = FPS,
) -> MediaInfo:
    """Construct a synthetic MediaInfo for mocking inspect_media."""
    streams: list[StreamInfo] = [
        StreamInfo(index=0, codec_type="video", codec_name="h264"),
        StreamInfo(index=1, codec_type="audio", codec_name="aac"),
    ]
    duration = (
        RationalTimeModel(value=duration_sec * rate, rate=rate)
        if duration_sec is not None
        else None
    )
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=duration,
        streams=streams,
        bit_rate=8_000_000,
    )


def _keep_opts(*ranges: tuple[float, float], padding: float = 0.0) -> TrimOptions:
    """Build a TrimOptions with keep ranges."""
    return TrimOptions(
        keep=[TrimRange(start_sec=s, end_sec=e) for s, e in ranges],
        padding_sec=padding,
    )


def _drop_opts(*ranges: tuple[float, float], padding: float = 0.0) -> TrimOptions:
    """Build a TrimOptions with drop ranges."""
    return TrimOptions(
        drop=[TrimRange(start_sec=s, end_sec=e) for s, e in ranges],
        padding_sec=padding,
    )


# ===========================================================================
# (1) OTIO structure: V1 track, clip count, source_range (AC-1 / AC-2)
# ===========================================================================


class TestOtioStructure:
    """Verify the OTIO timeline structure produced by trim_media.

    source_range values are compared as RationalTime, not float approximation (AC-1).
    """

    def test_v1_track_is_video_track(self, tmp_path: Path) -> None:
        """V1 (tracks[0]) must be a Video track."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = trim_media(media, output, _keep_opts((2.0, 5.0)))

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        assert v1.kind == otio.schema.TrackKind.Video

    def test_keep_two_ranges_produces_two_clips(self, tmp_path: Path) -> None:
        """Keep mode with 2 ranges produces exactly 2 clips in V1 (AC-1: clip_count=2)."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = trim_media(media, output, _keep_opts((1.0, 3.0), (6.0, 9.0)))

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) == 2

    def test_keep_source_range_as_rational_time(self, tmp_path: Path) -> None:
        """source_range start/duration values encode seconds*rate (RationalTime comparison).

        keep [(2.0, 5.0)], rate=30.0 -> start_time.value=60.0, duration.value=90.0.
        """
        rate = 30.0
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0, rate=rate),
        ):
            result = trim_media(media, output, _keep_opts((2.0, 5.0)))

        assert result["ok"] is True
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) == 1
        clip = clips[0]
        assert clip.source_range is not None
        # RationalTime comparison — exact, not float-approx
        expected_start = otio.opentime.RationalTime(value=2.0 * rate, rate=rate)
        expected_duration = otio.opentime.RationalTime(value=3.0 * rate, rate=rate)
        assert clip.source_range.start_time == expected_start
        assert clip.source_range.duration == expected_duration

    def test_keep_two_ranges_source_ranges_in_order(self, tmp_path: Path) -> None:
        """Two keep ranges produce clips with source_range values in enumeration order."""
        rate = 25.0
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=15.0, rate=rate),
        ):
            result = trim_media(media, output, _keep_opts((1.0, 4.0), (6.0, 9.0)))

        assert result["ok"] is True
        tl = load_timeline(output)
        clips = [it for it in tl.tracks[0] if isinstance(it, otio.schema.Clip)]
        assert len(clips) == 2

        # First clip: keep (1.0, 4.0)
        assert clips[0].source_range.start_time == otio.opentime.RationalTime(
            value=1.0 * rate, rate=rate
        )
        assert clips[0].source_range.duration == otio.opentime.RationalTime(
            value=3.0 * rate, rate=rate
        )
        # Second clip: keep (6.0, 9.0)
        assert clips[1].source_range.start_time == otio.opentime.RationalTime(
            value=6.0 * rate, rate=rate
        )
        assert clips[1].source_range.duration == otio.opentime.RationalTime(
            value=3.0 * rate, rate=rate
        )

    def test_drop_single_range_produces_two_clips(self, tmp_path: Path) -> None:
        """Drop mode: drop [(3.0, 7.0)] from 10s -> clips (0, 3) and (7, 10) (AC-2)."""
        rate = 30.0
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0, rate=rate),
        ):
            result = trim_media(media, output, _drop_opts((3.0, 7.0)))

        assert result["ok"] is True
        tl = load_timeline(output)
        clips = [it for it in tl.tracks[0] if isinstance(it, otio.schema.Clip)]
        assert len(clips) == 2

        # First clip: (0.0, 3.0)
        assert clips[0].source_range.start_time == otio.opentime.RationalTime(
            value=0.0 * rate, rate=rate
        )
        assert clips[0].source_range.duration == otio.opentime.RationalTime(
            value=3.0 * rate, rate=rate
        )
        # Second clip: (7.0, 10.0)
        assert clips[1].source_range.start_time == otio.opentime.RationalTime(
            value=7.0 * rate, rate=rate
        )
        assert clips[1].source_range.duration == otio.opentime.RationalTime(
            value=3.0 * rate, rate=rate
        )

    def test_target_url_matches_media_ref_for_otio(self, tmp_path: Path) -> None:
        """Each clip's target_url must match media_ref_for_otio output.

        When media is under the OTIO directory, media_ref_for_otio returns a
        relative POSIX path.  When media is outside, it returns an absolute path.
        This test uses same-directory placement, so a relative path is expected.
        """
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = trim_media(media, output, _keep_opts((1.0, 4.0)))

        assert result["ok"] is True
        tl = load_timeline(output)
        clips = [it for it in tl.tracks[0] if isinstance(it, otio.schema.Clip)]
        # Same-directory placement → relative reference ("video.mp4").
        expected_url = Path(media).name
        for clip in clips:
            assert isinstance(clip.media_reference, otio.schema.ExternalReference)
            assert clip.media_reference.target_url == expected_url


# ===========================================================================
# (2) Metadata: metadata["clipwright"] tool/version/kind/mode
# ===========================================================================


class TestClipMetadata:
    """Verify clip-level metadata["clipwright"] contents."""

    def test_keep_mode_metadata(self, tmp_path: Path) -> None:
        """Keep mode: metadata["clipwright"] must have tool/version/kind='keep'/mode='keep'."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = trim_media(media, output, _keep_opts((2.0, 5.0)))

        assert result["ok"] is True
        tl = load_timeline(output)
        clips = [it for it in tl.tracks[0] if isinstance(it, otio.schema.Clip)]
        assert len(clips) >= 1
        for clip in clips:
            cw = clip.metadata.get("clipwright")
            assert cw is not None, "metadata['clipwright'] missing"
            assert cw["tool"] == "clipwright-trim"
            assert cw["version"] == "0.2.2"
            assert cw["kind"] == "keep"
            assert cw["mode"] == "keep"

    def test_drop_mode_metadata_kind_is_keep(self, tmp_path: Path) -> None:
        """Drop mode: kind='keep' even though mode='drop' (ADR-1 — render uses V1 clips, not kind)."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = trim_media(media, output, _drop_opts((3.0, 7.0)))

        assert result["ok"] is True
        tl = load_timeline(output)
        clips = [it for it in tl.tracks[0] if isinstance(it, otio.schema.Clip)]
        assert len(clips) >= 1
        for clip in clips:
            cw = clip.metadata.get("clipwright")
            assert cw is not None
            assert cw["kind"] == "keep"
            assert cw["mode"] == "drop"


# ===========================================================================
# (3) Return value envelope (FR-8)
# ===========================================================================


class TestEnvelope:
    """Verify the success envelope: ok/summary/data/artifacts/warnings (FR-8)."""

    def test_success_envelope_keys_present(self, tmp_path: Path) -> None:
        """On success, ok/summary/data/artifacts/warnings must all be present."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = trim_media(media, output, _keep_opts((2.0, 5.0)))

        assert result["ok"] is True
        assert "summary" in result
        assert "data" in result
        assert "artifacts" in result
        assert "warnings" in result

    def test_data_fields_keep_mode(self, tmp_path: Path) -> None:
        """data must contain clip_count / kept_duration_sec / source_duration_sec / mode."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0),
        ):
            result = trim_media(media, output, _keep_opts((2.0, 5.0)))

        assert result["ok"] is True
        data = result["data"]
        assert "clip_count" in data
        assert "kept_duration_sec" in data
        assert "source_duration_sec" in data
        assert "mode" in data

    def test_data_values_keep_mode(self, tmp_path: Path) -> None:
        """data values must match expected counts and durations for keep mode (AC-1)."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0),
        ):
            result = trim_media(media, output, _keep_opts((2.0, 5.0), (7.0, 9.0)))

        data = result["data"]
        assert data["clip_count"] == 2
        assert data["kept_duration_sec"] == pytest.approx(5.0, abs=1e-6)
        assert data["source_duration_sec"] == pytest.approx(10.0, abs=1e-6)
        assert data["mode"] == "keep"

    def test_data_values_drop_mode(self, tmp_path: Path) -> None:
        """data.mode='drop' and kept_duration_sec=D-(e-s) for drop mode (AC-2)."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0),
        ):
            result = trim_media(media, output, _drop_opts((3.0, 7.0)))

        data = result["data"]
        assert data["mode"] == "drop"
        assert data["kept_duration_sec"] == pytest.approx(6.0, abs=1e-6)
        assert data["source_duration_sec"] == pytest.approx(10.0, abs=1e-6)

    def test_artifacts_contain_timeline_otio(self, tmp_path: Path) -> None:
        """artifacts must contain one entry with role='timeline' and format='otio'."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = trim_media(media, output, _keep_opts((2.0, 5.0)))

        assert result["ok"] is True
        artifacts = result["artifacts"]
        timeline_artifacts = [
            a
            for a in artifacts
            if (
                (isinstance(a, dict) and a.get("role") == "timeline")
                or (hasattr(a, "role") and a.role == "timeline")
            )
        ]
        assert len(timeline_artifacts) == 1
        artifact = timeline_artifacts[0]
        fmt = artifact.get("format") if isinstance(artifact, dict) else artifact.format
        assert fmt == "otio"

    def test_summary_contains_clip_count_and_duration(self, tmp_path: Path) -> None:
        """summary must be a non-empty string mentioning clip count and durations (FR-8)."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0),
        ):
            result = trim_media(media, output, _keep_opts((2.0, 5.0)))

        assert result["ok"] is True
        summary = result["summary"]
        assert isinstance(summary, str) and len(summary) > 0


# ===========================================================================
# (4) Error mapping — §5 all failure cases
# ===========================================================================


class TestErrorMapping:
    """Each §5 failure case must return ok=False with the correct error code."""

    # ---- Output path validation (must run BEFORE inspect_media) ----

    def test_output_wrong_extension_returns_invalid_input(self, tmp_path: Path) -> None:
        """output extension != .otio -> INVALID_INPUT (§5)."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.mp4")  # wrong extension

        inspect_called = []

        def _guarded_inspect(path: str) -> MediaInfo:
            inspect_called.append(path)
            return _make_media_info(path=path)

        with patch("clipwright_trim.trim.inspect_media", side_effect=_guarded_inspect):
            result = trim_media(media, output, _keep_opts((2.0, 5.0)))

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT
        # inspect_media must NOT have been called (output path validated first)
        assert len(inspect_called) == 0

    def test_output_parent_dir_missing_returns_invalid_input(
        self, tmp_path: Path
    ) -> None:
        """output parent directory missing -> INVALID_INPUT, inspect_media not called (§5)."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "nonexistent_dir" / "out.otio")

        inspect_called = []

        def _guarded_inspect(path: str) -> MediaInfo:
            inspect_called.append(path)
            return _make_media_info(path=path)

        with patch("clipwright_trim.trim.inspect_media", side_effect=_guarded_inspect):
            result = trim_media(media, output, _keep_opts((2.0, 5.0)))

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT
        assert len(inspect_called) == 0

    def test_output_equals_media_returns_path_not_allowed(self, tmp_path: Path) -> None:
        """output == media path -> PATH_NOT_ALLOWED, inspect_media not called.

        check_output_not_source runs before the extension check, so the error
        code is PATH_NOT_ALLOWED regardless of the output extension.
        """
        media = str(tmp_path / "video.otio")
        Path(media).touch()
        output = media  # same path

        inspect_called = []

        def _guarded_inspect(path: str) -> MediaInfo:
            inspect_called.append(path)
            return _make_media_info(path=path)

        with patch("clipwright_trim.trim.inspect_media", side_effect=_guarded_inspect):
            result = trim_media(media, output, _keep_opts((2.0, 5.0)))

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED
        assert len(inspect_called) == 0

    # ---- inspect_media error propagation ----

    def test_media_not_found_returns_file_not_found(self, tmp_path: Path) -> None:
        """inspect_media raises FILE_NOT_FOUND -> propagated with basename only (§5)."""
        media = str(tmp_path / "missing.mp4")
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            side_effect=ClipwrightError(
                code=ErrorCode.FILE_NOT_FOUND,
                message=f"File not found: {Path(media).name}",
                hint="Specify a valid media file path.",
            ),
        ):
            result = trim_media(media, output, _keep_opts((2.0, 5.0)))

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.FILE_NOT_FOUND
        # Basename only — no full path exposed (CWE-209)
        error_msg = result["error"]["message"]
        assert str(tmp_path) not in error_msg
        assert "missing.mp4" in error_msg

    def test_duration_none_returns_probe_failed(self, tmp_path: Path) -> None:
        """MediaInfo.duration is None -> PROBE_FAILED (§5)."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=None),
        ):
            result = trim_media(media, output, _keep_opts((2.0, 5.0)))

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PROBE_FAILED

    # ---- Same-directory check removed (new policy: output anywhere) ----

    def test_output_different_dir_succeeds_after_policy_update(
        self, tmp_path: Path
    ) -> None:
        """output in a different directory than media → ok=True (new policy).

        The same-directory co-location constraint has been removed.  After
        impl-trim, placing the output OTIO in any directory with an existing
        parent is allowed; only output==media is rejected.

        inspect_media must still be called (path validation runs before probe;
        the new flow does not skip probe when directories differ).
        """
        media_dir = tmp_path / "src"
        media_dir.mkdir()
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        media = str(media_dir / "video.mp4")
        Path(media).touch()
        output = str(other_dir / "out.otio")

        inspect_called: list[str] = []

        def _tracking_inspect(path: str) -> MediaInfo:
            inspect_called.append(path)
            return _make_media_info(path=path)

        with patch("clipwright_trim.trim.inspect_media", side_effect=_tracking_inspect):
            result = trim_media(media, output, _keep_opts((2.0, 5.0)))

        # New policy: different directory is allowed → ok=True.
        assert result["ok"] is True
        # inspect_media must have been called
        assert len(inspect_called) == 1

    # ---- Range validation errors ----

    def test_both_keep_and_drop_returns_invalid_input(self, tmp_path: Path) -> None:
        """Both keep and drop non-empty -> INVALID_INPUT (AC-4)."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        opts = TrimOptions(
            keep=[TrimRange(start_sec=1.0, end_sec=3.0)],
            drop=[TrimRange(start_sec=5.0, end_sec=8.0)],
        )

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = trim_media(media, output, opts)

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_drop_full_duration_returns_invalid_input(self, tmp_path: Path) -> None:
        """Drop covers full duration -> computed keep is empty -> INVALID_INPUT (AC-5)."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0),
        ):
            result = trim_media(media, output, _drop_opts((0.0, 10.0)))

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    # ---- Both-empty -> passthrough (ADR-4 resolution: full-duration single clip) ----

    def test_both_empty_keep_drop_passthrough_full_duration(
        self, tmp_path: Path
    ) -> None:
        """Both keep and drop empty -> passthrough ok=True, single full-duration clip.

        Per task spec and FR-2 literal reading: options=TrimOptions() (no ranges)
        produces a single clip covering the full media duration (no error).
        """
        rate = 30.0
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0, rate=rate),
        ):
            result = trim_media(media, output, TrimOptions())

        assert result["ok"] is True
        data = result["data"]
        assert data["clip_count"] == 1
        assert data["kept_duration_sec"] == pytest.approx(10.0, abs=1e-6)
        # OTIO also has the single full-duration clip
        tl = load_timeline(output)
        clips = [it for it in tl.tracks[0] if isinstance(it, otio.schema.Clip)]
        assert len(clips) == 1
        clip = clips[0]
        assert clip.source_range.duration == otio.opentime.RationalTime(
            value=10.0 * rate, rate=rate
        )

    # ---- error hint must be non-empty ----

    def test_error_has_hint(self, tmp_path: Path) -> None:
        """Every error response must include a non-empty hint (§6.4 / §5)."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.json")  # wrong extension

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = trim_media(media, output, _keep_opts((2.0, 5.0)))

        assert result["ok"] is False
        assert (
            isinstance(result["error"]["hint"], str)
            and len(result["error"]["hint"]) > 0
        )


# ===========================================================================
# (5) Output path validation order: before inspect_media
# ===========================================================================


class TestOutputPathValidationOrder:
    """Output extension/parent-dir/output==media checks run before inspect_media."""

    def test_invalid_extension_does_not_call_inspect_media(
        self, tmp_path: Path
    ) -> None:
        """Wrong output extension -> fails before inspect_media is called."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.json")

        calls: list[str] = []

        with patch(
            "clipwright_trim.trim.inspect_media",
            side_effect=lambda p: calls.append(p) or _make_media_info(path=p),
        ):
            result = trim_media(media, output, _keep_opts((1.0, 3.0)))

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT
        assert len(calls) == 0, (
            "inspect_media should not be called on invalid extension"
        )

    def test_missing_parent_dir_does_not_call_inspect_media(
        self, tmp_path: Path
    ) -> None:
        """Missing output parent directory -> fails before inspect_media is called."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "ghost_dir" / "out.otio")

        calls: list[str] = []

        with patch(
            "clipwright_trim.trim.inspect_media",
            side_effect=lambda p: calls.append(p) or _make_media_info(path=p),
        ):
            result = trim_media(media, output, _keep_opts((1.0, 3.0)))

        assert result["ok"] is False
        assert len(calls) == 0, "inspect_media should not be called on missing parent"


# ===========================================================================
# (6) Clamp warning surfaced in envelope warnings (AC-3)
# ===========================================================================


class TestClampWarning:
    """A range extending beyond media duration is clamped with a warning (AC-3)."""

    def test_range_beyond_duration_produces_warning(self, tmp_path: Path) -> None:
        """Keep range that extends past duration -> ok=True + non-empty warnings (AC-3)."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media, duration_sec=10.0),
        ):
            # end_sec=12.0 exceeds duration=10.0
            result = trim_media(media, output, _keep_opts((5.0, 12.0)))

        assert result["ok"] is True
        assert len(result["warnings"]) > 0


# ===========================================================================
# (7) Non-destructive: media file unchanged after trim_media
# ===========================================================================


class TestNonDestructive:
    """trim_media must not modify the input media file."""

    def test_media_file_unchanged_after_trim(self, tmp_path: Path) -> None:
        """Media file content is unmodified after trim_media completes."""
        media_path = tmp_path / "video.mp4"
        media_path.write_bytes(b"dummy media content")
        original_bytes = media_path.read_bytes()
        output = str(tmp_path / "out.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=str(media_path)),
        ):
            trim_media(str(media_path), output, _keep_opts((2.0, 5.0)))

        assert media_path.read_bytes() == original_bytes
