# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
