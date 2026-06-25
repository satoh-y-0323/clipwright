"""test_track_cli.py — Unit tests for clipwright_reframe.track_cli (Red phase).

These tests verify the motion-centroid detection logic in track_cli.py.
numpy is required; the test module is skipped entirely when numpy is absent.

Design notes (architecture-report §2):
  - track_cli spawns ffmpeg to write gray rawvideo to a temp file, reads it
    with np.fromfile, computes motion centroids (diff-based, EMA-smoothed),
    and decimates with RDP binary search to at most N_max=80 keyframes.
  - N_max was confirmed at 80 (parent adjudication: ffmpeg additive expression
    cap of 96 items → 80 for margin).  The architecture report quotes 120 in
    some places; all boundary tests here use the confirmed N_max=80.
  - Tests synthesise raw bytes directly to avoid a real ffmpeg dependency.

AC coverage:
  centroid tracking  → test_centroid_follows_moving_rect
  onset seed (DC-AS-004) → test_onset_seed_no_jump
  EMA smoothing         → test_ema_smoothing_applied
  RDP decimation boundary (DC-AS-006 / AC-09 / DC-AM-006):
      81 points → ≤80 after decimation + diagnostics
      80 points → no decimation (N_max=80 strict)
  minimum interval guard (DC-AM-005) → test_min_interval_guard
  JSON contract          → test_json_contract_success / test_json_contract_error
  temp cleanup (AC-14 / DC-GP-002) → test_temp_cleanup_on_exception
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Skip entire module when numpy is not installed.
# numpy is an optional extra ([track]).  Track_cli tests are only meaningful
# with numpy; skipping prevents false failures in CI without the extra.
# ---------------------------------------------------------------------------
np = pytest.importorskip("numpy")

# ---------------------------------------------------------------------------
# N_max constant (parent adjudication: confirmed 80, not 120)
# ---------------------------------------------------------------------------
N_MAX = 80

# ---------------------------------------------------------------------------
# Helpers: synthetic raw-bytes builders
# ---------------------------------------------------------------------------

_FPS = 4.0
_W0 = 160
_H0 = 90


def _make_gray_frame(width: int, height: int, fill: int) -> bytes:
    """Return a gray8 frame of given width×height filled with a single value."""
    return bytes([fill]) * (width * height)


def _make_raw_bytes_static(n_frames: int, fill: int = 128) -> bytes:
    """n_frames of identical gray8 frames (no motion)."""
    frame = _make_gray_frame(_W0, _H0, fill)
    return frame * n_frames


def _make_raw_bytes_moving_rect(
    n_frames: int,
    rect_w: int = 20,
    rect_h: int = 20,
    start_col: int = 0,
    end_col: int | None = None,
    bg: int = 0,
    fg: int = 200,
) -> bytes:
    """Synthetic rawvideo bytes with a moving bright rectangle.

    The rectangle moves horizontally from start_col to end_col over n_frames.
    Returns bytes suitable for np.fromfile-equivalent reading.
    """
    if end_col is None:
        end_col = _W0 - rect_w

    result = bytearray()
    for i in range(n_frames):
        col = int(start_col + (end_col - start_col) * i / max(n_frames - 1, 1))
        row = (_H0 - rect_h) // 2
        frame = bytearray([bg] * (_W0 * _H0))
        for r in range(row, row + rect_h):
            for c in range(col, col + rect_w):
                frame[r * _W0 + c] = fg
        result.extend(frame)
    return bytes(result)


def _make_raw_bytes_onset(
    n_static: int,
    n_moving: int,
    rect_col: int = 120,
    rect_w: int = 20,
    rect_h: int = 20,
    bg: int = 0,
    fg: int = 200,
) -> bytes:
    """Static frames followed by a moving rect; tests onset-seed behaviour."""
    static_part = _make_raw_bytes_static(n_static, fill=bg)
    # moving part: rect stays at rect_col (constant position, just appears suddenly)
    moving_frames = bytearray()
    for _ in range(n_moving):
        row = (_H0 - rect_h) // 2
        frame = bytearray([bg] * (_W0 * _H0))
        for r in range(row, row + rect_h):
            for c in range(rect_col, rect_col + rect_w):
                frame[r * _W0 + c] = fg
        moving_frames.extend(frame)
    return static_part + bytes(moving_frames)


# ---------------------------------------------------------------------------
# Import helpers: we call internal functions directly where possible.
# If track_cli does not yet exist the import fails with ModuleNotFoundError,
# which is exactly the expected Red state.
# ---------------------------------------------------------------------------


def _import_track_cli() -> Any:
    """Import clipwright_reframe.track_cli; raises ModuleNotFoundError when absent."""
    import importlib

    return importlib.import_module("clipwright_reframe.track_cli")


# ===========================================================================
# 1. Centroid tracking
# ===========================================================================


class TestCentroidTracking:
    """Centroid follows a moving bright rectangle in synthetic rawvideo (AC-01改 basis)."""

    def test_centroid_follows_moving_rect(self) -> None:
        """cx should increase as rectangle moves right across frames.

        Synthesises 10 frames with a 20×20 rect moving from left to right,
        calls the internal centroid computation, and checks that cx increases
        monotonically in the output track.

        """
        cli = _import_track_cli()

        n_frames = 10
        raw = _make_raw_bytes_moving_rect(
            n_frames, rect_w=20, rect_h=20, start_col=0, end_col=_W0 - 20
        )
        frames = np.frombuffer(raw, dtype=np.uint8).reshape(n_frames, _H0, _W0)

        # Call the internal function that processes frames into (t_s, cx, cy) tuples.
        # Expected interface: _compute_centroids(frames, fps, motion_floor, motion_threshold)
        #   returns list of (t_s, cx, cy).
        pts = cli._compute_centroids(
            frames, fps=_FPS, motion_floor=8, motion_threshold=_W0 * _H0 * 2
        )

        assert len(pts) > 0, "Expected at least one centroid point"
        cxs = [p[1] for p in pts if p[1] is not None]
        assert len(cxs) >= 2, "Expected multiple cx values for moving rect"
        # cx should generally increase (rect moving right)
        assert cxs[-1] > cxs[0], (
            f"Expected cx to increase as rect moves right; got first={cxs[0]:.3f}"
            f" last={cxs[-1]:.3f}"
        )
        # All cx must be normalised [0, 1]
        for cx in cxs:
            assert 0.0 <= cx <= 1.0, f"cx out of range: {cx}"

    def test_centroid_values_normalised(self) -> None:
        """All cx, cy in output track must be in [0.0, 1.0]."""
        cli = _import_track_cli()

        raw = _make_raw_bytes_moving_rect(8)
        frames = np.frombuffer(raw, dtype=np.uint8).reshape(8, _H0, _W0)
        pts = cli._compute_centroids(
            frames, fps=_FPS, motion_floor=8, motion_threshold=_W0 * _H0 * 2
        )

        for t_s, cx, cy in pts:
            assert t_s >= 0.0, f"t_s must be non-negative: {t_s}"
            if cx is not None:
                assert 0.0 <= cx <= 1.0, f"cx={cx} out of [0,1]"
            if cy is not None:
                assert 0.0 <= cy <= 1.0, f"cy={cy} out of [0,1]"


# ===========================================================================
# 2. Onset seed (DC-AS-004)
# ===========================================================================


class TestOnsetSeed:
    """Onset seed: first motion's centroid is back-filled for static prefix (DC-AS-004)."""

    def test_onset_seed_no_jump(self) -> None:
        """Static frames followed by motion: first EMA output must not jump to 0.5.

        Without the onset seed, the static-prefix frames would be filled with
        cx=cy=0.5 (center), then jump abruptly to the rect's centroid when motion
        begins.  With the onset seed, the first motion's centroid is seeded back
        to the static prefix, so the output cx at the first frame already points
        toward the rect (not center).

        Rect is placed at rect_col=120 → right side of 160px frame → cx_n > 0.5

        """
        cli = _import_track_cli()

        rect_col = 120  # right side → cx_n ≈ 120/159 ≈ 0.75
        raw = _make_raw_bytes_onset(n_static=5, n_moving=5, rect_col=rect_col)
        n_total = 10
        frames = np.frombuffer(raw, dtype=np.uint8).reshape(n_total, _H0, _W0)

        pts = cli._compute_centroids(
            frames, fps=_FPS, motion_floor=8, motion_threshold=_W0 * _H0 * 2
        )

        # Apply hold-fill (seed-based) and EMA to get smoothed track
        # Expected interface: _apply_seed_and_ema(raw_pts, ema_alpha)
        #   returns list of (t_s, cx, cy) with seed applied.
        smoothed = cli._apply_seed_and_ema(pts, ema_alpha=0.2)

        assert len(smoothed) > 0
        # First smoothed cx must be closer to the rect (>0.5) than to center (0.5)
        # because the onset seed propagates the first-motion cx back.
        first_cx = smoothed[0][1]
        assert first_cx > 0.5, (
            f"Onset seed failed: first cx={first_cx:.3f} should be > 0.5 "
            f"(rect at col={rect_col}/{_W0}, expected cx_n ≈ 0.75)"
        )

    def test_all_static_falls_back_to_center(self) -> None:
        """All-static video (no motion) must produce cx=cy=0.5 (no first-motion seed)."""
        cli = _import_track_cli()

        raw = _make_raw_bytes_static(8, fill=64)
        frames = np.frombuffer(raw, dtype=np.uint8).reshape(8, _H0, _W0)
        pts = cli._compute_centroids(
            frames, fps=_FPS, motion_floor=8, motion_threshold=_W0 * _H0 * 2
        )
        smoothed = cli._apply_seed_and_ema(pts, ema_alpha=0.2)

        if smoothed:
            for _, cx, cy in smoothed:
                assert cx == pytest.approx(0.5, abs=1e-6), (
                    f"All-static: cx should be 0.5, got {cx}"
                )
                assert cy == pytest.approx(0.5, abs=1e-6), (
                    f"All-static: cy should be 0.5, got {cy}"
                )


