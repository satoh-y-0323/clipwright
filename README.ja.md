# Clipwright

> English version: [README.md](README.md).

FFmpeg/OTIO をラップする MCP サーバー群。映像編集ワークフローを AI エージェントから操作できるプリミティブを提供する。

## 前提: FFmpeg

Clipwright は ffprobe（ランタイム）と ffmpeg（テスト素材生成）を PATH 上に要求する。バイナリは同梱しない。

### インストール（Windows / WinGet）

```bash
winget install Gyan.FFmpeg
```

**PATH への反映にはシェルの再起動が必要。** Claude Code から使う場合はアプリ再起動後に PATH が有効になる。

再起動を待てない場合は環境変数で直接指定する:

```bash
# runtime: ffprobe のみ使用
export CLIPWRIGHT_FFPROBE="C:/Users/<user>/AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-8.1.1-full_build/bin/ffprobe.exe"

# test: ffmpeg + ffprobe 両方（テスト素材生成用）
export CLIPWRIGHT_FFMPEG="C:/Users/<user>/AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-8.1.1-full_build/bin/ffmpeg.exe"
```

### 環境変数の用途区別

| 変数 | 用途 |
|------|------|
| `CLIPWRIGHT_FFPROBE` | **ランタイム専用**。`clipwright_inspect_media` ツールが使用する |
| `CLIPWRIGHT_FFMPEG` | **テスト専用**。`conftest.py` の `sample_media` フィクスチャが使用する |

> ランタイムは ffprobe のみ依存する。ffmpeg はテスト素材生成にのみ使用（設計: [DC-AM-008]）。

---

## 前提: clipwright-transcribe（whisper-cli）

`clipwright-transcribe` は **whisper.cpp** バイナリ（`whisper-cli`）と ggml モデルファイルを必要とする。pip ではインストールされないため別途取得すること。

### whisper-cli バイナリ

| プラットフォーム | 取得方法 |
|---|---|
| **Windows** | [whisper.cpp Releases](https://github.com/ggerganov/whisper.cpp/releases) からビルド済みバイナリをダウンロード → `whisper-bin-x64.zip`（CPU）または `whisper-cublas-*-bin-x64.zip`（CUDA）。展開して `whisper-cli.exe` を PATH の通ったディレクトリに配置するか、`CLIPWRIGHT_WHISPER` 環境変数で指定する。 |
| **macOS** | `brew install whisper-cpp` — `whisper-cli` が PATH に自動登録される。 |
| **Linux** | ソースからビルド: `git clone https://github.com/ggerganov/whisper.cpp && cd whisper.cpp && cmake -B build && cmake --build build -j --config Release` — バイナリは `build/bin/whisper-cli` に生成される。 |

```bash
# whisper-cli が PATH 上にない場合はフルパスで指定する
export CLIPWRIGHT_WHISPER=/path/to/whisper-cli
```

### ggml モデルファイル

[Hugging Face](https://huggingface.co/ggerganov/whisper.cpp) からモデル（例: `ggml-base.bin`）をダウンロードする。

```bash
export CLIPWRIGHT_WHISPER_MODEL=/path/to/ggml-base.bin
```

`CLIPWRIGHT_WHISPER` の指定もなく PATH 上にも `whisper-cli` が見つからない場合、`clipwright_transcribe` ツールは `DEPENDENCY_MISSING` を返し、統合テストは自動的にスキップされる。

---

## 開発環境のセットアップ

```bash
# 依存インストール
uv sync --dev

# テスト実行（カバレッジ付き）
uv run pytest --cov=clipwright --cov-report=term-missing

# lint / format
uv run ruff check src tests
uv run ruff format src tests

# 型検査
uv run mypy src
```

### 統合テストの前提条件

統合テスト（ffprobe/ffmpeg を実際に呼び出すテスト）を実行するには、ffmpeg / ffprobe が PATH 上に存在するか、または以下の環境変数を設定すること。

```bash
# ffprobe のパスを指定（ランタイムおよび統合テストで使用）
export CLIPWRIGHT_FFPROBE="/path/to/ffprobe"

# ffmpeg のパスを指定（テスト素材生成に使用）
export CLIPWRIGHT_FFMPEG="/path/to/ffmpeg"
```

PATH に ffmpeg / ffprobe が登録済みであれば環境変数の設定は不要。いずれも見つからない場合、対象の統合テストは自動的にスキップされる。

---

## 開発メモ: MCP パッケージ

### 採用パッケージ

**公式 MCP Python SDK**（`mcp[cli]`）を採用（ADR-5 確定）。

```
mcp[cli]>=1.27.2
```

`from mcp.server.fastmcp import FastMCP` で import 可能。Python 3.11 / Windows で動作確認済み。

### annotations の記法（採用版）

```python
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("clipwright")

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_inspect_media(path: str) -> dict:
    """メディアファイルを probe して情報を返す。"""
    ...
```

`ToolAnnotations` のフィールド: `title`, `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`

### outputSchema / structured_output

`mcp.tool(structured_output=True)` を指定すると Pydantic モデルの戻り値が JSON Schema として outputSchema に反映される。

```python
from pydantic import BaseModel

class MediaResult(BaseModel):
    ok: bool
    summary: str

@mcp.tool(structured_output=True)
def clipwright_inspect_media(path: str) -> MediaResult:
    ...
```

---

## MCP Inspector 疎通手順

MCP Inspector（`@modelcontextprotocol/inspector`）で server を手動確認する方法。

### 準備（Node.js が必要）

```bash
# Node.js がインストールされていることを確認
node --version
npx --version
```

### server の起動と接続

```bash
# MCP Inspector を起動し、stdio 経由で server を接続する
npx @modelcontextprotocol/inspector uv run python -m clipwright.server
```

ブラウザで `http://localhost:5173` が自動的に開く（または手動でアクセス）。

Inspector 上でツール一覧（`clipwright_init_project` / `clipwright_inspect_media` / `clipwright_read_timeline` / `clipwright_write_timeline`）が表示され、各ツールを手動実行できる。

### 期待する動作

- ツール一覧に 4 ツールが表示される
- `clipwright_inspect_media` に存在しないパスを渡すと `ok=false` のエラーエンベロープが返る
- ffprobe が PATH / 環境変数に設定されていない場合は `DEPENDENCY_MISSING` エラーが返る

---

## アーキテクチャ概要

```
src/clipwright/
  __init__.py       # バージョン定義
  schemas.py        # 共通 Pydantic 型（契約面）
  envelope.py       # 返り値エンベロープ + エラー整形
  errors.py         # エラーコード + ClipwrightError 例外
  process.py        # サブプロセスランナー（shell=False / timeout 必須）
  media.py          # ffprobe ラッパー
  otio_utils.py     # OTIO ヘルパー
  operations.py     # 宣言的編集オペレーション型 + 適用ロジック
  project.py        # プロジェクトディレクトリ管理
  server.py         # FastMCP サーバー（4 ツール公開）
```

依存方向: `schemas / envelope / errors` (契約面) → `process / media / otio_utils / project` → `operations` → `server`

詳細は [docs/clipwright-spec.md](docs/clipwright-spec.md) を参照。

---

## ライセンス

MIT — 詳細は [LICENSE](LICENSE) を参照。
