"""test_server.py — server.py（FastMCP 4 ツール）の Red テスト。

server.py は未実装のため、全テストが「機能未実装による失敗」で
Red になることを想定する。
テスト観点:
  - 各ツールの成功時 ToolResult 形・失敗時 ToolErrorResult 形（エンベロープ契約）
  - MCP annotations が §7 表どおり付与されていること
  - read_timeline: project_dir / timeline_path の排他必須・marker truncation 閾値 50
  - write_timeline: operations の追記セマンティクス・validate_only・ValidationReport
  - inspect_media: ffprobe 不在時（モック）に DEPENDENCY_MISSING エンベロープ
    + Windows hint
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ---- 未実装のモジュールを import
# （Red フェーズ: ModuleNotFoundError または ImportError が期待挙動）
# server.py が存在しないため、下記 import は失敗する。
# テストを構造化するため、pytestmark や try/except を使わずに直接 import し、
# 各テストクラス / 関数で ImportError/AttributeError が発生することを
# 明示的にチェックする。

try:
    from clipwright.server import (
        clipwright_init_project,
        clipwright_inspect_media,
        clipwright_read_timeline,
        clipwright_write_timeline,
        mcp,
    )

    _SERVER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _SERVER_AVAILABLE = False

# server.py が存在しない限り全テストを XFAIL としてマーク
pytestmark = pytest.mark.xfail(
    not _SERVER_AVAILABLE,
    reason="server.py が未実装のため Red（機能未実装による失敗）",
    strict=True,
)


# ===========================================================================
# ヘルパー
# ===========================================================================


def _assert_tool_result(result: Any) -> None:
    """ToolResult 形エンベロープの契約を検証する（§6.3）。"""
    assert isinstance(result, dict), "戻り値は dict であること"
    assert result.get("ok") is True, "成功時は ok=True"
    assert "summary" in result, "summary キーが必要"
    assert isinstance(result["summary"], str), "summary は str"
    assert len(result["summary"]) > 0, "summary は空でない"
    assert "data" in result, "data キーが必要"
    assert isinstance(result["data"], dict), "data は dict"
    assert "artifacts" in result, "artifacts キーが必要"
    assert isinstance(result["artifacts"], list), "artifacts は list"
    assert "warnings" in result, "warnings キーが必要"
    assert isinstance(result["warnings"], list), "warnings は list"


def _assert_tool_error_result(result: Any, expected_code: str) -> None:
    """ToolErrorResult 形エンベロープの契約を検証する（§6.4）。"""
    assert isinstance(result, dict), "戻り値は dict であること"
    assert result.get("ok") is False, "失敗時は ok=False"
    assert "error" in result, "error キーが必要"
    error = result["error"]
    assert isinstance(error, dict), "error は dict"
    assert "code" in error, "error.code が必要"
    assert "message" in error, "error.message が必要"
    assert "hint" in error, "error.hint が必要"
    assert isinstance(error["hint"], str) and len(error["hint"]) > 0, (
        "hint は空でない文字列（アクション可能な内容）"
    )
    assert error["code"] == expected_code, (
        f"error.code が {expected_code} であること（実際: {error['code']}）"
    )


# ===========================================================================
# MCP annotations テスト（§7 表・README 採用パッケージ記法）
# ===========================================================================


class TestMcpAnnotations:
    """FastMCP の ToolAnnotations が §7 表どおり付与されていることを検証する。

    README「annotations の記法（採用版）」にある ToolAnnotations フィールドを使う。
    mcp._tool_manager（または _tools）から登録済みツール定義を取得して確認する。
    """

    def _get_tool_annotations(self, tool_name: str) -> dict[str, Any]:
        """mcp オブジェクトから tool の annotations を取得する。

        FastMCP の公開 API（_tool_manager.get_tool）を使用する。
        """
        tool = mcp._tool_manager.get_tool(tool_name)  # type: ignore[attr-defined]
        assert tool is not None, f"ツール {tool_name} が mcp に登録されていること"
        return tool.annotations or {}

    def test_clipwright_init_project_annotations(self) -> None:
        """init_project: readOnly:false / destructive:false
        / idempotent:false / openWorld:false。"""
        ann = self._get_tool_annotations("clipwright_init_project")
        assert ann.readOnlyHint is False, "init_project は読み取り専用でない"
        assert ann.destructiveHint is False, (
            "init_project は非破壊（ユーザーデータ削除なし）"
        )
        assert ann.idempotentHint is False, (
            "init_project は冪等でない（再実行で PROJECT_EXISTS）"
        )
        assert ann.openWorldHint is False, "init_project は外部リソースにアクセスしない"

    def test_clipwright_inspect_media_annotations(self) -> None:
        """inspect_media: readOnly:true / destructive:false / idempotent:true。"""
        ann = self._get_tool_annotations("clipwright_inspect_media")
        assert ann.readOnlyHint is True, "inspect_media は読み取り専用"
        assert ann.destructiveHint is False, "inspect_media は非破壊"
        assert ann.idempotentHint is True, "inspect_media は冪等（同じ入力に同じ結果）"
        assert ann.openWorldHint is False, (
            "inspect_media は外部リソースにアクセスしない"
        )

    def test_clipwright_read_timeline_annotations(self) -> None:
        """read_timeline: readOnly:true / destructive:false / idempotent:true。"""
        ann = self._get_tool_annotations("clipwright_read_timeline")
        assert ann.readOnlyHint is True, "read_timeline は読み取り専用"
        assert ann.destructiveHint is False, "read_timeline は非破壊"
        assert ann.idempotentHint is True, "read_timeline は冪等"

    def test_clipwright_write_timeline_annotations(self) -> None:
        """write_timeline: readOnly:false / destructive:false / idempotent:false。"""
        ann = self._get_tool_annotations("clipwright_write_timeline")
        assert ann.readOnlyHint is False, "write_timeline は書き込みあり"
        assert ann.destructiveHint is False, (
            "write_timeline は非破壊（追記セマンティクス）"
        )
        assert ann.idempotentHint is False, "write_timeline は冪等でない"


# ===========================================================================
# clipwright_init_project テスト
# ===========================================================================


class TestInitProject:
    """clipwright_init_project ツールのエンベロープ契約を検証する。"""

    def test_success_returns_tool_result(self, tmp_path: Path) -> None:
        """正常系: ToolResult 形でプロジェクトを作成する。"""
        project_dir = str(tmp_path / "my_project")
        result = clipwright_init_project(
            project_dir=project_dir, name="テストプロジェクト"
        )
        _assert_tool_result(result)

    def test_success_creates_manifest(self, tmp_path: Path) -> None:
        """正常系: clipwright.json が生成されること。"""
        project_dir = str(tmp_path / "proj")
        clipwright_init_project(project_dir=project_dir, name="test")
        assert (tmp_path / "proj" / "clipwright.json").exists()

    def test_success_creates_timeline(self, tmp_path: Path) -> None:
        """正常系: timeline.otio が生成されること。"""
        project_dir = str(tmp_path / "proj")
        clipwright_init_project(project_dir=project_dir, name="test")
        assert (tmp_path / "proj" / "timeline.otio").exists()

    def test_success_artifacts_contain_manifest_and_timeline(
        self, tmp_path: Path
    ) -> None:
        """正常系: artifacts に manifest と timeline のパスが含まれること。"""
        project_dir = str(tmp_path / "proj")
        result = clipwright_init_project(project_dir=project_dir, name="test")
        _assert_tool_result(result)
        artifact_paths = [
            a["path"] if isinstance(a, dict) else a.path for a in result["artifacts"]
        ]
        assert any("clipwright.json" in p for p in artifact_paths), (
            "artifacts に clipwright.json が含まれること"
        )
        assert any("timeline.otio" in p for p in artifact_paths), (
            "artifacts に timeline.otio が含まれること"
        )

    def test_duplicate_project_returns_error(self, tmp_path: Path) -> None:
        """異常系: 既存プロジェクトに force=False で再 init すると
        エラーエンベロープを返す。"""
        project_dir = str(tmp_path / "proj")
        clipwright_init_project(project_dir=project_dir, name="test")
        # 2回目
        result = clipwright_init_project(project_dir=project_dir, name="test")
        _assert_tool_error_result(result, "PROJECT_EXISTS")

    def test_force_reinit_returns_tool_result(self, tmp_path: Path) -> None:
        """正常系: force=True で既存プロジェクトを再 init するとエラーにならない。"""
        project_dir = str(tmp_path / "proj")
        clipwright_init_project(project_dir=project_dir, name="test")
        result = clipwright_init_project(
            project_dir=project_dir, name="test", force=True
        )
        _assert_tool_result(result)

    def test_force_does_not_overwrite_existing_timeline(self, tmp_path: Path) -> None:
        """正常系: force=True でも既存 timeline.otio は上書きしない
        （非破壊 §13.2 DC-AM-007）。"""
        project_dir = str(tmp_path / "proj")
        clipwright_init_project(project_dir=project_dir, name="test")
        # timeline.otio に sentinel を書き込む
        timeline_path = tmp_path / "proj" / "timeline.otio"
        original_mtime = timeline_path.stat().st_mtime

        clipwright_init_project(project_dir=project_dir, name="test2", force=True)
        # mtime が変わっていないこと（上書きされていない）
        assert timeline_path.stat().st_mtime == original_mtime, (
            "force=True でも既存 timeline.otio の mtime が変わらないこと"
        )


# ===========================================================================
# clipwright_inspect_media テスト
# ===========================================================================


class TestInspectMedia:
    """clipwright_inspect_media ツールのエンベロープ契約を検証する。"""

    def test_success_returns_tool_result(self, sample_media: str) -> None:
        """正常系（統合）: 実 ffprobe で MediaInfo サマリを含む ToolResult を返す。"""
        result = clipwright_inspect_media(path=sample_media)
        _assert_tool_result(result)

    def test_success_data_contains_media_info(self, sample_media: str) -> None:
        """正常系（統合）: data に MediaInfo 相当の情報が含まれること。"""
        result = clipwright_inspect_media(path=sample_media)
        _assert_tool_result(result)
        data = result["data"]
        assert "path" in data or "container" in data or "streams" in data, (
            "data に MediaInfo 相当のフィールドが含まれること"
        )

    def test_file_not_found_returns_error(self, tmp_path: Path) -> None:
        """異常系: 存在しないパスを渡すと FILE_NOT_FOUND エラーエンベロープを返す。"""
        result = clipwright_inspect_media(path=str(tmp_path / "nonexistent.mp4"))
        _assert_tool_error_result(result, "FILE_NOT_FOUND")

    def test_dependency_missing_returns_error_with_windows_hint(
        self, tmp_path: Path, sample_media: str
    ) -> None:
        """異常系: ffprobe 不在時（§13.3 DC-GP-001/DC-GP-004）に
        DEPENDENCY_MISSING エンベロープ + Windows 向け hint（winget install）を返す。

        process.resolve_tool をモックして ffprobe が見つからない状況を再現する。
        """
        from clipwright.errors import ClipwrightError as _CWE
        from clipwright.errors import ErrorCode as _EC

        with patch(
            "clipwright.process.resolve_tool",
            side_effect=_CWE(
                _EC.DEPENDENCY_MISSING,
                "ffprobe が見つかりません",
                "winget install Gyan.FFmpeg で導入してください",
            ),
        ):
            result = clipwright_inspect_media(path=sample_media)

        _assert_tool_error_result(result, "DEPENDENCY_MISSING")
        hint = result["error"]["hint"]
        assert "winget" in hint.lower() or "winget" in hint, (
            "Windows 向け hint に winget install の記述があること"
        )

    def test_dependency_missing_hint_is_actionable(
        self, tmp_path: Path, sample_media: str
    ) -> None:
        """異常系: DEPENDENCY_MISSING の hint に Gyan.FFmpeg または
        CLIPWRIGHT_FFPROBE の記述があること。"""
        from clipwright.errors import ClipwrightError, ErrorCode

        with patch(
            "clipwright.process.resolve_tool",
            side_effect=ClipwrightError(
                ErrorCode.DEPENDENCY_MISSING,
                "ffprobe が見つかりません",
                "winget install Gyan.FFmpeg で導入するか"
                " CLIPWRIGHT_FFPROBE に設定してください",
            ),
        ):
            result = clipwright_inspect_media(path=sample_media)

        hint = result["error"]["hint"]
        assert "Gyan.FFmpeg" in hint or "CLIPWRIGHT_FFPROBE" in hint, (
            "hint に Gyan.FFmpeg または CLIPWRIGHT_FFPROBE の記述があること"
        )


# ===========================================================================
# clipwright_read_timeline テスト
# ===========================================================================


class TestReadTimeline:
    """clipwright_read_timeline ツールのエンベロープ契約・排他入力・
    marker truncation を検証する。"""

    def _setup_project(self, tmp_path: Path, name: str = "test") -> str:
        """テスト用プロジェクトを初期化して project_dir を返す。"""
        project_dir = str(tmp_path / "proj")
        clipwright_init_project(project_dir=project_dir, name=name)
        return project_dir

    # --- 正常系 ---

    def test_read_by_project_dir_returns_tool_result(self, tmp_path: Path) -> None:
        """正常系: project_dir 指定で ToolResult を返す。"""
        project_dir = self._setup_project(tmp_path)
        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(result)

    def test_read_by_timeline_path_returns_tool_result(self, tmp_path: Path) -> None:
        """正常系: timeline_path 指定で ToolResult を返す。"""
        project_dir = self._setup_project(tmp_path)
        timeline_path = str(Path(project_dir) / "timeline.otio")
        result = clipwright_read_timeline(timeline_path=timeline_path)
        _assert_tool_result(result)

    def test_data_contains_summary_fields(self, tmp_path: Path) -> None:
        """正常系: data に clip_count / gap_count / marker_count / total_duration
        が含まれること。"""
        project_dir = self._setup_project(tmp_path)
        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(result)
        data = result["data"]
        assert "clip_count" in data, "data.clip_count が必要"
        assert "gap_count" in data, "data.gap_count が必要"
        assert "marker_count" in data, "data.marker_count が必要"
        assert "total_duration" in data, "data.total_duration が必要"

    def test_artifacts_contain_timeline_path(self, tmp_path: Path) -> None:
        """正常系: artifacts に timeline.otio へのパスが含まれること。"""
        project_dir = self._setup_project(tmp_path)
        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(result)
        artifact_paths = [
            a["path"] if isinstance(a, dict) else a.path for a in result["artifacts"]
        ]
        assert any("timeline.otio" in p for p in artifact_paths), (
            "artifacts に timeline.otio パスが含まれること"
        )

    # --- 排他入力検証（§13.2 DC-AS-004）---

    def test_both_inputs_missing_returns_invalid_input(self, tmp_path: Path) -> None:
        """異常系: project_dir も timeline_path も指定しない
        → INVALID_INPUT（§13.2 DC-AS-004）。"""
        result = clipwright_read_timeline()
        _assert_tool_error_result(result, "INVALID_INPUT")

    def test_both_inputs_provided_returns_invalid_input(self, tmp_path: Path) -> None:
        """異常系: project_dir と timeline_path を両方指定
        → INVALID_INPUT（§13.2 DC-AS-004）。"""
        project_dir = self._setup_project(tmp_path)
        timeline_path = str(Path(project_dir) / "timeline.otio")
        result = clipwright_read_timeline(
            project_dir=project_dir,
            timeline_path=timeline_path,
        )
        _assert_tool_error_result(result, "INVALID_INPUT")

    def test_timeline_path_non_otio_extension_returns_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """異常系: timeline_path に .otio 以外を渡すと PATH_NOT_ALLOWED
        （F-02 パストラバーサル対策）。"""
        # 実際にファイルを作成して拡張子のみ検証されることを確認
        txt_path = tmp_path / "secrets.txt"
        txt_path.write_text("dummy")
        result = clipwright_read_timeline(timeline_path=str(txt_path))
        _assert_tool_error_result(result, "PATH_NOT_ALLOWED")

    def test_timeline_path_json_extension_returns_path_not_allowed(
        self, tmp_path: Path
    ) -> None:
        """異常系: timeline_path に .json を渡しても PATH_NOT_ALLOWED になる。"""
        json_path = tmp_path / "data.json"
        json_path.write_text("{}")
        result = clipwright_read_timeline(timeline_path=str(json_path))
        _assert_tool_error_result(result, "PATH_NOT_ALLOWED")

    # --- marker truncation（§13.2 DC-AS-004 / §13.5 DC-AM-001）---

    def test_markers_below_threshold_returns_markers_list(self, tmp_path: Path) -> None:
        """正常系: marker ≤ 50 件のとき data.markers にリストが返る（§13.5 DC-AM-001）。

        新規プロジェクトは marker 0 件のため threshold 以下に該当する。
        """
        project_dir = self._setup_project(tmp_path)
        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(result)
        data = result["data"]
        # marker 0 件でも markers キーは list として存在すること
        assert "markers" in data, "marker ≤ 50 のとき data.markers キーが必要"
        assert isinstance(data["markers"], list), "data.markers は list"
        # markers_truncated は False か存在しないこと
        assert not data.get("markers_truncated", False), (
            "marker ≤ 50 のとき markers_truncated は False または未設定"
        )

    def test_markers_above_threshold_returns_truncated(self, tmp_path: Path) -> None:
        """正常系: marker > 50 件のとき data.markers を省略し
        markers_truncated=true を返す（§13.5 DC-AM-001）。

        write_timeline で 51 件のマーカーを事前に追加してから read_timeline を呼ぶ。
        """
        project_dir = self._setup_project(tmp_path)
        # 51 件のマーカーを追加
        ops = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": float(i), "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": f"marker_{i:03d}",
            }
            for i in range(51)
        ]
        write_result = clipwright_write_timeline(
            project_dir=project_dir, operations=ops, validate_only=False
        )
        # write が失敗した場合はテスト前提が満たせないためスキップ
        if write_result.get("ok") is not True:
            pytest.skip(f"write_timeline の事前セットアップが失敗: {write_result}")

        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(result)
        data = result["data"]
        assert data.get("markers_truncated") is True, (
            "marker > 50 のとき data.markers_truncated=True が必要"
        )
        assert "marker_count" in data, "marker > 50 のとき data.marker_count が必要"
        assert data["marker_count"] == 51, (
            f"marker_count=51 であること（実際: {data.get('marker_count')}）"
        )
        assert "markers" not in data or data.get("markers") is None, (
            "marker > 50 のとき data.markers は省略または None"
        )

    def test_markers_exactly_at_threshold_returns_list(self, tmp_path: Path) -> None:
        """境界値: marker = 50 件のとき data.markers にリストが返る
        （≤50 は list 返却）。"""
        project_dir = self._setup_project(tmp_path)
        ops = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": float(i), "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": f"marker_{i:03d}",
            }
            for i in range(50)
        ]
        write_result = clipwright_write_timeline(
            project_dir=project_dir, operations=ops, validate_only=False
        )
        if write_result.get("ok") is not True:
            pytest.skip(f"write_timeline の事前セットアップが失敗: {write_result}")

        result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(result)
        data = result["data"]
        assert "markers" in data, (
            "marker = 50 のとき data.markers キーが必要（≤50 は list 返却）"
        )
        assert isinstance(data["markers"], list), "data.markers は list"
        assert not data.get("markers_truncated", False), (
            "marker = 50 のとき markers_truncated は False または未設定"
        )


# ===========================================================================
# clipwright_write_timeline テスト
# ===========================================================================


class TestWriteTimeline:
    """clipwright_write_timeline ツールのエンベロープ契約・追記セマンティクス・
    validate_only を検証する。"""

    def _setup_project(self, tmp_path: Path, name: str = "test") -> str:
        """テスト用プロジェクトを初期化して project_dir を返す。"""
        project_dir = str(tmp_path / "proj")
        clipwright_init_project(project_dir=project_dir, name=name)
        return project_dir

    # --- 正常系 ---

    def test_empty_operations_returns_tool_result(self, tmp_path: Path) -> None:
        """正常系: operations が空リストでも ToolResult を返す。"""
        project_dir = self._setup_project(tmp_path)
        result = clipwright_write_timeline(
            project_dir=project_dir, operations=[], validate_only=False
        )
        _assert_tool_result(result)

    def test_data_contains_validation_report(self, tmp_path: Path) -> None:
        """正常系: data に ValidationReport 相当のフィールドが含まれること
        （§13.1 DC-AM-003）。"""
        project_dir = self._setup_project(tmp_path)
        result = clipwright_write_timeline(
            project_dir=project_dir, operations=[], validate_only=False
        )
        _assert_tool_result(result)
        data = result["data"]
        assert "valid" in data, "data.valid が必要"
        assert "operation_count" in data, "data.operation_count が必要"
        assert "applied_count" in data, "data.applied_count が必要"

    def test_add_marker_operation_succeeds(self, tmp_path: Path) -> None:
        """正常系: add_marker op を渡すと成功し applied_count=1 になること。"""
        project_dir = self._setup_project(tmp_path)
        ops = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": 0.0, "rate": 30.0},
                    "duration": {"value": 30.0, "rate": 30.0},
                },
                "name": "テストマーカー",
            }
        ]
        result = clipwright_write_timeline(
            project_dir=project_dir, operations=ops, validate_only=False
        )
        _assert_tool_result(result)
        data = result["data"]
        assert data.get("valid") is True, "valid=True であること"
        assert data.get("applied_count") == 1, "applied_count=1 であること"

    def test_validate_only_does_not_apply(self, tmp_path: Path) -> None:
        """正常系: validate_only=True のとき applied_count=0 で
        timeline に書き込まない（§13.1 DC-AM-003）。"""
        project_dir = self._setup_project(tmp_path)
        timeline_path = Path(project_dir) / "timeline.otio"
        mtime_before = timeline_path.stat().st_mtime

        ops = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": 0.0, "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": "dry-run marker",
            }
        ]
        result = clipwright_write_timeline(
            project_dir=project_dir, operations=ops, validate_only=True
        )
        _assert_tool_result(result)
        data = result["data"]
        assert data.get("valid") is True, (
            "validate_only=True でも valid=True であること"
        )
        assert data.get("applied_count") == 0, (
            "validate_only=True のとき applied_count=0 であること"
        )
        # timeline.otio の mtime が変わっていないこと
        assert timeline_path.stat().st_mtime == mtime_before, (
            "validate_only=True では timeline.otio が更新されないこと"
        )

    def test_additive_semantics_preserves_existing_content(
        self, tmp_path: Path
    ) -> None:
        """正常系: 追記セマンティクス — 2 回目の write_timeline で
        既存内容が消えない（§13.2 DC-AM-001）。"""
        project_dir = self._setup_project(tmp_path)

        # 1 回目: marker_1 を追加
        ops_1 = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": 0.0, "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": "marker_first",
            }
        ]
        result_1 = clipwright_write_timeline(
            project_dir=project_dir, operations=ops_1, validate_only=False
        )
        _assert_tool_result(result_1)

        # 2 回目: marker_2 を追加
        ops_2 = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": 1.0, "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": "marker_second",
            }
        ]
        result_2 = clipwright_write_timeline(
            project_dir=project_dir, operations=ops_2, validate_only=False
        )
        _assert_tool_result(result_2)

        # read_timeline で marker_count=2 を確認
        read_result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(read_result)
        assert read_result["data"]["marker_count"] == 2, (
            "追記セマンティクスにより 2 回の write 後 marker_count=2 であること"
        )

    def test_invalid_op_returns_validation_error(self, tmp_path: Path) -> None:
        """異常系: 不正な op を渡すと ok=False の
        INVALID_INPUT エラーエンベロープを返す。

        Pydantic 検証失敗（不正な op 種別等）は入力スキーマ違反であるため
        ok=False / error.code=INVALID_INPUT として返す（§6.4 契約）。
        all-or-nothing: 1 件でも不正なら適用しない（§13.1 DC-AM-004）。
        """
        project_dir = self._setup_project(tmp_path)
        bad_ops = [{"op": "unknown_op", "track": 0}]
        result = clipwright_write_timeline(
            project_dir=project_dir, operations=bad_ops, validate_only=False
        )
        _assert_tool_error_result(result, "INVALID_INPUT")

    def test_all_or_nothing_on_invalid_op(self, tmp_path: Path) -> None:
        """異常系: 複数 op のうち1件でも不正なら全件適用しない（§13.1 DC-AM-004）。"""
        project_dir = self._setup_project(tmp_path)

        # まず正常な marker を1件追加しておく
        ops_init = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": 0.0, "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": "marker_before",
            }
        ]
        clipwright_write_timeline(
            project_dir=project_dir, operations=ops_init, validate_only=False
        )

        # 正常 op + 不正 op（out-of-range track）の混在
        ops_mixed = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": 2.0, "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": "marker_good",
            },
            {
                "op": "add_marker",
                "track": 999,  # 不正: 存在しないトラック
                "marked_range": {
                    "start_time": {"value": 3.0, "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": "marker_bad",
            },
        ]
        clipwright_write_timeline(
            project_dir=project_dir, operations=ops_mixed, validate_only=False
        )

        # all-or-nothing: 適用前の marker_count=1 を保持
        read_result = clipwright_read_timeline(project_dir=project_dir)
        _assert_tool_result(read_result)
        assert read_result["data"]["marker_count"] == 1, (
            "all-or-nothing: 不正 op 混在時は全件 rollback されて marker_count=1 のまま"
        )

    def test_artifacts_contain_timeline_after_write(self, tmp_path: Path) -> None:
        """正常系: write 成功後の artifacts に timeline.otio が含まれること。"""
        project_dir = self._setup_project(tmp_path)
        ops = [
            {
                "op": "add_marker",
                "track": 0,
                "marked_range": {
                    "start_time": {"value": 0.0, "rate": 30.0},
                    "duration": {"value": 1.0, "rate": 30.0},
                },
                "name": "m",
            }
        ]
        result = clipwright_write_timeline(
            project_dir=project_dir, operations=ops, validate_only=False
        )
        _assert_tool_result(result)
        artifact_paths = [
            a["path"] if isinstance(a, dict) else a.path for a in result["artifacts"]
        ]
        assert any("timeline.otio" in p for p in artifact_paths), (
            "write 成功後の artifacts に timeline.otio が含まれること"
        )


# ===========================================================================
# M-2: clipwright_inspect_media の resolve_tool 二重呼び出し排除テスト
# ===========================================================================


class TestInspectMediaResolveToolCallCount:
    """M-2 fixing: server.py の先行 resolve_tool 呼び出しが削除されていることを
    モックの呼び出し回数で固定する。

    修正後の設計:
      - server.py は _inspect_media が送出する ClipwrightError(DEPENDENCY_MISSING)
        をそのままエンベロープに変換する
      - process.resolve_tool は media.py 内で1回だけ呼ばれる
      - server.py から直接 resolve_tool を呼ぶコードは存在しない
    """

    def test_dependency_missing_from_inspect_media_returns_error_envelope(
        self, sample_media: str
    ) -> None:
        """M-2: _inspect_media が送出する DEPENDENCY_MISSING が
        server.py でそのままエンベロープに変換されること（正しい経路での Red 確認用）。

        server.py の先行 resolve_tool を削除した後も、
        _inspect_media 内部の resolve_tool 失敗がエンベロープに伝播することを固定。
        """
        from clipwright.errors import ClipwrightError as _CWE
        from clipwright.errors import ErrorCode as _EC

        # server モジュール内の _inspect_media を直接パッチ
        with patch("clipwright.server._inspect_media") as mock_inspect:
            mock_inspect.side_effect = _CWE(
                _EC.DEPENDENCY_MISSING,
                "ffprobe が見つかりません",
                "winget install Gyan.FFmpeg で導入してください",
            )
            result = clipwright_inspect_media(path=sample_media)

        # DEPENDENCY_MISSING エンベロープが返ること
        _assert_tool_error_result(result, "DEPENDENCY_MISSING")
        # hint が引き継がれること（server.py が ClipwrightError の hint を使うこと）
        assert "winget" in result["error"]["hint"], (
            "hint に winget の記述が引き継がれること"
        )

    def test_resolve_tool_not_called_directly_from_server_on_success_path(
        self, sample_media: str
    ) -> None:
        """M-2: 正常系でも server.py が resolve_tool を直接呼ばないこと。

        _inspect_media をモックして成功を返す場合、
        server.py の先行 resolve_tool 呼び出しがあれば call_count >= 1 になる。
        server.py から resolve_tool を直接呼ばない設計なら call_count == 0。
        """
        from clipwright.schemas import MediaInfo, RationalTimeModel

        mock_media_info = MediaInfo(
            path=sample_media,
            container="mp4",
            duration=RationalTimeModel(value=90.0, rate=30.0),
            streams=[],
        )
        with (
            patch("clipwright.process.resolve_tool") as mock_resolve,
            patch("clipwright.media.inspect_media", return_value=mock_media_info),
        ):
            result = clipwright_inspect_media(path=sample_media)

        # 正常エンベロープが返ること
        _assert_tool_result(result)
        # server.py から直接 resolve_tool を呼んでいなければ call_count == 0
        assert mock_resolve.call_count == 0, (
            f"server.py が resolve_tool を直接呼び出している"
            f"（call_count={mock_resolve.call_count}）。"
            "server.py からの先行 resolve_tool 呼び出しを削除してください。"
        )


# ===========================================================================
# F-06: read_timeline / write_timeline の exc 露出防止テスト
# ===========================================================================


class TestTimelineExcMessageNotExposed:
    """F-06 fixing: read_timeline / write_timeline の except ブロックで
    {exc} の内容（内部パス等）が message に含まれないことを固定する。

    otio_utils.load_timeline が L-3 対応で ClipwrightError に変換するようになったため、
    通常の OTIO ファイルエラーは ClipwrightError パスを通る。
    しかし server.py の except Exception as exc パスに非 OTIO 例外が到達した場合も
    汎用メッセージを返し、{exc} の内容を露出しないことを保証する。
    """

    def _setup_project(self, tmp_path: Path, name: str = "test") -> str:
        """テスト用プロジェクトを初期化して project_dir を返す。"""
        project_dir = str(tmp_path / "proj")
        clipwright_init_project(project_dir=project_dir, name=name)
        return project_dir

    def test_read_timeline_otio_error_message_does_not_contain_exc_detail(
        self, tmp_path: Path
    ) -> None:
        """F-06: read_timeline でファイル読み込み失敗時に
        message に生の例外文字列（内部パス等）が含まれないこと。

        load_timeline が ClipwrightError を送出する場合（L-3 対応済み）、
        server.py は exc.message のみを使い {exc} を message に含めない設計。
        ClipwrightError.message には内部パスが入らない（ファイル名のみ）ことを確認する。
        """
        self._setup_project(tmp_path)
        # 無効なコンテンツの .otio ファイルを作成
        bad_otio_path = tmp_path / "proj" / "bad.otio"
        bad_otio_path.write_text(
            "INVALID OTIO CONTENT - C:\\Users\\satoh\\secrets\\internal\\path.txt",
            encoding="utf-8",
        )

        result = clipwright_read_timeline(timeline_path=str(bad_otio_path))

        # ok=False で OTIO_ERROR が返ること
        _assert_tool_error_result(result, "OTIO_ERROR")
        message = result["error"]["message"]
        # 内部パス文字列（C:\Users\satoh 等）が message に含まれないこと
        assert "satoh" not in message, (
            f"message に内部パス（satoh）が含まれている: {message!r}"
        )
        assert "secrets" not in message, (
            f"message に内部パス（secrets）が含まれている: {message!r}"
        )
        assert "internal" not in message, (
            f"message に内部パス（internal）が含まれている: {message!r}"
        )

    def test_read_timeline_non_otio_exception_message_is_generic(
        self, tmp_path: Path
    ) -> None:
        """F-06: read_timeline で非 OTIO 例外が発生した場合も
        message に {exc} の内容が含まれないこと（汎用メッセージを返すこと）。

        server.py の except Exception as exc パスが汎用メッセージを返すことを固定する。
        """
        project_dir = self._setup_project(tmp_path)

        # load_timeline が非 OTIO 例外（RuntimeError）を送出するようにモック
        sensitive_detail = "C:\\Users\\satoh\\AppData\\internal_db_connection_string"
        with patch(
            "clipwright.server.load_timeline",
            side_effect=RuntimeError(f"internal error: {sensitive_detail}"),
        ):
            result = clipwright_read_timeline(project_dir=project_dir)

        _assert_tool_error_result(result, "OTIO_ERROR")
        message = result["error"]["message"]
        # {exc} の内容が message に含まれないこと
        assert sensitive_detail not in message, (
            f"message に RuntimeError の詳細（{sensitive_detail!r}）が含まれている: "
            f"{message!r}"
        )
        assert "internal error" not in message, (
            f"message に RuntimeError の内容（'internal error'）が含まれている: "
            f"{message!r}"
        )

    def test_write_timeline_non_otio_exception_message_is_generic(
        self, tmp_path: Path
    ) -> None:
        """F-06: write_timeline で非 OTIO 例外が発生した場合も
        message に {exc} の内容が含まれないこと（汎用メッセージを返すこと）。

        write_timeline の except Exception パスが汎用メッセージを返すことを固定。
        """
        project_dir = self._setup_project(tmp_path)

        sensitive_detail = "C:\\Users\\satoh\\AppData\\project_file_secret.otio"
        with patch(
            "clipwright.server.load_timeline",
            side_effect=RuntimeError(f"load failed: {sensitive_detail}"),
        ):
            result = clipwright_write_timeline(
                project_dir=project_dir, operations=[], validate_only=False
            )

        _assert_tool_error_result(result, "OTIO_ERROR")
        message = result["error"]["message"]
        # {exc} の内容が message に含まれないこと
        assert sensitive_detail not in message, (
            f"message に RuntimeError の詳細（{sensitive_detail!r}）が含まれている: "
            f"{message!r}"
        )
        assert "load failed" not in message, (
            f"message に RuntimeError の内容（'load failed'）が含まれている: "
            f"{message!r}"
        )

    def test_read_timeline_error_message_is_fixed_generic_string(
        self, tmp_path: Path
    ) -> None:
        """F-06: read_timeline の OTIO エラー時 message が定型の汎用文字列であること。

        message は固定フォーマットを持ち、可変の例外詳細を含まないことを確認する。
        """
        project_dir = self._setup_project(tmp_path)

        with patch(
            "clipwright.server.load_timeline",
            side_effect=RuntimeError("unexpected internal detail xyz"),
        ):
            result = clipwright_read_timeline(project_dir=project_dir)

        _assert_tool_error_result(result, "OTIO_ERROR")
        message = result["error"]["message"]
        hint = result["error"]["hint"]
        # message は可変の例外詳細を含まないこと
        assert "unexpected internal detail xyz" not in message, (
            f"message に生の例外メッセージが含まれている: {message!r}"
        )
        # hint が空でないこと（アクション可能な内容）
        assert len(hint) > 0, "hint は空でないこと"

    def test_write_timeline_error_hint_is_actionable(self, tmp_path: Path) -> None:
        """F-06: write_timeline の OTIO エラー時 hint が定型文であること。"""
        project_dir = self._setup_project(tmp_path)

        with patch(
            "clipwright.server.load_timeline",
            side_effect=RuntimeError("unexpected detail abc"),
        ):
            result = clipwright_write_timeline(
                project_dir=project_dir, operations=[], validate_only=False
            )

        _assert_tool_error_result(result, "OTIO_ERROR")
        hint = result["error"]["hint"]
        message = result["error"]["message"]
        # 生の例外メッセージが含まれないこと
        assert "unexpected detail abc" not in message, (
            f"message に生の例外メッセージが含まれている: {message!r}"
        )
        assert len(hint) > 0, "hint はアクション可能な文字列であること"
