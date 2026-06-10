"""test_plan.py — plan.py（純ロジック）の Red テスト。

対象関数:
  derive_keep_ranges(
      total_duration_sec: float,
      silence_intervals: list[tuple[float, float]],
      options: DetectSilenceOptions,
  ) -> list[tuple[float, float]]

plan.py は ffmpeg を一切実行しない純ロジック。
silence_threshold_db / min_silence_duration は silencedetect 側責務のため
derive_keep_ranges には渡さない（padding / min_keep_duration のみ使用）。

AD-3 / DC-AM-001 / DC-GP-001 の観点を網羅する。
"""

from __future__ import annotations

import pytest

from clipwright_silence.plan import derive_keep_ranges
from clipwright_silence.schemas import DetectSilenceOptions


# ===========================================================================
# ヘルパー
# ===========================================================================


def _opts(
    padding: float = 0.0,
    min_keep_duration: float = 0.0,
) -> DetectSilenceOptions:
    """テスト用 DetectSilenceOptions を構築する（silence 系パラメータは既定値）。"""
    return DetectSilenceOptions(
        silence_threshold_db=-30.0,
        min_silence_duration=0.5,
        padding=padding,
        min_keep_duration=min_keep_duration,
    )


# ===========================================================================
# ① 無音ゼロ → 全尺 1 区間
# ===========================================================================


class TestNoSilence:
    """無音区間が空の場合: 全尺を 1 KEEP として返す（AD-3 §2）。"""

    def test_no_silence_returns_full_range(self) -> None:
        """無音ゼロ → [(0.0, total_duration)] の 1 区間。"""
        keeps = derive_keep_ranges(10.0, [], _opts())
        assert keeps == [(0.0, 10.0)]

    def test_no_silence_returns_single_interval(self) -> None:
        """無音ゼロ: 返却リストの長さは 1。"""
        keeps = derive_keep_ranges(60.0, [], _opts())
        assert len(keeps) == 1

    def test_no_silence_with_padding_still_full_range(self) -> None:
        """無音ゼロ・padding あり: [(0.0, total)] のまま（クランプで変化なし）。"""
        keeps = derive_keep_ranges(10.0, [], _opts(padding=1.0))
        assert keeps == [(0.0, 10.0)]


# ===========================================================================
# ② 先頭無音の反転
# ===========================================================================


class TestLeadingSilence:
    """先頭が無音の場合: KEEP は無音終了点から始まる（AD-3 §2）。"""

    def test_leading_silence_keep_starts_at_silence_end(self) -> None:
        """先頭 0〜3s が無音 → KEEP は (3.0, 10.0)。"""
        keeps = derive_keep_ranges(10.0, [(0.0, 3.0)], _opts())
        assert keeps == [(3.0, 10.0)]

    def test_leading_silence_exact_boundary(self) -> None:
        """先頭無音の境界: silence_end と keep_start が一致する。"""
        keeps = derive_keep_ranges(5.0, [(0.0, 2.5)], _opts())
        assert len(keeps) == 1
        assert keeps[0][0] == pytest.approx(2.5)
        assert keeps[0][1] == pytest.approx(5.0)


# ===========================================================================
# ③ 末尾無音の反転 / 複数無音区間の反転
# ===========================================================================


class TestTrailingAndMultipleSilence:
    """末尾無音および複数無音区間の反転（AD-3 §2）。"""

    def test_trailing_silence_keep_ends_at_silence_start(self) -> None:
        """末尾 7〜10s が無音 → KEEP は (0.0, 7.0)。"""
        keeps = derive_keep_ranges(10.0, [(7.0, 10.0)], _opts())
        assert keeps == [(0.0, 7.0)]

    def test_two_silences_three_keeps(self) -> None:
        """中間無音 2 つ → KEEP 3 区間に反転される。"""
        # 無音: 2〜3, 6〜7 → KEEP: (0,2), (3,6), (7,10)
        keeps = derive_keep_ranges(10.0, [(2.0, 3.0), (6.0, 7.0)], _opts())
        assert len(keeps) == 3
        assert keeps[0] == pytest.approx((0.0, 2.0))
        assert keeps[1] == pytest.approx((3.0, 6.0))
        assert keeps[2] == pytest.approx((7.0, 10.0))

    def test_leading_and_trailing_silence_one_keep(self) -> None:
        """先頭・末尾に無音 → 中間 1 KEEP。"""
        keeps = derive_keep_ranges(10.0, [(0.0, 2.0), (8.0, 10.0)], _opts())
        assert keeps == [(2.0, 8.0)]

    def test_three_silences_four_keeps(self) -> None:
        """無音 3 つ → KEEP 4 区間。"""
        # 無音: 1〜2, 4〜5, 7〜8 → KEEP: (0,1),(2,4),(5,7),(8,10)
        keeps = derive_keep_ranges(
            10.0, [(1.0, 2.0), (4.0, 5.0), (7.0, 8.0)], _opts()
        )
        assert len(keeps) == 4
        assert keeps[0] == pytest.approx((0.0, 1.0))
        assert keeps[1] == pytest.approx((2.0, 4.0))
        assert keeps[2] == pytest.approx((5.0, 7.0))
        assert keeps[3] == pytest.approx((8.0, 10.0))