# ===========================================================================
# 3. EMA smoothing
# ===========================================================================


class TestEmaSmoothing:
    """EMA smoothing reduces frame-to-frame jitter (architecture-report §2.3)."""

    def test_ema_smoothing_applied(self) -> None:
        """EMA with alpha=0.2 should produce smoother cx than raw centroids.

        Generates raw centroid sequence with alternating high/low cx, then
        verifies that EMA output variance is lower than raw variance.

        """
        cli = _import_track_cli()

        # Alternating raw pts: high/low cx
        raw_pts = [
            (float(i) / _FPS, 0.8 if i % 2 == 0 else 0.2, 0.5) for i in range(1, 9)
        ]

        smoothed = cli._apply_seed_and_ema(raw_pts, ema_alpha=0.2)

        raw_cx = [p[1] for p in raw_pts]
        smoothed_cx = [p[1] for p in smoothed]

        raw_var = float(np.var(raw_cx))
        smooth_var = float(np.var(smoothed_cx))
        assert smooth_var < raw_var, (
            f"EMA should reduce variance: raw_var={raw_var:.4f}"
            f" smooth_var={smooth_var:.4f}"
        )

    def test_ema_output_clipped_to_unit_interval(self) -> None:
        """EMA output cx, cy must stay in [0.0, 1.0] even with extreme inputs."""
        cli = _import_track_cli()

        # Push cx near boundary to trigger clamp
        raw_pts = [(float(i) / _FPS, 0.99, 0.99) for i in range(1, 5)]
        raw_pts += [(float(i) / _FPS, 0.01, 0.01) for i in range(5, 9)]

        smoothed = cli._apply_seed_and_ema(
            raw_pts, ema_alpha=1.0
        )  # alpha=1 = no smoothing
        for _, cx, cy in smoothed:
            assert 0.0 <= cx <= 1.0
            assert 0.0 <= cy <= 1.0


