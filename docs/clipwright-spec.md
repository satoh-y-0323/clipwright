# Clipwright — 設計・要件・規約スペック

> AIに操作させることを前提とした、単機能・疎結合な動画編集ツール群（MCPスイート）。
> このドキュメントは Claude Code に渡して、まず **コア（clipwright）** と **clipwright-render** を実装するための設計・規約をまとめたものです。

---

## 0. このドキュメントの使い方

- まず「1. プロジェクト概要」「2. 設計原則」で思想を固定する。これは後から動かさない前提（=規約）。
- 「6. 規約（コントラクト）」が最重要。すべてのツール（コア・render・将来のツール）はここに従う。
- 当面の実装対象は **コア + clipwright-render** のみ。detect系（silence/transcribe など）は規約に乗せる前提で後続。
- 識別子・コード・スキーマは英語、説明は日本語で書いている。

---

## 1. プロジェクト概要

**Clipwright** は、人間がGUIで操作することを想定せず、**AIエージェントが操作者になる**ことを前提に設計された動画編集ツールのスイート。

性格を一言で言うと「職人の道具箱（clipwright）に、単機能の道具（clipwright-render, clipwright-silence, clipwright-transcribe …）が名前空間で収まっている」。各道具は Unix 哲学に従い、ひとつのことだけをうまくやる。

### 立ち位置
- **プラットフォームではなくツール。** 統合UIや課金基盤を作るのが目的ではない。AIから呼ばれる小さな道具を量産し、共通規約で束ねる。
- **実装本体は持たない（薄いラッパー）。** 文字起こし・無音検出・エンコード等の中身は、評判が良く更新も活発な既存OSS（FFmpeg, whisper.cpp/faster-whisper, auto-editor 等）に任せる。Clipwright が作るのは「規約とインターフェース」。
- **AIが背骨を通じて編集を組み立てる。** 編集の中間表現には業界標準の **OpenTimelineIO (OTIO)** を採用し、これを全ツール共通言語とする。

### 名前空間
- `clipwright` … コア（共有ライブラリ＋プリミティブMCP）
- `clipwright-render` … OTIO を実体の動画に焼くツール（最初に作る2つ目）
- `clipwright-transcribe` / `clipwright-silence` / `clipwright-noise` … 将来の detect 系ツール（規約に乗せて追加）

---

## 2. 設計原則（動かさない前提）

1. **AIファースト / GUIレス。** 入力（素材＋指示）と出力（成果物）だけが存在する。人間用タイムラインUIは作らない。
2. **単一責任。** 1ツール = 1機能。多機能な「全部入りサーバー」にしない。
3. **薄いラッパー / 厚いアダプタ。** MCPのプロトコル面は薄く保つ。各OSSのネイティブ出力を OTIO に正規化するアダプタ層こそが本体の仕事であり、ここは薄くできないと認識する。
4. **サブプロセス疎結合。** 外部OSS（ffmpeg/whisper/auto-editor 等）は必ず**別プロセス**として呼ぶ。ライブラリとして自分のバイナリにリンクしない。これは(a)ライセンス独立を保つため、(b)疎結合という思想のため、の両方。
5. **検出と適用の分離（detect / render split）。** 解析系ツールはメディアを書き換えず OTIO 上の注記（マーカー/カット候補）を返すだけ。実体化は `clipwright-render` 一本がまとめて一回だけ行う。これでエージェントのループが安く・速く・非破壊になる。
6. **ファイルパス受け渡し。** ツール間でメディアのバイト列を（AIのコンテキストを経由して）やり取りしない。入力はパスで受け取り、出力もパス＋メタデータで返す。重いデータはローカルディスクに留める。
7. **軽いが十分なコンテキスト。** 返り値は「人間/AIが読める短いサマリ」＋「構造化データ」＋「完全な成果物（OTIO等）へのパス」。"最小限"ではなく"判断に必要なだけ"。AIはまずサマリで判断し、必要なときだけ詳細を取りに行く。
8. **可搬フォーマット。** 字幕は SRT/VTT/ASS、編集判断は OTIO。独自フォーマットを新規発明しない。

---

## 3. アーキテクチャ

### 3.1 全体像

