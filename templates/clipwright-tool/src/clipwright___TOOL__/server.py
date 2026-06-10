"""server.py — clipwright-__TOOL__ MCP サーバー + CLI エントリポイント。

ビジネスロジックは __TOOL__.py に委譲する薄いラッパー（spec §2.3）。
ここでエラー変換や入力検証を二重に行わない（検証は schemas / __TOOL__ の責務）。

トランスポートは stdio 既定（mcp.run(transport="stdio")・M なし SHOULD §6.7）。
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright___TOOL__.__TOOL__ import __ACTION__
from clipwright___TOOL__.schemas import __Action__Options

# FastMCP インスタンス（サーバー名 = clipwright-<tool>）
mcp = FastMCP("clipwright-__TOOL__")


# ===========================================================================
# clipwright___ACTION__ MCP ツール
# ===========================================================================
#
# annotations は実態に合わせて正直に付ける（§2 SHOULD）。
#   - detect / inspect 系（雛形の既定）:
#       readOnlyHint=True / destructiveHint=False / idempotentHint=True
#   - render 系（新ファイルを生成する）:
#       readOnlyHint=False に変える（destructive=False は維持・入力は不変）。
#   - openWorldHint: ローカル決定論なら False、ネット/外部 API に触れるなら True。


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright___ACTION__(
    input: Annotated[
        str,
        Field(description="入力ファイルパス（既存ファイル）。"),
    ],
    output: Annotated[
        str,
        Field(description="出力 artifact パス（新規生成・入力とは別パス）。"),
    ],
    options: Annotated[
        __Action__Options | None,
        Field(description="ツール固有オプション。省略時は既定値を使用する。"),
    ] = None,
) -> dict[str, Any]:
    """（TODO: ツールの目的・入出力契約を1〜2文で。AI が読む説明になる。）

    入力ファイルは書き換えない（非破壊・readOnly）。
    ビジネスロジックは __TOOL__.__ACTION__ へ委譲する。
    options が None の場合は既定の __Action__Options() を使用する。
    """
    resolved_options = options if options is not None else __Action__Options()
    return __ACTION__(input=input, output=output, options=resolved_options)


# ===========================================================================
# エントリポイント（MCP stdio 起動）
# ===========================================================================


def main() -> None:
    """CLI エントリポイント。MCP サーバーを stdio で起動する。

    pyproject.toml の [project.scripts] で
    clipwright-__TOOL__ = "clipwright___TOOL__.server:main" として登録する。
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
