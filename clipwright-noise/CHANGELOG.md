# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-06-14

### Added

- **Typed outputSchema**: `clipwright_detect_noise` now returns `ToolResult` (Pydantic model) instead of `dict[str, Any]`. FastMCP generates a fully typed `outputSchema` that exposes all envelope fields (`ok`, `summary`, `data`, `artifacts`, `warnings`, `error`) to MCP clients.
- **Structured content**: All envelope fields are serialised directly at the top level of `structuredContent` (not wrapped in a `result` key), consistent with the MCP structured-output contract.
- **MCP-only console entry**: `clipwright-noise` script entry point launches the MCP server over stdio. No CLI argument parsing is needed for this tool.

### Changed

- `detect_noise` and `_detect_noise_inner` in `noise.py` now have return type `ToolResult` (was `dict[str, Any]`). The public API remains functionally identical; only the static type annotation changes.
- `server.py` return type annotation updated from `dict[str, Any]` to `ToolResult`.
- Bumped dependency `clipwright>=0.2.0` (was `>=0.1.1`).

## [0.1.1] - 2026-06-09

### Fixed

- Initial release with afftdn noise detection and OTIO timeline annotation.
- DeepFilterNet backend annotation only (render application unsupported in first release).
