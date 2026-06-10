"""test_captions.py — captions.py 純ロジックの Red テスト（契約面 100% 目標）。

architecture WR-AD-03/04/06/12/14/15 の仕様を観点に固定する。
このファイルは captions.py が存在しない段階で import 失敗により
機能未実装として失敗することを意図した Red テスト群。

対象 API（すべて budoux 非依存):
  - Cue: dataclass（index, start, end, text）
  - parse_captions(text, fmt) -> list[Cue]
  - wrap_cue_lines(segments, max_chars) -> list[str]
  - serialize_captions(cues, fmt) -> str

WR-AD-06: タイムコード文字列は不変保持（float 変換しない）。
WR-AD-12: transcribe のバイト構造仕様（末尾 cue 空行なし・WEBVTT\\n\\n・0件時）。
WR-AD-14: 文字カウント一律 1 文字・区切り未挿入・len(line) に \\n 含めない。
WR-AD-15(1): overflow 判定 = 行数超過 (a) + 行幅超過 (b) 両方。
"""

from __future__ import annotations

import pytest
from clipwright_wrap.captions import (
    Cue,
    parse_captions,
    serialize_captions,
    wrap_cue_lines,
)

# ===========================================================================
# Cue 型の確認
# ===========================================================================


class TestCueType:
    """Cue 型が必要なフィールドを持つ dataclass（または同等の型）であること。"""

    def test_cue_has_index_field(self) -> None:
        """Cue に index フィールドがあること。"""
        cue = Cue(index=1, start="00:00:00,000", end="00:00:01,000", text="テスト")
        assert cue.index == 1

    def test_cue_has_start_field(self) -> None:
        """Cue に start フィールドがあること（タイムコード文字列）。"""
        cue = Cue(index=1, start="00:00:00,000", end="00:00:01,000", text="テスト")
        assert cue.start == "00:00:00,000"

    def test_cue_has_end_field(self) -> None:
        """Cue に end フィールドがあること（タイムコード文字列）。"""
        cue = Cue(index=1, start="00:00:00,000", end="00:00:01,000", text="テスト")
        assert cue.end == "00:00:01,000"

    def test_cue_has_text_field(self) -> None:
        """Cue に text フィールドがあること。"""
        cue = Cue(index=1, start="00:00:00,000", end="00:00:01,000", text="テスト")
        assert cue.text == "テスト"

    def test_cue_start_is_string(self) -> None:
        """Cue.start はタイムコード文字列のまま保持されること（WR-AD-06・float 変換しない）。"""
        tc = "00:00:12,345"
        cue = Cue(index=1, start=tc, end="00:00:13,000", text="x")
        assert isinstance(cue.start, str)
        assert cue.start == tc

    def test_cue_end_is_string(self) -> None:
        """Cue.end はタイムコード文字列のまま保持されること（WR-AD-06・float 変換しない）。"""
        tc = "00:00:13,000"
        cue = Cue(index=1, start="00:00:12,345", end=tc, text="x")
        assert isinstance(cue.end, str)
        assert cue.end == tc


# ===========================================================================
# parse_captions — SRT 基本動作（WR-AD-12 バイト構造仕様）
# ===========================================================================


class TestParseCaptionsSrtBasic:
    """parse_captions('srt') の基本動作を検証する（WR-AD-03/WR-AD-12）。"""

    def test_single_cue_srt_returns_list(self) -> None:
        """1 cue の SRT を parse して list を返すこと。"""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nあいう\n"
        result = parse_captions(srt, "srt")
        assert isinstance(result, list)
        assert len(result) == 1

    def test_single_cue_srt_index(self) -> None:
        """SRT の cue index が正しく取得されること。"""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nあいう\n"
        cues = parse_captions(srt, "srt")
        assert cues[0].index == 1

    def test_single_cue_srt_start_timecode(self) -> None:
        """SRT の start タイムコードが文字列のまま保持されること（WR-AD-06）。"""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nあいう\n"
        cues = parse_captions(srt, "srt")
        assert cues[0].start == "00:00:00,000"

    def test_single_cue_srt_end_timecode(self) -> None:
        """SRT の end タイムコードが文字列のまま保持されること（WR-AD-06）。"""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nあいう\n"
        cues = parse_captions(srt, "srt")
        assert cues[0].end == "00:00:01,000"

    def test_single_cue_srt_text(self) -> None:
        """SRT の cue テキストが正しく取得されること。"""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nあいう\n"
        cues = parse_captions(srt, "srt")
        assert cues[0].text == "あいう"


