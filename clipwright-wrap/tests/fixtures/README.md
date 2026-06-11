# fixtures/ Confirmed Matters

budoux API specification confirmed in spike-budoux spike (2026-06-11).
Used as gate for test-contract / impl-wrapcli / impl-wrap / e2e.

---

## 1. budoux Version

```
budoux 0.8.4
```

## 2. parser Load API Confirmed Value

### Important: `load_parser()` Does Not Exist

budoux 0.8.4 does not have a generic `budoux.load_parser(<name>)` function.
Instead, use language-specific functions:

| Language | Confirmed API | Return Type |
|---|---|---|
| `ja` (Japanese) | `budoux.load_default_japanese_parser()` | `Parser` |
| `zh-hans` (Simplified Chinese) | `budoux.load_default_simplified_chinese_parser()` | `Parser` |
| `zh-hant` (Traditional Chinese) | `budoux.load_default_traditional_chinese_parser()` | `Parser` |
| `th` (Thai) | `budoux.load_default_thai_parser()` | `Parser` |

**Language → Loader Function Mapping** (should be isolated as module constant in wrap_cli.py):
```python
_PARSER_LOADERS = {
    "ja": budoux.load_default_japanese_parser,
    "zh-hans": budoux.load_default_simplified_chinese_parser,
    "zh-hant": budoux.load_default_traditional_chinese_parser,
    "th": budoux.load_default_thai_parser,
}
```

## 3. parse() Return Value Specification

```
parser.parse(text: str) -> list[str]
```

- Return value is `list[str]` (list of phrase tokens).
- Joining each token (with no delimiter) perfectly reconstructs the original text: `"".join(segments) == text`
- Half-width spaces and delimiters are **not inserted** (conforming to WR-AD-14 counting specification).

### parse Sample Results (ja)

```python
parser.parse("今日はいい天気です。")
# → ["今日は", "いい", "天気です。"]

parser.parse("今日はとてもいい天気なので公園に散歩に行きました。")
# → ["今日は", "とても", "いい", "天気なので", "公園に", "散歩に", "行きました。"]

parser.parse("桜の花びらが舞い散り、川沿いの遊歩道を歩きながら春の訪れを感じた。")
# → ["桜の", "花びらが", "舞い", "散り、", "川沿いの", "遊歩道を", "歩きながら春の", "訪れを", "感じた。"]

parser.parse("字幕改行ツールclipwright-wrapはBudouXを使って日本語テキストを文節で分割します。")
# → ["字幕改行ツールclipwright-wrapは", "BudouXを", "使って", "日本語テキストを", "文節で", "分割します。"]
```

## 4. Loadable Languages (DC-AM-005 Gate)

**All 4 languages loaded successfully**. Keep the languages in schemas.py `language` pattern:

```
ja | zh-hans | zh-hant | th
```

Regular expression pattern: `^(ja|zh-hans|zh-hant|th)$`

Unsupported languages: **None** (all 4 languages succeed)

## 5. parser Load Time (DC-AS-002 / DC-GP-005 Gate)

Time required for initial `load_default_*_parser()` (typical values):

| Language | Load Time |
|---|---|
| `ja` | ≈ 0.4 ms |
| `zh-hans` | ≈ 1.0 ms |
| `zh-hant` | ≈ 1.1 ms |
| `th` | ≈ 0.6 ms |

**Conclusion**: Loading is extremely fast. Loading once outside the texts loop poses no practical issues (conforming to DC-AS-002).

## 6. Long-Text cue Parse Time and timeout Reasonableness (DC-GP-005 Gate)

### Measurement Results (Japanese, 5-run Average)

| Text Length | Average Parse Time | Phrase Count |
|---|---|---|
| 2000 characters | ≈ 3.7 ms | ≈ 553 phrases |
| 3000 characters | ≈ 5.5 ms | ≈ 829 phrases |
| 5000 characters | ≈ 9.3 ms | ≈ 1380 phrases |
| 25 characters (typical 1 cue) | ≈ 0.04 ms | — |

### Reasonableness Assessment of WR-AD-15(2) `max(30, ceil(cue_count * 0.05))`

**0.05 coefficient and lower bound of 30 seconds is reasonable. Switching to total character count dependency is not necessary.**

Rationale:
- Parse time for typical 1 cue (25 characters) is ≈ 0.04 ms = 0.00004 seconds.
- For 1000 cues: actual parse ≈ 40 ms = 0.04 seconds vs. timeout = 50 seconds (1000 × 0.05).
- Even for extreme long text of 1 cue (5000 characters), ≈ 9.3 ms; timeout lower bound of 30 seconds adequately covers 100+ cues with long text.
- It is unrealistic for 1 cue to contain tens of thousands of characters in subtitles, so character-count dependency is over-engineering.
- The coefficient of `cue_count * 0.05` is reasonable as a buffer to absorb overhead from subprocess startup, JSON I/O, and OS scheduling beyond parse processing itself.

**Instruction for impl-wrap**: Implement `max(30, ceil(cue_count * 0.05))` as a module constant.

## 7. Fixture Files

- `budoux_sample.json`: ja with multiple samples (short text, long text, with punctuation, mixed alphanumeric) of actual budoux output. Used as confirmed fixture for test-wrapcli/test-captions.

---

*Generated: spike-budoux task (plan-report-20260611-023253.md)*
