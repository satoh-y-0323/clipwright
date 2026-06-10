"""plan.py — 無音区間から残す区間（KEEP）を導出する純ロジック。

ffmpeg を一切実行しない。秒 float の区間演算のみ行い、
OTIO 変換は detect 層に委ねる（AD-2/AD-3 設計方針）。
"""

from __future__ import annotations

from clipwright_silence.schemas import DetectSilenceOptions

# CR-Q-004: 浮動小数点比較の許容誤差。境界値の equal を保持するために使用する
# （DC-AM-001: min_keep の equal 保持 / _merge_intervals の隣接区間結合）。
_EPSILON = 1e-9


def derive_keep_ranges(
    total_duration_sec: float,
    silence_intervals: list[tuple[float, float]],
    options: DetectSilenceOptions,
) -> list[tuple[float, float]]:
    """無音区間リストから KEEP 区間リストを導出する。

    処理フロー（AD-3）:
    1. 無音区間を開始時刻でソートする。
    2. [0, total_duration_sec] を無音区間で反転して KEEP 区間を得る。
       - 無音ゼロ → [(0.0, total_duration_sec)] の 1 区間。
       - 全無音 → 空リスト。
    3. padding で各 KEEP を前後に拡張し [0, total] にクランプする。
    4. 重なりを持つ KEEP をマージする（DC-GP-001 短無音の埋め戻し）。
    5. min_keep_duration 未満の区間を破棄する（DC-AM-001 opt-in）。

    Args:
        total_duration_sec: 素材の総尺（秒）。
        silence_intervals: 無音区間のリスト。各要素は (start_sec, end_sec)。
        options: DetectSilenceOptions。padding / min_keep_duration を使用する
                 （silence_threshold_db / min_silence_duration は silencedetect
                 側の責務のため本関数では参照しない）。

    Returns:
        KEEP 区間のリスト。各要素は (start_sec, end_sec) の tuple[float, float]。
        時間昇順・重複なし。
    """
    total = total_duration_sec
    padding = options.padding
    min_keep = options.min_keep_duration

    # 1. 無音区間をソートする。
    sorted_silence = sorted(silence_intervals, key=lambda iv: iv[0])

    # 2. 反転: [0, total] から無音区間を引いて KEEP を得る。
    keeps: list[tuple[float, float]] = []
    cursor = 0.0
    for s_start, s_end in sorted_silence:
        if s_start > cursor:
            keeps.append((cursor, s_start))
        # cursor を無音終端まで進める（重複無音への対応）。
        cursor = max(cursor, s_end)
    # 末尾の発話区間。
    if cursor < total:
        keeps.append((cursor, total))

    # 3. padding 拡張 + クランプ。
    if padding > 0.0:
        padded: list[tuple[float, float]] = []
        for start, end in keeps:
            new_start = max(0.0, start - padding)
            new_end = min(total, end + padding)
            padded.append((new_start, new_end))
        keeps = padded

    # 4. 重なりをマージする（DC-GP-001）。
    keeps = _merge_intervals(keeps)

    # 5. min_keep_duration 未満を破棄する（既定 0.0 は破棄なし）。
    if min_keep > 0.0:
        # DC-AM-001: min_keep と同値の区間を破棄しないよう
        # _EPSILON で equal を保持する
        keeps = [
            (start, end) for start, end in keeps if (end - start) >= min_keep - _EPSILON
        ]

    return keeps


def _merge_intervals(
    intervals: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """重なる区間をマージして昇順の非重複リストを返す。

    区間は開始時刻でソート済みでなくてもよい（内部でソートする）。
    """
    if not intervals:
        return []

    sorted_ivs = sorted(intervals, key=lambda iv: iv[0])
    merged: list[tuple[float, float]] = [sorted_ivs[0]]

    for start, end in sorted_ivs[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + _EPSILON:
            # 重なりまたは隣接 → マージ。
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    return merged
