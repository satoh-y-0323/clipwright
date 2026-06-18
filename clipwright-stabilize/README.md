# clipwright-stabilize

MCP tool for video shake detection and OTIO timeline stabilize annotation generation.

## Overview

Runs ffmpeg `vidstabdetect` to generate a `.trf` transform file,
estimates shake severity from the binary TRF1 data (best-effort heuristic),
and writes a stabilize directive to timeline-level
`metadata["clipwright"]["stabilize"]`.

Performs detection only (OTIO annotation); realization (vidstabtransform application)
is done once by `clipwright-render` (design M3: separation of detection and application).

**Severity estimation**:

- Reads the binary TRF1 file produced by vidstabdetect.
- Scans all IEEE-754 little-endian doubles, computes mean absolute value.
- Normalises by a pinned heuristic constant (`_NORM_PX = 30.0 px`) to derive a
  severity in `[0.0, 1.0]`.
- Returns `severity=null` when the file cannot be parsed (non-fatal; render does not
  use severity).

## Prerequisites

- Python 3.11 or later
- **ffmpeg compiled with `--enable-libvidstab` must exist on PATH or full path set**
  **in environment variable `CLIPWRIGHT_FFMPEG`.**
  Standard distribution builds (apt, brew, choco) may NOT include libvidstab.
  Use a build that explicitly enables the vidstab filter.

```bash
export CLIPWRIGHT_FFMPEG=/path/to/ffmpeg-with-libvidstab
export CLIPWRIGHT_FFPROBE=/path/to/ffprobe
```

## MCP Tool

`clipwright_detect_shake`

### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `media` | `string` | required | Input video file path (video stream required) |
| `output` | `string` | required | Output OTIO timeline path (`.otio`, same directory as media) |
| `options.shakiness` | `int` | `5` | vidstabdetect shakiness 1-10 (higher = assume more shake) |
| `options.accuracy` | `int` | `15` | vidstabdetect accuracy 1-15 (higher = more accurate / slower) |
| `options.smoothing` | `int` | `30` | vidstabtransform smoothing window in frames 0-1000 |
| `timeline` | `string \| null` | `null` | Existing OTIO timeline path (if specified, append stabilize directive) |

### Return value

The tool returns a ToolResult envelope:

```json
{
  "ok": true,
  "summary": "Shake analysis of video.mp4 complete. severity=0.312, shakiness=5, smoothing=30. Stabilize directive and video.stabilize.trf written; apply with clipwright-render.",
  "data": {
    "severity": 0.312,
    "shakiness": 5,
    "accuracy": 15,
    "smoothing": 30,
    "trf_basename": "video.stabilize.trf"
  },
  "artifacts": [
    {"role": "timeline", "path": "out.otio", "format": "otio"},
    {"role": "analysis", "path": "video.stabilize.trf", "format": "trf"}
  ],
  "warnings": []
}
```

When libvidstab is not compiled into the ffmpeg build, the tool returns
`UNSUPPORTED_OPERATION` with installation guidance (no path or raw stderr exposed).

## Dependencies

| Package | Purpose |
|---------|---------|
| `clipwright` | Shared types, envelope, errors, process.run |
| `mcp[cli]` | MCP server |
| `pydantic` | Parameter validation |

ffmpeg is invoked as a separate process (via PATH or environment variable) for license independence.

## Detection and Render Two-Phase Flow

1. **detect (this tool)**: `ffmpeg -i <media> -vf "vidstabdetect=result=<stem>.stabilize.trf:shakiness=<n>:accuracy=<n>" -f null -`
   generates `.trf` and saves the stabilize directive to OTIO annotation.
2. **render (clipwright-render)**: reads `metadata["clipwright"]["stabilize"]` and applies
   `vidstabtransform=input=<basename>:smoothing=<n>` in the ffmpeg filter graph
   using `cwd=<trf parent directory>` for Windows-safe relative path resolution.

## Installation and Startup

Within a uv workspace:

```bash
uv run --package clipwright-stabilize clipwright-stabilize
```

Or install directly:

```bash
uv add clipwright-stabilize
clipwright-stabilize
```
