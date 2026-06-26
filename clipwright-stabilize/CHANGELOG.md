# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-06-26

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

## [0.1.0] - 2026-06-14

### Added
- Initial release of `clipwright-stabilize` MCP tool.
- `clipwright_detect_shake` tool: runs ffmpeg `vidstabdetect` to generate a
  `.trf` motion-analysis file and writes a stabilize directive to OTIO timeline
  metadata.
- MCP-only entry point (`clipwright-stabilize` console script →
  `mcp.run(transport="stdio")`).
- Shake severity estimation from `.trf` binary header; `severity=None` is
  tolerated and the directive is still written.
- `.trf` path and OTIO path both verified to exist before returning `ok_result`.
