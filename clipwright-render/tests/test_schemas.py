"""test_schemas.py — Red tests for RenderOptions (DC-AM-004).

Fixes the confirmed RenderOptions specification from architecture §6.1.
This file is intended to fail when schemas.py does not exist or RenderOptions
is not yet implemented.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from clipwright_render.schemas import RenderOptions

# ===========================================================================
# Default construction
# ===========================================================================


class TestRenderOptionsDefaults:
    """Verify that the model can be constructed with no arguments and each field has its default value."""

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
# Valid value acceptance
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
    """Valid codec strings (alphanumeric, underscore, hyphen) are accepted (Sec M-3)."""
    # Arrange / Act
    opts = RenderOptions(video_codec=video_codec, audio_codec=audio_codec)

    # Assert
    assert opts.video_codec == video_codec
    assert opts.audio_codec == audio_codec


# ===========================================================================
# codec character/length constraints (Sec M-3)
# ===========================================================================


@pytest.mark.parametrize(
    "codec",
    [
        "libx264 -preset slow",  # contains a space
        "libx264; rm -rf /",  # contains semicolon and space
        "codec|pipe",  # contains pipe
        "codec && other",  # contains &&
        "a" * 65,  # 65 characters (exceeds max 64)
    ],
)
def test_invalid_video_codec_rejected(codec: str) -> None:
    """Invalid video_codec (spaces/symbols or exceeds 64 chars) raises ValidationError (Sec M-3)."""
    with pytest.raises(ValidationError):
        RenderOptions(video_codec=codec)


@pytest.mark.parametrize(
    "codec",
    [
        "aac; rm -rf /",  # contains semicolon and space
        "opus -vbr on",  # contains a space
        "a" * 65,  # 65 characters (exceeds max 64)
    ],
)
def test_invalid_audio_codec_rejected(codec: str) -> None:
    """Invalid audio_codec (spaces/symbols or exceeds 64 chars) raises ValidationError (Sec M-3)."""
    with pytest.raises(ValidationError):
        RenderOptions(audio_codec=codec)


@pytest.mark.parametrize(
    "codec",
    [
        "a" * 64,  # exactly 64 characters (boundary — accepted)
        "libx264",
        "copy",
        "libvpx-vp9",
        "h264_nvenc",
    ],
)
def test_valid_codec_boundary_accepted(codec: str) -> None:
    """Codec strings up to 64 chars with only alphanumeric/underscore/hyphen are accepted (Sec M-3)."""
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
    """A pair of positive integers (both width and height specified) is accepted."""
    # Arrange / Act
    opts = RenderOptions(width=width, height=height)

    # Assert
    assert opts.width == width
    assert opts.height == height


def test_both_resolution_none_accepted() -> None:
    """width/height both None is valid (the default None pair)."""
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
    """Positive fps values are accepted."""
    # Arrange / Act
    opts = RenderOptions(fps=fps)

    # Assert
    assert opts.fps == pytest.approx(fps)


@pytest.mark.parametrize(
    "crf",
    [0, 1, 23, 50, 51],
)
def test_valid_crf_range_accepted(crf: int) -> None:
    """crf values in range 0-51 (including boundaries) are accepted (DC-AM-004)."""
    # Arrange / Act
    opts = RenderOptions(crf=crf)

    # Assert
    assert opts.crf == crf


# ===========================================================================
# Resolution pair constraint (DC-AM-004)
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
    """Both specified or both None are valid."""
    # Arrange / Act / Assert (no exception)
    opts = RenderOptions(width=width, height=height)
    assert opts.width == width
    assert opts.height == height


@pytest.mark.parametrize(
    "width,height",
    [
        (1920, None),  # width only
        (None, 1080),  # height only
    ],
)
def test_resolution_pair_only_one_raises_validation_error(
    width: int | None, height: int | None
) -> None:
    """Specifying only one of width/height raises ValidationError (INVALID_INPUT)."""
    # Arrange / Act / Assert
    with pytest.raises(ValidationError):
        RenderOptions(width=width, height=height)


# ===========================================================================
# Invalid value rejection
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
    """Negative or zero width/height raises ValidationError."""
    # Arrange / Act / Assert
    with pytest.raises(ValidationError):
        RenderOptions(width=width, height=height)


@pytest.mark.parametrize(
    "fps",
    [-1.0, -0.001, 0.0],
)
def test_non_positive_fps_rejected(fps: float) -> None:
    """Negative or zero fps raises ValidationError."""
    # Arrange / Act / Assert
    with pytest.raises(ValidationError):
        RenderOptions(fps=fps)


@pytest.mark.parametrize(
    "crf",
    [-1, 52, -100, 100],
)
def test_out_of_range_crf_rejected(crf: int) -> None:
    """crf outside 0-51 raises ValidationError (DC-AM-004)."""
    # Arrange / Act / Assert
    with pytest.raises(ValidationError):
        RenderOptions(crf=crf)


# ===========================================================================
# Verify core types are not redefined
# ===========================================================================


def test_render_options_does_not_redefine_core_types() -> None:
    """RenderOptions must not redefine core shared types (MediaRef/TimeRange/Artifact).

    Confirms that they can be imported from clipwright.schemas, and that
    clipwright_render.schemas does not define classes with the same names.
    """
    # Core shared types must be importable
    from clipwright.schemas import Artifact, MediaRef, ToolResult  # noqa: F401

    # clipwright_render.schemas must not define classes with the same names
    import clipwright_render.schemas as render_schemas

    assert not hasattr(render_schemas, "MediaRef"), (
        "schemas.py defining RenderOptions is redefining core MediaRef"
    )
    assert not hasattr(render_schemas, "Artifact"), (
        "schemas.py defining RenderOptions is redefining core Artifact"
    )
    assert not hasattr(render_schemas, "ToolResult"), (
        "schemas.py defining RenderOptions is redefining core ToolResult"
    )


# ===========================================================================
# SubtitleOptions — Red tests (subtitle burn-in / ADR-S2-r2 / ADR-S6-r2 / ADR-S6-r3)
# ===========================================================================
# Verified on real hardware (M2 2026-06-11):
#   - Windows path escaping: \ -> \\ then : -> \: is the confirmed syntax
#   - VTT direct read: works (RC=0)
#   - PrimaryColour: both 6-digit &HBBGGRR and 8-digit &HAABBGGRR accepted
#     (opaque = AA=00)
#   - force_style: FontName/FontSize/Outline/Alignment/MarginV all accepted
#   - fontsdir: :fontsdir='<path>' accepted
#   - Alignment 1/2/5/7/9 all accepted (ASS v4+ numpad)
#   - ASS + force_style: accepted (built-in styles take precedence — libass behaviour,
#     no error)


class TestSubtitleOptionsDefaults:
    """Verify SubtitleOptions default construction and None style fields (ADR-S2-r2)."""

    def test_subtitle_options_path_only(self) -> None:
        """Model constructed with path only; all style fields default to None."""
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
        """Empty path raises ValidationError (DC-AM-005 / min_length=1)."""
        # Arrange / Act / Assert
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="")

    def test_subtitle_options_path_required(self) -> None:
        """Omitting path raises ValidationError (required field)."""
        # Arrange / Act / Assert
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions()  # type: ignore[call-arg]

    def test_subtitle_options_extra_field_forbidden(self) -> None:
        """Unknown fields raise ValidationError due to extra='forbid' (ADR-S2-r2)."""
        # Arrange / Act / Assert
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", unknown_key="evil")  # type: ignore[call-arg]

    def test_path_with_single_quote_raises_validation_error(self) -> None:
        """A path containing a single quote (') raises ValidationError (S-H-1 / CR-E-001).

        ffmpeg filtergraph uses filename='{path}' with single-quote wrapping, so
        a single quote in the path breaks the filtergraph parser syntax.
        Single quotes are therefore forbidden at the Pydantic validation layer (CR-E-001).
        """
        # Arrange / Act / Assert
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/proj/sub's.srt")

    def test_fonts_dir_with_single_quote_raises_validation_error(self) -> None:
        """A fonts_dir containing a single quote (') raises ValidationError (S-H-1 / CR-E-001).

        The fontsdir='{dir}' single-quote wrapping would break the filtergraph syntax,
        so single quotes are forbidden in fonts_dir for the same reason as path (CR-E-001).
        """
        # Arrange / Act / Assert
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", fonts_dir="/proj/my'fonts")


class TestSubtitleOptionsFontName:
    """Verify allowed and forbidden characters in font_name (DC-AM-004 / ADR-S2-r2)."""

    def test_font_name_ascii_accepted(self) -> None:
        """Alphanumeric, space, and hyphen font names are valid (DC-AM-004)."""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", font_name="Noto Sans CJK JP")
        assert sub.font_name == "Noto Sans CJK JP"

    def test_font_name_japanese_accepted(self) -> None:
        """Japanese font names (CJK characters) are allowed (ADR-S2-r2)."""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", font_name="游ゴシック")
        assert sub.font_name == "游ゴシック"

    def test_font_name_with_comma_raises_validation_error(self) -> None:
        """font_name containing ',' (force_style separator) raises ValidationError (DC-AM-004)."""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_name="Font,Name")

    def test_font_name_with_colon_raises_validation_error(self) -> None:
        """font_name containing ':' (filtergraph separator) raises ValidationError (DC-AM-004)."""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_name="Font:Name")

    def test_font_name_with_single_quote_raises_validation_error(self) -> None:
        """font_name containing ''' (filter argument separator) raises ValidationError (DC-AM-004)."""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_name="Font'Name")

    def test_font_name_with_backslash_raises_validation_error(self) -> None:
        """font_name containing '\\' (escape character) raises ValidationError (DC-AM-004)."""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_name="Font\\Name")

    def test_font_name_with_bracket_raises_validation_error(self) -> None:
        """font_name containing '[' raises ValidationError (filtergraph label separator)."""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_name="Font[Name]")

    def test_font_name_with_semicolon_raises_validation_error(self) -> None:
        """font_name containing ';' raises ValidationError (filtergraph separator)."""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_name="Font;Name")

    def test_font_name_with_equals_raises_validation_error(self) -> None:
        """font_name containing '=' raises ValidationError (filtergraph key/value separator)."""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_name="Font=Name")

    def test_font_name_with_hash_raises_validation_error(self) -> None:
        """font_name containing '#' raises ValidationError (libass colour misparse risk / SR-L-2).

        Some libass versions may misinterpret '#' in a FontName value as a colour specifier.
        Defensively forbidden (SR-NEW). '#' added to _FONT_NAME_FORBIDDEN_CHARS_RE.
        """
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_name="Font#Name")

    def test_font_name_none_accepted(self) -> None:
        """font_name=None is valid as the default."""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", font_name=None)
        assert sub.font_name is None


class TestSubtitleOptionsNumericConstraints:
    """Verify range constraints on font_size / outline / margin_v (ADR-S2-r2)."""

    def test_font_size_positive_accepted(self) -> None:
        """font_size=24 (positive integer) is valid (gt=0)."""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", font_size=24)
        assert sub.font_size == 24

    def test_font_size_zero_raises_validation_error(self) -> None:
        """font_size=0 raises ValidationError (gt=0 constraint)."""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_size=0)

    def test_font_size_negative_raises_validation_error(self) -> None:
        """font_size=-1 raises ValidationError (gt=0 constraint)."""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_size=-1)

    def test_outline_zero_accepted(self) -> None:
        """outline=0 (boundary value ge=0) is valid."""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", outline=0.0)
        assert sub.outline == 0.0

    def test_outline_negative_raises_validation_error(self) -> None:
        """outline=-0.1 raises ValidationError (ge=0 constraint)."""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", outline=-0.1)

    def test_margin_v_zero_accepted(self) -> None:
        """margin_v=0 (boundary value ge=0) is valid."""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", margin_v=0)
        assert sub.margin_v == 0

    def test_margin_v_negative_raises_validation_error(self) -> None:
        """margin_v=-1 raises ValidationError (ge=0 constraint)."""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", margin_v=-1)

    def test_font_size_exceeds_max_raises_validation_error(self) -> None:
        """font_size exceeding the upper limit (1_000_000_000) raises ValidationError."""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_size=int(1e9 + 1))  # exceeds limit

    def test_outline_inf_raises_validation_error(self) -> None:
        """outline=inf raises ValidationError (allow_inf_nan=False in model_config + field level).

        SubtitleOptions.model_config sets allow_inf_nan=False, preventing inf from
        entering any float field at the model_config level (SR-V-001).
        """
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", outline=float("inf"))

    def test_outline_nan_raises_validation_error(self) -> None:
        """outline=nan raises ValidationError (allow_inf_nan=False)."""
        import math

        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", outline=math.nan)


class TestSubtitleOptionsFontColor:
    """Verify the #RRGGBB pattern constraint on font_color (ADR-S2-r2)."""

    def test_font_color_valid_hex_accepted(self) -> None:
        """A valid colour string in #RRGGBB format is accepted."""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", font_color="#FFFFFF")
        assert sub.font_color == "#FFFFFF"

    def test_font_color_lowercase_hex_accepted(self) -> None:
        """Lowercase #ffffff is also valid (case-insensitive)."""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", font_color="#ff0000")
        assert sub.font_color == "#ff0000"

    def test_font_color_invalid_format_raises_validation_error(self) -> None:
        """Non-#RRGGBB strings (e.g. 'red', 'rgb(255,0,0)') raise ValidationError."""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_color="red")

    def test_font_color_short_hex_raises_validation_error(self) -> None:
        """3-digit shorthand hex (#RGB) raises ValidationError (6 digits required)."""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_color="#FFF")

    def test_font_color_without_hash_raises_validation_error(self) -> None:
        """'FFFFFF' without leading '#' raises ValidationError."""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", font_color="FFFFFF")

    def test_font_color_none_accepted(self) -> None:
        """font_color=None is valid as the default (all style fields are Optional)."""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", font_color=None)
        assert sub.font_color is None


class TestSubtitleOptionsAlignment:
    """Verify the 1-9 range constraint on alignment (ASS v4+ numpad / DC-AM-001)."""

    @pytest.mark.parametrize("alignment", [1, 2, 3, 4, 5, 6, 7, 8, 9])
    def test_alignment_valid_range_accepted(self, alignment: int) -> None:
        """All integers 1-9 are accepted for alignment (full ASS v4+ numpad range)."""
        from clipwright_render.schemas import SubtitleOptions

        sub = SubtitleOptions(path="/sub.srt", alignment=alignment)
        assert sub.alignment == alignment

    def test_alignment_zero_raises_validation_error(self) -> None:
        """alignment=0 raises ValidationError (outside 1-9 range)."""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", alignment=0)

    def test_alignment_ten_raises_validation_error(self) -> None:
        """alignment=10 raises ValidationError (outside 1-9 range)."""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", alignment=10)

    def test_alignment_negative_raises_validation_error(self) -> None:
        """alignment=-1 raises ValidationError (outside 1-9 range)."""
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            SubtitleOptions(path="/sub.srt", alignment=-1)


class TestRenderOptionsSubtitleField:
    """Verify the RenderOptions.subtitle field addition (ADR-S2-r2 / ADR-S8)."""

    def test_render_options_subtitle_default_none(self) -> None:
        """RenderOptions() defaults subtitle to None (backward compatible / ADR-S8)."""
        opts = RenderOptions()
        assert opts.subtitle is None  # type: ignore[attr-defined]

    def test_render_options_subtitle_accepts_subtitle_options(self) -> None:
        """RenderOptions(subtitle=SubtitleOptions(...)) is accepted (ADR-S2-r2)."""
        from clipwright_render.schemas import SubtitleOptions

        opts = RenderOptions(
            subtitle=SubtitleOptions(path="/proj/subs.srt", font_size=24)  # type: ignore[call-arg]
        )
        assert opts.subtitle is not None  # type: ignore[attr-defined]
        assert opts.subtitle.path == "/proj/subs.srt"  # type: ignore[attr-defined]
        assert opts.subtitle.font_size == 24  # type: ignore[attr-defined]

    def test_render_options_subtitle_nested_validation(self) -> None:
        """Nested SubtitleOptions validation is enforced through RenderOptions (ADR-S2-r2).

        font_size=0 causes a ValidationError in SubtitleOptions.
        The same ValidationError must be raised when passing through RenderOptions nesting.
        """
        from clipwright_render.schemas import SubtitleOptions

        with pytest.raises(ValidationError):
            RenderOptions(
                subtitle=SubtitleOptions(path="/sub.srt", font_size=0)  # type: ignore[call-arg]
            )
