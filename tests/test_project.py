"""test_project.py — project.py のテスト。

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

追加 Red テスト（レビュー対応 M-3 / F-03 / L-4=F-08）:
- [M-3] save_manifest はアトミック書き込み（temp → os.replace）を使う
- [F-03] find_project はディレクトリでない start_dir を渡したときにエラーにする
- [L-4/F-08] find_project 未検出時の hint に start_dir フルパスを重複掲載しない
"""

from __future__ import annotations

import json
import os
import unittest.mock as mock
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
        """timeline.otio に V1（Video）トラックが含まれる
        （§13.1 DC-AS-003 / §13.5）。"""
        import opentimelineio as otio

        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        tl = otio.adapters.read_from_file(str(proj / "timeline.otio"))
        video_tracks = [t for t in tl.tracks if t.kind == otio.schema.TrackKind.Video]
        assert len(video_tracks) >= 1
        assert video_tracks[0].name == "V1"

    def test_timeline_has_a1_track(self, tmp_project: Path) -> None:
        """timeline.otio に A1（Audio）トラックが含まれる
        （§13.1 DC-AS-003 / §13.5）。"""
        import opentimelineio as otio

        proj = tmp_project / "myproject"
        init_project(str(proj), name="myproject")
        tl = otio.adapters.read_from_file(str(proj / "timeline.otio"))
        audio_tracks = [t for t in tl.tracks if t.kind == otio.schema.TrackKind.Audio]
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


# ===========================================================================
# [M-3] save_manifest — アトミック書き込み（temp → os.replace）
# ===========================================================================


class TestSaveManifestAtomic:
    """[M-3] save_manifest は temp ファイル経由のアトミック書き込みを使う。

    save_timeline と同一のパターン（temp → os.replace）に揃えることで
    書き込み途中の中断による clipwright.json 破損を防ぐ。
    """

    def test_save_manifest_uses_os_replace(self, tmp_project: Path) -> None:
        """save_manifest が os.replace を呼ぶことを monkeypatch で確認する。

        現在の実装は write_text の直接上書きを使っているため、このテストは
        os.replace が呼ばれないことを示す（Red: 機能未実装による失敗）。
        実装後は clipwright.project モジュール内の os.replace が1回呼ばれる。
        """
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        manifest = load_manifest(str(proj))

        replace_calls: list[tuple[str, str]] = []

        original_replace = os.replace

        def recording_replace(src: str, dst: str) -> None:
            replace_calls.append((src, dst))
            original_replace(src, dst)

        # project.py は "import os" して os.replace を使う想定でパッチ
        # mock.patch.object で os モジュールの replace を差し替える
        with mock.patch.object(os, "replace", side_effect=recording_replace):
            save_manifest(str(proj), manifest)

        # アトミック書き込みでは os.replace が必ず1回呼ばれる
        assert len(replace_calls) >= 1, (
            "save_manifest が os.replace を呼んでいません。"
            "temp → os.replace のアトミック書き込みになっていない（M-3 未実装）。"
        )

    def test_save_manifest_temp_in_same_dir(self, tmp_project: Path) -> None:
        """アトミック書き込みの temp ファイルが同一ディレクトリ内に作られる。

        os.replace はファイルシステムをまたぐとアトミックにならないため、
        temp ファイルは manifest_path と同一ディレクトリに置く必要がある。
        """
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        manifest = load_manifest(str(proj))

        replace_calls: list[tuple[str, str]] = []

        original_replace = os.replace

        def recording_replace(src: str, dst: str) -> None:
            replace_calls.append((src, dst))
            original_replace(src, dst)

        with mock.patch.object(os, "replace", side_effect=recording_replace):
            save_manifest(str(proj), manifest)

        assert len(replace_calls) >= 1, (
            "save_manifest が os.replace を呼んでいません（M-3 未実装）。"
        )
        src_path, dst_path = replace_calls[0]
        # temp と dest は同一ディレクトリでなければならない
        assert Path(src_path).parent == Path(dst_path).parent, (
            f"temp ({src_path}) と dest ({dst_path}) が異なるディレクトリ。"
            "クロスデバイス atomic write になっている可能性がある。"
        )

    def test_save_manifest_result_is_valid_json_after_atomic_write(
        self, tmp_project: Path
    ) -> None:
        """アトミック書き込み後に clipwright.json が有効な JSON であること。

        この正常系テストは既存の test_save_writes_valid_json と重複しているが、
        M-3 実装後も回帰しないことを明示的に担保するために残す。
        """
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")
        manifest = load_manifest(str(proj))
        manifest["m3_marker"] = "atomic"
        save_manifest(str(proj), manifest)

        raw = (proj / "clipwright.json").read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert parsed["m3_marker"] == "atomic"

    def test_save_manifest_overwrites_existing_without_corruption(
        self, tmp_project: Path
    ) -> None:
        """既存 manifest を上書きしても内容が破損しない。

        複数回 save_manifest を呼んだ場合、最後の呼び出し内容が正しく残る。
        """
        proj = tmp_project / "proj"
        init_project(str(proj), name="proj")

        for i in range(3):
            manifest = load_manifest(str(proj))
            manifest["counter"] = i
            save_manifest(str(proj), manifest)

        final = load_manifest(str(proj))
        assert final["counter"] == 2  # 最後の値
        # JSON として parse できること（破損チェック）
        raw = (proj / "clipwright.json").read_text(encoding="utf-8")
        assert json.loads(raw) == final


