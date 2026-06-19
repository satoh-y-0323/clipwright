# clipwright-trim

MCP tool for explicit keep/drop range selection and OTIO timeline generation. Accepts user-defined time ranges in seconds and produces a kept-range OTIO timeline that clipwright-render can use for precise, non-destructive video editing.

## Overview

- **Input**: Media file (video or audio), output OTIO path, optional keep/drop range specification
- **Process**: Validates ranges against media duration (via ffprobe), applies padding, builds a kept-range OTIO timeline using the same structure as clipwright-silence output
- **Output**: New OTIO file with KEEP intervals; input media is never modified (non-destructive, readOnly)

## MCP Tool

`clipwright_trim`

### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `media` | `string` | required | Input media file path (video or audio) |
| `output` | `string` | required | Output OTIO timeline file path (`.otio` extension) |
| `options` | `object` | `null` | TrimOptions (see below) |

### TrimOptions

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `keep` | `array[TrimRange]` | `[]` | Ranges to retain, in enumeration order. Mutually exclusive with `drop`. |
| `drop` | `array[TrimRange]` | `[]` | Ranges to remove; complement becomes the kept region. Mutually exclusive with `keep`. |
| `padding_sec` | `float` | `0.0` | Non-negative padding (seconds) applied to each range boundary. In keep mode, expands outward; in drop mode, shrinks inward. |

### TrimRange

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `start_sec` | `float` | `>= 0` | Range start time in seconds from media start |
| `end_sec` | `float` | `> start_sec` | Range end time in seconds from media start |

## Return Value

```json
{
  "ok": true,
  "summary": "Kept 2 clip(s) totalling 45.0 s out of 120.0 s source duration (mode: keep).",
  "data": {
    "clip_count": 2,
    "kept_duration_sec": 45.0,
    "source_duration_sec": 120.0,
    "mode": "keep"
  },
  "artifacts": [
    {"role": "timeline", "path": "/path/to/output.otio", "format": "otio"}
  ],
  "warnings": []
}
```

## Requirements

- Python 3.11+
- ffprobe (path resolved via `CLIPWRIGHT_FFPROBE` environment variable or `PATH`)
- ffmpeg is **not** required (this tool produces OTIO only; rendering is `clipwright-render`'s responsibility)

## Usage with MCP

Register in your MCP client configuration:

```json
{
  "mcpServers": {
    "clipwright-trim": {
      "command": "clipwright-trim",
      "env": {
        "CLIPWRIGHT_FFPROBE": "/path/to/ffprobe"
      }
    }
  }
}
```

## License

MIT
