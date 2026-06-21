# Changelog — clipwright-sequence

All notable changes to `clipwright-sequence` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
  - `warnings`: per-clip range-clamp notices (e.g. `end_sec` clamped to source duration).

- **MCP annotations**: `readOnlyHint=true`, `destructiveHint=false`,
  `idempotentHint=true`, `openWorldHint=false`.

- **Error codes**: `INVALID_INPUT`, `FILE_NOT_FOUND`, `PATH_NOT_ALLOWED`,
  `PROBE_FAILED`, `DEPENDENCY_MISSING`.
