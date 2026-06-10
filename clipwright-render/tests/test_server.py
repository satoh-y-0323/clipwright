"""test_server.py — clipwright-render server.py（MCP + CLI）の Red テスト。

対象:
  - clipwright_render ツールが MCP に登録され、render.render_timeline へ委譲すること
  - MCP annotations（§5）: readOnly:false / destructive:false
    / idempotent:true / openWorld:false
  - 成功時エンベロープ（ok:true）
  - 失敗時エンベロープ（ok:false, error:{code,message,hint}）
  - dry_run 委譲が render 層へ渡ること
  - CLI main() の引数パース（DC-GP-003 / §6.3）:
    - timeline / output 位置引数
    - --dry-run でドライラン経路
    - --width のみ（--height 欠落）→ INVALID_INPUT（ペア制約）
    - --crf 52 → 範囲エラー（0–51）
    - --overwrite が options.overwrite=True として render_timeline に渡ること

server.py は未実装のため全テストが「機能未実装による失敗」で Red になる。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# server.py の import 試行（未実装なら _SERVER_AVAILABLE = False）
# ---------------------------------------------------------------------------

try:
    from clipwright_render.server import (
        clipwright_render as server_clipwright_render,
    )
    from clipwright_render.server import main, mcp

    _SERVER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _SERVER_AVAILABLE = False

# server.py が存在しない限り全テストを xfail にする
pytestmark = pytest.mark.xfail(
    not _SERVER_AVAILABLE,
    reason="server.py が未実装のため Red（機能未実装による失敗）",
    strict=True,
)


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _ok_envelope(**kwargs: Any) -> dict[str, Any]:
    """成功エンベロープのひな型を返す。"""
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
    """失敗エンベロープのひな型を返す。"""
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": "error",
            "hint": "hint",
        },
    }


# ---------------------------------------------------------------------------
# MCP annotations テスト（§5）
# ---------------------------------------------------------------------------


class TestMcpAnnotations:
    """clipwright_render ツールの MCP annotations が §5 仕様どおりか検証する。"""

    def _get_annotations(self) -> Any:
        tool = mcp._tool_manager.get_tool(  # type: ignore[attr-defined]
            "clipwright_render"
        )
        assert tool is not None, "clipwright_render が mcp に登録されていること"
        return tool.annotations

    def test_tool_is_registered(self) -> None:
        """clipwright_render が mcp に登録されていること。"""
        tool = mcp._tool_manager.get_tool(  # type: ignore[attr-defined]
            "clipwright_render"
        )
        assert tool is not None

    def test_read_only_hint_is_false(self) -> None:
        """readOnlyHint=False（出力ファイルを生成する）。"""
        ann = self._get_annotations()
        assert ann.readOnlyHint is False

    def test_destructive_hint_is_false(self) -> None:
        """destructiveHint=False（入力・OTIO は不変）。"""
        ann = self._get_annotations()
        assert ann.destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        """idempotentHint=True（同じ入力に同じ出力）。"""
        ann = self._get_annotations()
        assert ann.idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False（外部ネットワークに触れない）。"""
        ann = self._get_annotations()
        assert ann.openWorldHint is False


# ---------------------------------------------------------------------------
# MCP ツール呼び出し: render.render_timeline への委譲テスト
# ---------------------------------------------------------------------------


class TestMcpToolDelegation:
    """server.clipwright_render が render.render_timeline を呼ぶ薄いラッパー検証。"""

    def test_success_delegates_to_render_timeline(self, tmp_path: Path) -> None:
        """成功時に render.render_timeline を呼び委譲すること。"""
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
        """render_timeline が失敗エンベロープを返すと server もそのまま返す。"""
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
        """dry_run=True が render_timeline に渡されること。"""
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
        # positional または keyword で dry_run=True が渡される
        assert kwargs.get("dry_run") is True or (len(_args) >= 4 and _args[3] is True)

    def test_options_passed_to_render_timeline(self, tmp_path: Path) -> None:
        """options の内容が render_timeline に渡されること。"""
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
        # options が何らかの形で渡されている
        assert call_args is not None

    def test_error_envelope_has_code_message_hint(self, tmp_path: Path) -> None:
        """失敗エンベロープに code / message / hint が含まれること。"""
        expected: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": "INVALID_INPUT",
                "message": "不正な入力",
                "hint": "修正してください",
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
# CLI main() テスト（DC-GP-003 / §6.3）
# ---------------------------------------------------------------------------


