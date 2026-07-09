# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.3] - 2026-07-09

### Fixed

- **`ExternalReference.available_range` is now populated** (GitHub Issue #1). `_add_full_clip`
  now sets `available_range` equal to `source_range` (the full `0..media duration` range), using
  `clipwright`'s corrected video-stream-based duration (see `clipwright` v0.6.1, which no longer
  inflates `MediaInfo.duration` with audio drift).

## [0.3.2] - 2026-07-02

### Security

- Added an internal-error boundary guard to the tool entry point so unexpected
  exceptions no longer leak absolute paths in error messages (CWE-209).

## [0.3.0] - 2026-06-26

### Changed
- **DC-AS-004**: Removed the same-directory-as-media constraint for `output`.
  Output may now be placed in any directory whose parent exists; the tool is
  typed as a **create** operation.
- **DC-AM-004**: `_add_full_clip()` now uses `media_ref_for_otio()` to write
  a relative POSIX `target_url` when the media is co-located with the output
  OTIO, and an absolute path otherwise.
- Timeline source validation replaced `_check_source_within_timeline_dir()` with
  `check_media_ref()`: absolute paths to existing files are accepted regardless of
  directory (DC-AM-004); relative path traversal remains rejected (CWE-22).
- `output` field description in the MCP tool updated to reflect the new policy.
- Dependency bumped to `clipwright>=0.4.0` (requires `pathpolicy` module).

## [0.2.0] - 2026-06-14

### Added
- **Typed outputSchema**: `clipwright_detect_loudness` now returns `ToolResult` instead of
  `dict[str, Any]`, enabling FastMCP to emit a typed `outputSchema` and populate
  `structuredContent` with all envelope fields (`ok`, `summary`, `data`, `artifacts`, `warnings`).
- **MCP boundary tests**: Added `TestMcpOutputSchema` with two tests that verify the typed
  `outputSchema` contract and that `structuredContent` exposes `ok` at the top level without
  extra wrapping.
- `to_tool_result` conversion at the server boundary lifts the dict returned by
  `detect_loudness` into a typed `ToolResult` (via `clipwright.envelope.to_tool_result`).

### Changed
- Return type of `clipwright_detect_loudness` changed from `dict[str, Any]` to `ToolResult`.
- Dependency pin updated to `clipwright>=0.2.0` (requires typed envelope helpers).
- Existing delegation tests updated to use `result.model_dump()` for field access
  instead of direct dict subscript.

## [0.1.1] - 2026-06-09

### Added
- Initial release of `clipwright-loudness` MCP tool.
- `clipwright_detect_loudness` tool: measures audio loudness with ffmpeg
  `loudnorm` / `volumedetect` and writes directives to OTIO timeline metadata.
- MCP-only entry point (`clipwright-loudness` console script → `mcp.run(transport="stdio")`).
- Supports `loudnorm` (EBU R128) and `peak` normalization modes.
- `track` scope only for initial render support.