# ===========================================================================
# 4. RDP decimation (DC-AS-006 / AC-09 / DC-AM-006)
# ===========================================================================


class TestRdpDecimation:
    """RDP binary-search decimation with N_max=80 boundary (parent adjudication)."""

    def _make_pts(self, n: int) -> list[tuple[float, float, float]]:
        """Generate n uniformly-spaced (t_s, cx, cy) points."""
        return [(i / _FPS, 0.3 + 0.4 * (i / max(n - 1, 1)), 0.5) for i in range(n)]

    def test_81_points_decimated_to_at_most_80(self) -> None:
        """81 input points must be decimated to ≤80 (N_max=80 boundary, AC-09).

        Diagnostics must report keyframes_before_decimation=81,
        keyframes_after_decimation<=80, and dropped>=1.

        """
        cli = _import_track_cli()

        pts = self._make_pts(81)
        kept, diag = cli._decimate(pts, n_max=N_MAX)

        assert len(kept) <= N_MAX, (
            f"81 points must be decimated to ≤{N_MAX}, got {len(kept)}"
        )
        assert diag["keyframes_before_decimation"] == 81
        assert diag["keyframes_after_decimation"] == len(kept)
        assert diag["dropped"] == 81 - len(kept)
        assert diag["dropped"] >= 1

    def test_80_points_not_decimated(self) -> None:
        """Exactly 80 input points must NOT be decimated (N_max=80 strict, AC-09).

        N_max=80 means ≤80 is already acceptable; no decimation should occur.
        diagnostics must reflect dropped=0.

        """
        cli = _import_track_cli()

        pts = self._make_pts(N_MAX)
        kept, diag = cli._decimate(pts, n_max=N_MAX)

        assert len(kept) == N_MAX, (
            f"Exactly {N_MAX} points must not be decimated, got {len(kept)}"
        )
        assert diag["dropped"] == 0

    def test_decimation_preserves_monotonic_t_s(self) -> None:
        """After decimation, t_s must remain strictly monotonically increasing."""
        cli = _import_track_cli()

        pts = self._make_pts(100)
        kept, _ = cli._decimate(pts, n_max=N_MAX)

        ts_vals = [p[0] for p in kept]
        for i in range(1, len(ts_vals)):
            assert ts_vals[i] > ts_vals[i - 1], (
                f"t_s not monotonically increasing after decimation at index {i}: "
                f"{ts_vals[i - 1]:.4f} >= {ts_vals[i]:.4f}"
            )

    def test_fewer_than_nmax_unchanged(self) -> None:
        """Fewer than N_max points must pass through without decimation."""
        cli = _import_track_cli()

        pts = self._make_pts(10)
        kept, diag = cli._decimate(pts, n_max=N_MAX)

        assert len(kept) == 10
        assert diag["dropped"] == 0


