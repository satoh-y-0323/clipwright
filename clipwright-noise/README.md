# clipwright-noise

MCP tool for noise detection and OTIO timeline annotation generation.

## Overview

Measures audio noise floor using ffmpeg `astats` filter,
writes denoise instructions (backend, parameters) to timeline-level `metadata["clipwright"]["denoise"]`.

Performs detection only (OTIO annotation); realization (ffmpeg filter application) is done once by `clipwright-render`
(design M3: separation of detection and application).

**Initial render support**:
- `afftdn` backend: render application supported (`clipwright-render` injects afftdn filter).
- `deepfilternet` backend: annotation only. render application not yet supported (planned in future version).

## Prerequisites

- Python 3.11 or later
- **ffmpeg / ffprobe must exist on PATH or full paths set in environment variables `CLIPWRIGHT_FFMPEG` / `CLIPWRIGHT_FFPROBE`.**

Add ffmpeg to PATH directly or specify via environment variables:

```bash
export CLIPWRIGHT_FFMPEG=/path/to/ffmpeg
export CLIPWRIGHT_FFPROBE=/path/to/ffprobe
```

## MCP Tool

`clipwright_detect_noise`

### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `media` | `string` | required | Input media file path (video + audio required) |
| `output` | `string` | required | Output OTIO timeline path (`.otio`, same directory as media) |
| `options.backend` | `"afftdn" \| "deepfilternet"` | `"afftdn"` | denoise backend |
| `options.strength` | `"light" \| "medium" \| "strong"` | `"medium"` | afftdn nr mapping (light=6/medium=12/strong=24 dB) |
| `timeline` | `string \| null` | `null` | Existing OTIO timeline path (if specified, append to it) |

## Dependencies

| Package | Purpose |
|---------|---------|
| `clipwright` | Shared types, envelope, errors, process.run |
| `mcp[cli]` | MCP server |
| `pydantic` | Parameter validation |

ffmpeg / ffprobe are invoked as separate processes (via PATH or environment variables) for license independence.
DeepFilterNet binary is not bundled in initial version; render-side dependency planned.

## Installation and Startup

Within a uv workspace:

```bash
uv run --package clipwright-noise clipwright-noise
```

Or install directly:

```bash
uv add clipwright-noise
clipwright-noise
```
