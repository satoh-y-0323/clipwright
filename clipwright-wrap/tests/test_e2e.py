"""test_e2e.py — clipwright-wrap e2e テスト。

実 budoux（通常依存・env ゲートなし常時実行）を使い、日本語 SRT/VTT の
文節改行整形を end-to-end で検証する。

テスト分類:
- e2e_1_srt / e2e_1_vtt : 実 budoux 文節改行（成功条件 1/2）
- e2e_2_transcribe      : transcribe→wrap 連携（DC-AM-004 正本）
- e2e_zero_srt / e2e_zero_vtt : 0 件 e2e（DC-GP-004）
- e2e_overflow          : 超過 warnings（WR-AD-15/DC-AM-003）

Windows 環境（cp932 ターミナル）では PYTHONIOENCODING=utf-8 を設定しないと
wrap_cli の subprocess が stdin を cp932 でデコードし文節分割に失敗するため、
conftest.py の autouse フィクスチャでセッション開始時に設定する（MINGW64/pytest 環境）。
本ファイルではモジュールレベルで追加設定する。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Windows 環境でサブプロセスが UTF-8 を正しく読めるよう設定する。
# wrap.py 内の subprocess.run が PYTHONIOENCODING を継承するため
# pytest 起動前（モジュールロード時）に設定する。
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


from clipwright_wrap.captions import parse_captions
from clipwright_wrap.schemas import WrapCaptionsOptions
from clipwright_wrap.wrap import wrap_captions

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_JA_SRT = FIXTURES_DIR / "sample_ja.srt"
SAMPLE_JA_VTT = FIXTURES_DIR / "sample_ja.vtt"

# ============================================================
# ヘルパー
# ============================================================


def _run_wrap(
    input_path: Path,
    output_path: Path,
    options: WrapCaptionsOptions,
) -> dict[str, Any]:
    """wrap_captions を呼び出して結果を返す。"""
    return wrap_captions(str(input_path), str(output_path), options)


# ============================================================
# e2e ① 実 budoux 文節改行（SRT）
# ============================================================


def test_e2e_1_srt_ok_true(tmp_path: Path) -> None:
    """e2e①-SRT: result['ok'] is True（実 budoux 起動）。"""
    out = tmp_path / "out.srt"
    result = _run_wrap(
        SAMPLE_JA_SRT,
        out,
        WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2),
    )
    assert result["ok"] is True, f"ok should be True, got: {result}"


def test_e2e_1_srt_newline_inserted(tmp_path: Path) -> None:
    """e2e①-SRT: 改行 \\n が cue 内に挿入される（WR-AD-14）。

    sample_ja.srt の 2 cue は max_chars=16 超えの日本語テキスト。
    少なくとも 1 cue に改行が挿入されることを確認する。

    注意: parse_captions は複数テキスト行を空文字結合するため（WR-AD-14）、
    パース後の cue.text では改行を直接確認できない。
    出力 SRT の raw 内容でタイムライン行の直後に複数テキスト行があることを確認する。
    または wrapped_count > 0 を確認する（wrap.py が改行挿入を追跡）。
    """
    out = tmp_path / "out.srt"
    result = _run_wrap(
        SAMPLE_JA_SRT,
        out,
        WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2),
    )
    # wrap.py の wrapped_count で改行挿入を確認（テキストが変化した cue 数）
    assert result["data"]["wrapped_count"] > 0, (
        f"At least one cue should have been wrapped (got wrapped_count=0): {result['data']}"
    )
    # 出力 SRT の raw 内容で \n が cue テキスト部に存在することを確認
    raw = out.read_text(encoding="utf-8")
    # SRT の cue ブロックを抽出してテキスト行が複数あるか確認
    blocks = raw.strip().split("\n\n")
    assert any(
        len(block.splitlines()) > 3  # index + timeline + 2以上のテキスト行
        for block in blocks
    ), "At least one cue block should have multiple text lines after wrap"


def test_e2e_1_srt_line_width_within_max_chars(tmp_path: Path) -> None:
    """e2e①-SRT: overflow(b) でない cue の各行は max_chars 以内（WR-AD-14）。

    出力 SRT の raw ブロックから実際のテキスト行幅を確認する。
    parse_captions は複数テキスト行を結合するため、raw ブロックを直接検証する。
    overflow_width_cue_indices に含まれない cue の各テキスト行 len() <= 16。
    """
    opts = WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2)
    out = tmp_path / "out.srt"
    result = _run_wrap(SAMPLE_JA_SRT, out, opts)
    overflow_width = set(result["data"]["overflow_width_cue_indices"])

    raw = out.read_text(encoding="utf-8")
    blocks = raw.strip().split("\n\n")
    for i, block in enumerate(blocks):
        if i in overflow_width:
            continue  # 巨大文節による行幅超過は許容
        lines = block.splitlines()
        # lines[0] = index, lines[1] = timeline, lines[2:] = テキスト行
        text_lines = lines[2:] if len(lines) > 2 else []
        for line in text_lines:
            assert len(line) <= opts.max_chars, (
                f"cue[{i}] line {line!r} len={len(line)} > max_chars={opts.max_chars}"
            )


def test_e2e_1_srt_timecodes_unchanged(tmp_path: Path) -> None:
    """e2e①-SRT: タイムコードが入力と不変（WR-AD-06）。"""
    opts = WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2)
    out = tmp_path / "out.srt"
    _run_wrap(SAMPLE_JA_SRT, out, opts)
    original_cues = parse_captions(SAMPLE_JA_SRT.read_text(encoding="utf-8"), "srt")
    output_cues = parse_captions(out.read_text(encoding="utf-8"), "srt")
    assert len(original_cues) == len(output_cues)
    for orig, outp in zip(original_cues, output_cues, strict=True):
        assert orig.start == outp.start, (
            f"start changed: {orig.start!r} -> {outp.start!r}"
        )
        assert orig.end == outp.end, f"end changed: {orig.end!r} -> {outp.end!r}"


def test_e2e_1_srt_artifacts_is_dict(tmp_path: Path) -> None:
    """e2e①-SRT: artifacts が dict のリスト（DC-AS-005）。"""
    out = tmp_path / "out.srt"
    result = _run_wrap(
        SAMPLE_JA_SRT,
        out,
        WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2),
    )
    assert isinstance(result["artifacts"], list)
    assert len(result["artifacts"]) == 1
    artifact = result["artifacts"][0]
    assert isinstance(artifact, dict), f"artifact should be dict, got {type(artifact)}"
    assert artifact["role"] == "captions"
    assert artifact["format"] == "srt"


# ============================================================
# e2e ① 実 budoux 文節改行（VTT）
# ============================================================


def test_e2e_1_vtt_ok_true(tmp_path: Path) -> None:
    """e2e①-VTT: result['ok'] is True（実 budoux 起動）。"""
    out = tmp_path / "out.vtt"
    result = _run_wrap(
        SAMPLE_JA_VTT,
        out,
        WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2),
    )
    assert result["ok"] is True, f"ok should be True, got: {result}"


def test_e2e_1_vtt_newline_inserted(tmp_path: Path) -> None:
    """e2e①-VTT: 改行 \\n が cue 内に挿入される（WR-AD-14）。

    wrapped_count > 0 で改行挿入を確認する。
    VTT の raw 内容でも cue テキスト部が複数行あることを確認する。
    """
    out = tmp_path / "out.vtt"
    result = _run_wrap(
        SAMPLE_JA_VTT,
        out,
        WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2),
    )
    assert result["data"]["wrapped_count"] > 0, (
        f"At least one cue should have been wrapped (VTT), got wrapped_count=0: {result['data']}"
    )
    # VTT の raw 内容で改行が cue テキスト部に存在することを確認
    raw = out.read_text(encoding="utf-8")
    # WEBVTT ヘッダと空行をスキップして cue ブロックを確認
    cue_blocks = [b for b in raw.strip().split("\n\n") if "-->" in b]
    assert any(
        len(block.splitlines()) > 2  # timeline行 + 2以上のテキスト行
        for block in cue_blocks
    ), "At least one VTT cue should have multiple text lines after wrap"


def test_e2e_1_vtt_timecodes_unchanged(tmp_path: Path) -> None:
    """e2e①-VTT: タイムコードが入力と不変（WR-AD-06）。"""
    opts = WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2)
    out = tmp_path / "out.vtt"
    _run_wrap(SAMPLE_JA_VTT, out, opts)
    original_cues = parse_captions(SAMPLE_JA_VTT.read_text(encoding="utf-8"), "vtt")
    output_cues = parse_captions(out.read_text(encoding="utf-8"), "vtt")
    assert len(original_cues) == len(output_cues)
    for orig, outp in zip(original_cues, output_cues, strict=True):
        assert orig.start == outp.start
        assert orig.end == outp.end


def test_e2e_1_vtt_artifacts_is_dict(tmp_path: Path) -> None:
    """e2e①-VTT: artifacts が dict のリスト（DC-AS-005）。"""
    out = tmp_path / "out.vtt"
    result = _run_wrap(
        SAMPLE_JA_VTT,
        out,
        WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2),
    )
    artifact = result["artifacts"][0]
    assert isinstance(artifact, dict)
    assert artifact["format"] == "vtt"


# ============================================================
# e2e ② transcribe→wrap 連携（DC-AM-004 正本）
# ============================================================
#
# clipwright_transcribe.captions の to_srt/to_vtt を実 import し、
# 日本語 Segment リストから SRT/VTT を生成 → wrap に通す。
# import が成功した場合は「transcribe 経路」で記録。
# （setup-wrap で clipwright-transcribe が dev 依存として追加済み）

try:
    from clipwright_transcribe.captions import (
        to_srt,
        to_vtt,
    )

    _TRANSCRIBE_IMPORT_OK = True
except ImportError:
    _TRANSCRIBE_IMPORT_OK = False

# transcribe import 成否に応じてフィクスチャ SRT/VTT 生成方法を切り替える
_TRANSCRIBE_SEGMENTS = [
    {
        "start_sec": 0.0,
        "end_sec": 2.0,
        "text": "今日はとてもいい天気なので公園に散歩に行きました。",
    },
    {
        "start_sec": 2.5,
        "end_sec": 5.0,
        "text": "桜の花びらが舞い散り、川沿いの遊歩道を歩きながら春の訪れを感じた。",
    },
]

if _TRANSCRIBE_IMPORT_OK:
    _E2E2_SRT_CONTENT: str = to_srt(_TRANSCRIBE_SEGMENTS)  # type: ignore[arg-type]
    _E2E2_VTT_CONTENT: str = to_vtt(_TRANSCRIBE_SEGMENTS)  # type: ignore[arg-type]
else:
    # フォールバック: WR-AD-12 バイト構造準拠の手組みフィクスチャ
    _E2E2_SRT_CONTENT = (
        "1\n00:00:00,000 --> 00:00:02,000\n今日はとてもいい天気なので公園に散歩に行きました。\n"
        "\n"
        "2\n00:00:02,500 --> 00:00:05,000\n桜の花びらが舞い散り、川沿いの遊歩道を歩きながら春の訪れを感じた。\n"
    )
    _E2E2_VTT_CONTENT = (
        "WEBVTT\n"
        "\n"
        "00:00:00.000 --> 00:00:02.000\n今日はとてもいい天気なので公園に散歩に行きました。\n"
        "\n"
        "00:00:02.500 --> 00:00:05.000\n桜の花びらが舞い散り、川沿いの遊歩道を歩きながら春の訪れを感じた。\n"
    )


def test_e2e_2_transcribe_srt_ok(tmp_path: Path) -> None:
    """e2e②-SRT: transcribe to_srt 出力を wrap に通して ok:True。

    _TRANSCRIBE_IMPORT_OK が True の場合は transcribe 経路（正本）。
    False の場合は手組みフィクスチャ経路（フォールバック）。
    test-report に経路を明記する。
    """
    # transcribe 出力をファイルに書き出す
    in_srt = tmp_path / "transcribe_out.srt"
    in_srt.write_text(_E2E2_SRT_CONTENT, encoding="utf-8")

    out_srt = tmp_path / "wrapped.srt"
    result = _run_wrap(
        in_srt, out_srt, WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2)
    )
    assert result["ok"] is True, f"ok should be True, got: {result}"


def test_e2e_2_transcribe_vtt_ok(tmp_path: Path) -> None:
    """e2e②-VTT: transcribe to_vtt 出力を wrap に通して ok:True。"""
    in_vtt = tmp_path / "transcribe_out.vtt"
    in_vtt.write_text(_E2E2_VTT_CONTENT, encoding="utf-8")

    out_vtt = tmp_path / "wrapped.vtt"
    result = _run_wrap(
        in_vtt, out_vtt, WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2)
    )
    assert result["ok"] is True, f"ok should be True, got: {result}"


def test_e2e_2_transcribe_srt_wrapped_output(tmp_path: Path) -> None:
    """e2e②-SRT: wrap 出力の SRT が ok:True かつ整形 cue を持つ。"""
    in_srt = tmp_path / "transcribe_out.srt"
    in_srt.write_text(_E2E2_SRT_CONTENT, encoding="utf-8")
    out_srt = tmp_path / "wrapped.srt"

    result = _run_wrap(
        in_srt, out_srt, WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2)
    )
    assert result["ok"] is True
    assert result["data"]["cue_count"] == 2
    # 少なくとも 1 cue に改行が挿入される（max_chars=16 超えテキスト）
    # wrapped_count で確認（parse_captions は複数テキスト行を結合するため raw 確認と併用）
    assert result["data"]["wrapped_count"] > 0, (
        f"Expected wrapped_count > 0 for transcribe output, got: {result['data']}"
    )
    raw = out_srt.read_text(encoding="utf-8")
    blocks = [b for b in raw.strip().split("\n\n") if b.strip()]
    assert any(len(b.splitlines()) > 3 for b in blocks), (
        "Expected multi-line text in at least one cue"
    )


# ============================================================
# DC-GP-004: 0 件 e2e
# ============================================================


def test_e2e_zero_srt_ok_empty(tmp_path: Path) -> None:
    """DC-GP-004-SRT: 0件 SRT（空文字列）を wrap に通し ok:True・空出力で往復同一。

    transcribe の 0件出力は to_srt='' (空文字列)。
    wrap は ok:True で SRT='' を出力する。
    """
    in_srt = tmp_path / "empty.srt"
    in_srt.write_text("", encoding="utf-8")

    out_srt = tmp_path / "out_empty.srt"
    result = _run_wrap(
        in_srt, out_srt, WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2)
    )
    assert result["ok"] is True, f"ok should be True for empty SRT, got: {result}"

    # 出力が空文字列（往復同一）
    assert out_srt.read_text(encoding="utf-8") == "", (
        "Empty SRT should produce empty output"
    )

    # data に cue_count=0
    assert result["data"]["cue_count"] == 0


def test_e2e_zero_vtt_ok_header_only(tmp_path: Path) -> None:
    """DC-GP-004-VTT: 0件 VTT（'WEBVTT\\n'）を wrap に通し ok:True・WEBVTT\\n 出力で往復同一。

    transcribe の 0件出力は to_vtt='WEBVTT\\n' (ヘッダのみ)。
    wrap は ok:True で VTT='WEBVTT\\n' を出力する。
    """
    in_vtt = tmp_path / "empty.vtt"
    in_vtt.write_text("WEBVTT\n", encoding="utf-8")

    out_vtt = tmp_path / "out_empty.vtt"
    result = _run_wrap(
        in_vtt, out_vtt, WrapCaptionsOptions(language="ja", max_chars=16, max_lines=2)
    )
    assert result["ok"] is True, f"ok should be True for empty VTT, got: {result}"

    # 出力が 'WEBVTT\n'（往復同一）
    assert out_vtt.read_text(encoding="utf-8") == "WEBVTT\n", (
        "Empty VTT should produce 'WEBVTT\\n' output"
    )

    assert result["data"]["cue_count"] == 0


# ============================================================
# spike 照合: budoux API 仕様（fixtures/README.md との一致確認）
# ============================================================


def test_spike_budoux_parser_load_api() -> None:
    """spike 照合: load_default_japanese_parser() が呼び出せる（README §2 確定）。"""
    import budoux

    assert hasattr(budoux, "load_default_japanese_parser"), (
        "budoux.load_default_japanese_parser should exist"
    )
    assert not hasattr(budoux, "load_parser"), (
        "budoux.load_parser should NOT exist (spike 確認済み)"
    )


def test_spike_budoux_parse_returns_list_str() -> None:
    """spike 照合: parse() -> list[str]（README §3 確定）。"""
    import budoux

    parser = budoux.load_default_japanese_parser()
    result = parser.parse("今日はいい天気です。")
    assert isinstance(result, list)
    assert all(isinstance(t, str) for t in result)
    # token 結合で元テキストを復元できる（区切り文字なし）
    assert "".join(result) == "今日はいい天気です。"


def test_spike_budoux_parse_sample_ja() -> None:
    """spike 照合: fixtures/README.md §3 のサンプル文節分割と一致するか確認。

    README サンプル: parse("今日はとてもいい天気なので公園に散歩に行きました。")
    → ["今日は", "とても", "いい", "天気なので", "公園に", "散歩に", "行きました。"]
    """
    import budoux

    parser = budoux.load_default_japanese_parser()
    result = parser.parse("今日はとてもいい天気なので公園に散歩に行きました。")
    expected = [
        "今日は",
        "とても",
        "いい",
        "天気なので",
        "公園に",
        "散歩に",
        "行きました。",
    ]
    assert result == expected, f"budoux parse mismatch: {result} != {expected}"


def test_spike_budoux_all_languages_loadable() -> None:
    """spike 照合: 全4言語ロード成功（README §4 確定）。"""
    import budoux

    loaders = {
        "ja": budoux.load_default_japanese_parser,
        "zh-hans": budoux.load_default_simplified_chinese_parser,
        "zh-hant": budoux.load_default_traditional_chinese_parser,
        "th": budoux.load_default_thai_parser,
    }
    for lang, loader in loaders.items():
        parser = loader()
        assert parser is not None, f"Failed to load parser for language: {lang}"


def test_spike_budoux_parser_loaders_dict() -> None:
    """spike 照合: wrap_cli._PARSER_LOADERS に全4言語が存在する（README §2）。"""
    from clipwright_wrap.wrap_cli import _PARSER_LOADERS

    assert set(_PARSER_LOADERS.keys()) == {"ja", "zh-hans", "zh-hant", "th"}


# ============================================================
# WR-AD-15/DC-AM-003: 超過 warnings
# ============================================================


def test_e2e_overflow_line_count_warning(tmp_path: Path) -> None:
    """WR-AD-15(1)(a): max_chars=4 で行数超過 → warnings に集約1文・overflow_cue_indices 記録。

    '今日はとてもいい天気なので' を max_chars=4 で wrap すると
    文節 ['今日は', 'とても', 'いい', '天気なので'] → 4行（> max_lines=2）になる。
    """
    # max_chars=4 でオーバーフローを強制する
    text = "今日はとてもいい天気なので"
    in_srt = tmp_path / "overflow.srt"
    in_srt.write_text(
        f"1\n00:00:00,000 --> 00:00:02,000\n{text}\n",
        encoding="utf-8",
    )
    out_srt = tmp_path / "out_overflow.srt"
    opts = WrapCaptionsOptions(language="ja", max_chars=4, max_lines=2)
    result = _run_wrap(in_srt, out_srt, opts)

    assert result["ok"] is True

    # overflow_cue_indices に cue[0] が記録されている
    assert 0 in result["data"]["overflow_cue_indices"], (
        f"Expected cue[0] in overflow_cue_indices: {result['data']['overflow_cue_indices']}"
    )

    # warnings に集約1文が含まれる
    warnings = result.get("warnings", [])
    assert any("max_lines" in w for w in warnings), (
        f"Expected max_lines warning, got: {warnings}"
    )

    # 切り捨てなし（テキストが保持されている）
    content = out_srt.read_text(encoding="utf-8")
    assert text in content.replace("\n", ""), (
        "Original text should be preserved (no truncation)"
    )


def test_e2e_overflow_line_width_warning(tmp_path: Path) -> None:
    """WR-AD-15(1)(b): 単一巨大文節（行幅超過）→ warnings に集約1文・overflow_width_cue_indices 記録。

    '天気なので' (5文字) は max_chars=4 より長い1文節 → 行幅超過。
    """
    text = "今日はとてもいい天気なので"
    in_srt = tmp_path / "overflow_width.srt"
    in_srt.write_text(
        f"1\n00:00:00,000 --> 00:00:02,000\n{text}\n",
        encoding="utf-8",
    )
    out_srt = tmp_path / "out_overflow_width.srt"
    opts = WrapCaptionsOptions(language="ja", max_chars=4, max_lines=2)
    result = _run_wrap(in_srt, out_srt, opts)

    assert result["ok"] is True

    # overflow_width_cue_indices に cue[0] が記録されている
    assert 0 in result["data"]["overflow_width_cue_indices"], (
        f"Expected cue[0] in overflow_width_cue_indices: {result['data']['overflow_width_cue_indices']}"
    )

    # warnings に max_chars の集約1文が含まれる
    warnings = result.get("warnings", [])
    assert any("max_chars" in w for w in warnings), (
        f"Expected max_chars warning, got: {warnings}"
    )


def test_e2e_overflow_no_truncation(tmp_path: Path) -> None:
    """WR-AD-15(1): overflow 時も切り捨てなし（情報欠落回避・WR-AD-04）。

    行数/行幅超過があっても元テキストの全文節が出力に保持される。
    """
    text = "今日はとてもいい天気なので"
    in_srt = tmp_path / "no_truncation.srt"
    in_srt.write_text(
        f"1\n00:00:00,000 --> 00:00:02,000\n{text}\n",
        encoding="utf-8",
    )
    out_srt = tmp_path / "out_no_truncation.srt"
    opts = WrapCaptionsOptions(language="ja", max_chars=4, max_lines=2)
    _run_wrap(in_srt, out_srt, opts)

    content = out_srt.read_text(encoding="utf-8")
    # 出力テキストを結合すると元テキストと一致する
    cues = parse_captions(content, "srt")
    assert len(cues) == 1
    rejoined = cues[0].text.replace("\n", "")
    assert rejoined == text, f"Expected '{text}', got '{rejoined}'"