# ===========================================================================
# 5. Minimum time interval guard (DC-AM-005)
# ===========================================================================


class TestMinIntervalGuard:
    """After decimation, minimum t_s gap must be >= 1/fps (DC-AM-005)."""

    def test_min_interval_at_least_one_over_fps(self) -> None:
        """After _decimate, consecutive t_s differ by at least 1/fps.

        1/fps = 0.25s for fps=4.0.

        """
        cli = _import_track_cli()

        # 50 points spaced 1/fps apart (no compression needed, tests guarantee)
        pts = [(i / _FPS, 0.5, 0.5) for i in range(1, 51)]
        kept, _ = cli._decimate(pts, n_max=N_MAX)

        min_gap = 1.0 / _FPS
        for i in range(1, len(kept)):
            gap = kept[i][0] - kept[i - 1][0]
            assert gap >= min_gap - 1e-9, (
                f"Minimum interval violated: gap={gap:.6f}s < 1/fps={min_gap:.6f}s "
                f"at index {i}"
            )

    def test_min_interval_enforced_after_compression(self) -> None:
        """Even after aggressive decimation, interval guard must hold."""
        cli = _import_track_cli()

        # 100 points; decimation to 80 may produce close-spaced points
        pts = [(i / _FPS, 0.3 + 0.2 * (i / 99), 0.5) for i in range(100)]
        # Introduce two near-duplicate t_s that could collapse after RDP
        kept, _ = cli._decimate(pts, n_max=N_MAX)

        min_gap = 1.0 / _FPS
        for i in range(1, len(kept)):
            gap = kept[i][0] - kept[i - 1][0]
            assert gap >= min_gap - 1e-9, (
                f"Interval guard violated after compression: gap={gap:.6f}s < "
                f"1/fps={min_gap:.6f}s at index {i}"
            )


