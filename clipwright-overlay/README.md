# clipwright-overlay

MCP tool that annotates an OTIO timeline with a static image overlay (logo,
watermark, lower-third graphic, end card) for materialisation by `clipwright-render`.

## Overview

`clipwright-overlay` writes an `image_overlay` marker to the first video track (V1)
of an OTIO timeline. The marker stores the image path, position, scale, opacity, and
timing. `clipwright-render` reads the marker and inserts the image as an extra `-i`
input, building an FFmpeg filter chain that composites the image onto the video during
the single render pass.

**Design principle** (separation of annotation and realisation):
`clipwright-overlay` writes only the OTIO; it does not invoke FFmpeg or touch media
files. All image compositing is deferred to `clipwright-render`.

## Prerequisites

- Python 3.11 or later
- `clipwright` core package (shared types, envelope, OTIO utils)
- No FFmpeg is needed at annotation time. FFmpeg is only invoked by `clipwright-render`
  at realisation time.

## MCP Tool: `clipwright_add_overlay`

### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `timeline` | `string` | required | Input OTIO timeline file path (`.otio`). The parent directory is the co-location root for the image file. |
| `output` | `string` | required | Output OTIO timeline file path (`.otio`). Must differ from `timeline`. |
| `image_path` | `string` | required | Path to the overlay image. Must be co-located under the output timeline's parent directory (see Co-location Constraint). Supported formats: `.png`, `.jpg`, `.jpeg`, `.webp`. |
| `start_sec` | `float` | required | Start time in seconds (≥ 0) when the overlay becomes visible. |
| `duration_sec` | `float` | required | Duration in seconds (> 0) that the overlay remains visible. |
| `x` | `string` | `"(W-w)/2"` | Horizontal position expression (FFmpeg overlay filter syntax). `W` = base video width, `w` = overlay width. Default centres the overlay horizontally. |
| `y` | `string` | `"(H-h)/2"` | Vertical position expression. `H` = base video height, `h` = overlay height. Default centres the overlay vertically. |
| `scale` | `float` | `1.0` | Overlay scale factor relative to the original image size. Range: `(0, 8]`. `1.0` = original size. |
| `opacity` | `float` | `1.0` | Opacity of the overlay. Range: `[0.0, 1.0]`. `1.0` = fully opaque. |
| `fade_in_sec` | `float` | `0.3` | Fade-in duration in seconds (≥ 0). `0.0` = no fade-in (overlay appears immediately). |
| `fade_out_sec` | `float` | `0.3` | Fade-out duration in seconds (≥ 0). `0.0` = no fade-out. `fade_in_sec + fade_out_sec` must not exceed `duration_sec`. |

### Return value

```json
{
  "ok": true,
  "summary": "Added image overlay 'logo.png' at 5.0s for 10.0s. Timeline now has 1 image overlay(s). Output: output.otio.",
  "data": {
    "applied": 1,
    "overlay_count": 1,
    "start_sec": 5.0,
    "duration_sec": 10.0
  },
  "artifacts": [{"role": "timeline", "path": "/absolute/path/to/output.otio", "format": "otio"}],
  "warnings": []
}
```

When an identical overlay is submitted again (same parameters), `applied` returns `0`
and a warning is added; no duplicate marker is written (idempotency).

### Error codes (annotation time)

| Code | Cause |
|------|-------|
| `INVALID_INPUT` | `start_sec < 0`, `duration_sec ≤ 0`, `scale ≤ 0` or `scale > 8`, `opacity` outside `[0, 1]`, `fade_in_sec < 0`, `fade_out_sec < 0`, or `fade_in_sec + fade_out_sec > duration_sec` |
| `INVALID_INPUT` | `image_path` extension is not `.png`, `.jpg`, `.jpeg`, or `.webp` |
| `INVALID_INPUT` | `image_path` or `x` / `y` expression contains a control character or a prohibited character (`: ; [ ] , '`) |
| `INVALID_INPUT` | `output` path is identical to `timeline` path |
| `INVALID_INPUT` | Timeline already has 64 `image_overlay` markers (per-timeline limit) |
| `FILE_NOT_FOUND` | `image_path` does not exist |
| `PATH_NOT_ALLOWED` | `image_path` is outside the output timeline's parent directory tree |
| `UNSUPPORTED_OPERATION` | The input timeline has no V1 video track |

