# Changelog — clipwright-overlay

All notable changes to `clipwright-overlay` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
