# clipwright-render

OTIO タイムラインを FFmpeg で実体化する MCP ツール。

clipwright は「検出（detect）と適用（render）の分離」を中核思想とする道具箱です。detect 系ツールはメディアを書き換えず OTIO に注記を返すだけで、**実体化はこの `clipwright-render` 一本がまとめて一回だけ行います**（再エンコード 1 回で区間抽出・連結・トリムを完結させます）。

---

## 前提条件

本ツールは以下の条件を満たす素材・タイムラインを対象とします。条件を外れた入力はエラーを返します。

| 条件 | 詳細 |
|---|---|
| フレームレート | CFR（定フレームレート）のみ。VFR（可変フレームレート）は非対応 |
| 解像度 | 固定解像度のみ。フレームごとに解像度が変動する素材は非対応 |
| ソース数 | タイムライン内の素材は単一ソース（1 ファイル）のみ |
| 映像トラック | 必須。映像なしは非対応 |
| 音声トラック | 0 または 1 ストリームのみ。複数ある場合は第 1 音声のみ採用 |

### スコープ外（将来対応予定）

- VFR / 解像度変動素材
- 複数ソースファイルの連結
- 字幕焼き込み（subtitle burn-in）
- トランジション
- video トラック 2 本以上のタイムライン

---

## FFmpeg の準備

**FFmpeg / FFprobe はこのパッケージに同梱していません**。各自の環境に導入してください。

```bash
# macOS（Homebrew）
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg
```

`ffmpeg` / `ffprobe` が PATH 上にある場合はそのまま動作します。PATH に追加できない環境では環境変数で明示的にパスを指定してください。

```bash
export CLIPWRIGHT_FFMPEG=/usr/local/bin/ffmpeg
export CLIPWRIGHT_FFPROBE=/usr/local/bin/ffprobe
```

> ライセンスについて: ラッパー本体（このパッケージ）は **MIT** ライセンスです。FFmpeg バイナリは同梱していないため、FFmpeg の LGPL / GPL 再配布義務はラッパーには適用されません。FFmpeg 自体のライセンス（LGPL v2.1 / GPL v2）はユーザー環境でご確認ください。

---

## インストール

```bash
uv sync
```

---

## 使い方

### MCP ツール（`clipwright_render`）

Claude / エージェントから MCP 経由で呼び出します。

```jsonc
{
  "tool": "clipwright_render",
  "arguments": {
    "timeline": "/path/to/timeline.otio",
    "output": "/path/to/output.mp4",
    "dry_run": false,
    "options": {
      "video_codec": "libx264",
      "audio_codec": "aac",
      "width": 1920,
      "height": 1080,
      "fps": 29.97,
      "crf": 23,
      "overwrite": false
    }
  }
}
```

**引数**

| 引数 | 型 | 必須 | 説明 |
|---|---|---|---|
| `timeline` | string | 必須 | 入力 OTIO ファイルのパス |
| `output` | string | 必須 | 出力ファイルのパス（`.mp4` / `.mkv` / `.mov` / `.webm`） |
| `dry_run` | bool | 省略可（既定 `false`） | `true` にすると実レンダリングを行わず計画のみ返す |
| `options` | object | 省略可 | 出力オプション（下記 RenderOptions 参照） |

**RenderOptions**

| フィールド | 型 | 説明 |
|---|---|---|
| `video_codec` | string \| null | 映像コーデック（例: `libx264`・既定: ソース踏襲） |
| `audio_codec` | string \| null | 音声コーデック（例: `aac`・既定: ソース踏襲） |
| `width` | int \| null | 出力幅（`height` と必ずセットで指定） |
| `height` | int \| null | 出力高さ（`width` と必ずセットで指定） |
| `fps` | float \| null | 出力フレームレート |
| `crf` | int \| null | 品質指定 CRF 値（0〜51） |
| `overwrite` | bool | `true` にすると出力ファイルが既存でも上書き（既定 `false`） |

`width` / `height` は両方指定するか両方 `null` にしてください。片方のみ指定はエラーになります。

**返り値（成功時）**

