# Clipwright ツール作者コントラクト（CONVENTIONS）— ドラフト骨子

> ステータス: **ドラフト（骨子）**。`docs/clipwright-spec.md` 第6章を「新しく道具を足す人（人間 / AI）」向けに独立させた公開コントラクトの叩き台。
> 確定後に root `CONVENTIONS.md` へ昇格する想定（spec §12）。

## 0. これは何か

- 読者は **Clipwright スイートに新しいツールを足したい人**（外部コントリビュータ含む）。
- ここに書かれた **MUST だけ** 守れば「Clipwright のツール」として既存スイートに乗り、AI から他ツールと一貫して呼べる。
- 設計思想の背景は `clipwright-spec.md`（§2 設計原則・§6 規約）を参照。本書は「何を守れば乗るか」を最短で示す。
- **方針**: 縛りは"最小核（MUST）"に絞る。それ以外は"正確に記述してほしいこと（SHOULD）"であって、参入ゲートにはしない。

---

## 1. 最小核（MUST）— これだけは必須

守らないと「スイートのツール」として成立しない、本当に少数の核。**5つだけ。**

### M1. 命名
- MCP ツール名: `clipwright_<action>`（スネークケース・動詞起点）。例: `clipwright_render`, `clipwright_detect_silence`。
- パッケージ / CLI 名: `clipwright-<tool>`（全小文字）。
- 一貫した接頭辞で、AI が正しい道具を選べるようにする。

### M2. 返り値エンベロープ
- 成功: `{ ok: true, summary, data, artifacts, warnings }`。
- 失敗: `{ ok: false, error: { code, message, hint } }`。
- `message` = 何が起きたか / `hint` = 次の一手（具体策）。
- コア共有ライブラリ `clipwright.envelope`（`ok_result` / `error_result`）を使えば自動で満たせる。
- `error.code` は §3 の許可リスト（共通 `ErrorCode`）から選ぶ。

### M3. 検出と適用の分離（detect / render split）
- detect / inspect 系ツールは **メディアを書き換えない**。結果は OTIO 注記・字幕・解析データとして返す。
- メディアの実体化（再エンコード・連結など）は **`clipwright-render` 一本** に委ねる。
- 自前ツールでメディア出力を焼かない（新フォーマットの動画を吐くツールを増やさない）。

### M4. 外部 OSS はサブプロセスで呼ぶ
- 外部 OSS（ffmpeg / whisper / budoux / VAD 等）は **別プロセス** で起動する。ライブラリとして自分のコードに import リンクしない。
- 目的: **ライセンス独立**（GPL/LGPL でも伝染しない）＋疎結合。
- Python ライブラリしかない OSS でも、**薄い CLI シム**（`wrap_cli.py` / `vad_cli.py` が実例）で包めば OK。OSS の種類は狭めない。
- → これは"縛り"であると同時に **使える OSS を広げる仕組み**（FFmpeg すら使える理由）。

### M5. 入出力と非破壊
- 入力は **既存ファイルのパス** で受け取る（バイト列は受け取らない）。
- 出力は **新規生成**（`outputs/` / `artifacts/`）し、パスを `artifacts` で返す。
- **元素材・OTIO・入力ファイルを上書きしない**（`output == input` は拒否する）。

> MUST はこの5つで全部。これ以外で実装をブロックされることはない。

---

## 2. 正確に記述してほしいこと（SHOULD / 説明であってゲートではない）

値を強制はしないが、AI と他ツールのために**正確に書く**。

- **annotations を正確に付ける**: detect/inspect 系は `readOnlyHint:true` / `destructiveHint:false`、render は `readOnlyHint:false`。
  - `openWorldHint` は **そのツールの実態に合わせて正直に**。ローカル決定論なら `false`、ネット/外部 API に触れるなら `true`。**特定値を強制しない**（→ §3）。
- **summary は判断に足る1〜2文**。件数・尺・最大値など。"最小限"にしない。巨大な明細（全カットリスト等）は `data` に詰めず `artifacts` のファイルへ逃がす。
- **OTIO メタデータは `metadata["clipwright"]` 名前空間** に置く。時間は `opentime`（RationalTime / TimeRange）で扱い、秒 float で持ち回らない。
- **サブプロセス規律の詳細**: 引数配列で実行（`shell=False`）・`timeout` 必須・stderr 収集・終了コード検査・失敗は M2 のエラーへ変換。コア `clipwright.process.run` がこれを満たす。
- **共通型を再利用**: `MediaRef` / `TimeRange` / `Artifact` / `ToolResult`（`clipwright.schemas`）。ツールごとに再定義しない。
- **依存不在は親切に**: ffmpeg 等が無ければ `DEPENDENCY_MISSING` ＋導入手順を `hint` に。