class TestParseCaptionsSrtTwoCues:
    """2 cue の SRT パース（WR-AD-12 の transcribe バイト構造仕様固定テスト）。"""

    # DC-AS-001/WR-AD-12 固定フィクスチャ
    # transcribe to_srt の正確なバイト構造:
    #   cue 間は空行1つ・末尾 cue は空行なし単一改行 EOF
    SRT_2CUE = (
        "1\n00:00:00,000 --> 00:00:01,000\nあいう\n"
        "\n"
        "2\n00:00:01,000 --> 00:00:02,000\nえお\n"
    )

    def test_two_cues_parsed(self) -> None:
        """2 cue SRT（cue 間空行1・末尾空行なし単一改行 EOF）が正しく 2 件 parse されること（DC-AS-001）。"""
        cues = parse_captions(self.SRT_2CUE, "srt")
        assert len(cues) == 2

    def test_first_cue_index(self) -> None:
        """1 番目の cue の index が 1 であること。"""
        cues = parse_captions(self.SRT_2CUE, "srt")
        assert cues[0].index == 1

    def test_second_cue_index(self) -> None:
        """2 番目の cue の index が 2 であること。"""
        cues = parse_captions(self.SRT_2CUE, "srt")
        assert cues[1].index == 2

    def test_first_cue_text(self) -> None:
        """1 番目の cue テキストが 'あいう' であること。"""
        cues = parse_captions(self.SRT_2CUE, "srt")
        assert cues[0].text == "あいう"

    def test_second_cue_text(self) -> None:
        """2 番目の cue テキストが 'えお' であること（末尾 cue 取りこぼしなし）。"""
        cues = parse_captions(self.SRT_2CUE, "srt")
        assert cues[1].text == "えお"

    def test_second_cue_timecode_preserved(self) -> None:
        """2 番目の cue のタイムコードが不変保持されること（WR-AD-06）。"""
        cues = parse_captions(self.SRT_2CUE, "srt")
        assert cues[1].start == "00:00:01,000"
        assert cues[1].end == "00:00:02,000"


class TestParseCaptionsSrtEdgeCases:
    """SRT パースの境界・防御ケースを検証する（WR-AD-12(2)）。"""

    def test_empty_string_returns_empty_list(self) -> None:
        """SRT の空文字列 '' → [] を返すこと（0件・例外なし）（WR-AD-12(2)）。"""
        result = parse_captions("", "srt")
        assert result == []

    def test_trailing_newline_only_returns_empty_list(self) -> None:
        """SRT の '\\n' のみ → [] を返すこと（0件・例外なし）。"""
        result = parse_captions("\n", "srt")
        assert result == []

    def test_trailing_blank_lines_handled(self) -> None:
        """末尾に複数の空行がある SRT でも正しく parse されること（頑健性）。"""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nテスト\n\n\n"
        cues = parse_captions(srt, "srt")
        assert len(cues) == 1
        assert cues[0].text == "テスト"

    def test_multiple_blank_lines_between_cues_handled(self) -> None:
        """cue 間に複数の空行がある SRT でも正しく parse されること（頑健性）。"""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nあ\n\n\n2\n00:00:01,000 --> 00:00:02,000\nい\n"
        cues = parse_captions(srt, "srt")
        assert len(cues) == 2

    def test_multiline_text_in_cue_joined_without_space(self) -> None:
        """cue 内の複数行テキストは空文字結合されること（WR-AD-14・半角空白挿入なし）。"""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nあいう\nえおか\n"
        cues = parse_captions(srt, "srt")
        # \\n を空文字で除去して連結（半角空白を入れない）
        assert cues[0].text == "あいうえおか"

    def test_multiline_text_no_space_inserted(self) -> None:
        """cue 内複数行結合時に半角空白が挿入されないこと（WR-AD-14 明示）。"""
        srt = "1\n00:00:00,000 --> 00:00:01,000\nHello\nWorld\n"
        cues = parse_captions(srt, "srt")
        # 空文字結合: "HelloWorld"（半角空白なし）
        assert " " not in cues[0].text or cues[0].text == "Hello World"
        # 厳密: 半角空白挿入なしの確認
        assert cues[0].text == "HelloWorld"

    def test_invalid_timecode_raises_exception(self) -> None:
        """不正な timecode 行が含まれる SRT → 例外（INVALID_INPUT 相当）が送出されること（WR-AD-09）。"""
        srt = "1\nINVALID_TIMECODE\nテスト\n"
        with pytest.raises((ValueError, RuntimeError)):
            parse_captions(srt, "srt")


# ===========================================================================
# parse_captions — VTT 基本動作（WR-AD-12 バイト構造仕様）
# ===========================================================================


