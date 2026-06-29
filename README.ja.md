# Clipwright

> English version: [README.md](README.md).

FFmpeg/OTIO をラップする MCP サーバー群。映像編集ワークフローを AI エージェントから操作できるプリミティブを提供する。

## 前提: FFmpeg

Clipwright は ffprobe（ランタイム）と ffmpeg（テスト素材生成）を PATH 上に要求する。バイナリは同梱しない。

> **`clipwright-stabilize` は libvidstab 付きでビルドされた ffmpeg（`--enable-libvidstab`）が必須。**
> apt / brew / choco / WinGet の標準パッケージは libvidstab を含まない場合がある。
> libvidstab が存在しない場合、`clipwright_detect_shake` は `UNSUPPORTED_OPERATION` を返し、
> libvidstab 入りビルドの導入方法を案内する。

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

## 推奨ワークフロー

### 無音カット動画への字幕焼き込み

無音除去と字幕焼き込みは、どちらも頻出の編集操作だが**順序が重要**。以下の**クリーン連鎖**で断片化のない字幕を得られる。

1. **無音検出**で元素材から KEEP 範囲 OTIO を生成する:
   ```
   clipwright_detect_silence(media="source.mp4", output="silence.otio")
   ```
2. **カット動画を render** して OTIO を実ファイルに実体化する:
   ```
   clipwright_render(timeline="silence.otio", output="cut.mp4")
   ```
3. **カット済み動画を transcribe** する（元素材ではなく）。これにより cue のタイムスタンプがカット後のプログラム基準になる:
   ```
   clipwright_transcribe(media="cut.mp4", output="cut.otio")
   → artifacts: [{role:"timeline", path:"cut.otio"}, {role:"captions", path:"cut.srt"}, ...]
   ```
4. *(任意)* 行長が長い場合は **キャプション折り返し**を行う:
   ```
   clipwright_wrap_captions(input="cut.srt", output="wrapped.srt")
   ```
5. **字幕付きで再 render** する。timeline には step3 の transcription OTIO を使う:
   ```
   clipwright_render(timeline="cut.otio", output="final.mp4",
                     options={"subtitle": {"path": "cut.srt"}})  # または wrapped.srt
   ```

**この順序が重要な理由:**
元素材を先に transcribe すると、すべての cue が元尺のタイムスタンプに固定される。その後 `clipwright_render` で無音カットを適用したとき、カット境界をまたぐ cue は split または clip される。`retime_markers="auto"`（デフォルト）を使っても同様で、render はこの状態を検出して `fragmented by cuts` を含む warning を `warnings` に出力するが、cue テキスト自体が元尺に合わせて区切られているため render 段階での完全な解消はできない。カット済み動画を transcribe することでこの問題をそもそも回避できる。

---

### 単語同期カラオケ字幕

各単語が発話に合わせてハイライトされるカラオケ字幕を生成するには、`clipwright_transcribe` で `word_timestamps=true` を指定し、`clipwright_render` で `subtitle.karaoke=true` を使う。

1. **単語タイムスタンプ付きで transcribe** して単語単位 VTT artifact を取得する:
   ```
   clipwright_transcribe(media="clip.mp4", output="clip.otio", word_timestamps=true)
   → artifacts: [{role:"timeline"}, {role:"captions", path:"clip.srt"}, ...,
                 {role:"word_captions", path:"clip.words.vtt"}]
   ```
2. **カラオケモードで render** する（word-VTT パスを subtitle.path に指定）:
   ```
   clipwright_render(
     timeline="clip.otio",
     output="karaoke.mp4",
     options={"subtitle": {"path": "clip.words.vtt", "karaoke": true,
                           "highlight_color": "#FFFF00"}}
   )
   ```

スタイルパラメータ: `highlight_color`（デフォルト `#FFFF00`・黄色）・`chars_per_line`（デフォルト 42）・`max_lines`（デフォルト 2）。CWE-400: 50,000 語または 10,000 cue を超える入力は `INVALID_INPUT` を返す。`karaoke=false`（デフォルト）では既存のすべてのレンダリング呼び出しはバイト単位で同一。

> **wrap + karaoke 注記:** `clipwright_wrap_captions` のカラオケ折り返し対応は Phase 2 です。`transcribe → render` の直接チェーンはそれなしでも完全に機能します。

---

## 利用可能なツール

