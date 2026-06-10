"""captions.py — clipwright-transcribe 純ロジック層（plan.py 同型）。

whisper.cpp の `-oj` JSON（transcription[].offsets.from/to ミリ秒・text）を
正規化 segments に変換し、SRT/VTT 文字列を生成する。

設計判断:
- 外部プロセスを一切実行しない純関数群（契約面 100% 目標）。
- SRT/VTT のタイムコードは同一の秒値から導出し一貫性を保証する（DC-AS-005）。
  区切り文字のみが異なる（SRT="HH:MM:SS,mmm" / VTT="HH:MM:SS.mmm"）。
- セグメント0件時は to_srt が空文字列・to_vtt は "WEBVTT" ヘッダのみ（DC-GP-002）。
- whisper 出力の防御: 空 text・退化区間（start>=end）・欠落キーを除去する。
"""

from __future__ import annotations

from typing import Any, TypedDict


class Segment(TypedDict):
    """正規化済み字幕セグメント。

    start_sec / end_sec は秒（float）、text は前後空白を除去した本文。
    """

    start_sec: float
    end_sec: float
    text: str


def normalize_segments(whisper_json: dict[str, Any]) -> list[Segment]:
    """whisper `-oj` JSON を正規化 segments に変換する。

    transcription[].offsets.from/to（ミリ秒）を秒換算し、text を strip する。
    防御（DC-GP-002 補完）として以下を除去する:
      - offsets / from / to / text のいずれかのキーが欠落した要素
      - text が空または空白のみの要素
      - 退化区間（start_sec >= end_sec）の要素

    transcription キーが欠落・空の場合は空リストを返す。

    Args:
        whisper_json: whisper.cpp の `-oj` JSON を読み込んだ dict。

    Returns:
        正規化済み Segment のリスト。
    """
    transcription = whisper_json.get("transcription")
    if not isinstance(transcription, list):
        return []

    segments: list[Segment] = []
    for entry in transcription:
        if not isinstance(entry, dict):
            continue

        offsets = entry.get("offsets")
        if not isinstance(offsets, dict):
            continue
        if "from" not in offsets or "to" not in offsets:
            continue
        if "text" not in entry:
            continue

        try:
            start_ms = float(offsets["from"])
            end_ms = float(offsets["to"])
        except (TypeError, ValueError):
            continue

        text = str(entry["text"]).strip()
        if not text:
            continue

        start_sec = start_ms / 1000.0
        end_sec = end_ms / 1000.0
        # 退化区間（start >= end）を除去する
        if start_sec >= end_sec:
            continue

        segments.append({"start_sec": start_sec, "end_sec": end_sec, "text": text})

    return segments


def _format_timecode(total_seconds: float, *, ms_separator: str) -> str:
    """秒を "HH:MM:SS{sep}mmm" 形式のタイムコードに整形する。

    ms_separator で SRT（","）と VTT（"."）を切り替える。
    SRT/VTT で同一の秒値・ミリ秒値を共有させ一貫性を保つ（DC-AS-005）。
    ミリ秒は四捨五入（round → int 変換）で算出する。

    Args:
        total_seconds: 秒数。
        ms_separator: 秒とミリ秒の区切り文字（"," または "."）。

    Returns:
        整形済みタイムコード文字列。
    """
    total_ms = int(round(total_seconds * 1000.0))
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    seconds, milliseconds = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{ms_separator}{milliseconds:03d}"


def to_srt(segments: list[Segment]) -> str:
    """正規化 segments を SRT 文字列に変換する。

    1 始まりインデックス・"HH:MM:SS,mmm" タイムコード・空行区切り。
    セグメント0件時は空文字列を返す（DC-GP-002）。

    Args:
        segments: 正規化済み Segment のリスト。

    Returns:
        SRT 形式の文字列。
    """
    if not segments:
        return ""

    blocks: list[str] = []
    for index, seg in enumerate(segments, start=1):
        start_tc = _format_timecode(seg["start_sec"], ms_separator=",")
        end_tc = _format_timecode(seg["end_sec"], ms_separator=",")
        blocks.append(f"{index}\n{start_tc} --> {end_tc}\n{seg['text']}\n")

    return "\n".join(blocks)


def to_vtt(segments: list[Segment]) -> str:
    """正規化 segments を WebVTT 文字列に変換する。

    "WEBVTT" ヘッダ・"HH:MM:SS.mmm" タイムコード（ドット区切り）。
    セグメント0件時は "WEBVTT" ヘッダのみを返す（DC-GP-002）。

    Args:
        segments: 正規化済み Segment のリスト。

    Returns:
        WebVTT 形式の文字列。
    """
    if not segments:
        return "WEBVTT\n"

    blocks: list[str] = ["WEBVTT\n"]
    for seg in segments:
        start_tc = _format_timecode(seg["start_sec"], ms_separator=".")
        end_tc = _format_timecode(seg["end_sec"], ms_separator=".")
        blocks.append(f"{start_tc} --> {end_tc}\n{seg['text']}\n")

    return "\n".join(blocks)
