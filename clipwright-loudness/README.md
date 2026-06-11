# clipwright-loudness

音量均一化検出 → OTIO タイムライン注記生成 MCP ツール。

## 概要

ffmpeg `loudnorm` / `volumedetect` フィルタで音声のラウドネス・ピーク音量を測定し、
loudness 指示（mode・target・measured）を timeline-level `metadata["clipwright"]["loudness"]` に書き込む。

検出のみ（OTIO 注記）を行い、実体化（ffmpeg フィルタ適用）は `clipwright-render` が一回だけ行う
（設計 M3: 検出と適用の分離）。

**正規化モード**:
- `loudnorm`（EBU R128 LUFS）: linear 二段方式。detect が `loudnorm print_format=json` で測定した
  `measured_*` パラメータを OTIO 注記に保存し、render が `loudnorm:linear=true` で正確な1パス適用を行う。
- `peak`（max dB 合わせ）: `volumedetect` で max_volume を測定し、target との差分ゲインを render で適用する。

**初版 render 対応状況**:
- `track` scope のみ対応（timeline 全体に単一の音量均一化を適用）。
- `per_clip` scope（各クリップ個別適用）は合体後に延期。

## 前提

- Python 3.11 以上
- **ffmpeg / ffprobe が `PATH` 上に存在するか、環境変数 `CLIPWRIGHT_FFMPEG` / `CLIPWRIGHT_FFPROBE` にフルパスが設定されていること。**

ffmpeg は PATH に直接追加するか、以下の環境変数で明示指定:

```bash
export CLIPWRIGHT_FFMPEG=/path/to/ffmpeg
export CLIPWRIGHT_FFPROBE=/path/to/ffprobe
```

## MCP ツール

`clipwright_detect_loudness`

### パラメータ

| 名前 | 型 | 既定値 | 説明 |
|---|---|---|---|
| `media` | `string` | 必須 | 入力メディアファイルパス（音声必須） |
| `output` | `string` | 必須 | 出力 OTIO タイムラインパス（`.otio`・media と同一ディレクトリ） |
| `options.mode` | `"loudnorm" \| "peak"` | `"loudnorm"` | 正規化モード |
| `options.target_i` | `float` | `-14.0` | loudnorm モード: 目標統合ラウドネス（LUFS・-70〜-5） |
| `options.target_tp` | `float` | `-1.0` | loudnorm モード: 目標トゥルーピーク（dBTP・-9〜0） |
| `options.target_lra` | `float` | `11.0` | loudnorm モード: 目標ラウドネスレンジ（LU・1〜50） |
| `options.target_peak_db` | `float` | `-1.0` | peak モード: 目標ピーク音量（dB・-60〜0） |
| `timeline` | `string \| null` | `null` | 既存 OTIO タイムラインパス（指定時は追記） |

## 依存関係

| パッケージ | 用途 |
|---|---|
| `clipwright` | 共通型・エンベロープ・エラー・process.run |
| `mcp[cli]` | MCP サーバー |
| `pydantic` | パラメータ検証 |

ffmpeg / ffprobe はライセンス独立のため別プロセス起動（PATH または環境変数経由）。

## loudnorm linear 二段方式

1. **detect（本ツール）**: `ffmpeg -i <media> -af loudnorm=I=-14:TP=-1:LRA=11:print_format=json -f null -`
   で measured_* パラメータを取得し OTIO 注記に保存。
2. **render（clipwright-render）**: `loudnorm=I=-14:TP=-1:LRA=11:measured_I=..:...:linear=true`
   で検出済みパラメータを使った線形適用1パスのみ実行（精度向上）。

## インストール・起動

uv workspace 内で:

```bash
uv run --package clipwright-loudness clipwright-loudness
```

または直接インストール:

```bash
uv add clipwright-loudness
clipwright-loudness
```