| パッケージ | MCP ツール | 説明 |
|------------|-----------|------|
| `clipwright`（コア） | `clipwright_inspect_media` | メディアファイルを probe し、コーデック / 尺 / ストリーム情報を返す |
| `clipwright`（コア） | `clipwright_init_project` | 空の OTIO タイムラインでプロジェクトディレクトリを初期化する |
| `clipwright`（コア） | `clipwright_read_timeline` | OTIO タイムラインファイルを読み込み、その構造を返す |
| `clipwright`（コア） | `clipwright_write_timeline` | OTIO タイムラインをディスクへ書き戻す |
| `clipwright-silence` | `clipwright_detect_silence` | FFmpeg `silencedetect` で無音区間を検出し OTIO マーカーを注記する |
| `clipwright-loudness` | `clipwright_measure_loudness` | FFmpeg で EBU R128 ラウドネス（積分 LUFS / トゥルーピーク）を測定する |
| `clipwright-noise` | `clipwright_reduce_noise` | FFmpeg `afftdn` のノイズ低減設定を OTIO タイムラインに注記する |
| `clipwright-transcribe` | `clipwright_transcribe` | whisper-cli で音声をテキスト化し、単語単位の OTIO マーカーを書き込む。CUDA / Metal ビルドの whisper.cpp を透過利用可（`CLIPWRIGHT_WHISPER` を GPU ビルドに向けるだけで GPU 動作）。`data.backend.device` と `data.realtime_factor` で実機デバイスと速度をランタイム確認できる。`word_timestamps=true` を指定すると単語単位 WebVTT artifact（`<stem>.words.vtt`・WebVTT inline timestamp `<HH:MM:SS.mmm>word` 形式）を追加出力し、OTIO マーカーに `metadata["clipwright"]["words"]` を付与する。これは `clipwright_render` のカラオケモードへの入力として使用する |
| `clipwright-bgm` | `clipwright_place_bgm` | BGM の配置注記（音量 / フェード / ダッキング）を OTIO タイムラインに書く |
| `clipwright-render` | `clipwright_render` | OTIO の編集オペレーション（トリム / 連結 / フィルタ / LinearTimeWarp 速度変換 / drawtext テキストオーバーレイ）を FFmpeg で出力メディアに実体化する。タイムラインに無音カットやスピード変換が含まれる場合、`.srt` 字幕キューと `text_overlay` マーカーをプログラム時間へ再タイミングする（デフォルト `retime_markers="auto"`）。再タイミング時は非破壊で `{output_stem}.retimed.srt` を出力する。`.vtt` / `.ass` およびマルチソースタイムラインは warning 付きでスキップする。ハードウェアエンコード（`hw_encoder`: none/auto/nvenc/amf/qsv/vaapi/videotoolbox）および GPU デコード（`hwaccel_decode`）をサポート。NVENC は開発機で動作確認済み。AMF / QSV / VAAPI / VideoToolbox は experimental（コミュニティ検証待ち）。**カラオケモード**: `subtitle.karaoke=true` と単語単位 WebVTT パス（`clipwright_transcribe(word_timestamps=true)` が出力する `<stem>.words.vtt`）を指定すると、ASS `\k` タグを用いた単語同期カラオケ字幕を焼き込む。スタイルオプション: `highlight_color`（デフォルト `#FFFF00`）・`chars_per_line`（デフォルト 42）・`max_lines`（デフォルト 2）。`karaoke=false`（デフォルト）では既存のすべてのレンダリング呼び出しは変更なし。 |
| `clipwright-speed` | `clipwright_set_speed` | OTIO の `LinearTimeWarp` でクリップに速度倍率を注記する。実体化は `clipwright-render` が行う |
| `clipwright-text` | `clipwright_add_text` | OTIO タイムラインにテキストオーバーレイ設定（drawtext）を注記する。映像への描画は `clipwright-render` が行う |
| `clipwright-wrap` | `clipwright_wrap_captions` | 字幕キュー（SRT/VTT）を行長制限内に収まるよう折り返す。CJK・Thai は BudouX フレーズ境界分割、空白区切り Latin 言語（`en` / `es` / `fr` / `de` / `it` / `pt` / `nl`）は単語境界での greedy word-wrap に対応。`language` パラメータで分割戦略を選択する。非破壊: 新しい字幕ファイルのみ書き出す。 |
| `clipwright-scene` | `clipwright_detect_scenes` | FFmpeg `scdet` または PySceneDetect（`backend='pyscenedetect'`）でショット境界を検出し OTIO マーカーを書く。0 境界の場合は具体的な閾値半減提案を返し、ffmpeg バックエンドでは pyscenedetect への切替を推奨する。PySceneDetect は `pip install scenedetect`（または `clipwright-scene[pyscenedetect]`）でインストール。PATH 上にない場合は `CLIPWRIGHT_SCENEDETECT` で実行ファイルパスを指定する |
| `clipwright-frames` | `clipwright_extract_frames` | 指定時刻 / シーン境界 / 固定間隔で動画から静止画を抽出し、画像・OTIO マーカー・JSON マニフェストを出力する。`mode="scene"` では `scene_sample` パラメータでショット区間内のサンプリング位置を制御する: `"midpoint"` *（デフォルト）* — 各ショット区間の中点で 1 枚（N 境界に対し N+1 枚。コンタクトシート用途に最適）/ `"start"` — 各ショット区間の先頭で 1 枚（同 N+1 枚）/ `"boundary"` — 各 `scene_boundary` マーカー位置で 1 枚（N 枚。0.2.0 以前の動作を完全再現）。境界が 0 個の場合、`midpoint`/`start` はクリップ全体から 1 枚抽出し、`boundary` は warning を出して 0 枚を返す |
| `clipwright-color` | `clipwright_detect_color` | FFmpeg `signalstats` で平均輝度を測定し、`eq` カラー補正ディレクティブを OTIO タイムラインのメタデータに書き込む。補正は `clipwright-render` が一括レンダリングパスで適用する |
| `clipwright-stabilize` | `clipwright_detect_shake` | FFmpeg `vidstabdetect`（libvidstab 必須）でカメラ手ブレを解析し、`.trf` モーション解析ファイルと stabilize ディレクティブを OTIO タイムラインのメタデータに書き込む。`clipwright-render` が一括レンダリングパスで `vidstabtransform` として適用する |
| `clipwright-trim` | `clipwright_trim` | 明示した keep/drop 時間範囲から kept-range の OTIO タイムラインを生成する（オプション省略時はクリップ全体をパススルー）。`clipwright-render` がそのまま連結する。「どの区間を残すか」を指定する最も基本的なプリミティブ |
| `clipwright-reframe` | `clipwright_reframe` | リフレーム指令（目標解像度 / フィットモード / アンカー）を OTIO タイムラインのメタデータに注記する。実体化は `clipwright-render` が FFmpeg フィルタチェーンとして一括適用する。4 つのフィットモード: `crop`（スケールしてクロップ）/ `pad`（スケールしてソリッドカラーでレターボックス/ピラーボックス、`pad_color` で色指定可）/ `blur_pad`（ぼかした背景の上にフォアグラウンドを重ね合わせ；16:9 → 9:16 縦型 Shorts/Reels で人気）/ `track`（コンテンツ追従の被写体トラッキング — 動き重心を検出して正規化キーフレーム track を書き込み、`clipwright-render` が被写体に追従する時間変化クロップとして実体化する。numpy のオプション extra `clipwright-reframe[track]` が必要で、未導入時は静的な中央クロップにフォールバックし warning を出す）。`target_w` / `target_h` は偶数 (2–7680)。`anchor` は配置アンカー（9 方向、デフォルト `center`） |
| `clipwright-sequence` | `clipwright_build_sequence` | 複数のソースメディアファイルをひとつのマルチソース OTIO タイムライン（V1 ビデオトラック）に組み立て、`clipwright-render` で連結できる形にする。各クリップに `start_sec` / `end_sec` でサブ範囲を指定できる（省略時はソース全体）。ソースはどこでも readable なら可: 出力ディレクトリ配下のソースは相対 POSIX パスで OTIO に格納（移植性確保）、外部ソースは絶対パスで格納し `clipwright-render` が ADR-PP-1 絶対参照エスケープハッチで受け取る。symlink は全パス成分で拒否（ADR-PP-2）。非破壊: 入力メディアは変更しない。 |
| `clipwright-overlay` | `clipwright_add_overlay` | OTIO タイムラインに画像オーバーレイ（PNG/JPEG ロゴ・ウォーターマーク・ロワーサード）を注記する。位置・スケール・不透明度・時間範囲を指定し、FFmpeg `fade:alpha=1` フィルタチェーンでフェードイン/フェードアウトをサポート。画像ファイルはどこでも readable なら可: 出力タイムラインの親ディレクトリ配下の画像は相対 POSIX パスで OTIO に格納（移植性確保）、外部画像は絶対パスで格納し `clipwright-render` が ADR-PP-1 絶対参照エスケープハッチで受け取る。symlink は拒否。`clipwright-render` が実体化時に画像を追加 `-i` として入力し、`scale/format=rgba/colorchannelmixer/fade/overlay` フィルタチェーンを filtergraph に挿入する（drawtext の後段で最前面に合成）。非破壊: 入力メディアおよびタイムラインは変更しない。 |
| `clipwright-transition` | `clipwright_add_transition` | 隣接するクリップ境界にクロスフェード/ディゾルブ（映像は FFmpeg `xfade`、音声は `acrossfade`）を注記する。`options.uniform`（全境界に適用する TransitionSpec: type と duration_sec）または `options.per_boundary`（境界ごとの TransitionSpec リスト）を指定する。非破壊: 新しい OTIO ファイルのみ書き出す。実体化は `clipwright-render` が行う。v1 制約: 一部の境界のみを指定する歯抜け per_boundary は UNSUPPORTED_OPERATION。uniform モードまたは全境界を指定した per_boundary は正式サポート。 |

