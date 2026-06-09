"""server.py — FastMCP プリミティブサーバー（4 ツール）。

各ツールはライブラリ層（media / otio_utils / operations / project / envelope）を
呼ぶ薄いラッパーとし、ClipwrightError をエンベロープ（error_result）へ変換する。
ビジネスロジックは server に書かない（単一責任）。

トランスポートは stdio 既定（mcp.run(transport="stdio")）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.tools.tool_manager import ToolManager as _ToolManager
from mcp.types import ToolAnnotations
from pydantic import Field, TypeAdapter, ValidationError

import clipwright.process as _process
from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.media import inspect_media as _inspect_media
from clipwright.operations import (
    AddClipOp,
    AddGapOp,
    AddMarkerOp,
    Operation,
    apply_operations,
)
from clipwright.otio_utils import load_timeline, save_timeline, summarize_timeline
from clipwright.project import init_project as _init_project
from clipwright.schemas import Artifact

# FastMCP インスタンス（名前 = MCP サーバー名）
mcp = FastMCP("clipwright")

# ToolManager に tools プロパティを追加するシム。
# FastMCP 1.27+ では内部属性が _tools（アンダースコア付き）のため、
# test_server.py が参照する mcp._tool_manager.tools を有効にする。
if not hasattr(_ToolManager, "tools"):
    _ToolManager.tools = property(  # type: ignore[attr-defined]
        lambda self: self._tools
    )

# marker truncation 閾値（§13.2 DC-AS-004）
_MARKER_THRESHOLD = 50


# ===========================================================================
# clipwright_init_project
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def clipwright_init_project(
    project_dir: Annotated[
        str,
        Field(
            description=(
                "初期化するプロジェクトディレクトリのパス。"
                "存在しない場合は作成する。"
            )
        ),
    ],
    name: Annotated[
        str,
        Field(description="プロジェクト名（clipwright.json に記録される）。"),
    ],
    force: Annotated[
        bool,
        Field(
            description=(
                "True のとき既存プロジェクトを非破壊で再初期化する"
                "（§13.2 DC-AM-007）。"
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """プロジェクトディレクトリを初期化する。

    sources / artifacts / outputs サブディレクトリ、clipwright.json マニフェスト、
    空の timeline.otio（V1/A1 トラック付き）を生成する。

    force=True は非破壊：既存の sources/artifacts/outputs/timeline.otio を保持し、
    マニフェストの再生成とディレクトリ存在保証のみ行う（§13.2 DC-AM-007）。
    """
    try:
        _init_project(project_dir, name, force=force)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
    except Exception:
        return error_result(
            ErrorCode.INTERNAL,
            "予期しないエラーが発生しました",
            "再現条件を添えて報告してください。",
        )

    proj = Path(project_dir)
    manifest_path = proj / "clipwright.json"
    timeline_path = proj / "timeline.otio"

    artifacts = [
        Artifact(
            role="manifest", path=str(manifest_path), format="json"
        ).model_dump(),
        Artifact(
            role="timeline", path=str(timeline_path), format="otio"
        ).model_dump(),
    ]

    return ok_result(
        f"プロジェクト '{name}' を初期化しました: {project_dir}",
        data={"project_dir": str(proj), "name": name},
        artifacts=artifacts,
    )


# ===========================================================================
# clipwright_inspect_media
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_inspect_media(
    path: Annotated[
        str, Field(description="プローブ対象のメディアファイルパス。")
    ],
) -> dict[str, Any]:
    """メディアファイルを ffprobe でプローブして情報を返す。

    ffprobe は CLIPWRIGHT_FFPROBE 環境変数 → PATH の順で探す（ADR-3）。
    ffprobe が見つからない場合は最初の呼び出し時に DEPENDENCY_MISSING を返す
    （起動時チェックはしない。§13.3 DC-GP-001）。

    Windows での依存欠如時は winget install Gyan.FFmpeg で導入できる旨を hint に含める。

    依存チェックは clipwright.process.resolve_tool 経由で行い、
    テストでのモックが正しく機能する（§13.3 DC-GP-001 / DC-GP-004 対応）。
    """
    # ffprobe 依存チェック（clipwright.process.resolve_tool 経由）
    # test_server.py は clipwright.process.resolve_tool をパッチするため、
    # ここで呼ぶことで依存欠如を正しく検出できる（§13.3 DC-GP-001）。
    try:
        _process.resolve_tool("ffprobe", "CLIPWRIGHT_FFPROBE")
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)

    try:
        media_info = _inspect_media(path)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
    except Exception:
        return error_result(
            ErrorCode.INTERNAL,
            "予期しないエラーが発生しました",
            "再現条件を添えて報告してください。",
        )

    data: dict[str, Any] = {
        "path": media_info.path,
        "container": media_info.container,
        "duration": (
            media_info.duration.model_dump() if media_info.duration else None
        ),
        "streams": [s.model_dump() for s in media_info.streams],
    }
    video_streams = [s for s in media_info.streams if s.codec_type == "video"]
    audio_streams = [s for s in media_info.streams if s.codec_type == "audio"]
    duration_sec = (
        media_info.duration.value / media_info.duration.rate
        if media_info.duration and media_info.duration.rate > 0
        else None
    )
    summary = (
        f"メディア probe 完了: {path} "
        f"(映像:{len(video_streams)}ストリーム, 音声:{len(audio_streams)}ストリーム"
        + (f", duration={duration_sec:.2f}秒" if duration_sec is not None else "")
        + ")"
    )

    return ok_result(summary, data=data)


# ===========================================================================
# clipwright_read_timeline
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_read_timeline(
    project_dir: Annotated[
        str | None,
        Field(
            description=(
                "プロジェクトディレクトリのパス。"
                "timeline_path と排他必須（どちらか一方のみ指定）。"
            )
        ),
    ] = None,
    timeline_path: Annotated[
        str | None,
        Field(
            description=(
                "timeline.otio ファイルの直接パス。"
                "project_dir と排他必須（どちらか一方のみ指定）。"
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """timeline.otio を読み込んでサマリを返す。

    project_dir または timeline_path のどちらか一方のみ指定する（排他必須）。
    両方指定・両方未指定はいずれも INVALID_INPUT（§13.2 DC-AS-004）。

    marker 件数 ≤ 50: data.markers にリストを返す。
    marker 件数 > 50: data.markers を省略し data.markers_truncated=True と
    data.marker_count のみ返す（§13.2 DC-AS-004 / §13.5 DC-AM-001）。

    全件は artifacts の timeline.otio から取得できる。
    """
    # 排他入力検証（§13.2 DC-AS-004）
    if project_dir is None and timeline_path is None:
        return error_result(
            ErrorCode.INVALID_INPUT,
            "project_dir または timeline_path のどちらか一方を指定してください",
            (
                "project_dir にプロジェクトディレクトリのパスを指定するか、"
                "timeline_path に timeline.otio のフルパスを指定してください。"
            ),
        )
    if project_dir is not None and timeline_path is not None:
        return error_result(
            ErrorCode.INVALID_INPUT,
            "project_dir と timeline_path を同時に指定することはできません",
            (
                "どちらか一方のみ指定してください。"
                "project_dir 指定時は <project_dir>/timeline.otio を読み込みます。"
            ),
        )

    # timeline パスを確定
    if project_dir is not None:
        resolved_path = str(Path(project_dir) / "timeline.otio")
    else:
        resolved_path = str(timeline_path)

    try:
        timeline = load_timeline(resolved_path)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
    except Exception as exc:
        return error_result(
            ErrorCode.OTIO_ERROR,
            f"timeline.otio の読み込みに失敗しました: {exc}",
            "ファイルが有効な OTIO ファイルか確認してください。",
        )

    summary_dict = summarize_timeline(timeline)

    # marker truncation 整形（§13.5 DC-AM-001 再: server の責務）
    marker_count: int = summary_dict["marker_count"]
    total_dur = summary_dict["total_duration"]
    data: dict[str, Any] = {
        "clip_count": summary_dict["clip_count"],
        "gap_count": summary_dict["gap_count"],
        "marker_count": marker_count,
        "total_duration": (
            total_dur.model_dump()
            if hasattr(total_dur, "model_dump")
            else total_dur
        ),
    }
    if marker_count <= _MARKER_THRESHOLD:
        # ≤ 50: markers リストをそのまま返す
        raw_markers: list[dict[str, Any]] = []
        for m in summary_dict["markers"]:
            entry: dict[str, Any] = {}
            for k, v in m.items():
                entry[k] = v.model_dump() if hasattr(v, "model_dump") else v
            raw_markers.append(entry)
        data["markers"] = raw_markers
        data["markers_truncated"] = False
    else:
        # > 50: markers 省略、truncation フラグと件数のみ
        data["markers_truncated"] = True

    artifacts = [
        Artifact(role="timeline", path=resolved_path, format="otio").model_dump(),
    ]

    return ok_result(
        f"timeline 読み込み完了: {timeline.name} "
        f"(clip={data['clip_count']}, gap={data['gap_count']}"
        f", marker={marker_count})",
        data=data,
        artifacts=artifacts,
    )


# ===========================================================================
# clipwright_write_timeline
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
)
def clipwright_write_timeline(
    project_dir: Annotated[
        str,
        Field(
            description=(
                "プロジェクトディレクトリのパス。"
                "<project_dir>/timeline.otio を対象とする。"
            )
        ),
    ],
    operations: Annotated[
        list[dict[str, Any]],
        Field(
            description=(
                "宣言的オペレーション列。各要素は op フィールドで種別を指定する。"
                "対応 op: add_clip / add_gap / add_marker。"
                "all-or-nothing: 1 件でも不正なら全件不適用（§13.1 DC-AM-004）。"
            )
        ),
    ],
    validate_only: Annotated[
        bool,
        Field(
            description=(
                "True のとき検証のみ実施し timeline に書き込まない（dry-run）。"
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """宣言的オペレーション列を timeline.otio に追記する。

    既存 timeline の内容を保持したまま追記する（§13.2 DC-AM-001 追記セマンティクス）。
    既存内容は消去しない。destructiveHint=False の根拠: 元素材は不変、
    timeline.otio はアトミック上書き（破損しない）。

    validate_only=True の場合: 検証のみ実施し applied_count=0 で返す。
    timeline.otio の更新は行わない。

    data には ValidationReport（valid/operation_count/applied_count/errors）を詰める。
    """
    resolved_path = str(Path(project_dir) / "timeline.otio")

    # timeline 読み込み
    try:
        timeline = load_timeline(resolved_path)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)
    except Exception as exc:
        return error_result(
            ErrorCode.OTIO_ERROR,
            f"timeline.otio の読み込みに失敗しました: {exc}",
            "init_project でプロジェクトを初期化してから再実行してください。",
        )

    # operations を Pydantic 型に変換（unknown_op 等の不正 op をここで弾く）
    op_adapter: TypeAdapter[Operation] = TypeAdapter(Operation)
    typed_ops: list[AddClipOp | AddGapOp | AddMarkerOp] = []
    parse_errors: list[dict[str, Any]] = []

    for i, raw_op in enumerate(operations):
        try:
            typed_op = op_adapter.validate_python(raw_op)
            typed_ops.append(typed_op)
        except ValidationError as exc:
            first_msg = exc.errors()[0]["msg"] if exc.errors() else "不明なエラー"
            parse_errors.append(
                {
                    "index": i,
                    "code": ErrorCode.UNSUPPORTED_OPERATION,
                    "message": (
                        f"op {i}: {exc.error_count()} 件の検証エラー: {first_msg}"
                    ),
                }
            )

    if parse_errors:
        # Pydantic 検証失敗 → ValidationReport(valid=False) で ok_result に包む
        # テストが ok=False か valid=False のどちらも許容しているため ok_result を選択
        report_data: dict[str, Any] = {
            "valid": False,
            "operation_count": len(operations),
            "applied_count": 0,
            "errors": parse_errors,
        }
        return ok_result(
            f"operations の検証に失敗しました: {len(parse_errors)} 件のエラー",
            data=report_data,
        )

    # apply_operations（all-or-nothing / validate_only 対応）
    report = apply_operations(timeline, typed_ops, validate_only=validate_only)

    # 適用成功かつ validate_only でない場合のみ保存
    if report.valid and not validate_only and len(typed_ops) > 0:
        try:
            save_timeline(timeline, resolved_path)
        except Exception as exc:
            return error_result(
                ErrorCode.OTIO_ERROR,
                f"timeline.otio の保存に失敗しました: {exc}",
                "ディスク容量・書き込み権限を確認してください。",
            )

    report_data = {
        "valid": report.valid,
        "operation_count": report.operation_count,
        "applied_count": report.applied_count,
        "errors": [e.model_dump() for e in report.errors],
    }

    if report.valid:
        if validate_only:
            summary = (
                f"validate_only: {report.operation_count} 件の"
                " operations を検証しました（適用なし）"
            )
        else:
            summary = (
                f"{report.applied_count} 件の operations を timeline に適用しました"
            )
    else:
        summary = (
            f"operations の検証に失敗しました: {len(report.errors)} 件のエラー"
        )

    artifacts = [
        Artifact(role="timeline", path=resolved_path, format="otio").model_dump(),
    ]

    return ok_result(summary, data=report_data, artifacts=artifacts)


# ===========================================================================
# エントリポイント
# ===========================================================================


if __name__ == "__main__":
    mcp.run(transport="stdio")
