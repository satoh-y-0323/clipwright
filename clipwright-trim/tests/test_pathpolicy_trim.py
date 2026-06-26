"""test_pathpolicy_trim.py — Red-phase tests for trim output-placement policy update.

Policy change (impl-trim target): the same-directory constraint is removed from
trim_media.  After impl-trim, output may be placed in any directory provided:
  - parent directory exists
  - output extension is .otio
  - output path does not resolve to the same file as the source media

Red state (before impl-trim):
  - trim.py L120-154 same-directory check raises PATH_NOT_ALLOWED when
    output is in a different directory than media.
  - output==media currently raises INVALID_INPUT, not PATH_NOT_ALLOWED.

Test groups:
  A. output in different directory from media → ok=True (new policy)
  B. OTIO media reference is absolute when media is outside the otio_dir
  C. output == media → PATH_NOT_ALLOWED (error code change from INVALID_INPUT)
  D. preserved checks: .otio extension, parent dir existence (regression guards)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
from clipwright.errors import ErrorCode
from clipwright.otio_utils import load_timeline
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_trim.schemas import TrimOptions, TrimRange
from clipwright_trim.trim import trim_media

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FPS = 30.0
_DURATION_SEC = 10.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_media_info(
    path: str = "/fake/video.mp4",
    *,
    duration_sec: float | None = _DURATION_SEC,
    rate: float = _FPS,
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


def _keep_opts(*ranges: tuple[float, float]) -> TrimOptions:
    """Build a TrimOptions with keep ranges."""
    return TrimOptions(
        keep=[TrimRange(start_sec=s, end_sec=e) for s, e in ranges],
    )


# ===========================================================================
# A. output in different directory from media → ok=True (new policy)
# ===========================================================================


class TestOutputAnyDirectory:
    """After impl-trim, output may live in any directory.

    Red: current same-directory check in trim.py L120-154 rejects this and
    returns PATH_NOT_ALLOWED before writing the OTIO.
    """

    def test_output_in_separate_workdir_succeeds(self, tmp_path: Path) -> None:
        """media in src/, output in work/ → ok=True.

        Red: trim_media currently returns PATH_NOT_ALLOWED because the
        same-directory check fires after inspect_media.
        """
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        work_dir = tmp_path / "work"
        work_dir.mkdir()

        media = str(src_dir / "video.mp4")
        Path(media).touch()
        output = str(work_dir / "project.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = trim_media(media, output, _keep_opts((2.0, 5.0)))

        # New policy: different directory is allowed.
        # Red: current code returns ok=False with PATH_NOT_ALLOWED.
        assert result["ok"] is True

    def test_output_in_nested_workdir_succeeds(self, tmp_path: Path) -> None:
        """media at project root, output in a nested artifacts/ subdir → ok=True.

        Red: same-directory check rejects nested subdirectory output.
        """
        media = str(tmp_path / "raw.mp4")
        Path(media).touch()
        artifacts_dir = tmp_path / "artifacts" / "trim"
        artifacts_dir.mkdir(parents=True)
        output = str(artifacts_dir / "trimmed.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = trim_media(media, output, _keep_opts((1.0, 4.0)))

        # New policy: nested output directories are allowed.
        # Red: current code returns ok=False.
        assert result["ok"] is True

    def test_output_different_dir_inspect_media_is_called(self, tmp_path: Path) -> None:
        """inspect_media must be called even when output is in a different directory.

        This confirms the old same-directory check (which ran AFTER inspect_media)
        no longer blocks the execution flow.

        Red: inspect_media would be called, but the same-directory check that
        follows returns PATH_NOT_ALLOWED, so ok=False.  After impl-trim,
        ok=True confirms inspect_media ran and the function completed normally.
        """
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        media = str(src_dir / "clip.mp4")
        Path(media).touch()
        output = str(out_dir / "clip.otio")

        inspect_called: list[str] = []

        def _tracking_inspect(path: str) -> MediaInfo:
            inspect_called.append(path)
            return _make_media_info(path=path)

        with patch(
            "clipwright_trim.trim.inspect_media",
            side_effect=_tracking_inspect,
        ):
            result = trim_media(media, output, _keep_opts((0.0, 5.0)))

        assert len(inspect_called) == 1
        # Red: result["ok"] is False due to same-directory check.
        assert result["ok"] is True


# ===========================================================================
# B. OTIO media reference is absolute when media is outside the otio_dir
# ===========================================================================


class TestOtioMediaReferenceAbsolute:
    """When media lives outside the OTIO file's directory, the reference must be
    an absolute path so the OTIO is self-consistent regardless of cwd.

    Red: the same-directory check prevents trim_media from writing the OTIO at
    all when media and output are in different directories, so the OTIO file
    never exists and the load_timeline call below fails.
    """

    def test_media_ref_is_absolute_when_media_outside_otio_dir(
        self, tmp_path: Path
    ) -> None:
        """OTIO clip target_url is absolute when media is not under the output dir.

        Red: current code never writes the OTIO (PATH_NOT_ALLOWED), so
        load_timeline raises an error → the assertion is never reached.
        """
        src_dir = tmp_path / "raw_footage"
        src_dir.mkdir()
        work_dir = tmp_path / "project"
        work_dir.mkdir()

        media = str(src_dir / "interview.mp4")
        Path(media).touch()
        output = str(work_dir / "interview_trimmed.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=media),
        ):
            result = trim_media(media, output, _keep_opts((1.0, 8.0)))

        # Step 1 — trim must succeed (Red: currently PATH_NOT_ALLOWED).
        assert result["ok"] is True, f"trim_media failed: {result.get('error')}"

        # Step 2 — inspect the OTIO media reference.
        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) == 1, "Expected exactly one clip"

        ref = clips[0].media_reference
        assert ref is not None
        target_url: str = ref.target_url  # type: ignore[union-attr]

        # media is outside work_dir → reference must be absolute.
        assert Path(target_url).is_absolute(), (
            f"Expected absolute media reference; got: {target_url!r}"
        )

    def test_media_ref_path_matches_resolved_media(self, tmp_path: Path) -> None:
        """The absolute media reference resolves to the same file as the input.

        Verifies that the reference is not only absolute but also correct
        (no stale path, no wrong file).

        Red: same-directory check prevents OTIO write → assertion unreachable.
        """
        src_dir = tmp_path / "footage"
        src_dir.mkdir()
        work_dir = tmp_path / "timeline"
        work_dir.mkdir()

        media_path = src_dir / "take1.mp4"
        media_path.touch()
        output = str(work_dir / "take1.otio")

        with patch(
            "clipwright_trim.trim.inspect_media",
            return_value=_make_media_info(path=str(media_path)),
        ):
            result = trim_media(str(media_path), output, _keep_opts((0.0, 5.0)))

        assert result["ok"] is True, f"trim_media failed: {result.get('error')}"

        tl = load_timeline(output)
        v1 = tl.tracks[0]
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert len(clips) >= 1

        ref = clips[0].media_reference
        assert ref is not None
        target_url = ref.target_url  # type: ignore[union-attr]

        # The stored path must resolve to the original media file.
        assert Path(target_url).resolve() == media_path.resolve()


# ===========================================================================
# C. output == media → PATH_NOT_ALLOWED (error code change)
# ===========================================================================


class TestOutputEqualsMediaPathNotAllowed:
    """output resolving to the same file as media must be rejected.

    After impl-trim, this uses check_output_not_source which raises
    PATH_NOT_ALLOWED.  Current code raises INVALID_INPUT — so asserting
    PATH_NOT_ALLOWED is Red until the implementation is updated.
    """

    def test_output_same_path_as_media_is_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """output resolving to the same file as media → PATH_NOT_ALLOWED.

        Red: current code raises INVALID_INPUT at the pre-probe check, not
        PATH_NOT_ALLOWED.
        """
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        # output is exactly the same path as media
        output = media

        result = trim_media(media, output, _keep_opts((1.0, 5.0)))

        assert result["ok"] is False
        # Red: current code returns INVALID_INPUT, not PATH_NOT_ALLOWED.
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED

    def test_output_same_path_as_media_no_path_in_message(self, tmp_path: Path) -> None:
        """CWE-209: absolute path must not appear in error message or hint.

        Red: the error code assertion (PATH_NOT_ALLOWED) drives the Red;
        CWE-209 guard is inherited from the existing policy.
        """
        media = str(tmp_path / "private" / "footage.mp4")
        Path(media).parent.mkdir(parents=True)
        Path(media).touch()
        output = media  # same path

        result = trim_media(media, output, _keep_opts((0.0, 3.0)))

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED
        # No full absolute directory path in the error text.
        error_msg = result["error"]["message"]
        error_hint = result["error"]["hint"]
        assert str(tmp_path) not in error_msg
        assert str(tmp_path) not in error_hint


# ===========================================================================
# D. preserved checks: .otio extension, parent dir existence (regression guards)
# ===========================================================================


class TestPreservedPathChecks:
    """These checks existed before impl-trim and must remain in force.

    Expected status: Green (already implemented) — they serve as regression
    guards to confirm impl-trim does not accidentally remove them.
    """

    def test_non_otio_extension_returns_invalid_input(self, tmp_path: Path) -> None:
        """output with an extension other than .otio → INVALID_INPUT (pre-probe check)."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "out.mp4")  # wrong extension

        result = trim_media(media, output, _keep_opts((1.0, 5.0)))

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT

    def test_missing_parent_dir_returns_invalid_input(self, tmp_path: Path) -> None:
        """output parent directory does not exist → INVALID_INPUT (pre-probe check)."""
        media = str(tmp_path / "video.mp4")
        Path(media).touch()
        output = str(tmp_path / "nonexistent_dir" / "out.otio")

        result = trim_media(media, output, _keep_opts((1.0, 5.0)))

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT
