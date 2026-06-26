# Changelog — clipwright-transition

All notable changes to `clipwright-transition` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-06-26

### Changed

- **Removed co-location constraint** (`add_transition` transform I/O contract):
  output may now reside in any directory whose parent exists, not just within the
  timeline's directory tree.  Enables tool-chaining workflows where output is
  written to a separate artifacts directory (DC-AM-003).

- **`output == timeline` now returns `PATH_NOT_ALLOWED`** (previously `INVALID_INPUT`).
  Delegates to `clipwright.pathpolicy.check_output_not_source`; consistent with the
  shared transform tool error contract across the suite.

- **Requires `clipwright >= 0.4.0`** for `pathpolicy.check_output_not_source`.

### Removed

- Internal `_check_output_not_input` and `_check_output_within_timeline_dir`
  functions (replaced by `clipwright.pathpolicy.check_output_not_source`).

## [0.1.0] - 2026-06-23

### Added

- **Initial release**: MCP tool `clipwright_add_transition` that annotates an OTIO
  timeline with transition directives at internal clip boundaries for materialisation
  by `clipwright-render` (xfade/acrossfade filter chains).

- **`TransitionSpec` schema**: Specifies a transition type (`"fade"`, `"dissolve"`,
  `"fadeblack"`, `"fadewhite"`) and a `duration_sec` (0 < duration ≤ 5.0).

- **`BoundaryTransition` schema**: Per-boundary variant with `after_clip_index` (≥ 0),
  `type`, and `duration_sec`. The index refers to the zero-based clip *before* the
  boundary.

- **`AddTransitionOptions` schema**: Mutually exclusive `uniform` (single spec applied
  to all internal boundaries) or `per_boundary` (list of up to 1000 per-boundary specs,
  all internal boundaries must be covered in v1).

- **`resolve_transitions` pure logic** (`plan.py`): Expands uniform mode to all
  `[0, n_clips-2]` boundaries; validates per-boundary mode for range, duplicates, and
  full coverage. Returns an ascending `list[ResolvedTransition]`.

- **Validation pipeline** (`transition.py`):
  - `.otio` extension check on `output`.
  - Output parent directory existence check.
  - `output == timeline` (same-file) check.
  - `count_video_clips`: single V1 track required; OTIO Transition objects and Gaps
    are rejected or skipped; fewer than 2 clips → `INVALID_INPUT`.
  - Transition directive normalised to ascending order and stored in
    `metadata["clipwright"]["transition"]` (non-destructive; existing directives
    preserved under other keys).

- **Non-destructive output**: Input OTIO is loaded into memory, annotated, and saved
  to `output`. The source `timeline` file is never modified.

- **MCP annotations**: `readOnlyHint=false`, `destructiveHint=false`,
  `idempotentHint=true`, `openWorldHint=false`.

- **Error codes**: `INVALID_INPUT`, `UNSUPPORTED_OPERATION`, `FILE_NOT_FOUND`.

- **v1 scope note**: Partial/gapped `per_boundary` (not all internal boundaries
  covered) returns `UNSUPPORTED_OPERATION`. Mixed hard-cut + transition boundaries are
  planned for v2.