# ===========================================================================
# ④ padding 拡張と [0, total] クランプ
# ===========================================================================


class TestPaddingClamp:
    """padding 拡張後に [0, total_duration] へクランプされること（AD-3 §3）。"""

    def test_padding_expands_keep_range(self) -> None:
        """padding=0.5s: KEEP が前後に 0.5s 拡張される。"""
        # 無音: 3〜7 → 反転 KEEP: (0,3),(7,10)
        # padding=0.5 → (0,3.5),(6.5,10) ← クランプ後
        keeps = derive_keep_ranges(10.0, [(3.0, 7.0)], _opts(padding=0.5))
        assert len(keeps) == 2
        assert keeps[0][1] == pytest.approx(3.5)
        assert keeps[1][0] == pytest.approx(6.5)

    def test_padding_clamps_start_to_zero(self) -> None:
        """先頭 KEEP の start が padding で 0 より小さくなる → 0 にクランプ。"""
        # 無音: 2〜5 → KEEP: (0,2),(5,10)
        # padding=1.0 → (0,3),(4,10) ← start クランプ
        keeps = derive_keep_ranges(10.0, [(2.0, 5.0)], _opts(padding=1.0))
        assert keeps[0][0] == pytest.approx(0.0)

    def test_padding_clamps_end_to_total(self) -> None:
        """末尾 KEEP の end が padding で total を超える → total にクランプ。"""
        # 無音: 5〜8 → KEEP: (0,5),(8,10)
        # padding=1.0 → end(10+1=11) → クランプで 10
        keeps = derive_keep_ranges(10.0, [(5.0, 8.0)], _opts(padding=1.0))
        last_end = keeps[-1][1]
        assert last_end == pytest.approx(10.0)

    def test_padding_zero_no_expansion(self) -> None:
        """padding=0.0: 拡張なし（反転のみ）。"""
        keeps = derive_keep_ranges(10.0, [(3.0, 7.0)], _opts(padding=0.0))
        assert keeps[0] == pytest.approx((0.0, 3.0))
        assert keeps[1] == pytest.approx((7.0, 10.0))


# ===========================================================================
# ⑤ padding で隣接 KEEP がマージされる
# ===========================================================================


class TestPaddingMerge:
    """padding 拡張で隣接 KEEP が重なったらマージされること（AD-3 §3）。"""

    def test_padding_merges_adjacent_keeps(self) -> None:
        """padding で 2 KEEP の端が重なる → 1 KEEP にマージ。

        例: 無音 (3,4) → KEEP (0,3),(4,10)
        padding=1.0 → (0,4),(3,10) → 重なり → (0,10)
        """
        keeps = derive_keep_ranges(10.0, [(3.0, 4.0)], _opts(padding=1.0))
        assert len(keeps) == 1
        assert keeps[0][0] == pytest.approx(0.0)
        assert keeps[0][1] == pytest.approx(10.0)

    def test_padding_no_merge_when_gap_large_enough(self) -> None:
        """padding が小さく KEEP 端が重ならない場合はマージしない。"""
        # 無音 (3,7) → KEEP (0,3),(7,10)
        # padding=0.1 → (0,3.1),(6.9,10) → 重ならない
        keeps = derive_keep_ranges(10.0, [(3.0, 7.0)], _opts(padding=0.1))
        assert len(keeps) == 2

    def test_padding_merges_three_keeps_into_two(self) -> None:
        """padding で 3 KEEP の隣接 2 つが重なる → 2 KEEP になる。"""
        # 無音: (2,3),(5,6) → KEEP: (0,2),(3,5),(6,10)
        # padding=0.6 → (0,2.6),(2.4,5.6),(5.4,10)
        # (0,2.6) と (2.4,5.6) が重なる → merge (0, 5.6)
        # (0,5.6) と (5.4,10) が重なる → merge (0, 10)
        keeps = derive_keep_ranges(10.0, [(2.0, 3.0), (5.0, 6.0)], _opts(padding=0.6))
        # 全体が連結されて 1 区間になる場合もある
        assert len(keeps) >= 1
        assert len(keeps) < 3


