"""schemas.py — clipwright-wrap 固有の Pydantic スキーマ。

共通型（MediaRef / Artifact / ToolResult 等）は clipwright.schemas で
一元定義されているため、このモジュールでは再定義しない。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field


class WrapCaptionsOptions(BaseModel):
    """clipwright_wrap_captions のオプション（WR-AD-05）。

    language は budoux parser 選択。
    spike-budoux で全4言語ロード可確認済み（DC-AM-005）。
    max_chars は 1 行最大文字数（一律 1 文字カウント・len() 判定）。
    max_lines は 1 cue 最大行数（超過は warnings 対象・WR-AD-15(1)）。
    """

    language: Annotated[
        str,
        Field(
            default="ja",
            max_length=7,
            pattern=r"^(ja|zh-hans|zh-hant|th)$",
            description=(
                "budoux の文節分割器を選択する言語コード。"
                "対応言語: ja / zh-hans / zh-hant / th。"
                "spike-budoux で全4言語確認済み（DC-AM-005）。"
                "対応外は INVALID_INPUT で拒否。"
            ),
        ),
    ] = "ja"

    max_chars: Annotated[
        int,
        Field(
            default=16,
            gt=0,
            description=(
                "1 行の最大文字数（一律 1 文字カウント・len() 判定）。"
                "日本語字幕慣習の全角 ~16 文字を既定値とする（WR-AD-05）。"
                "超過直前で改行を挿入する（貪欲行詰め・WR-AD-04）。"
                "1 文節が単独で超過する場合は途中で割らず 1 行に置く。"
                "gt=0 制約: 0 以下は INVALID_INPUT。"
            ),
        ),
    ] = 16

    max_lines: Annotated[
        int,
        Field(
            default=2,
            gt=0,
            description=(
                "1 cue あたりの最大行数。超過時は warnings に記録する。"
                "切り捨ては行わず原文を保持する（WR-AD-15(1)・要件 §2）。"
                "gt=0 制約: 0 以下は INVALID_INPUT。"
            ),
        ),
    ] = 2