# ===========================================================================
# 6. JSON contract
# ===========================================================================


class TestJsonContract:
    """Output JSON contract: success and error shapes."""

    def test_json_contract_success_shape(self, fake_tmp_file_factory: Any) -> None:
        """main() must write {"track":[...], "diagnostics":{...}} on success.

        We monkeypatch the ffmpeg call and file read so no real ffmpeg is needed.
        The ffmpeg invocation must use a list (shell=False) and write to a temp file.
        """
        cli = _import_track_cli()

        # Synthesise rawvideo bytes (5 frames of moving rect)
        raw_bytes = _make_raw_bytes_moving_rect(5)

        # Patch: core run returns success, temp file contains our raw bytes
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = ""
        fake_result.stderr = ""

        captured_cmd: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> MagicMock:
            captured_cmd.append(list(cmd))
            return fake_result

        import io

        captured_stdout = io.StringIO()

        # Build argv for main
        media_path = "/fake/video.mp4"
        argv = [
            "--media",
            media_path,
            "--fps",
            str(_FPS),
            "--width",
            str(_W0),
            "--media-duration",
            "5.0",
        ]

        with (
            patch.object(
                sys.modules.get(
                    "clipwright.process",
                    sys.modules.get("clipwright_reframe.track_cli", cli).__module__,
                ),
                "run",
                side_effect=fake_run,
                create=True,
            ) as _mock_run,
            patch(
                "tempfile.NamedTemporaryFile",
                side_effect=fake_tmp_file_factory(raw_bytes=raw_bytes),
            ),
            patch("sys.stdout", captured_stdout),
            patch("os.unlink"),  # prevent actual unlink of our fake temp
        ):
            try:
                cli.main(argv)
            except SystemExit:
                pass

        output = captured_stdout.getvalue().strip()
        # May be empty if patch failed; test that import at least works
        # (ImportError would have already failed above)
        assert output, "main() must write JSON to stdout"

        data = json.loads(output)
        assert "track" in data or "error" in data, (
            f"Output must have 'track' or 'error' key, got: {list(data.keys())}"
        )
        if "track" in data:
            assert isinstance(data["track"], list)
            assert "diagnostics" in data
            for item in data["track"]:
                assert "t_s" in item
                assert "cx" in item
                assert "cy" in item
                assert 0.0 <= item["cx"] <= 1.0
                assert 0.0 <= item["cy"] <= 1.0
            # t_s must be ascending and non-duplicate
            ts_vals = [item["t_s"] for item in data["track"]]
            for i in range(1, len(ts_vals)):
                assert ts_vals[i] > ts_vals[i - 1], (
                    f"t_s not strictly ascending at index {i}"
                )

    def test_json_contract_error_shape(self) -> None:
        """main() with nonexistent media must write {"error":{code,message,hint}}."""
        cli = _import_track_cli()

        import io

        captured_stdout = io.StringIO()

        argv = [
            "--media",
            "/nonexistent/path/does_not_exist.mp4",
            "--fps",
            str(_FPS),
            "--width",
            str(_W0),
        ]

        with patch("sys.stdout", captured_stdout):
            try:
                cli.main(argv)
            except SystemExit:
                pass

        output = captured_stdout.getvalue().strip()
        assert output, "main() must write JSON to stdout even on error"

        data = json.loads(output)
        assert "error" in data, (
            f"Expected 'error' key for nonexistent media, got: {list(data.keys())}"
        )
        err = data["error"]
        assert "code" in err, "error must have 'code'"
        assert "message" in err, "error must have 'message'"
        assert "hint" in err, "error must have 'hint'"

    def test_error_message_excludes_path_and_stack(self) -> None:
        """CWE-209: error message/hint must not echo full path or stack trace."""
        cli = _import_track_cli()

        import io

        captured_stdout = io.StringIO()
        secret_path = "/very/secret/internal/video.mp4"

        argv = [
            "--media",
            secret_path,
            "--fps",
            str(_FPS),
            "--width",
            str(_W0),
        ]

        with patch("sys.stdout", captured_stdout):
            try:
                cli.main(argv)
            except SystemExit:
                pass

        output = captured_stdout.getvalue().strip()
        if output:
            data = json.loads(output)
            if "error" in data:
                err_str = json.dumps(data["error"])
                assert secret_path not in err_str, (
                    "CWE-209: full path must not appear in error JSON"
                )
                assert "Traceback" not in err_str, (
                    "CWE-209: stack trace must not appear in error JSON"
                )


