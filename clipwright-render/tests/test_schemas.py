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


# ===========================================================================
# SubtitleOptions — Red テスト（字幕焼き込み・ADR-S2-r2 / ADR-S6-r2 / ADR-S6-r3）
# ===========================================================================
# 実機確認済み (M2 2026-06-11):
#   - Windowsパスエスケープ: \ → \\ then : → \: が確定構文
#   - VTT直読: 可能 (RC=0)
#   - PrimaryColour: 6桁 &HBBGGRR も 8桁 &HAABBGGRR も受理可（不透明は AA=00）
#   - force_style: FontName/FontSize/Outline/Alignment/MarginV 全て受理
#   - fontsdir: :fontsdir='<path>' で受理
#   - Alignment 1/2/5/7/9 全て受理 (ASS v4+ numpad)
#   - ASS + force_style: 受理される（内蔵スタイル優先は libass の動作・エラーなし）


class TestSubtitleOptionsDefaults:
    """SubtitleOptions のデフォルト構築とスタイル系 None を検証する（ADR-S2-r2）。"""

    def test_subtitle_options_path_only(self) -> None:
        """path のみ指定でモデルが構築でき、スタイル系フィールドが None になること。"""
        # Arrange / Act
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/proj/subs.srt")

        # Assert
        assert sub.path == "/proj/subs.srt"
        assert sub.font_name is None
        assert sub.fonts_dir is None
        assert sub.font_size is None
        assert sub.font_color is None
        assert sub.outline is None
        assert sub.alignment is None
        assert sub.margin_v is None

    def test_subtitle_options_path_empty_raises_validation_error(self) -> None:
        """path が空文字 → ValidationError（DC-AM-005・min_length=1）。"""
        # Arrange / Act / Assert
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="")

    def test_subtitle_options_path_required(self) -> None:
        """path を省略すると ValidationError（必須フィールド）。"""
        # Arrange / Act / Assert
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions()  # type: ignore[call-arg]

    def test_subtitle_options_extra_field_forbidden(self) -> None:
        """未知フィールドは extra='forbid' で ValidationError（ADR-S2-r2）。"""
        # Arrange / Act / Assert
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", unknown_key="evil")  # type: ignore[call-arg]

    def test_path_with_single_quote_raises_validation_error(self) -> None:
        """path に ''' を含む → ValidationError（S-H-1 / CR-E-001）。

        ffmpeg filtergraph は filename='{path}' のシングルクォート囲みを使うため、
        パスにシングルクォートが含まれると ffmpeg の filtergraph パーサーが構文エラーになる。
        これを防ぐため Pydantic バリデーション層でシングルクォートを禁止する（CR-E-001）。
        """
        # Arrange / Act / Assert
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/proj/sub's.srt")

    def test_fonts_dir_with_single_quote_raises_validation_error(self) -> None:
        """fonts_dir に ''' を含む → ValidationError（S-H-1 / CR-E-001）。

        fontsdir='{dir}' 形式のシングルクォート囲みで構文破綻が起きるため
        path と同様にシングルクォートを禁止する（CR-E-001）。
        """
        # Arrange / Act / Assert
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", fonts_dir="/proj/my'fonts")