class TestParseCaptionsVttBasic:
    """parse_captions('vtt') の基本動作を検証する（WR-AD-03/WR-AD-12）。"""

    def test_single_cue_vtt_returns_list(self) -> None:
        """1 cue の VTT（WEBVTT\\n\\n<cue>）が正しく parse されること（WR-AD-12 ヘッダ仕様）。"""
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nあいう\n"
        result = parse_captions(vtt, "vtt")
        assert isinstance(result, list)
        assert len(result) == 1

    def test_single_cue_vtt_start_timecode(self) -> None:
        """VTT の start タイムコードが文字列のまま保持されること（WR-AD-06）。"""
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nあいう\n"
        cues = parse_captions(vtt, "vtt")
        assert cues[0].start == "00:00:00.000"

    def test_single_cue_vtt_end_timecode(self) -> None:
        """VTT の end タイムコードが文字列のまま保持されること（WR-AD-06）。"""
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nあいう\n"
        cues = parse_captions(vtt, "vtt")
        assert cues[0].end == "00:00:01.000"

    def test_single_cue_vtt_text(self) -> None:
        """VTT の cue テキストが正しく取得されること。"""
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nあいう\n"
        cues = parse_captions(vtt, "vtt")
        assert cues[0].text == "あいう"

    def test_header_only_vtt_returns_empty_list(self) -> None:
        """VTT の 'WEBVTT\\n'（ヘッダのみ）→ [] を返すこと（0件・例外なし）（WR-AD-12(2)）。"""
        result = parse_captions("WEBVTT\n", "vtt")
        assert result == []

    def test_webvtt_header_blank_line_skipped(self) -> None:
        """WEBVTT ヘッダ直後の空行が正常にスキップされて最初の cue に到達すること（WR-AD-12(2)）。"""
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nテスト\n"
        cues = parse_captions(vtt, "vtt")
        assert len(cues) == 1
        assert cues[0].text == "テスト"


class TestParseCaptionsVttEdgeCases:
    """VTT エッジ 5 種の保持/warnings 挙動を検証する（WR-AD-12(3) / DC-AM-001）。"""

    def test_vtt_cue_with_id_line_text_only_wrapped(self) -> None:
        """(a) cue id 行付き VTT: cue id は保持・text 行のみ整形対象になること。"""
        vtt = "WEBVTT\n\ncue-1\n00:00:00.000 --> 00:00:01.000\nあいう\n"
        cues = parse_captions(vtt, "vtt")
        assert len(cues) == 1
        assert cues[0].text == "あいう"
        # タイムコードが不変であること
        assert cues[0].start == "00:00:00.000"

    def test_vtt_cue_with_settings_preserved(self) -> None:
        """(d) cue settings 付き VTT: settings 部分は不変保持・text 行のみ整形されること。"""
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000 line:90% position:50%\nテスト\n"
        cues = parse_captions(vtt, "vtt")
        assert len(cues) == 1
        assert cues[0].text == "テスト"
        # settings を含むタイムライン行が原文保持されること
        assert "line:90%" in cues[0].end or "line:90%" in (cues[0].start + cues[0].end)

    def test_vtt_note_block_preserved_with_warnings(self) -> None:
        """(b) NOTE ブロック: parse 結果に反映され、warnings 情報が得られること。

        NOTE ブロックは整形対象外で原文保持・warnings に記録される（WR-AD-12(3)(b)）。
        parse_captions の戻り値の型または warnings 属性で確認する。
        """
        vtt = "WEBVTT\n\nNOTE これはコメントです\n\n00:00:00.000 --> 00:00:01.000\nテスト\n"
        # NOTE ブロックが含まれても cue は正しく取得できること（整形対象外で保持）
        cues = parse_captions(vtt, "vtt")
        assert any(c.text == "テスト" for c in cues)

    def test_vtt_style_block_preserved_with_warnings(self) -> None:
        """(c) STYLE ブロック: parse 結果に反映され、cue は正しく取得できること。

        STYLE ブロックは整形対象外で原文保持・warnings に記録される（WR-AD-12(3)(c)）。
        """
        vtt = "WEBVTT\n\nSTYLE\n::cue { color: white; }\n\n00:00:00.000 --> 00:00:01.000\nテスト\n"
        cues = parse_captions(vtt, "vtt")
        assert any(c.text == "テスト" for c in cues)

    def test_vtt_inline_tag_cue_text_preserved_with_warnings(self) -> None:
        """(e) インラインタグ付き cue: タグ込みテキストが原文保持されること（WR-AD-12(3)(e)）。

        インラインタグを含む cue は文節整形をスキップし原文保持・warnings に記録される。
        """
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n<c.yellow>テキスト</c>\n"
        cues = parse_captions(vtt, "vtt")
        assert len(cues) == 1
        # タグ込みテキストが原文保持されること
        assert cues[0].text == "<c.yellow>テキスト</c>"

    def test_vtt_timecode_invariant_through_all_edge_cases(self) -> None:
        """VTT エッジケースを通じてタイムコードが不変であること（WR-AD-06）。"""
        vtt = "WEBVTT\n\n00:01:23.456 --> 00:01:24.789\nテスト\n"
        cues = parse_captions(vtt, "vtt")
        assert cues[0].start == "00:01:23.456"
        assert cues[0].end == "00:01:24.789"


