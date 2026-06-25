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
| `track` | Content-aware subject tracking. At annotation time, the tool detects the motion centroid over time and writes a normalised keyframe track (`[{t_s, cx, cy}]`, with `cx`/`cy` in `0..1`); `clipwright-render` materialises it as a **time-varying crop window that follows the subject, then scales** to the target. Keeps the subject in frame for 16:9 → 9:16 vertical Shorts even when it drifts away from centre. Detection runs in a separate process using **numpy**, which is an optional extra (`pip install clipwright-reframe[track]`). When numpy is missing or detection fails, it **falls back to a static centre crop** (a vertical video is always produced) and reports a warning. `anchor` and `pad_color` are not used in this mode. |

## Prerequisites

- Python 3.11 or later
- `clipwright` core package (shared types, envelope, OTIO utils)
- No FFmpeg dependency at annotation time (FFmpeg is only required by `clipwright-render` at realisation time)
- For `mode="track"`: the optional `[track]` extra (numpy), installed in a separate
  detection process. When numpy is not installed, `track` mode still works but degrades
  gracefully to a **static centre crop** (see [Fallback behaviour](#fallback-behaviour)).

## MCP Tool: `clipwright_reframe`

### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `media` | `string` | required | Input video file path (must contain a video stream) |
| `output` | `string` | required | Output OTIO timeline path (`.otio`) |
| `options.target_w` | `integer` | required | Target output width in pixels (even integer, 2–7680) |
| `options.target_h` | `integer` | required | Target output height in pixels (even integer, 2–7680) |
| `options.mode` | `string` | `"pad"` | Fit mode: `crop`, `pad`, `blur_pad`, or `track` |
| `options.anchor` | `string` | `"center"` | Crop/pad alignment anchor. One of: `top-left`, `top`, `top-right`, `left`, `center`, `right`, `bottom-left`, `bottom`, `bottom-right`. Ignored for `blur_pad` and `track`. |
| `options.pad_color` | `string` | `"black"` | Background fill color for `pad` mode. Accepts CSS color names or `#RRGGBB` hex strings. Ignored for `blur_pad` and `track`. |
| `timeline` | `string \| null` | `null` | Existing OTIO timeline path to append the directive to (accumulate pattern). When omitted a new timeline is created. |

When `mode="track"` is selected, the tool runs motion-centroid detection automatically and
writes the resulting keyframe track into the directive — the `track` field is **produced by
the tool**, not a parameter you supply. `anchor` and `pad_color` are not used in this mode.
The `[track]` extra (numpy) must be installed for tracking to take effect; if it is missing,
the tool falls back to a static centre crop (see [Fallback behaviour](#fallback-behaviour)).
The keyframe track is capped at **80 keyframes** (an FFmpeg filter-expression length limit);
the detector decimates the track to fit, and `clipwright-render` materialises the track it
receives as-is.

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

### Fallback behaviour

`mode="track"` is designed to never hard-fail (AI-first robustness). When the `[track]`
extra (numpy) is not installed, or when motion-centroid detection fails for any reason,
the tool does **not** return an error: it writes a **static centre crop** track instead,
returns `ok: true`, and adds a `warning` explaining that tracking was disabled and how to
enable it (install the `[track]` extra). A vertical video is therefore always produced.
A multi-source timeline combined with `track` is also handled gracefully: the track is
ignored and rendering falls back to the existing per-clip cover crop, with a warning.

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

### 16:9 → 9:16 vertical with subject tracking (`mode="track"`)

Keeps a moving subject in the frame as the crop window follows the motion centroid.
Requires the `[track]` extra (numpy); without it the same call still produces a vertical
video via a static centre crop (with a warning).

```python
# Phase 1 — annotate: motion-centroid detection runs automatically
result = await session.call_tool("clipwright_reframe", {
    "media": "source.mp4",
    "output": "tracked.otio",
    "options": {
        "target_w": 1080,
        "target_h": 1920,
        "mode": "track"
        # anchor / pad_color are not used in track mode
    }
})
# Phase 2 — realise: render applies the time-varying (subject-following) crop, then scale
render_result = await session.call_tool("clipwright_render", {
    "timeline": "tracked.otio",
    "output": "vertical_tracked.mp4"
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

To enable `mode="track"` (motion-centroid subject tracking), install the optional
`[track]` extra (pulls in numpy):

```bash
pip install clipwright-reframe[track]
```

If the `[track]` extra is not installed, `mode="track"` still works but degrades to a
**static centre crop** with a warning (see [Fallback behaviour](#fallback-behaviour)).

## License

MIT — See [LICENSE](../LICENSE) for details.
