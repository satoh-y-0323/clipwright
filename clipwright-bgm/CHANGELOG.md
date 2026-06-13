# Changelog

All notable changes to clipwright-bgm are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/satoh-y-0323/clipwright/compare/clipwright-bgm-v0.2.0...HEAD
[0.2.0]: https://github.com/satoh-y-0323/clipwright/compare/clipwright-bgm-v0.1.1...clipwright-bgm-v0.2.0
[0.1.1]: https://github.com/satoh-y-0323/clipwright/compare/clipwright-bgm-v0.1.0...clipwright-bgm-v0.1.1
[0.1.0]: https://github.com/satoh-y-0323/clipwright/releases/tag/clipwright-bgm-v0.1.0
