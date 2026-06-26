# Changelog — clipwright-reframe

All notable changes to this project will be documented in this file.

## [0.3.0] — 2026-06-26

### Changed

- **Output path policy relaxed**: the `.otio` output file may now be placed in
  any directory with an existing parent.  The previous co-location restriction
  (output must reside in the same directory as the media file) is removed.
- **OTIO media reference now context-aware**: `_add_full_clip` calls
  `clipwright.pathpolicy.media_ref_for_otio()` to embed a *relative* POSIX path
  when the media file is under the output directory tree, and an *absolute* path
  when the media file is outside the tree.  Previously the reference was always
  absolute (`str(media_path.resolve())`).
- **Source collision check unified**: `_same_path` (which returned
  `INVALID_INPUT`) is replaced by
  `clipwright.pathpolicy.check_output_not_source()` (which returns
  `PATH_NOT_ALLOWED`).  The error code for `output == media` and
  `output == timeline` changes from `INVALID_INPUT` to `PATH_NOT_ALLOWED`.
- **Dependency floor raised**: `clipwright>=0.4.0` (adds `pathpolicy` module
  with `check_output_not_source`, `media_ref_for_otio`).

### Removed

- `reframe._same_path()` — replaced by `check_output_not_source`.
- `reframe._check_output_within_media_dir()` — co-location guard removed per
  spec4 #5 path-boundary relaxation.

## [0.2.0] — 2026-06-09

### Added

- `mode='track'`: motion-centroid crop-from-source using `track_cli` subprocess
  (numpy optional; constant-center fallback when unavailable).
- `_run_track_cli`: spawns `clipwright_reframe.track_cli` as a subprocess to
  keep numpy out of the MCP server process.
- Timeout guard for extreme `duration_sec` values (CWE-400 / SR-V-001).
- N_max=80 keyframe cap (`_TRACK_MAX_KEYFRAMES`) locked to `track_cli` and
  `clipwright-render` via `TestNMaxSync`.

## [0.1.0] — 2026-05-01

### Added

- Initial release: `clipwright_reframe` MCP tool annotating a reframe directive
  (`target_w`, `target_h`, `mode`, `anchor`, `pad_color`) to OTIO timeline
  metadata for `clipwright-render` to apply.
- Modes: `crop`, `pad`, `blur_pad`.
- Non-destructive: input media and OTIO are never modified.
