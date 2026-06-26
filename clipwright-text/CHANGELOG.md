# Changelog — clipwright-text

All notable changes to `clipwright-text` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-06-26

### Changed

- **Removed co-location constraint** (`add_text` transform I/O contract): output may
  now reside in any directory whose parent exists, not just within the timeline's
  directory tree.  Enables tool-chaining workflows where output is written to a
  separate artifacts directory (DC-AM-003).

- **`output == timeline` now returns `PATH_NOT_ALLOWED`** (previously `INVALID_INPUT`).
  Delegates to `clipwright.pathpolicy.check_output_not_source`; consistent with the
  shared transform tool error contract across the suite.

- **Requires `clipwright >= 0.4.0`** for `pathpolicy.check_output_not_source`.

### Removed

- Internal `_check_output_within_timeline_dir` function (replaced by
  `clipwright.pathpolicy.check_output_not_source`).

## [0.1.0] - 2026-06-23

### Added

- **Initial release**: MCP tool `clipwright_add_text` that annotates an OTIO
  timeline with `text_overlay` markers for materialisation by `clipwright-render`
  as `drawtext` filters.

- **`AddTextOptions` schema**: `text`, `start_sec`, `duration_sec` (required);
  `x`, `y`, `font_size`, `font_color`, `box`, `box_color`, `fade_in_sec`,
  `fade_out_sec`, `font_path` (optional with sensible defaults).

- **Idempotency**: exact-duplicate overlays produce `applied=0` and a warning
  rather than adding a second marker (ADR-T1).

- **Non-destructive**: the input timeline file is never modified.

- **MCP annotations**: `readOnlyHint=false`, `destructiveHint=false`,
  `idempotentHint=true`, `openWorldHint=false`.
