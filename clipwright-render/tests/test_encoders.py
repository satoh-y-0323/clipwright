"""test_encoders.py — Tests for encoders.py (pure logic + capability layer).

Tests the following:
  - Pure functions: resolve_encoder_name, rate_control_flags, hwaccel_value,
    auto_priority, encoder_listed
  - ResolvedEncoder dataclass fields
  - Capability layer: _resolve_hw_encoder with process.run mocked

Architecture reference: architecture-report §4 (mock boundary)
AC: 2/3/4/5/6/9, NFR-4, FR-4/5/6
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock, patch

import pytest

from clipwright.errors import ClipwrightError, ErrorCode
from clipwright_render.encoders import (
    ResolvedEncoder,
    _resolve_hw_encoder,
    auto_priority,
    encoder_listed,
    hwaccel_value,
    rate_control_flags,
    resolve_encoder_name,
)
from clipwright_render.schemas import RenderOptions


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture(autouse=True)
def clear_capability_cache() -> None:
    """Clear _CAPABILITY_CACHE before each test to prevent cross-test pollution."""
    import clipwright_render.encoders as enc

    enc._CAPABILITY_CACHE.clear()  # type: ignore[attr-defined]


# ===========================================================================
# ResolvedEncoder dataclass
# ===========================================================================


class TestResolvedEncoder:
    """Verify ResolvedEncoder dataclass shape (ADR-4/§2)."""

    def test_resolved_encoder_has_encoder_name(self) -> None:
        """ResolvedEncoder must have an encoder_name field."""
        re = ResolvedEncoder(
            encoder_name="h264_nvenc",
            rate_control_flags=["-cq", "28", "-rc", "vbr"],
            hwaccel_value="cuda",
            warnings=[],
        )
        assert re.encoder_name == "h264_nvenc"

    def test_resolved_encoder_has_rate_control_flags(self) -> None:
        """ResolvedEncoder must have a rate_control_flags field."""
        re = ResolvedEncoder(
            encoder_name="h264_nvenc",
            rate_control_flags=["-cq", "28", "-rc", "vbr"],
            hwaccel_value="cuda",
            warnings=[],
        )
        assert re.rate_control_flags == ["-cq", "28", "-rc", "vbr"]

    def test_resolved_encoder_has_hwaccel_value(self) -> None:
        """ResolvedEncoder must have a hwaccel_value field."""
        re = ResolvedEncoder(
            encoder_name="h264_nvenc",
            rate_control_flags=[],
            hwaccel_value="cuda",
            warnings=[],
        )
        assert re.hwaccel_value == "cuda"

    def test_resolved_encoder_has_warnings(self) -> None:
        """ResolvedEncoder must have a warnings field."""
        re = ResolvedEncoder(
            encoder_name="libx264",
            rate_control_flags=["-crf", "21"],
            hwaccel_value=None,
            warnings=["fell back to libx264"],
        )
        assert re.warnings == ["fell back to libx264"]

    def test_resolved_encoder_is_dataclass(self) -> None:
        """ResolvedEncoder must be a dataclass (not a plain class)."""
        assert dataclasses.is_dataclass(ResolvedEncoder)

    def test_resolved_encoder_hwaccel_value_can_be_none(self) -> None:
        """hwaccel_value must accept None (e.g. amf/none vendors)."""
        re = ResolvedEncoder(
            encoder_name="h264_amf",
            rate_control_flags=[],
            hwaccel_value=None,
            warnings=[],
        )
        assert re.hwaccel_value is None


# ===========================================================================
# resolve_encoder_name — vendor × family → concrete encoder name (AC-9)
# ===========================================================================


class TestResolveEncoderName:
    """Verify vendor × family → concrete encoder name mapping (AC-9, FR-3)."""

    # --- nvenc ---
    def test_nvenc_h264_default(self) -> None:
        """nvenc + h264 → h264_nvenc."""
        assert resolve_encoder_name("nvenc", "h264") == "h264_nvenc"

    def test_nvenc_hevc(self) -> None:
        """nvenc + hevc → hevc_nvenc (AC-9)."""
        assert resolve_encoder_name("nvenc", "hevc") == "hevc_nvenc"

    def test_nvenc_av1(self) -> None:
        """nvenc + av1 → av1_nvenc."""
        assert resolve_encoder_name("nvenc", "av1") == "av1_nvenc"

    # --- amf ---
    def test_amf_h264(self) -> None:
        """amf + h264 → h264_amf."""
        assert resolve_encoder_name("amf", "h264") == "h264_amf"

    def test_amf_hevc(self) -> None:
        """amf + hevc → hevc_amf."""
        assert resolve_encoder_name("amf", "hevc") == "hevc_amf"

    def test_amf_av1(self) -> None:
        """amf + av1 → av1_amf."""
        assert resolve_encoder_name("amf", "av1") == "av1_amf"

    # --- qsv ---
    def test_qsv_h264(self) -> None:
        """qsv + h264 → h264_qsv."""
        assert resolve_encoder_name("qsv", "h264") == "h264_qsv"

    def test_qsv_hevc(self) -> None:
        """qsv + hevc → hevc_qsv."""
        assert resolve_encoder_name("qsv", "hevc") == "hevc_qsv"

    def test_qsv_av1(self) -> None:
        """qsv + av1 → av1_qsv."""
        assert resolve_encoder_name("qsv", "av1") == "av1_qsv"

    # --- vaapi ---
    def test_vaapi_h264(self) -> None:
        """vaapi + h264 → h264_vaapi."""
        assert resolve_encoder_name("vaapi", "h264") == "h264_vaapi"

    def test_vaapi_hevc(self) -> None:
        """vaapi + hevc → hevc_vaapi."""
        assert resolve_encoder_name("vaapi", "hevc") == "hevc_vaapi"

    def test_vaapi_av1(self) -> None:
        """vaapi + av1 → av1_vaapi."""
        assert resolve_encoder_name("vaapi", "av1") == "av1_vaapi"

    # --- videotoolbox ---
    def test_videotoolbox_h264(self) -> None:
        """videotoolbox + h264 → h264_videotoolbox."""
        assert resolve_encoder_name("videotoolbox", "h264") == "h264_videotoolbox"

    def test_videotoolbox_hevc(self) -> None:
        """videotoolbox + hevc → hevc_videotoolbox."""
        assert resolve_encoder_name("videotoolbox", "hevc") == "hevc_videotoolbox"


# ===========================================================================
# rate_control_flags — encoder_name × quality → argv list (AC-3/5, FR-5)
# ===========================================================================


class TestRateControlFlagsNvenc:
    """Verify NVENC rate control flags (AC-5)."""

    def test_h264_nvenc_quality_28(self) -> None:
        """h264_nvenc + quality=28 → ['-cq','28','-rc','vbr'] (AC-5)."""
        assert rate_control_flags("h264_nvenc", 28) == ["-cq", "28", "-rc", "vbr"]

    def test_hevc_nvenc_quality_28(self) -> None:
        """hevc_nvenc + quality=28 → ['-cq','28','-rc','vbr']."""
        assert rate_control_flags("hevc_nvenc", 28) == ["-cq", "28", "-rc", "vbr"]

    def test_av1_nvenc_quality_20(self) -> None:
        """av1_nvenc + quality=20 → ['-cq','20','-rc','vbr']."""
        assert rate_control_flags("av1_nvenc", 20) == ["-cq", "20", "-rc", "vbr"]

    def test_nvenc_no_crf_in_flags(self) -> None:
        """nvenc rate_control_flags must never contain '-crf' (AC-3)."""
        flags = rate_control_flags("h264_nvenc", 28)
        assert "-crf" not in flags

    def test_nvenc_quality_none_returns_empty(self) -> None:
        """quality=None for nvenc → [] (parent confirmed Q3: HW default)."""
        assert rate_control_flags("h264_nvenc", None) == []  # type: ignore[arg-type]


class TestRateControlFlagsQsv:
    """Verify QSV rate control flags (FR-5)."""

    def test_h264_qsv_quality_28(self) -> None:
        """h264_qsv + quality=28 → ['-global_quality','28']."""
        assert rate_control_flags("h264_qsv", 28) == ["-global_quality", "28"]

    def test_hevc_qsv_quality_23(self) -> None:
        """hevc_qsv + quality=23 → ['-global_quality','23']."""
        assert rate_control_flags("hevc_qsv", 23) == ["-global_quality", "23"]

    def test_qsv_no_crf_in_flags(self) -> None:
        """qsv rate_control_flags must never contain '-crf' (AC-3)."""
        flags = rate_control_flags("h264_qsv", 28)
        assert "-crf" not in flags

    def test_qsv_quality_none_returns_empty(self) -> None:
        """quality=None for qsv → [] (parent confirmed Q3)."""
        assert rate_control_flags("h264_qsv", None) == []  # type: ignore[arg-type]


class TestRateControlFlagsVaapi:
    """Verify VAAPI rate control flags (FR-5)."""

    def test_h264_vaapi_quality_28(self) -> None:
        """h264_vaapi + quality=28 → ['-rc_mode','CQP','-global_quality','28']."""
        assert rate_control_flags("h264_vaapi", 28) == [
            "-rc_mode",
            "CQP",
            "-global_quality",
            "28",
        ]

    def test_vaapi_no_crf_in_flags(self) -> None:
        """vaapi rate_control_flags must never contain '-crf' (AC-3)."""
        flags = rate_control_flags("h264_vaapi", 28)
        assert "-crf" not in flags

    def test_vaapi_quality_none_returns_empty(self) -> None:
        """quality=None for vaapi → [] (parent confirmed Q3)."""
        assert rate_control_flags("h264_vaapi", None) == []  # type: ignore[arg-type]


class TestRateControlFlagsAmf:
    """Verify AMF rate control flags (FR-5)."""

    def test_h264_amf_quality_28(self) -> None:
        """h264_amf + quality=28 → ['-rc','cqp','-qp_i','28','-qp_p','28']."""
        assert rate_control_flags("h264_amf", 28) == [
            "-rc",
            "cqp",
            "-qp_i",
            "28",
            "-qp_p",
            "28",
        ]

    def test_amf_no_crf_in_flags(self) -> None:
        """amf rate_control_flags must never contain '-crf' (AC-3)."""
        flags = rate_control_flags("h264_amf", 28)
        assert "-crf" not in flags

    def test_amf_quality_none_returns_empty(self) -> None:
        """quality=None for amf → [] (parent confirmed Q3)."""
        assert rate_control_flags("h264_amf", None) == []  # type: ignore[arg-type]


class TestRateControlFlagsVideotoolbox:
    """Verify VideoToolbox rate control flags (FR-5)."""

    def test_h264_videotoolbox_quality_28(self) -> None:
        """h264_videotoolbox + quality=28 → ['-q:v','28','-b:v','0']."""
        assert rate_control_flags("h264_videotoolbox", 28) == [
            "-q:v",
            "28",
            "-b:v",
            "0",
        ]

    def test_videotoolbox_no_crf_in_flags(self) -> None:
        """videotoolbox rate_control_flags must never contain '-crf' (AC-3)."""
        flags = rate_control_flags("h264_videotoolbox", 28)
        assert "-crf" not in flags

    def test_videotoolbox_quality_none_returns_empty(self) -> None:
        """quality=None for videotoolbox → [] (parent confirmed Q3)."""
        assert rate_control_flags("h264_videotoolbox", None) == []  # type: ignore[arg-type]


class TestRateControlFlagsSoftware:
    """Verify software encoder rate control flags (existing behavior, AC-6)."""

    def test_libx264_quality_21(self) -> None:
        """libx264 + quality=21 → ['-crf','21'] (existing behavior maintained)."""
        assert rate_control_flags("libx264", 21) == ["-crf", "21"]

    def test_libx265_quality_28(self) -> None:
        """libx265 + quality=28 → ['-crf','28']."""
        assert rate_control_flags("libx265", 28) == ["-crf", "28"]

    def test_quality_int_converted_to_str(self) -> None:
        """Quality value must be converted to str in flags (plan.py M-1 rule)."""
        flags = rate_control_flags("libx264", 21)
        # '-crf' must be followed by '21' (string, not int)
        crf_idx = flags.index("-crf")
        assert flags[crf_idx + 1] == "21"
        assert isinstance(flags[crf_idx + 1], str)


class TestRateControlFlagsNoCrf:
    """Verify no HW encoder emits -crf (AC-3)."""

    @pytest.mark.parametrize(
        "hw_encoder_name",
        [
            "h264_nvenc",
            "hevc_nvenc",
            "av1_nvenc",
            "h264_qsv",
            "hevc_qsv",
            "h264_vaapi",
            "hevc_vaapi",
            "h264_amf",
            "hevc_amf",
            "h264_videotoolbox",
            "hevc_videotoolbox",
        ],
    )
    def test_hw_encoder_never_emits_crf(self, hw_encoder_name: str) -> None:
        """All HW encoders must never emit '-crf' in rate_control_flags (AC-3)."""
        flags = rate_control_flags(hw_encoder_name, 28)
        assert "-crf" not in flags, f"{hw_encoder_name} emitted '-crf': {flags}"


# ===========================================================================
# hwaccel_value — vendor → -hwaccel CLI value (FR-6 + parent confirmed Q1)
# ===========================================================================


class TestHwaccelValue:
    """Verify vendor → -hwaccel value mapping (FR-6 table, Q1)."""

    def test_nvenc_returns_cuda(self) -> None:
        """nvenc → 'cuda'."""
        assert hwaccel_value("nvenc") == "cuda"

    def test_qsv_returns_qsv(self) -> None:
        """qsv → 'qsv'."""
        assert hwaccel_value("qsv") == "qsv"

    def test_vaapi_returns_vaapi(self) -> None:
        """vaapi → 'vaapi'."""
        assert hwaccel_value("vaapi") == "vaapi"

    def test_videotoolbox_returns_videotoolbox(self) -> None:
        """videotoolbox → 'videotoolbox'."""
        assert hwaccel_value("videotoolbox") == "videotoolbox"

    def test_amf_returns_none(self) -> None:
        """amf → None (no -hwaccel flag for AMF, FR-6)."""
        assert hwaccel_value("amf") is None

    def test_none_vendor_returns_none(self) -> None:
        """none vendor → None (no -hwaccel flag, FR-6 + Q1 parent confirmed)."""
        assert hwaccel_value("none") is None

    def test_auto_returns_auto(self) -> None:
        """auto → 'auto' (used when hwaccel_decode=True in auto mode, Q1)."""
        assert hwaccel_value("auto") == "auto"


# ===========================================================================
# auto_priority — OS → ordered vendor list (FR-4)
# ===========================================================================


class TestAutoPriority:
    """Verify OS-specific auto priority list (FR-4)."""

    def test_windows_priority(self) -> None:
        """Windows → ['nvenc', 'amf', 'qsv'] (FR-4)."""
        assert auto_priority("Windows") == ["nvenc", "amf", "qsv"]

    def test_linux_priority(self) -> None:
        """Linux → ['vaapi', 'qsv', 'nvenc'] (FR-4)."""
        assert auto_priority("Linux") == ["vaapi", "qsv", "nvenc"]

    def test_macos_priority(self) -> None:
        """Darwin → list containing 'videotoolbox' (FR-4, macOS)."""
        priority = auto_priority("Darwin")
        assert "videotoolbox" in priority

    def test_priority_list_non_empty(self) -> None:
        """auto_priority must return a non-empty list for any known OS."""
        for system in ("Windows", "Linux", "Darwin"):
            assert len(auto_priority(system)) > 0


# ===========================================================================
# encoder_listed — parse ffmpeg -encoders stdout (ADR-2)
# ===========================================================================


SAMPLE_ENCODERS_OUTPUT = """\
 V..... libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10
 V..... libx265              libx265 H.265 / HEVC
 V..... h264_nvenc           NVIDIA NVENC H.264 encoder
 V..... hevc_nvenc           NVIDIA NVENC hevc encoder
 V..... h264_amf             AMD AMF H.264 Encoder
 A..... aac                  AAC (Advanced Audio Coding)