```jsonc
{
  "ok": true,
  "summary": "2 クリップ → 45.2 秒 / 42.1 MB / outputs/out.mp4",
  "data": {
    "output_path": "/path/to/output.mp4",
    "duration_sec": 45.2,
    "size_bytes": 44150784,
    "clip_count": 2
  },
  "artifacts": ["/path/to/output.mp4"],
  "warnings": []
}
```

**dry_run 時の返り値**

```jsonc
{
  "ok": true,
  "summary": "dry_run: 2 区間 / 想定 45.2 秒 / 概算 42.1 MB",
  "data": {
    "dry_run": true,
    "clip_count": 2,
    "estimated_duration_sec": 45.2,
    "estimated_size_bytes": 44150784,
    "ffmpeg_args": ["ffmpeg", "-i", "source.mp4", "-filter_complex", "..."]
  },
  "artifacts": [],
  "warnings": []
}
```

`estimated_size_bytes` は FFprobe で取得したビットレートと出力尺から計算した概算値です。ビットレートが取得できない場合は `null` になり `warnings` に理由が付きます。なお、`video_codec` / `width` / `height` / `fps` / `crf` のいずれかを指定した場合はソースビットレートベースの概算が実際と大きく異なる可能性があるため、`warnings` に目安旨が付きます。

**エラー時の返り値**

```jsonc
{
  "ok": false,
  "error": {
    "code": "FILE_NOT_FOUND",
    "message": "タイムラインファイルが見つかりません: /path/to/timeline.otio",
    "hint": "ファイルパスを確認してください"
  }
}
```

主なエラーコード:

| コード | 意味 |
|---|---|
| `FILE_NOT_FOUND` | タイムライン / ソース / 出力先ディレクトリが存在しない |
| `INVALID_INPUT` | 不正な拡張子 / 既存出力で overwrite=false / 空タイムライン |
| `PATH_NOT_ALLOWED` | 出力パスが入力ソースと同じ |
| `UNSUPPORTED_OPERATION` | 映像なし / 複数ソース / Transition / video トラック 2 本以上 |
| `PROBE_FAILED` | FFprobe の解析失敗 |
| `SUBPROCESS_FAILED` | FFmpeg の終了コードが非ゼロ |
| `SUBPROCESS_TIMEOUT` | FFmpeg がタイムアウト（`max(300, 尺秒 × 10)` 秒） |
| `DEPENDENCY_MISSING` | ffmpeg / ffprobe が PATH にも環境変数にも見つからない |

---

### CLI（`clipwright-render`）

コマンドラインから直接実行できます。MCP ツールと同じロジックを共有します。

```bash
clipwright-render <timeline> <output> [オプション]
```

**引数**

```
clipwright-render <timeline> <output>
    [--dry-run]
    [--video-codec C]
    [--audio-codec C]
    [--width W --height H]
    [--fps F]
    [--crf N]
    [--overwrite]
```

**例: dry_run で計画を確認してからレンダリング**

```bash
# まず計画を確認
clipwright-render timeline.otio out.mp4 --dry-run

# 問題なければ実レンダリング
clipwright-render timeline.otio out.mp4 --video-codec libx264 --crf 23
```

**例: 解像度を指定してレンダリング**

```bash
clipwright-render timeline.otio out.mp4 --width 1280 --height 720 --fps 29.97
```

---

## テスト

### ユニットテスト（FFmpeg 不要）

```bash
uv run --package clipwright-render pytest clipwright-render/tests/ -m "not integration"
```

### integration テスト（FFmpeg 必須）

実際に FFmpeg を使用して単一ソースの連結・出力を検証するテストです。FFmpeg が用意されていない環境では自動的にスキップされます。

環境変数を設定してから実行してください。

```bash
export CLIPWRIGHT_FFMPEG=/path/to/ffmpeg
export CLIPWRIGHT_FFPROBE=/path/to/ffprobe

uv run --package clipwright-render pytest clipwright-render/tests/ -m integration
```

> integration テストは `CLIPWRIGHT_FFMPEG` / `CLIPWRIGHT_FFPROBE` が設定されていない場合は skip します。CI で実行する場合はこれらの環境変数を設定してください。

---

## ライセンス

ラッパー本体（このパッケージ）は **MIT** ライセンスです。

FFmpeg バイナリは同梱していないため、FFmpeg の LGPL v2.1 / GPL v2 に基づく再配布義務はこのパッケージには適用されません。
