# clipwright-reframe

MCP tool that annotates a reframe directive (target resolution / fit mode / anchor)
to an OTIO timeline for aspect-ratio conversion and delivery-format preparation.

## Overview

`clipwright-reframe` writes a `reframe` directive to
`metadata["clipwright"]["reframe"]` in an OTIO timeline file. The directive is
materialised by `clipwright-render` as an FFmpeg filter chain in a single render
pass (design M3: separation of annotation and realisation).

**Fit modes**:

| Mode | Behaviour |
|------|-----------|
| `crop` | Scale to cover the target rectangle, then crop to size. Content at the edges may be lost; controlled by `anchor`. |
| `pad` | Scale to fit inside the target rectangle (letterbox / pillarbox), then pad the remaining area with `pad_color` (default `"black"`). |
| `blur_pad` | Scale the foreground to fit; overlay it over a blurred, cover-scaled version of the same frame as background. Popular for 16:9 → 9:16 vertical conversions (Shorts / Reels). |

## Prerequisites

- Python 3.11 or later
- `clipwright` core package (shared types, envelope, OTIO utils)
- No FFmpeg dependency at annotation time (FFmpeg is only required by `clipwright-render` at realisation time)

## MCP Tool: `clipwright_reframe`

### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `media` | `string` | required | Input video file path (must contain a video stream) |
| `output` | `string` | required | Output OTIO timeline path (`.otio`) |
| `options.target_w` | `integer` | required | Target output width in pixels (even integer, 2–7680) |
| `options.target_h` | `integer` | required | Target output height in pixels (even integer, 2–7680) |
| `options.mode` | `string` | `"pad"` | Fit mode: `crop`, `pad`, or `blur_pad` |
| `options.anchor` | `string` | `"center"` | Crop/pad alignment anchor. One of: `top-left`, `top`, `top-right`, `left`, `center`, `right`, `bottom-left`, `bottom`, `bottom-right` |
| `options.pad_color` | `string` | `"black"` | Background fill color for `pad` mode. Accepts CSS color names or `#RRGGBB` hex strings. |
| `timeline` | `string \| null` | `null` | Existing OTIO timeline path to append the directive to (accumulate pattern). When omitted a new timeline is created. |

### Return value

```json
{
  "ok": true,
  "summary": "Reframe directive written for video.mp4. target=1080x1920 mode=blur_pad anchor=center.",
  "data": {
    "target_w": 1080,
    "target_h": 1920,
    "mode": "blur_pad",
    "anchor": "center",
    "pad_color": "black"
  },
  "artifacts": [{"role": "timeline", "path": "out.otio", "format": "otio"}],
  "warnings": []
}
```

### Error codes

| Code | Cause |
|------|-------|
| `INVALID_INPUT` | `target_w` / `target_h` is odd, out of range, or `mode` / `anchor` is unrecognised |
| `FILE_NOT_FOUND` | `media` or `timeline` path does not exist |
| `INVALID_INPUT` | `output` and `timeline` point to the same file (use distinct paths) |

## Two-Phase Workflow

```
clipwright_reframe(media, output, options)   # Phase 1 — annotate
        │
        ▼  OTIO timeline with metadata["clipwright"]["reframe"]
clipwright_render(timeline, output_media)    # Phase 2 — realise
        │
        ▼  output video in target resolution/aspect ratio
```

`clipwright-reframe` can be combined with other directive tools in any order before
the final render:

```
clipwright_detect_color  →  clipwright_reduce_noise  →  clipwright_reframe  →  clipwright_render
```

## MCP Client Registration

Register `clipwright-reframe` as a standalone MCP server in your client configuration
(`.mcp.json` / `claude_desktop_config.json`). No FFmpeg environment variables are
required for the annotation step.

```json
{
  "mcpServers": {
    "clipwright-reframe": {
      "command": "clipwright-reframe"
    }
  }
}
```

`clipwright-render` (which materialises the directive) still requires
`CLIPWRIGHT_FFMPEG`.

## Usage Examples

### 16:9 landscape → 9:16 vertical (blur-pad background)

```python
# Via MCP call_tool
result = await session.call_tool("clipwright_reframe", {
    "media": "source.mp4",
    "output": "reframed.otio",
    "options": {
        "target_w": 1080,
        "target_h": 1920,
        "mode": "blur_pad",
        "anchor": "center"
    }
})
# Then render
render_result = await session.call_tool("clipwright_render", {
    "timeline": "reframed.otio",
    "output": "vertical.mp4"
})
```

### Crop to 1:1 square (top-aligned)

```python
result = await session.call_tool("clipwright_reframe", {
    "media": "source.mp4",
    "output": "square.otio",
    "options": {
        "target_w": 1080,
        "target_h": 1080,
        "mode": "crop",
        "anchor": "top"
    }
})
```

### Pad to 4:3 with white bars (accumulate on existing timeline)

```python
result = await session.call_tool("clipwright_reframe", {
    "media": "source.mp4",
    "timeline": "edited.otio",   # existing timeline from other tools
    "output": "edited_reframed.otio",
    "options": {
        "target_w": 1440,
        "target_h": 1080,
        "mode": "pad",
        "anchor": "center",
        "pad_color": "white"
    }
})
```

## Installation

Within a uv workspace:

```bash
uv run --package clipwright-reframe clipwright-reframe
```

Or install from PyPI:

```bash
pip install clipwright-reframe
clipwright-reframe
```

## License

MIT — See [LICENSE](../LICENSE) for details.
