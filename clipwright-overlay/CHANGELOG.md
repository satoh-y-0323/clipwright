# Changelog — clipwright-overlay

All notable changes to `clipwright-overlay` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-07-09

### Added

- **New MCP tool `clipwright_add_pip`**: annotates an OTIO timeline with a
  picture-in-picture (PiP) video overlay marker (`kind="pip_overlay"`) for
  materialisation by `clipwright-render`.

- **`AddPipOptions` schema**: Parameters -- `media_path`, `start_sec`,
  `duration_sec` (required), `media_start_sec` (default `0.0`), `x`/`y`
  (default centered), `scale` (default `0.3`, range `(0, 8]` -- note this
  differs from `AddOverlayOptions.scale`'s `1.0` default), `opacity` (default
  `1.0`), `fade_in_sec`/`fade_out_sec` (default `0.3`), `mix_audio` (default
  `False`), `audio_volume` (default `1.0`, range `(0, 4]`), and `ducking`
  (`PipDuckingOptions`, default disabled). `inf`/`nan` rejected at schema
  boundary (`allow_inf_nan=False`).

- **`PipDuckingOptions` schema**: `enabled`/`threshold`/`ratio`, a local
  re-declaration of `clipwright-bgm`'s `DuckingOptions` shape (no
  cross-satellite-package import; see architecture-report-20260709-093022.md
  ADR-PIP-4).

- **Validation pipeline** (`_validate_pip_fields`, first-failure order):
  1. Value domain: `start_sec >= 0`, `duration_sec > 0`, `media_start_sec >= 0`,
     `scale in (0, 8]`, `opacity in [0, 1]`, `fade_in/out_sec >= 0`,
     `fade_in + fade_out <= duration`, `audio_volume in (0, 4]`.
  2. `media_path` 4-stage validation: path safety (control chars /
     single-quote) -> existence + symlink rejection (`FILE_NOT_FOUND` /
     `PATH_NOT_ALLOWED`, CWE-59) -> extension allowlist (`.mp4`, `.mkv`,
     `.mov`, `.webm`) -> video stream presence via `inspect_media`
     (audio-only sources rejected with a hint pointing at
     `clipwright_add_bgm`).
  3. `x`/`y` allowlist (same pattern as `image_overlay`).

- **Relative-path storage**: `media_path` is stored via `media_ref_for_otio`
  (ADR-PP-1): relative POSIX path when co-located under the output OTIO's
  parent directory tree, absolute path otherwise.

- **Accumulate pattern**: Each call appends a new `pip_overlay` marker named
  `pip_{n}` (0-indexed) to the first video track (V1). Maximum 4 markers per
  timeline (`_MAX_PIP_OVERLAYS = 4`, a much lower cap than
  `_MAX_IMAGE_OVERLAYS` since each PiP decodes a full video stream). V1
  absent -> `UNSUPPORTED_OPERATION`.

- **Idempotency**: Duplicate detection compares `media_path` (relative
  string, exact), `x`, `y`, `mix_audio`, `ducking.enabled` (exact), and
  numeric fields (including `ducking.threshold`/`ducking.ratio`) with
  `<= 1e-6` tolerance. Identical PiP overlay -> `applied=0` + warning; no
  duplicate marker written.

- **Return envelope** (`ok_result`): `applied` (1 or 0), `pip_count`,
  `start_sec`, `duration_sec` in `data`; `artifacts`:
  `[{"role": "timeline", "path": "<output>", "format": "otio"}]`.

- **MCP annotations**: `readOnlyHint=true` (writes only a new `.otio`; input
  media and timeline are never modified), `destructiveHint=false`,
  `idempotentHint=true`, `openWorldHint=false`.

## [0.2.2] - 2026-07-03

### Security

- `image_path` and `timeline` source inputs are now validated through the
  shared `clipwright.pathpolicy.validate_source_file` guard, rejecting
  symlinked paths (CWE-59).

## [0.2.1] - 2026-07-02

### Security

- Added an internal-error boundary guard to the tool entry point so
  unexpected exceptions no longer leak absolute paths in error messages
  (CWE-209).

## [0.2.0] - 2026-06-27

### Changed

