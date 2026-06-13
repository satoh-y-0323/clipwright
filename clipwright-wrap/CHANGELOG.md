# Changelog

All notable changes to `clipwright-wrap` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-06-14

### Changed

- **Typed outputSchema**: `clipwright_wrap_captions` now returns `ToolResult` instead of
  `dict[str, Any]`. FastMCP generates a typed `outputSchema` from the Pydantic model,
  exposing all envelope fields (`ok`, `summary`, `data`, `artifacts`, `warnings`, `error`)
  to MCP clients.
- **structuredContent all fields**: All ToolResult fields are serialised to
  `structuredContent` at the top level (not wrapped in `{"result": ...}`), matching the
  wire contract shared across all Clipwright tools.
- **MCP-only console entry**: `clipwright-wrap` console script launches the MCP stdio
  server via `server:main()`. No CLI command interface is provided.
- **Dependency bump**: requires `clipwright>=0.2.0` for the updated `ToolResult` /
  `to_tool_result` envelope helpers.

## [0.1.1] - 2026-06-09

### Fixed

- subprocess worker (`wrap_cli`) UTF-8 encoding on Windows (cp932 decode bug).
- Input validation hardening: extension mismatch, same-path output, missing parent
  directory checks.

## [0.1.0] - 2026-06-01

### Added

- Initial release: `clipwright_wrap_captions` MCP tool for phrase-boundary line-break
  insertion into SRT/VTT subtitle files using BudouX.
- Supported languages: `ja`, `zh-hans`, `zh-hant`, `th`.
- Non-destructive: input subtitle file is never modified.
- Overflow detection and aggregated warnings for `max_lines` and `max_chars` violations.
