# tests/fixtures

## whisper_sample.json

**ステータス: 仮説（実バイナリ未確認）**

`CLIPWRIGHT_WHISPER` と `CLIPWRIGHT_WHISPER_MODEL` が環境変数に設定されていなかったため、
実 whisper.cpp バイナリを叩かずに生成した仮説フィクスチャ。
architecture-report-20260610-221243.md の TR-AD-02 に基づく想定スキーマを用いた。

---

### 確認方法（env が揃ったとき）

```bash
# WAV 生成（16kHz mono libflite TTS）
ffmpeg -f lavfi -i "flite=text='hello world':voice=slt" \
  -ar 16000 -ac 1 -f wav /tmp/spike_test.wav

# whisper 実行（-oj で JSON 出力・-of でプレフィックス指定）
$CLIPWRIGHT_WHISPER -m $CLIPWRIGHT_WHISPER_MODEL \
  -f /tmp/spike_test.wav -oj -of /tmp/spike_test
# 生成ファイルを確認
ls /tmp/spike_test.json
cat /tmp/spike_test.json
```

実 JSON が取れたら `whisper_sample.json` を置き換え、このファイルを「**確定**」に更新し、
下記「仮説スキーマ」を「実測スキーマ」に上書きする。

---

### 仮説スキーマ（architecture TR-AD-02 ベース）

| フィールド | 仮説値 | 根拠 |
|---|---|---|
| バイナリ名 | `whisper-cli` | whisper.cpp 最新版の推奨名。旧版は `main` |
| `-oj` 出力ファイル名 | `<prefix>.json` | whisper.cpp -oj の一般的規則 |
| `transcription[].offsets.from` | ミリ秒（整数） | TR-AD-02 想定・whisper.cpp ソース確認済み |
| `transcription[].offsets.to` | ミリ秒（整数） | 同上 |
| `transcription[].text` | str（先頭に空白が入る場合あり） | whisper.cpp の一般的出力形式 |
| `transcription[].timestamps.from` | `"HH:MM:SS,mmm"` 文字列 | whisper.cpp の一般的出力形式 |
| `transcription[].timestamps.to` | `"HH:MM:SS,mmm"` 文字列 | 同上 |
| 言語自動検出フラグ | `-l auto` | whisper.cpp -l オプション・仮置き |
| `result.language` | 検出言語コード（例: `"en"`） | whisper.cpp の一般的出力形式 |

**WHISPER_BINARY_NAME 定数（仮置き）**: `whisper-cli`
**LANG_AUTO_FLAG 定数（仮置き）**: `-l auto`

---

### e2e 照合ゲート

e2e タスク（test_e2e.py）が実バイナリを叩いたとき、以下を照合する:

1. 実 JSON の `transcription[].offsets.from/to` がミリ秒整数であること
2. `transcription[].text` キーが存在すること
3. `-oj -of <prefix>` で生成ファイル名が `<prefix>.json` であること
4. 言語自動検出フラグ（`-l auto`）が受け入れられること（エラーにならないこと）
5. 実バイナリのファイル名が `WHISPER_BINARY_NAME` 定数と一致すること

差異があれば impl-contract / impl-transcribe への手戻りが必要。

---

### 注記

- `whisper_sample.json` の `"systeminfo"` フィールドは実バイナリが生成するフィールドではない可能性がある（仮説）。
- `model` / `params` / `result` フィールド群は実バイナリ出力に含まれる可能性があるが、`captions.py` は `transcription` 配列のみを参照するため、他フィールドの有無に依存しない設計。
- spike フィクスチャが「仮説」のままの場合、`confirm-contract` / `confirm-transcribe` の「契約面100%カバレッジ」は仮説 JSON に対する被覆であり、実スキーマの検証は e2e 照合まで確定しない（DC-GP-001-R）。
