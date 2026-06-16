# clipwright-frames

MCP tool for still-frame extraction from video into images, OTIO markers, and a JSON manifest.

Extracts still frames from a video file using FFmpeg and writes:
- Image files (JPEG or PNG) to the specified output directory
- An OTIO timeline (`frames.otio`) with one zero-duration `Marker` per frame
- A JSON manifest (`frames.json`) listing each frame's path and timestamp

## MCP Server Setup

Add `clipwright-frames` to your MCP client configuration:

```json
{
  "mcpServers": {
    "clipwright-frames": {
      "command": "clipwright-frames",
      "args": []
    }
  }
}
```

Or using `uvx` without a global install:

```json
{
  "mcpServers": {
    "clipwright-frames": {
      "command": "uvx",
      "args": ["--from", "clipwright-frames", "clipwright-frames"]
    }
  }
}
```

## MCP Tool

### `clipwright_extract_frames`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `media` | `string` | yes | Input video file path. |
| `output_dir` | `string` | yes | Existing output directory where frames and artifacts are written. |
| `options` | `ExtractFramesOptions` | no | Extraction options (mode, format, quality, etc.). |

**Return value:** Standard `ToolResult` envelope — `{ ok, summary, data, artifacts, warnings }`.

- `data.frame_count`: Number of frames extracted.
- `data.mode`: Extraction mode used (`"interval"`, `"scene"`, or `"timestamps"`).
- `data.total_duration_sec`: Total duration of the media in seconds.
- `artifacts[0]`: The output OTIO timeline path (`role: "timeline"`, `format: "otio"`).
- `artifacts[1]`: The JSON manifest path (`role: "manifest"`, `format: "json"`).

## Extraction Modes

### interval (default)

Extracts one frame every `interval_sec` seconds throughout the video.

```json
{
  "mode": "interval",
  "interval_sec": 10.0
}
```

### scene

Extracts frames at scene boundaries detected by `clipwright-scene`.
Requires an OTIO timeline produced by `clipwright_detect_scenes`.

```json
{
  "mode": "scene",
  "scene_timeline": "/path/to/scenes.otio"
}
```

### timestamps

Extracts frames at explicit timestamps (in seconds).

```json
{
  "mode": "timestamps",
  "timestamps": [0.0, 5.5, 12.3, 30.0]
}
```

## Options

`ExtractFramesOptions` fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | `"interval"` \| `"scene"` \| `"timestamps"` | `"interval"` | Extraction mode. |
| `interval_sec` | `float` (> 0) | `10.0` | Seconds between frames when `mode="interval"`. |
| `scene_timeline` | `string` \| `null` | `null` | OTIO timeline path. Required when `mode="scene"`. |
| `timestamps` | `list[float]` | `[]` | Explicit timestamps in seconds when `mode="timestamps"`. |
| `format` | `"jpeg"` \| `"png"` | `"jpeg"` | Output image format. |
| `quality` | `int` (1–31) | `2` | FFmpeg `-q:v` quality for JPEG (1=best, 31=worst). Ignored for PNG. |
| `max_width` | `int` \| `null` | `null` | Maximum output width in pixels. Aspect ratio is preserved. `null` means no resizing. |

## Output Contract

All output is written to `output_dir`. The tool never modifies the input media file.

### OTIO timeline (`frames.otio`)

An OTIO timeline with one zero-duration `Marker` per extracted frame on the V1 track.

Each marker's `metadata["clipwright"]` contains:

| Key | Type | Description |
|-----|------|-------------|
| `kind` | `string` | Always `"frame"`. |
| `frame_index` | `int` | 0-based sequential index. |
| `path` | `string` | Absolute path to the extracted image file. |
| `tool` | `string` | Always `"clipwright-frames"`. |
| `version` | `string` | Package version. |

### JSON manifest (`frames.json`)

A JSON array where each element describes one extracted frame:

```json
[
  {
    "frame_index": 0,
    "timestamp_sec": 0.0,
    "path": "/output/frame_0000.jpg"
  }
]
```

## Prerequisites

### FFmpeg (required)

FFmpeg must be available on `PATH` or specified via the `CLIPWRIGHT_FFMPEG` environment variable:

```bash
export CLIPWRIGHT_FFMPEG=/path/to/ffmpeg
export CLIPWRIGHT_FFPROBE=/path/to/ffprobe
```

On Windows with winget:

```bash
winget install Gyan.FFmpeg
```

FFmpeg is used to seek to timestamps and extract frames. It is invoked as a subprocess
and is never linked as a library.

> Note: `clipwright-frames` also requires ffprobe (via `inspect_media`) for video-stream detection and duration probing, so `CLIPWRIGHT_FFPROBE` must be configured as well.

## License

MIT
