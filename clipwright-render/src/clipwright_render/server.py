"""server.py — clipwright-render MCP サーバー + CLI エントリポイント。

ビジネスロジックは render.py に委譲する薄いラッパー。
ClipwrightError の変換は render.py 側で行うため、ここでは二重変換しない。

トランスポートは stdio 既定（mcp.run(transport="stdio")）。
CLI は argparse で引数をパースし render_timeline を呼ぶ。
"""

from __future__ import annotations

import argparse
import sys
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field, ValidationError

from clipwright_render.render import clipwright_render as render_timeline  # noqa: F401
from clipwright_render.schemas import RenderOptions

# FastMCP インスタンス（サーバー名）
mcp = FastMCP("clipwright-render")


# ===========================================================================
# clipwright_render MCP ツール
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_render(
    timeline: Annotated[
        str,
        Field(description="入力 OTIO タイムラインファイルパス。"),
    ],
    output: Annotated[
        str,
        Field(description="出力動画ファイルパス（.mp4/.mkv/.mov/.webm）。"),
    ],
    options: Annotated[
        RenderOptions | None,
        Field(
            description=(
                "レンダリングオプション（コーデック/解像度/fps/crf/overwrite）。"
                " 省略時はすべてソース踏襲（ffmpeg 既定）。"
            )
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        Field(description="True のとき ffmpeg を実行せず計画のみを返す。"),
    ] = False,
) -> dict[str, Any]:
    """OTIO タイムラインを FFmpeg で実体化する MCP ツール。

    入力 timeline ファイル・元素材メディアは一切書き換えない（非破壊）。
    出力は新規生成した動画ファイルのパスを artifacts に返す。

    ビジネスロジックは render.render_timeline へ委譲する。
    options が None の場合はデフォルト RenderOptions() を使用する。
    """
    resolved_options = options if options is not None else RenderOptions()
    return render_timeline(
        timeline=timeline,
        output=output,
        options=resolved_options,
        dry_run=dry_run,
    )


# ===========================================================================
# CLI エントリポイント（DC-GP-003 / §6.3）
# ===========================================================================


def main() -> None:
    """CLI エントリポイント。

    clipwright-render <timeline> <output> [--dry-run] [--video-codec C]
      [--audio-codec C] [--width W --height H] [--fps F] [--crf N] [--overwrite]

    argparse で引数をパースし RenderOptions を組み立てて render_timeline を呼ぶ。
    MCP ツールと render.py ロジックを共有する（DC-GP-003）。
    """
    parser = argparse.ArgumentParser(
        prog="clipwright-render",
        description="OTIO タイムラインを FFmpeg で実体化する",
    )

    # 位置引数
    parser.add_argument("timeline", help="入力 OTIO タイムラインファイルパス")
    parser.add_argument("output", help="出力動画ファイルパス")

    # オプション
    parser.add_argument(
        "--dry-run", action="store_true", help="計画のみ返す（ffmpeg 未実行）"
    )
    parser.add_argument(
        "--video-codec", dest="video_codec", metavar="C", help="出力映像コーデック"
    )
    parser.add_argument(
        "--audio-codec", dest="audio_codec", metavar="C", help="出力音声コーデック"
    )
    parser.add_argument(
        "--width", type=int, metavar="W", help="出力映像幅（height とペア）"
    )
    parser.add_argument(
        "--height", type=int, metavar="H", help="出力映像高さ（width とペア）"
    )
    parser.add_argument("--fps", type=float, metavar="F", help="出力フレームレート")
    parser.add_argument("--crf", type=int, metavar="N", help="映像品質（CRF 0〜51）")
    parser.add_argument(
        "--overwrite", action="store_true", help="既存出力ファイルを上書き"
    )

    args = parser.parse_args()

    # RenderOptions を Pydantic で組み立て（バリデーション込み）
    try:
        options = RenderOptions(
            video_codec=args.video_codec,
            audio_codec=args.audio_codec,
            width=args.width,
            height=args.height,
            fps=args.fps,
            crf=args.crf,
            overwrite=args.overwrite,
        )
    except ValidationError as exc:
        print(f"オプションのバリデーションエラー: {exc}", file=sys.stderr)
        sys.exit(1)

    result = render_timeline(
        timeline=args.timeline,
        output=args.output,
        options=options,
        dry_run=args.dry_run,
    )

    if result.get("ok"):
        print(result.get("summary", ""))
    else:
        error = result.get("error", {})
        print(f"エラー: {error.get('message', '')}", file=sys.stderr)
        print(f"ヒント: {error.get('hint', '')}", file=sys.stderr)
        sys.exit(1)


# ===========================================================================
# エントリポイント（MCP stdio 起動）
# ===========================================================================

if __name__ == "__main__":
    mcp.run(transport="stdio")
