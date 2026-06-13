# Changelog

All notable changes to `clipwright-render` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-06-13

### Removed

- Removed argparse CLI from `server.py`; the `clipwright-render` console script now
  starts the MCP server directly over stdio. Pass all arguments via the MCP tool
  interface instead of the command line.

### Added

- Typed output schema: `clipwright_render` tool now returns `ToolResult` (a Pydantic
  model). FastMCP emits a typed `outputSchema` so MCP clients receive schema-validated
  structured output with null/empty defaults on all fields.
- `structuredContent` and `content` now include all `ToolResult` fields (`ok`, `summary`,
  `data`, `artifacts`, `warnings`, `error`) with null/empty defaults.

- `RenderOptions.fit` option (`"contain"` | `"cover"` | `"stretch"`), default `"contain"`.
  Controls how the source frame is fitted into the target resolution when both `width`
  and `height` are specified.
  - `"contain"` (default): preserve aspect ratio and letterbox/pillarbox with black bars.
    No distortion.
  - `"cover"`: preserve aspect ratio, fill the frame, and centre-crop the overflow.
  - `"stretch"`: scale to exactly `width × height`, ignoring aspect ratio.
    Restores the pre-0.2 behaviour when distortion is acceptable.
  - `fit` is silently ignored when `width`/`height` are not both specified.

### Changed

- **Default frame fitting changed from stretch to `contain`.**
  When both `width` and `height` are set, the source is now letterboxed/pillarboxed
  with black bars to preserve aspect ratio. Previously the source was stretched to the
  target resolution, which distorted footage whenever the aspect ratios differed.
  Set `fit="stretch"` to restore the old behaviour.

- **`SubtitleOptions.font_size` and `margin_v` are now interpreted in output pixels.**
  Style dimension fields (`FontSize`, `MarginV`, `Outline`, `MarginL`, `MarginR`) passed
  to `force_style` are counter-scaled by `288 / frame_H` before being handed to ffmpeg,
  so libass's internal `frame_H / PlayResY(288)` upscale cancels out and the values
  render at the requested pixel size in the output frame.
  Previously these values were applied in the ffmpeg/libass `PlayResY=288`
  script-coordinate space, causing them to be multiplied by `frame_H / 288` at render
  time. On tall or aspect-changed output this pushed subtitles far off-screen; on plain
  horizontal output it inflated font sizes and margins beyond intent.
  Existing `font_size`/`margin_v` values tuned against the old 288-based coordinate
  space will appear at a different size and position after this change.

- Single-source renders now round `width`/`height` to the nearest even number to
  satisfy the yuv420p chroma subsampling constraint, consistent with the multi-source
  path. Odd values (e.g. `width=1081`) were previously passed through as-is and could
  cause an ffmpeg encoding failure.

- `RenderOptions.width`/`height` minimum is now 2 (previously any positive integer).
  Values of 1 rounded down to 0 after even-rounding, which broke the ffmpeg scale
  filter and caused a `ZeroDivisionError` in subtitle counter-scaling.

### Fixed

- Burned-in SRT subtitles no longer fly off-screen on tall or vertical outputs.
  The root cause was that ffmpeg converts SRT to ASS with a fixed `PlayResY=288`
  header; libass then scales all script-coordinate dimensions by `frame_H / 288`,
  multiplying a `MarginV=700` into ≈ 4 666 px on a 1920 px-tall frame.
  The counter-scale approach (`script_value = round(px_value × 288 / frame_H)`)
  corrects this for all output heights.
  Note: the `subtitles=...:original_size=` option was evaluated as an alternative fix
  and **not adopted** — on ffmpeg 8.1.1 it does not alter how `force_style`-overridden
  dimension values are interpreted (SSIM identical with and without `original_size`
  across all tested configurations). Counter-scaling the style values is used instead.

### Security

- `RenderOptions` now rejects unknown/unexpected fields (`extra="forbid"`) and
  non-finite numeric values such as `fps=inf` / `fps=nan` (`allow_inf_nan=False`),
  consistent with `SubtitleOptions` and the other clipwright models. This prevents
  typo'd options from being silently ignored and stops `-r inf` from reaching ffmpeg
  (hardening; SR-V-001).

## [0.1.1] - 2026-06-13

### Fixed

- Fixed subprocess output decoding on cp932/UTF-8 Windows environments where child
  process UTF-8 output could raise `UnicodeDecodeError` due to missing `encoding`
  argument in `process.run()`.

### Changed

- Genericized internal docstring references that cited a private report; no
  functional change.

## [0.1.0] - 2026-06-12

### Added

- Initial release of `clipwright-render` as a standalone MCP tool.
- Single-source trimming and remuxing via ffmpeg subprocess.
- Multi-source concatenation (`concat` mode) with `filter_complex`.
- Subtitle burn-in from SRT files using `subtitles` libavfilter.
- Loudness normalization pass (`loudness` option, powered by `clipwright-loudness`
  annotations).
- BGM mix pass (`bgm` option, powered by `clipwright-bgm` annotations).
- Noise suppression pass (`denoise` option, powered by `clipwright-noise` annotations).
- `clipwright_render` MCP tool with `readOnlyHint: false`, `destructiveHint: false`,
  `idempotentHint: true` annotations.

[Unreleased]: https://github.com/satoh-y-0323/clipwright/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/satoh-y-0323/clipwright/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/satoh-y-0323/clipwright/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/satoh-y-0323/clipwright/releases/tag/v0.1.0
