# clipwright-text

MCP tool for annotating an OTIO timeline with text overlay markers.
Text is not rendered here — `clipwright-render` reads the markers and applies
`drawtext` filters when producing the output video.

## Overview

`clipwright-text` is part of the [clipwright](https://github.com/satoh-y-0323/clipwright)
suite. It is designed for **AI agents**, not humans — there is no GUI or
interactive CLI. All interaction is via the MCP (Model Context Protocol) `stdio`
transport.

## Available Tools

| Tool | Description |
|------|-------------|
| `clipwright_add_text` | Append a `text_overlay` marker to an OTIO timeline for later rendering. |

## How It Works

1. AI calls `clipwright_add_text(timeline, output, options)` once per text overlay.
2. The tool appends a `text_overlay` marker (name `text_0`, `text_1`, …) to the
   first video track of the timeline and writes a new `.otio` file to `output`.
3. The input timeline is never modified (non-destructive).
4. Repeated calls with identical options are idempotent — the second call returns
   `applied=0` with a warning instead of duplicating the marker.
5. After annotating, pass the output OTIO to `clipwright-render` which converts
   the markers into `drawtext` ffmpeg filters and bakes the text into the video.

## MCP Client Registration

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "clipwright-text": {
      "command": "clipwright-text",
      "args": []
    }
  }
}
```

If `clipwright-text` is not on `PATH`, use the full path to the script or the
Python interpreter:

```json
{
  "mcpServers": {
    "clipwright-text": {
      "command": "/path/to/.venv/bin/clipwright-text",
      "args": []
    }
  }
}
```

## `clipwright_add_text` Reference

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `timeline` | `str` | Yes | Path to the input `.otio` timeline file. |
| `output` | `str` | Yes | Path for the new `.otio` output (must end in `.otio`, must differ from `timeline`). |
| `options` | `AddTextOptions` | Yes | Text overlay options (see below). |

### `AddTextOptions` Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `text` | `str` | — | Text to display. Single-line; no newlines or control characters. |
| `start_sec` | `float` | — | Start time in seconds (>= 0). |
| `duration_sec` | `float` | — | Duration in seconds (> 0). |
| `x` | `str` | `"(w-tw)/2"` | Horizontal position (ffmpeg drawtext expression). |
| `y` | `str` | `"h-th-40"` | Vertical position (ffmpeg drawtext expression). |
| `font_size` | `int` | `48` | Font size in points (> 0). |
| `font_color` | `str` | `"white"` | Font color: named color, `#RRGGBB`, or `name@alpha`. |
| `box` | `bool` | `False` | Draw a background box behind the text. |
| `box_color` | `str` | `"black@0.5"` | Background box color. |
| `fade_in_sec` | `float` | `0.3` | Fade-in duration (>= 0; `fade_in + fade_out <= duration`). |
| `fade_out_sec` | `float` | `0.3` | Fade-out duration (>= 0). |
| `font_path` | `str \| None` | `None` | Absolute path to a `.ttf`/`.otf` font file. `None` lets `clipwright-render` resolve a platform default. |

### Return Value

```json
{
  "ok": true,
  "summary": "Added text overlay \"Hello\" at 1.0s for 3.0s. Timeline now has 1 text overlay(s). Output: out.otio.",
  "data": {
    "applied": 1,
    "overlay_count": 1,
    "start_sec": 1.0,
    "duration_sec": 3.0
  },
  "artifacts": [
    { "role": "timeline", "path": "/abs/path/out.otio", "format": "otio" }
  ],
  "warnings": []
}
```

On error:

```json
{
  "ok": false,
  "error": {
    "code": "INVALID_INPUT",
    "message": "...",
    "hint": "..."
  }
}
```

## Requirements

- Python >= 3.11
- [opentimelineio](https://github.com/AcademySoftwareFoundation/OpenTimelineIO) >= 0.18
- [mcp](https://github.com/modelcontextprotocol/python-sdk) >= 1.27.2
- [clipwright](https://pypi.org/project/clipwright/) >= 0.3.0 (core library)

## License

MIT
