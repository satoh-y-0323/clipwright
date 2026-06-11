# clipwright-bgm

MCP tool to write BGM placement annotations to an OTIO timeline. Records BGM volume, fade, and ducking instructions as metadata on A2 Audio track clips, which clipwright-render realizes as a mix.

## Overview

- **Input**: Timeline OTIO file, BGM audio file, output path, optional parameters (volume, fade, ducking)
- **Process**: OTIO manipulation only (no ffmpeg/external OSS). Adds BGM clip to A2 Audio track and writes clipwright metadata
- **Output**: New OTIO file with BGM annotations (input timeline immutable, M5)

## MCP Tool

`clipwright_add_bgm`

### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `timeline` | `string` | required | Input timeline file path (existing .otio) |
| `bgm` | `string` | required | BGM audio file path (mp3/wav/m4a/aac/flac/ogg/opus/mp4/mkv/mov/webm) |
| `output` | `string` | required | Output OTIO file path (newly generated, different from input) |
| `options` | `object` | `null` | BgmOptions (volume_db / fade_in_sec / fade_out_sec / ducking) |

## Dependencies

| Package | Purpose |
|---------|---------|
| `clipwright` | Shared types, envelope, errors, inspect_media |
| `mcp[cli]` | MCP server |
| `pydantic` | Parameter validation |

## Installation and Startup

```bash
uv add clipwright-bgm
clipwright-bgm
```

Or within a uv workspace:

```bash
uv run --package clipwright-bgm clipwright-bgm
```

## Prerequisites

- Python 3.11 or later
- ffprobe available on PATH or specified via `CLIPWRIGHT_FFPROBE` environment variable (used to determine BGM media length)