```
AI エージェント (Claude Code など)
        │  (MCP / stdio)
        ▼
┌───────────────────────────────────────────────┐
│ clipwright ツール群（各々が独立した stdio MCP） │
│                                                 │
│  clipwright (core)      … project / timeline /  │
│                            media inspect 等の   │
│                            プリミティブ          │
│  clipwright-render      … OTIO → 実体動画(FFmpeg)│
│  clipwright-silence     … 無音検出 → OTIOに注記 │ (後続)
│  clipwright-transcribe  … 文字起こし → 字幕+OTIO│ (後続)
└───────────────────────────────────────────────┘
        │ 全ツールが共有する規約
        ▼
  共有 IR: OTIO タイムライン（プロジェクトの「背骨」）
  共有ディスク: clipwright プロジェクトディレクトリ
        │ 各ツールは内部で別プロセスとして呼ぶ
        ▼
  外部 OSS: ffmpeg / ffprobe / whisper.cpp / auto-editor …
```

### 3.2 コンポーネントの役割分担

- **clipwright (core)** は2つの顔を持つ。
  - **共有ライブラリ**（各ツールが `import` する）: OTIO 読み書き、メディア probe、返り値エンベロープ、エラー整形、サブプロセス実行、プロジェクト管理のユーティリティと型定義。各ツールはこれを土台にする。
  - **プリミティブ MCP サーバー**: プロジェクト初期化・メディア検査・タイムライン読み書きといった基礎操作をAIに公開する。
- **clipwright-render** は単独の MCP/CLI。OTIO タイムラインを受け取り、FFmpeg で一回（最小回数）にまとめて実体化する。唯一の「破壊的（=新ファイルを生成する）」ツール。

### 3.3 状態の持ち方（プロジェクトモデル）

MCP の呼び出しは基本ステートレスなので、状態は**ディスク上のプロジェクトディレクトリ**で共有する。タイムライン（OTIO）が「背骨」で、各ツールはこれを読み書きして協調する。

```
<project_dir>/
  clipwright.json          # マニフェスト（バージョン, 作成情報, 設定）
  timeline.otio            # 背骨。編集判断はすべてここに集約
  sources/                 # 元素材（またはそのパス参照。コピーは任意）
  artifacts/               # 中間生成物（字幕, 解析結果 等）
    captions.srt
    silence.json
  outputs/                 # render の最終成果物
    final.mp4
```

- ツールは `timeline.otio` を読み、注記（マーカー/クリップ）を加えて書き戻す。
- 元素材は**参照**が基本（OTIO は「メディアのコンテナではなく参照を持つ」設計なのでこれに合う）。

---

## 4. 中間表現（OTIO）の規約

OTIO を共通言語として使う。独自の編集フォーマットは定義しない。

### 4.1 採用方針
- ライブラリ: `opentimelineio`（PyPI, Apache-2.0, 公式Pythonバインディング。0.18 系）。
- タイムラインは1本の OTIO ファイル（`timeline.otio`）に集約。
- 出口は OTIO のアダプタ機構を利用（FCPXML / CMX3600 EDL 等への書き出しは将来オプション）。

### 4.2 「残す/捨てる」の表現
- detect 系ツール（無音検出など）は、メディアを書き換えず **OTIO 上の注記**として結果を返す。
- 推奨表現:
  - カット候補・残す区間は **clip / gap** として timeline に並べる、もしくは
  - 検出イベント（無音, フィラー, シーン境界など）は **marker** として該当 time に置き、`marker.metadata["clipwright"]` に種別・信頼度などを格納する。
- どちらを使うかはツール種別で決め、規約として固定する（例: silence は「残す区間の clip 列」を生成、transcribe は「字幕 marker＋外部SRT」）。

### 4.3 メタデータ名前空間
- Clipwright が書き込む独自メタデータはすべて `metadata["clipwright"]` 配下に置く。他ツール・他フォーマットとの衝突を避ける。
  ```json
  { "clipwright": { "tool": "clipwright-silence", "version": "0.1.0", "kind": "keep", "confidence": 0.92 } }
  ```

