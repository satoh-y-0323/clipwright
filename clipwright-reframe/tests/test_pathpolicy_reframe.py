"""test_pathpolicy_reframe.py — Path-boundary policy tests for clipwright-reframe.

New output-location policy (spec4 #5 / plan-report wave 6 task test-reframe):
  - Output .otio file is no longer required to be in the same directory as the media.
  - OTIO media reference uses media_ref_for_otio rules (architecture-report §2.1):
    - relative posix when media is under the otio_dir tree
    - absolute path when media is outside the otio_dir
  - output == media is still rejected (PATH_NOT_ALLOWED via check_output_not_source)
  - Extension check / parent-dir-exists check / output == timeline check are unchanged.

Verification points:
  P-1: output in a different directory from media → ok=True
  P-2: OTIO media reference is relative posix when media and output share the same dir
  P-3: OTIO media reference is absolute when media is outside the otio_dir
  P-4: output == media is rejected with PATH_NOT_ALLOWED (from check_output_not_source)

Layer-4 propagation test (architecture-report-20260720-082027.md §3 層4,
ADR-PB-1): reframe's own `timeline` argument only does a raw Path.exists()
existence pre-check (D1, reframe.py L201) and otherwise delegates to core
`load_timeline`. This layer proves the protection lives entirely in core
(reframe.py is not modified for this batch — bump-free per ADR-PB-6).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ErrorCode
from clipwright.otio_utils import new_timeline, save_timeline
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_reframe.reframe import reframe
from clipwright_reframe.schemas import ReframeOptions

_FPS = 30.0
_DURATION_SEC = 10.0


def _make_media_info(
    path: str,
    *,
    has_video: bool = True,
    has_audio: bool = True,
) -> MediaInfo:
    """Build a minimal MediaInfo for monkeypatching inspect_media."""
    streams: list[StreamInfo] = []
    if has_video:
        streams.append(StreamInfo(index=0, codec_type="video", codec_name="h264"))
    if has_audio:
        streams.append(StreamInfo(index=1, codec_type="audio", codec_name="aac"))
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=_DURATION_SEC * _FPS, rate=_FPS),
        streams=streams,
        bit_rate=8_000_000,
    )


def _default_opts() -> ReframeOptions:
    """Return default ReframeOptions for path-policy tests."""
    return ReframeOptions(target_w=1080, target_h=1920)


def _get_clip_target_url(otio_path: Path) -> str:
    """Read an OTIO file and return the first clip's ExternalReference target_url."""
    tl = otio.adapters.read_from_file(str(otio_path))
    for track in tl.tracks:
        for item in track:
            if isinstance(item, otio.schema.Clip):
                ref = item.media_reference
                if isinstance(ref, otio.schema.ExternalReference):
                    return ref.target_url
    raise AssertionError("No ExternalReference clip found in timeline")


# ===========================================================================
# P-1: Output in a different directory from media
# ===========================================================================


class TestOutputInSeparateDir:
    """output can be placed in a directory different from the media file directory.

    Impl removes _check_output_within_media_dir; only parent-dir-exists and
    check_output_not_source remain.
    """

    def test_output_in_separate_dir_returns_ok_true(self, tmp_path: Path) -> None:
        """output dir != media dir must succeed (ok=True) after impl (P-1)."""
        media_dir = tmp_path / "media"
        media_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy media")

        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            result = reframe(
                media=str(media),
                output=str(output_dir / "out.otio"),
                options=_default_opts(),
                timeline=None,
            )

        assert result["ok"] is True, (
            f"output in a different directory from media must succeed (P-1). "
            f"Got: error={result.get('error')}"
        )

    def test_output_in_separate_dir_creates_otio_file(self, tmp_path: Path) -> None:
        """output .otio must be created in the specified separate directory (P-1)."""
        media_dir = tmp_path / "media"
        media_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy media")
        output_path = output_dir / "out.otio"

        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            result = reframe(
                media=str(media),
                output=str(output_path),
                options=_default_opts(),
                timeline=None,
            )

        assert result["ok"] is True
        assert output_path.exists(), (
            "Output .otio must be created at the specified path in the separate output dir"
        )

    def test_output_in_deeply_nested_dir_returns_ok_true(self, tmp_path: Path) -> None:
        """output in a deeply nested directory that differs from media dir succeeds (P-1)."""
        media_dir = tmp_path / "source"
        media_dir.mkdir()
        output_dir = tmp_path / "work" / "project" / "reframe"
        output_dir.mkdir(parents=True)

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy media")

        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            result = reframe(
                media=str(media),
                output=str(output_dir / "reframed.otio"),
                options=_default_opts(),
                timeline=None,
            )

        assert result["ok"] is True, (
            f"Deeply nested output dir must succeed (P-1). "
            f"Got: error={result.get('error')}"
        )

    def test_parent_dir_not_existing_still_rejected(self, tmp_path: Path) -> None:
        """Parent directory must still exist — this constraint is unchanged (P-1 negative).

        Regression guard: removing _check_output_within_media_dir must not accidentally
        remove the parent-dir-exists check.
        """
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy media")

        result = reframe(
            media=str(media),
            output=str(tmp_path / "nonexistent_dir" / "out.otio"),
            options=_default_opts(),
            timeline=None,
        )

        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.INVALID_INPUT.value, (
            "Missing parent dir must still return INVALID_INPUT (unchanged constraint)"
        )