---

## 3. エラーコード（ErrorCode 許可リスト）

M2 の `error.code` は下記の共通 `ErrorCode`（コア `clipwright.errors.ErrorCode`）から選ぶ。**ツール固有の私的コードを増やさず、まず既存で表現できないか検討する**（増やす場合はコアに追加して全ツール共有にする）。

| code | 意味 | 主な用途 |
|---|---|---|
| `INVALID_INPUT` | 引数バリデーション失敗 | 拡張子不一致・パラメータ範囲外・不正フォーマット |
| `FILE_NOT_FOUND` | 入力パスにファイルが存在しない | 入力素材・OTIO の不在（message は basename のみ推奨） |
| `PATH_NOT_ALLOWED` | パス検証失敗 | パストラバーサル・許可ディレクトリ外 |
| `DEPENDENCY_MISSING` | 外部ツール/依存が見つからない | ffmpeg/ffprobe/OSS 未インストール（hint に導入手順） |
| `SUBPROCESS_FAILED` | 外部プロセスが非ゼロ終了 | ffmpeg/whisper 等の失敗 |
| `SUBPROCESS_TIMEOUT` | 外部プロセスがタイムアウト | timeout 超過 |
| `PROBE_FAILED` | ffprobe 出力のパース失敗 | メディア解析の異常 |
| `OTIO_ERROR` | OTIO の読み書き/パース失敗 | timeline.otio の破損・不整合 |
| `PROJECT_NOT_FOUND` | clipwright.json が見つからない | プロジェクト未初期化 |
| `PROJECT_EXISTS` | init 先に既存プロジェクト | 二重初期化の防止 |
| `TRACK_NOT_FOUND` | track 指定がトラック総数超過 | timeline 操作の範囲外 |
| `UNSUPPORTED_OPERATION` | 未知/未対応のオペレーション種別 | write_timeline の未知 op 等 |
| `INTERNAL` | 想定外の内部エラー | 汎用 message・スタックは stderr/ログのみ・hint に「再現条件を添えて報告」 |

運用ルール（SHOULD）:
- **入力起因は `INVALID_INPUT` / `FILE_NOT_FOUND` / `PATH_NOT_ALLOWED` を使い分ける**（AI が「直せる入力ミス」か「環境問題」かを判別できる）。
- 外部 OSS 由来の失敗は `SUBPROCESS_FAILED` / `SUBPROCESS_TIMEOUT` に正規化し、**stderr 全文を `message` に載せない**（秘密・パス露出防止）。
- **ネット接続ツール（§4）の到達不能・タイムアウトは、当面 §3 の既存コードで表現する（YAGNI — 現状ネット接続ツールは0個）**。専用コードを先回りで増やさず、次のように正規化する:
  - 接続失敗・HTTP エラー・到達不能 → `SUBPROCESS_FAILED`（外部 API も CLI シム＝サブプロセス経由なら自然に該当する）。
  - タイムアウト → `SUBPROCESS_TIMEOUT`。
  - 認証情報・API キー不備など「環境が整っていない」系 → `DEPENDENCY_MISSING`（hint に設定手順を書く）。
  - `NETWORK_ERROR` 等の専用コードは、**最初のネット接続ツールを実装する PR で必要性が実証された時点**でコア `ErrorCode` に追加し、全ツール共有にする（→ §4）。それまで `errors.py` は変更しない。
- 既存で表現できないときだけコア `ErrorCode` に追加し、全ツールで共有する。

---

## 4. ネット接続・オンラインツールの扱い（明示的に許容）