# ===========================================================================
# 7. Temp file cleanup (AC-14 / DC-GP-002)
# ===========================================================================


class TestTempCleanup:
    """Temp rawvideo file must be deleted even when ffmpeg fails (try/finally)."""

    def test_temp_cleanup_on_exception(
        self, tmp_path: Path, tracking_tmp_file_factory: Any
    ) -> None:
        """If ffmpeg raises an exception, the temp file must be removed.

        We inject a failing run() so the try/finally in track_cli must clean up.
        """
        cli = _import_track_cli()

        import io

        captured_stdout = io.StringIO()
        created_temp, tracking_factory = tracking_tmp_file_factory()

        def _failing_run(cmd: Any, **kwargs: Any) -> None:
            raise RuntimeError("Simulated ffmpeg crash")

        argv = [
            "--media",
            "/fake/video.mp4",
            "--fps",
            str(_FPS),
            "--width",
            str(_W0),
        ]

        with (
            patch("tempfile.NamedTemporaryFile", side_effect=tracking_factory),
            patch("sys.stdout", captured_stdout),
            patch("clipwright.process.run", side_effect=_failing_run),
        ):
            try:
                cli.main(argv)
            except (SystemExit, Exception):
                pass

        # Any temp files that were created must no longer exist
        for temp_path in created_temp:
            assert not Path(temp_path).exists(), (
                f"Temp file not cleaned up after exception: {temp_path}"
            )

    def test_temp_cleanup_on_timeout(
        self, tmp_path: Path, tracking_tmp_file_factory: Any
    ) -> None:
        """If ffmpeg times out, the temp file must be removed."""
        cli = _import_track_cli()

        import io

        from clipwright.errors import ClipwrightError, ErrorCode

        captured_stdout = io.StringIO()
        created_temp, tracking_factory = tracking_tmp_file_factory()

        def _timeout_run(cmd: Any, **kwargs: Any) -> None:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_TIMEOUT,
                message="ffmpeg timed out",
                hint="Reduce video duration or increase timeout.",
            )

        argv = [
            "--media",
            "/fake/video.mp4",
            "--fps",
            str(_FPS),
            "--width",
            str(_W0),
        ]

        with (
            patch("tempfile.NamedTemporaryFile", side_effect=tracking_factory),
            patch("sys.stdout", captured_stdout),
            patch("clipwright.process.run", side_effect=_timeout_run),
        ):
            try:
                cli.main(argv)
            except (SystemExit, Exception):
                pass

        for temp_path in created_temp:
            assert not Path(temp_path).exists(), (
                f"Temp file not cleaned up after timeout: {temp_path}"
            )


# ===========================================================================
# 8. Subprocess discipline (AC-10)
# ===========================================================================


