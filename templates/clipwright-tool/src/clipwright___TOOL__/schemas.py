"""schemas.py — clipwright-__TOOL__ 固有の Pydantic スキーマ。

共通型（MediaRef / TimeRange / Artifact / ToolResult 等）は clipwright.schemas で
一元定義されているため、このモジュールでは再定義しない（CONVENTIONS §2 共通型再利用）。
ここにはこのツール固有の入力オプションだけを置く。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field


class __Action__Options(BaseModel):
    """clipwright___ACTION__ のオプション。

    FastMCP は Annotated[type, Field(description=...)] からスキーマを生成する。
    description は AI が読む説明になるため、目的・既定値・制約を簡潔に書く。
    フィールドはこのツール固有のものに置き換える（example_threshold は雛形例）。
    """

    example_threshold: Annotated[
        float,
        Field(
            default=0.5,
            gt=0,
            description=(
                "（TODO: 雛形のサンプルフィールド。"
                "実際の検出・整形パラメータに置き換える。）"
                "gt=0 制約: 0 以下は INVALID_INPUT として拒否される。"
            ),
        ),
    ] = 0.5