# ===========================================================================
# P-2: OTIO media reference — relative posix when media under otio_dir
# ===========================================================================


class TestMediaRefRelativeWhenSameDir:
    """When media and output .otio share the same directory, the OTIO
    ExternalReference target_url must be a relative POSIX path (no backslashes,
    no leading / or drive letter).

    media_ref_for_otio contract (architecture-report §2.1):
      source under otio_dir tree → relative posix (e.g. "video.mp4").
    """

    def test_media_ref_is_relative_when_same_dir(self, tmp_path: Path) -> None:
        """target_url must be relative when media is in the same dir as output (P-2)."""
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy media")
        output_path = tmp_path / "out.otio"

        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            result = reframe(
                media=str(media),
                output=str(output_path),
                options=_default_opts(),
                timeline=None,
            )

        assert result["ok"] is True
        target_url = _get_clip_target_url(output_path)
        assert not os.path.isabs(target_url), (
            f"media_ref_for_otio must return a relative posix path when media is in "
            f"the same dir as output. Got absolute: {target_url!r}"
        )

    def test_media_ref_no_backslashes_when_same_dir(self, tmp_path: Path) -> None:
        """Relative media reference must not contain backslashes (POSIX only) (P-2)."""
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy media")
        output_path = tmp_path / "out.otio"

        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            result = reframe(
                media=str(media),
                output=str(output_path),
                options=_default_opts(),
                timeline=None,
            )

        assert result["ok"] is True
        target_url = _get_clip_target_url(output_path)
        assert "\\" not in target_url, (
            f"Relative media reference must use POSIX separators (no backslashes). "
            f"Got: {target_url!r}"
        )

    def test_media_filename_in_relative_ref_and_is_relative(
        self, tmp_path: Path
    ) -> None:
        """Relative reference must contain the media filename AND must be relative (P-2)."""
        media = tmp_path / "my_video_file.mp4"
        media.write_bytes(b"dummy media")
        output_path = tmp_path / "out.otio"

        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            result = reframe(
                media=str(media),
                output=str(output_path),
                options=_default_opts(),
                timeline=None,
            )

        assert result["ok"] is True
        target_url = _get_clip_target_url(output_path)
        assert not os.path.isabs(target_url), (
            f"target_url must be relative when media is in the same dir. "
            f"Got absolute: {target_url!r}"
        )
        assert "my_video_file.mp4" in target_url, (
            f"Relative media reference must contain the media filename. "
            f"Got: {target_url!r}"
        )


# ===========================================================================
# P-3: OTIO media reference — absolute when media outside otio_dir
# ===========================================================================


class TestMediaRefAbsoluteWhenDifferentDir:
    """When media is outside the output .otio's directory, the OTIO
    ExternalReference target_url must be an absolute path.

    media_ref_for_otio contract (architecture-report §2.1):
      source outside otio_dir tree → absolute path string.
    """

    def test_media_ref_is_absolute_when_media_outside_otio_dir(
        self, tmp_path: Path
    ) -> None:
        """When media is outside the output dir, target_url must be absolute (P-3)."""
        media_dir = tmp_path / "media"
        media_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"dummy media")
        output_path = output_dir / "out.otio"

        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            result = reframe(
                media=str(media),
                output=str(output_path),
                options=_default_opts(),
                timeline=None,
            )

        assert result["ok"] is True, (
            f"P-1 must be implemented before P-3 can be verified. "
            f"Got: error={result.get('error')}"
        )
        target_url = _get_clip_target_url(output_path)
        assert os.path.isabs(target_url), (
            f"When media is outside otio_dir, target_url must be absolute. "
            f"Got: {target_url!r}"
        )

    def test_media_ref_points_to_actual_media_when_different_dir(
        self, tmp_path: Path
    ) -> None:
        """Absolute media ref must resolve to the actual media file (P-3)."""
        media_dir = tmp_path / "media"
        media_dir.mkdir()
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        media = media_dir / "source.mp4"
        media.write_bytes(b"dummy media")
        output_path = output_dir / "out.otio"

        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            result = reframe(
                media=str(media),
                output=str(output_path),
                options=_default_opts(),
                timeline=None,
            )

        assert result["ok"] is True, (
            f"P-1 must be implemented before P-3 can be verified. "
            f"Got: error={result.get('error')}"
        )
        target_url = _get_clip_target_url(output_path)
        assert os.path.isabs(target_url), (
            f"Absolute media ref expected when media is outside otio_dir. "
            f"Got: {target_url!r}"
        )
        resolved = Path(target_url)
        assert "source.mp4" in resolved.name, (
            f"Absolute media ref must reference the source media file. "
            f"Got: {target_url!r}"
        )


