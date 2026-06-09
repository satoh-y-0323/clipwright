# clipwright-render

OTIO タイムラインを FFmpeg で実体化する MCP ツール。

区間抽出・連結・トリムを一回の再エンコードで完結させる。

## 前提

- Python 3.11+
- `ffmpeg` および `ffprobe` が PATH 上に存在すること（同梱しない）

`ffmpeg` を PATH に追加できない環境では、環境変数 `CLIPWRIGHT_FFMPEG` / `CLIPWRIGHT_FFPROBE` にフルパスを設定してください。

## インストール

```bash
uv sync
```

## ライセンス

MIT
