"""analyze.py — ffmpeg loudnorm/volumedetect によるラウドネス測定。

設計 §3.1・ADR-L1/L2 参照。

実 ffmpeg 8.1.1 Windows 出力形式（ADR-L3 実機確認済み）:

  loudnorm print_format=json:
    [Parsed_loudnorm_0 @ 0x...] ← 空行
    {
    \t"input_i" : "-21.75",
    \t"input_tp" : "-18.06",
    \t"input_lra" : "0.00",
    \t"input_thresh" : "-31.75",
    \t"output_i" : "-14.03",
    ...
    \t"target_offset" : "0.03"
    }
    ※ 値は文字列として引用符付きで出力される。"-inf" の場合がある（無音素材）。

  volumedetect:
    [Parsed_volumedetect_0 @ 0x...] max_volume: -18.1 dB
    ※ "max_volume: <VALUE> dB" 形式。

測定不能時は measured=None + warning を返す（U-1 確定方針・DC-AM-003）。
失敗 ClipwrightError はそのまま伝播させる。
message に絶対パスを混入させない（固定文言）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from clipwright.errors import ClipwrightError
from clipwright.process import resolve_tool, run
from pydantic import ValidationError

from clipwright_loudness.schemas import LoudnormMeasured, PeakMeasured

# ffmpeg 実行 timeout（秒）
# 測定のみ（loudnorm 1パス / volumedetect）は再エンコードより大幅に高速なため、
# 一律 300 秒あれば長尺素材でも十分に完了する。動的計算は不要と判断。
_TIMEOUT_SECONDS: float = 300.0


def _parse_loudnorm_measured(stderr: str) -> dict[str, Any] | None:
    """loudnorm print_format=json の stderr 末尾 JSON から測定値を抽出する。

    ffmpeg は stderr に JSON ブロックを出力する。値は文字列で引用符付き。
    "-inf" / "inf" が含まれる場合は LoudnormMeasured の allow_inf_nan=False により
    ValidationError になり None を返す（U-1）。

    Returns:
        {input_i, input_tp, input_lra, input_thresh, target_offset} の dict、
        または抽出不能な場合 None。
    """
    # stderr に含まれる全 JSON ブロック候補（{ ... }）を取得する。
    # re.search は先頭一致のため、先行する {} が loudnorm JSON より前に来た場合に
    # 正しい末尾ブロックを取りこぼす恐れがある（H-1）。
    # re.findall で全候補を取得し、末尾から required_keys を全て含む最初を採用する。
    candidates = re.findall(r"\{[^{}]+\}", stderr, re.DOTALL)
    if not candidates:
        return None

    required_keys = [
        "input_i",
        "input_tp",
        "input_lra",
        "input_thresh",
        "target_offset",
    ]

    raw: dict[str, Any] | None = None
    for block in reversed(candidates):
        try:
            parsed: dict[str, Any] = json.loads(block)
        except json.JSONDecodeError:
            continue
        if all(k in parsed for k in required_keys):
            raw = parsed
            break

    if raw is None:
        return None

    # 必要なフィールドを文字列 → float に変換する
    extracted: dict[str, Any] = {}
    for key in required_keys:
        val = raw[key]
        try:
            extracted[key] = float(val)
        except (ValueError, TypeError):
            return None

    # LoudnormMeasured で検証（inf/nan → ValidationError → None に縮退）
    try:
        validated = LoudnormMeasured(**extracted)
    except ValidationError:
        return None

    return validated.model_dump()


def _parse_volumedetect_measured(stderr: str) -> dict[str, Any] | None:
    """volumedetect の stderr から max_volume を抽出する。

    形式: "[Parsed_volumedetect_0 @ 0x...] max_volume: -X.X dB"

    Returns:
        {max_volume_db: float} の dict、または抽出不能な場合 None。
    """
    m = re.search(r"max_volume:\s*(-?\d+\.?\d*)\s*dB", stderr)
    if not m:
        return None

    try:
        val = float(m.group(1))
    except ValueError:
        return None

    # PeakMeasured で検証（範囲外 → ValidationError → None に縮退）
    try:
        validated = PeakMeasured(max_volume_db=val)
    except ValidationError:
        return None

    return validated.model_dump()


def measure_loudness(
    media: Path,
    *,
    mode: str,
    target_i: float = -14.0,
    target_tp: float = -1.0,
    target_lra: float = 11.0,
    target_peak_db: float = -1.0,
) -> dict[str, Any]:
    """メディアの音声をラウドネス測定し、測定値を返す（ADR-L1/L2/L7）。

    Args:
        media: 入力メディアファイルのパス（映像＋音声）。
        mode: "loudnorm" または "peak"。
        target_i: loudnorm 統合ラウドネス目標値（LUFS）。
        target_tp: loudnorm トゥルーピーク目標値（dBTP）。
        target_lra: loudnorm LRA 目標値（LU）。
        target_peak_db: peak ピーク目標値（dB）。

    Returns:
        {
            "measured": dict | None,  # mode 別の測定値。None は U-1 測定不能。
            "warnings": list[str],
        }

    Raises:
        clipwright.errors.ClipwrightError:
            DEPENDENCY_MISSING / SUBPROCESS_FAILED / SUBPROCESS_TIMEOUT。
    """
    # ffmpeg 実行ファイルを解決する（PATH 非依存）
    ffmpeg_bin = resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")

    warnings: list[str] = []

    if mode == "loudnorm":
        # loudnorm=I=<I>:TP=<TP>:LRA=<LRA>:print_format=json で1パス測定（ADR-L1）
        af_filter = (
            f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:print_format=json"
        )
        cmd: list[str] = [
            ffmpeg_bin,
            "-i",
            str(media),
            "-af",
            af_filter,
            "-f",
            "null",
            "-",
        ]

        try:
            result = run(cmd, timeout=_TIMEOUT_SECONDS)
        except ClipwrightError as exc:
            # run が送出する ClipwrightError の message に絶対パスが混入することを防ぐ。
            # ErrorCode は維持して固定文言で再送出する（セキュリティ: CWE-209）。
            # from None で __cause__ に元例外が残らないようにする（SR L-1）。
            raise ClipwrightError(
                code=exc.code,
                message="ffmpeg loudnorm コマンドが失敗しました。",
                hint="ffmpeg のバージョンや引数を確認してください。",
            ) from None
        measured = _parse_loudnorm_measured(result.stderr)

        if measured is None:
            warnings.append(
                "loudnorm 測定値を取得できませんでした。"
                " loudness 指示は書き込みません（U-1・DC-AM-003）。"
                " ffmpeg stderr に有効な loudnorm JSON が含まれませんでした。"
            )

        return {"measured": measured, "warnings": warnings}

    else:
        # mode == "peak": volumedetect で max_volume を測定（ADR-L2）
        cmd = [
            ffmpeg_bin,
            "-i",
            str(media),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ]

        try:
            result = run(cmd, timeout=_TIMEOUT_SECONDS)
        except ClipwrightError as exc:
            # from None で __cause__ に元例外が残らないようにする（SR L-1）。
            raise ClipwrightError(
                code=exc.code,
                message="ffmpeg volumedetect コマンドが失敗しました。",
                hint="ffmpeg のバージョンや引数を確認してください。",
            ) from None
        measured = _parse_volumedetect_measured(result.stderr)

        if measured is None:
            warnings.append(
                "volumedetect 測定値を取得できませんでした。"
                " loudness 指示は書き込みません（U-1・DC-AM-003）。"
                " ffmpeg stderr に max_volume フィールドが含まれませんでした。"
            )

        return {"measured": measured, "warnings": warnings}