class TestSubtitleOptionsFontName:
    """font_name の許可文字・禁止文字を検証する（DC-AM-004・ADR-S2-r2）。"""

    def test_font_name_ascii_accepted(self) -> None:
        """英数字・スペース・ハイフンのフォント名は有効（DC-AM-004）。"""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", font_name="Noto Sans CJK JP")
        assert sub.font_name == "Noto Sans CJK JP"

    def test_font_name_japanese_accepted(self) -> None:
        """日本語フォント名（CJK 文字列）は許可される（ADR-S2-r2）。"""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", font_name="游ゴシック")
        assert sub.font_name == "游ゴシック"

    def test_font_name_with_comma_raises_validation_error(self) -> None:
        """font_name に ',' (force_style 区切り) を含む → ValidationError（DC-AM-004）。"""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_name="Font,Name")

    def test_font_name_with_colon_raises_validation_error(self) -> None:
        """font_name に ':' (filtergraph 区切り) を含む → ValidationError（DC-AM-004）。"""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_name="Font:Name")

    def test_font_name_with_single_quote_raises_validation_error(self) -> None:
        """font_name に ''' (フィルタ引数区切り) を含む → ValidationError（DC-AM-004）。"""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_name="Font'Name")

    def test_font_name_with_backslash_raises_validation_error(self) -> None:
        """font_name に '\\' (エスケープ文字) を含む → ValidationError（DC-AM-004）。"""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_name="Font\\Name")

    def test_font_name_with_bracket_raises_validation_error(self) -> None:
        """font_name に '[' を含む → ValidationError（filtergraph ラベル区切り）。"""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_name="Font[Name]")

    def test_font_name_with_semicolon_raises_validation_error(self) -> None:
        """font_name に ';' を含む → ValidationError（filtergraph 区切り）。"""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_name="Font;Name")

    def test_font_name_with_equals_raises_validation_error(self) -> None:
        """font_name に '=' を含む → ValidationError（filtergraph キー/値区切り）。"""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_name="Font=Name")

    def test_font_name_with_hash_raises_validation_error(self) -> None:
        """font_name に '#' を含む → ValidationError（libass 色表記誤解釈リスク・SR-L-2）。

        libass の一部バージョンで FontName 値の '#' が色表記と誤解釈されるリスクがあるため
        防御的に禁止する（SR-NEW）。_FONT_NAME_FORBIDDEN_CHARS_RE に '#' を追加済み。
        """
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_name="Font#Name")

    def test_font_name_none_accepted(self) -> None:
        """font_name=None はデフォルトとして有効。"""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", font_name=None)
        assert sub.font_name is None


class TestSubtitleOptionsNumericConstraints:
    """font_size / outline / margin_v の範囲制約を検証する（ADR-S2-r2）。"""

    def test_font_size_positive_accepted(self) -> None:
        """font_size=24（正の整数）は有効（gt=0）。"""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", font_size=24)
        assert sub.font_size == 24

    def test_font_size_zero_raises_validation_error(self) -> None:
        """font_size=0 → ValidationError（gt=0 制約）。"""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_size=0)

    def test_font_size_negative_raises_validation_error(self) -> None:
        """font_size=-1 → ValidationError（gt=0 制約）。"""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_size=-1)

    def test_outline_zero_accepted(self) -> None:
        """outline=0（境界値 ge=0）は有効。"""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", outline=0.0)
        assert sub.outline == 0.0

    def test_outline_negative_raises_validation_error(self) -> None:
        """outline=-0.1 → ValidationError（ge=0 制約）。"""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", outline=-0.1)

    def test_margin_v_zero_accepted(self) -> None:
        """margin_v=0（境界値 ge=0）は有効。"""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", margin_v=0)
        assert sub.margin_v == 0

    def test_margin_v_negative_raises_validation_error(self) -> None:
        """margin_v=-1 → ValidationError（ge=0 制約）。"""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", margin_v=-1)

    def test_font_size_exceeds_max_raises_validation_error(self) -> None:
        """font_size が上限（1_000_000_000）を超える → ValidationError（le=_FONT_SIZE_MAX）。"""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_size=int(1e9 + 1))  # 上限超過

    def test_outline_inf_raises_validation_error(self) -> None:
        """outline=inf → ValidationError（allow_inf_nan=False・model_config + フィールドレベル）。

        SubtitleOptions.model_config に allow_inf_nan=False が設定されており、
        float フィールドへの inf 混入を model_config レベルで防ぐ（SR-V-001）。
        """
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", outline=float("inf"))

    def test_outline_nan_raises_validation_error(self) -> None:
        """outline=nan → ValidationError（allow_inf_nan=False）。"""
        import math

        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", outline=math.nan)


