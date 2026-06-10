"""server.py — clipwright-wrap MCP サーバー + CLI エントリポイント。

ビジネスロジックは wrap.py に委譲する薄いラッパー。
ClipwrightError の変換・language 検証は wrap.py / schemas.py 側で行うため、
ここでは二重変換しない（DC-GP-001）。

トランスポートは stdio 既定（mcp.run(transport="stdio")）。
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_wrap.schemas import WrapCaptionsOptions
from clipwright_wrap.wrap import wrap_captions

# FastMCP インスタンス（サーバー名）
mcp = FastMCP("clipwright-wrap")


# ===========================================================================
# clipwright_wrap_captions MCP ツール
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_wrap_captions(
    input: Annotated[
        str,
        Field(description="入力字幕ファイルパス（.srt または .vtt）。"),
    ],
    output: Annotated[
        str,
        Field(description="出力字幕ファイルパス（入力と同一拡張子）。"),
    ],
    options: Annotated[
        WrapCaptionsOptions | None,
        Field(
            description=(
                "文節改行オプション（language / max_chars / max_lines）。"
                "省略時は全てデフォルト値"
                "（language='ja' / max_chars=16 / max_lines=2）を使用する。"
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """字幕ファイルに文節改行を挿入して整形済み字幕を生成する MCP ツール。

    入力字幕ファイルは一切書き換えない（非破壊・readOnly）。
    出力は新規生成した SRT/VTT のパスを artifacts に返す。

    ビジネスロジックは wrap.wrap_captions へ委譲する。
    options が None の場合はデフォルト WrapCaptionsOptions() を使用する。
    """
    resolved_options = options if options is not None else WrapCaptionsOptions()
    return wrap_captions(
        input=input,
        output=output,
        options=resolved_options,
    )


# ===========================================================================
# エントリポイント（MCP stdio 起動）
# ===========================================================================


def main() -> None:
    """CLI エントリポイント。MCP サーバーを stdio で起動する。

    pyproject.toml の [project.scripts] で
    clipwright-wrap = "clipwright_wrap.server:main" として登録する。
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