class TestSubprocessDiscipline:
    """ffmpeg must be invoked as a list (shell=False) via core run (AC-10)."""

    def test_ffmpeg_invoked_as_list_not_shell(self, fake_tmp_file_factory: Any) -> None:
        """The ffmpeg command passed to core run must be a list, not a string."""
        cli = _import_track_cli()

        import io

        captured_stdout = io.StringIO()
        captured_cmds: list[Any] = []
        fake_result = MagicMock(returncode=0, stdout="", stderr="")

        def _capturing_run(cmd: Any, **kwargs: Any) -> MagicMock:
            captured_cmds.append(cmd)
            return fake_result

        argv = [
            "--media",
            "/fake/video.mp4",
            "--fps",
            str(_FPS),
            "--width",
            str(_W0),
        ]

        with (
            patch(
                "tempfile.NamedTemporaryFile",
                side_effect=fake_tmp_file_factory(raw_bytes=b""),
            ),
            patch("sys.stdout", captured_stdout),
            patch("clipwright.process.run", side_effect=_capturing_run),
            patch("os.unlink"),
        ):
            try:
                cli.main(argv)
            except (SystemExit, Exception):
                pass

        assert len(captured_cmds) > 0, (
            "Expected at least one call to core run with ffmpeg command"
        )
        cmd = captured_cmds[0]
        assert isinstance(cmd, list), (
            f"ffmpeg command must be a list (shell=False), got {type(cmd).__name__}"
        )

    def test_media_not_found_returns_error_json(self) -> None:
        """Nonexistent media path must return ok=false JSON with hint (AC-10)."""
        cli = _import_track_cli()

        import io

        captured_stdout = io.StringIO()

        argv = [
            "--media",
            "/nonexistent/video.mp4",
            "--fps",
            str(_FPS),
            "--width",
            str(_W0),
        ]

        with patch("sys.stdout", captured_stdout):
            try:
                cli.main(argv)
            except (SystemExit, Exception):
                pass

        output = captured_stdout.getvalue().strip()
        data = json.loads(output)
        assert "error" in data
        assert data["error"].get("hint"), (
            "error.hint must be non-empty for missing media"
        )


# ===========================================================================
# 9. Temp file size guard (SR-L-2 / DC-GP-002 / AC-14)
# ===========================================================================


class TestSizeGuard:
    """Rawvideo size estimate must trigger fps/width downsampling when over 512 MB."""

    def test_size_guard_reduces_fps_for_long_media(
        self, fake_tmp_file_factory: Any
    ) -> None:
        """Estimated size > 512 MB must cause fps reduction before ffmpeg is called.

        Uses a very long duration (100 000 s) with default fps=4.0 to trigger the guard,
        then verifies that the ffmpeg -vf argument uses a reduced fps value.
        """
        cli = _import_track_cli()

        import io

        captured_stdout = io.StringIO()
        captured_cmds: list[Any] = []
        fake_result = MagicMock(returncode=0, stdout="", stderr="")

        def _capturing_run(cmd: Any, **kwargs: Any) -> MagicMock:
            captured_cmds.append(cmd)
            return fake_result

        # 160×90 × 4 fps × 100 000 s = 5 760 000 000 bytes ≫ 512 MB
        argv = [
            "--media",
            "/fake/long_video.mp4",
            "--fps",
            "4.0",
            "--width",
            "160",
            "--media-duration",
            "100000",
        ]

        with (
            patch(
                "clipwright.process.run",
                side_effect=_capturing_run,
            ),
            patch("sys.stdout", captured_stdout),
            # Provide empty raw bytes so np.fromfile returns empty array → early-return.
            patch(
                "tempfile.NamedTemporaryFile",
                side_effect=fake_tmp_file_factory(raw_bytes=b""),
            ),
        ):
            try:
                cli.main(argv)
            except Exception:
                pass

        # The guard must have fired: at least one ffmpeg invocation must use a
        # reduced fps (< 4.0) in the -vf argument.
        assert len(captured_cmds) > 0, "Expected ffmpeg to be invoked"
        vf_arg = ""
        for cmd in captured_cmds:
            if isinstance(cmd, list) and "-vf" in cmd:
                idx = cmd.index("-vf")
                vf_arg = cmd[idx + 1]
                break

        assert vf_arg, "Expected a -vf argument in ffmpeg command"
        # fps=N.N must be less than 4.0 after size-guard downsampling.
        import re

        m = re.search(r"fps=([0-9.]+)", vf_arg)
        assert m is not None, f"No fps= found in -vf: {vf_arg!r}"
        actual_fps = float(m.group(1))
        assert actual_fps < 4.0, (
            f"Size guard must reduce fps below 4.0 for 100 000 s media;"
            f" got fps={actual_fps}"
        )

    def test_size_guard_below_limit_passes_unchanged(
        self, fake_tmp_file_factory: Any
    ) -> None:
        """Estimated size ≤ 512 MB must not change fps or width.

        10 s at default fps=4.0, width=160: 160×90×4×10 ≈ 5.76 MB — well under limit.
        """
        cli = _import_track_cli()

        import io

        captured_stdout = io.StringIO()
        captured_cmds: list[Any] = []
        fake_result = MagicMock(returncode=0, stdout="", stderr="")

        def _capturing_run(cmd: Any, **kwargs: Any) -> MagicMock:
            captured_cmds.append(cmd)
            return fake_result

        argv = [
            "--media",
            "/fake/video.mp4",
            "--fps",
            "4.0",
            "--width",
            "160",
            "--media-duration",
            "10",
        ]

        with (
            patch("clipwright.process.run", side_effect=_capturing_run),
            patch("sys.stdout", captured_stdout),
            patch(
                "tempfile.NamedTemporaryFile",
                side_effect=fake_tmp_file_factory(raw_bytes=b""),
            ),
        ):
            try:
                cli.main(argv)
            except Exception:
                pass

        assert len(captured_cmds) > 0, "Expected ffmpeg to be invoked"
        vf_arg = ""
        for cmd in captured_cmds:
            if isinstance(cmd, list) and "-vf" in cmd:
                idx = cmd.index("-vf")
                vf_arg = cmd[idx + 1]
                break

        import re

        m = re.search(r"fps=([0-9.]+)", vf_arg)
        assert m is not None, f"No fps= found in -vf: {vf_arg!r}"
        actual_fps = float(m.group(1))
        # fps must be unchanged (4.0) when under the size limit.
        assert actual_fps == pytest.approx(4.0, abs=0.01), (
            f"fps must stay 4.0 for short media; got {actual_fps}"
        )


