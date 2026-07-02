# Changelog — clipwright-sequence

All notable changes to `clipwright-sequence` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-07-02

### Security

- Added an internal-error boundary guard to the tool entry point so
  unexpected exceptions no longer leak absolute paths in error messages
  (CWE-209).

## [0.2.0] - 2026-06-27

### Changed

- **External-source policy relaxed** (ADR-SEQ-6): source media files may now reside
  in any readable location — not just under the output `.otio` parent directory.
  The previous co-location restriction is removed; `output == any source` is still
  rejected (`PATH_NOT_ALLOWED`).

- **OTIO `target_url` encoding**: co-located sources are now stored as relative POSIX
  paths (e.g. `"footage/clip.mp4"`) via `pathpolicy.media_ref_for_otio`.  External
  sources continue to be stored as absolute POSIX paths.  This allows the produced
  OTIO file to be relocated as a self-contained unit when all sources are co-located.

- **Dependency**: `clipwright>=0.4.0` (requires `pathpolicy.media_ref_for_otio` and
  `pathpolicy.check_output_not_source`).

- **MCP tool `output` field description** updated to clarify create semantics and
  multi-location source support.

## [0.1.0] - 2026-06-22

### Added

- **Initial release**: MCP tool `clipwright_build_sequence` that assembles an ordered
  list of source media files into a single multi-source OTIO timeline (single V1 video
  track; A1 audio track left empty) for concatenation by `clipwright-render`.

- **`SequenceClip` schema**: Each clip entry specifies a `media` path and an optional
  sub-range via `start_sec` (default `0.0`) and `end_sec` (default: full source
  duration). Up to 1000 clips per call (DC-GP-003).

- **Source validation pipeline** (per unique source, first-occurrence order):
  - `inspect_media` probe via `CLIPWRIGHT_FFPROBE` (duration, video stream presence,
    frame-rate sentinel check).
  - Co-location boundary check: source must be under the output `.otio` parent
    directory or a recursive subdirectory (mirrors `clipwright-render`'s boundary
    so the produced timeline round-trips without `PATH_NOT_ALLOWED` — ADR-SEQ-6).
  - Symlink sources rejected (DC-AS-005).

- **Range resolution** (`plan.resolve_clip_specs`): pure arithmetic, no I/O; validates
  `start_sec < end_sec`, `end_sec ≤ source duration`, and per-range overlap / order.

- **OTIO construction**: `new_timeline` + `add_clip` from `clipwright.otio_utils`;
  each clip carries `metadata["clipwright"]` with `tool`, `version`, `kind`, and
  positional `index`.

- **Atomic save** via `clipwright.otio_utils.save_timeline`.

- **Return envelope** (`ok_result`):
  - `clip_count`, `total_duration_sec` (approximate — DC-AM-003), `unique_source_count`
    in `data`.
  - `artifacts`: `[{"role": "timeline", "path": "<output>", "format": "otio"}]`.
  - `warnings`: always empty in v0.1.0 (probe-error tolerance absorption is silent; no clamping warnings are emitted — DC-AS-003).

- **MCP annotations**: `readOnlyHint=true`, `destructiveHint=false`,
  `idempotentHint=true`, `openWorldHint=false`.

- **Error codes**: `INVALID_INPUT`, `FILE_NOT_FOUND`, `PATH_NOT_ALLOWED`,
  `PROBE_FAILED`, `DEPENDENCY_MISSING`.
