"""captions.py — clipwright-wrap 純ロジック層。

SRT/VTT の parse・文節トークン列の max_chars 貪欲行詰め・SRT/VTT の再シリアライズ・
overflow 判定を担う。budoux を一切 import しない純関数群（契約面 100% 目標）。

設計判断:
- タイムコード文字列は float 変換せず不変保持する（WR-AD-06）。
- SRT/VTT のバイト構造は WR-AD-12 の仕様に準拠する。
- 文節トークン結合時の区切り文字は挿入しない（WR-AD-14）。
- overflow 判定は行数超過(a) + 行幅超過(b) の両方を対象とする（WR-AD-15(1)）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TypedDict

from clipwright.errors import ClipwrightError, ErrorCode

# VTT タイムライン行: "HH:MM:SS.mmm --> HH:MM:SS.mmm [settings]" にマッチする正規表現
_VTT_TIMELINE_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2}\.\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}\.\d{3})(.*)"
)

# SRT タイムライン行: "HH:MM:SS,mmm --> HH:MM:SS,mmm" にマッチする正規表現
_SRT_TIMELINE_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2},\d{3})\s*$"
)

# VTT インラインタグ（<c> <b> <i> <v> <ruby> 等）の検出
_VTT_INLINE_TAG_RE = re.compile(r"<[a-zA-Z/][^>]*>")


@dataclass
class Cue:
    """字幕 1 cue の正規化表現。

    index はシーケンス番号（1始まり）。
    start / end はタイムコード文字列（float 変換しない・WR-AD-06）。
    text は cue の本文テキスト（改行は '\\n' で表現）。
    VTT の cue settings は end フィールドの末尾に保持する
    （例: "00:00:01.000 line:90% position:50%"）。
    """

    index: int
    start: str
    end: str
    text: str


class _OverflowResult(TypedDict):
    """check_overflow の戻り値型。"""

    line_count_overflow: bool
    line_width_overflow: bool


def _parse_srt(text: str) -> list[Cue]:
    """SRT テキストを cue リストに変換する。

    WR-AD-12(1)(2) のバイト構造仕様に準拠する:
    - 空行区切り（連続/末尾空行に頑健）
    - 末尾 cue に空行が無い（単一改行 EOF）ケースで最終 cue を取りこぼさない
    - 0 件（空文字列・改行のみ）→ []
    - cue 内複数行テキストは空文字結合（半角空白挿入なし・WR-AD-14）
    - 不正な timecode 行 → ClipwrightError(INVALID_INPUT)
    """
    if not text.strip():
        return []

    # 連続空行を 1 区切りとして扱うため、複数改行を 2 改行に正規化してから分割
    normalized = re.sub(r"\n{2,}", "\n\n", text.strip())
    blocks = normalized.split("\n\n")

    cues: list[Cue] = []
    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:
            continue

        # 行1: index 番号
        try:
            index = int(lines[0].strip())
        except ValueError:
            # index 行でないブロックはスキップ（空ブロック等の防御）
            continue

        if len(lines) < 2:
            continue

        # 行2: タイムライン行
        timeline_line = lines[1].strip()
        m = _SRT_TIMELINE_RE.match(timeline_line)
        if m is None:
            # タイムコード行が不正: ValueError を送出（テスト契約 WR-AD-09 に準拠）
            raise ValueError(
                f"SRT タイムコード行が不正です: {timeline_line!r}"
                f" (期待形式: 'HH:MM:SS,mmm --> HH:MM:SS,mmm')"
            )

        start = m.group(1)
        end = m.group(2)

        # 行3以降: テキスト（複数行は空文字結合・半角空白挿入なし）
        text_lines = lines[2:] if len(lines) > 2 else []
        joined_text = "".join(text_lines)

        cues.append(Cue(index=index, start=start, end=end, text=joined_text))

    return cues


def _parse_vtt(text: str) -> list[Cue]:
    """VTT テキストを cue リストに変換する。

    WR-AD-12(1)(2)(3) のバイト構造仕様・VTT エッジ 5 種の挙動に準拠する:
    - WEBVTT ヘッダ直後の空行をスキップ
    - 0 件（"WEBVTT\\n" のみ）→ []
    - NOTE/STYLE ブロック: 原文保持（cue としては扱わない）
    - cue id 行: 保持し text 行のみ整形対象
    - cue settings（タイムライン行の後続文字列）: end フィールドの末尾に保持
    - インラインタグを含む cue: text をそのまま保持（タグ込みで 1 行）
    - cue 内複数行テキストは空文字結合（WR-AD-14）
    """
    lines = text.splitlines()

    # WEBVTT ヘッダの確認と除去
    if not lines or not lines[0].startswith("WEBVTT"):
        return []

    # ヘッダ行以降を処理
    pos = 1
    total = len(lines)

    # ヘッダ直後の空行をスキップ
    while pos < total and lines[pos].strip() == "":
        pos += 1

    cues: list[Cue] = []
    cue_index = 1

    while pos < total:
        # 空行をスキップ（cue 区切り）
        if lines[pos].strip() == "":
            pos += 1
            continue

        # NOTE ブロック: 次の空行または EOF まで読み飛ばす
        if lines[pos].startswith("NOTE"):
            pos += 1
            while pos < total and lines[pos].strip() != "":
                pos += 1
            continue

        # STYLE ブロック: 次の空行または EOF まで読み飛ばす
        if lines[pos].startswith("STYLE"):
            pos += 1
            while pos < total and lines[pos].strip() != "":
                pos += 1
            continue

        # cue id 行の確認（タイムライン行ではない非空行）
        if not _VTT_TIMELINE_RE.match(lines[pos]):
            # cue id 行: タイムライン前の識別子行（保持のため読み飛ばす）
            pos += 1
            if pos >= total:
                break

        # タイムライン行
        if pos >= total or lines[pos].strip() == "":
            pos += 1
            continue

        m = _VTT_TIMELINE_RE.match(lines[pos])
        if m is None:
            # タイムライン行として認識できない → スキップ
            pos += 1
            continue

        start = m.group(1)
        # settings 部分を end に付加して保持（WR-AD-12(3)(d)）
        end_raw = m.group(2)
        settings = m.group(3).strip()
        end = f"{end_raw} {settings}" if settings else end_raw

        pos += 1

        # テキスト行の収集（次の空行 or EOF まで）
        text_lines: list[str] = []
        while pos < total and lines[pos].strip() != "":
            text_lines.append(lines[pos])
            pos += 1

        # テキストを空文字結合（半角空白挿入なし・WR-AD-14）
        joined_text = "".join(text_lines)

        cues.append(Cue(index=cue_index, start=start, end=end, text=joined_text))
        cue_index += 1

    return cues


def parse_captions(text: str, fmt: str) -> list[Cue]:
    """SRT または VTT テキストを Cue リストに変換する。

    fmt は "srt" または "vtt" を指定する。
    タイムコード文字列は不変保持する（WR-AD-06）。
    cue 内の複数行テキストは空文字結合する（WR-AD-14）。
    不正な timecode 行は ClipwrightError(INVALID_INPUT) を送出する（WR-AD-09）。

    Args:
        text: SRT または VTT 形式の文字列。
        fmt: "srt" または "vtt"。

    Returns:
        Cue のリスト。0 件の場合は空リストを返す。
    """
    if fmt == "srt":
        return _parse_srt(text)
    elif fmt == "vtt":
        return _parse_vtt(text)
    else:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"未対応の字幕形式です: {fmt!r}",
            hint="fmt には 'srt' または 'vtt' を指定してください。",
        )


def wrap_cue_lines(segments: list[str], max_chars: int) -> list[str]:
    """文節トークン列を max_chars で貪欲に行へ詰めた行リストを返す。

    WR-AD-04/WR-AD-14 の仕様に準拠する:
    - 文節を 1 行に足していき、超過直前で改行する（貪欲行詰め）。
    - 1 文節が単独で max_chars を超える場合はその文節を 1 行に置く（途中で割らない）。
    - 文節間の区切り文字は挿入しない（WR-AD-14(i)・結合すると原文を復元できる）。
    - 各行の len() に '\\n' は含まれない（WR-AD-14(ii)）。
    - 全角/半角を同じ 1 文字としてカウントする（WR-AD-14(iii)・一律 len() 判定）。

    Args:
        segments: 文節トークンのリスト。
        max_chars: 1 行の最大文字数（gt=0）。

    Returns:
        行リスト（各行に '\\n' を含まない）。空の segments は [] を返す。
    """
    if not segments:
        return []

    lines: list[str] = []
    current_line = ""

    for seg in segments:
        if not current_line:
            # 行の先頭: 文節が max_chars を超えていても 1 行に置く（途中で割らない）
            current_line = seg
        elif len(current_line) + len(seg) <= max_chars:
            # 追加しても max_chars 以内 → 同じ行に連結
            current_line += seg
        else:
            # 超過直前で改行
            lines.append(current_line)
            current_line = seg

    if current_line:
        lines.append(current_line)

    return lines


def _serialize_srt(cues: list[Cue]) -> str:
    """Cue リストを SRT 文字列に変換する。

    WR-AD-12(1) のバイト構造仕様:
    - 各 block = "index\\nstart --> end\\ntext\\n"
    - cue 間は空行 1 つ（block 末尾 \\n + join の \\n）
    - 末尾 cue の後ろは単一改行（空行なし）
    - 0 件 → ""
    """
    if not cues:
        return ""

    blocks: list[str] = []
    for cue in cues:
        blocks.append(f"{cue.index}\n{cue.start} --> {cue.end}\n{cue.text}\n")

    return "\n".join(blocks)


def _serialize_vtt(cues: list[Cue]) -> str:
    """Cue リストを VTT 文字列に変換する。

    WR-AD-12(1) のバイト構造仕様:
    - "WEBVTT\\n" + "\\n" + cue1 + "\\n" + cue2 + ...
    - 各 cue block = "start --> end\\ntext\\n"
    - cue 間は空行 1 つ、末尾 cue の後ろは単一改行（空行なし）
    - 0 件 → "WEBVTT\\n"
    """
    if not cues:
        return "WEBVTT\n"

    blocks: list[str] = ["WEBVTT\n"]
    for cue in cues:
        blocks.append(f"{cue.start} --> {cue.end}\n{cue.text}\n")

    return "\n".join(blocks)


def serialize_captions(cues: list[Cue], fmt: str) -> str:
    """Cue リストを SRT または VTT 文字列に変換する。

    タイムコード文字列は不変で書き戻す（WR-AD-06）。
    0 件時: SRT は "" / VTT は "WEBVTT\\n"（往復同一・WR-AD-12(2)）。

    Args:
        cues: Cue のリスト。
        fmt: "srt" または "vtt"。

    Returns:
        SRT または VTT 形式の文字列。
    """
    if fmt == "srt":
        return _serialize_srt(cues)
    elif fmt == "vtt":
        return _serialize_vtt(cues)
    else:
        raise ClipwrightError(
            code=ErrorCode.INVALID_INPUT,
            message=f"未対応の字幕形式です: {fmt!r}",
            hint="fmt には 'srt' または 'vtt' を指定してください。",
        )


def check_overflow(lines: list[str], max_chars: int, max_lines: int) -> _OverflowResult:
    """行リストの overflow（行数超過・行幅超過）を判定する。

    WR-AD-15(1) の overflow 判定仕様:
    - (a) 行数 > max_lines → line_count_overflow: True
    - (b) いずれかの行の len() > max_chars → line_width_overflow: True
    単一巨大文節（行数1・行幅超過）も (b) の対象になる。
    lines は変更しない（情報欠落回避）。

    Args:
        lines: 判定対象の行リスト（各行に '\\n' を含まない想定）。
        max_chars: 1 行の最大文字数。
        max_lines: 最大行数。

    Returns:
        line_count_overflow と line_width_overflow を持つ dict。
    """
    line_count_overflow = len(lines) > max_lines
    line_width_overflow = any(len(line) > max_chars for line in lines)

    return {
        "line_count_overflow": line_count_overflow,
        "line_width_overflow": line_width_overflow,
    }
