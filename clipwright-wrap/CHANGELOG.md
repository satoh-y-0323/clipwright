# Changelog

All notable changes to `clipwright-wrap` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.1] - 2026-07-02

### Security

- **`input` now rejects symbolic links (CWE-59)** — subtitle `input` is validated through the
  shared `clipwright.pathpolicy.validate_source_file` guard, closing a path-boundary bypass
  where a symlinked subtitle file could point outside the intended source tree.
- **Extension-error messages no longer echo caller-supplied extensions (CWE-209)** — the
  "unsupported extension" errors for `input`/`output` now use a fixed message instead of
  interpolating the caller-supplied path/extension, and an internal-error boundary guard
  prevents unexpected exceptions from disclosing filesystem paths.

## [0.3.0] - 2026-06-29

### Added

- **Latin (space-delimited) language word-wrap**: `clipwright_wrap_captions` now
  accepts space-delimited Latin-script languages (`en`, `es`, `fr`, `de`, `it`,
  `pt`, `nl`) in addition to CJK/Thai. Latin cues are wrapped on word boundaries
  using whitespace segmentation, preserving a single space between words. This
  unblocks the transcribe → wrap → render chain for English subtitles.

### Changed

- **`language` accepts Latin allowlist**: the MCP input schema `language` pattern
  is extended from `^(ja|zh-hans|zh-hant|th)$` to include the Latin allowlist.
  CJK/Thai segmentation (budoux) and output remain byte-for-byte unchanged.
- **`wrap_cue_lines` / `_merge_to_max_lines` gain a `joiner` parameter** (default
  `""`): CJK uses `""` (no delimiter, unchanged); Latin uses `" "`. The
  `max_chars` budget accounts for the joiner.

### Fixed

- **`__version__` drift**: `clipwright_wrap.__version__` corrected from `0.1.1`
  to match the package version.

### Notes

- Latin word-wrap runs in-process (whitespace split); it does not launch the
  budoux subprocess. `DEPENDENCY_MISSING` (budoux) can only occur on the CJK/Thai
  path.

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
- **`max_lines` overflow resolved by deterministic greedy front-merge**: when the wrapped
  line count exceeds `max_lines`, adjacent lines are collapsed from the front (empty
  separator, no truncation) until `len(lines) <= max_lines`. This replaces the previous
  warning-only behavior that left the overflow unresolved.
- **`data.merged_cue_indices`**: reports the cue indices that were collapsed during
  front-merge.
- **Overflow warning model simplified**: line-count overflow warnings and
  `data.overflow_cue_indices` are removed. Width-overflow detection
  (`data.overflow_width_cue_indices` + `max_chars` warnings) is unchanged and now runs on
  post-merge lines; a cue widened past `max_chars` by merging is reported.
- **Summary string updated**: the summary now reports collapsed/`max_chars` counts instead
  of "exceeded limits".

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
