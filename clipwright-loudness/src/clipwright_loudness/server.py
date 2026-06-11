"""server.py — clipwright-loudness MCP サーバー + CLI エントリポイント。

ビジネスロジックは loudness.py に委譲する薄いラッパー。
ClipwrightError の変換は loudness.py 側で行うため、ここでは二重変換しない。

トランスポートは stdio 既定（mcp.run(transport="stdio")）。
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_loudness.loudness import detect_loudness
from clipwright_loudness.schemas import DetectLoudnessOptions

# FastMCP インスタンス（サーバー名）
mcp = FastMCP("clipwright-loudness")


# ===========================================================================
# clipwright_detect_loudness MCP ツール
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_detect_loudness(
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
        DetectLoudnessOptions | None,
        Field(
            description=(
                "ラウドネス検出オプション（mode / scope / target 等）。"
                "省略時は mode=loudnorm / scope=track /"
                " I=-14 / TP=-1 / LRA=11 を使用する。"
            )
        ),
    ] = None,
    timeline: Annotated[
        str | None,
        Field(
            description=(
                "既存 OTIO タイムラインファイルパス。"
                "指定した場合はそのタイムラインに loudness 指示を追記する。"
                "省略時は新規タイムラインを生成する。"
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """音声ラウドネスを解析して loudness 指示付き OTIO タイムラインを生成するツール。

    入力メディアファイルは一切書き換えない（非破壊・readOnly）。
    readOnlyHint=True は「入力メディアを変更しない」意味であり、
    output に指定した .otio ファイルは新規生成する（readOnly の範囲外）。
    ffmpeg loudnorm/volumedetect でラウドネスを測定し、loudness 指示を
    timeline-level metadata["clipwright"]["loudness"] に書き込む。
    出力は loudness 指示を持つ timeline.otio のパスを artifacts に返す。

    ビジネスロジックは loudness.detect_loudness へ委譲する。
    options が None の場合はデフォルト DetectLoudnessOptions() を使用する。
    """
    resolved_options = options if options is not None else DetectLoudnessOptions()
    return detect_loudness(
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
    clipwright-loudness = "clipwright_loudness.server:main" として登録する。
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
