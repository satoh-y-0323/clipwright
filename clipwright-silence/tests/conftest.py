"""clipwright-silence テスト用共有フィクスチャ。

ffmpeg/ffprobe の探索順序:
  1. PATH (shutil.which)
  2. 環境変数 CLIPWRIGHT_FFMPEG / CLIPWRIGHT_FFPROBE

いずれも見つからない場合のみ integration テストを skip する。
clipwright-render/tests/conftest.py と同じ解決方針を維持する。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


class _FakeSileroVadModuleType:
    """MagicMock(spec=ModuleType) の代替として test_vad_cli で使われる疑似型。

    types.ModuleType は load_silero_vad / get_speech_timestamps を持たないため
    MagicMock(spec=ModuleType) ではこれらの属性にアクセスできない。
    本クラスを test_vad_cli.ModuleType として差し替えることで、
    MagicMock(spec=_FakeSileroVadModuleType) が silero_vad の属性を持てるようにする。
    """

    load_silero_vad: Any = None
    get_speech_timestamps: Any = None


def _make_silero_mock_fixed(
    speech_segments: list[dict[str, Any]],
) -> tuple[MagicMock, MagicMock]:
    """silero_vad モジュールのモック一式を返す（spec なし版）。

    test_vad_cli._make_silero_mock は MagicMock(spec=ModuleType) を使っているが、
    types.ModuleType には load_silero_vad / get_speech_timestamps が存在しないため
    AttributeError になる。autouse fixture でこの関数に差し替える。
    """
    mock_module = MagicMock()  # spec なし: 任意の属性アクセスを許可
    mock_model = MagicMock()
    mock_module.load_silero_vad.return_value = mock_model
    mock_get_ts = MagicMock(return_value=speech_segments)
    mock_module.get_speech_timestamps = mock_get_ts
    return mock_module, mock_get_ts


@pytest.fixture(autouse=True)
def _patch_vad_cli_test_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    """test_vad_cli のヘルパーと ModuleType を正しい実装に差し替える autouse fixture。

    MagicMock(spec=ModuleType) では silero_vad 固有の属性にアクセスできないため、
    - _make_silero_mock を spec なし版に置き換える
    - ModuleType を silero_vad の属性を持つ _FakeSileroVadModuleType に置き換える
    """
    try:
        import tests.test_vad_cli as tv  # noqa: PLC0415

        monkeypatch.setattr(tv, "_make_silero_mock", _make_silero_mock_fixed)
        monkeypatch.setattr(tv, "ModuleType", _FakeSileroVadModuleType)
    except (ImportError, AttributeError):
        # test_vad_cli が存在しない環境では何もしない
        pass


def _find_binary(name: str, env_var: str) -> str | None:
    """バイナリを PATH → env_var の順で探す。どちらも見つからなければ None。"""
    found = shutil.which(name)
    if found:
        return found
    env_val = os.environ.get(env_var)
    if env_val and Path(env_val).is_file():
        return env_val
    return None


@pytest.fixture(scope="session")
def ffprobe_path() -> str | None:
    """ffprobe 実行ファイルのパス。見つからなければ None。"""
    return _find_binary("ffprobe", "CLIPWRIGHT_FFPROBE")


@pytest.fixture(scope="session")
def ffmpeg_path() -> str | None:
    """ffmpeg 実行ファイルのパス。見つからなければ None。"""
    return _find_binary("ffmpeg", "CLIPWRIGHT_FFMPEG")


@pytest.fixture
def require_ffprobe(ffprobe_path: str | None) -> str:
    """integration テスト用: ffprobe がなければ skip する。パスを返す。"""
    if ffprobe_path is None:
        pytest.skip(
            "ffprobe が見つかりません。"
            "PATH に ffprobe を追加するか "
            "CLIPWRIGHT_FFPROBE 環境変数にフルパスを設定してください。"
        )
    return ffprobe_path


@pytest.fixture
def require_ffmpeg(ffmpeg_path: str | None) -> str:
    """integration テスト用: ffmpeg がなければ skip する。パスを返す。"""
    if ffmpeg_path is None:
        pytest.skip(
            "ffmpeg が見つかりません。"
            "PATH に ffmpeg を追加するか "
            "CLIPWRIGHT_FFMPEG 環境変数にフルパスを設定してください。"
        )
    return ffmpeg_path
