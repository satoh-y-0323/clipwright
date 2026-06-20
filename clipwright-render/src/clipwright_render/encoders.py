"""encoders.py — Hardware encoder resolution for clipwright-render.

Provides two layers:
  1. Pure logic (no I/O): vendor/family → encoder name, rate-control flags,
     hwaccel value, auto-priority, encoder listing.
  2. Capability layer: _resolve_hw_encoder() probes ffmpeg via subprocess.

Architecture: ADR-1/2/3/4/5 from architecture-report-20260620-203900.md.
"""

from __future__ import annotations

import dataclasses
import platform
from typing import TYPE_CHECKING

from clipwright.errors import ClipwrightError, ErrorCode
from clipwright.process import resolve_tool, run

if TYPE_CHECKING:
    from clipwright_render.schemas import RenderOptions

# ---------------------------------------------------------------------------
# Module-level caches (NFR-4)
# ---------------------------------------------------------------------------

# Cache for ffmpeg -encoders stdout (populated once per process)
_ENCODERS_OUTPUT_CACHE: str | None = None

# Cache for dry-encode capability checks: concrete encoder name → bool
_CAPABILITY_CACHE: dict[str, bool] = {}

# ---------------------------------------------------------------------------
# Encoder name map: (vendor, family) → concrete ffmpeg encoder name (FR-3)
# ---------------------------------------------------------------------------

# Explicit table — avoids fragile suffix matching (ADR-3)
_ENCODER_NAME_MAP: dict[tuple[str, str], str] = {
    # nvenc
    ("nvenc", "h264"): "h264_nvenc",
    ("nvenc", "hevc"): "hevc_nvenc",
    ("nvenc", "av1"): "av1_nvenc",
    # amf
    ("amf", "h264"): "h264_amf",
    ("amf", "hevc"): "hevc_amf",
    ("amf", "av1"): "av1_amf",
    # qsv
    ("qsv", "h264"): "h264_qsv",
    ("qsv", "hevc"): "hevc_qsv",
    ("qsv", "av1"): "av1_qsv",
    # vaapi
    ("vaapi", "h264"): "h264_vaapi",
    ("vaapi", "hevc"): "hevc_vaapi",
    ("vaapi", "av1"): "av1_vaapi",
    # videotoolbox
    ("videotoolbox", "h264"): "h264_videotoolbox",
    ("videotoolbox", "hevc"): "hevc_videotoolbox",
    ("videotoolbox", "av1"): "av1_videotoolbox",
    # software — auto fallback only; not a user-selectable vendor (libx264 path)
    ("software", "h264"): "libx264",
    ("software", "hevc"): "libx265",
}

# Rate-control flag map: vendor → argv tokens template (values use placeholder "Q")
# Lifted to module level to avoid per-call reconstruction (CR-M-2).
# Actual quality value is substituted at call time in rate_control_flags().
_RC_FLAG_TEMPLATES: dict[str, list[str]] = {
    "software": ["-crf", "Q"],
    "nvenc": ["-cq", "Q", "-rc", "vbr"],
    "qsv": ["-global_quality", "Q"],
    "vaapi": ["-rc_mode", "CQP", "-global_quality", "Q"],
    "amf": ["-rc", "cqp", "-qp_i", "Q", "-qp_p", "Q"],
    "videotoolbox": ["-q:v", "Q", "-b:v", "0"],
}

# Reverse map: concrete encoder name → vendor (ADR-3: explicit dict, no suffix matching)
_ENCODER_TO_VENDOR: dict[str, str] = {
    # nvenc
    "h264_nvenc": "nvenc",
    "hevc_nvenc": "nvenc",
    "av1_nvenc": "nvenc",
    # amf
    "h264_amf": "amf",
    "hevc_amf": "amf",
    "av1_amf": "amf",
    # qsv
    "h264_qsv": "qsv",
    "hevc_qsv": "qsv",
    "av1_qsv": "qsv",
    # vaapi
    "h264_vaapi": "vaapi",
    "hevc_vaapi": "vaapi",
    "av1_vaapi": "vaapi",
    # videotoolbox
    "h264_videotoolbox": "videotoolbox",
    "hevc_videotoolbox": "videotoolbox",
    "av1_videotoolbox": "videotoolbox",
    # software
    "libx264": "software",
    "libx265": "software",
}

