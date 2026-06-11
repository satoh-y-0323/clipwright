"""analyze.py — ffmpeg astats によるノイズフロア測定とパラメータ算出（設計 §2.3）。

astats フィルタで音声の RMS/Noise_floor を測定し、
backend 別の denoise パラメータを算出する。
測定不能時は nf=-50.0 にフォールバックし warning を返す（設計 B-6）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from clipwright.process import resolve_tool, run

# strength → afftdn nr (dB) の写像（設計 §2.1 確定値）
_STRENGTH_TO_NR: dict[str, float] = {
    "light": 6.0,
    "medium": 12.0,
    "strong": 24.0,
}

# nf のフォールバック値（astats 取得不能時・設計 B-6）
_NF_FALLBACK: float = -50.0

# nf の clamp 範囲（AfftdnParams の制約に合わせる）
_NF_MIN: float = -80.0
_NF_MAX: float = -20.0

# astats 実行 timeout（秒）
_TIMEOUT_SECONDS: float = 60.0


def _parse_noise_floor(stderr: str) -> float | None:
    """astats stderr からノイズフロア値（dB）を抽出する。

    優先順位:
    1. `Noise floor dB:` フィールド（実 ffmpeg astats 出力形式）
    2. `RMS level dB:` フィールド（fallback）

    取得不能なら None を返す。
    """
    # Noise floor dB を優先（実 ffmpeg astats 出力形式: "Noise floor dB: -X.X"）
    m = re.search(r"Noise floor dB:\s*(-?\d+\.?\d*)", stderr)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass

    # RMS level dB を fallback（実 ffmpeg astats 出力形式: "RMS level dB: -X.X"）
    m = re.search(r"RMS level dB:\s*(-?\d+\.?\d*)", stderr)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass

    return None


def _clamp(value: float, lo: float, hi: float) -> float:
    """value を [lo, hi] に clamp する。"""
    return max(lo, min(hi, value))


def measure_noise(
    media_path: Path,
    strength: str,
    backend: str,
) -> dict[str, Any]:
    """メディアの音声を astats で解析し、backend 別の denoise パラメータを返す。

    Args:
        media_path: 入力メディアファイルのパス（映像＋音声）。
        strength: DetectNoiseOptions.strength（"light"/"medium"/"strong"）。
        backend: "afftdn" または "deepfilternet"。

    Returns:
        {
            "params": dict（AfftdnParams 相当 or {}），
            "measured_noise_floor_db": float | None，
            "warnings": list[str]，
        }

    Raises:
        clipwright.errors.ClipwrightError: DEPENDENCY_MISSING / SUBPROCESS_FAILED /
            SUBPROCESS_TIMEOUT。
    """
    # ffmpeg 実行ファイルを解決する（B-1: resolve_tool 経由で PATH 非依存）
    ffmpeg_bin = resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")

    # astats で全区間のノイズフロアを測定（metadata=1:reset=0 で全体統計）
    cmd = [
        ffmpeg_bin,
        "-i",
        str(media_path),
        "-af",
        "astats=metadata=1:reset=0",
        "-f",
        "null",
        "-",
    ]

    warnings: list[str] = []

    # run は ClipwrightError（SUBPROCESS_FAILED / SUBPROCESS_TIMEOUT 等）を送出し、
    # そのまま呼び出し元に伝播させる。
    result = run(
        cmd,
        timeout=_TIMEOUT_SECONDS,
    )

    # astats の統計は stderr に出力される（run は CompletedProcess を返す）
    stderr_text = result.stderr

    measured = _parse_noise_floor(stderr_text)

    if backend == "afftdn":
        nr = _STRENGTH_TO_NR.get(strength, _STRENGTH_TO_NR["medium"])

        if measured is not None:
            nf = _clamp(measured, _NF_MIN, _NF_MAX)
        else:
            nf = _NF_FALLBACK
            warnings.append(
                f"ノイズフロア測定不能のため既定 nf={_NF_FALLBACK} を使用します。"
                " astats 出力に Noise floor dB / RMS level dB"
                " フィールドが含まれませんでした。"
            )

        params: dict[str, Any] = {"nr": nr, "nf": nf, "nt": "w"}
    else:
        # deepfilternet: params は {} 固定（初版・設計 DC-AM-002）
        if measured is None:
            warnings.append(
                "ノイズフロア測定不能のため measured_noise_floor_db=None になります。"
            )
        params = {}

    return {
        "params": params,
        "measured_noise_floor_db": measured,
        "warnings": warnings,
    }