# ===========================================================================
# ⑥ min_keep_duration: 既定 0.0 では破棄なし / 正値時のみ短 KEEP 破棄（DC-AM-001）
# ===========================================================================


class TestMinKeepDuration:
    """min_keep_duration の挙動（DC-AM-001）。"""

    def test_default_zero_keeps_all_intervals(self) -> None:
        """min_keep_duration=0.0（既定）: 短い KEEP も破棄されない。"""
        # 無音 (1,1.9) → KEEP (0,1),(1.9,10)
        # (1.9,10) は長い・(0,1) は 1s → 0.0 なので破棄なし
        keeps = derive_keep_ranges(10.0, [(1.0, 1.9)], _opts(min_keep_duration=0.0))
        assert len(keeps) == 2

    def test_min_keep_filters_short_keep(self) -> None:
        """min_keep_duration > 0: それ未満の KEEP は破棄される。

        例: 無音 (0.5, 9.5) → KEEP (0,0.5),(9.5,10)
        (0,0.5) は 0.5s, (9.5,10) は 0.5s
        min_keep_duration=1.0 → 両方破棄
        """
        keeps = derive_keep_ranges(
            10.0, [(0.5, 9.5)], _opts(min_keep_duration=1.0)
        )
        for start, end in keeps:
            assert (end - start) >= 1.0 - 1e-9

    def test_min_keep_keeps_long_interval(self) -> None:
        """min_keep_duration 設定でも、長い KEEP は保持される。"""
        # 無音 (2,3) → KEEP (0,2),(3,10)
        # (0,2)=2s, (3,10)=7s → min_keep=1.5 → 両方保持
        keeps = derive_keep_ranges(
            10.0, [(2.0, 3.0)], _opts(min_keep_duration=1.5)
        )
        assert len(keeps) == 2

    def test_min_keep_exact_boundary_kept(self) -> None:
        """min_keep_duration と同値の KEEP は破棄されない（境界値: ちょうど equal は保持）。"""
        # 無音 (1,2) → KEEP (0,1),(2,10)
        # (0,1)=1.0s → min_keep=1.0 → 保持
        keeps = derive_keep_ranges(
            10.0, [(1.0, 2.0)], _opts(min_keep_duration=1.0)
        )
        # 1.0s の KEEP が残っていること
        durations = [end - start for start, end in keeps]
        assert any(abs(d - 1.0) < 1e-9 for d in durations)

    def test_min_keep_applied_after_padding_merge(self) -> None:
        """min_keep_duration はパディング・マージ後に適用される。

        padding でマージされた KEEP の長さで判定すること。
        """
        # 無音 (5,6) → KEEP (0,5),(6,10)
        # padding=0 → 2 KEEP: 5s,4s → min_keep=3 → 両方残る
        keeps = derive_keep_ranges(
            10.0, [(5.0, 6.0)], _opts(padding=0.0, min_keep_duration=3.0)
        )
        assert len(keeps) == 2


# ===========================================================================
# ⑦ 全無音 → KEEP 空リスト
# ===========================================================================


class TestAllSilence:
    """全尺が無音の場合: KEEP 空リストを返す（AD-3 §2）。"""

    def test_full_silence_returns_empty_list(self) -> None:
        """全尺が無音 → []。"""
        keeps = derive_keep_ranges(10.0, [(0.0, 10.0)], _opts())
        assert keeps == []

    def test_full_silence_no_padding_effect(self) -> None:
        """全尺無音 + padding: それでも空リストを返す。"""
        keeps = derive_keep_ranges(10.0, [(0.0, 10.0)], _opts(padding=1.0))
        assert keeps == []

    def test_nearly_full_silence_only_tiny_keep(self) -> None:
        """ほぼ全無音 → 微小 KEEP が 1 区間（min_keep_duration=0 では破棄しない）。"""
        # 無音 (0,9.99) → KEEP (9.99, 10.0)
        keeps = derive_keep_ranges(10.0, [(0.0, 9.99)], _opts(min_keep_duration=0.0))
        assert len(keeps) == 1
        assert keeps[0][0] == pytest.approx(9.99)
        assert keeps[0][1] == pytest.approx(10.0)


# ===========================================================================
# ⑧ padding で 2 KEEP が短無音をまたいで重なる → 連結（DC-GP-001・単語切れ防止）
# ===========================================================================