### Error codes (render time, raised by `clipwright-render`)

| Code | Cause |
|------|-------|
| `SUBPROCESS_FAILED` | The overlay image could not be decoded by FFmpeg (corrupt file or unsupported encoding). Message shows the image basename only (CWE-209). Hint: `"The overlay image may be corrupt or an unsupported format; provide a valid .png/.jpg/.jpeg/.webp."` |

## Co-location Constraint

The `image_path` **must be located under the same directory as the output `.otio` file**
(or in a recursive subdirectory of it).

**Why this rule exists:**
`clipwright-render` enforces the same co-location boundary for all resources referenced
in an OTIO timeline (sources, subtitles, image overlays). If the image were outside that
boundary, the annotation would succeed but render would immediately fail with
`PATH_NOT_ALLOWED`.

By enforcing co-location at annotation time, `clipwright-overlay` guarantees that any
`.otio` it produces will pass through `clipwright-render` without a `PATH_NOT_ALLOWED`
error.

**Relative-path storage (V2-3 round-trip portability):**
The image path is stored in the OTIO marker as a POSIX relative path from the output
timeline's parent directory (e.g. `images/logo.png`). When `clipwright-render` reads the
marker, it reconstructs the absolute path using the timeline file's parent directory as the
base. This means projects remain portable when the entire directory tree is moved or copied
to another location, as long as the relative positions of the timeline and image files are
preserved.

```
project/
  logo.png           ← allowed (same directory, stored as "logo.png")
  assets/
    watermark.png    ← allowed (subdirectory, stored as "assets/watermark.png")
  output.otio        ← output timeline
```

```
/other/path/logo.png  ← PATH_NOT_ALLOWED
```

## Position Expressions

`x` and `y` accept FFmpeg overlay filter expressions. The following variables are
available at render time:

| Variable | Meaning |
|----------|---------|
| `W` | Base video width in pixels |
| `H` | Base video height in pixels |
| `w` | Overlay image width (after scaling) |
| `h` | Overlay image height (after scaling) |
| `main_w` | Alias for `W` |
| `main_h` | Alias for `H` |
| `overlay_w` | Alias for `w` |
| `overlay_h` | Alias for `h` |

### Common position examples

| Position | `x` | `y` |
|----------|-----|-----|
| Centre | `(W-w)/2` (default) | `(H-h)/2` (default) |
| Top-left (10 px margin) | `10` | `10` |
| Top-right (10 px margin) | `W-w-10` | `10` |
| Bottom-left (10 px margin) | `10` | `H-h-10` |
| Bottom-right (10 px margin) | `W-w-10` | `H-h-10` |
| Bottom-centre | `(W-w)/2` | `H-h-10` |

**Allowed characters in `x` / `y`:** letters, digits, `_`, `(`, `)`, `+`, `-`, `*`,
`/`, `.`, and space. Characters `: ; [ ] , '` and control characters are prohibited to
prevent FFmpeg filtergraph injection.

## `readOnlyHint` Rationale

`clipwright_add_overlay` carries `readOnlyHint=true` in its MCP annotations.

`clipwright-overlay` writes only a new `.otio` file; the input media, the input
timeline, and the image file are never modified. The new-file write is outside the
readOnly scope (consistent with `clipwright-sequence`, `clipwright-trim`,
`clipwright-silence`, and other annotation tools). This signals to AI orchestrators
that the tool is safe for speculative execution and automatic retry without side effects.

## Fade Chain

The opacity and fade effect are implemented in a single filter chain per overlay:

```
[{N}:v]scale=iw*{scale}:-2,format=rgba,colorchannelmixer=aa={opacity},
fade=t=in:st={start}:d={fade_in}:alpha=1,fade=t=out:st={end-fade_out}:d={fade_out}:alpha=1[ov{i}];
{base}[ov{i}]overlay=x='{x}':y='{y}':enable='between(t,{start},{end})'[outvimg{i}]
```

- `scale=iw*{scale}:-2` — scale the image width by `scale`; height is computed
  automatically with even rounding (`-2`) for yuv420p compatibility.
- `format=rgba` — add an alpha channel to the image.
- `colorchannelmixer=aa={opacity}` — set constant opacity (`aa` accepts a constant
  double only; time-varying expressions are not supported by FFmpeg).
- `fade=t=in:...:alpha=1` — ramp the alpha from 0 to 1 over `fade_in_sec`. When
  `fade_in_sec == 0` this segment is omitted entirely (no degenerate `d=0` filter).
- `fade=t=out:...:alpha=1` — ramp the alpha from 1 to 0 over `fade_out_sec`. Omitted
  when `fade_out_sec == 0`.
- The `fade:alpha=1` flag multiplies the existing alpha channel, so the effective
  alpha ramps from `0 → opacity → 0` across the fade windows.
- `overlay=x='{x}':y='{y}':enable='between(t,{start},{end})'` — composite the
  prepared image onto the base video within the time window. `x` / `y` are
  single-quoted (consistent with `enable` and `drawtext`).

This chain is inserted after the `drawtext` filter, so image overlays appear on top
of text overlays.

## Two-Phase Workflow

```
clipwright_add_overlay(timeline, output, image_path, ...)   # Phase 1 — annotate OTIO
        │
        ▼  OTIO timeline with image_overlay marker
clipwright_render(timeline, output_media)                   # Phase 2 — composite and encode
        │
        ▼  video with image/logo composited
```

### Stacking multiple overlays

Multiple calls accumulate overlays on the same timeline:

```python
# Add a channel logo (top-right, always visible)
r1 = await session.call_tool("clipwright_add_overlay", {
    "timeline":      "/project/edit.otio",
    "output":        "/project/with_logo.otio",
    "image_path":    "/project/assets/logo.png",
    "start_sec":     0.0,
    "duration_sec":  120.0,
    "x":             "W-w-20",
    "y":             "20",
    "scale":         0.15,
    "opacity":       0.8,
    "fade_in_sec":   0.0,
    "fade_out_sec":  0.0
})

# Add a lower-third graphic at a specific moment
r2 = await session.call_tool("clipwright_add_overlay", {
    "timeline":      "/project/with_logo.otio",
    "output":        "/project/with_logo_lowerthird.otio",
    "image_path":    "/project/assets/lower_third.png",
    "start_sec":     15.0,
    "duration_sec":  5.0,
    "x":             "(W-w)/2",
    "y":             "H-h-80",
    "scale":         0.6,
    "opacity":       1.0,
    "fade_in_sec":   0.3,
    "fade_out_sec":  0.3
})

# Render
render_result = await session.call_tool("clipwright_render", {
    "timeline": "/project/with_logo_lowerthird.otio",
    "output":   "/project/final.mp4"
})
```

## MCP Client Registration

`clipwright-overlay` does not require FFmpeg at annotation time, so no environment
variables are needed in the MCP server entry. Register it in your MCP client
configuration (`.mcp.json` / `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "clipwright-overlay": {
      "command": "clipwright-overlay"
    }
  }
}
```

`clipwright-render` (which materialises the OTIO into video) still requires
`CLIPWRIGHT_FFMPEG`.

## Installation

Within a uv workspace:

```bash
uv run --package clipwright-overlay clipwright-overlay
```

Or install from PyPI:

```bash
pip install clipwright-overlay
clipwright-overlay
```

## License

MIT — See [LICENSE](../LICENSE) for details.
