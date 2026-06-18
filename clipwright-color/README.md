# clipwright-color

MCP tool for video brightness detection and OTIO timeline color annotation generation.

## Overview

Measures average luma using the ffmpeg `signalstats` filter,
writes a color correction directive (brightness offset, eq parameters) to timeline-level
`metadata["clipwright"]["color"]`.

Performs detection only (OTIO annotation); realization (eq filter application) is done once
by `clipwright-render` (design M3: separation of detection and application).

**Brightness derivation**:

- Samples frames at a configurable interval using `fps=1/<interval>` and `signalstats=stat=brng`.
- Computes mean luma (YAVG) across sampled frames.
- Derives brightness offset as `clamp((target_luma - YAVG) / 255.0, -1.0, 1.0)`.
- Contrast, saturation, and gamma are left at neutral defaults (1.0 / 1.0 / 1.0);
  only brightness is auto-derived from the measured luma.

## Prerequisites

- Python 3.11 or later
- **ffmpeg must exist on PATH or full path set in environment variable `CLIPWRIGHT_FFMPEG`.**

```bash
export CLIPWRIGHT_FFMPEG=/path/to/ffmpeg
export CLIPWRIGHT_FFPROBE=/path/to/ffprobe
```

## MCP Tool

`clipwright_detect_color`

### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `media` | `string` | required | Input video file path (video stream required) |
| `output` | `string` | required | Output OTIO timeline path (`.otio`, same directory as media) |
| `options.target_luma` | `float` | `128.0` | Target average luma on the 0-255 scale (default: mid-grey) |
| `options.sample_interval_sec` | `float` | `1.0` | Frame sampling interval in seconds (ffmpeg fps=1/interval, must be > 0) |
| `timeline` | `string \| null` | `null` | Existing OTIO timeline path (if specified, append color directive to it) |

### Return value

The tool returns a ToolResult envelope:

```json
{
  "ok": true,
  "summary": "Color analysis complete. measured_luma=96.4 ...",
  "data": {
    "measured_luma": 96.4,
    "brightness": 0.123,
    "contrast": 1.0,
    "saturation": 1.0,
    "gamma": 1.0,
    "target_luma": 128.0,
    "sampled_frames": 12
  },
  "artifacts": [{"role": "timeline", "path": "out.otio", "format": "otio"}],
  "warnings": []
}
```

When measurement is not possible (`measured=None`, U-1), the color directive is not written
but the timeline is still saved and a warning is returned.

## Dependencies

| Package | Purpose |
|---------|---------|
| `clipwright` | Shared types, envelope, errors, process.run |
| `mcp[cli]` | MCP server |
| `pydantic` | Parameter validation |

ffmpeg is invoked as a separate process (via PATH or environment variable) for license independence.

## Detection and Render Two-Phase Flow

1. **detect (this tool)**: `ffmpeg -i <media> -vf "fps=1/1,signalstats=stat=brng,metadata=print" -f null -`
   extracts per-frame YAVG and saves the derived eq directive to OTIO annotation.
2. **render (clipwright-render)**: reads `metadata["clipwright"]["color"]["eq"]` and applies
   `eq=brightness=...:contrast=...:saturation=...:gamma=...` in the ffmpeg filter graph.

## Installation and Startup

Within a uv workspace:

```bash
uv run --package clipwright-color clipwright-color
```

Or install directly:

```bash
uv add clipwright-color
clipwright-color
```
