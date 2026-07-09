# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.2] - 2026-07-09

### Fixed

- **`available_range` now reflects the full source-media duration, not the kept sub-range**
  (GitHub Issue #1). Each keep-range clip's `MediaRef.available_range` is now built once per
  source as `TimeRange(0, media duration)` — using `clipwright`'s corrected video-stream-based
  duration (see `clipwright` v0.6.1, which no longer inflates `MediaInfo.duration` with audio
  drift) — instead of being left unset. `source_range` (the kept sub-range) remains a subset of
  `available_range`, giving NLE importers the true bounds of the source media.

## [0.2.1] - 2026-07-02

### Security

- Added an internal-error boundary guard to the tool entry point so unexpected
  exceptions no longer leak absolute paths in error messages (CWE-209).

## [0.2.0] - Unreleased

### Changed

- **Output placement ergonomics**: removed the same-directory co-location constraint.
  The output OTIO may now be placed in any directory whose parent already exists,
  enabling project-oriented layouts where media and timelines live in separate trees.
- **Output collision detection**: the check that rejects `output == media` now raises
  `PATH_NOT_ALLOWED` (previously `INVALID_INPUT`) via `clipwright.pathpolicy.check_output_not_source`.
- **OTIO media reference**: clips now embed a relative path when the media file lives
  under the OTIO directory, and an absolute path otherwise, via
  `clipwright.pathpolicy.media_ref_for_otio`.  This makes cross-directory timelines
  self-consistent regardless of working directory.
- Bumped `clipwright` core dependency to `>=0.4.0` (requires `pathpolicy.media_ref_for_otio`).

## [0.1.0] - Unreleased

### Added

- Initial release of `clipwright-trim`.
- `clipwright_trim` MCP tool: accepts explicit keep or drop time ranges (in seconds) and produces a kept-range OTIO timeline compatible with `clipwright-render`.
- **Keep mode**: retains specified ranges in enumeration order with optional outward padding.
- **Drop mode**: removes specified ranges and retains the complement, with optional inward padding.
- **Padding**: `padding_sec` applied per-range; in keep mode expands outward, in drop mode shrinks the dropped region inward (retaining more content).
- **Boundary clamping**: ranges extending beyond media duration are clamped with a warning in the response envelope.
- **Strict mutual exclusion**: providing both `keep` and `drop` in one call returns a descriptive error.
- **Render-compatible OTIO output**: single V1 track with Clip entries; consumed by `clipwright-render::resolve_kept_ranges` without modification.
- **No ffmpeg dependency**: only ffprobe (via `clipwright.media.inspect_media`) is required.
- MCP annotations: `readOnlyHint=True`, `destructiveHint=False`, `idempotentHint=True`, `openWorldHint=False`.