# ---------------------------------------------------------------------------
# ResolvedEncoder dataclass (ADR-4/5)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ResolvedEncoder:
    """Result of hardware encoder resolution.

    Carries the concrete encoder name, rate-control flags, hwaccel value
    (for -hwaccel decode option), and any warnings accumulated during resolution.

    Attributes:
        encoder_name: Concrete ffmpeg encoder name (e.g. 'h264_nvenc').
        rate_control_flags: Rate-control argv tokens for this encoder
            (e.g. ['-cq', '28', '-rc', 'vbr']). Empty list when quality is
            None or not applicable (parent confirmed Q3).
        hwaccel_value: Value for the -hwaccel flag (e.g. 'cuda'), or None
            when no -hwaccel flag should be emitted (amf, none vendors).
        warnings: Human-readable warnings accumulated during resolution
            (e.g. auto fallback message per FR-8/ADR-5).
    """

    encoder_name: str
    rate_control_flags: list[str]
    hwaccel_value: str | None
    warnings: list[str]


# ---------------------------------------------------------------------------
# Pure logic layer — no I/O (ADR-3)
# ---------------------------------------------------------------------------


def resolve_encoder_name(vendor: str, family: str) -> str:
    """Resolve the concrete ffmpeg encoder name for a vendor/codec family pair.

    Args:
        vendor: Hardware vendor string ('nvenc', 'amf', 'qsv', 'vaapi',
            'videotoolbox').
        family: Codec family string: 'h264' (default), 'hevc', or 'av1'.

    Returns:
        Concrete ffmpeg encoder name (e.g. 'h264_nvenc').

    Raises:
        ValueError: When the (vendor, family) combination is not in the map.
    """
    key = (vendor, family)
    encoder = _ENCODER_NAME_MAP.get(key)
    if encoder is None:
        raise ValueError(
            f"Unknown vendor/family combination: vendor={vendor!r}, family={family!r}"
        )
    return encoder


def rate_control_flags(encoder_name: str, quality: int | None) -> list[str]:
    """Return the rate-control argv flags for the given encoder and quality.

    Uses an explicit encoder-name → vendor reverse dict for classification
    (ADR-3: no suffix matching).

    When quality is None and the encoder is a hardware encoder, returns an
    empty list (parent confirmed Q3: delegate to ffmpeg encoder default).
    When quality is None for software encoders (libx264/libx265), also
    returns an empty list.

    Args:
        encoder_name: Concrete ffmpeg encoder name (e.g. 'h264_nvenc').
        quality: Integer quality value (0–51), or None.

    Returns:
        List of argv tokens for rate control (may be empty).
    """
    vendor = _ENCODER_TO_VENDOR.get(encoder_name)

    # Q3: quality=None → return empty list (delegate to encoder default)
    if quality is None:
        return []

    q = str(quality)

    # Substitute quality value into the per-vendor template (ADR-3: explicit map,
    # no suffix matching). Template uses "Q" as a placeholder.
    template = _RC_FLAG_TEMPLATES.get(vendor or "")
    if template is None:
        return []
    return [q if tok == "Q" else tok for tok in template]


