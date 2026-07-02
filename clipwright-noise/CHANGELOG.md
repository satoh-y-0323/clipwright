# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.2] - 2026-07-02

### Security

- Added an internal-error boundary guard to the tool entry point so unexpected
  exceptions no longer leak absolute paths in error messages (CWE-209).

## [0.3.0] - 2026-06-27

### Changed

- **Removed same-directory constraint**: `clipwright_detect_noise` / `detect_noise`
  no longer requires the output `.otio` file to reside in the same directory as the
  input media.  The output may now be placed in any directory whose parent already
  exists, enabling cross-directory workflow chaining.
- **Always-absolute `target_url`**: Clip `target_url` in newly generated timelines is
  now always the resolved absolute path of the media file (DC-AS-002).  Relative
  references are no longer written to the OTIO so that `render_timeline` can accept
  the timeline via the ADR-PP-1 absolute escape hatch without a co-location boundary
  restriction.
- **`_add_full_clip` API change**: The internal `otio_dir` parameter has been removed
  from `_add_full_clip`; the function now always resolves the absolute path directly.
- Bumped dependency `clipwright>=0.2.0` → `clipwright>=0.4.0`.

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