# ===========================================================================
# P-4: output == media is rejected with PATH_NOT_ALLOWED
# ===========================================================================


class TestOutputEqualsMediaPathNotAllowed:
    """output == media must be rejected with PATH_NOT_ALLOWED.

    New policy (architecture-report §2.1): check_output_not_source raises
    PATH_NOT_ALLOWED when output resolves equal to any source.
    """

    def test_output_equals_media_returns_path_not_allowed(self, tmp_path: Path) -> None:
        """output == media must return PATH_NOT_ALLOWED via check_output_not_source (P-4)."""
        media = tmp_path / "video.otio"
        media.write_bytes(b"dummy")
        result = reframe(
            media=str(media),
            output=str(media),
            options=_default_opts(),
            timeline=None,
        )
        assert result["ok"] is False
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED.value, (
            f"output == media must return PATH_NOT_ALLOWED (check_output_not_source). "
            f"Got: {result['error']['code']!r}. "
            f"(INVALID_INPUT indicates _same_path is still used; "
            f"PATH_NOT_ALLOWED indicates check_output_not_source is active.)"
        )


# ===========================================================================
# Layer 4 (ADR-PB-1): reframe's `timeline` argument must be protected
# transitively via core load_timeline's symlink guard, without any change
# to reframe.py. reframe's own D1 pre-check is a raw Path.exists(), which
# follows symlinks; the guard must come from core load_timeline.
#
# ADR-PB-1 is implemented in core otio_utils.load_timeline (Wave 1), so a
# symlinked timeline is now rejected with PATH_NOT_ALLOWED. This test pins
# that the protection is sourced entirely from core (reframe.py is not
# modified for this batch — bump-free per ADR-PB-6).
# ===========================================================================


def _probe_symlink_support() -> bool:
    """Return True when the runtime environment allows symlink creation.

    Executed once at module import (collection) time so pytest.mark.skipif
    can reference the result. File-local duplication per ADR-PB-4 convention
    (mirrors clipwright-bgm/tests/test_pathpolicy_bgm.py:50-88).
    """
    try:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            real = base / "_probe_real.txt"
            real.write_bytes(b"probe")
            link = base / "_probe_link.txt"
            link.symlink_to(real)
        return True
    except OSError:
        return False


_SYMLINK_SUPPORTED: bool = _probe_symlink_support()
_SKIP_SYMLINK_REASON = (
    "Symlink creation requires elevated privileges on this system (WinError 1314)."
    " Enable Windows Developer Mode or run as Administrator."
)
_skip_no_symlinks = pytest.mark.skipif(
    not _SYMLINK_SUPPORTED,
    reason=_SKIP_SYMLINK_REASON,
)


def _try_symlink(link: Path, target: Path) -> None:
    """Create a symlink; skip the test if the OS refuses (Windows privilege)."""
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip(
            "Cannot create symlinks on this system (requires elevated privileges)"
        )


class TestTimelineSymlinkRejectedViaCore:
    """A symlinked `timeline` argument must be rejected with PATH_NOT_ALLOWED,
    proving that core load_timeline's symlink guard protects reframe even
    though reframe.py is not modified in this batch.
    """

    @_skip_no_symlinks
    def test_timeline_symlink_rejected_via_core(self, tmp_path: Path) -> None:
        """A timeline path that is a symlink to a real .otio file must be
        rejected with PATH_NOT_ALLOWED, sourced entirely from core
        load_timeline (ADR-PB-1) — not from any reframe-local check."""
        media = tmp_path / "video.mp4"
        media.write_bytes(b"dummy media")

        real_timeline_path = tmp_path / "real_timeline.otio"
        tl = new_timeline("video.mp4")
        save_timeline(tl, str(real_timeline_path))

        symlinked_timeline_path = tmp_path / "timeline_link.otio"
        _try_symlink(symlinked_timeline_path, real_timeline_path)

        output_path = tmp_path / "out.otio"

        with patch(
            "clipwright_reframe.reframe.inspect_media",
            side_effect=lambda p: _make_media_info(str(p)),
        ):
            result = reframe(
                media=str(media),
                output=str(output_path),
                options=_default_opts(),
                timeline=str(symlinked_timeline_path),
            )

        assert result["ok"] is False, (
            "A symlinked timeline must be rejected, not silently followed "
            f"(core load_timeline / ADR-PB-1). Got: {result}"
        )
        assert result["error"]["code"] == ErrorCode.PATH_NOT_ALLOWED.value, (
            "Symlinked timeline must return PATH_NOT_ALLOWED via core "
            f"load_timeline (ADR-PB-1). Got: {result['error']['code']!r}"
        )
