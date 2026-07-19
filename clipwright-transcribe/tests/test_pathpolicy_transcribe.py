"""test_pathpolicy_transcribe.py — Path-boundary constraint tests for transcribe_media.

Verifies removal of the same-directory output constraint while maintaining all other
safety invariants.

All tests are whisper-binary-independent: inspect_media and _run_whisper are mocked.

Test ID:
  DP-T-1  test_external_dir_output_allowed               external dir -> ok
  DP-T-2  test_clip_target_url_absolute_external_media   media outside -> abs target_url
  DP-T-3  test_output_equals_media_still_rejected        regression guard (PATH_NOT_ALLOWED)
  DP-T-4  test_non_otio_extension_still_rejected         regression guard
  DP-T-5  test_missing_parent_dir_still_rejected         regression guard

model-path symlink coverage (architecture-report-20260720-082027.md ADR-PB-3, G1):
  test_model_path_symlink_rejected        options.model_path symlink -> PATH_NOT_ALLOWED
  test_env_model_symlink_rejected         env CLIPWRIGHT_WHISPER_MODEL symlink -> PATH_NOT_ALLOWED
                                           (fail-closed: must not silently fall through)
  test_model_symlink_message_no_fullpath  CWE-209: message has no full dir path, hint non-empty
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import opentimelineio as otio
import pytest
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.otio_utils import load_timeline
from clipwright.schemas import MediaInfo, RationalTimeModel, StreamInfo

from clipwright_transcribe.schemas import TranscribeOptions
from clipwright_transcribe.transcribe import WhisperRun, _resolve_model_path

# ===========================================================================
# Helpers
# ===========================================================================

FPS = 30.0


def _make_media_info(
    path: str,
    *,
    duration_sec: float = 10.0,
    rate: float = FPS,
) -> MediaInfo:
    """Build a MediaInfo with one video + one audio stream."""
    streams = [
        StreamInfo(index=0, codec_type="video", codec_name="h264"),
        StreamInfo(index=1, codec_type="audio", codec_name="aac"),
    ]
    return MediaInfo(
        path=path,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        duration=RationalTimeModel(value=duration_sec * rate, rate=rate),
        streams=streams,
        bit_rate=8_000_000,
    )


def _fake_whisper_run() -> WhisperRun:
    """Return a minimal WhisperRun with no segments for use as a _run_whisper mock."""
    return WhisperRun(
        segments=[],
        language="en",
        backend={"device": "cpu", "detail": "cpu"},
        wall_seconds=0.1,
    )


def _opts(**kwargs: object) -> TranscribeOptions:
    return TranscribeOptions(**kwargs)  # type: ignore[arg-type]


# ===========================================================================
# DP-T-1 / DP-T-2: external dir output allowed + absolute media ref
# ===========================================================================


class TestExternalDirOutput:
    """Output placed in a directory different from media must now be accepted.

    Tests are whisper-independent: inspect_media and _run_whisper are mocked.
    """

    def test_external_dir_output_allowed(self, tmp_path: Path) -> None:
        """DP-T-1: transcribe_media must succeed when output dir differs from media dir.

        Whisper-independent: inspect_media and _run_whisper are mocked so no real
        whisper binary is required.
        """
        from clipwright_transcribe.transcribe import transcribe_media

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "work"
        out_dir.mkdir()
        model_dir = tmp_path / "models"
        model_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"x")
        model = model_dir / "ggml-base.bin"
        model.write_bytes(b"model")
        output = out_dir / "out.otio"

        with (
            patch(
                "clipwright_transcribe.transcribe.inspect_media",
                return_value=_make_media_info(str(media)),
            ),
            patch(
                "clipwright_transcribe.transcribe._run_whisper",
                return_value=_fake_whisper_run(),
            ),
        ):
            result = transcribe_media(
                str(media), str(output), _opts(model_path=str(model))
            )

        assert result.ok is True

    def test_clip_target_url_absolute_external_media(self, tmp_path: Path) -> None:
        """DP-T-2: When media is outside otio_dir, clip target_url must be absolute.

        Whisper-independent.
        Verifies media_ref_for_otio rule (media outside otio_dir -> absolute).
        """
        from clipwright_transcribe.transcribe import transcribe_media

        media_dir = tmp_path / "src"
        media_dir.mkdir()
        out_dir = tmp_path / "work"
        out_dir.mkdir()
        model_dir = tmp_path / "models"
        model_dir.mkdir()

        media = media_dir / "video.mp4"
        media.write_bytes(b"x")
        model = model_dir / "ggml-base.bin"
        model.write_bytes(b"model")
        output = out_dir / "out.otio"

        with (
            patch(
                "clipwright_transcribe.transcribe.inspect_media",
                return_value=_make_media_info(str(media)),
            ),
            patch(
                "clipwright_transcribe.transcribe._run_whisper",
                return_value=_fake_whisper_run(),
            ),
        ):
            result = transcribe_media(
                str(media), str(output), _opts(model_path=str(model))
            )

        assert result.ok is True

        tl = load_timeline(str(output))
        v1 = next(t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video)
        clips = [it for it in v1 if isinstance(it, otio.schema.Clip)]
        assert clips, "No clips in V1 after transcribe_media succeeded"
        for clip in clips:
            ref = clip.media_reference
            assert isinstance(ref, otio.schema.ExternalReference)
            ref_path = Path(ref.target_url)
            assert ref_path.is_absolute(), (
                "target_url must be absolute when media is outside otio_dir; "
                f"got {ref.target_url!r}"
            )
            try:
                resolved = str(ref_path.resolve())
            except OSError:
                resolved = str(ref_path.absolute())
            assert resolved == str(media.resolve()), (
                f"target_url resolved to {resolved!r}, expected {media.resolve()!r}"
            )


# ===========================================================================
# DP-T-3 .. DP-T-5: regression guards (already pass; must not regress)
# ===========================================================================


class TestRegressionGuards:
    """Removing the same-dir constraint must not weaken other path safety invariants."""

    def test_output_equals_media_still_rejected(self, tmp_path: Path) -> None:
        """DP-T-3: output path identical to media path must return PATH_NOT_ALLOWED."""
        from clipwright_transcribe.transcribe import transcribe_media

        media = tmp_path / "same.otio"
        media.write_bytes(b"x")

        result = transcribe_media(str(media), str(media), _opts())

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.PATH_NOT_ALLOWED

    def test_non_otio_extension_still_rejected(self, tmp_path: Path) -> None:
        """DP-T-4: output with non-.otio extension must remain INVALID_INPUT."""
        from clipwright_transcribe.transcribe import transcribe_media

        media = tmp_path / "video.mp4"
        media.write_bytes(b"x")
        output = tmp_path / "out.srt"

        result = transcribe_media(str(media), str(output), _opts())

        assert result.ok is False
        assert result.error is not None
        assert result.error.code == ErrorCode.INVALID_INPUT

    def test_missing_parent_dir_still_rejected(self, tmp_path: Path) -> None:
        """DP-T-5: output whose parent directory does not exist must remain rejected."""
        from clipwright_transcribe.transcribe import transcribe_media

        media = tmp_path / "video.mp4"
        media.write_bytes(b"x")
        output = tmp_path / "nonexistent_dir" / "out.otio"

        result = transcribe_media(str(media), str(output), _opts())

        assert result.ok is False
        assert result.error is not None
        assert result.error.code in (
            ErrorCode.INVALID_INPUT,
            ErrorCode.FILE_NOT_FOUND,
        )


# ===========================================================================
# G1 (ADR-PB-3): _resolve_model_path candidate loop must fail-closed on
# symlinks instead of silently accepting them via os.path.isfile.
#
# Reference implementation for _resolve_model_path (architecture-report
# §2 ADR-PB-3):
#   try:
#       validate_source_file(candidate)
#   except ClipwrightError as exc:
#       if exc.code == ErrorCode.FILE_NOT_FOUND:
#           continue  # discard full-path message (CWE-209)
#       raise  # PATH_NOT_ALLOWED: propagate, do not fall through to env
#   return candidate
#
# Current impl (transcribe.py L308-339) uses os.path.isfile(candidate),
# which follows symlinks unconditionally, so these tests are expected to
# FAIL (candidate accepted / falls through to DEPENDENCY_MISSING) until
# ADR-PB-3 is implemented. This is the intended Red-phase failure mode
# for this batch (implementation is out of scope for this task).
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


class TestResolveModelPathSymlinkRejection:
    """options.model_path / env CLIPWRIGHT_WHISPER_MODEL symlinks must be
    rejected with PATH_NOT_ALLOWED (fail-closed), not silently followed or
    masked by falling through to the next candidate / DEPENDENCY_MISSING.
    """

    @_skip_no_symlinks
    def test_model_path_symlink_rejected(self, tmp_path: Path) -> None:
        """A symlinked options.model_path must raise PATH_NOT_ALLOWED, not resolve."""
        real_model = tmp_path / "real_model.bin"
        real_model.write_bytes(b"dummy model bytes")
        linked_model = tmp_path / "model_link.bin"
        _try_symlink(linked_model, real_model)

        with pytest.raises(ClipwrightError) as exc_info:
            _resolve_model_path(_opts(model_path=str(linked_model)))

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED, (
            "A symlinked model_path must be rejected fail-closed (ADR-PB-3), not "
            f"followed. Got: {exc_info.value.code!r}"
        )

    @_skip_no_symlinks
    def test_env_model_symlink_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A symlinked env CLIPWRIGHT_WHISPER_MODEL must raise PATH_NOT_ALLOWED.

        Regression guard for the fail-closed requirement: a symlinked env
        candidate must not be silently discarded (which would surface as
        DEPENDENCY_MISSING instead of PATH_NOT_ALLOWED).
        """
        real_model = tmp_path / "real_env_model.bin"
        real_model.write_bytes(b"dummy model bytes")
        linked_model = tmp_path / "env_model_link.bin"
        _try_symlink(linked_model, real_model)

        monkeypatch.setenv("CLIPWRIGHT_WHISPER_MODEL", str(linked_model))

        with pytest.raises(ClipwrightError) as exc_info:
            _resolve_model_path(_opts())

        assert exc_info.value.code == ErrorCode.PATH_NOT_ALLOWED, (
            "A symlinked env model candidate must be rejected fail-closed "
            f"(ADR-PB-3), not masked as DEPENDENCY_MISSING. Got: {exc_info.value.code!r}"
        )

    @_skip_no_symlinks
    def test_model_symlink_message_no_fullpath(self, tmp_path: Path) -> None:
        """Symlinked-model PATH_NOT_ALLOWED message must not expose the full
        directory path (CWE-209), and hint must be non-empty."""
        real_model = tmp_path / "real_model.bin"
        real_model.write_bytes(b"dummy model bytes")
        linked_model = tmp_path / "model_link.bin"
        _try_symlink(linked_model, real_model)

        with pytest.raises(ClipwrightError) as exc_info:
            _resolve_model_path(_opts(model_path=str(linked_model)))

        assert str(tmp_path) not in exc_info.value.message, (
            "Symlinked model_path error message must not expose the full "
            f"directory path (CWE-209). Got: {exc_info.value.message!r}"
        )
        assert exc_info.value.hint, "PATH_NOT_ALLOWED error must carry a non-empty hint"