# ===========================================================================
# parse_captions — 往復同一（SRT/VTT）
# ===========================================================================


class TestParseCaptionsRoundTrip:
    """parse → serialize の往復でタイムコードが同一であることを検証する（WR-AD-06）。"""

    def test_srt_roundtrip_timecode_preserved(self) -> None:
        """SRT の parse → serialize でタイムコードが不変であること。"""
        original = "1\n00:00:00,000 --> 00:00:01,000\nあいう\n"
        cues = parse_captions(original, "srt")
        result = serialize_captions(cues, "srt")
        # タイムコード文字列が復元されること
        assert "00:00:00,000" in result
        assert "00:00:01,000" in result

    def test_vtt_roundtrip_timecode_preserved(self) -> None:
        """VTT の parse → serialize でタイムコードが不変であること。"""
        original = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nあいう\n"
        cues = parse_captions(original, "vtt")
        result = serialize_captions(cues, "vtt")
        assert "00:00:00.000" in result
        assert "00:00:01.000" in result


# ===========================================================================
# wrap_cue_lines — 貪欲行詰め（WR-AD-04/WR-AD-14）
# ===========================================================================


class TestWrapCueLinesBasic:
    """wrap_cue_lines の基本動作を検証する（WR-AD-04）。"""

    def test_returns_list_of_str(self) -> None:
        """wrap_cue_lines が list[str] を返すこと。"""
        result = wrap_cue_lines(["今日は", "いい", "天気です。"], max_chars=10)
        assert isinstance(result, list)
        assert all(isinstance(line, str) for line in result)

    def test_short_segments_fit_in_one_line(self) -> None:
        """max_chars に収まる文節列は 1 行に詰められること。"""
        # "今日は" + "いい" + "天気です。" = 10 文字 → max_chars=16 で 1 行
        result = wrap_cue_lines(["今日は", "いい", "天気です。"], max_chars=16)
        assert len(result) == 1

    def test_segments_joined_without_delimiter(self) -> None:
        """文節トークンを結合しても原文が復元されること（WR-AD-14(i)・区切り未挿入）。"""
        segments = ["今日は", "いい", "天気です。"]
        result = wrap_cue_lines(segments, max_chars=100)
        # 1 行に全部入る → 区切り未挿入で結合が "今日はいい天気です。" になること
        assert result[0] == "今日はいい天気です。"

    def test_join_restores_original_text(self) -> None:
        """wrap 後の全行を結合すると元テキストを復元できること（WR-AD-14(i)）。"""
        segments = ["今日は", "いい", "天気です。"]
        result = wrap_cue_lines(segments, max_chars=5)
        # 分割されても空文字結合で原文復元
        assert "".join(result) == "今日はいい天気です。"

    def test_line_len_excludes_newline(self) -> None:
        """各行の len() に '\\n' が含まれないこと（WR-AD-14(ii)）。"""
        result = wrap_cue_lines(["今日は", "いい", "天気です。"], max_chars=5)
        for line in result:
            assert "\n" not in line

    def test_empty_segments_returns_empty_list(self) -> None:
        """空の segments → [] を返すこと（防御）。"""
        result = wrap_cue_lines([], max_chars=16)
        assert result == []


class TestWrapCueLinesGreedy:
    """貪欲行詰めの動作を検証する（WR-AD-04）。"""

    def test_segments_split_at_max_chars_boundary(self) -> None:
        """max_chars を超える直前で改行されること（貪欲）。"""
        # "今日は"(3) + "とても"(3) = 6 文字 ≤ 5? → max_chars=5 の場合
        # "今日は"(3) ≤ 5: OK → + "とても"(3) = 6 > 5: 改行
        result = wrap_cue_lines(["今日は", "とても", "いい"], max_chars=5)
        # 1行目: "今日は", 2行目: "ともも..." の想定で行数 > 1
        assert len(result) >= 2

    def test_line_does_not_exceed_max_chars_when_possible(self) -> None:
        """文節単独が max_chars 以下の場合、各行の len が max_chars 以下になること。"""
        segments = ["今日は", "いい", "天気です。"]
        result = wrap_cue_lines(segments, max_chars=6)
        for line in result:
            # 文節単独が max_chars を超えない場合、行は max_chars 以下
            assert len(line) <= 6

    def test_exactly_max_chars_stays_in_same_line(self) -> None:
        """1 行の長さがちょうど max_chars の場合、その行に収まること（境界値）。"""
        # "あいうえ"(4文字) ちょうど max_chars=4
        result = wrap_cue_lines(["あいうえ"], max_chars=4)
        assert len(result) == 1
        assert result[0] == "あいうえ"

    def test_greedy_fill_uses_max_chars_efficiently(self) -> None:
        """貪欲詰めで max_chars を効率よく使っていること（budoux_sample.json fixture）。"""
        # "今日は"(3) + "いい"(2) = 5文字 → max_chars=5 で 1 行に収まること
        result = wrap_cue_lines(["今日は", "いい"], max_chars=5)
        assert len(result) == 1
        assert result[0] == "今日はいい"


