# clipwright-reframe

MCP tool for video reframing and OTIO timeline reframe annotation generation.

## Overview

Annotates a reframe directive (target resolution / fit mode / anchor) to
timeline-level `metadata["clipwright"]["reframe"]`.

Performs annotation only (OTIO annotation); realization (ffmpeg filter application)
is done once by `clipwright-render` (design M3: separation of detection and application).

**Fit modes**:

- `crop` — scale to cover, then crop to target aspect ratio (content may be lost)
- `pad` — scale to fit (letterbox / pillarbox), then pad with a solid color
- `blur_pad` — scale foreground to fit, overlay over a blurred background (cover scaled)

## Prerequisites

- Python 3.11 or later

## MCP Tool

`clipwright_reframe`

### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `media` | `string` | required | Input video file path (video stream required) |
| `output` | `string` | required | Output OTIO timeline path (`.otio`) |
| `options.target_w` | `integer` | required | Target output width in pixels (even, 2-7680) |
| `options.target_h` | `integer` | required | Target output height in pixels (even, 2-7680) |
| `options.mode` | `string` | `"pad"` | Fit mode: `crop`, `pad`, or `blur_pad` |
| `options.anchor` | `string` | `"center"` | Crop/pad alignment anchor (9-direction) |
| `options.pad_color` | `string` | `"black"` | Pad background color (CSS name or `#RRGGBB`) |
| `timeline` | `string \| null` | `null` | Existing OTIO timeline path (append directive) |

### Return value

The tool returns a ToolResult envelope:

```json
{
  "ok": true,
  "summary": "Reframe directive written for video.mp4. target=1080x1920 mode=pad anchor=center.",
  "data": {
    "target_w": 1080,
    "target_h": 1920,
    "mode": "pad",
    "anchor": "center",
    "pad_color": "black"
  },
  "artifacts": [{"role": "timeline", "path": "out.otio", "format": "otio"}],
  "warnings": []
}
```

## Dependencies

| Package | Purpose |
|---------|---------|
| `clipwright` | Shared types, envelope, errors, OTIO utils |
| `mcp[cli]` | MCP server |
| `pydantic` | Parameter validation |

## Detection and Render Two-Phase Flow

1. **annotate (this tool)**: writes a `reframe` directive to `metadata["clipwright"]["reframe"]` in the OTIO timeline.
2. **render (`clipwright-render`)**: reads `metadata["clipwright"]["reframe"]` and applies the corresponding ffmpeg filter (`crop` / `pad=...:color=...` / `split→blur→overlay`) in the filter graph.

## Installation and Startup

Within a uv workspace:

```bash
uv run --package clipwright-reframe clipwright-reframe
```

Or install directly:

```bash
uv add clipwright-reframe
clipwright-reframe
```