def hwaccel_value(vendor: str) -> str | None:
    """Return the -hwaccel flag value for the given vendor.

    Returns None when no -hwaccel flag should be emitted (amf, none).
    Returns 'auto' for the 'auto' pseudo-vendor (used when hwaccel_decode=True
    in auto resolution mode per parent confirmed Q1).

    Args:
        vendor: Vendor string ('nvenc', 'qsv', 'vaapi', 'videotoolbox',
            'amf', 'none', 'auto').

    Returns:
        The -hwaccel value string, or None.
    """
    _MAP: dict[str, str | None] = {
        "nvenc": "cuda",
        "qsv": "qsv",
        "vaapi": "vaapi",
        "videotoolbox": "videotoolbox",
        "auto": "auto",
        "amf": None,
        "none": None,
        "software": None,
    }
    return _MAP.get(vendor)


def auto_priority(system: str) -> list[str]:
    """Return the ordered list of vendor strings for 'auto' mode by OS.

    Does NOT call platform.system() internally; the caller passes the system
    string so this function stays pure and testable on any OS (ADR-3).

    Args:
        system: OS name as returned by platform.system()
            (e.g. 'Windows', 'Linux', 'Darwin').

    Returns:
        Ordered list of vendor strings to probe (e.g. ['nvenc', 'amf', 'qsv']).
    """
    _PRIORITY: dict[str, list[str]] = {
        "Windows": ["nvenc", "amf", "qsv"],
        "Linux": ["vaapi", "qsv", "nvenc"],
        "Darwin": ["videotoolbox"],
    }
    # Fallback for unknown OS: try common vendors
    return _PRIORITY.get(system, ["nvenc", "amf", "qsv", "vaapi"])


def encoder_listed(encoders_output: str, name: str) -> bool:
    """Return True if the encoder name appears in ffmpeg -encoders stdout.

    Parses each line of the output and checks whether the encoder name appears
    as a whitespace-delimited token (not just as a substring), preventing
    false positives from partial matches.

    Args:
        encoders_output: stdout from 'ffmpeg -encoders'.
        name: Concrete encoder name to look for (e.g. 'h264_nvenc').

    Returns:
        True when the encoder is listed, False otherwise.
    """
    for line in encoders_output.splitlines():
        # Each encoder line has the form: " V..... encoder_name    description"
        # Split by whitespace; token at index 1 (after the flags column) is the name.
        parts = line.split()
        if len(parts) >= 2 and parts[1] == name:
            return True
    return False


# ---------------------------------------------------------------------------
# Capability layer — ffmpeg I/O + caching (ADR-2)
# ---------------------------------------------------------------------------


def _get_encoders_output(ffmpeg: str) -> str:
    """Fetch and cache ffmpeg -encoders output (called once per process).

    Args:
        ffmpeg: Path to the ffmpeg executable.

    Returns:
        stdout string from 'ffmpeg -encoders'.
    """
    global _ENCODERS_OUTPUT_CACHE
    if _ENCODERS_OUTPUT_CACHE is None:
        result = run([ffmpeg, "-encoders"], timeout=30.0)
        _ENCODERS_OUTPUT_CACHE = result.stdout
    return _ENCODERS_OUTPUT_CACHE


def _probe_encoder(ffmpeg: str, encoder_name: str) -> bool:
    """Run a dry-encode to verify the encoder is functional on this system.

    Uses a trivial lavfi testsrc so no real media file is needed.
    Output is '-f null -' (no file created, cwd-independent, non-destructive).

    NOTE: testsrc size=256x256:rate=25 is the 'safe-side' value per parent
    confirmed Q2. Final size/rate should be confirmed against NVENC e2e results
    before release (may need adjustment for encoders with resolution constraints).

    Args:
        ffmpeg: Path to the ffmpeg executable.
        encoder_name: Concrete encoder name to test (e.g. 'h264_nvenc').

    Returns:
        True when the dry-encode succeeds, False when ClipwrightError is raised.
    """
    if encoder_name in _CAPABILITY_CACHE:
        return _CAPABILITY_CACHE[encoder_name]

    cmd = [
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=1:size=256x256:rate=25",
        "-c:v",
        encoder_name,
        "-f",
        "null",
        "-",
    ]
    try:
        run(cmd, timeout=30.0)
        result = True
    except ClipwrightError:
        # ClipwrightError(SUBPROCESS_FAILED) = encoder not usable on this system
        # (ADR-2: capability-check is the only context where we swallow this error)
        result = False

    _CAPABILITY_CACHE[encoder_name] = result
    return result


