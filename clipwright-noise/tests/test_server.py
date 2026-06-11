"""test_server.py — server.py（MCP + CLI）の完全版テスト。

対象:
  - clipwright_detect_noise が MCP に登録されていること
  - annotations: readOnlyHint=True / destructiveHint=False / idempotentHint=True / openWorldHint=False
  - options=None 時に既定 DetectNoiseOptions() が委譲先へ渡ること
  - timeline=None が既定であること
  - noise.detect_noise へ委譲すること
  - main() が mcp.run(transport="stdio") を呼ぶこと
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from clipwright_noise.schemas import DetectNoiseOptions
from clipwright_noise.server import clipwright_detect_noise as server_action
from clipwright_noise.server import main, mcp

# ===========================================================================
# ヘルパー
# ===========================================================================


def _ok_envelope(**kwargs: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ok": True,
        "summary": "ok",
        "data": {},
        "artifacts": [],
        "warnings": [],
    }
    base.update(kwargs)
    return base


def _get_tool_annotations() -> Any:
    # FastMCP は登録済みツールを取得する公開 API を持たないため、
    # テスト目的でプライベート属性 _tool_manager を参照する。
    tool = mcp._tool_manager.get_tool("clipwright_detect_noise")  # noqa: SLF001
    assert tool is not None, "clipwright_detect_noise が mcp に登録されていること"
    return tool.annotations


# ===========================================================================
# MCP 登録・annotations 検証
# ===========================================================================


class TestMcpRegistration:
    """clipwright_detect_noise が MCP に正しく登録されていること。"""

    def test_tool_is_registered(self) -> None:
        """clipwright_detect_noise が MCP ツールリストに存在すること。"""
        # FastMCP は登録済みツールを取得する公開 API を持たないため、プライベート属性を使用する。
        tool = mcp._tool_manager.get_tool("clipwright_detect_noise")  # noqa: SLF001
        assert tool is not None, "clipwright_detect_noise が MCP に登録されていない。"


class TestMcpAnnotations:
    """detect 系ツールの MCP annotations 確認（設計 §2.4）。"""

    def test_read_only_hint_is_true(self) -> None:
        """readOnlyHint=True: 入力メディアを書き換えない。"""
        annotations = _get_tool_annotations()
        assert annotations.readOnlyHint is True

    def test_destructive_hint_is_false(self) -> None:
        """destructiveHint=False: 破壊的操作でない。"""
        annotations = _get_tool_annotations()
        assert annotations.destructiveHint is False

    def test_idempotent_hint_is_true(self) -> None:
        """idempotentHint=True: 同じ入力で同じ出力。"""
        annotations = _get_tool_annotations()
        assert annotations.idempotentHint is True

    def test_open_world_hint_is_false(self) -> None:
        """openWorldHint=False: ネットワークアクセスなし。"""
        annotations = _get_tool_annotations()
        assert annotations.openWorldHint is False


# ===========================================================================
# 委譲（detect_noise への委譲）
# ===========================================================================


class TestDelegation:
    """clipwright_detect_noise が noise.detect_noise へ正しく委譲すること。"""

    def test_success_delegates_to_detect_noise(self) -> None:
        """成功時に detect_noise が呼ばれ結果が返ること。"""
        with patch(
            "clipwright_noise.server.detect_noise",
            return_value=_ok_envelope(summary="done"),
        ) as mock_fn:
            result = server_action(
                media="in.mp4", output="out.otio", options=None, timeline=None
            )

        mock_fn.assert_called_once()
        assert result["ok"] is True
        assert result["summary"] == "done"

    def test_error_result_propagates(self) -> None:
        """detect_noise がエラーエンベロープを返した場合にそのまま伝播すること。"""
        error_envelope: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": "INVALID_INPUT",
                "message": "test error",
                "hint": "test hint",
            },
        }
        with patch(
            "clipwright_noise.server.detect_noise",
            return_value=error_envelope,
        ):
            result = server_action(
                media="in.mp4", output="out.otio", options=None, timeline=None
            )

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_INPUT"

    def test_media_and_output_forwarded(self) -> None:
        """media / output が detect_noise に正しく渡ること。"""
        with patch(
            "clipwright_noise.server.detect_noise",
            return_value=_ok_envelope(),
        ) as mock_fn:
            server_action(
                media="/path/to/video.mp4",
                output="/path/to/out.otio",
                options=None,
                timeline=None,
            )

        _args, kwargs = mock_fn.call_args
        assert kwargs.get("media") == "/path/to/video.mp4"
        assert kwargs.get("output") == "/path/to/out.otio"

    def test_timeline_forwarded_when_specified(self) -> None:
        """timeline 引数が detect_noise に正しく渡ること。"""
        with patch(
            "clipwright_noise.server.detect_noise",
            return_value=_ok_envelope(),
        ) as mock_fn:
            server_action(
                media="in.mp4",
                output="out.otio",
                options=None,
                timeline="existing.otio",
            )

        _args, kwargs = mock_fn.call_args
        assert kwargs.get("timeline") == "existing.otio"

    def test_timeline_none_is_forwarded(self) -> None:
        """timeline=None が detect_noise に渡ること（省略時の既定）。"""
        with patch(
            "clipwright_noise.server.detect_noise",
            return_value=_ok_envelope(),
        ) as mock_fn:
            server_action(
                media="in.mp4", output="out.otio", options=None, timeline=None
            )

        _args, kwargs = mock_fn.call_args
        assert kwargs.get("timeline") is None


# ===========================================================================
# options=None 時の既定値
# ===========================================================================


class TestOptionsDefault:
    """options=None の場合に DetectNoiseOptions() が使われること。"""

    def test_options_none_uses_default_detect_noise_options(self) -> None:
        """options=None → backend=afftdn / strength=medium の既定が渡ること。"""
        with patch(
            "clipwright_noise.server.detect_noise",
            return_value=_ok_envelope(),
        ) as mock_fn:
            server_action(
                media="in.mp4", output="out.otio", options=None, timeline=None
            )

        _args, kwargs = mock_fn.call_args
        passed = kwargs.get("options")
        assert isinstance(passed, DetectNoiseOptions), (
            f"options が DetectNoiseOptions でない: {type(passed)}"
        )
        assert passed.backend == "afftdn"
        assert passed.strength == "medium"

    def test_options_explicit_is_forwarded(self) -> None:
        """options を明示指定した場合はそのまま渡ること。"""
        custom_opts = DetectNoiseOptions(backend="deepfilternet", strength="strong")
        with patch(
            "clipwright_noise.server.detect_noise",
            return_value=_ok_envelope(),
        ) as mock_fn:
            server_action(
                media="in.mp4", output="out.otio", options=custom_opts, timeline=None
            )

        _args, kwargs = mock_fn.call_args
        passed = kwargs.get("options")
        assert passed is custom_opts or (
            isinstance(passed, DetectNoiseOptions)
            and passed.backend == "deepfilternet"
            and passed.strength == "strong"
        )


# ===========================================================================
# main() — stdio 起動
# ===========================================================================


class TestCliMain:
    """main() が MCP サーバーを stdio で起動すること。"""

    def test_main_runs_mcp_with_stdio_transport(self) -> None:
        """main() が mcp.run(transport="stdio") を呼ぶこと。"""
        with patch.object(mcp, "run") as mock_run:
            main()

        mock_run.assert_called_once()
        _args, kwargs = mock_run.call_args
        assert kwargs.get("transport") == "stdio" or (
            len(_args) >= 1 and _args[0] == "stdio"
        ), f"transport='stdio' が渡されていない。args={_args}, kwargs={kwargs}"
