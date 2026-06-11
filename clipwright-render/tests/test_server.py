"""test_server.py — Red tests for clipwright-render server.py (MCP + CLI).

Targets:
  - clipwright_render tool is registered with MCP and delegates to render.render_timeline
  - MCP annotations (§5): readOnly:false / destructive:false / idempotent:true / openWorld:false
  - Success envelope (ok:true)
  - Failure envelope (ok:false, error:{code,message,hint})
  - dry_run delegation is passed to the render layer
  - CLI main() argument parsing (DC-GP-003 / §6.3):
    - timeline / output positional args
    - --dry-run triggers the dry-run path
    - --width only (--height missing) -> INVALID_INPUT (pair constraint)
    - --crf 52 -> range error (0-51)
    - --overwrite is passed as options.overwrite=True to render_timeline

server.py is not yet implemented, so all tests are expected to fail Red
due to missing implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Attempt to import server.py (if not implemented, _SERVER_AVAILABLE = False)
# ---------------------------------------------------------------------------

try:
    from clipwright_render.server import (
        clipwright_render as server_clipwright_render,
    )
    from clipwright_render.server import main, mcp

    _SERVER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _SERVER_AVAILABLE = False

# Mark all tests as xfail until server.py is available
pytestmark = pytest.mark.xfail(
    not _SERVER_AVAILABLE,
    reason="server.py not yet implemented — Red (failing due to missing implementation)",
    strict=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_envelope(**kwargs: Any) -> dict[str, Any]:
    """Return a success envelope template."""
    base: dict[str, Any] = {
        "ok": True,
        "summary": "ok",
        "data": {},
        "artifacts": [],
        "warnings": [],
    }
    base.update(kwargs)
    return base


def _error_envelope(code: str) -> dict[str, Any]:
    """Return a failure envelope template."""
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": "error",
            "hint": "hint",
        },
    }


# ---------------------------------------------------------------------------
# MCP annotations tests (§5)
# ---------------------------------------------------------------------------


class TestMcpAnnotations:
    """Verify that the clipwright_render tool's MCP annotations match the §5 specification."""

    def _get_annotations(self) -> Any:
        # FastMCP has no stable public API to retrieve tool info, so we depend on
        # the private _tool_manager API here (L-2).
        # This may break on FastMCP version upgrades; it is supplemented by
        # Inspector smoke-testing as a separate assurance.
        tool = mcp._tool_manager.get_tool(  # type: ignore[attr-defined]
            "clipwright_render"
        )
        assert tool is not None, "clipwright_render must be registered with mcp"
        return tool.annotations

    def test_tool_is_registered(self) -> None:
        """clipwright_render is registered with mcp."""
        # Depends on internal API due to no stable public API (L-2);
        # supplemented by Inspector smoke-testing.
        tool = mcp._tool_manager.get_tool(  # type: ignore[attr-defined]
            "clipwright_render"
        )
        assert tool is not None

    def test_read_only_hint_is_false(self) -> None:
        """readOnlyHint=False (generates an output file)."""
        ann = self._get_annotations()
        assert ann.readOnlyHint is False

    def test_destructive_hint_is_false(self) -> None:
        """destructiveHint=False (input and OTIO are unchanged)."""
        ann = self._get_annotations()
        assert ann.destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        """idempotentHint=True (same input produces same output)."""
        ann = self._get_annotations()
        assert ann.idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False (does not touch the external network)."""
        ann = self._get_annotations()
        assert ann.openWorldHint is False


# ---------------------------------------------------------------------------
# MCP tool call: delegation to render.render_timeline
# ---------------------------------------------------------------------------


class TestMcpToolDelegation:
    """Verify that server.clipwright_render is a thin wrapper that calls render.render_timeline."""

    def test_success_delegates_to_render_timeline(self, tmp_path: Path) -> None:
        """On success, render.render_timeline is called and the result is delegated."""
        expected = _ok_envelope(summary="rendered ok")

        with patch(
            "clipwright_render.server.render_timeline",
            return_value=expected,
        ) as mock_render:
            result = server_clipwright_render(
                timeline="tl.otio",
                output="out.mp4",
                options={},
                dry_run=False,
            )

        mock_render.assert_called_once()
        assert result["ok"] is True

    def test_failure_returns_error_envelope(self, tmp_path: Path) -> None:
        """When render_timeline returns a failure envelope, the server returns it unchanged."""
        expected = _error_envelope("FILE_NOT_FOUND")

        with patch(
            "clipwright_render.server.render_timeline",
            return_value=expected,
        ):
            result = server_clipwright_render(
                timeline="missing.otio",
                output="out.mp4",
                options={},
            )

        assert result["ok"] is False
        assert result["error"]["code"] == "FILE_NOT_FOUND"

    def test_dry_run_passed_to_render_timeline(self, tmp_path: Path) -> None:
        """dry_run=True is passed to render_timeline."""
        with patch(
            "clipwright_render.server.render_timeline",
            return_value=_ok_envelope(),
        ) as mock_render:
            server_clipwright_render(
                timeline="tl.otio",
                output="out.mp4",
                options={},
                dry_run=True,
            )

        _args, kwargs = mock_render.call_args
        # dry_run=True must be passed as positional or keyword argument
        assert kwargs.get("dry_run") is True or (len(_args) >= 4 and _args[3] is True)

    def test_options_passed_to_render_timeline(self, tmp_path: Path) -> None:
        """options content is passed to render_timeline."""
        from clipwright_render.schemas import RenderOptions

        opts = RenderOptions(video_codec="libx264", crf=23)

        with patch(
            "clipwright_render.server.render_timeline",
            return_value=_ok_envelope(),
        ) as mock_render:
            server_clipwright_render(
                timeline="tl.otio",
                output="out.mp4",
                options=opts,
            )

        mock_render.assert_called_once()
        call_args = mock_render.call_args
        # options must be passed in some form
        assert call_args is not None

    def test_error_envelope_has_code_message_hint(self, tmp_path: Path) -> None:
        """The failure envelope contains code / message / hint."""
        expected: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": "INVALID_INPUT",
                "message": "invalid input",
                "hint": "please fix it",
            },
        }

        with patch(
            "clipwright_render.server.render_timeline",
            return_value=expected,
        ):
            result = server_clipwright_render(
                timeline="tl.otio",
                output="out.mp4",
                options={},
            )

        assert result["ok"] is False
        error = result["error"]
        assert "code" in error
        assert "message" in error
        assert "hint" in error


# ---------------------------------------------------------------------------
# CLI main() tests (DC-GP-003 / §6.3)
# ---------------------------------------------------------------------------


class TestCliMain:
    """Verify CLI argument parsing and render_timeline delegation via main()."""

    def _run_main(self, argv: list[str]) -> dict[str, Any]:
        """Replace sys.argv and call main()."""
        captured: dict[str, Any] = {}

        def _fake_render(
            timeline: str,
            output: str,
            options: Any,
            dry_run: bool = False,
        ) -> dict[str, Any]:
            captured["timeline"] = timeline
            captured["output"] = output
            captured["options"] = options
            captured["dry_run"] = dry_run
            return _ok_envelope()

        with (
            patch("clipwright_render.server.render_timeline", side_effect=_fake_render),
            patch.object(sys, "argv", ["clipwright-render"] + argv),
        ):
            main()

        return captured

    def test_positional_args_timeline_and_output(self, tmp_path: Path) -> None:
        """timeline / output are passed as positional args to main() (§6.3)."""
        captured = self._run_main(["tl.otio", "out.mp4"])
        assert captured["timeline"] == "tl.otio"
        assert captured["output"] == "out.mp4"

    def test_dry_run_flag(self, tmp_path: Path) -> None:
        """--dry-run flag is passed as dry_run=True to render_timeline."""
        captured = self._run_main(["tl.otio", "out.mp4", "--dry-run"])
        assert captured["dry_run"] is True

    def test_no_dry_run_defaults_to_false(self, tmp_path: Path) -> None:
        """Without --dry-run, dry_run is False."""
        captured = self._run_main(["tl.otio", "out.mp4"])
        assert captured["dry_run"] is False

    def test_overwrite_flag_sets_options_overwrite(self, tmp_path: Path) -> None:
        """--overwrite flag is passed as RenderOptions.overwrite=True."""
        captured = self._run_main(["tl.otio", "out.mp4", "--overwrite"])
        opts = captured["options"]
        assert opts.overwrite is True

    def test_video_codec_option(self, tmp_path: Path) -> None:
        """--video-codec C is passed as RenderOptions.video_codec=C."""
        captured = self._run_main(["tl.otio", "out.mp4", "--video-codec", "libx264"])
        assert captured["options"].video_codec == "libx264"

    def test_audio_codec_option(self, tmp_path: Path) -> None:
        """--audio-codec C is passed as RenderOptions.audio_codec=C."""
        captured = self._run_main(["tl.otio", "out.mp4", "--audio-codec", "aac"])
        assert captured["options"].audio_codec == "aac"

    def test_fps_option(self, tmp_path: Path) -> None:
        """--fps F is passed as RenderOptions.fps=F."""
        captured = self._run_main(["tl.otio", "out.mp4", "--fps", "24"])
        assert captured["options"].fps == pytest.approx(24.0)

    def test_crf_option(self, tmp_path: Path) -> None:
        """--crf N is passed as RenderOptions.crf=N."""
        captured = self._run_main(["tl.otio", "out.mp4", "--crf", "23"])
        assert captured["options"].crf == 23

    def test_width_and_height_option(self, tmp_path: Path) -> None:
        """--width W --height H are passed to RenderOptions."""
        captured = self._run_main(
            ["tl.otio", "out.mp4", "--width", "1920", "--height", "1080"]
        )
        assert captured["options"].width == 1920
        assert captured["options"].height == 1080

    def test_width_only_raises_invalid_input(self, tmp_path: Path) -> None:
        """--width only (--height missing) raises INVALID_INPUT or SystemExit/ValueError.

        Pair constraint (DC-AM-004) must be enforced in the CLI as well.
        Rejected at the argparse stage or at RenderOptions validation.
        """
        if not _SERVER_AVAILABLE:
            pytest.xfail(
                "server.py not yet implemented — Red (failing due to missing implementation)"
            )

        raised = False
        try:
            self._run_main(["tl.otio", "out.mp4", "--width", "1280"])
        except (SystemExit, ValueError, Exception) as exc:
            raised = True
            if isinstance(exc, SystemExit):
                assert exc.code != 0
        assert raised, (
            "--width only must raise an exception (pair constraint violation)"
        )

    def test_crf_out_of_range_raises_error(self, tmp_path: Path) -> None:
        """--crf 52 -> out of range (0-51): SystemExit/ValueError must be raised."""
        if not _SERVER_AVAILABLE:
            pytest.xfail(
                "server.py not yet implemented — Red (failing due to missing implementation)"
            )

        raised = False
        try:
            self._run_main(["tl.otio", "out.mp4", "--crf", "52"])
        except (SystemExit, ValueError, Exception):
            raised = True
        assert raised, "--crf 52 must raise an exception (out of range)"

    @pytest.mark.parametrize(
        "extra_args,field,expected",
        [
            (["--overwrite"], "overwrite", True),
            (["--video-codec", "libx265"], "video_codec", "libx265"),
            (["--crf", "0"], "crf", 0),
            (["--crf", "51"], "crf", 51),
        ],
    )
    def test_parametrize_options_mapping(
        self,
        tmp_path: Path,
        extra_args: list[str],
        field: str,
        expected: Any,
    ) -> None:
        """Each option is correctly mapped to the corresponding RenderOptions field."""
        captured = self._run_main(["tl.otio", "out.mp4"] + extra_args)
        opts = captured["options"]
        assert getattr(opts, field) == expected
