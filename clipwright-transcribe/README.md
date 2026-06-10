# clipwright-transcribe

音声・映像ファイルを文字起こしし、SRT/VTT キャプションと OTIO タイムラインを生成する MCP ツール。

## 依存バイナリ・ファイル

このツールは以下の外部バイナリ・ファイルが実行環境に存在することを前提とする。**pip でインストールされない**ため、別途用意すること。

### whisper.cpp バイナリ

文字起こしに使用する。

- PATH に `whisper-cli`（または環境に応じたバイナリ名）を配置するか、環境変数 `CLIPWRIGHT_WHISPER` にバイナリのフルパスを指定する。
- 入手: https://github.com/ggerganov/whisper.cpp からビルド、またはリリースバイナリを使用する。

```
export CLIPWRIGHT_WHISPER=/path/to/whisper-cli
```

### ggml モデルファイル

whisper.cpp が使用する音声認識モデル（`.bin` ファイル）。

- 環境変数 `CLIPWRIGHT_WHISPER_MODEL` にモデルファイルのフルパスを指定する。ツール呼び出し時に `model_path` パラメータでも上書き可能。
- 入手: https://huggingface.co/ggerganov/whisper.cpp などからダウンロード。

```
export CLIPWRIGHT_WHISPER_MODEL=/path/to/ggml-base.bin
```

### ffmpeg

音声ファイルを 16kHz mono WAV に変換（whisper.cpp の入力形式）するために必須。

- PATH に `ffmpeg` を配置するか、環境変数 `CLIPWRIGHT_FFMPEG` にフルパスを指定する。

```
export CLIPWRIGHT_FFMPEG=/path/to/ffmpeg
```

## 環境変数まとめ

| 環境変数 | 用途 | 必須 |
|---|---|---|
| `CLIPWRIGHT_WHISPER` | whisper.cpp バイナリのパス（PATH になければ必須） | 条件付き |
| `CLIPWRIGHT_WHISPER_MODEL` | ggml モデルファイルのパス（`model_path` パラメータが優先） | 条件付き |
| `CLIPWRIGHT_FFMPEG` | ffmpeg バイナリのパス（PATH になければ必須） | 条件付き |

## MCP ツール

`clipwright_transcribe(media, output, options?)` — 音声・映像ファイルを文字起こしし、`output.otio` / `output.srt` / `output.vtt` を生成する。
