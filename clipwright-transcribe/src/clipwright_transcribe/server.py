"""server.py — clipwright-transcribe MCP サーバー + CLI エントリポイント。

ビジネスロジックは transcribe.py に委譲する薄いラッパー。
ClipwrightError の変換は transcribe.py 側で行うため、ここでは二重変換しない。

トランスポートは stdio 既定（mcp.run(transport="stdio")）。
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_transcribe.schemas import TranscribeOptions
from clipwright_transcribe.transcribe import transcribe_media

# FastMCP インスタンス（サーバー名）
mcp = FastMCP("clipwright-transcribe")


# ===========================================================================
# clipwright_transcribe MCP ツール
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_transcribe(
    media: Annotated[
        str,
        Field(description="入力メディアファイルパス（音声を含む素材・映像は任意）。"),
    ],
    output: Annotated[
        str,
        Field(description="出力 OTIO タイムラインファイルパス（.otio 拡張子）。"),
    ],
    options: Annotated[
        TranscribeOptions | None,
        Field(
            description=(
                "文字起こしオプション（language / model_path / initial_prompt）。"
                "省略時は全てデフォルト値（言語自動検出・env モデル）を使用する。"
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """音声を文字起こしして SRT/VTT 字幕と OTIO タイムラインを生成する MCP ツール。

    入力メディアファイルは一切書き換えない（非破壊・readOnly）。
    出力は新規生成した timeline.otio / SRT / VTT のパスを artifacts に返す。

    ビジネスロジックは transcribe.transcribe_media へ委譲する。
    options が None の場合はデフォルト TranscribeOptions() を使用する。
    """
    resolved_options = options if options is not None else TranscribeOptions()
    return transcribe_media(
        media=media,
        output=output,
        options=resolved_options,
    )


# ===========================================================================
# エントリポイント（MCP stdio 起動）
# ===========================================================================


def main() -> None:
    """CLI エントリポイント。MCP サーバーを stdio で起動する。

    pyproject.toml の [project.scripts] で
    clipwright-transcribe = "clipwright_transcribe.server:main" として登録する。
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