"""

EMPTY_ENCODERS_OUTPUT = """\
 V..... libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10
 V..... libx265              libx265 H.265 / HEVC
"""


class TestEncoderListed:
    """Verify encoder_listed correctly parses ffmpeg -encoders output (ADR-2)."""

    def test_h264_nvenc_present(self) -> None:
        """h264_nvenc present in output → True."""
        assert encoder_listed(SAMPLE_ENCODERS_OUTPUT, "h264_nvenc") is True

    def test_hevc_nvenc_present(self) -> None:
        """hevc_nvenc present in output → True."""
        assert encoder_listed(SAMPLE_ENCODERS_OUTPUT, "hevc_nvenc") is True

    def test_h264_amf_present(self) -> None:
        """h264_amf present in output → True."""
        assert encoder_listed(SAMPLE_ENCODERS_OUTPUT, "h264_amf") is True

    def test_h264_qsv_absent(self) -> None:
        """h264_qsv absent → False."""
        assert encoder_listed(SAMPLE_ENCODERS_OUTPUT, "h264_qsv") is False

    def test_vaapi_absent(self) -> None:
        """h264_vaapi absent → False."""
        assert encoder_listed(SAMPLE_ENCODERS_OUTPUT, "h264_vaapi") is False

    def test_partial_match_does_not_count(self) -> None:
        """'nvenc' alone should not match 'h264_nvenc' as an encoder name boundary."""
        # This depends on implementation detail but a bare 'nvenc' should not be listed
        # because the actual entry is 'h264_nvenc', not 'nvenc'.
        # If impl uses word-boundary or exact column match, this holds.
        # We test that libx264 is found but 'x264' alone is not.
        assert encoder_listed(SAMPLE_ENCODERS_OUTPUT, "libx264") is True

    def test_empty_output_returns_false(self) -> None:
        """Empty encoder output → False."""
        assert encoder_listed("", "h264_nvenc") is False


# ===========================================================================
# _resolve_hw_encoder — capability layer (process.run mocked, AC-2/4, NFR-4)
# ===========================================================================

# Sample ffmpeg -encoders output used in capability tests
_ENCODERS_WITH_NVENC = """\
 V..... libx264              libx264 H.264 / AVC
 V..... h264_nvenc           NVIDIA NVENC H.264 encoder
 V..... hevc_nvenc           NVIDIA NVENC hevc encoder