def _resolve_hw_encoder(options: RenderOptions) -> ResolvedEncoder | None:
    """Resolve the hardware encoder to use based on RenderOptions.

    Returns None when hw_encoder is 'none' (backward-compatible path).
    Otherwise probes ffmpeg to find and verify a working encoder.

    Resolution strategy (ADR-2/4):
      - 'none'   → None (existing software path unchanged, FR-2/AC-1)
      - 'auto'   → walk auto_priority(platform.system()), pick first usable
                   encoder; if all fail, fall back to libx264 + warning (FR-8)
      - explicit → resolve concrete name, probe, raise on failure (FR-9/AC-4)

    The effective quality value is resolved from options.quality → options.crf
    → None (parent confirmed Q3: None means no rate-control flags, delegate to
    ffmpeg encoder default).

    Args:
        options: RenderOptions carrying hw_encoder, quality, crf, etc.

    Returns:
        ResolvedEncoder on success, or None for the 'none' path.

    Raises:
        ClipwrightError(UNSUPPORTED_OPERATION): When an explicit vendor fails
            and fallback is not permitted (AC-4).
        ClipwrightError(DEPENDENCY_MISSING): When ffmpeg cannot be located.
    """
    if options.hw_encoder == "none":
        return None

    ffmpeg = resolve_tool("ffmpeg", "CLIPWRIGHT_FFMPEG")

    # Resolve effective quality: options.quality → options.crf → None
    quality: int | None = options.quality
    if quality is None:
        quality = options.crf

    # Determine codec family (default h264)
    family = "h264"  # v1 default; future: derive from options

    if options.hw_encoder == "auto":
        system = platform.system()
        vendors = auto_priority(system)

        encoders_out = _get_encoders_output(ffmpeg)

        for vendor in vendors:
            candidate = resolve_encoder_name(vendor, family)
            if not encoder_listed(encoders_out, candidate):
                continue
            if _probe_encoder(ffmpeg, candidate):
                rc_flags = rate_control_flags(candidate, quality)
                hw_val = hwaccel_value(vendor)
                return ResolvedEncoder(
                    encoder_name=candidate,
                    rate_control_flags=rc_flags,
                    hwaccel_value=hw_val,
                    warnings=[],
                )

        # All candidates failed — fall back to libx264 (FR-8/ADR-5)
        rc_flags = rate_control_flags("libx264", quality)
        return ResolvedEncoder(
            encoder_name="libx264",
            rate_control_flags=rc_flags,
            hwaccel_value=None,
            warnings=[
                "No hardware encoder available; fell back to libx264."
                " Install GPU drivers or set hw_encoder='none'."
            ],
        )

    else:
        # Explicit vendor
        vendor = options.hw_encoder
        candidate = resolve_encoder_name(vendor, family)

        encoders_out = _get_encoders_output(ffmpeg)

        if encoder_listed(encoders_out, candidate) and _probe_encoder(
            ffmpeg, candidate
        ):
            rc_flags = rate_control_flags(candidate, quality)
            hw_val = hwaccel_value(vendor)
            return ResolvedEncoder(
                encoder_name=candidate,
                rate_control_flags=rc_flags,
                hwaccel_value=hw_val,
                warnings=[],
            )

        # Explicit vendor failed — raise, do NOT fall back (FR-9/AC-4)
        raise ClipwrightError(
            code=ErrorCode.UNSUPPORTED_OPERATION,
            message="Hardware encoder initialisation failed.",
            hint=(
                f"Encoder '{candidate}' (vendor '{vendor}') is present but"
                " failed to initialise; the GPU or driver may be unavailable."
                " Try hw_encoder='auto' or 'none'."
            ),
        )
