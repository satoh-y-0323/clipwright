# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-06-17

### Added

- Initial release of `clipwright-frames` as part of clipwright v0.4.0.
- MCP tool `clipwright_extract_frames` for still-frame extraction from video files.
- Three extraction modes:
  - `interval` — extract one frame every N seconds (default: 10 s).
  - `scene` — extract frames at scene boundaries from a `clipwright-scene` OTIO timeline.
  - `timestamps` — extract frames at explicit timestamp positions.
- Output contract: image files (JPEG or PNG) + OTIO timeline (`frames.otio`) + JSON manifest (`frames.json`) written to a caller-specified directory.
- FFmpeg subprocess integration with argument-array invocation (`shell=False`), timeout, and stderr capture.
- MCP annotations: `readOnlyHint=false`, `destructiveHint=false`, `idempotentHint=true`.
- Standard `ToolResult` envelope (`ok`, `summary`, `data`, `artifacts`, `warnings`).