### 4.4 時間の扱い
- 時間は OTIO の `opentime`（RationalTime / TimeRange）で扱い、秒の浮動小数で持ち回らない。フレーム精度・レート差での誤差を避ける。

---

## 5. コア（clipwright）の要件

コアは「他のツールが乗る土台」。ここが固まれば detect 系は同じ規約に乗せるだけで量産できる。

### 5.1 共有ライブラリとして提供するもの
- **OTIO ヘルパー**: タイムラインの新規作成 / 読み込み / 保存、clip・gap・marker の追加、`metadata["clipwright"]` の読み書き。
- **メディア probe**: `ffprobe` をサブプロセスで叩き、解像度・尺・fps・コーデック・音声トラック等を**構造化**して返す。
- **返り値エンベロープ**: 全ツール共通のレスポンス整形ヘルパー（→ 6.3）。
- **エラー整形**: アクション可能なエラーメッセージを作るヘルパー（→ 6.4）。
- **サブプロセスランナー**: 外部CLIを安全・一貫した方法で実行（引数配列で渡す / shell=False / タイムアウト / stderr収集 / 終了コード検査）。
- **プロジェクト管理**: プロジェクトディレクトリの init / 検出 / マニフェスト読み書き。
- **共通型（スキーマ）**: `MediaRef`, `TimeRange`, `Artifact`, `ToolResult` などを Pydantic で定義し、全ツールで共有。

### 5.2 プリミティブ MCP サーバーとして公開するツール（最小）
- `clipwright_init_project` — プロジェクトディレクトリを作成・初期化。
- `clipwright_inspect_media` — 素材ファイルのメタデータを構造化して返す（render の前段でAIが素材を把握するのに使う）。
- `clipwright_read_timeline` — `timeline.otio` を要約して返す（クリップ数・総尺・マーカー一覧などのサマリ＋パス。全文は返さない）。
- `clipwright_write_timeline` — 与えられた編集操作を timeline に反映して保存（または検証のみ）。

> 注: コアの MCP は「基礎操作」に絞る。編集の実体化は render の責務。

### 5.3 コアの非機能要件
- 外部依存（ffmpeg/ffprobe）が無い場合は、起動時または最初の呼び出しで**明示的かつアクション可能なエラー**を返す（「`ffmpeg` が PATH 上に見つかりません。`brew install ffmpeg` 等で導入してください」）。
- すべての公開ツールに 6.2 のアノテーションを付ける。
- 返り値は 6.3 のエンベロープに統一。

---

## 6. 規約（コントラクト）★最重要

すべてのツール（コア・render・将来のツール）が従う契約。新しい道具を足す人（人間でもAIでも）はここだけ読めば乗せられる、を目指す。

### 6.1 ツール命名
- ツール名は `clipwright_<action>` のスネークケース、動詞起点。例: `clipwright_render`, `clipwright_inspect_media`, `clipwright_detect_silence`。
- パッケージ／配布名は `clipwright-<tool>`（例: `clipwright-render`）。CLI コマンドも同名で全部小文字。
- 一貫した接頭辞によりエージェントが正しい道具を選びやすくする。

### 6.2 アノテーション（MCP annotations）
各ツールに必ず付与する。detect/render 分離が型に現れる。
- detect 系・inspect 系: `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`。
- `clipwright-render`: `readOnlyHint: false`（新ファイルを生成する）, `destructiveHint: false`（入力・OTIO は変更しない）, `idempotentHint: true`（同じ入力→同じ出力）。
- 外部プロセス/ネット等に触れるものは `openWorldHint` を適切に設定。

### 6.3 返り値エンベロープ（全ツール共通）
「軽いサマリ＋構造化データ＋成果物パス」を必ずこの形で返す。

```jsonc
{
  "ok": true,
  "summary": "47区間・合計3分12秒の無音を検出。最長は02:14付近の8秒。",  // AI が一読して判断できる1〜2文
  "data": {
    // ツール固有の構造化結果（軽量。巨大配列はパスに逃がす）
  },
  "artifacts": [
    { "role": "timeline", "path": "<project>/timeline.otio", "format": "otio" },
    { "role": "output",   "path": "<project>/outputs/final.mp4", "format": "mp4" }
  ],
  "warnings": []
}
```
- `summary` は判断に必要な要点（件数・尺・最大値など）を含める。"最小限"にしない。
- 巨大な明細（全カットリスト等）は `data` に詰めず、OTIO/JSON ファイルとして `artifacts` に出し、AIが必要なときだけ読む。
- 可能なら MCP の `outputSchema` / `structuredContent` を併用し、クライアントが構造を解釈できるようにする。