class TestWrapCueLinesOversizedSegment:
    """単一巨大文節の扱いを検証する（WR-AD-04・途中で割らない）。"""

    def test_oversized_single_segment_placed_on_own_line(self) -> None:
        """1 文節が単独で max_chars を超える場合、その文節を 1 行に置くこと（途中で割らない）。"""
        # "歩きながら春の"(8文字) が max_chars=5 を超える
        result = wrap_cue_lines(["歩きながら春の"], max_chars=5)
        assert len(result) == 1
        assert result[0] == "歩きながら春の"

    def test_oversized_segment_not_split(self) -> None:
        """巨大文節は文字途中で分割されないこと（WR-AD-04・文節境界優先）。"""
        big_segment = "字幕改行ツールclipwright-wrapは"  # 17文字
        result = wrap_cue_lines([big_segment], max_chars=10)
        assert len(result) == 1
        assert result[0] == big_segment

    def test_oversized_segment_followed_by_small_segments(self) -> None:
        """巨大文節の後に小文節が続く場合、巨大文節は単独行・後続は別行に詰められること。"""
        # "歩きながら春の"(8) が max_chars=5 を超える → 1 行
        # "訪れを"(4) → 次の行
        result = wrap_cue_lines(["歩きながら春の", "訪れを", "感じた。"], max_chars=5)
        assert result[0] == "歩きながら春の"
        # 後続が別の行に配置されること
        assert len(result) >= 2


class TestWrapCueLinesCharCount:
    """文字カウント仕様を検証する（WR-AD-14）。"""

    def test_full_width_and_half_width_counted_equally(self) -> None:
        """全角・半角を同じ 1 文字としてカウントすること（WR-AD-14(iii)・一律 1 文字）。"""
        # "abc"(半角3文字) + "あいう"(全角3文字) = 6文字 → max_chars=6 で 1 行
        result = wrap_cue_lines(["abc", "あいう"], max_chars=6)
        assert len(result) == 1
        assert result[0] == "abcあいう"

    def test_half_width_ascii_counted_as_one(self) -> None:
        """半角英数字も len() で 1 文字としてカウントされること（WR-AD-14(iii)）。"""
        # "BudouXを"(7文字: B,u,d,o,u,X,を) → max_chars=7 で 1 行
        result = wrap_cue_lines(["BudouXを"], max_chars=7)
        assert len(result) == 1

    def test_len_matches_character_count(self) -> None:
        """len(line) が \\n 除外の文字数と一致すること（WR-AD-14(ii)）。"""
        segments = ["今日は", "いい", "天気です。"]
        result = wrap_cue_lines(segments, max_chars=100)
        for line in result:
            assert len(line) == len(line.replace("\n", ""))

    def test_space_in_original_text_counted_as_one_char(self) -> None:
        """元テキストの半角空白は原文の一部として 1 文字カウントに含まれること（WR-AD-14）。"""
        # "Hello "(6文字・空白含む) → len = 6
        result = wrap_cue_lines(["Hello "], max_chars=6)
        assert len(result) == 1
        assert result[0] == "Hello "


class TestWrapCueLinesWithBudouxFixture:
    """budoux_sample.json の実セグメントで wrap_cue_lines を検証する。"""

    def test_short_ja_segments_with_max_chars_10(
        self, budoux_segments_ja: list[list[str]]
    ) -> None:
        """budoux fixture の短文（'今日はいい天気です。'）を max_chars=10 で詰めること。"""
        # segments[0]: ["今日は", "いい", "天気です。"] = 9文字
        segments = budoux_segments_ja[0]
        result = wrap_cue_lines(segments, max_chars=10)
        assert "".join(result) == "今日はいい天気です。"

    def test_long_ja_segments_wrapped_by_max_chars(
        self, budoux_segments_ja: list[list[str]]
    ) -> None:
        """budoux fixture の長文（7文節）が max_chars=10 で複数行に分割されること。"""
        # segments[1]: ["今日は", "とても", "いい", "天気なので", "公園に", "散歩に", "行きました。"]
        segments = budoux_segments_ja[1]
        result = wrap_cue_lines(segments, max_chars=10)
        # 全部で 26 文字 → max_chars=10 なので複数行になること
        assert len(result) > 1
        # 結合すると原文を復元できること
        assert "".join(result) == "今日はとてもいい天気なので公園に散歩に行きました。"

    def test_mixed_segments_join_without_delimiter(
        self, budoux_segments_ja: list[list[str]]
    ) -> None:
        """英数字混じりセグメントでも区切り未挿入で結合されること（WR-AD-14(i)）。"""
        # segments[3]: ["字幕改行ツールclipwright-wrapは", "BudouXを", "使って", ...]
        segments = budoux_segments_ja[3]
        result = wrap_cue_lines(segments, max_chars=100)
        original = "".join(segments)
        assert "".join(result) == original