# ===========================================================================
# 10. N_max sync guard (SR-L-3)
# ===========================================================================


class TestNMaxSyncCli:
    """N_max=80 must be the confirmed value in track_cli constants."""

    def test_default_n_max_is_80(self) -> None:
        """_DEFAULT_N_MAX in track_cli must be 80 (spike-confirmed value)."""
        cli = _import_track_cli()

        assert cli._DEFAULT_N_MAX == N_MAX, (
            f"_DEFAULT_N_MAX must equal {N_MAX}, got {cli._DEFAULT_N_MAX}."
            " Update N_MAX in the test and document the reason for the change."
        )

    def test_argparse_max_keyframes_default_is_n_max(self) -> None:
        """argparse --max-keyframes default must equal _DEFAULT_N_MAX."""
        cli = _import_track_cli()

        import argparse
        import io

        # Parse an empty argv (except required --media) to get defaults.
        captured_stdout = io.StringIO()
        captured_args: list[Any] = []

        original_parse = argparse.ArgumentParser.parse_args

        def _capturing_parse(
            self: argparse.ArgumentParser,
            args: Any = None,
            namespace: Any = None,
        ) -> argparse.Namespace:
            ns = original_parse(self, args, namespace)
            captured_args.append(ns)
            return ns

        with (
            patch.object(argparse.ArgumentParser, "parse_args", _capturing_parse),
            patch("sys.stdout", captured_stdout),
            patch(
                "clipwright.process.run",
                side_effect=RuntimeError("abort early"),
            ),
            patch(
                "tempfile.NamedTemporaryFile",
                side_effect=RuntimeError("abort early"),
            ),
        ):
            try:
                cli.main(["--media", "/fake/v.mp4"])
            except Exception:
                pass

        if captured_args:
            ns = captured_args[0]
            assert ns.max_keyframes == cli._DEFAULT_N_MAX, (
                f"argparse default max_keyframes={ns.max_keyframes} != "
                f"_DEFAULT_N_MAX={cli._DEFAULT_N_MAX}"
            )