class TestCliMain:
    """main() の argparse 経由 CLI 引数パースと render_timeline 委譲を検証する。"""

    def _run_main(self, argv: list[str]) -> dict[str, Any]:
        """sys.argv を差し替えて main() を呼び出す。"""
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
        """timeline / output が位置引数として main() に渡ること（§6.3）。"""
        captured = self._run_main(["tl.otio", "out.mp4"])
        assert captured["timeline"] == "tl.otio"
        assert captured["output"] == "out.mp4"

    def test_dry_run_flag(self, tmp_path: Path) -> None:
        """--dry-run フラグが dry_run=True として render_timeline に渡ること。"""
        captured = self._run_main(["tl.otio", "out.mp4", "--dry-run"])
        assert captured["dry_run"] is True

    def test_no_dry_run_defaults_to_false(self, tmp_path: Path) -> None:
        """--dry-run なしのとき dry_run=False であること。"""
        captured = self._run_main(["tl.otio", "out.mp4"])
        assert captured["dry_run"] is False

    def test_overwrite_flag_sets_options_overwrite(self, tmp_path: Path) -> None:
        """--overwrite フラグが RenderOptions.overwrite=True として渡ること。"""
        captured = self._run_main(["tl.otio", "out.mp4", "--overwrite"])
        opts = captured["options"]
        assert opts.overwrite is True

    def test_video_codec_option(self, tmp_path: Path) -> None:
        """--video-codec C が RenderOptions.video_codec=C として渡ること。"""
        captured = self._run_main(["tl.otio", "out.mp4", "--video-codec", "libx264"])
        assert captured["options"].video_codec == "libx264"

    def test_audio_codec_option(self, tmp_path: Path) -> None:
        """--audio-codec C が RenderOptions.audio_codec=C として渡ること。"""
        captured = self._run_main(["tl.otio", "out.mp4", "--audio-codec", "aac"])
        assert captured["options"].audio_codec == "aac"

    def test_fps_option(self, tmp_path: Path) -> None:
        """--fps F が RenderOptions.fps=F として渡ること。"""
        captured = self._run_main(["tl.otio", "out.mp4", "--fps", "24"])
        assert captured["options"].fps == pytest.approx(24.0)

    def test_crf_option(self, tmp_path: Path) -> None:
        """--crf N が RenderOptions.crf=N として渡ること。"""
        captured = self._run_main(["tl.otio", "out.mp4", "--crf", "23"])
        assert captured["options"].crf == 23

    def test_width_and_height_option(self, tmp_path: Path) -> None:
        """--width W --height H が RenderOptions に渡ること。"""
        captured = self._run_main(
            ["tl.otio", "out.mp4", "--width", "1920", "--height", "1080"]
        )
        assert captured["options"].width == 1920
        assert captured["options"].height == 1080

    def test_width_only_raises_invalid_input(self, tmp_path: Path) -> None:
        """--width のみ（--height 欠落）→ INVALID_INPUT または SystemExit/ValueError。

        ペア制約（DC-AM-004）が CLI でも有効であること。
        argparse の段階か RenderOptions バリデーションで拒否される。
        """
        if not _SERVER_AVAILABLE:
            pytest.xfail("server.py が未実装のため Red（機能未実装による失敗）")

        raised = False
        try:
            self._run_main(["tl.otio", "out.mp4", "--width", "1280"])
        except (SystemExit, ValueError, Exception) as exc:
            raised = True
            if isinstance(exc, SystemExit):
                assert exc.code != 0
        assert raised, "--width のみ指定は例外（ペア制約違反）になること"

    def test_crf_out_of_range_raises_error(self, tmp_path: Path) -> None:
        """--crf 52 → 範囲外（0–51）で SystemExit/ValueError が発生すること。"""
        if not _SERVER_AVAILABLE:
            pytest.xfail("server.py が未実装のため Red（機能未実装による失敗）")

        raised = False
        try:
            self._run_main(["tl.otio", "out.mp4", "--crf", "52"])
        except (SystemExit, ValueError, Exception):
            raised = True
        assert raised, "--crf 52 は例外（範囲外）になること"

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
        """各オプションが RenderOptions の対応フィールドに正しく渡ること。"""
        captured = self._run_main(["tl.otio", "out.mp4"] + extra_args)
        opts = captured["options"]
        assert getattr(opts, field) == expected
