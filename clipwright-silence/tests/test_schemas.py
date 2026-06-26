"""test_schemas.py — Tests for DetectSilenceOptions.

Fixes the DetectSilenceOptions specification from architecture §AD-2/AD-3 and DC-AM-001
as test observations.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from clipwright_silence.schemas import DetectSilenceOptions

# ===========================================================================
# Default construction
# ===========================================================================


class TestDetectSilenceOptionsDefaults:
    """Model must be constructable with all fields omitted, and each field must have a default."""

    def test_build_with_no_args(self) -> None:
        # Arrange / Act
        opts = DetectSilenceOptions()

        # Assert
        assert opts.silence_threshold_db == pytest.approx(-30.0)
        assert opts.min_silence_duration == pytest.approx(0.5)
        assert opts.padding == pytest.approx(0.1)
        assert opts.min_keep_duration == pytest.approx(0.0)

    def test_default_silence_threshold_db_is_negative(self) -> None:
        """Default value of silence_threshold_db must be negative (within dB <= 0 constraint)."""
        opts = DetectSilenceOptions()
        assert opts.silence_threshold_db <= 0.0

    def test_default_min_silence_duration_is_positive(self) -> None:
        """Default value of min_silence_duration must be positive (within > 0 constraint)."""
        opts = DetectSilenceOptions()
        assert opts.min_silence_duration > 0.0

    def test_default_padding_is_non_negative(self) -> None:
        """Default value of padding must be non-negative (within >= 0 constraint)."""
        opts = DetectSilenceOptions()
        assert opts.padding >= 0.0

    def test_default_min_keep_duration_is_zero(self) -> None:
        """Default of min_keep_duration is 0.0 (DC-AM-001: opt-in guard)."""
        opts = DetectSilenceOptions()
        assert opts.min_keep_duration == pytest.approx(0.0)


# ===========================================================================
# Valid value acceptance
# ===========================================================================


@pytest.mark.parametrize(
    "threshold",
    [-10.0, -20.0, -30.0, -40.0, -60.0, 0.0],
)
def test_valid_silence_threshold_db_accepted(threshold: float) -> None:
    """Values <= 0 must be accepted as silence_threshold_db."""
    opts = DetectSilenceOptions(silence_threshold_db=threshold)
    assert opts.silence_threshold_db == pytest.approx(threshold)


@pytest.mark.parametrize(
    "duration",
    [0.001, 0.1, 0.5, 1.0, 5.0, 10.0],
)
def test_valid_min_silence_duration_accepted(duration: float) -> None:
    """Values > 0 must be accepted as min_silence_duration."""
    opts = DetectSilenceOptions(min_silence_duration=duration)
    assert opts.min_silence_duration == pytest.approx(duration)


@pytest.mark.parametrize(
    "pad",
    [0.0, 0.05, 0.1, 0.5, 1.0, 2.0],
)
def test_valid_padding_accepted(pad: float) -> None:
    """Values >= 0 must be accepted as padding."""
    opts = DetectSilenceOptions(padding=pad)
    assert opts.padding == pytest.approx(pad)


@pytest.mark.parametrize(
    "min_keep",
    [0.0, 0.1, 0.5, 1.0, 2.0],
)
def test_valid_min_keep_duration_accepted(min_keep: float) -> None:
    """Values >= 0 must be accepted as min_keep_duration."""
    opts = DetectSilenceOptions(min_keep_duration=min_keep)
    assert opts.min_keep_duration == pytest.approx(min_keep)


# ===========================================================================
# Constraint violations (ValidationError)
# ===========================================================================


@pytest.mark.parametrize(
    "threshold",
    [0.1, 1.0, 10.0, 0.001],
)
def test_positive_silence_threshold_db_rejected(threshold: float) -> None:
    """Positive value for silence_threshold_db -> ValidationError (constraint: <= 0)."""
    with pytest.raises(ValidationError):
        DetectSilenceOptions(silence_threshold_db=threshold)


@pytest.mark.parametrize(
    "duration",
    [0.0, -0.001, -1.0, -5.0],
)
def test_non_positive_min_silence_duration_rejected(duration: float) -> None:
    """Value <= 0 for min_silence_duration -> ValidationError (constraint: > 0)."""
    with pytest.raises(ValidationError):
        DetectSilenceOptions(min_silence_duration=duration)


@pytest.mark.parametrize(
    "pad",
    [-0.001, -0.1, -1.0, -5.0],
)
def test_negative_padding_rejected(pad: float) -> None:
    """Negative value for padding -> ValidationError (constraint: >= 0)."""
    with pytest.raises(ValidationError):
        DetectSilenceOptions(padding=pad)


@pytest.mark.parametrize(
    "min_keep",
    [-0.001, -0.1, -1.0, -5.0],
)
def test_negative_min_keep_duration_rejected(min_keep: float) -> None:
    """Negative value for min_keep_duration -> ValidationError (constraint: >= 0)."""
    with pytest.raises(ValidationError):
        DetectSilenceOptions(min_keep_duration=min_keep)


# ===========================================================================
# All fields specified
# ===========================================================================


def test_all_fields_specified_accepted() -> None:
    """Model must be constructable with all fields explicitly set to valid values."""
    opts = DetectSilenceOptions(
        silence_threshold_db=-25.0,
        min_silence_duration=0.3,
        padding=0.05,
        min_keep_duration=1.0,
    )
    assert opts.silence_threshold_db == pytest.approx(-25.0)
    assert opts.min_silence_duration == pytest.approx(0.3)
    assert opts.padding == pytest.approx(0.05)
    assert opts.min_keep_duration == pytest.approx(1.0)


# ===========================================================================
# No redefinition of core types
# ===========================================================================


def test_detect_silence_options_does_not_redefine_core_types() -> None:
    """schemas.py must not redefine core common types (MediaRef/Artifact/ToolResult)."""
    # core common types must be importable
    from clipwright.schemas import Artifact, MediaRef, ToolResult  # noqa: F401

    # clipwright_silence.schemas must not have classes with the same names
    import clipwright_silence.schemas as silence_schemas

    assert not hasattr(silence_schemas, "MediaRef"), (
        "schemas.py redefines MediaRef from core"
    )
    assert not hasattr(silence_schemas, "Artifact"), (
        "schemas.py redefines Artifact from core"
    )
    assert not hasattr(silence_schemas, "ToolResult"), (
        "schemas.py redefines ToolResult from core"
    )


# ===========================================================================
# VAD extension fields (VAD-AD-01 / VAD-AD-05 / §7.6)
# ===========================================================================


class TestBackendField:
    """Validate type, default, and constraints for the backend field (VAD-AD-01)."""

    def test_backend_default_is_silencedetect(self) -> None:
        """Default value of backend when not specified must be "silencedetect".

        Backward-compatible opt-in VAD.
        """
        opts = DetectSilenceOptions()
        assert opts.backend == "silencedetect"

    def test_backend_silencedetect_accepted(self) -> None:
        """backend="silencedetect" must be accepted."""
        opts = DetectSilenceOptions(backend="silencedetect")
        assert opts.backend == "silencedetect"

    def test_backend_vad_accepted(self) -> None:
        """backend="vad" must be accepted."""
        opts = DetectSilenceOptions(backend="vad")
        assert opts.backend == "vad"

    @pytest.mark.parametrize(
        "invalid_backend",
        ["whisper", "auto", "VAD", "Silencedetect", "", "none", "ffmpeg"],
    )
    def test_invalid_backend_rejected(self, invalid_backend: str) -> None:
        """Value outside the Literal -> ValidationError."""
        with pytest.raises(ValidationError):
            DetectSilenceOptions(backend=invalid_backend)  # type: ignore[arg-type]


class TestVadThresholdField:
    """Validate type, default, and range constraints for vad_threshold (VAD-AD-05)."""

    def test_vad_threshold_default_is_0_5(self) -> None:
        """Default value of vad_threshold must be 0.5."""
        opts = DetectSilenceOptions()
        assert opts.vad_threshold == pytest.approx(0.5)

    @pytest.mark.parametrize(
        "threshold",
        [0.0, 0.1, 0.5, 0.9, 1.0],
    )
    def test_valid_vad_threshold_accepted(self, threshold: float) -> None:
        """Values in range 0.0-1.0 must be accepted as vad_threshold."""
        opts = DetectSilenceOptions(vad_threshold=threshold)
        assert opts.vad_threshold == pytest.approx(threshold)

    @pytest.mark.parametrize(
        "threshold",
        [-0.001, -0.1, -1.0, 1.001, 1.5, 2.0],
    )
    def test_out_of_range_vad_threshold_rejected(self, threshold: float) -> None:
        """Values outside range 0.0-1.0 for vad_threshold -> ValidationError."""
        with pytest.raises(ValidationError):
            DetectSilenceOptions(vad_threshold=threshold)


class TestVadMinSpeechDurationField:
    """Validate type, default, and constraints for vad_min_speech_duration (VAD-AD-05)."""

    def test_vad_min_speech_duration_default_is_0_25(self) -> None:
        """Default value of vad_min_speech_duration must be 0.25."""
        opts = DetectSilenceOptions()
        assert opts.vad_min_speech_duration == pytest.approx(0.25)

    @pytest.mark.parametrize(
        "duration",
        [0.001, 0.1, 0.25, 0.5, 1.0, 5.0],
    )
    def test_valid_vad_min_speech_duration_accepted(self, duration: float) -> None:
        """Values > 0 must be accepted as vad_min_speech_duration."""
        opts = DetectSilenceOptions(vad_min_speech_duration=duration)
        assert opts.vad_min_speech_duration == pytest.approx(duration)

    @pytest.mark.parametrize(
        "duration",
        [0.0, -0.001, -0.1, -1.0],
    )
    def test_non_positive_vad_min_speech_duration_rejected(
        self, duration: float
    ) -> None:
        """Value <= 0 for vad_min_speech_duration -> ValidationError (constraint: > 0)."""
        with pytest.raises(ValidationError):
            DetectSilenceOptions(vad_min_speech_duration=duration)


class TestVadMinSilenceDurationField:
    """Validate type, default, and constraints for vad_min_silence_duration (VAD-AD-05)."""

    def test_vad_min_silence_duration_default_is_0_1(self) -> None:
        """Default value of vad_min_silence_duration must be 0.1."""
        opts = DetectSilenceOptions()
        assert opts.vad_min_silence_duration == pytest.approx(0.1)

    @pytest.mark.parametrize(
        "duration",
        [0.001, 0.05, 0.1, 0.5, 1.0, 3.0],
    )
    def test_valid_vad_min_silence_duration_accepted(self, duration: float) -> None:
        """Values > 0 must be accepted as vad_min_silence_duration."""
        opts = DetectSilenceOptions(vad_min_silence_duration=duration)
        assert opts.vad_min_silence_duration == pytest.approx(duration)

    @pytest.mark.parametrize(
        "duration",
        [0.0, -0.001, -0.1, -1.0],
    )
    def test_non_positive_vad_min_silence_duration_rejected(
        self, duration: float
    ) -> None:
        """Value <= 0 for vad_min_silence_duration -> ValidationError (constraint: > 0)."""
        with pytest.raises(ValidationError):
            DetectSilenceOptions(vad_min_silence_duration=duration)


class TestExistingFieldsUnchanged:
    """Existing field defaults and constraints must remain unchanged after VAD extension (non-regression)."""

    def test_silence_threshold_db_default_unchanged(self) -> None:
        """Default value of silence_threshold_db must remain -30.0."""
        opts = DetectSilenceOptions()
        assert opts.silence_threshold_db == pytest.approx(-30.0)

    def test_min_silence_duration_default_unchanged(self) -> None:
        """Default value of min_silence_duration must remain 0.5."""
        opts = DetectSilenceOptions()
        assert opts.min_silence_duration == pytest.approx(0.5)

    def test_padding_default_unchanged(self) -> None:
        """Default value of padding must remain 0.1."""
        opts = DetectSilenceOptions()
        assert opts.padding == pytest.approx(0.1)

    def test_min_keep_duration_default_unchanged(self) -> None:
        """Default value of min_keep_duration must remain 0.0."""
        opts = DetectSilenceOptions()
        assert opts.min_keep_duration == pytest.approx(0.0)

    def test_positive_silence_threshold_db_still_rejected(self) -> None:
        """The > 0 constraint on silence_threshold_db must be maintained after VAD extension."""
        with pytest.raises(ValidationError):
            DetectSilenceOptions(silence_threshold_db=0.1)

    def test_zero_min_silence_duration_still_rejected(self) -> None:
        """The > 0 constraint on min_silence_duration must be maintained after VAD extension."""
        with pytest.raises(ValidationError):
            DetectSilenceOptions(min_silence_duration=0.0)

    def test_negative_padding_still_rejected(self) -> None:
        """The >= 0 constraint on padding must be maintained after VAD extension."""
        with pytest.raises(ValidationError):
            DetectSilenceOptions(padding=-0.1)

    def test_all_fields_together_with_vad_fields(self) -> None:
        """Model must be constructable with all existing and VAD extension fields explicitly set."""
        opts = DetectSilenceOptions(
            silence_threshold_db=-25.0,
            min_silence_duration=0.3,
            padding=0.05,
            min_keep_duration=1.0,
            backend="vad",
            vad_threshold=0.7,
            vad_min_speech_duration=0.3,
            vad_min_silence_duration=0.15,
        )
        assert opts.silence_threshold_db == pytest.approx(-25.0)
        assert opts.min_silence_duration == pytest.approx(0.3)
        assert opts.padding == pytest.approx(0.05)
        assert opts.min_keep_duration == pytest.approx(1.0)
        assert opts.backend == "vad"
        assert opts.vad_threshold == pytest.approx(0.7)
        assert opts.vad_min_speech_duration == pytest.approx(0.3)
        assert opts.vad_min_silence_duration == pytest.approx(0.15)


class TestFieldDescriptions:
    """Field descriptions must state intended use for misuse prevention (DC-AM-002, §7.6)."""

    def test_min_silence_duration_description_mentions_silencedetect(self) -> None:
        """description of min_silence_duration must contain 'silencedetect'."""
        field_info = DetectSilenceOptions.model_fields["min_silence_duration"]
        description = field_info.description or ""
        assert "silencedetect" in description, (
            "description of min_silence_duration does not contain 'silencedetect'. "
            "§7.6 DC-AM-002: must state that this is a silencedetect-only field."
        )

    def test_silence_threshold_db_description_mentions_silencedetect(self) -> None:
        """description of silence_threshold_db must contain 'silencedetect'."""
        field_info = DetectSilenceOptions.model_fields["silence_threshold_db"]
        description = field_info.description or ""
        assert "silencedetect" in description, (
            "description of silence_threshold_db does not contain 'silencedetect'. "
            "§7.6 DC-AM-002: must state that this is a silencedetect-only field."
        )

    def test_vad_threshold_description_mentions_vad(self) -> None:
        """description of vad_threshold must contain 'VAD'."""
        field_info = DetectSilenceOptions.model_fields["vad_threshold"]
        description = field_info.description or ""
        assert "VAD" in description, (
            "description of vad_threshold does not contain 'VAD'. "
            "§7.6 DC-AM-002: must state that this is a VAD-only field."
        )

    def test_vad_min_speech_duration_description_mentions_vad(self) -> None:
        """description of vad_min_speech_duration must contain 'VAD'."""
        field_info = DetectSilenceOptions.model_fields["vad_min_speech_duration"]
        description = field_info.description or ""
        assert "VAD" in description, (
            "description of vad_min_speech_duration does not contain 'VAD'. "
            "§7.6 DC-AM-002: must state that this is a VAD-only field."
        )

    def test_vad_min_silence_duration_description_mentions_vad(self) -> None:
        """description of vad_min_silence_duration must contain 'VAD'."""
        field_info = DetectSilenceOptions.model_fields["vad_min_silence_duration"]
        description = field_info.description or ""
        assert "VAD" in description, (
            "description of vad_min_silence_duration does not contain 'VAD'. "
            "§7.6 DC-AM-002: must state that this is a VAD-only field."
        )