### 6.4 エラー
- 失敗時は `{ "ok": false, "error": { "code": "...", "message": "...", "hint": "..." } }`。
- `message` は何が起きたか、`hint` は次の一手（具体的な解決策）を必ず書く。

### 6.5 サブプロセス規律
- 外部ツールは引数配列で実行（`shell=False` 相当）。ユーザー/AI由来の値を文字列連結でシェルに渡さない。
- 入力ファイルパスは検証してから渡す。
- すべて別プロセス。ライブラリリンクはしない（ライセンス独立の維持）。

### 6.6 ファイル入出力
- 入力: 既存のパスを受け取る。バイト列は受け取らない。
- 出力: `outputs/` または `artifacts/` に書き、パスを `artifacts` で返す。
- 元素材・OTIO は破壊しない（render も含め、常に新規ファイルを生成）。

### 6.7 トランスポート
- ローカル動作なので **stdio** トランスポートを既定とする（素材がローカルで巨大なため、アップロード不要・高速）。

---

## 7. clipwright-render の要件

最初に作る2つ目のツール。OTIO を受け取り、FFmpeg で実体化する唯一のツール。

### 7.1 入力
- `timeline`（OTIO ファイルのパス）＋ `output`（出力パス）＋ オプション（コーデック/解像度/字幕焼き込み有無 等）。

### 7.2 振る舞い
- OTIO のクリップ列（残す区間）を解決し、**一回（または最小回数）の FFmpeg 実行**で連結・トリムして出力する。中間で何度も再エンコードしない。
- 当面サポート: 単一ソースからの区間抽出＋連結（=無音カットの実体化）、基本のトリム、パススルー。将来: 複数ソース連結、字幕焼き込み、トランジション。
- **非破壊**: 入力・OTIO を変更せず、`outputs/` に新規生成。

### 7.3 dry-run（プレビュー）モード
- `dry_run: true` のとき、実際にレンダリングせず「何が起きるか」を返す: 予定の FFmpeg コマンド（またはフィルタ計画）、残す区間数、想定出力尺・概算サイズ。
- これによりAIは安くタイムラインを反復・検証し、納得してから commit（実レンダリング）できる。detect/render 分離の利点を最大化する。

### 7.4 返り値
- 成功時: 6.3 のエンベロープ。`summary` に「総尺・出力サイズ・連結したクリップ数」など。`artifacts` に出力動画とタイムライン。

---

## 8. 技術スタックの判断

- **言語: Python（FastMCP）を推奨。** 一般論では MCP は TypeScript SDK が手厚いが、本プロジェクトは OTIO の**公式バインディングが Python** であり、想定する外部OSS（auto-editor は Python、whisper.cpp は Python ラッパー多数）も Python 生態系が濃い。OTIO を背骨にする以上、Python が最も摩擦が少ない。
  - トレードオフ: TS の方が SDK 成熟度・型の取り回しで有利な面はある。ただし本件では OTIO 連携の容易さを優先する。
- **MCP フレームワーク**: Python SDK / FastMCP。`@mcp.tool`、Pydantic でスキーマ定義、アノテーション付与。
- **スキーマ**: 入力は Pydantic で制約と説明（例つき）を書く。出力は可能な範囲で `outputSchema` を定義。
- **外部依存**: `opentimelineio`（Apache-2.0）、`ffmpeg`/`ffprobe`（**同梱せず**前提インストール）。
- **検証**: `python -m py_compile`、MCP Inspector（`npx @modelcontextprotocol/inspector`）で疎通確認。

---

## 9. ライセンス／配布方針

