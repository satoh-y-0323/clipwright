# Changelog

All notable changes to `clipwright` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`clipwright-render` caption & overlay re-timing (v0.7.0)**: `clipwright_render`
  now re-times burned-in captions and text overlays from source-media time onto the
  post-edit program timeline. When the timeline contains silence cuts or
  `LinearTimeWarp` speed changes, subtitle cues (`.srt`) and `text_overlay` markers
  no longer land at the wrong frames. Key behaviours:
  - `retime_markers` option: `"auto"` (default) — re-time whenever the timeline
    contains cuts or warps; `"off"` — skip re-timing unconditionally (legacy behaviour).
  - **Non-destructive subtitle output**: when cues are re-timed a new file
    `{output_stem}.retimed.srt` is written alongside the rendered video; the original
    `.srt` is never modified.
  - **Identity timelines** (no cuts, no warps, single clip at 1× speed) produce no
    `.retimed.srt` and add no processing overhead.
  - **Cut-spanning cues/overlays** are split at cut boundaries; cues/overlays that
    fall entirely inside a removed range are dropped with a `warnings[]` entry.
  - **Format support**: `.srt` only. `.vtt` and `.ass` are skipped with a
    `warnings[]` entry (not yet supported).
  - **Multi-source timelines** (more than one distinct source file) are skipped with
    a `warnings[]` entry.
  - Fully backward compatible: existing render calls without subtitle options behave
    identically.

## [0.9.0] - 2026-06-20

### Added

- **`clipwright-trim` package (v0.1.0)**: New MCP tool `clipwright_trim` that builds a
  kept-range OTIO timeline from explicit time ranges. Specify `keep` ranges (segments to
  retain, in listed order) or `drop` ranges (segments to remove; the complement is kept);
  with no options it passes the whole clip through as a single renderable clip. Output is the
  same kept-range shape produced by `clipwright-silence`, so `clipwright-render` concatenates
  the segments with no changes. This fills the most basic editing gap — selecting which parts
  of a clip to keep — which previously had no in-suite path. Non-destructive: only a new OTIO
  file is written; the source media is never modified. Requires `CLIPWRIGHT_FFPROBE` to read
  the source duration.

## [0.8.0] - 2026-06-18

### Added

- **`clipwright-stabilize` package (v0.1.0)**: New MCP tool `clipwright_detect_shake` that
  analyses camera shake in a video file using FFmpeg `vidstabdetect` (requires an ffmpeg build
  compiled with `--enable-libvidstab`). Generates a binary `.trf` motion-analysis file alongside
  the output OTIO timeline. A `StabilizeDirective` is written to
  `metadata["clipwright"]["stabilize"]` recording `trf_path`, `shakiness`, `accuracy`,
  `smoothing`, and best-effort `severity` (0.0–1.0, `null` when the binary `.trf` cannot be
  parsed). The annotation is non-destructive; the `vidstabtransform` filter pass is materialized
  in a single render pass by `clipwright-render`. If libvidstab is absent, the tool returns
  `UNSUPPORTED_OPERATION` with installation guidance.
- **`clipwright-render` stabilize support (v0.6.0)**: `clipwright_render` now realizes
  stabilization annotations written by `clipwright_detect_shake`. The `vidstabtransform` filter
  is injected immediately after the `trim` stage and before `setpts` for each clip, ensuring
  stabilization is applied to source frames before any timing adjustments (speed changes, etc.).
  The `.trf` file is resolved via `cwd + relative basename` to work around vid.stab's inability
  to parse Windows absolute paths in filtergraph strings. Fully backward compatible: timelines
  without a `stabilize` directive render identically to before.

## [0.7.0] - 2026-06-18

### Added

- **`clipwright-color` package (v0.1.0)**: New MCP tool `clipwright_detect_color` that measures
  average luma (brightness) in a video file using FFmpeg `signalstats` and writes an `eq`
  color-correction directive to `metadata["clipwright"]["color"]` in an OTIO timeline. The
  directive specifies a derived `brightness` offset (`(target_luma - measured_luma) / 255`,
  clamped to `[-1, 1]`) alongside neutral `contrast`, `saturation`, and `gamma` values.
  The annotation is non-destructive; the `eq` filter pass is materialized in a single render
  pass by `clipwright-render`.
- **`clipwright-render` color eq support (v0.5.0)**: `clipwright_render` now realizes color
  correction annotations written by `clipwright_detect_color`. The `eq` filter is injected
  after the scale stage and before any subtitle/drawtext burn-in, applying brightness, contrast,
  saturation, and gamma adjustments in a single FFmpeg pass. Fully backward compatible: timelines
  without a `color` directive render identically to before.

## [0.6.0] - 2026-06-18

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
