"""test_captions.py — captions.py 純ロジックの Red テスト（契約面 100% 目標）。

architecture TR-AD-02/06/07 と DC-GP-002/DC-AS-005 の仕様を観点に固定する。
このファイルは captions.py が存在しない段階で import 失敗により
機能未実装として失敗することを意図した Red テスト群。

注意（DC-GP-001-R）:
  契約面100%は spike 仮説 fixture（whisper_sample.json）に対する被覆であり、
  env 未設定で spike が仮説の場合は実スキーマ未検証・e2e 照合まで確定しない。
"""

from __future__ import annotations

from typing import Any

import pytest

from clipwright_transcribe.captions import (
    Segment,
    normalize_segments,
    to_srt,
    to_vtt,
)

# ===========================================================================
# normalize_segments — 基本動作
# ===========================================================================


class TestNormalizeSegmentsBasic:
    """normalize_segments の基本動作（fixture ベース）を検証する。"""

    def test_normalize_returns_list(self, whisper_sample_json: dict[str, Any]) -> None:
        """normalize_segments が list を返すこと。"""
        result = normalize_segments(whisper_sample_json)
        assert isinstance(result, list)

    def test_normalize_returns_correct_count(
        self, whisper_sample_json: dict[str, Any]
    ) -> None:
        """fixture の 3 セグメントが正しく正規化されること。"""
        result = normalize_segments(whisper_sample_json)
        assert len(result) == 3

    def test_first_segment_start_sec(self, whisper_sample_json: dict[str, Any]) -> None:
        """offsets.from=0ms → start_sec=0.0 秒になること（TR-AD-07）。"""
        result = normalize_segments(whisper_sample_json)
        assert result[0]["start_sec"] == pytest.approx(0.0)

    def test_first_segment_end_sec(self, whisper_sample_json: dict[str, Any]) -> None:
        """offsets.to=1200ms → end_sec=1.2 秒になること（TR-AD-07）。"""
        result = normalize_segments(whisper_sample_json)
        assert result[0]["end_sec"] == pytest.approx(1.2)

    def test_second_segment_start_sec(
        self, whisper_sample_json: dict[str, Any]
    ) -> None:
        """offsets.from=1500ms → start_sec=1.5 秒になること。"""
        result = normalize_segments(whisper_sample_json)
        assert result[1]["start_sec"] == pytest.approx(1.5)

    def test_second_segment_end_sec(self, whisper_sample_json: dict[str, Any]) -> None:
        """offsets.to=2800ms → end_sec=2.8 秒になること。"""
        result = normalize_segments(whisper_sample_json)
        assert result[1]["end_sec"] == pytest.approx(2.8)

    def test_third_segment_start_sec(self, whisper_sample_json: dict[str, Any]) -> None:
        """offsets.from=3000ms → start_sec=3.0 秒になること。"""
        result = normalize_segments(whisper_sample_json)
        assert result[2]["start_sec"] == pytest.approx(3.0)

    def test_third_segment_end_sec(self, whisper_sample_json: dict[str, Any]) -> None:
        """offsets.to=4500ms → end_sec=4.5 秒になること。"""
        result = normalize_segments(whisper_sample_json)
        assert result[2]["end_sec"] == pytest.approx(4.5)

    def test_segment_text_stripped(self, whisper_sample_json: dict[str, Any]) -> None:
        """text は前後の空白を strip されること（whisper 出力は先頭に空白が入ることがある）。"""
        result = normalize_segments(whisper_sample_json)
        # fixture の text は " Hello world." → "Hello world."
        assert result[0]["text"] == "Hello world."

    def test_all_segments_have_required_keys(
        self, whisper_sample_json: dict[str, Any]
    ) -> None:
        """全セグメントに start_sec / end_sec / text キーが存在すること。"""
        result = normalize_segments(whisper_sample_json)
        for seg in result:
            assert "start_sec" in seg
            assert "end_sec" in seg
            assert "text" in seg


# ===========================================================================
# normalize_segments — 防御・除去ロジック
# ===========================================================================


