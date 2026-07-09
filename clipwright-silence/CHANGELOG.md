# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.2] - 2026-07-09

### Fixed

- **`available_range` now reflects the full source-media duration, not the kept sub-range**
  (GitHub Issue #1). Each keep-range clip's `MediaRef.available_range` is now built once per
  source as `TimeRange(0, media duration)` — using `clipwright`'s corrected video-stream-based
  duration (see `clipwright` v0.6.1, which no longer inflates `MediaInfo.duration` with audio
  drift) — instead of being left unset. `source_range` (the kept sub-range) remains a subset of
  `available_range`, giving NLE importers the true bounds of the source media.

## [0.3.1] - 2026-07-02

### Security

- Added an internal-error boundary guard to the tool entry point so unexpected
  exceptions no longer leak absolute paths in error messages (CWE-209).

## [0.3.0] - 2026-06-27

### Changed

- **Removed same-directory constraint (silencedetect backend)**: `clipwright_detect_silence`
  no longer requires the output `.otio` file to reside in the same directory as the input
  media when using the `silencedetect` backend.  The output may now be placed in any
  directory whose parent already exists, enabling cross-directory workflow chaining.
- **VAD backend co-location requirement**: When `backend="vad"`, the output `.otio` must
  still be placed in the same directory as the input media file.  A new
  `INVALID_INPUT` error is returned immediately (before VAD invocation) when this
  constraint is violated.
- **Always-absolute `target_url`**: Clip `target_url` is now always the resolved absolute
  path of the media file (DC-AS-001).  Relative references are no longer written to the
  OTIO so that `render_timeline` can accept the timeline via the ADR-PP-1 absolute escape
  hatch without a co-location boundary restriction.
- Bumped dependency `clipwright>=0.2.0` → `clipwright>=0.4.0`.

## [0.2.0] - 2026-06-14

### Added
- **Typed `outputSchema`**: `clipwright_detect_silence` now returns `ToolResult` instead
  of `dict[str, Any]`. FastMCP generates a typed `outputSchema` from the Pydantic model,
  allowing MCP clients to validate the response shape at the schema level.
- **Structured content / all fields**: The tool response exposes all `ToolResult` fields
  (`ok`, `summary`, `data`, `artifacts`, `warnings`, `error`) directly in
  `structuredContent` without any wrapping key. This conforms to the envelope contract
  defined in `clipwright>=0.2.0`.
- **MCP-only console entry**: `clipwright-silence` script launches the MCP server over
  stdio (`mcp.run(transport="stdio")`). No CLI subcommands are exposed; the entry point
  is exclusively for MCP agent integration.
- **MCP boundary tests**: Added `TestMcpBoundary` in `tests/test_server.py` to assert
  that `outputSchema` declares `ok` in `properties` and that `call_tool` returns
  `structuredContent` without a wrapping `result` key.

### Changed
- Bumped dependency `clipwright>=0.1.1` → `clipwright>=0.2.0` to align with the typed
  envelope (`ToolResult`, `to_tool_result`) introduced in the core package.
- Return type annotation of `clipwright_detect_silence` updated from `dict[str, Any]`
  to `ToolResult`.
- Test assertions updated to use `result.model_dump()` instead of direct dict subscript
  access to match the typed return value.

## [0.1.1] - 2025-01-01

### Fixed
- Initial bugfix release (placeholder — see git history for details).

## [0.1.0] - 2025-01-01

### Added
- Initial release: silence detection via ffmpeg `silencedetect` filter.
- Optional VAD backend using Silero VAD (`clipwright-silence[vad]`).
- OTIO timeline generation for KEEP intervals.
- MCP tool `clipwright_detect_silence` with annotations (`readOnlyHint`, `destructiveHint`,
  `idempotentHint`).
