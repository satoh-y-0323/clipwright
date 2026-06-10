# clipwright-wrap

字幕ファイル（SRT/VTT）の各 cue テキストを BudouX で文節境界改行整形する MCP ツール。

## 概要

`clipwright-wrap` は SRT/VTT 字幕ファイルを入力に取り、各 cue のテキストを BudouX で文節分割したうえで指定文字数・行数に収まるよう改行を挿入し、同形式の字幕ファイルを出力する。FFmpeg / Whisper には依存しない純テキスト整形ツール。

## 入出力

- **入力**: SRT ファイル（`.srt`）または VTT ファイル（`.vtt`）
- **出力**: 入力と同一形式の字幕ファイル（文節境界改行挿入済み）
- **タイムコード**: 不変（再タイミングは行わない）

## MCP ツール

`clipwright_wrap_captions`

### パラメータ

| 名前 | 型 | 既定値 | 説明 |
|---|---|---|---|
| `input` | `string` | 必須 | 入力字幕ファイルパス（`.srt` / `.vtt`） |
| `output` | `string` | 必須 | 出力字幕ファイルパス（入力と同一拡張子） |
| `language` | `string` | `"ja"` | 文節分割言語（`ja` / `zh-hans` / `zh-hant` / `th`） |
| `max_chars` | `int` | `16` | 1 行最大文字数（全角・半角とも 1 文字カウント）。正の整数。 |
| `max_lines` | `int` | `2` | 1 cue あたりの最大行数。超過 cue は warnings に記録（切り捨てなし）。正の整数。 |

### 文字数カウント仕様

`max_chars` は **一律 1 文字カウント**（全角・半角とも `len()` の 1 文字）。全角換算は将来拡張（要件 §8）。

## 文節改行の仕組み

1. 各 cue テキスト（複数行を持つ場合は改行を除去して結合）を BudouX で文節分割
2. 文節トークン列を `max_chars` に収まるよう貪欲に 1 行へ詰める
3. 整形済みテキスト（`\n` 区切りの複数行）を cue に書き戻す

1 文節が単独で `max_chars` を超える場合はその文節を 1 行に置く（途中で割らない）。

## 対応言語

BudouX が文節分割をサポートする以下の言語に対応する:

| `language` 値 | 言語 |
|---|---|
| `ja` | 日本語 |
| `zh-hans` | 中国語（簡体字） |
| `zh-hant` | 中国語（繁体字） |
| `th` | タイ語 |

## 依存関係

| パッケージ | 用途 |
|---|---|
| `budoux` | 文節境界分割（通常依存・軽量モデル同梱） |
| `clipwright` | 共通型・エンベロープ・エラー |
| `mcp[cli]` | MCP サーバー |
| `pydantic` | パラメータ検証 |

**FFmpeg・Whisper には依存しない**（純テキスト整形のため）。`budoux` は通常依存として同梱されるため、環境変数ゲートなしで e2e テストを常時実行できる。

## インストール・起動

```bash
uv add clipwright-wrap
clipwright-wrap
```

または uv workspace 内で:

```bash
uv run --package clipwright-wrap clipwright-wrap
```

## 前提

- Python 3.11 以上
- FFmpeg 不要（テキスト整形のみ）
