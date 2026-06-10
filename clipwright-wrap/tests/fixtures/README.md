# fixtures/ 確定事項

spike-budoux スパイク（2026-06-11）で確定した budoux API 仕様。
test-contract / impl-wrapcli / impl-wrap / e2e のゲートとして使う。

---

## 1. budoux バージョン

```
budoux 0.8.4
```

## 2. parser ロード API 確定値

### 重要: `load_parser()` は存在しない

budoux 0.8.4 には `budoux.load_parser(<name>)` という汎用ロード関数は**存在しない**。
代わりに言語ごとの専用関数を使う:

| 言語 | 確定 API | 戻り値型 |
|---|---|---|
| `ja`（日本語） | `budoux.load_default_japanese_parser()` | `Parser` |
| `zh-hans`（簡体字） | `budoux.load_default_simplified_chinese_parser()` | `Parser` |
| `zh-hant`（繁体字） | `budoux.load_default_traditional_chinese_parser()` | `Parser` |
| `th`（タイ語） | `budoux.load_default_thai_parser()` | `Parser` |

**language → ロード関数のマッピング**（wrap_cli.py のモジュール定数として隔離すること）:
```python
_PARSER_LOADERS = {
    "ja": budoux.load_default_japanese_parser,
    "zh-hans": budoux.load_default_simplified_chinese_parser,
    "zh-hant": budoux.load_default_traditional_chinese_parser,
    "th": budoux.load_default_thai_parser,
}
```

## 3. parse() 戻り値仕様

```
parser.parse(text: str) -> list[str]
```

- 戻り値は `list[str]`（文節トークンのリスト）。
- 各トークンを結合（区切り文字なし）すると元のテキストに完全復元できる: `"".join(segments) == text`
- 半角空白・区切り文字は**挿入しない**（WR-AD-14 カウント仕様に合致）。

### parse サンプル結果（ja）

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

## 4. 実ロード可能な対応言語（DC-AM-005 ゲート）

**全4言語ロード成功**。schemas.py の `language` pattern に残す言語:

```
ja | zh-hans | zh-hant | th
```

正規表現パターン: `^(ja|zh-hans|zh-hant|th)$`

ロード不可言語: **なし**（4言語すべて成功）

## 5. parser ロード時間（DC-AS-002 / DC-GP-005 ゲート）

初回 `load_default_*_parser()` の所要時間（典型値）:

| 言語 | ロード時間 |
|---|---|
| `ja` | ≈ 0.4 ms |
| `zh-hans` | ≈ 1.0 ms |
| `zh-hant` | ≈ 1.1 ms |
| `th` | ≈ 0.6 ms |

**結論**: ロードは極めて高速。texts ループ外で1回だけロードすれば実用上問題ない（DC-AS-002 準拠）。

## 6. 長文 cue パース時間と timeout 妥当性（DC-GP-005 ゲート）

### 計測結果（日本語・5回平均）

| テキスト長 | 平均パース時間 | 文節数 |
|---|---|---|
| 2000 文字 | ≈ 3.7 ms | ≈ 553 文節 |
| 3000 文字 | ≈ 5.5 ms | ≈ 829 文節 |
| 5000 文字 | ≈ 9.3 ms | ≈ 1380 文節 |
| 25 文字（典型1cue） | ≈ 0.04 ms | — |

### WR-AD-15(2) `max(30, ceil(cue_count * 0.05))` の妥当性判断

**0.05 係数・下限 30 秒は妥当。総文字数連動への切り替えは不要。**

根拠:
- 典型1cue（25文字）のパース時間は ≈ 0.04 ms = 0.00004 秒。
- 1000 cue の場合: 実パース ≈ 40 ms = 0.04 秒に対して timeout = 50 秒（1000 × 0.05）。
- 5000 文字の極端な長文1cueでも ≈ 9.3 ms。timeout の下限 30 秒は極端な長文を含む100cue 以上の字幕を十分吸収する。
- 1cue が万単位の文字数になることは字幕として非現実的なため、文字数連動は過剰設計。
- `cue_count * 0.05` の係数はパース処理以外のオーバーヘッド（subprocess 起動・JSON I/O・OS スケジューリング）を吸収するバッファとして妥当。

**impl-wrap への指示**: `max(30, ceil(cue_count * 0.05))` をモジュール定数として実装すること。

## 7. フィクスチャファイル

- `budoux_sample.json`: ja の複数サンプル（短文・長文・句読点入り・英数字混じり）の実 budoux 出力。test-wrapcli/test-captions の確定 fixture として使う。

---

*生成: spike-budoux タスク（plan-report-20260611-023253.md）*
