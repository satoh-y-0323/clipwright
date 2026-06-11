# clipwright-noise

ノイズ検出 → OTIO タイムライン注記生成 MCP ツール。

## 概要

ffmpeg `astats` フィルタで音声のノイズフロアを測定し、
denoise 指示（backend・パラメータ）を timeline-level `metadata["clipwright"]["denoise"]` に書き込む。

検出のみ（OTIO 注記）を行い、実体化（ffmpeg フィルタ適用）は `clipwright-render` が一回だけ行う
（設計 M3: 検出と適用の分離）。

**初版 render 対応状況**:
- `afftdn` backend: render 適用対応済み（`clipwright-render` が afftdn フィルタを注入）。
- `deepfilternet` backend: 注記のみ。render 適用は未対応（将来版で対応予定）。

## 前提

- Python 3.11 以上
- **ffmpeg / ffprobe が `PATH` 上に存在するか、環境変数 `CLIPWRIGHT_FFMPEG` / `CLIPWRIGHT_FFPROBE` にフルパスが設定されていること。**

ffmpeg は PATH に直接追加するか、以下の環境変数で明示指定:

```bash
export CLIPWRIGHT_FFMPEG=/path/to/ffmpeg
export CLIPWRIGHT_FFPROBE=/path/to/ffprobe
```

## MCP ツール

`clipwright_detect_noise`

### パラメータ

| 名前 | 型 | 既定値 | 説明 |
|---|---|---|---|
| `media` | `string` | 必須 | 入力メディアファイルパス（映像＋音声必須） |
| `output` | `string` | 必須 | 出力 OTIO タイムラインパス（`.otio`・media と同一ディレクトリ） |
| `options.backend` | `"afftdn" \| "deepfilternet"` | `"afftdn"` | denoise バックエンド |
| `options.strength` | `"light" \| "medium" \| "strong"` | `"medium"` | afftdn nr 写像（light=6/medium=12/strong=24 dB） |
| `timeline` | `string \| null` | `null` | 既存 OTIO タイムラインパス（指定時は追記） |

## 依存関係

| パッケージ | 用途 |
|---|---|
| `clipwright` | 共通型・エンベロープ・エラー・process.run |
| `mcp[cli]` | MCP サーバー |
| `pydantic` | パラメータ検証 |

ffmpeg / ffprobe はライセンス独立のため別プロセス起動（PATH または環境変数経由）。
DeepFilterNet バイナリは初版で同梱せず・render 側将来依存。

## インストール・起動

uv workspace 内で:

```bash
uv run --package clipwright-noise clipwright-noise
```

または直接インストール:

```bash
uv add clipwright-noise
clipwright-noise
```
