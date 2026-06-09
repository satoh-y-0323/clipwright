"""errors.py — エラーコード taxonomy と ClipwrightError 例外。

ライブラリ層は失敗時に ClipwrightError を送出し、
server.py の MCP 境界で error_result に変換する。
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Clipwright 全ツール共通のエラーコード（§4 + §13.1 DC-AM-002/DC-AS-003）。

    str を継承することで JSON 境界でのシリアライズ互換を保つ。
    値は名前文字列と一致させる。
    """

    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
    """ffmpeg/ffprobe 等の外部ツールが見つからない。"""
    INVALID_INPUT = "INVALID_INPUT"
    """引数バリデーション失敗。"""
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    """入力パスにファイルが存在しない。"""
    PATH_NOT_ALLOWED = "PATH_NOT_ALLOWED"
    """パストラバーサル等のパス検証失敗。"""
    SUBPROCESS_FAILED = "SUBPROCESS_FAILED"
    """外部プロセスが非ゼロ終了コードで終了した。"""
    SUBPROCESS_TIMEOUT = "SUBPROCESS_TIMEOUT"
    """外部プロセスがタイムアウトした。"""
    PROBE_FAILED = "PROBE_FAILED"
    """ffprobe 出力のパースに失敗した。"""
    OTIO_ERROR = "OTIO_ERROR"
    """OTIO ファイルの読み書き・パースに失敗した。"""
    PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
    """clipwright.json が見つからない。"""
    PROJECT_EXISTS = "PROJECT_EXISTS"
    """init 先に既存プロジェクトが存在する。"""
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    """未知または未対応のオペレーション種別。"""
    INTERNAL = "INTERNAL"
    """想定外の内部エラー（§13.1 DC-AM-002）。

    message は汎用メッセージとし、スタックトレースは hints/ログのみに出す。
    hint には「再現条件を添えて報告してください」を含める。
    """
    TRACK_NOT_FOUND = "TRACK_NOT_FOUND"
    """operations の track 指定がトラック総数を超えた（§13.1 DC-AS-003）。"""


class ClipwrightError(Exception):
    """Clipwright ライブラリ層が送出する例外。

    code / message / hint の三点セットを必ず持つ（§6.4 エラー規約）。
    hint にはユーザー・AI が次に取るべきアクションを具体的に記す。
    """

    def __init__(self, code: ErrorCode, message: str, hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
