# clipwright-__TOOL__

（TODO: このツールが何をするか1文で。例: 〜を検出して OTIO/JSON 注記を返す MCP ツール。）

## 概要

（TODO: 入力・処理・出力の概要。detect/inspect 系か render 系かを明記する。）

## MCP ツール

`clipwright___ACTION__`

### パラメータ

| 名前 | 型 | 既定値 | 説明 |
|---|---|---|---|
| `input` | `string` | 必須 | 入力ファイルパス（既存ファイル） |
| `output` | `string` | 必須 | 出力 artifact パス（新規生成・入力とは別パス） |
| `example_threshold` | `float` | `0.5` | （TODO: 実パラメータに置き換える） |

## 依存関係

| パッケージ | 用途 |
|---|---|
| `clipwright` | 共通型・エンベロープ・エラー |
| `mcp[cli]` | MCP サーバー |
| `pydantic` | パラメータ検証 |

（外部 OSS を subprocess で包む場合はここに追記し、README に PATH 前提や導入手順を明記する。）

## インストール・起動

```bash
uv add clipwright-__TOOL__
clipwright-__TOOL__
```

または uv workspace 内で:

```bash
uv run --package clipwright-__TOOL__ clipwright-__TOOL__
```

## 前提

- Python 3.11 以上
- （外部 OSS が必要なら PATH 前提をここに明記する）
