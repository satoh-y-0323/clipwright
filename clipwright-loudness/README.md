# clipwright-loudness

MCP tool for audio loudness normalization detection and OTIO timeline annotation generation.

## Overview

Measures audio loudness and peak level using ffmpeg `loudnorm` / `volumedetect` filters,
writes loudness instructions (mode, target, measured) to timeline-level `metadata["clipwright"]["loudness"]`.

Performs detection only (OTIO annotation); realization (ffmpeg filter application) is done once by `clipwright-render`
(design M3: separation of detection and application).

**Normalization modes**:
- `loudnorm` (EBU R128 LUFS): Two-stage linear method. detect measures with `loudnorm print_format=json` and saves
  `measured_*` parameters to OTIO annotation; render applies exact one-pass with `loudnorm:linear=true`.
- `peak` (max dB match): Measures max_volume with `volumedetect` and applies gain difference in render.

**Initial render support**:
- `track` scope only (single loudness normalization applied to entire timeline).
- `per_clip` scope (individual clip application) deferred until after compositing.

## Prerequisites

- Python 3.11 or later
- **ffmpeg / ffprobe must exist on PATH or full paths set in environment variables `CLIPWRIGHT_FFMPEG` / `CLIPWRIGHT_FFPROBE`.**

Add ffmpeg to PATH directly or specify via environment variables:

```bash
export CLIPWRIGHT_FFMPEG=/path/to/ffmpeg
export CLIPWRIGHT_FFPROBE=/path/to/ffprobe
```

## MCP Tool

`clipwright_detect_loudness`

### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `media` | `string` | required | Input media file path (audio required) |
| `output` | `string` | required | Output OTIO timeline path (`.otio`, same directory as media) |
| `options.mode` | `"loudnorm" \| "peak"` | `"loudnorm"` | Normalization mode |
| `options.target_i` | `float` | `-14.0` | loudnorm mode: target integrated loudness (LUFS, -70 to -5) |
| `options.target_tp` | `float` | `-1.0` | loudnorm mode: target true peak (dBTP, -9 to 0) |
| `options.target_lra` | `float` | `11.0` | loudnorm mode: target loudness range (LU, 1 to 50) |
| `options.target_peak_db` | `float` | `-1.0` | peak mode: target peak level (dB, -60 to 0) |
| `timeline` | `string \| null` | `null` | Existing OTIO timeline path (if specified, append to it) |

## Dependencies

| Package | Purpose |
|---------|---------|
| `clipwright` | Shared types, envelope, errors, process.run |
| `mcp[cli]` | MCP server |
| `pydantic` | Parameter validation |

ffmpeg / ffprobe are invoked as separate processes (via PATH or environment variables) for license independence.

## loudnorm Linear Two-Stage Method

1. **detect (this tool)**: `ffmpeg -i <media> -af loudnorm=I=-14:TP=-1:LRA=11:print_format=json -f null -`
   gets measured_* parameters and saves them to OTIO annotation.
2. **render (clipwright-render)**: `loudnorm=I=-14:TP=-1:LRA=11:measured_I=..:...:linear=true`
   executes only one-pass linear application with detected parameters (improved accuracy).

## Installation and Startup

Within a uv workspace:

```bash
uv run --package clipwright-loudness clipwright-loudness
```

Or install directly:

```bash
uv add clipwright-loudness
clipwright-loudness
```