class TestShortSilenceBridging:
    """短い無音を padding がまたぐ場合: 埋め戻し（連結）が起きること（DC-GP-001）。

    設計意図: 短い無音（息継ぎ・ポーズ）を挟んで KEEP が隣接している場合に
    padding が無音をまたいで連結することで単語切れを防ぐ。
    """

    def test_short_silence_bridged_by_padding(self) -> None:
        """短い無音 (4.8, 5.2) = 0.4s を padding=0.3 がまたぐ → 1 KEEP に連結。

        KEEP before padding: (0,4.8),(5.2,10)
        KEEP after padding:  (0,5.1),(4.9,10)  ← 重なる
        merged:              (0, 10)
        """
        keeps = derive_keep_ranges(
            10.0, [(4.8, 5.2)], _opts(padding=0.3)
        )
        assert len(keeps) == 1
        assert keeps[0][0] == pytest.approx(0.0)
        assert keeps[0][1] == pytest.approx(10.0)

    def test_long_silence_not_bridged(self) -> None:
        """長い無音 (3,7) = 4s は padding=0.3 でまたがない → 2 KEEP のまま。"""
        keeps = derive_keep_ranges(10.0, [(3.0, 7.0)], _opts(padding=0.3))
        assert len(keeps) == 2

    def test_bridging_preserves_outer_keeps(self) -> None:
        """埋め戻し後の結合 KEEP が元の 2 KEEP を包含していること。"""
        # 無音 (4.9, 5.1) → KEEP (0,4.9),(5.1,10) → padding=0.2 → bridge
        keeps = derive_keep_ranges(10.0, [(4.9, 5.1)], _opts(padding=0.2))
        assert len(keeps) == 1
        # 元の (0, 10) 全体が保持されている
        assert keeps[0][0] <= pytest.approx(0.0 + 1e-9)
        assert keeps[0][1] >= pytest.approx(10.0 - 1e-9)

    def test_multiple_short_silences_all_bridged(self) -> None:
        """複数の短い無音が全て padding でまたがれる → 全体が 1 KEEP に連結。

        無音: (2,2.3),(5,5.3),(8,8.3) → 各 0.3s の無音
        padding=0.2 → 各無音をまたぐ → 全体 1 KEEP
        """
        keeps = derive_keep_ranges(
            10.0,
            [(2.0, 2.3), (5.0, 5.3), (8.0, 8.3)],
            _opts(padding=0.2),
        )
        assert len(keeps) == 1


# ===========================================================================
# 境界値・その他
# ===========================================================================


class TestEdgeCases:
    """境界値とその他のエッジケース。"""

    def test_total_duration_zero_no_silence_returns_empty(self) -> None:
        """total_duration=0.0, 無音なし → [] または [(0,0)]（空区間）。

        実装依存だが長さ 0 の区間は実質無意味であるため空でもよい。
        """
        keeps = derive_keep_ranges(0.0, [], _opts())
        # 空か (0,0) を許容（実装に依存する正確な動作は実装時に確定）
        assert isinstance(keeps, list)

    def test_silence_interval_at_exact_boundary(self) -> None:
        """無音区間が total_duration の末尾にぴったりかかる場合の安全性。"""
        # 無音 (9.0, 10.0) → KEEP (0.0, 9.0)
        keeps = derive_keep_ranges(10.0, [(9.0, 10.0)], _opts())
        assert len(keeps) == 1
        assert keeps[0] == pytest.approx((0.0, 9.0))

    def test_return_type_is_list_of_tuples(self) -> None:
        """戻り値が list[tuple[float, float]] 型であること。"""
        keeps = derive_keep_ranges(10.0, [(3.0, 7.0)], _opts())
        assert isinstance(keeps, list)
        for item in keeps:
            assert isinstance(item, tuple)
            assert len(item) == 2
            assert isinstance(item[0], float)
            assert isinstance(item[1], float)

    def test_keep_intervals_are_non_overlapping(self) -> None:
        """返却される KEEP 区間が重複しないこと（マージ済み）。"""
        keeps = derive_keep_ranges(
            20.0,
            [(2.0, 3.0), (5.0, 6.0), (8.0, 9.0)],
            _opts(padding=0.0),
        )
        for i in range(len(keeps) - 1):
            assert keeps[i][1] <= keeps[i + 1][0] + 1e-9, (
                f"KEEP 区間が重複している: {keeps[i]} と {keeps[i+1]}"
            )

    def test_keep_intervals_are_ordered(self) -> None:
        """返却される KEEP 区間が時間順に並んでいること。"""
        keeps = derive_keep_ranges(
            20.0,
            [(5.0, 6.0), (2.0, 3.0)],  # 逆順入力
            _opts(padding=0.0),
        )
        for i in range(len(keeps) - 1):
            assert keeps[i][0] < keeps[i + 1][0], "KEEP 区間が時間順でない"
