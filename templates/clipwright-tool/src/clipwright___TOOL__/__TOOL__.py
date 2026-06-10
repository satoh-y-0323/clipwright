"""__TOOL__.py — clipwright-__TOOL__ オーケストレーション層。

入出力検証 → （必要なら）OSS を subprocess 起動 → 結果の正規化 →
artifact 書き込み → エンベロープ返却。ここが「薄いラッパー/厚いアダプタ」の
アダプタ本体（spec §2.3）。MCP プロトコル面（server.py）は薄く保つ。

CONVENTIONS の MUST 対応:
- M2 返り値エンベロープ: clipwright.envelope の ok_result / error_result を使う。
- M3 検出と適用の分離: detect/inspect 系はメディアを書き換えず注記を返す。
- M4 外部 OSS は subprocess: 本体から import せず __TOOL___cli.py を別プロセス起動する。
- M5 非破壊: 入力は読むだけ・出力は新規生成・output == input は拒否する。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode

from clipwright___TOOL__.schemas import __Action__Options

# subprocess 失敗/timeout 時のサニタイズ済み文言（stderr のパス・秘密漏洩防止・CWE-209）
_SUBPROCESS_SAFE_MESSAGE = "内部サブプロセスが失敗しました"

# OSS 起動の timeout（秒）。入力規模に連動させたい場合は cue 数等から計算する。
_TIMEOUT_SECONDS = 60.0


def __ACTION__(
    input: str,
    output: str,
    options: __Action__Options,
) -> dict[str, Any]:
    """（TODO: このツールが何をするかを1文で。例: 〜を検出して JSON 注記を返す。）

    非破壊: 入力ファイルは読むだけで書き換えない（M5）。
    出力は新規生成した artifact のパスを artifacts に返す。

    Args:
        input: 入力ファイルパス（既存ファイル）。
        output: 出力 artifact パス（新規生成・入力とは別パス）。
        options: __Action__Options。

    Returns:
        ok_result または error_result のエンベロープ dict。
    """
    try:
        return ___ACTION___inner(input, output, options)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)


def ___ACTION___inner(
    input: str,
    output: str,
    options: __Action__Options,
) -> dict[str, Any]:
    """__ACTION__ の内部実装。ClipwrightError をそのまま送出する。"""
    input_path = Path(input)
    output_path = Path(output)

    # --- 1. 出力検証（M5）---

    # 出力拡張子の確認（このツールの出力形式に合わせる。雛形は JSON）。
    if output_path.suffix.lower() != ".json":
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"未対応の出力拡張子です: {output_path.suffix!r}",
            hint="出力ファイルの拡張子を .json にしてください。",
        )

    # 出力先の親ディレクトリ存在確認
    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="出力先ディレクトリが存在しません。",
            hint="出力先ディレクトリを先に作成してから再実行してください。",
        )

    # output == input 禁止（非破壊・M5）
    if _same_path(output_path, input_path):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="出力パスと入力パスが同一です。",
            hint="出力ファイルパスを入力とは別のパスに変更してください。",
        )

    # --- 2. 入力存在検査（FILE_NOT_FOUND の message は basename のみ・パス非露出）---

    if not input_path.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"ファイルが見つかりません: {input_path.name}",
            hint="入力ファイルのパスが正しいか確認してください。",
        )

    # --- 3. 検出/解析の本体 ---
    #
    # TODO: ここで実際の処理を行う。
    #   - 外部 OSS を使う場合: _run_cli() で別プロセス起動する（M4）。
    #     OSS を使わない純 Python 処理ならこのブロックを直接実装する。
    #   - detect/inspect 系はメディアを書き換えず注記データを作るだけ（M3）。
    #
    # 雛形ではダミー結果を生成する。
    result_data: dict[str, Any] = {
        "input": input_path.name,
        "threshold": options.example_threshold,
        "detections": [],  # TODO: 実際の検出結果に置き換える
    }

    # --- 4. artifact 書き込み（巨大明細は data でなくファイルへ逃がす・§2 SHOULD）---

    output_path.write_text(
        json.dumps(result_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # --- 5. エンベロープ構築（summary は判断に足る1〜2文・§2 SHOULD）---

    detection_count = len(result_data["detections"])
    summary = (
        f"{input_path.name} を解析し {detection_count} 件を検出しました。"
        f"結果を {output_path.name} に書き出しました。"
    )

    artifacts = [
        {"role": "analysis", "path": str(output_path), "format": "json"},
    ]

    return ok_result(
        summary,
        data={"detection_count": detection_count},
        artifacts=artifacts,
        warnings=[],
    )


def _same_path(a: Path, b: Path) -> bool:
    """2 パスが同一実体を指すかを判定する（resolve 失敗時は文字列比較に退避）。"""
    try:
        return a.resolve() == b.resolve()
    except OSError:  # pragma: no cover
        return str(a) == str(b)


def _run_cli(payload: dict[str, Any]) -> dict[str, Any]:
    """__TOOL___cli.py を別プロセス起動し stdout JSON を返す（M4・参考実装）。

    OSS を使うツールだけがこのヘルパーを使う。__ACTION___inner の TODO から呼ぶ。
    cli は常に return 0 し、失敗も stdout JSON の "error" キーで表現する契約。
    """
    stdin_payload = json.dumps(payload, ensure_ascii=False)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "clipwright___TOOL__.__TOOL___cli"],
            input=stdin_payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise ClipwrightError(
            code=ErrorCode.SUBPROCESS_TIMEOUT,
            message=f"{_SUBPROCESS_SAFE_MESSAGE}（タイムアウト）",
            hint="入力規模が大きすぎる可能性があります。再度試してください。",
        ) from None
    except OSError:
        raise ClipwrightError(
            code=ErrorCode.SUBPROCESS_FAILED,
            message=_SUBPROCESS_SAFE_MESSAGE,
            hint="CLI シムの起動に失敗しました。インストールを確認してください。",
        ) from None

    try:
        parsed: dict[str, Any] = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        raise ClipwrightError(
            code=ErrorCode.SUBPROCESS_FAILED,
            message=_SUBPROCESS_SAFE_MESSAGE,
            hint="CLI シムの出力 JSON パースに失敗しました。再実行してください。",
        ) from None

    if "error" in parsed:
        err = parsed["error"]
        code_str: str = err.get("code", str(ErrorCode.INTERNAL))
        msg: str = err.get("message", "CLI シムでエラーが発生しました")
        hint: str = err.get("hint", "再現条件を添えて報告してください。")
        try:
            code = ErrorCode(code_str)
        except ValueError:
            code = ErrorCode.INTERNAL
        raise ClipwrightError(code=code, message=msg, hint=hint)

    return parsed
