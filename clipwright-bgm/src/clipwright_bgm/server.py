"""server.py — clipwright-bgm MCP サーバー + CLI エントリポイント。

ビジネスロジックは bgm.py に委譲する薄いラッパー。
ClipwrightError の変換は bgm.py 側で行うため、ここでは二重変換しない。

トランスポートは stdio 既定（mcp.run(transport="stdio")）。
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from clipwright_bgm.bgm import add_bgm
from clipwright_bgm.schemas import BgmOptions

# FastMCP インスタンス（サーバー名）
mcp = FastMCP("clipwright-bgm")


# ===========================================================================
# clipwright_add_bgm MCP ツール
# ===========================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_add_bgm(
    timeline: Annotated[
        str,
        Field(description="入力 OTIO タイムラインファイルパス（.otio）。"),
    ],
    bgm: Annotated[
        str,
        Field(
            description=(
                "BGM ファイルパス（音声または動画）。"
                "許可拡張子: mp3, wav, m4a, aac, flac, ogg, opus, mp4, mkv, mov, webm。"
                "タイムラインファイルと同一ディレクトリに配置する必要がある。"
            )
        ),
    ],
    output: Annotated[
        str,
        Field(
            description=(
                "出力 OTIO タイムラインファイルパス（.otio 拡張子）。"
                "入力タイムラインとは別のパスを指定すること（非破壊・M5）。"
            )
        ),
    ],
    options: Annotated[
        BgmOptions | None,
        Field(
            description=(
                "BGM オプション（volume_db / fade_in_sec / fade_out_sec / ducking）。"
                "省略時は add_bgm 内部のデフォルト値を使用する。"
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """BGM クリップを OTIO タイムラインに追加する MCP ツール。

    出力 OTIO ファイルを新規生成するため readOnly ではないが、
    入力 OTIO・メディアは不変（非破壊）。
    clipwright-render の readOnlyHint=False（新ファイル生成）と対称の設計（CR M-4）。
    BGM 尺を core inspect_media で取得し、A2 Audio トラックに BGM クリップを追加する。
    BGM クリップには BgmDirective（volume_db/fade/ducking）を metadata に書く。
    実体化（ミックス）は clipwright-render が行う。

    ビジネスロジックは bgm.add_bgm へ委譲する。
    """
    return add_bgm(
        timeline=timeline,
        bgm=bgm,
        output=output,
        options=options,
    )


# ===========================================================================
# エントリポイント（MCP stdio 起動）
# ===========================================================================


def main() -> None:
    """CLI エントリポイント。MCP サーバーを stdio で起動する。

    pyproject.toml の [project.scripts] で
    clipwright-bgm = "clipwright_bgm.server:main" として登録する。
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
