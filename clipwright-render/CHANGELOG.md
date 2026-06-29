# Changelog

All notable changes to `clipwright-render` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.16.0] - 2026-06-30

### Added

- **Karaoke burn-in mode**: `SubtitleOptions` gains four new fields:
  `karaoke: bool = False`, `highlight_color: str | None = None` (default
  `#FFFF00` — yellow), `chars_per_line: int = 42`, `max_lines: int = 2`.
  When `karaoke=true`, `clipwright_render` parses the word-level WebVTT
  produced by `clipwright_transcribe(word_timestamps=true)`, groups words into
  lines via a greedy char-budget algorithm, generates ASS `\k<cs>` tags (cs =
  1/100 s karaoke duration, computed as accumulated boundary differences for
  drift-free totals), and burns the result into the output video via the
  existing `subtitles` / libass filter path.
- **`highlight_color`** maps to the ASS `PrimaryColour` (the karaoke highlight);
  `font_color` remains the `SecondaryColour` (pre-highlight text). Both accept
  `#RRGGBB` and are converted to ASS `&HBBGGRR` internally.
- **ASS injection guard (SEC-04)**: word text is escaped (`\`, `{`, `}`) before
  `\k` tag generation to prevent ASS injection.
- **CWE-400 guards**: the VTT parser rejects input with more than 50 000 words
  or 10 000 cues (`INVALID_INPUT`, hint includes the limit); guards are applied
  at parse time (before any ASS is generated) to prevent OOM.
- **`pix_fmt=yuv420p` maintained** in karaoke mode (F-R-06; no chroma
  renegotiation from the ASS path).

### Changed

- `karaoke=false` (default): the existing subtitle burn-in path (SRT / VTT / ASS
  via `subtitles` filter) is byte-for-byte identical to v0.15.0. No behavioural
  change to any existing render call.

## [0.14.0] - 2026-06-27

### Changed

- **Path policy unified (ADR-PP-1)**: `clipwright_render` now accepts absolute
  references to existing real files for source media, subtitle, and image overlay
  inputs regardless of whether they are located inside the OTIO timeline directory.
  Previously all three input types were required to reside under the same directory
  as the `.otio` file. The new policy is:
  - Absolute ref to an existing regular file with no symlink components → allowed
    (ADR-PP-2: symlink check runs before `resolve()` to prevent CWE-59 bypass).
  - Relative ref → must still resolve within the timeline directory tree (CWE-22
    guard unchanged).
  - Absolute ref to a non-existent path → rejected.
  - Absolute ref through a symlink component → rejected (PATH_NOT_ALLOWED).
- Boundary/symlink validation now delegates to
  `clipwright.pathpolicy.check_media_ref` (requires `clipwright>=0.4.0`), which
  centralises the policy across all tools.
- The old per-type boundary helpers (`_check_source_within_timeline_dir`,
  `_check_subtitle_within_timeline_dir`, `_check_image_overlay_within_timeline_dir`)
  are retained for backward compatibility with tests that import them directly.

## [0.11.1] - 2026-06-25

### Fixed

- Output chroma is now always pinned to `yuv420p` (4:2:0) by passing `-pix_fmt yuv420p`
  once at the encoder input. Previously, transition (xfade) outputs could negotiate to
  `yuvj444p` / H.264 High 4:4:4 Predictive and fail to play in common players (e.g.
  Windows "Movies & TV"). The fix covers all output paths — single-source, multi-source,
  concat, transition, subtitle, overlay, reframe, scale, BGM — for both software
  (libx264) and hardware (NVENC) encoders. Resolution, codec type, and output duration
  are unchanged; color range is not converted.

## [0.11.0] - 2026-06-24

### Added

- Transition materialisation: `clipwright_render` now reads `transition` markers written
  by `clipwright-transition` and realises them as FFmpeg `xfade` (video) and `acrossfade`
  (audio) effects during the single transcode pass.
- `xfade` is applied between adjacent video segments using `filter_complex`; duration and
  effect name are read from the marker metadata (`clipwright.duration`,
  `clipwright.effect`).
- `acrossfade` is applied to the corresponding audio streams in sync with the video
  transition so that audio and video fades remain aligned.
- Unknown or missing effect names fall back to `"fade"` (the xfade default) and a warning
  is appended to `ToolResult.warnings`.

## [0.10.0] - 2026-06-22

### Added

- Image overlay materialisation: `clipwright_render` now reads `image_overlay` markers
  written by `clipwright-overlay` and composites the referenced image onto the video
  using an FFmpeg `overlay` filter chain during the single transcode pass.
- The overlay image is added as an extra `-i` input after the BGM audio input, giving it
  a stable stream index that does not shift when BGM, loudness, or other optional inputs
  are absent.
- Filter chain per overlay: `scale=iw*{scale}:-2` (even-rounded height for yuv420p
  compatibility) → `format=rgba` → `colorchannelmixer=aa={opacity}` (constant opacity)
  → optional `fade=t=in:...:alpha=1` → optional `fade=t=out:...:alpha=1` →
  `overlay=x='{x}':y='{y}':enable='between(t,{start},{end})'`.
- Static image inputs use `-loop 1 -t {total_duration}` before the `-i` flag so that
  FFmpeg generates a video stream with the required duration; without this flag, `fade`
  filters with `st > 0` have no frames to ramp and the overlay disappears silently.
- `scale=iw*{scale}:-2` uses the `-2` even-rounding shorthand, consistent with the
  subtitle scaling logic already present in the render pipeline.
- Co-location boundary is re-validated at render time by reconstructing the absolute path
  from the stored POSIX-relative image path and the timeline's parent directory, then
  confirming it lies within that directory (CWE-22 path-traversal guard).
- Corrupt or undecodable overlay images are detected via a magic-byte pre-check before
  the FFmpeg subprocess is started; a detected bad file returns `SUBPROCESS_FAILED` with
  the image **basename only** in the message to avoid leaking absolute paths (CWE-209).

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
