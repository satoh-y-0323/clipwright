"""server.py — clipwright-silence MCP サーバー + CLI エントリポイント。

ビジネスロジックは detect.py に委譲する薄いラッパー。
ClipwrightError の変換は detect.py 側で行うため、ここでは二重変換しない。

トランスポートは stdio 既定（mcp.run(transport="stdio")）。
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_silence.detect import detect_silence
from clipwright_silence.schemas import DetectSilenceOptions

# FastMCP インスタンス（サーバー名）
mcp = FastMCP("clipwright-silence")


# ===========================================================================
# clipwright_detect_silence MCP ツール
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_detect_silence(
    media: Annotated[
        str,
        Field(description="入力メディアファイルパス（映像＋音声を含む素材）。"),
    ],
    output: Annotated[
        str,
        Field(description="出力 OTIO タイムラインファイルパス（.otio 拡張子）。"),
    ],
    options: Annotated[
        DetectSilenceOptions | None,
        Field(
            description=(
                "無音検出オプション（silence_threshold_db / min_silence_duration"
                " / padding / min_keep_duration）。省略時は全てデフォルト値を使用する。"
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """無音区間を検出して KEEP 区間の OTIO タイムラインを生成する MCP ツール。

    入力メディアファイルは一切書き換えない（非破壊・readOnly）。
    出力は新規生成した timeline.otio のパスを artifacts に返す。

    ビジネスロジックは detect.detect_silence へ委譲する。
    options が None の場合はデフォルト DetectSilenceOptions() を使用する。
    """
    resolved_options = options if options is not None else DetectSilenceOptions()
    return detect_silence(
        media=media,
        output=output,
        options=resolved_options,
    )


# ===========================================================================
# エントリポイント（MCP stdio 起動 / DC-GP-002）
# ===========================================================================


def main() -> None:
    """CLI エントリポイント。MCP サーバーを stdio で起動する（DC-GP-002）。

    pyproject.toml の [project.scripts] で
    clipwright-silence = "clipwright_silence.server:main" として登録する。
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
