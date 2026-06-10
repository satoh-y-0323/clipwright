# clipwright ツール雛形（scaffold）

新しい `clipwright-*` ツールを足すときのコピー元雛形。`clipwright-tool/` を
コピーしてプレースホルダを置換すれば、CONVENTIONS の MUST（M1〜M5）を満たした
動く骨格（MCP サーバー + CLI シム + tests）が手に入る。

> 方針: cookiecutter 等の依存は足さない（YAGNI）。素のコピー + 文字列置換で済ませる。
> 規約の出典は root `CONVENTIONS.md`。

## プレースホルダ

| トークン | 意味 | 例 |
|---|---|---|
| `__TOOL__` | ツールの短いスラッグ（小文字・identifier 可） | `noise` |
| `__ACTION__` | MCP アクション名（snake_case の動詞起点） | `detect_noise` |
| `__Action__` | アクションの PascalCase（クラス名用） | `DetectNoise` |

`__TOOL__` はパッケージ名・ファイル名・CLI 名に、`__ACTION__` は MCP ツール名
`clipwright___ACTION__` とオーケストレーション関数名に、`__Action__` は
オプションクラス名 `__Action__Options` に使う。

## 生成手順（Bash / MINGW64）

```bash
# 1. パラメータを決める
TOOL=noise                 # 小文字スラッグ
ACTION=detect_noise        # snake_case のアクション
ACTION_PASCAL=DetectNoise  # PascalCase のアクション

# 2. 雛形をリポジトリ直下にコピー
cp -r templates/clipwright-tool clipwright-$TOOL
cd clipwright-$TOOL

# 3. ファイル内容のトークンを置換（case-sensitive・3 トークン）
grep -rlZ -E '__ACTION__|__Action__|__TOOL__' . | xargs -0 sed -i \
  -e "s/__Action__/$ACTION_PASCAL/g" \
  -e "s/__ACTION__/$ACTION/g" \
  -e "s/__TOOL__/$TOOL/g"

# 4. ディレクトリ・ファイル名をリネーム（ディレクトリ → 中のファイルの順）
mv src/clipwright___TOOL__ src/clipwright_$TOOL
mv src/clipwright_$TOOL/__TOOL__.py        src/clipwright_$TOOL/$TOOL.py
mv src/clipwright_$TOOL/__TOOL___cli.py    src/clipwright_$TOOL/${TOOL}_cli.py
mv tests/test___TOOL__.py                  tests/test_$TOOL.py
cd ..
```

外部 OSS を使わない純 Python ツールなら、`src/clipwright_$TOOL/${TOOL}_cli.py`
は不要なので削除してよい（`*.py` 本体側の `_run_cli` 参照も外す）。

## ワークスペース登録

ルート `pyproject.toml` の uv workspace members に追加する:

```toml
[tool.uv.workspace]
members = ["clipwright-render", "clipwright-silence", "clipwright-transcribe", "clipwright-wrap", "clipwright-noise"]
```

その後リポジトリ直下で:

```bash
uv sync
uv run --package clipwright-$TOOL pytest
uv run ruff format clipwright-$TOOL && uv run ruff check clipwright-$TOOL
uv run mypy clipwright-$TOOL/src
```

## 雛形に含まれるもの

```
clipwright-tool/
  README.md                          # ツール自身の README（要編集）
  pyproject.toml                     # MIT・clipwright 依存・ruff/mypy/pytest 設定
  src/clipwright___TOOL__/
    __init__.py                      # __version__
    py.typed                         # 型配布マーカー
    schemas.py                       # ツール固有 Pydantic（共通型は clipwright.schemas 再利用）
    __TOOL__.py                      # オーケストレーション層（検証→OSS→正規化→エンベロープ）
    __TOOL___cli.py                  # OSS を包む別プロセス CLI シム（M4・不要なら削除）
    server.py                        # FastMCP @mcp.tool + annotations + stdio 起動
  tests/
    conftest.py / test_schemas.py / test_server.py / test___TOOL__.py
```

## 置換後にやること（チェックリスト）

雛形には `TODO:` を埋め込んである。最低限、以下を実装・確認する:

- [ ] `pyproject.toml` の `description` を1文で書く。OSS を使うなら依存に追加。
- [ ] `schemas.py` の `example_threshold` を実パラメータに置き換える。
- [ ] `<tool>.py` の検出/解析本体（`# TODO:` ブロック）を実装する。
- [ ] detect/inspect 系か render 系かで `server.py` の annotations を合わせる
      （render 系は `readOnlyHint=False`）。ネット接続なら `openWorldHint=True`。
- [ ] README のパラメータ表・前提（OSS の PATH 要否）を更新する。
- [ ] `CONVENTIONS.md` §7 の PR 前セルフチェックリストを通す。
- [ ] （任意）`evals/` に AI 実タスク評価を用意する（CONVENTIONS §6）。