---

## MCP クライアントへの登録

各 clipwright ツールは独立した MCP サーバー。MCP クライアントの設定（`.mcp.json` / `claude_desktop_config.json`）に登録する:

```json
{
  "mcpServers": {
    "clipwright": {
      "command": "clipwright-mcp",
      "env": {
        "CLIPWRIGHT_FFMPEG": "/path/to/ffmpeg",
        "CLIPWRIGHT_FFPROBE": "/path/to/ffprobe"
      }
    },
    "clipwright-render": {
      "command": "clipwright-render",
      "env": {
        "CLIPWRIGHT_FFMPEG": "/path/to/ffmpeg"
      }
    },
    "clipwright-bgm": {
      "command": "clipwright-bgm"
    },
    "clipwright-scene": {
      "command": "clipwright-scene",
      "env": {
        "CLIPWRIGHT_FFMPEG": "/path/to/ffmpeg",
        "CLIPWRIGHT_FFPROBE": "/path/to/ffprobe"
      }
    },
    "clipwright-frames": {
      "command": "clipwright-frames",
      "env": {
        "CLIPWRIGHT_FFMPEG": "/path/to/ffmpeg",
        "CLIPWRIGHT_FFPROBE": "/path/to/ffprobe"
      }
    },
    "clipwright-speed": {
      "command": "clipwright-speed"
    },
    "clipwright-text": {
      "command": "clipwright-text"
    },
    "clipwright-color": {
      "command": "clipwright-color",
      "env": {
        "CLIPWRIGHT_FFMPEG": "/path/to/ffmpeg",
        "CLIPWRIGHT_FFPROBE": "/path/to/ffprobe"
      }
    },
    "clipwright-stabilize": {
      "command": "clipwright-stabilize",
      "env": {
        "CLIPWRIGHT_FFMPEG": "/path/to/ffmpeg",
        "CLIPWRIGHT_FFPROBE": "/path/to/ffprobe"
      }
    },
    "clipwright-trim": {
      "command": "clipwright-trim",
      "env": {
        "CLIPWRIGHT_FFPROBE": "/path/to/ffprobe"
      }
    },
    "clipwright-reframe": {
      "command": "clipwright-reframe"
    },
    "clipwright-sequence": {
      "command": "clipwright-sequence",
      "env": {
        "CLIPWRIGHT_FFPROBE": "/path/to/ffprobe"
      }
    },
    "clipwright-overlay": {
      "command": "clipwright-overlay"
    },
    "clipwright-transition": {
      "command": "clipwright-transition"
    }
  }
}
```

