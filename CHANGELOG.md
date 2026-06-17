# Changelog

All notable changes to `clipwright` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`clipwright-text` package (v0.1.0)**: New MCP tool `clipwright_add_text` that annotates an
  OTIO timeline with text overlay settings (position, font, size, color, timing). The annotation
  is non-destructive; the `drawtext` filter pass is materialized in a single render pass by
  `clipwright-render`.
- **`clipwright-render` drawtext support (v0.4.0)**: `clipwright_render` now realizes text
  overlay annotations written by `clipwright_add_text`, applying them via the FFmpeg `drawtext`
  filter in a single render pass.

## [0.5.0] - 2026-06-17

### Added

- **`clipwright-speed` package (v0.1.0)**: New MCP tool `clipwright_set_speed` that annotates a
  clip with a speed multiplier by writing an OTIO `LinearTimeWarp` effect. The annotation is
  non-destructive; the actual `setpts`/`atempo` filter pass is materialized in a single render
  pass by `clipwright-render`.
- **`clipwright-render` LinearTimeWarp support (v0.3.0)**: `clipwright_render` now realizes
  `LinearTimeWarp` effects written by `clipwright_set_speed`. Video timing is adjusted via the
  `setpts` filter and audio pitch-corrected via `atempo`, both applied in a single FFmpeg pass.

## [0.4.0] - 2026-06-17

### Added

- **`clipwright-frames` package (v0.1.0)**: New MCP tool `clipwright_extract_frames` for still-frame
  extraction from video. Supports three extraction modes — `interval` (fixed interval in seconds),
  `scene` (one frame per scene boundary from a `clipwright-scene` OTIO timeline), and `timestamps`
  (explicit list of timestamp positions). Writes extracted images to an output directory and returns
  OTIO markers and a JSON manifest as artifacts.

## [0.3.0] - 2026-06-16

### Added

- **`clipwright` core (v0.3.0)**: Added `otio_utils.get_markers()` to collect markers across
  tracks, optionally filtered by clipwright kind.
- **`clipwright-scene` package (v0.1.0)**: New MCP tool `clipwright_detect_scenes` for shot
  boundary detection. Detects scene transitions via FFmpeg's `scdet` filter (default) or
  PySceneDetect (optional backend) and writes detected boundaries as OTIO markers into a new
  or existing timeline. Supports configurable `threshold` (0–1), `min_scene_duration` (seconds),
  and `backend` (`ffmpeg` | `pyscenedetect`).
- **FFmpeg 8.x `scdet` output format support** (`clipwright-scene`): Added dual-regex parsing
  for the new `lavfi.scd.score: X, lavfi.scd.time: Y` format introduced in FFmpeg 8.x alongside
  the legacy `pts_time=X score=Y` format. The parser tries the new format first and falls back
  to the legacy format automatically.

### Changed

- **MCP `call_tool()` test protocol**: All package test suites (`clipwright-scene`,
  `clipwright-silence`, `clipwright-loudness`, `clipwright-noise`, `clipwright-transcribe`,
  `clipwright-bgm`, `clipwright-wrap`) now invoke tools via `mcp.call_tool()` (FastMCP test
  client) instead of calling Python functions directly. Tests now exercise the full MCP wire
  path including input validation, schema coercion, and `structuredContent` serialization.

## [0.2.0] - 2026-06-14

### Added

- **Typed output schema**: Tool return type changed from generic `dict[str, Any]` to
  a typed `ToolResult` envelope. FastMCP now emits a typed `outputSchema` with explicit
  property definitions instead of the generic `additionalProperties: true` form.
- **`clipwright-mcp` console script**: Added `clipwright.server:main` entry point so the
  MCP server can be launched over stdio via `clipwright-mcp` without running Python directly.
- **`to_tool_result(d)` helper**: New `clipwright.envelope.to_tool_result` function converts
  raw dicts (from satellite tools or cross-process calls) to typed `ToolResult` instances
  via `ToolResult.model_validate`.

### Changed

- **Unified `ToolResult` envelope**: `ToolResult` is now a single model that carries both
  success (`ok=True`) and error (`ok=False`) responses. `summary` is now `str | None = None`
  (optional, to support error-only results). `error: ToolError | None = None` field added.
  Using a union (`ToolResult | ToolErrorResult`) was avoided because FastMCP 1.27.2 activates
  `wrap_output=True` for union return types, which wraps `structuredContent` in a `result` key
  and breaks the wire contract.
- **`structuredContent` and `content`** now include all `ToolResult` fields with null/empty
  defaults for absent fields (e.g. `error: null` on success, `summary: null` on error).
  FastMCP 1.27.2 has no API to exclude these fields. This change is additive and does not
  break existing parsers that only read the fields they expect.
- **`Artifact` extra keys ignored**: Added `model_config = ConfigDict(extra="ignore")` to
  `Artifact` so that dicts with additional metadata keys (e.g. from satellite tools) can be
  coerced to `Artifact` without raising `ValidationError` (M-002).

### Removed

- **`ToolErrorResult`**: Removed from `clipwright.schemas`. Success and error envelopes are
  now unified in `ToolResult`. Code that previously imported `ToolErrorResult` must be updated
  to use `ToolResult` with `ok=False`.

<!-- TODO: add compare link once v0.1.1 and v0.2.0 tags are pushed -->
