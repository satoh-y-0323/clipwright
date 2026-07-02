# Changelog

All notable changes to clipwright-bgm are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.1] - 2026-07-02

### Security

- Added an internal-error boundary guard to the tool entry point so unexpected
  exceptions no longer leak absolute paths in error messages (CWE-209).

## [0.3.0] - 2026-06-27

### Changed

- Relaxed BGM path policy: BGM file may now reside in any directory (external
  files accepted).  The ADR-B8 co-location constraint has been removed.
- Relaxed output path policy: output file may now be written to any directory
  whose parent already exists (accumulate contract).  The SR L-3 co-location
  constraint has been removed.
- Output collision detection is now performed via
  `clipwright.pathpolicy.check_output_not_source(output, [timeline, bgm])`,
  returning `PATH_NOT_ALLOWED` when output equals either source file.
- OTIO `target_url` for the BGM clip is now produced by
  `clipwright.pathpolicy.media_ref_for_otio`: relative POSIX path when BGM is
  under the output's parent directory, absolute path otherwise.
- Removed internal helpers `_check_bgm_within_timeline_dir`,
  `_check_output_within_timeline_dir`, and `_same_path` from `bgm.py`.
- Bumped dependency to `clipwright>=0.4.0` (requires `pathpolicy` module).

## [0.2.0] - 2026-06-14

### Added

- Typed output schema: `clipwright_add_bgm` now returns `ToolResult` (FastMCP emits typed `outputSchema`).
- `structuredContent` and `content` now include all `ToolResult` fields (`ok`, `summary`, `data`, `artifacts`, `warnings`, `error`) with null/empty defaults — no extra wrapping layer.
- MCP-only console entry (`clipwright-bgm`) starts the MCP server over stdio.

### Changed

- Bumped dependency `clipwright>=0.2.0` to align with core ToolResult/envelope contract.

## [0.1.1] - 2026-06-09

### Fixed

- Initial stabilisation release; bgm.py input-validation hardening and OTIO track creation fixes.

## [0.1.0] - 2026-06-01

### Added

- Initial release: `clipwright_add_bgm` MCP tool to annotate OTIO timelines with BGM placement metadata.
- BgmOptions schema (volume_db / fade_in_sec / fade_out_sec / ducking).
- MCP annotations: `readOnlyHint=False` / `destructiveHint=False` / `idempotentHint=True` / `openWorldHint=False`.

[Unreleased]: https://github.com/satoh-y-0323/clipwright/compare/clipwright-bgm-v0.3.0...HEAD
[0.3.0]: https://github.com/satoh-y-0323/clipwright/compare/clipwright-bgm-v0.2.0...clipwright-bgm-v0.3.0
[0.2.0]: https://github.com/satoh-y-0323/clipwright/compare/clipwright-bgm-v0.1.1...clipwright-bgm-v0.2.0
[0.1.1]: https://github.com/satoh-y-0323/clipwright/compare/clipwright-bgm-v0.1.0...clipwright-bgm-v0.1.1
[0.1.0]: https://github.com/satoh-y-0323/clipwright/releases/tag/clipwright-bgm-v0.1.0
