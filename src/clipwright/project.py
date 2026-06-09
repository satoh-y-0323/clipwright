"""project.py — プロジェクトディレクトリ・マニフェスト管理。

プロジェクト構成:
  <project_dir>/
    clipwright.json   — マニフェスト
    timeline.otio     — OTIO タイムライン（V1/A1 トラック付き空 timeline）
    sources/          — 入力素材置き場
    artifacts/        — 中間生成物置き場
    outputs/          — 最終出力置き場
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clipwright import __version__
from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.otio_utils import new_timeline, save_timeline

# マニフェストファイル名
_MANIFEST_FILENAME = "clipwright.json"

# マニフェストのスキーマバージョン（将来の移行判定に使う）
_SCHEMA_VERSION = "1.0"

# サブディレクトリ一覧（init_project で必ず作成・再作成する）
_SUBDIRS = ("sources", "artifacts", "outputs")


# ===========================================================================
# init_project
# ===========================================================================


def init_project(
    project_dir: str,
    name: str,
    *,
    force: bool = False,
) -> None:
    """プロジェクトディレクトリを初期化する。

    project_dir が存在しない場合は作成する。
    sources / artifacts / outputs サブディレクトリを作成する。
    clipwright.json マニフェストと空の timeline.otio を生成する。

    既存プロジェクト（clipwright.json が存在する）に force=False で呼ぶと
    ClipwrightError(PROJECT_EXISTS) を発生させる。

    force=True の挙動（§13.2 DC-AM-007・非破壊）:
      - マニフェストを再生成する（name 等の変更を反映）
      - サブディレクトリの存在を保証する（消えていれば再作成）
      - 既存の sources / artifacts / outputs / timeline.otio は削除・上書きしない
      - timeline.otio が欠落している場合のみ空 timeline を生成する
    """
    proj = Path(project_dir)
    manifest_path = proj / _MANIFEST_FILENAME
    timeline_path = proj / "timeline.otio"

    # 既存チェック
    if manifest_path.exists() and not force:
        raise ClipwrightError(
            code=ErrorCode.PROJECT_EXISTS,
            message=f"プロジェクトがすでに存在します: {project_dir}",
            hint=(
                "既存プロジェクトを再初期化するには force=True を指定してください。"
                " force=True は非破壊です"
                "（既存 sources/artifacts/outputs/timeline.otio を保持します）。"
            ),
        )

    # ディレクトリ作成（存在していても問題なし）
    proj.mkdir(parents=True, exist_ok=True)

    # サブディレクトリ作成（存在保証）
    for subdir in _SUBDIRS:
        (proj / subdir).mkdir(exist_ok=True)

    # マニフェスト生成（force=True では再生成）
    manifest: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "name": name,
        "clipwright_version": __version__,
        "created_at": datetime.now(UTC).isoformat(),
        "settings": {},
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # timeline.otio 生成（force=True かつ既存がある場合はスキップ）
    if not timeline_path.exists():
        timeline = new_timeline(name)
        save_timeline(timeline, str(timeline_path))


# ===========================================================================
# find_project
# ===========================================================================


def find_project(start_dir: str) -> str:
    """start_dir から上位ディレクトリへ遡って clipwright.json を探索する。

    見つかった場合は clipwright.json があるディレクトリのパス（str）を返す。
    ルートまで辿っても見つからない場合は ClipwrightError(PROJECT_NOT_FOUND)。
    """
    current = Path(start_dir).resolve()

    while True:
        if (current / _MANIFEST_FILENAME).exists():
            return str(current)

        parent = current.parent
        if parent == current:
            # ファイルシステムルートに達した
            break
        current = parent

    raise ClipwrightError(
        code=ErrorCode.PROJECT_NOT_FOUND,
        message=f"clipwright.json が見つかりません: {start_dir} から上位へ探索しました",
        hint=(
            "init_project でプロジェクトを初期化してから再実行してください。"
            f"探索開始ディレクトリ: {start_dir}"
        ),
    )


# ===========================================================================
# load_manifest / save_manifest
# ===========================================================================


def load_manifest(project_dir: str) -> dict[str, Any]:
    """プロジェクトディレクトリのマニフェスト（clipwright.json）を読み込む。

    clipwright.json が存在しない場合は ClipwrightError(PROJECT_NOT_FOUND)。
    戻り値は dict（JSON の top-level object を直接返す）。
    """
    manifest_path = Path(project_dir) / _MANIFEST_FILENAME
    if not manifest_path.exists():
        raise ClipwrightError(
            code=ErrorCode.PROJECT_NOT_FOUND,
            message=f"clipwright.json が見つかりません: {project_dir}",
            hint="init_project でプロジェクトを初期化してください。",
        )
    return json.loads(manifest_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def save_manifest(project_dir: str, manifest: dict[str, Any]) -> None:
    """マニフェストをプロジェクトディレクトリに書き込む。

    既存の clipwright.json を上書きする。
    manifest は dict（JSON にシリアライズ可能な型のみ使用すること）。
    """
    manifest_path = Path(project_dir) / _MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