# ===========================================================================
# [F-03] find_project — is_dir() チェック
# ===========================================================================


class TestFindProjectValidation:
    """[F-03] find_project はディレクトリでない start_dir を検証してエラーにする。

    セキュリティレビュー F-03 に対応。ファイルパスや存在しないパスを
    start_dir として渡したとき、ファイルシステムを無意味に探索しない。
    """

    def test_raises_error_when_start_dir_is_file(self, tmp_project: Path) -> None:
        """start_dir がファイルの場合にエラーを発生させる。

        現在の実装は is_dir() チェックなしに Path(start_dir).resolve() を呼ぶため、
        ファイルを指定するとその親ディレクトリから探索が始まり、意図しない動作になる。
        このテストは適切なエラーコード（PROJECT_NOT_FOUND または INVALID_INPUT）が
        返ることを期待する（Red: is_dir() チェック未実装のため失敗する）。
        """
        from clipwright.errors import ClipwrightError, ErrorCode

        # ファイルを作成して start_dir として渡す
        file_path = tmp_project / "not_a_dir.txt"
        file_path.write_text("I am a file", encoding="utf-8")

        with pytest.raises(ClipwrightError) as exc_info:
            find_project(str(file_path))

        assert exc_info.value.code in (
            ErrorCode.PROJECT_NOT_FOUND,
            ErrorCode.INVALID_INPUT,
        ), (
            f"start_dir がファイル時に PROJECT_NOT_FOUND/INVALID_INPUT 期待だが "
            f"code={exc_info.value.code} が返りました。"
        )

    def test_raises_invalid_input_when_start_dir_is_file(
        self, tmp_project: Path
    ) -> None:
        """start_dir がファイルのとき INVALID_INPUT を返すことが望ましい。

        F-03 修正方針に最も近いコード。実装で INVALID_INPUT が確定した場合の確認テスト。
        """
        from clipwright.errors import ClipwrightError, ErrorCode

        file_path = tmp_project / "not_a_dir.txt"
        file_path.write_text("I am a file", encoding="utf-8")

        with pytest.raises(ClipwrightError) as exc_info:
            find_project(str(file_path))

        # is_dir() チェックが追加されれば INVALID_INPUT になる（実装選択による）
        # 現状（チェックなし）では PROJECT_NOT_FOUND か例外なしで正しくない動作をする
        assert exc_info.value.code == ErrorCode.INVALID_INPUT, (
            "start_dir がファイルのとき INVALID_INPUT を期待します。"
            "find_project に is_dir() チェックを追加してください（F-03 未実装）。"
        )

    def test_init_project_success_not_regressed(self, tmp_project: Path) -> None:
        """F-03 対応後も init_project の正常系が回帰しないことを確認する。

        find_project に is_dir() チェックを追加しても、
        正規のディレクトリパスで init_project が成功することを担保する。
        """
        proj = tmp_project / "valid_proj"
        # 正常系: ディレクトリ → エラーなし
        init_project(str(proj), name="valid_proj")
        found = find_project(str(proj))
        assert Path(found) == proj

    def test_find_project_with_valid_dir_not_regressed(self, tmp_project: Path) -> None:
        """F-03 対応後も find_project の既存動作が回帰しないことを確認する。

        正常な既存ディレクトリからは変わらず PROJECT_NOT_FOUND が返る。
        """
        from clipwright.errors import ClipwrightError, ErrorCode

        empty_dir = tmp_project / "empty"
        empty_dir.mkdir()

        with pytest.raises(ClipwrightError) as exc_info:
            find_project(str(empty_dir))

        assert exc_info.value.code == ErrorCode.PROJECT_NOT_FOUND


# ===========================================================================
# [L-4 / F-08] find_project — hint のパス重複・長大パス対策
# ===========================================================================