- **Clipwright はローカル限定ではない。** クラウド文字起こし・オンライン翻訳テロップなど、ネット/外部 API を使うツールを足してよい。
- その場合は **`openWorldHint: true`** で正直に表示する（AI がコスト・非決定性・到達性を判断できるように）。
- 追加で配慮すること（SHOULD）:
  - 認証情報・URL・パスなど秘密や入力値を `summary` / `data` / エラー `message` に露出させない。
  - タイムアウトと失敗時の `hint`（再試行・オフライン代替の有無）を用意する。
  - 可能ならオフライン代替バックエンドを併設し、パラメータで切替（例: silence の silencedetect / VAD）。
  - **エラーコードは当面 §3 の既存コードで表現する（YAGNI）**: 到達不能・HTTP エラーは `SUBPROCESS_FAILED`、タイムアウトは `SUBPROCESS_TIMEOUT`、認証・API キー不備は `DEPENDENCY_MISSING` に正規化する。`NETWORK_ERROR` 等の専用コードは、最初のネット接続ツールを実装する際に必要性が実証されてからコア `ErrorCode` へ追加する（先回りで `errors.py` に足さない）。
- **M4 の射程に注意**: M4 は「OSS をライブラリリンクしない」話。外部 **API 呼び出し**（HTTP）は OSS リンクではないので M4 の対象外。ネットツールは M1/M2/M3/M5 ＋ `openWorldHint:true` を満たせばよい。

---

## 5. 新ツールの作り方（scaffold 指針）

- **既存ツールを雛形にする**: `clipwright-silence` / `clipwright-transcribe` / `clipwright-wrap` が同型 scaffold。OSS を包む薄い CLI が要るなら `vad_cli.py` / `wrap_cli.py` をコピー元にする。
- **パッケージ構成（src layout）**:
  ```
  clipwright-<tool>/
    pyproject.toml          # license = MIT/Apache-2.0・clipwright を依存に
    src/clipwright_<tool>/
      __init__.py
      <tool>.py             # オーケストレーション層（検証→OSS起動→OTIO/字幕正規化）
      <tool>_cli.py         # （必要なら）OSS を包む薄いサブプロセス CLI
      schemas.py            # ツール固有の入力 Pydantic（共通型は clipwright.schemas 再利用）
      server.py             # FastMCP @mcp.tool + annotations + stdio 起動
    tests/
  ```
- **検証フロー**: `ruff format` / `ruff check` / `mypy` / `pytest` を通す。**契約面（schemas / 返り値整形）は実質100%**。最後に MCP Inspector で疎通確認。
- **トランスポート**: stdio 既定（`mcp.run(transport="stdio")`）。

---

## 6. PR 前セルフチェックリスト

**MUST（5項目・必須）**
- [ ] M1 ツール名 `clipwright_<action>` / パッケージ `clipwright-<tool>`
- [ ] M2 返り値が `{ok,summary,data,artifacts,warnings}` ／失敗が `{ok:false,error:{code,message,hint}}`
- [ ] M3 detect/inspect はメディア非改変（実体化は render に委譲）
- [ ] M4 外部 OSS は別プロセス（ライブラリリンクなし）
- [ ] M5 入力はパス・非破壊・出力は新規生成（`output==input` 拒否）

**SHOULD（推奨）**
- [ ] annotations を実態どおり付与（ネット接続なら `openWorldHint:true`）
- [ ] summary は判断に足る1〜2文・巨大明細は artifacts へ
- [ ] OTIO は `metadata["clipwright"]` ＋ opentime
- [ ] サブプロセス規律（引数配列 / timeout / stderr / 終了コード）
- [ ] 共通型 `MediaRef`/`TimeRange`/`Artifact`/`ToolResult` 再利用
- [ ] ruff / mypy / pytest / MCP Inspector 疎通

---

## 7. 参照・未確定事項（ドラフト段階）

- 出典: `docs/clipwright-spec.md` §2（設計原則）/ §4（OTIO）/ §6（規約）/ §9（ライセンス）。
- **TODO（確定前に詰める）**:
  - [ ] 配置: root `CONVENTIONS.md` へ昇格するか docs/ 据え置きか。
  - [x] `ErrorCode` の許可リストを一覧化して公開 → §3 に記載（全13コード）。
  - [x] ネット接続ツール用のエラーコード方針を決定 → §3 運用ルール末尾・§4 に記載。**YAGNI: 当面 `SUBPROCESS_FAILED` / `SUBPROCESS_TIMEOUT` / `DEPENDENCY_MISSING` で代用し、最初のネットツール実装時に専用コードを検討。`errors.py` は変更しない。**
  - [ ] eval（§11: AI が実タスクを解けるかの評価）をコントラクトの一部にするか、別ガイドにするか。
  - [ ] 外部コントリビュータ向けに最小 scaffold をテンプレート化（cookiecutter 等）するか。