- **Path policy relaxation (ADR-PP-1)**: The co-location restriction on
  `image_path` and `output` has been removed.
  - `output` may now be placed anywhere (not constrained to the input
    timeline's parent directory); only `output != timeline` is enforced.
  - `image_path` may reference images inside or outside the output OTIO's
    parent directory. Storage follows `media_ref_for_otio` from `clipwright`
    ≥ 0.4.0: relative POSIX path when inside the output OTIO's directory,
    absolute path when outside. No `../` traversal is stored.

- **`image_path` validation reduced from 4-stage to 3-stage**: The co-location
  check (stage 2 in v0.1.0) is removed. New order:
  path safety → existence (`FILE_NOT_FOUND`) → extension allowlist.

- **Idempotency comparison updated**: Uses `media_ref_for_otio` to compute the
  stored path representation before comparing, covering both relative and
  absolute stored paths consistently.

- **`clipwright` dependency bumped** to `>=0.4.0` (requires `media_ref_for_otio`
  and `check_output_not_source` from `clipwright.pathpolicy`).

- **DC-AM-003 round-trip safety**: Existing relative media references in clip
  `target_url` fields are preserved unchanged across the load→save round-trip
  when an external image marker is added.

## [0.1.0] - 2026-06-22

### Added

- **Initial release**: MCP tool `clipwright_add_overlay` that annotates an OTIO
  timeline with a static image overlay (PNG/JPEG/WebP logo, watermark, lower-third
  graphic, end card) for materialisation by `clipwright-render`.

- **`AddOverlayOptions` schema**: Parameters — `image_path`, `start_sec`,
  `duration_sec`, `x` (default `"(W-w)/2"`), `y` (default `"(H-h)/2"`),
  `scale` (default `1.0`, range `(0, 8]`), `opacity` (default `1.0`, range
  `[0, 1]`), `fade_in_sec` (default `0.3`), `fade_out_sec` (default `0.3`).
  `inf`/`nan` rejected at schema boundary (`allow_inf_nan=False`).

- **Validation pipeline** (`_validate_overlay_fields`, first-failure order):
  1. Value domain: `start_sec ≥ 0`, `duration_sec > 0`, `scale ∈ (0, 8]`,
     `opacity ∈ [0, 1]`, `fade_in/out_sec ≥ 0`, `fade_in + fade_out ≤ duration`.
  2. `image_path` 4-stage validation:
     - Co-location: image must be under the output timeline's parent directory
       tree (mirrors `clipwright-render`'s source boundary — `PATH_NOT_ALLOWED`).
     - Existence check: `FILE_NOT_FOUND` (basename only — CWE-209).
     - Extension allowlist: `.png`, `.jpg`, `.jpeg`, `.webp` — `INVALID_INPUT`.
     - Path safety: control characters and single-quote prohibited — `INVALID_INPUT`.
  3. `x` / `y` allowlist: `^[A-Za-z0-9_()+\-*/. ]+$`; prohibits `: ; [ ] , '`
     and control characters (filtergraph injection prevention — `INVALID_INPUT`).

- **Relative-path storage (V2-3 round-trip portability)**: `image_path` is stored
  in the OTIO marker as a POSIX relative path from the output timeline's parent
  directory (e.g. `assets/logo.png`). `clipwright-render` reconstructs the absolute
  path using the render-time timeline's parent as the base. Projects remain portable
  when moved to a different directory, as long as the relative positions of the
  timeline and image files are preserved.

- **Accumulate pattern**: Each call appends a new `image_overlay` marker named
  `image_{n}` (0-indexed) to the first video track (V1). Maximum 64 markers per
  timeline (`_MAX_IMAGE_OVERLAYS = 64`). V1 absent → `UNSUPPORTED_OPERATION`.

- **Idempotency**: Duplicate detection compares `image_path` (relative string, exact),
  `x`, `y` (exact), and numeric fields with `≤ 1e-6` tolerance. Identical overlay →
  `applied=0` + warning; no duplicate marker written.

- **Rate resolution** (`_resolve_rate`): Uses the first V1 clip's
  `source_range.rate`, then any existing `image_overlay` marker rate, then fallback
  `1000.0` with a warning. Consistent with `clipwright-text`.

- **Return envelope** (`ok_result`):
  - `applied` (1 or 0), `overlay_count`, `start_sec`, `duration_sec` in `data`.
  - `artifacts`: `[{"role": "timeline", "path": "<output>", "format": "otio"}]`.
  - `summary`: AI-readable one-line with basename, timing, count, and output name.
  - `warnings`: rate fallback or idempotency notice.

- **MCP annotations**: `readOnlyHint=true` (writes only a new `.otio`; input media
  and timeline are never modified; new-file write is outside the readOnly scope),
  `destructiveHint=false`, `idempotentHint=true`, `openWorldHint=false`.

- **Error codes**: `INVALID_INPUT`, `FILE_NOT_FOUND`, `PATH_NOT_ALLOWED`,
  `UNSUPPORTED_OPERATION`.