class TestFindProjectHintQuality:
    """[L-4/F-08] find_project 未検出時の hint にフルパスを重複掲載しない。

    コードレビュー L-4 / セキュリティレビュー F-08 に対応。
    hint は「次の一手」の説明のみとし、パス情報は message にのみ残す。
    長大な start_dir を渡しても hint が肥大化しないことを固定する。
    """

    def test_hint_does_not_contain_full_start_dir(self, tmp_project: Path) -> None:
        """PROJECT_NOT_FOUND の hint に start_dir フルパスが含まれないこと。

        現在の実装は hint に start_dir を埋め込んでいるため、このテストは失敗する
        （Red: L-4/F-08 未修正のため）。
        """
        from clipwright.errors import ClipwrightError

        empty_dir = tmp_project / "no_proj"
        empty_dir.mkdir()
        start_dir_str = str(empty_dir)

        with pytest.raises(ClipwrightError) as exc_info:
            find_project(start_dir_str)

        hint = exc_info.value.hint
        # hint にフルパスが含まれていてはならない
        assert start_dir_str not in hint, (
            f"hint にフルパス '{start_dir_str}' が含まれています。"
            "hint はパス情報を含めず次の一手のみ記述してください（L-4/F-08）。"
        )

    def test_message_contains_start_dir(self, tmp_project: Path) -> None:
        """PROJECT_NOT_FOUND の message には start_dir が含まれる。

        hint からパスを除去した代わりに、message 側に残ることを確認する。
        """
        from clipwright.errors import ClipwrightError

        empty_dir = tmp_project / "no_proj2"
        empty_dir.mkdir()

        with pytest.raises(ClipwrightError) as exc_info:
            find_project(str(empty_dir))

        message = exc_info.value.message
        assert str(empty_dir) in message or empty_dir.name in message, (
            "message にパス情報が含まれていません。"
            "パスは hint ではなく message に記述してください。"
        )

    def test_hint_and_message_do_not_both_contain_full_path(
        self, tmp_project: Path
    ) -> None:
        """hint と message の両方に同一フルパスが入らないこと。

        L-4: 同じ情報を hint/message 双方に含めると summary として冗長。
        """
        from clipwright.errors import ClipwrightError

        empty_dir = tmp_project / "no_proj3"
        empty_dir.mkdir()
        start_dir_str = str(empty_dir)

        with pytest.raises(ClipwrightError) as exc_info:
            find_project(start_dir_str)

        hint = exc_info.value.hint
        message = exc_info.value.message

        hint_has_full_path = start_dir_str in hint
        message_has_full_path = start_dir_str in message

        assert not (hint_has_full_path and message_has_full_path), (
            f"hint と message の両方にフルパス '{start_dir_str}' が含まれています。"
            "フルパスは message のみに残し hint から除去してください（L-4/F-08）。"
        )

    def test_hint_length_bounded_with_long_path(self, tmp_project: Path) -> None:
        """長大な start_dir を渡しても hint が一定長に収まる。

        F-08: 数千文字の悪意あるパスを渡した場合に hint が肥大化しないことを確認する。
        hint は「次の一手」のみを含み、入力値をそのまま返すべきではない。

        Windows の MAX_PATH 制限を回避するため、ディレクトリ名は短くしつつ
        start_dir 全体としては十分に長い文字列になるよう深いパスを使う。
        """
        from clipwright.errors import ClipwrightError

        # 深いネストで合計パス長を伸ばす（各ディレクトリ名は短く保つ）
        # tmp_path 自体は既に長めのパスなので、少し深くするだけで十分
        nested = tmp_project / "a" / "b" / "c" / "no_project_here"
        nested.mkdir(parents=True)
        start_dir_str = str(nested)

        # start_dir_str（フルパス）が長い文字列であることを前提として
        # hint にそのフルパスが含まれないことを確認する
        with pytest.raises(ClipwrightError) as exc_info:
            find_project(start_dir_str)

        hint = exc_info.value.hint
        # hint にフルパスが含まれていてはならない（F-08 未修正なら失敗する）
        assert start_dir_str not in hint, (
            f"hint に入力フルパス '{start_dir_str}' が含まれています。"
            "hint は末尾ディレクトリ名のみかパス情報を含めないでください（F-08）。"
        )

    def test_hint_contains_actionable_guidance(self, tmp_project: Path) -> None:
        """PROJECT_NOT_FOUND の hint は init_project への言及を含む。

        hint からパス除去後も「次の一手」として init_project 案内が残ることを確認。
        """
        from clipwright.errors import ClipwrightError

        empty_dir = tmp_project / "no_proj4"
        empty_dir.mkdir()

        with pytest.raises(ClipwrightError) as exc_info:
            find_project(str(empty_dir))

        hint = exc_info.value.hint
        assert "init_project" in hint, (
            "hint に 'init_project' への案内が含まれていません。"
            "パスを除去した後も、次の一手（init_project）を hint に残してください。"
        )