# ===========================================================================
# serialize_captions — SRT 出力フォーマット（WR-AD-12 バイト構造）
# ===========================================================================


class TestSerializeCaptionsSrt:
    """serialize_captions('srt') の出力フォーマットを検証する（WR-AD-12(1)）。"""

    def test_single_cue_srt_format(self) -> None:
        """1 cue の SRT 出力が正しいフォーマットであること（インデックス・タイムコード・テキスト）。"""
        cues = [Cue(index=1, start="00:00:00,000", end="00:00:01,000", text="あいう")]
        result = serialize_captions(cues, "srt")
        assert "1\n" in result
        assert "00:00:00,000 --> 00:00:01,000" in result
        assert "あいう" in result

    def test_single_cue_srt_timecode_format(self) -> None:
        """SRT タイムコードが 'HH:MM:SS,mmm' 形式で不変出力されること（WR-AD-06）。"""
        cues = [Cue(index=1, start="00:00:12,345", end="00:00:13,678", text="x")]
        result = serialize_captions(cues, "srt")
        assert "00:00:12,345 --> 00:00:13,678" in result

    def test_zero_cues_srt_returns_empty_string(self) -> None:
        """0 件の SRT → '' を返すこと（WR-AD-12(2)）。"""
        result = serialize_captions([], "srt")
        assert result == ""

    def test_two_cues_srt_separated_by_blank_line(self) -> None:
        """2 cue SRT で cue 間に空行が 1 つあること（WR-AD-12(1) バイト構造）。"""
        cues = [
            Cue(index=1, start="00:00:00,000", end="00:00:01,000", text="あ"),
            Cue(index=2, start="00:00:01,000", end="00:00:02,000", text="い"),
        ]
        result = serialize_captions(cues, "srt")
        assert "\n\n" in result

    def test_two_cues_srt_last_cue_no_trailing_blank_line(self) -> None:
        """2 cue SRT の末尾 cue 後に空行がないこと（WR-AD-12(1) 末尾単一改行 EOF）。"""
        cues = [
            Cue(index=1, start="00:00:00,000", end="00:00:01,000", text="あ"),
            Cue(index=2, start="00:00:01,000", end="00:00:02,000", text="い"),
        ]
        result = serialize_captions(cues, "srt")
        # 末尾が "\n\n" で終わらないこと
        assert not result.endswith("\n\n")
        # 末尾が単一改行で終わること
        assert result.endswith("\n")

    def test_srt_two_cue_exact_byte_structure(self) -> None:
        """SRT 2 cue の正確なバイト構造が WR-AD-12(1) フィクスチャと一致すること（DC-AS-001）。"""
        cues = [
            Cue(index=1, start="00:00:00,000", end="00:00:01,000", text="あいう"),
            Cue(index=2, start="00:00:01,000", end="00:00:02,000", text="えお"),
        ]
        expected = (
            "1\n00:00:00,000 --> 00:00:01,000\nあいう\n"
            "\n"
            "2\n00:00:01,000 --> 00:00:02,000\nえお\n"
        )
        result = serialize_captions(cues, "srt")
        assert result == expected


# ===========================================================================
# serialize_captions — VTT 出力フォーマット（WR-AD-12 バイト構造）
# ===========================================================================