> 注: `clipwright-transition` は `CLIPWRIGHT_FFPROBE` / `CLIPWRIGHT_FFMPEG` を必要としない（pure OTIO 注記ツールのため、注記時に ffprobe / ffmpeg を呼び出さない）。

> 注: `clipwright-scene` はデフォルトの ffmpeg バックエンドで `CLIPWRIGHT_FFMPEG` を必要とする。`backend='pyscenedetect'` を使う場合は `scenedetect` CLI のインストール（`pip install scenedetect`）または `CLIPWRIGHT_SCENEDETECT` でのパス指定が必要。オプション extra `clipwright-scene[pyscenedetect]` を指定すると PySceneDetect が自動インストールされる。

> 注: `clipwright-sequence` は `CLIPWRIGHT_FFPROBE` を必要とする。`inspect_media` が各ソースの尺とビデオストリームを probe してから OTIO タイムラインを構築するためである。

> 注: `clipwright-overlay` は注記時に FFmpeg を使用しない（subprocess-free）。FFmpeg は `clipwright-render` がオーバーレイを実体化するときにのみ呼び出される。

> 注: `clipwright-frames` は `CLIPWRIGHT_FFMPEG`（フレーム抽出）と `CLIPWRIGHT_FFPROBE`（`inspect_media` 経由のビデオストリーム検出・尺取得）の両方を使うため、両変数を設定する必要がある。

> 注: `clipwright-color` は `CLIPWRIGHT_FFPROBE` を必要とする。`inspect_media` が ffprobe を使用して輝度測定前にビデオストリームの存在を検証するためである。

> 注: `clipwright-stabilize` は `--enable-libvidstab` 付きでコンパイルされた `CLIPWRIGHT_FFMPEG` を必要とする。`inspect_media` が vidstabdetect 実行前にビデオストリームの存在を検証するため、`CLIPWRIGHT_FFPROBE` も両方設定すること。

ffmpeg が `PATH` 上にない場合は `CLIPWRIGHT_FFMPEG` / `CLIPWRIGHT_FFPROBE` 環境変数を設定する。

---

## ライセンス

MIT — 詳細は [LICENSE](LICENSE) を参照。