- **ラッパー本体（Clipwright のコード）は permissive: MIT もしくは Apache-2.0。**
- **FFmpeg バイナリは同梱しない。** 「PATH 上に ffmpeg があること」を前提条件として README に明記。配布物に含めない＝LGPL/GPL の再配布義務を発生させない。
- 取り込む各 OSS（auto-editor, unsilence 等）のライセンスは個別に確認し、**サブプロセスで呼ぶ**設計を保つ（import によるリンクを避ける）。
- 将来、事業化や同梱インストーラを検討する段階になったら、その時点でライセンス専門家のレビューを受ける。

---

## 10. MVP スコープと段階

### フェーズ1（今ここ）— 土台
1. リポジトリ初期化、`clipwright` コア（共有ライブラリ＋プリミティブMCP）。
   - OTIO ヘルパー、ffprobe ベースの `inspect_media`、プロジェクト管理、返り値エンベロープ、エラー整形、サブプロセスランナー、共通 Pydantic 型。
2. `clipwright-render`（dry-run 付き）。
   - 「単一ソースから OTIO の残す区間を連結して1本に出力」を最小成立条件にする。

### フェーズ2 — 最初の detect ツールで規約を検証
3. `clipwright-silence`（auto-editor もしくは無音検出を薄くラップ）。
   - 「無音検出 → OTIO に残す区間として注記」を実装し、`silence → render` の連携が規約だけで成立することを確認する（規約のドッグフーディング）。

### フェーズ3 — 広げる
4. `clipwright-transcribe`（whisper.cpp / faster-whisper をラップ、SRT/VTT＋OTIO marker）。
5. ノイズ除去等、必要な道具を同じ規約で追加。
6. （任意）誰かが使いやすいよう統合プラットフォーム化を検討。ただしコアの思想は「ツールの集合」のまま保つ。

---

## 11. 今後の方向性 / コントリビューション

- 規約（第6章）を**ツール作者向けの公開コントラクト**として独立文書化し、外部の人が同じ作法で道具を足せるようにする。
- 各ツールには MCP Inspector での疎通に加え、「AIが実際にタスクを解けるか」の評価（eval）を用意する（独立・読み取り専用・複数ツール呼び出しを要する現実的な設問）。
- 名前空間 `clipwright-*` を中心に、detect 系を増やしていく。render は安定した1本を維持する。

---

## 12. Claude Code への着手指示（最初のタスク）

1. リポジトリと開発環境を用意（Python, FastMCP, opentimelineio, ffprobe/ffmpeg の存在チェック）。推奨レイアウト:
   ```
   clipwright/                 # コア共有ライブラリ＋プリミティブMCP
     pyproject.toml            # license = MIT/Apache-2.0
     clipwright/
       __init__.py
       otio_utils.py           # OTIO 読み書き/注記ヘルパー
       media.py                # ffprobe ラッパー（構造化 probe）
       project.py              # プロジェクトdir / マニフェスト
       process.py              # サブプロセスランナー（shell=False, timeout）
       envelope.py             # 返り値エンベロープ / エラー整形
       schemas.py              # 共通 Pydantic 型（MediaRef, TimeRange, ToolResult …）
       server.py               # プリミティブ MCP（init_project, inspect_media, read/write_timeline）
   clipwright-render/          # 2つ目のツール（別パッケージ）
     pyproject.toml
     clipwright_render/
       __init__.py
       render.py               # OTIO → FFmpeg 計画 → 実行（dry_run 対応）
       server.py               # MCP（clipwright_render）
   CONVENTIONS.md              # 第6章を独立させたコントラクト（任意）
   README.md                   # ffmpeg 前提・インストール・使い方
   ```
2. コアの共通型（`schemas.py`）と返り値エンベロープ（`envelope.py`）を最初に固定する。これが全ツールの契約面。
3. `inspect_media`（ffprobe）と OTIO ヘルパーを実装し、プリミティブMCPを通す。
4. `clipwright-render` を実装。まず `dry_run` で「FFmpeg 計画と想定尺」を返せるようにし、その後に実レンダリングを通す。
5. MCP Inspector で疎通確認 → 小さな実素材で「区間を残して1本に連結」を検証。

> 実装着手前に、`clipwright` / `clipwright-render` の名前が PyPI・npm・GitHub org・ドメインで空いていることを最終確認し、空いていれば PyPI のプロジェクト名だけ先に確保しておくこと。
