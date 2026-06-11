# clipwright-bgm

BGM 配置注記を OTIO タイムラインに書き込む MCP ツール。BGM の音量・フェード・ダッキング指示を A2 Audio トラッククリップのメタデータとして記録し、clipwright-render がミックスを実体化する。

## 概要

- **入力**: タイムライン OTIO ファイル・BGM 音声ファイル・出力パス・オプション（音量・フェード・ダッキング）
- **処理**: OTIO 操作のみ（ffmpeg/外部 OSS なし）。BGM クリップを A2 Audio トラックに追加し clipwright metadata を書き込む
- **出力**: BGM 注記付きの新規 OTIO ファイル（入力 timeline は不変・M5）

## MCP ツール

`clipwright_add_bgm`

### パラメータ

| 名前 | 型 | 既定値 | 説明 |
|---|---|---|---|
| `timeline` | `string` | 必須 | 入力タイムラインファイルパス（既存 .otio） |
| `bgm` | `string` | 必須 | BGM 音声ファイルパス（mp3/wav/m4a/aac/flac/ogg/opus/mp4/mkv/mov/webm） |
| `output` | `string` | 必須 | 出力 OTIO ファイルパス（新規生成・入力とは別パス） |
| `options` | `object` | `null` | BgmOptions（volume_db / fade_in_sec / fade_out_sec / ducking） |

## 依存関係

| パッケージ | 用途 |
|---|---|
| `clipwright` | 共通型・エンベロープ・エラー・inspect_media |
| `mcp[cli]` | MCP サーバー |
| `pydantic` | パラメータ検証 |

## インストール・起動

```bash
uv add clipwright-bgm
clipwright-bgm
```

または uv workspace 内で:

```bash
uv run --package clipwright-bgm clipwright-bgm
```

## 前提

- Python 3.11 以上
- ffprobe が PATH 上、または `CLIPWRIGHT_FFPROBE` 環境変数で指定済み（BGM メディア尺取得に使用）
