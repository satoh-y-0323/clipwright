"""server.py — clipwright-noise MCP サーバー + CLI エントリポイント。

ビジネスロジックは noise.py に委譲する薄いラッパー。
ClipwrightError の変換は noise.py 側で行うため、ここでは二重変換しない。

トランスポートは stdio 既定（mcp.run(transport="stdio")）。
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_noise.noise import detect_noise
from clipwright_noise.schemas import DetectNoiseOptions

# FastMCP インスタンス（サーバー名）
mcp = FastMCP("clipwright-noise")


# ===========================================================================
# clipwright_detect_noise MCP ツール
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_detect_noise(
    media: Annotated[
        str,
        Field(description="入力メディアファイルパス（映像＋音声を含む素材）。"),
    ],
    output: Annotated[
        str,
        Field(
            description=(
                "出力 OTIO タイムラインファイルパス（.otio 拡張子）。"
                "メディアファイルと同一ディレクトリに配置する必要がある。"
            )
        ),
    ],
    options: Annotated[
        DetectNoiseOptions | None,
        Field(
            description=(
                "ノイズ検出オプション（backend / strength）。"
                "省略時は backend=afftdn / strength=medium を使用する。"
            )
        ),
    ] = None,
    timeline: Annotated[
        str | None,
        Field(
            description=(
                "既存 OTIO タイムラインファイルパス。"
                "指定した場合はそのタイムラインに denoise 指示を追記する。"
                "省略時は新規タイムラインを生成する。"
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """音声ノイズを解析して denoise 指示付き OTIO タイムラインを生成する MCP ツール。

    入力メディアファイルは一切書き換えない（非破壊・readOnly）。
    ffmpeg astats でノイズフロアを測定し、backend 別のパラメータを算出して
    timeline-level metadata["clipwright"]["denoise"] に書き込む。
    出力は denoise 指示を持つ timeline.otio のパスを artifacts に返す。

    ビジネスロジックは noise.detect_noise へ委譲する。
    options が None の場合はデフォルト DetectNoiseOptions() を使用する。
    """
    resolved_options = options if options is not None else DetectNoiseOptions()
    return detect_noise(
        media=media,
        output=output,
        options=resolved_options,
        timeline=timeline,
    )


# ===========================================================================
# エントリポイント（MCP stdio 起動）
# ===========================================================================


def main() -> None:
    """CLI エントリポイント。MCP サーバーを stdio で起動する。

    pyproject.toml の [project.scripts] で
    clipwright-noise = "clipwright_noise.server:main" として登録する。
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
