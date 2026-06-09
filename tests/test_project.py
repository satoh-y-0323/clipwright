"""test_project.py — project.py の Red フェーズテスト。

このテストは project.py が未実装のため ImportError で失敗する（Red）。
機能が未実装であることによる失敗が期待動作。

対象（§7 / §13.2 DC-AM-007）:
- init_project(project_dir, name, force=False):
    - dir / sources / artifacts / outputs を作成
    - clipwright.json マニフェストを生成
    - V1/A1 トラック付き空 timeline.otio を生成（§13.1 DC-AS-003）
    - 既存なら ClipwrightError(PROJECT_EXISTS)
    - force=True は非破壊:
        - マニフェスト再生成とディレクトリ存在保証のみ
        - 既存 sources/artifacts/outputs/timeline.otio を削除・上書きしない
        - timeline.otio 欠落時のみ空 timeline を生成
- find_project(start_dir): 上位ディレクトリへ遡って clipwright.json を探索
- load_manifest(project_dir) / save_manifest(project_dir, manifest): 往復シリアライズ
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# --- Import（project.py 未実装のため ImportError が発生する → Red） ---
from clipwright.project import (
    find_project,
    init_project,
    load_manifest,
    save_manifest,
)


# ===========================================================================
# init_project — 正常系
# ===========================================================================


class TestInitProjectSuccess:
    """init_project の正常系（ディレクトリ・ファイル生成）。"""

    def test_creates_project_dir(self, tmp_project: Path) -> None:
        """project_dir が存在しない場合に作成される。"""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        assert proj.is_dir()

    def test_creates_subdirs(self, tmp_project: Path) -> None:
        """sources / artifacts / outputs サブディレクトリが作成される。"""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        assert (proj / "sources").is_dir()
        assert (proj / "artifacts").is_dir()
        assert (proj / "outputs").is_dir()

    def test_creates_manifest(self, tmp_project: Path) -> None:
        """clipwright.json マニフェストが生成される。"""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        assert (proj / "clipwright.json").is_file()

    def test_manifest_schema_version(self, tmp_project: Path) -> None:
        """マニフェストに schema_version が含まれる。"""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        manifest = json.loads((proj / "clipwright.json").read_text(encoding="utf-8"))
        assert "schema_version" in manifest

    def test_manifest_name(self, tmp_project: Path) -> None:
        """マニフェストの name フィールドが引数と一致する。"""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        manifest = json.loads((proj / "clipwright.json").read_text(encoding="utf-8"))
        assert manifest["name"] == "myproject"

    def test_manifest_has_clipwright_version(self, tmp_project: Path) -> None:
        """マニフェストに clipwright_version が含まれる。"""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        manifest = json.loads((proj / "clipwright.json").read_text(encoding="utf-8"))
        assert "clipwright_version" in manifest

    def test_manifest_has_created_at(self, tmp_project: Path) -> None:
        """マニフェストに created_at タイムスタンプが含まれる。"""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        manifest = json.loads((proj / "clipwright.json").read_text(encoding="utf-8"))
        assert "created_at" in manifest

    def test_creates_timeline_otio(self, tmp_project: Path) -> None:
        """timeline.otio が生成される。"""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        assert (proj / "timeline.otio").is_file()

    def test_timeline_has_v1_track(self, tmp_project: Path) -> None:
        """timeline.otio に V1（Video）トラックが含まれる（§13.1 DC-AS-003 / §13.5）。"""
        import opentimelineio as otio

        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        tl = otio.adapters.read_from_file(str(proj / "timeline.otio"))
        video_tracks = [
            t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video
        ]
        assert len(video_tracks) >= 1
        assert video_tracks[0].name == "V1"

    def test_timeline_has_a1_track(self, tmp_project: Path) -> None:
        """timeline.otio に A1（Audio）トラックが含まれる（§13.1 DC-AS-003 / §13.5）。"""
        import opentimelineio as otio

        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        tl = otio.adapters.read_from_file(str(proj / "timeline.otio"))
        audio_tracks = [
            t for t in tl.tracks if t.kind == otio.schema.TrackKind.Audio
        ]
        assert len(audio_tracks) >= 1
        assert audio_tracks[0].name == "A1"

    def test_timeline_track_order(self, tmp_project: Path) -> None:
        """トラック順序は [V1(Video), A1(Audio)]（§13.5 DC-AS-001 再）。"""
        import opentimelineio as otio

        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        tl = otio.adapters.read_from_file(str(proj / "timeline.otio"))
        tracks = list(tl.tracks)
        assert len(tracks) == 2
        assert tracks[0].kind == otio.schema.TrackKind.Video
        assert tracks[1].kind == otio.schema.TrackKind.Audio

    def test_timeline_is_empty(self, tmp_project: Path) -> None:
        """初期 timeline.otio はクリップ・マーカーを持たない空の状態。"""
        import opentimelineio as otio

        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        tl = otio.adapters.read_from_file(str(proj / "timeline.otio"))
        for track in tl.tracks:
            assert len(track) == 0


# ===========================================================================
# init_project — PROJECT_EXISTS エラー
# ===========================================================================


class TestInitProjectExists:
    """既存プロジェクトへの init は PROJECT_EXISTS を発生させる。"""

    def test_raises_project_exists(self, tmp_project: Path) -> None:
        """既存プロジェクトに force なしで init すると PROJECT_EXISTS。"""
        from clipwright.errors import ClipwrightError, ErrorCode

        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        with pytest.raises(ClipwrightError) as exc_info:
            init_project(str(proj), name="myproject")
        assert exc_info.value.code == ErrorCode.PROJECT_EXISTS

    def test_error_has_hint(self, tmp_project: Path) -> None:
        """PROJECT_EXISTS エラーの hint が空でない。"""
        from clipwright.errors import ClipwrightError

        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        with pytest.raises(ClipwrightError) as exc_info:
            init_project(str(proj), name="myproject")
        assert exc_info.value.hint


# ===========================================================================
# init_project — force=True（非破壊セマンティクス）DC-AM-007
# ===========================================================================


class TestInitProjectForce:
    """force=True は非破壊（§13.2 DC-AM-007）。"""

    def test_force_does_not_raise(self, tmp_project: Path) -> None:
        """force=True では既存プロジェクトに対しても例外が出ない。"""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        # 例外なく完了するはず
        init_project(str(proj), name="myproject", force=True)

    def test_force_regenerates_manifest(self, tmp_project: Path) -> None:
        """force=True はマニフェストを再生成する（設定変更の反映）。"""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        # name を変えて force=True で再生成
        init_project(str(proj), name="renamed", force=True)
        manifest = json.loads((proj / "clipwright.json").read_text(encoding="utf-8"))
        assert manifest["name"] == "renamed"

    def test_force_preserves_sources_content(self, tmp_project: Path) -> None:
        """force=True でも sources/ 内のファイルは削除されない（非破壊）。"""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        sentinel = proj / "sources" / "keep.txt"
        sentinel.write_text("preserve me", encoding="utf-8")
        init_project(str(proj), name="myproject", force=True)
        assert sentinel.is_file()
        assert sentinel.read_text(encoding="utf-8") == "preserve me"

    def test_force_preserves_artifacts_content(self, tmp_project: Path) -> None:
        """force=True でも artifacts/ 内のファイルは削除されない（非破壊）。"""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        sentinel = proj / "artifacts" / "keep.json"
        sentinel.write_text("{}", encoding="utf-8")
        init_project(str(proj), name="myproject", force=True)
        assert sentinel.is_file()

    def test_force_preserves_outputs_content(self, tmp_project: Path) -> None:
        """force=True でも outputs/ 内のファイルは削除されない（非破壊）。"""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        sentinel = proj / "outputs" / "keep.mp4"
        sentinel.write_bytes(b"\x00\x01\x02")
        init_project(str(proj), name="myproject", force=True)
        assert sentinel.is_file()

    def test_force_preserves_existing_timeline(self, tmp_project: Path) -> None:
        """force=True でも既存の timeline.otio は上書き・削除しない（非破壊）。"""
        import opentimelineio as otio

        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")

        # timeline.otio を変更してから mtime を記録
        timeline_path = proj / "timeline.otio"
        original_mtime = timeline_path.stat().st_mtime

        import time
        time.sleep(0.05)  # OS の mtime 分解能への対策

        init_project(str(proj), name="myproject", force=True)

        # mtime が変わっていない = 上書きされていない
        new_mtime = timeline_path.stat().st_mtime
        assert new_mtime == original_mtime

    def test_force_creates_missing_timeline(self, tmp_project: Path) -> None:
        """force=True で timeline.otio が欠落している場合のみ空 timeline を生成する。"""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")

        # timeline.otio を手動で削除
        timeline_path = proj / "timeline.otio"
        timeline_path.unlink()
        assert not timeline_path.exists()

        init_project(str(proj), name="myproject", force=True)
        assert timeline_path.is_file()

    def test_force_ensures_subdirs_exist(self, tmp_project: Path) -> None:
        """force=True で消えたサブディレクトリを再作成する（ディレクトリ存在保証）。"""
        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")

        # サブディレクトリを手動削除
        import shutil
        shutil.rmtree(proj / "sources")

        init_project(str(proj), name="myproject", force=True)
        assert (proj / "sources").is_dir()


# ===========================================================================
# find_project — 上位ディレクトリ探索
# ===========================================================================


class TestFindProject:
    """find_project: 上位ディレクトリへ遡って clipwright.json を探索する。"""

    def test_find_from_project_root(self, tmp_project: Path) -> None:
        """プロジェクトルート自身から検索して見つかる。"""
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        found = find_project(str(proj))
        assert Path(found) == proj

    def test_find_from_subdir(self, tmp_project: Path) -> None:
        """プロジェクト配下のサブディレクトリから上位に遡って見つかる。"""
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        subdir = proj / "sources" / "nested"
        subdir.mkdir(parents=True)
        found = find_project(str(subdir))
        assert Path(found) == proj

    def test_raises_not_found(self, tmp_project: Path) -> None:
        """プロジェクトが存在しないディレクトリからは PROJECT_NOT_FOUND。"""
        from clipwright.errors import ClipwrightError, ErrorCode

        empty_dir = tmp_project / "no_project"
        empty_dir.mkdir()
        with pytest.raises(ClipwrightError) as exc_info:
            find_project(str(empty_dir))
        assert exc_info.value.code == ErrorCode.PROJECT_NOT_FOUND

    def test_returns_str(self, tmp_project: Path) -> None:
        """戻り値は str 型（ToolResult に詰めやすくするため）。"""
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        found = find_project(str(proj))
        assert isinstance(found, str)


# ===========================================================================
# load_manifest / save_manifest — 往復シリアライズ
# ===========================================================================


class TestManifestRoundtrip:
    """load_manifest / save_manifest の往復シリアライズ。"""

    def test_load_returns_dict(self, tmp_project: Path) -> None:
        """load_manifest は dict を返す。"""
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        manifest = load_manifest(str(proj))
        assert isinstance(manifest, dict)

    def test_load_contains_name(self, tmp_project: Path) -> None:
        """load_manifest の戻り値に name が含まれる。"""
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        manifest = load_manifest(str(proj))
        assert manifest["name"] == "proj"

    def test_save_and_load_roundtrip(self, tmp_project: Path) -> None:
        """save_manifest → load_manifest で値が往復する。"""
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        original = load_manifest(str(proj))
        original["settings"] = {"custom_key": "custom_value"}
        save_manifest(str(proj), original)
        reloaded = load_manifest(str(proj))
        assert reloaded["settings"]["custom_key"] == "custom_value"

    def test_load_raises_not_found_for_missing_manifest(
        self, tmp_project: Path
    ) -> None:
        """clipwright.json が存在しないディレクトリに対して PROJECT_NOT_FOUND。"""
        from clipwright.errors import ClipwrightError, ErrorCode

        empty_dir = tmp_project / "no_manifest"
        empty_dir.mkdir()
        with pytest.raises(ClipwrightError) as exc_info:
            load_manifest(str(empty_dir))
        assert exc_info.value.code == ErrorCode.PROJECT_NOT_FOUND

    def test_save_writes_valid_json(self, tmp_project: Path) -> None:
        """save_manifest が書き出すファイルは有効な JSON。"""
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        manifest = load_manifest(str(proj))
        manifest["extra"] = 42
        save_manifest(str(proj), manifest)
        raw = (proj / "clipwright.json").read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert parsed["extra"] == 42