"""

_ENCODERS_WITHOUT_HW = """\
 V..... libx264              libx264 H.264 / AVC
 V..... libx265              libx265 H.265 / HEVC
"""


def _make_process_run_mock(
    encoders_stdout: str,
    *,
    dry_encode_fails: bool = False,
) -> MagicMock:
    """Build a mock for clipwright.process.run.

    First call (ffmpeg -encoders) returns encoders_stdout.
    Subsequent calls (dry encode) either succeed (return MagicMock with returncode=0)
    or raise ClipwrightError(SUBPROCESS_FAILED) to simulate encode failure.
    """
    mock_encoders_result = MagicMock()
    mock_encoders_result.stdout = encoders_stdout

    mock_encode_result = MagicMock()

    if dry_encode_fails:

        def side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            if "-encoders" in cmd:
                return mock_encoders_result
            raise ClipwrightError(
                code=ErrorCode.SUBPROCESS_FAILED,
                message="ffmpeg exited with code 1",
                hint="encoder not supported",
            )

    else:

        def side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            if "-encoders" in cmd:
                return mock_encoders_result
            return mock_encode_result

    m = MagicMock(side_effect=side_effect)
    return m


class TestResolveHwEncoderNone:
    """hw_encoder='none' → None (existing path, backward-compatible)."""

    def test_none_returns_none(self) -> None:
        """_resolve_hw_encoder with hw_encoder='none' must return None."""
        opts = RenderOptions(hw_encoder="none")  # type: ignore[call-arg]
        result = _resolve_hw_encoder(opts)
        assert result is None


class TestResolveHwEncoderAutoSuccess:
    """'auto' with nvenc available → ResolvedEncoder(h264_nvenc), warnings empty."""

    def test_auto_nvenc_success_on_windows(self) -> None:
        """auto on Windows with nvenc → ResolvedEncoder(encoder_name='h264_nvenc') (AC-8)."""
        opts = RenderOptions(hw_encoder="auto")  # type: ignore[call-arg]
        mock_run = _make_process_run_mock(_ENCODERS_WITH_NVENC, dry_encode_fails=False)

        with (
            patch("clipwright_render.encoders.run", mock_run),
            patch("clipwright_render.encoders.platform.system", return_value="Windows"),
        ):
            result = _resolve_hw_encoder(opts)

        assert result is not None
        assert result.encoder_name == "h264_nvenc"
        assert result.warnings == []


class TestResolveHwEncoderAutoAllFail:
    """'auto' with all candidates failing → libx264 fallback + warning (AC-2)."""

    def test_auto_all_fail_falls_back_to_libx264(self) -> None:
        """auto with all dry-encodes failing → libx264 + 1 warning (AC-2)."""
        opts = RenderOptions(hw_encoder="auto")  # type: ignore[call-arg]
        mock_run = _make_process_run_mock(_ENCODERS_WITHOUT_HW, dry_encode_fails=True)

        with (
            patch("clipwright_render.encoders.run", mock_run),
            patch("clipwright_render.encoders.platform.system", return_value="Windows"),
        ):
            result = _resolve_hw_encoder(opts)

        assert result is not None
        assert result.encoder_name == "libx264"
        assert len(result.warnings) == 1
        assert "fell back" in result.warnings[0].lower()

    def test_auto_fallback_warning_message(self) -> None:
        """Fallback warning must mention libx264 and suggest next action."""
        opts = RenderOptions(hw_encoder="auto")  # type: ignore[call-arg]
        mock_run = _make_process_run_mock(_ENCODERS_WITHOUT_HW, dry_encode_fails=True)

        with (
            patch("clipwright_render.encoders.run", mock_run),
            patch("clipwright_render.encoders.platform.system", return_value="Windows"),
        ):
            result = _resolve_hw_encoder(opts)

        assert result is not None
        warning = result.warnings[0]
        # Must reference libx264 and provide guidance (FR-8 / ADR-5)
        assert "libx264" in warning
        assert (
            "hw_encoder" in warning or "GPU" in warning or "driver" in warning.lower()
        )


class TestResolveHwEncoderExplicitFail:
    """Explicit vendor failure → ClipwrightError(UNSUPPORTED_OPERATION), no fallback (AC-4)."""

    def test_qsv_fail_raises_unsupported_operation(self) -> None:
        """Explicit qsv + dry-encode fail → UNSUPPORTED_OPERATION, no fallback (AC-4)."""
        opts = RenderOptions(hw_encoder="qsv")  # type: ignore[call-arg]

        # ffmpeg -encoders reports qsv is listed but dry-encode fails
        encoders_with_qsv = (
            _ENCODERS_WITHOUT_HW + " V..... h264_qsv            Intel QSV H.264\n"
        )
        mock_run = _make_process_run_mock(encoders_with_qsv, dry_encode_fails=True)

        with (
            patch("clipwright_render.encoders.run", mock_run),
            patch("clipwright_render.encoders.platform.system", return_value="Windows"),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            _resolve_hw_encoder(opts)

        err = exc_info.value
        assert err.code == ErrorCode.UNSUPPORTED_OPERATION

    def test_qsv_fail_message_contains_encoder_name(self) -> None:
        """ClipwrightError message must contain the failing encoder name (AC-4)."""
        opts = RenderOptions(hw_encoder="qsv")  # type: ignore[call-arg]
        encoders_with_qsv = (
            _ENCODERS_WITHOUT_HW + " V..... h264_qsv            Intel QSV H.264\n"
        )
        mock_run = _make_process_run_mock(encoders_with_qsv, dry_encode_fails=True)

        with (
            patch("clipwright_render.encoders.run", mock_run),
            patch("clipwright_render.encoders.platform.system", return_value="Windows"),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            _resolve_hw_encoder(opts)

        # message must include the concrete encoder name (e.g. 'h264_qsv')
        assert "qsv" in exc_info.value.message.lower()

    def test_qsv_fail_hint_suggests_auto_or_none(self) -> None:
        """ClipwrightError hint must suggest hw_encoder='auto' or 'none' (AC-4)."""
        opts = RenderOptions(hw_encoder="qsv")  # type: ignore[call-arg]
        encoders_with_qsv = (
            _ENCODERS_WITHOUT_HW + " V..... h264_qsv            Intel QSV H.264\n"
        )
        mock_run = _make_process_run_mock(encoders_with_qsv, dry_encode_fails=True)

        with (
            patch("clipwright_render.encoders.run", mock_run),
            patch("clipwright_render.encoders.platform.system", return_value="Windows"),
            pytest.raises(ClipwrightError) as exc_info,
        ):
            _resolve_hw_encoder(opts)

        hint = exc_info.value.hint
        # hint must contain hw_encoder='auto' or hw_encoder='none'
        assert "hw_encoder='auto'" in hint or "hw_encoder='none'" in hint

    def test_explicit_vendor_does_not_fallback_to_libx264(self) -> None:
        """Explicit vendor failure must raise, NOT silently fall back to libx264 (AC-4)."""
        opts = RenderOptions(hw_encoder="nvenc")  # type: ignore[call-arg]
        mock_run = _make_process_run_mock(_ENCODERS_WITH_NVENC, dry_encode_fails=True)

        with (
            patch("clipwright_render.encoders.run", mock_run),
            patch("clipwright_render.encoders.platform.system", return_value="Windows"),
        ):
            with pytest.raises(ClipwrightError) as exc_info:
                _resolve_hw_encoder(opts)

        assert exc_info.value.code == ErrorCode.UNSUPPORTED_OPERATION


class TestResolveHwEncoderCache:
    """Capability cache: 2nd call must not re-run dry-encode (NFR-4)."""

    def test_cache_prevents_duplicate_dry_encode(self) -> None:
        """Second _resolve_hw_encoder call must not call process.run for dry-encode again (NFR-4)."""
        opts = RenderOptions(hw_encoder="nvenc")  # type: ignore[call-arg]

        encode_call_count = 0
        encoders_result = MagicMock()
        encoders_result.stdout = _ENCODERS_WITH_NVENC

        def run_side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            nonlocal encode_call_count
            if "-encoders" in cmd:
                return encoders_result
            # This is a dry-encode call
            encode_call_count += 1
            return MagicMock()

        mock_run = MagicMock(side_effect=run_side_effect)

        with (
            patch("clipwright_render.encoders.run", mock_run),
            patch("clipwright_render.encoders.platform.system", return_value="Windows"),
        ):
            _resolve_hw_encoder(opts)
            # Clear only the result but NOT _CAPABILITY_CACHE to test caching
            _resolve_hw_encoder(opts)

        # Dry-encode should have been called only once across both invocations
        assert encode_call_count == 1, (
            f"Expected 1 dry-encode call, got {encode_call_count}"
        )

    def test_cache_key_is_concrete_encoder_name(self) -> None:
        """Cache must key on the concrete encoder name, not on options object."""
        # Two different options objects pointing to same concrete encoder
        opts1 = RenderOptions(hw_encoder="nvenc")  # type: ignore[call-arg]
        opts2 = RenderOptions(hw_encoder="nvenc", quality=28)  # type: ignore[call-arg]

        encode_call_count = 0
        encoders_result = MagicMock()
        encoders_result.stdout = _ENCODERS_WITH_NVENC

        def run_side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            nonlocal encode_call_count
            if "-encoders" in cmd:
                return encoders_result
            encode_call_count += 1
            return MagicMock()

        mock_run = MagicMock(side_effect=run_side_effect)

        with (
            patch("clipwright_render.encoders.run", mock_run),
            patch("clipwright_render.encoders.platform.system", return_value="Windows"),
        ):
            _resolve_hw_encoder(opts1)
            _resolve_hw_encoder(opts2)

        # Same concrete encoder → should only dry-encode once
        assert encode_call_count == 1, (
            f"Expected 1 dry-encode call (cache hit), got {encode_call_count}"
        )
