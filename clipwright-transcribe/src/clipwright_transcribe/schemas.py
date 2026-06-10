"""schemas.py — clipwright-transcribe 固有の Pydantic スキーマ。

共通型（MediaRef / Artifact / ToolResult 等）は clipwright.schemas で
一元定義されているため、このモジュールでは再定義しない。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field


class TranscribeOptions(BaseModel):
    """clipwright_transcribe のオプション（TR-AD-06）。

    language は whisper の言語指定（None=自動検出）。model_path は ggml
    モデルへのパス（None の場合 env CLIPWRIGHT_WHISPER_MODEL にフォールバック）。
    initial_prompt は whisper の認識精度を高める文脈ヒント。
    """

    language: Annotated[
        str | None,
        Field(
            default=None,
            max_length=10,
            pattern=r"^[a-zA-Z]{2,}$|^auto$",
            description=(
                '文字起こしの言語コード（例: "ja", "en"）。'
                "None（既定）の場合は whisper が言語を自動検出する。"
                "ISO639-1 相当の2文字以上英字 または 'auto'。それ以外は拒否。"
            ),
        ),
    ] = None

    model_path: Annotated[
        str | None,
        Field(
            default=None,
            max_length=4096,
            description=(
                "whisper.cpp の ggml モデルファイルへのパス"
                "（OS パス長上限相当: 4096）。"
                "None（既定）の場合は環境変数 CLIPWRIGHT_WHISPER_MODEL を使う。"
                "どちらも無い・ファイルが存在しない場合はエラーになる。"
            ),
        ),
    ] = None

    initial_prompt: Annotated[
        str | None,
        Field(
            default=None,
            max_length=2048,
            description=(
                "whisper に与える文脈ヒント（固有名詞・専門用語など）。"
                "None（既定）の場合はプロンプトなし。認識精度の調整に使う。"
                "whisper.cpp コンテキスト長相当の上限 2048 文字。"
            ),
        ),
    ] = None
