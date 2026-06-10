"""wrap.py — clipwright-wrap オーケストレーション層。

出力検証 → 入力存在検査 → 字幕パース → wrap_cli 起動（文節分割）→
captions で貪欲行詰め・再シリアライズ → 出力書き込み → エンベロープ返却。

設計判断:
- wrap_cli は sys.executable -m clipwright_wrap.wrap_cli で起動する（WR-AD-01）。
- wrap_cli エラー判定は stdout JSON の "error" キー有無で行う（DC-AS-007）。
- subprocess 失敗/timeout 時は _SUBPROCESS_SAFE_MESSAGE 同型サニタイズ。
- FILE_NOT_FOUND の message は basename のみ（フルパス非露出・WR-AD-09）。
- overflow 判定は行数超過(a) + 行幅超過(b) の両方（WR-AD-15(1)）。
- warnings は集約1文 + data に index 配列（WR-AD-13(2)・DC-AM-002）。
- artifacts は dict（Artifact モデル非インスタンス化・DC-AS-005）。
- OTIO は生成しない・使わない（WR-AD-06）。
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

from clipwright.envelope import error_result, ok_result
from clipwright.errors import ClipwrightError, ErrorCode

from clipwright_wrap.captions import (
    check_overflow,
    parse_captions,
    serialize_captions,
    wrap_cue_lines,
)
from clipwright_wrap.schemas import WrapCaptionsOptions

# subprocess 失敗/timeout 時のサニタイズ済み文言（stderr パス漏洩防止）
_SUBPROCESS_SAFE_MESSAGE = "内部サブプロセスが失敗しました"

# cue 数連動 timeout 係数（WR-AD-11/WR-AD-15(2)）
_TIMEOUT_COEFFICIENT = 0.05
_TIMEOUT_MIN = 30


def _compute_timeout(cue_count: int) -> float:
    """cue 数連動 timeout を計算する（max(30, ceil(cue_count * 0.05))）。"""
    return float(max(_TIMEOUT_MIN, math.ceil(cue_count * _TIMEOUT_COEFFICIENT)))


def wrap_captions(
    input: str,
    output: str,
    options: WrapCaptionsOptions,
) -> dict[str, Any]:
    """字幕ファイルに文節改行を挿入して整形済み字幕を生成する（WR-AD-04）。

    非破壊: 入力字幕ファイルは一切書き換えない。
    出力は新規生成した SRT/VTT のパスを artifacts に返す。

    Args:
        input: 入力字幕ファイルパス（.srt または .vtt）。
        output: 出力字幕ファイルパス（input と同一拡張子）。
        options: WrapCaptionsOptions（language/max_chars/max_lines）。

    Returns:
        ok_result または error_result のエンベロープ dict。
    """
    try:
        return _wrap_inner(input, output, options)
    except ClipwrightError as exc:
        return error_result(exc.code, exc.message, exc.hint)


def _wrap_inner(
    input: str,
    output: str,
    options: WrapCaptionsOptions,
) -> dict[str, Any]:
    """wrap_captions の内部実装。ClipwrightError をそのまま送出する。"""
    input_path = Path(input)
    output_path = Path(output)

    # --- 1. 出力検証（WR-AD-07/08）---

    # 拡張子が srt/vtt であることを確認
    input_ext = input_path.suffix.lower()
    output_ext = output_path.suffix.lower()

    if input_ext not in (".srt", ".vtt"):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"未対応の字幕形式です: {input_ext!r}",
            hint="入力ファイルの拡張子を .srt または .vtt にしてください。",
        )

    if output_ext not in (".srt", ".vtt"):
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"未対応の出力拡張子です: {output_ext!r}",
            hint="出力ファイルの拡張子を .srt または .vtt にしてください。",
        )

    # 入力・出力の拡張子一致を確認（SRT↔VTT 混在変換はスコープ外）
    if input_ext != output_ext:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=(
                f"入力と出力の拡張子が一致しません"
                f"（入力: {input_ext!r} / 出力: {output_ext!r}）。"
            ),
            hint="入力と同一拡張子の出力パスを指定してください。",
        )

    # 出力先の親ディレクトリ存在確認
    if not output_path.parent.exists():
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message="出力先ディレクトリが存在しません。",
            hint="出力先ディレクトリを先に作成してから再実行してください。",
        )

    # output == input 禁止
    try:
        if output_path.resolve() == input_path.resolve():
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="出力パスと入力パスが同一です。",
                hint="出力ファイルパスを入力とは別のパスに変更してください。",
            )
    except OSError:
        if str(output_path) == str(input_path):
            raise ClipwrightError(
                code=ErrorCode.INVALID_INPUT,
                message="出力パスと入力パスが同一です。",
                hint="出力ファイルパスを入力とは別のパスに変更してください。",
            ) from None

    # --- 2. 入力存在検査（WR-AD-09・FILE_NOT_FOUND basename 化）---

    if not input_path.exists():
        raise ClipwrightError(
            code=ErrorCode.FILE_NOT_FOUND,
            message=f"ファイルが見つかりません: {input_path.name}",
            hint="入力ファイルのパスが正しいか確認してください。",
        )

    # --- 3. 入力読み込み ---

    raw_text = input_path.read_text(encoding="utf-8")
    fmt = input_ext.lstrip(".")  # "srt" または "vtt"

    # --- 4. captions.parse_captions（不正 timecode → INVALID_INPUT + hint）---

    try:
        cues = parse_captions(raw_text, fmt)
    except ValueError as exc:
        # captions._parse_srt / _parse_vtt が送出する ValueError を INVALID_INPUT に変換
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"字幕ファイルのパースに失敗しました: {str(exc)}",
            hint=(
                "タイムコード行の形式を確認してください"
                "（例: 00:00:00,000 --> 00:00:01,000）。"
            ),
        ) from exc
    except ClipwrightError:
        raise

    # --- 5. wrap_cli 起動（WR-AD-02・DC-AS-007）---

    cue_count = len(cues)
    # 0 件の場合は wrap_cli を呼ばず直接シリアライズ
    if cue_count > 0:
        stdin_payload = json.dumps(
            {
                "language": options.language,
                "texts": [cue.text for cue in cues],
            },
            ensure_ascii=False,
        )
        timeout = _compute_timeout(cue_count)

        try:
            proc = subprocess.run(
                [sys.executable, "-m", "clipwright_wrap.wrap_cli"],
                input=stdin_payload.encode("utf-8"),
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_TIMEOUT,
                message=f"{_SUBPROCESS_SAFE_MESSAGE}（タイムアウト）",
                hint=(
                    "字幕ファイルの cue 数が多すぎる可能性があります。"
                    "再度試すか、cue 数を減らしてください。"
                ),
            ) from None
        except OSError:
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=_SUBPROCESS_SAFE_MESSAGE,
                hint=(
                    "wrap_cli の起動に失敗しました。"
                    "clipwright-wrap が正しくインストールされているか確認してください。"
                ),
            ) from None

        # wrap_cli は常に return 0 → exit code でなく "error" キーで判定（DC-AS-007）
        try:
            parsed: dict[str, Any] = json.loads(proc.stdout.decode("utf-8"))
        except (json.JSONDecodeError, ValueError):
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message=_SUBPROCESS_SAFE_MESSAGE,
                hint="wrap_cli の出力 JSON パースに失敗しました。再実行してください。",
            ) from None

        if "error" in parsed:
            err = parsed["error"]
            code_str: str = err.get("code", str(ErrorCode.INTERNAL))
            msg: str = err.get("message", "wrap_cli でエラーが発生しました")
            hint: str = err.get("hint", "再現条件を添えて報告してください。")
            # ErrorCode への変換（DEPENDENCY_MISSING は伝播）
            try:
                code = ErrorCode(code_str)
            except ValueError:
                code = ErrorCode.INTERNAL
            raise ClipwrightError(code=code, message=msg, hint=hint)

        segments: list[list[str]] = parsed.get("segments", [])
    else:
        segments = []

    # --- 6. 各 cue に wrap_cue_lines を適用 → overflow 判定 ---

    overflow_cue_indices: list[int] = []
    overflow_width_cue_indices: list[int] = []
    wrapped_count = 0

    for i, cue in enumerate(cues):
        seg = segments[i] if i < len(segments) else [cue.text]
        lines = wrap_cue_lines(seg, options.max_chars)

        # テキストが変化した（改行挿入）場合は wrapped_count を増やす
        new_text = "\n".join(lines)
        if new_text != cue.text:
            wrapped_count += 1

        # overflow 判定（WR-AD-15(1)）
        overflow = check_overflow(lines, options.max_chars, options.max_lines)
        if overflow["line_count_overflow"]:
            overflow_cue_indices.append(i)
        if overflow["line_width_overflow"]:
            overflow_width_cue_indices.append(i)

        # cue.text を整形済みテキストに更新（切り捨てなし・全文保持）
        cue.text = new_text

    # --- 7. captions.serialize_captions → output 書き込み ---

    serialized = serialize_captions(cues, fmt)
    output_path.write_text(serialized, encoding="utf-8")

    # --- 8. エンベロープ構築（WR-AD-13）---

    warnings: list[str] = []

    # 行数超過 warnings（集約1文・0件時は出さない・DC-AM-002）
    if overflow_cue_indices:
        warnings.append(
            f"max_lines（{options.max_lines}）を超える行数になった cue が"
            f" {len(overflow_cue_indices)} 件あります"
            f"（index: data.overflow_cue_indices を参照）。"
            "情報欠落を避けるため切り捨てずそのまま出力しました。"
        )

    # 行幅超過 warnings（集約1文・0件時は出さない）
    if overflow_width_cue_indices:
        warnings.append(
            f"max_chars（{options.max_chars}）を超える行幅になった cue が"
            f" {len(overflow_width_cue_indices)} 件あります"
            f"（index: data.overflow_width_cue_indices を参照）。"
            "情報欠落を避けるため切り捨てずそのまま出力しました。"
        )

    total_overflow = len(overflow_cue_indices) + len(overflow_width_cue_indices)
    summary = (
        f"{cue_count} cue を文節改行整形"
        f"（うち {wrapped_count} cue に改行挿入"
        f"・{total_overflow} cue が超過"
        f"）。言語: {options.language}。"
        f"{output_path.name} を生成しました。"
    )

    artifacts = [
        {"role": "captions", "path": str(output_path), "format": fmt},
    ]

    return ok_result(
        summary,
        data={
            "cue_count": cue_count,
            "wrapped_count": wrapped_count,
            "overflow_cue_indices": overflow_cue_indices,
            "overflow_width_cue_indices": overflow_width_cue_indices,
            "language": options.language,
        },
        artifacts=artifacts,
        warnings=warnings,
    )
