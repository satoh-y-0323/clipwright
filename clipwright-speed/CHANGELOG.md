# Changelog — clipwright-speed

All notable changes to `clipwright-speed` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-07-02

### Security

- Added an internal-error boundary guard to the tool entry point so
  unexpected exceptions no longer leak absolute paths in error messages
  (CWE-209).

## [0.2.0] - 2026-06-26

### Changed

- **Removed co-location constraint** (`set_speed` transform I/O contract): output may
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

- **Initial release**: MCP tool `clipwright_set_speed` that annotates an OTIO
  timeline with `LinearTimeWarp` speed effects for materialisation by
  `clipwright-render`.

- **`SetSpeedOptions` schema**: `speed` (0.25–8.0, required) and `clip_index`
  (optional; omit to apply to all clips).

- **Idempotency**: applying twice with the same speed replaces rather than stacks
  the clipwright warp on each clip (AC-4).

- **Non-destructive**: the input timeline file is never modified (AC-1).

- **MCP annotations**: `readOnlyHint=false`, `destructiveHint=false`,
  `idempotentHint=true`, `openWorldHint=false`.
