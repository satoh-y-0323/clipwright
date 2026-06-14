# clipwright-silence

MCP tool for silence detection and OTIO timeline generation. Detects silent intervals in a media file and writes a KEEP-interval timeline (`.otio`) that clipwright-render can use for automated cut editing.

## Overview

- **Input**: Media file (video or audio), output OTIO path, optional detection parameters
- **Process**: Runs the ffmpeg `silencedetect` filter (default) or Silero VAD (opt-in) to find silence intervals, inverts them to KEEP intervals, applies padding and merging, and writes an OTIO timeline
- **Output**: New OTIO file with KEEP intervals; input media is never modified (non-destructive, readOnly)

## MCP Tool

`clipwright_detect_silence`

### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `media` | `string` | required | Input media file path (video or audio) |
| `output` | `string` | required | Output OTIO timeline file path (`.otio` extension) |
| `options` | `object` | `null` | DetectSilenceOptions (see below) |

### DetectSilenceOptions

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `silence_threshold_db` | `float` | `-30.0` | Volume threshold (dB) for silence detection (`silencedetect` backend only; must be ≤ 0) |
| `min_silence_duration` | `float` | `0.5` | Minimum duration (seconds) to classify as silence (`silencedetect` backend only; must be > 0) |
| `padding` | `float` | `0.1` | Padding (seconds) added to both sides of each KEEP interval. Overlapping intervals are merged |
| `min_keep_duration` | `float` | `0.0` | KEEP intervals shorter than this value (seconds) are discarded after padding and merging |
| `backend` | `string` | `"silencedetect"` | Detection backend: `"silencedetect"` (ffmpeg) or `"vad"` (Silero VAD / ONNX) |
| `vad_threshold` | `float` | `0.5` | VAD backend only. Speech probability threshold (0.0–1.0); values ≥ this are speech |
| `vad_min_speech_duration` | `float` | `0.25` | VAD backend only. Minimum speech segment duration (seconds) |
| `vad_min_silence_duration` | `float` | `0.1` | VAD backend only. Minimum silence gap (seconds) between speech intervals |

## Dependencies

| Package | Purpose |
|---------|---------|
| `clipwright` | Shared types, envelope, errors, subprocess runner |
| `mcp[cli]` | MCP server |
| `pydantic` | Parameter validation |
| `onnxruntime` | Silero VAD inference (VAD backend) |

## Installation and Startup

```bash
uv add clipwright-silence
clipwright-silence
```

Or within a uv workspace:

```bash
uv run --package clipwright-silence clipwright-silence
```

## Prerequisites

- Python 3.11 or later
- ffmpeg and ffprobe available on PATH, or set via environment variables:
  - `CLIPWRIGHT_FFMPEG` — path to the ffmpeg binary
  - `CLIPWRIGHT_FFPROBE` — path to the ffprobe binary
- For the `vad` backend: `onnxruntime` is bundled as a dependency; no additional binary is required