class TestNormalizeSegmentsFiltering:
    """不正・退化セグメントの除去を検証する。"""

    def test_empty_text_segment_removed(self) -> None:
        """text が空文字のセグメントは除去されること（DC-GP-002 補完）。"""
        data = {
            "transcription": [
                {"offsets": {"from": 0, "to": 1000}, "text": ""},
                {"offsets": {"from": 1000, "to": 2000}, "text": "Hello"},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "Hello"

    def test_whitespace_only_text_segment_removed(self) -> None:
        """text が空白のみのセグメントは除去されること。"""
        data = {
            "transcription": [
                {"offsets": {"from": 0, "to": 1000}, "text": "   "},
                {"offsets": {"from": 1000, "to": 2000}, "text": " Hello "},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "Hello"

    def test_degenerate_segment_start_equals_end_removed(self) -> None:
        """start_sec == end_sec の退化区間は除去されること。"""
        data = {
            "transcription": [
                {"offsets": {"from": 1000, "to": 1000}, "text": "degenerate"},
                {"offsets": {"from": 1000, "to": 2000}, "text": "valid"},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "valid"

    def test_degenerate_segment_start_greater_than_end_removed(self) -> None:
        """start_sec > end_sec の退化区間は除去されること。"""
        data = {
            "transcription": [
                {"offsets": {"from": 2000, "to": 1000}, "text": "reversed"},
                {"offsets": {"from": 1000, "to": 2000}, "text": "valid"},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "valid"

    def test_missing_offsets_key_segment_removed(self) -> None:
        """offsets キーが欠落したセグメントは除去されること（防御）。"""
        data = {
            "transcription": [
                {"text": "no offsets"},
                {"offsets": {"from": 0, "to": 1000}, "text": "valid"},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "valid"

    def test_missing_from_key_segment_removed(self) -> None:
        """offsets.from キーが欠落したセグメントは除去されること（防御）。"""
        data = {
            "transcription": [
                {"offsets": {"to": 1000}, "text": "no from"},
                {"offsets": {"from": 0, "to": 1000}, "text": "valid"},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "valid"

    def test_missing_to_key_segment_removed(self) -> None:
        """offsets.to キーが欠落したセグメントは除去されること（防御）。"""
        data = {
            "transcription": [
                {"offsets": {"from": 0}, "text": "no to"},
                {"offsets": {"from": 0, "to": 1000}, "text": "valid"},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "valid"

    def test_missing_text_key_segment_removed(self) -> None:
        """text キーが欠落したセグメントは除去されること（防御）。"""
        data = {
            "transcription": [
                {"offsets": {"from": 0, "to": 1000}},
                {"offsets": {"from": 1000, "to": 2000}, "text": "valid"},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "valid"

    def test_non_dict_entry_segment_removed(self) -> None:
        """transcription 要素が dict でない場合は除去されること（防御）。"""
        data = {
            "transcription": [
                "not a dict",
                {"offsets": {"from": 0, "to": 1000}, "text": "valid"},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "valid"

    def test_non_numeric_offsets_segment_removed(self) -> None:
        """offsets.from/to が数値変換できない場合は除去されること（防御）。"""
        data = {
            "transcription": [
                {"offsets": {"from": "abc", "to": 1000}, "text": "bad offset"},
                {"offsets": {"from": 0, "to": 1000}, "text": "valid"},
            ]
        }
        result = normalize_segments(data)
        assert len(result) == 1
        assert result[0]["text"] == "valid"

    def test_non_list_transcription_returns_empty(self) -> None:
        """transcription が list でない場合は空リストを返すこと（防御）。"""
        result = normalize_segments({"transcription": "not a list"})
        assert result == []

    def test_empty_transcription_list_returns_empty(self) -> None:
        """transcription が空リストの場合、空リストを返すこと（DC-GP-002）。"""
        data: dict[str, Any] = {"transcription": []}
        result = normalize_segments(data)
        assert result == []

    def test_missing_transcription_key_returns_empty(self) -> None:
        """transcription キーが欠落している場合、空リストを返すこと（防御）。"""
        result = normalize_segments({})
        assert result == []


# ===========================================================================
# DC-GP-002 — セグメント 0 件
# ===========================================================================


class TestDCGP002ZeroSegments:
    """セグメント 0 件時の各関数の振る舞いを検証する（DC-GP-002）。"""

    def test_normalize_segments_empty_returns_empty_list(self) -> None:
        """normalize_segments の 0 件入力 → 空リストを返すこと。"""
        result = normalize_segments({"transcription": []})
        assert result == []

    def test_to_srt_empty_segments_returns_empty_string(self) -> None:
        """to_srt の 0 件入力 → 空文字列を返すこと。"""
        result = to_srt([])
        assert result == ""

    def test_to_vtt_empty_segments_returns_header_only(self) -> None:
        """to_vtt の 0 件入力 → "WEBVTT" ヘッダのみを返すこと。"""
        result = to_vtt([])
        assert result.strip() == "WEBVTT"


# ===========================================================================
# to_srt — タイムコードと出力フォーマット
# ===========================================================================


class TestToSrt:
    """to_srt のフォーマット・インデックス・タイムコードを検証する。"""

    def _make_segment(self, start_sec: float, end_sec: float, text: str) -> Segment:
        return {"start_sec": start_sec, "end_sec": end_sec, "text": text}

    def test_single_segment_index_starts_at_1(self) -> None:
        """SRT のインデックスが 1 始まりであること。"""
        segments = [self._make_segment(0.0, 1.0, "Hello")]
        srt = to_srt(segments)
        lines = srt.strip().splitlines()
        assert lines[0] == "1"

    def test_single_segment_timecode_format(self) -> None:
        """SRT タイムコードが HH:MM:SS,mmm 形式であること（TR-AD-07）。"""
        segments = [self._make_segment(0.0, 1.5, "Hello")]
        srt = to_srt(segments)
        lines = srt.strip().splitlines()
        # 2行目がタイムコード行: 00:00:00,000 --> 00:00:01,500
        assert "-->" in lines[1]
        start, end = lines[1].split(" --> ")
        # HH:MM:SS,mmm パターン検証
        import re

        pattern = r"^\d{2}:\d{2}:\d{2},\d{3}$"
        assert re.match(pattern, start), f"SRT start timecode format error: {start}"
        assert re.match(pattern, end), f"SRT end timecode format error: {end}"

    def test_timecode_zero_second(self) -> None:
        """start_sec=0.0 → '00:00:00,000' であること（境界値）。"""
        segments = [self._make_segment(0.0, 0.5, "Zero")]
        srt = to_srt(segments)
        assert "00:00:00,000" in srt

    def test_timecode_hour_rollover(self) -> None:
        """60分以上の秒値で時間が繰り上がること（境界値）。"""
        segments = [self._make_segment(3661.0, 3662.5, "Rollover")]
        srt = to_srt(segments)
        # 3661秒 = 1時間1分1秒
        assert "01:01:01,000" in srt

    def test_timecode_milliseconds_precision(self) -> None:
        """ミリ秒部分が正しく整形されること。"""
        segments = [self._make_segment(1.234, 2.567, "Millis")]
        srt = to_srt(segments)
        assert "00:00:01,234" in srt
        assert "00:00:02,567" in srt

    def test_multiple_segments_sequential_index(self) -> None:
        """複数セグメントで連番インデックスが付くこと。"""
        segments = [
            self._make_segment(0.0, 1.0, "First"),
            self._make_segment(1.5, 2.5, "Second"),
            self._make_segment(3.0, 4.0, "Third"),
        ]
        srt = to_srt(segments)
        lines = [line for line in srt.splitlines() if line.strip().isdigit()]
        assert lines == ["1", "2", "3"]

    def test_multiple_segments_blank_line_separator(self) -> None:
        """複数セグメント間に空行区切りがあること。"""
        segments = [
            self._make_segment(0.0, 1.0, "First"),
            self._make_segment(1.5, 2.5, "Second"),
        ]
        srt = to_srt(segments)
        # 空行が存在すること
        assert "\n\n" in srt

    def test_segment_text_in_output(self) -> None:
        """セグメントの text が SRT 出力に含まれること。"""
        segments = [self._make_segment(0.0, 1.0, "Hello world")]
        srt = to_srt(segments)
        assert "Hello world" in srt

    def test_fixture_based_srt_output(
        self, whisper_sample_json: dict[str, Any]
    ) -> None:
        """fixture JSON から normalize_segments → to_srt の一連フローが機能すること。"""
        segments = normalize_segments(whisper_sample_json)
        srt = to_srt(segments)
        assert len(srt) > 0
        assert "1" in srt
        assert "-->" in srt


# ===========================================================================
# to_vtt — タイムコードと出力フォーマット
# ===========================================================================


class TestToVtt:
    """to_vtt のフォーマット・ヘッダ・タイムコードを検証する。"""

    def _make_segment(self, start_sec: float, end_sec: float, text: str) -> Segment:
        return {"start_sec": start_sec, "end_sec": end_sec, "text": text}

    def test_output_starts_with_webvtt_header(self) -> None:
        """VTT 出力が 'WEBVTT' ヘッダで始まること。"""
        segments = [self._make_segment(0.0, 1.0, "Hello")]
        vtt = to_vtt(segments)
        assert vtt.startswith("WEBVTT")

    def test_timecode_format_uses_dot_separator(self) -> None:
        """VTT タイムコードが HH:MM:SS.mmm 形式（ドット区切り）であること（TR-AD-07）。"""
        segments = [self._make_segment(0.0, 1.5, "Hello")]
        vtt = to_vtt(segments)
        import re

        pattern = r"\d{2}:\d{2}:\d{2}\.\d{3}"
        assert re.search(pattern, vtt), f"VTT timecode format error: {vtt}"

    def test_timecode_zero_second(self) -> None:
        """start_sec=0.0 → '00:00:00.000' であること（境界値）。"""
        segments = [self._make_segment(0.0, 0.5, "Zero")]
        vtt = to_vtt(segments)
        assert "00:00:00.000" in vtt

    def test_timecode_hour_rollover(self) -> None:
        """60分以上の秒値で時間が繰り上がること（境界値）。"""
        segments = [self._make_segment(3661.0, 3662.5, "Rollover")]
        vtt = to_vtt(segments)
        assert "01:01:01.000" in vtt

    def test_timecode_milliseconds_precision(self) -> None:
        """ミリ秒部分が正しく整形されること。"""
        segments = [self._make_segment(1.234, 2.567, "Millis")]
        vtt = to_vtt(segments)
        assert "00:00:01.234" in vtt
        assert "00:00:02.567" in vtt

    def test_segment_text_in_output(self) -> None:
        """セグメントの text が VTT 出力に含まれること。"""
        segments = [self._make_segment(0.0, 1.0, "Hello world")]
        vtt = to_vtt(segments)
        assert "Hello world" in vtt

    def test_arrow_separator_present(self) -> None:
        """タイムコード間の --> 区切りが含まれること。"""
        segments = [self._make_segment(0.0, 1.0, "Hello")]
        vtt = to_vtt(segments)
        assert "-->" in vtt

    def test_fixture_based_vtt_output(
        self, whisper_sample_json: dict[str, Any]
    ) -> None:
        """fixture JSON から normalize_segments → to_vtt の一連フローが機能すること。"""
        segments = normalize_segments(whisper_sample_json)
        vtt = to_vtt(segments)
        assert vtt.startswith("WEBVTT")
        assert "-->" in vtt


# ===========================================================================
# DC-AS-005 — SRT と VTT のタイムコード一貫性
# ===========================================================================


class TestTimecodeConsistency:
    """SRT と VTT が同一秒値から導出され一貫することを検証する（DC-AS-005 純ロジック側）。"""

    def _make_segment(self, start_sec: float, end_sec: float, text: str) -> Segment:
        return {"start_sec": start_sec, "end_sec": end_sec, "text": text}

    def test_srt_and_vtt_share_same_second_values(self) -> None:
        """SRT と VTT で秒整数部分（HH:MM:SS）が一致すること。"""
        segments = [self._make_segment(1.234, 2.567, "Consistent")]
        srt = to_srt(segments)
        vtt = to_vtt(segments)
        # SRT: 00:00:01,234 → ミリ秒前半 "00:00:01"
        # VTT: 00:00:01.234 → ミリ秒前半 "00:00:01"
        srt_hms_start = "00:00:01"
        vtt_hms_start = "00:00:01"
        assert srt_hms_start in srt
        assert vtt_hms_start in vtt

    def test_srt_comma_and_vtt_dot_differ_only_in_separator(self) -> None:
        """SRT のコンマ区切りと VTT のドット区切りのみが異なり、値は同一であること。"""
        segments = [self._make_segment(0.0, 1.5, "Test")]
        srt = to_srt(segments)
        vtt = to_vtt(segments)
        # SRT: 00:00:00,000 → 00:00:01,500
        # VTT: 00:00:00.000 --> 00:00:01.500
        assert "00:00:00,000" in srt
        assert "00:00:01,500" in srt
        assert "00:00:00.000" in vtt
        assert "00:00:01.500" in vtt

    def test_fixture_srt_and_vtt_timecodes_consistent(
        self, whisper_sample_json: dict[str, Any]
    ) -> None:
        """fixture 由来のセグメントで SRT/VTT タイムコードが一貫すること。"""
        segments = normalize_segments(whisper_sample_json)
        srt = to_srt(segments)
        vtt = to_vtt(segments)

        # 1セグメント目: start=0.0s → "00:00:00"
        assert "00:00:00,000" in srt
        assert "00:00:00.000" in vtt

        # 1セグメント目: end=1.2s → "00:00:01,200" / "00:00:01.200"
        assert "00:00:01,200" in srt
        assert "00:00:01.200" in vtt

    def test_millisecond_rounding_consistent_between_srt_and_vtt(self) -> None:
        """SRT と VTT でミリ秒の値が一致すること（丸め処理の一貫性）。"""
        # ミリ秒に切り捨てて一致することを確認
        segments = [self._make_segment(1.001, 2.999, "Rounding")]
        srt = to_srt(segments)
        vtt = to_vtt(segments)
        # ミリ秒部分: SRT "001" / VTT "001" が一致すること
        assert "00:00:01,001" in srt
        assert "00:00:01.001" in vtt
        assert "00:00:02,999" in srt
        assert "00:00:02.999" in vtt