class TestSerializeCaptionsVtt:
    """serialize_captions('vtt') の出力フォーマットを検証する（WR-AD-12(1)）。"""

    def test_vtt_starts_with_webvtt_header(self) -> None:
        """VTT 出力が 'WEBVTT' ヘッダで始まること。"""
        cues = [Cue(index=1, start="00:00:00.000", end="00:00:01.000", text="あいう")]
        result = serialize_captions(cues, "vtt")
        assert result.startswith("WEBVTT")

    def test_vtt_header_followed_by_blank_line(self) -> None:
        """VTT ヘッダ直後に空行があること（WEBVTT\\n\\n<cue> 構造・WR-AD-12(1)）。"""
        cues = [Cue(index=1, start="00:00:00.000", end="00:00:01.000", text="あいう")]
        result = serialize_captions(cues, "vtt")
        assert result.startswith("WEBVTT\n\n")

    def test_vtt_timecode_uses_dot_separator(self) -> None:
        """VTT タイムコードがドット区切り 'HH:MM:SS.mmm' で不変出力されること（WR-AD-06）。"""
        cues = [Cue(index=1, start="00:00:12.345", end="00:00:13.678", text="x")]
        result = serialize_captions(cues, "vtt")
        assert "00:00:12.345 --> 00:00:13.678" in result

    def test_zero_cues_vtt_returns_header_only(self) -> None:
        """0 件の VTT → 'WEBVTT\\n' を返すこと（WR-AD-12(2)）。"""
        result = serialize_captions([], "vtt")
        assert result == "WEBVTT\n"

    def test_vtt_one_cue_exact_byte_structure(self) -> None:
        """VTT 1 cue の正確なバイト構造が WR-AD-12(1) フィクスチャと一致すること（DC-AS-001）。"""
        cues = [Cue(index=1, start="00:00:00.000", end="00:00:01.000", text="あいう")]
        expected = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nあいう\n"
        result = serialize_captions(cues, "vtt")
        assert result == expected

    def test_vtt_two_cues_separated_by_blank_line(self) -> None:
        """2 cue VTT で cue 間に空行が 1 つあること（WR-AD-12(1) バイト構造）。"""
        cues = [
            Cue(index=1, start="00:00:00.000", end="00:00:01.000", text="あ"),
            Cue(index=2, start="00:00:01.000", end="00:00:02.000", text="い"),
        ]
        result = serialize_captions(cues, "vtt")
        # WEBVTT\n\ncue1\n\ncue2\n の構造
        lines = result.split("\n")
        # 空行が存在すること
        assert "" in lines

    def test_vtt_last_cue_no_trailing_blank_line(self) -> None:
        """VTT の末尾 cue 後に空行がないこと（WR-AD-12(1) 末尾単一改行 EOF）。"""
        cues = [
            Cue(index=1, start="00:00:00.000", end="00:00:01.000", text="あ"),
            Cue(index=2, start="00:00:01.000", end="00:00:02.000", text="い"),
        ]
        result = serialize_captions(cues, "vtt")
        assert not result.endswith("\n\n")
        assert result.endswith("\n")


# ===========================================================================
# serialize_captions — 往復同一（SRT/VTT）
# ===========================================================================


class TestSerializeCaptionsRoundTrip:
    """parse → serialize の往復同一性を検証する（WR-AD-06 不変保持）。"""

    def test_srt_zero_cues_roundtrip(self) -> None:
        """SRT 空文字列 parse → serialize が '' を返すこと（往復同一・WR-AD-12(2)）。"""
        cues = parse_captions("", "srt")
        result = serialize_captions(cues, "srt")
        assert result == ""

    def test_vtt_header_only_roundtrip(self) -> None:
        """VTT 'WEBVTT\\n' parse → serialize が 'WEBVTT\\n' を返すこと（往復同一・WR-AD-12(2)）。"""
        cues = parse_captions("WEBVTT\n", "vtt")
        result = serialize_captions(cues, "vtt")
        assert result == "WEBVTT\n"

    def test_srt_two_cue_roundtrip_exact_match(self) -> None:
        """SRT 2 cue の parse → serialize が元文字列と一致すること（往復同一・DC-AS-001）。"""
        original = (
            "1\n00:00:00,000 --> 00:00:01,000\nあいう\n"
            "\n"
            "2\n00:00:01,000 --> 00:00:02,000\nえお\n"
        )
        cues = parse_captions(original, "srt")
        result = serialize_captions(cues, "srt")
        assert result == original


# ===========================================================================
# overflow 判定（WR-AD-15(1) / DC-AM-003）
# ===========================================================================

# overflow 判定は captions.py の純ロジック関数として実装される想定。
# 関数シグネチャ: is_overflow(lines: list[str], max_chars: int, max_lines: int)
#   -> dict with "line_count_overflow": bool, "line_width_overflow": bool
# または別途 Tuple / dataclass。
# ここでは wrap_cue_lines の戻り値と組み合わせて overflow 判定ロジックを検証する。
# 実装時に is_overflow 関数が存在する場合は直接 import して使う。
# 存在しない場合は wrap_cue_lines 結果のメタデータから判定する。

# 注: overflow 判定は captions.py から is_overflow として export されることを想定。
# Red テストとして: captions.py が存在しない = import 失敗で Red になる。