class TestSubtitleOptionsFontColor:
    """font_color の #RRGGBB パターン制約を検証する（ADR-S2-r2）。"""

    def test_font_color_valid_hex_accepted(self) -> None:
        """#RRGGBB 形式の有効な色文字列は受理される。"""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", font_color="#FFFFFF")
        assert sub.font_color == "#FFFFFF"

    def test_font_color_lowercase_hex_accepted(self) -> None:
        """小文字 #ffffff も有効（大文字小文字不問）。"""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", font_color="#ff0000")
        assert sub.font_color == "#ff0000"

    def test_font_color_invalid_format_raises_validation_error(self) -> None:
        """#RRGGBB 形式でない文字列（例: 'red', 'rgb(255,0,0)'）→ ValidationError。"""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_color="red")

    def test_font_color_short_hex_raises_validation_error(self) -> None:
        """3桁の短縮 HEX (#RGB) → ValidationError（6桁必須）。"""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_color="#FFF")

    def test_font_color_without_hash_raises_validation_error(self) -> None:
        """# なし ('FFFFFF') → ValidationError。"""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_color="FFFFFF")

    def test_font_color_none_accepted(self) -> None:
        """font_color=None はデフォルトとして有効（スタイル系全 Optional）。"""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", font_color=None)
        assert sub.font_color is None


class TestSubtitleOptionsAlignment:
    """alignment の 1〜9 範囲制約を検証する（ASS v4+ numpad・DC-AM-001）。"""

    @pytest.mark.parametrize("alignment", [1, 2, 3, 4, 5, 6, 7, 8, 9])
    def test_alignment_valid_range_accepted(self, alignment: int) -> None:
        """alignment 1〜9 の整数値はすべて受理される（ASS v4+ numpad 全体）。"""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", alignment=alignment)
        assert sub.alignment == alignment

    def test_alignment_zero_raises_validation_error(self) -> None:
        """alignment=0 → ValidationError（1〜9 の範囲外）。"""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", alignment=0)

    def test_alignment_ten_raises_validation_error(self) -> None:
        """alignment=10 → ValidationError（1〜9 の範囲外）。"""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", alignment=10)

    def test_alignment_negative_raises_validation_error(self) -> None:
        """alignment=-1 → ValidationError（1〜9 の範囲外）。"""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", alignment=-1)


class TestRenderOptionsSubtitleField:
    """RenderOptions.subtitle フィールドの追加を検証する（ADR-S2-r2 / ADR-S8）。"""

    def test_render_options_subtitle_default_none(self) -> None:
        """RenderOptions() のデフォルトで subtitle が None（後方互換・ADR-S8）。"""
        opts = RenderOptions()
        assert opts.subtitle is None  # type: ignore[attr-defined]

    def test_render_options_subtitle_accepts_subtitle_options(self) -> None:
        """RenderOptions(subtitle=SubtitleOptions(...)) が受理される（ADR-S2-r2）。"""
        from clipwright_render.schemas import SubtitleOptions

        opts = RenderOptions(
            subtitle=SubtitleOptions(path="/proj/subs.srt", font_size=24)  # type: ignore[call-arg]
        )
        assert opts.subtitle is not None  # type: ignore[attr-defined]
        assert opts.subtitle.path == "/proj/subs.srt"  # type: ignore[attr-defined]
        assert opts.subtitle.font_size == 24  # type: ignore[attr-defined]

    def test_render_options_subtitle_nested_validation(self) -> None:
        """RenderOptions を通じて SubtitleOptions のネスト検証が効く（ADR-S2-r2）。

        font_size=0 は SubtitleOptions で ValidationError になる。
        RenderOptions のネスト経由でも同様に ValidationError になることを確認する。
        """
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            RenderOptions(
                subtitle=SubtitleOptions(path="/sub.srt", font_size=0)  # type: ignore[call-arg]
            )
