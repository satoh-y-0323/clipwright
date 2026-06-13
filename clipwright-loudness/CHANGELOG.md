# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
