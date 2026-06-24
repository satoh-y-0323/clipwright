# clipwright-scene

MCP tool for shot boundary detection.

Detects scene cuts in video files using FFmpeg's `scdet` filter (built-in, no extra install)
or PySceneDetect (optional, more accurate). Boundaries are written as zero-duration OTIO
markers on the V1 track of an output timeline.

## MCP Tool

### `clipwright_detect_scenes`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `media` | `string` | yes | Input video file path. |
| `output` | `string` | yes | Output OTIO timeline file path (must end in `.otio`). |
| `options` | `DetectScenesOptions` | no | Detection options (see Options section). |
| `timeline` | `string` | no | Existing OTIO timeline path to augment. When provided, markers are appended to the V1 track instead of creating a new timeline. |

**Return value:** Standard `ToolResult` envelope — `{ ok, summary, data, artifacts, warnings }`.

- `data.scene_count`: Number of scene boundaries detected.
- `data.backend`: Backend used (`"ffmpeg"` or `"pyscenedetect"`).
- `data.total_duration_sec`: Total duration of the media in seconds.
- `artifacts[0]`: The output `.otio` file path (`role: "timeline"`, `format: "otio"`).

## Options

`DetectScenesOptions` fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `threshold` | `float` (0.0–1.0) | `0.3` | Scene change sensitivity. Lower = more sensitive. AI agents should use ~0.5 for major cuts only, ~0.1 for subtle transitions. |
| `min_scene_duration` | `float` (≥ 0.0) | `1.0` | Minimum seconds between boundaries. Closer boundaries are merged (highest confidence kept). Set `0.0` to disable merging. |
| `backend` | `"ffmpeg"` \| `"pyscenedetect"` | `"ffmpeg"` | Detection backend. `"ffmpeg"` uses the built-in `scdet` filter. `"pyscenedetect"` uses the `scenedetect` CLI (requires install). |

## Prerequisites

### FFmpeg (required)

FFmpeg must be available on `PATH` or specified via the `CLIPWRIGHT_FFMPEG` environment variable:

```bash
export CLIPWRIGHT_FFMPEG=/path/to/ffmpeg
```

On Windows with winget:
```bash
winget install Gyan.FFmpeg
```

### PySceneDetect (optional)

Required only when `options.backend = "pyscenedetect"`. Install via pip:

```bash
pip install scenedetect
```

Or install the optional extra:

```bash
pip install "clipwright-scene[pyscenedetect]"
```

Note: PySceneDetect performs a full video decode and is significantly slower than the FFmpeg
backend. It may produce more accurate results for content-based detection. Verified with
PySceneDetect 0.7+.

## Output

The output is an OTIO timeline file containing zero-duration `Marker` objects on the V1 track.
Each marker represents a detected shot boundary (an instantaneous point event, not an interval).

### Marker metadata

Each marker's `metadata["clipwright"]` contains:

| Key | Type | Description |
|-----|------|-------------|
| `kind` | `string` | Always `"scene_boundary"`. |
| `scene_index` | `int` | 0-based sequential index. |
| `confidence` | `float` | 0.0–1.0. FFmpeg: normalized `scdet` score. PySceneDetect: always `1.0`. |
| `backend` | `string` | Backend that produced this boundary (`"ffmpeg"` or `"pyscenedetect"`). |
| `tool` | `string` | Always `"clipwright-scene"`. |
| `version` | `string` | Package version. |

### Augment mode

Provide an existing `.otio` file via the `timeline` parameter to append scene markers to
a previously created timeline (e.g., one produced by `clipwright-transcribe` or
`clipwright-silence`). No clips are added or removed; only markers are appended.