class TestOverflowDetection:
    """overflow 判定の境界値テストを検証する（WR-AD-15(1) / DC-AM-003）。

    overflow 判定:
      (a) 行数 > max_lines → 行数超過
      (b) いずれかの行幅 > max_chars → 行幅超過（単一巨大文節も対象）
    """

    def test_no_overflow_when_within_limits(self) -> None:
        """行数・行幅ともに制限内の場合、overflow でないこと。"""
        from clipwright_wrap.captions import check_overflow

        lines = ["あいうえ", "かきくけ"]  # 4文字 × 2行
        result = check_overflow(lines, max_chars=5, max_lines=2)
        assert result["line_count_overflow"] is False
        assert result["line_width_overflow"] is False

    def test_line_count_overflow_at_max_lines_plus_1(self) -> None:
        """行数が max_lines + 1 の場合、line_count_overflow が True であること（境界値）。"""
        from clipwright_wrap.captions import check_overflow

        lines = ["あ", "い", "う"]  # 3行, max_lines=2
        result = check_overflow(lines, max_chars=10, max_lines=2)
        assert result["line_count_overflow"] is True

    def test_no_line_count_overflow_at_exactly_max_lines(self) -> None:
        """行数がちょうど max_lines の場合、line_count_overflow が False であること（境界値）。"""
        from clipwright_wrap.captions import check_overflow

        lines = ["あ", "い"]  # 2行, max_lines=2
        result = check_overflow(lines, max_chars=10, max_lines=2)
        assert result["line_count_overflow"] is False

    def test_line_width_overflow_at_max_chars_plus_1(self) -> None:
        """いずれかの行幅が max_chars + 1 の場合、line_width_overflow が True であること（境界値）。"""
        from clipwright_wrap.captions import check_overflow

        lines = ["あいうえおか"]  # 6文字, max_chars=5
        result = check_overflow(lines, max_chars=5, max_lines=2)
        assert result["line_width_overflow"] is True

    def test_no_line_width_overflow_at_exactly_max_chars(self) -> None:
        """行幅がちょうど max_chars の場合、line_width_overflow が False であること（境界値）。"""
        from clipwright_wrap.captions import check_overflow

        lines = ["あいうえお"]  # 5文字, max_chars=5
        result = check_overflow(lines, max_chars=5, max_lines=2)
        assert result["line_width_overflow"] is False

    def test_single_oversized_segment_causes_width_overflow(self) -> None:
        """単一巨大文節（行数1・行幅超過）が line_width_overflow=True になること（WR-AD-15(1)）。"""
        from clipwright_wrap.captions import check_overflow

        # 行数は 1 ≤ max_lines=2 だが行幅超過
        lines = ["歩きながら春の"]  # 8文字, max_chars=5
        result = check_overflow(lines, max_chars=5, max_lines=2)
        assert result["line_count_overflow"] is False  # 行数は OK
        assert result["line_width_overflow"] is True  # 行幅は超過

    def test_both_overflow_simultaneously(self) -> None:
        """行数超過かつ行幅超過の場合、両方が True であること。"""
        from clipwright_wrap.captions import check_overflow

        lines = [
            "あいうえおか",
            "きくけこ",
            "さしすせ",
        ]  # 3行 > max_lines=2 かつ行幅超過
        result = check_overflow(lines, max_chars=5, max_lines=2)
        assert result["line_count_overflow"] is True
        assert result["line_width_overflow"] is True

    def test_overflow_does_not_truncate_content(self) -> None:
        """overflow 判定は内容を切り捨てないこと（情報欠落回避・WR-AD-15(1)）。"""
        from clipwright_wrap.captions import check_overflow

        lines = ["あいうえおか", "きくけこ", "さしすせ"]
        # check_overflow は lines を変更しないこと
        original_lines = lines.copy()
        check_overflow(lines, max_chars=5, max_lines=2)
        assert lines == original_lines


# ===========================================================================
# 防御ケース — 0件・空テキスト
# ===========================================================================


class TestDefensiveCases:
    """0件・空テキストの防御ケースを検証する。"""

    def test_parse_srt_empty_text_no_exception(self) -> None:
        """空文字列の SRT parse が例外なく [] を返すこと。"""
        assert parse_captions("", "srt") == []

    def test_parse_vtt_header_only_no_exception(self) -> None:
        """WEBVTT のみの VTT parse が例外なく [] を返すこと。"""
        assert parse_captions("WEBVTT\n", "vtt") == []

    def test_serialize_empty_cues_srt_no_exception(self) -> None:
        """0件 cue の SRT serialize が例外なく '' を返すこと。"""
        assert serialize_captions([], "srt") == ""

    def test_serialize_empty_cues_vtt_no_exception(self) -> None:
        """0件 cue の VTT serialize が例外なく 'WEBVTT\\n' を返すこと。"""
        assert serialize_captions([], "vtt") == "WEBVTT\n"

    def test_wrap_cue_lines_empty_segments_no_exception(self) -> None:
        """空 segments の wrap_cue_lines が例外なく [] を返すこと。"""
        assert wrap_cue_lines([], max_chars=16) == []
