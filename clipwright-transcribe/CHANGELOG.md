# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-06-26

### Changed

- **Removed same-directory constraint**: `clipwright_transcribe` / `transcribe_media`
  no longer requires the output `.otio` file to reside in the same directory as the
  input media.  The output may now be placed in any directory whose parent already
  exists, enabling cross-directory workflow chaining.
- **`target_url` via `media_ref_for_otio`**: Clip `target_url` is now computed by
  `clipwright.pathpolicy.media_ref_for_otio()`.  When the media file is under the OTIO
  output directory the URL is relative (portable); when it is outside, the URL is
  absolute (ADR-PP-1).
- Bumped dependency `clipwright>=0.2.0` → `clipwright>=0.4.0` to pick up
  `pathpolicy.media_ref_for_otio`.

## [0.3.0] - 2026-06-22

### Added

- **`data.backend` and `data.realtime_factor`**: The transcribe envelope now surfaces
  `data.backend` (fields: `device` in `cuda | metal | cpu | unknown`, `detail` with a
  sanitized fixed device label (CWE-209: no raw stderr / model path); e.g. `"CUDA"`,
  `"Metal"`, `"cpu"`, `""`) and `data.realtime_factor`
  (`audio_duration_sec / whisper_wall_seconds`; values **above 1.0 mean faster than
  realtime**) so callers can confirm the GPU device and transcription speed without
  parsing `summary`. `data.whisper_wall_seconds` (raw wall-clock seconds in the whisper
  subprocess) is also included.
- **`summary` backend reporting**: The one-line `summary` now includes the backend
  used (e.g. `" Backend: cuda (12.5x realtime)."`) so the GPU device is visible without
  unpacking `data`.
- **GPU / CUDA acceleration guidance in README**: New `## GPU / CUDA Acceleration`
  section documents how to point `CLIPWRIGHT_WHISPER` at a CUDA or Metal whisper.cpp
  build, confirms no code changes are required, and explains `data.backend.device` /
  `data.realtime_factor` for runtime verification.

### Changed

- **Version reconciliation**: `__init__.py` and `pyproject.toml` versions unified to
  `0.3.0` (previously `0.1.1` / `0.2.0` respectively).
- Depends on `clipwright>=0.2.0` (unchanged; `run()` → `CompletedProcess.stderr`
  contract already satisfied).

## [0.2.0] - 2026-06-14

### Added

- **Typed outputSchema**: The `clipwright_transcribe` MCP tool now returns
  `ToolResult` instead of `dict[str, Any]`. FastMCP generates a typed
  `outputSchema` so MCP clients can validate the full envelope shape
  (`ok`, `summary`, `data`, `artifacts`, `warnings`, `error`).
- **structuredContent all fields**: All envelope fields are surfaced via
  `structuredContent` without wrapper nesting, conforming to the §6.3
  return-value contract.
- **MCP-only console entry**: The `clipwright-transcribe` script entry remains
  a pure stdio MCP server (`mcp.run(transport="stdio")`). No CLI-specific
  behaviour was added; MCP clients are the sole consumers.

### Changed

- Return type annotation of `clipwright_transcribe` tool function updated from
  `dict[str, Any]` to `ToolResult`.
- Raw dict from `transcribe_media` is now lifted through `to_tool_result()`
  before being returned by the tool function, ensuring consistent typed output.
- Depends on `clipwright>=0.2.0` (typed envelope / `to_tool_result` helper).

## [0.1.1] - 2026-06-09

### Fixed

- Initial release fixes for packaging and entry point registration.

## [0.1.0] - 2026-06-09

### Added

- Initial release: `clipwright_transcribe` MCP tool — transcribe audio/video
  with whisper.cpp and produce `.srt`, `.vtt`, and `.otio` outputs.
