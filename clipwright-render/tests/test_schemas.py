"""test_schemas.py — RenderOptions の Red テスト（DC-AM-004）。

architecture §6.1 の RenderOptions 確定仕様を観点として固定する。
このファイルは schemas.py が存在しない / RenderOptions が未実装の段階で
機能未実装により失敗することを意図した Red テスト群。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from clipwright_render.schemas import RenderOptions

# ===========================================================================
# デフォルト構築
# ===========================================================================


class TestRenderOptionsDefaults:
    """全フィールド省略でモデルが構築でき、各フィールドが既定値を持つこと。"""

    def test_build_with_no_args(self) -> None:
        # Arrange / Act
        opts = RenderOptions()

        # Assert
        assert opts.video_codec is None
        assert opts.audio_codec is None
        assert opts.width is None
        assert opts.height is None
        assert opts.fps is None
        assert opts.crf is None
        assert opts.overwrite is False


# ===========================================================================
# 有効値の受理
# ===========================================================================


@pytest.mark.parametrize(
    "video_codec,audio_codec",
    [
        ("libx264", "aac"),
        ("libx265", "opus"),
        ("copy", None),
        (None, "mp3"),
        (None, None),
        ("libvpx-vp9", "opus"),
        ("h264_nvenc", "pcm_s16le"),
    ],
)
def test_valid_codecs_accepted(
    video_codec: str | None, audio_codec: str | None
) -> None:
    """有効なコーデック文字列（英数字・アンスコ・ハイフン）を受理する（Sec M-3）。"""
    # Arrange / Act
    opts = RenderOptions(video_codec=video_codec, audio_codec=audio_codec)

    # Assert
    assert opts.video_codec == video_codec
    assert opts.audio_codec == audio_codec


# ===========================================================================
# codec 文字種・長さ制約（Sec M-3）
# ===========================================================================


@pytest.mark.parametrize(
    "codec",
    [
        "libx264 -preset slow",  # スペースを含む
        "libx264; rm -rf /",  # セミコロン・スペースを含む
        "codec|pipe",  # パイプを含む
        "codec && other",  # && を含む
        "a" * 65,  # 65 文字（最大64文字超過）
    ],
)
def test_invalid_video_codec_rejected(codec: str) -> None:
    """不正 video_codec（スペース/記号含む・65文字超）→ ValidationError（Sec M-3）。"""
    with pytest.raises(ValidationError):
        RenderOptions(video_codec=codec)


@pytest.mark.parametrize(
    "codec",
    [
        "aac; rm -rf /",  # セミコロン・スペースを含む
        "opus -vbr on",  # スペースを含む
        "a" * 65,  # 65 文字（最大64文字超過）
    ],
)
def test_invalid_audio_codec_rejected(codec: str) -> None:
    """不正 audio_codec（スペース/記号含む・65文字超）→ ValidationError（Sec M-3）。"""
    with pytest.raises(ValidationError):
        RenderOptions(audio_codec=codec)


@pytest.mark.parametrize(
    "codec",
    [
        "a" * 64,  # ちょうど64文字（境界値・受理）
        "libx264",
        "copy",
        "libvpx-vp9",
        "h264_nvenc",
    ],
)
def test_valid_codec_boundary_accepted(codec: str) -> None:
    """64文字以内・英数字/アンスコ/ハイフンのみのコーデックは受理される（Sec M-3）。"""
    opts = RenderOptions(video_codec=codec)
    assert opts.video_codec == codec


@pytest.mark.parametrize(
    "width,height",
    [
        (1920, 1080),
        (1280, 720),
        (3840, 2160),
        (1, 1),
    ],
)
def test_valid_resolution_pair_accepted(width: int, height: int) -> None:
    """正の整数ペア（width/height 両方指定）を受理すること。"""
    # Arrange / Act
    opts = RenderOptions(width=width, height=height)

    # Assert
    assert opts.width == width
    assert opts.height == height


def test_both_resolution_none_accepted() -> None:
    """width/height 両方 None が妥当であること（既定の None ペア）。"""
    # Arrange / Act
    opts = RenderOptions(width=None, height=None)

    # Assert
    assert opts.width is None
    assert opts.height is None


@pytest.mark.parametrize(
    "fps",
    [24.0, 30.0, 60.0, 23.976, 0.001],
)
def test_valid_fps_accepted(fps: float) -> None:
    """正の fps 値を受理すること。"""
    # Arrange / Act
    opts = RenderOptions(fps=fps)

    # Assert
    assert opts.fps == pytest.approx(fps)


@pytest.mark.parametrize(
    "crf",
    [0, 1, 23, 50, 51],
)
def test_valid_crf_range_accepted(crf: int) -> None:
    """crf が 0〜51 の範囲（境界値含む）を受理すること（DC-AM-004）。"""
    # Arrange / Act
    opts = RenderOptions(crf=crf)

    # Assert
    assert opts.crf == crf


# ===========================================================================
# 解像度ペア制約（DC-AM-004）
# ===========================================================================


@pytest.mark.parametrize(
    "width,height",
    [
        (1920, 1080),
        (1280, 720),
        (None, None),
    ],
)
def test_resolution_pair_both_or_none_is_valid(
    width: int | None, height: int | None
) -> None:
    """「両方指定」と「両方 None」はいずれも妥当であること。"""
    # Arrange / Act / Assert（例外なし）
    opts = RenderOptions(width=width, height=height)
    assert opts.width == width
    assert opts.height == height


@pytest.mark.parametrize(
    "width,height",
    [
        (1920, None),  # width だけ指定
        (None, 1080),  # height だけ指定
    ],
)
def test_resolution_pair_only_one_raises_validation_error(
    width: int | None, height: int | None
) -> None:
    """width/height の片方だけ指定 → ValidationError（INVALID_INPUT）。"""
    # Arrange / Act / Assert
    with pytest.raises(ValidationError):
        RenderOptions(width=width, height=height)


# ===========================================================================
# 不正値の拒否
# ===========================================================================


@pytest.mark.parametrize(
    "width,height",
    [
        (-1, 1080),
        (1920, -1),
        (0, 1080),
        (1920, 0),
        (-1, -1),
        (0, 0),
    ],
)
def test_non_positive_resolution_rejected(width: int, height: int) -> None:
    """負またはゼロの width/height → ValidationError。"""
    # Arrange / Act / Assert
    with pytest.raises(ValidationError):
        RenderOptions(width=width, height=height)


@pytest.mark.parametrize(
    "fps",
    [-1.0, -0.001, 0.0],
)
def test_non_positive_fps_rejected(fps: float) -> None:
    """負またはゼロの fps → ValidationError。"""
    # Arrange / Act / Assert
    with pytest.raises(ValidationError):
        RenderOptions(fps=fps)


@pytest.mark.parametrize(
    "crf",
    [-1, 52, -100, 100],
)
def test_out_of_range_crf_rejected(crf: int) -> None:
    """crf が 0〜51 の範囲外 → ValidationError（DC-AM-004）。"""
    # Arrange / Act / Assert
    with pytest.raises(ValidationError):
        RenderOptions(crf=crf)


# ===========================================================================
# 共通型の再定義なし確認
# ===========================================================================


def test_render_options_does_not_redefine_core_types() -> None:
    """RenderOptions が core 共通型（MediaRef/TimeRange/Artifact）を再定義しないこと。

    clipwright.schemas から import できることを確認し、
    clipwright_render.schemas では同名クラスを定義していないことを検証する。
    """
    # core の共通型が import できること
    from clipwright.schemas import Artifact, MediaRef, ToolResult  # noqa: F401

    # clipwright_render.schemas に同名クラスが存在しないこと
    import clipwright_render.schemas as render_schemas

    assert not hasattr(render_schemas, "MediaRef"), (
        "RenderOptions を定義する schemas.py が core の MediaRef を再定義している"
    )
    assert not hasattr(render_schemas, "Artifact"), (
        "RenderOptions を定義する schemas.py が core の Artifact を再定義している"
    )
    assert not hasattr(render_schemas, "ToolResult"), (
        "RenderOptions を定義する schemas.py が core の ToolResult を再定義している"
    )
