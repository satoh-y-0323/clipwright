# clipwright-render

MCP tool to realize OTIO timelines with FFmpeg.

Clipwright is a toolkit centered on "separation of detection (detect) and application (render)". detect-type tools only return annotations to OTIO without modifying media, and **this single `clipwright-render` tool performs all realization in one pass** (completes segment extraction, concatenation, and trimming in a single transcode pass).

---

## Prerequisites

This tool targets materials and timelines that meet the following conditions. Inputs outside these conditions return errors.

| Condition | Details |
|-----------|---------|
| Frame rate | CFR (constant frame rate) only. VFR (variable frame rate) not supported |
| Resolution | Fixed resolution only. Materials with per-frame resolution changes not supported |
| Source count | Only single source (1 file) in timeline |
| Video track | Required. No video not supported |
| Audio track | 0 or 1 stream only. If multiple, first audio stream adopted |

### Out of Scope (Planned for Future)

- VFR / resolution-changing materials
- Multiple source file concatenation
- Subtitle burn-in
- Transitions
- 2+ video tracks in timeline

---

## FFmpeg Setup

**FFmpeg / FFprobe are not bundled with this package**. Install in your environment.

```bash
# macOS (Homebrew)
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg
```

If `ffmpeg` / `ffprobe` are on PATH, it works as-is. In environments where PATH cannot be modified, explicitly specify paths with environment variables.

```bash
export CLIPWRIGHT_FFMPEG=/usr/local/bin/ffmpeg
export CLIPWRIGHT_FFPROBE=/usr/local/bin/ffprobe
```

> About license: This wrapper package itself is **MIT** licensed. Since FFmpeg binaries are not bundled, FFmpeg's LGPL / GPL redistribution obligations do not apply to this wrapper. Verify FFmpeg's own license (LGPL v2.1 / GPL v2) in your environment.

---

## Installation

```bash
uv sync
```

---

## Usage

### MCP Tool (`clipwright_render`)

Invoked from Claude / agents via MCP.

```jsonc
{
  "tool": "clipwright_render",
  "arguments": {
    "timeline": "/path/to/timeline.otio",
    "output": "/path/to/output.mp4",
    "dry_run": false,
    "options": {
      "video_codec": "libx264",
      "audio_codec": "aac",
      "width": 1920,
      "height": 1080,
      "fps": 29.97,
      "crf": 23,
      "overwrite": false
    }
  }
}
```

**Arguments**

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `timeline` | string | yes | Input OTIO file path |
| `output` | string | yes | Output file path (`.mp4` / `.mkv` / `.mov` / `.webm`) |
| `dry_run` | bool | optional (default `false`) | If `true`, returns plan without actual rendering |
| `options` | object | optional | Output options (see RenderOptions below) |

**RenderOptions**

| Field | Type | Description |
|-------|------|-------------|
| `video_codec` | string \| null | Video codec (e.g. `libx264`, default: inherit from source) |
| `audio_codec` | string \| null | Audio codec (e.g. `aac`, default: inherit from source) |
| `width` | int \| null | Output width (must be set with `height`) |
| `height` | int \| null | Output height (must be set with `width`) |
| `fps` | float \| null | Output frame rate |
| `crf` | int \| null | Quality CRF value (0-51) |
| `overwrite` | bool | If `true`, overwrite existing output file (default `false`) |

`width` / `height` must both be specified or both `null`. Specifying only one is an error.

**Return Value (Success)**

```jsonc
{
  "ok": true,
  "summary": "2 clips → 45.2 sec / 42.1 MB / outputs/out.mp4",
  "data": {
    "output_path": "/path/to/output.mp4",
    "duration_sec": 45.2,
    "size_bytes": 44150784,
    "clip_count": 2
  },
  "artifacts": ["/path/to/output.mp4"],
  "warnings": []
}
```

**Return Value (dry_run)**

```jsonc
{
  "ok": true,
  "summary": "dry_run: 2 segments / estimated 45.2 sec / approx 42.1 MB",
  "data": {
    "dry_run": true,
    "clip_count": 2,
    "estimated_duration_sec": 45.2,
    "estimated_size_bytes": 44150784,
    "ffmpeg_args": ["ffmpeg", "-i", "source.mp4", "-filter_complex", "..."]
  },
  "artifacts": [],
  "warnings": []
}
```

`estimated_size_bytes` is calculated from source bitrate obtained by FFprobe and output duration. If bitrate cannot be obtained, it is `null` with reason in `warnings`. If any of `video_codec` / `width` / `height` / `fps` / `crf` are specified, estimation based on source bitrate may differ significantly from actual, so `warnings` includes a note.

**Return Value (Error)**

```jsonc
{
  "ok": false,
  "error": {
    "code": "FILE_NOT_FOUND",
    "message": "Timeline file not found: /path/to/timeline.otio",
    "hint": "Verify the file path"
  }
}
```

Main error codes:

| Code | Meaning |
|------|---------|
| `FILE_NOT_FOUND` | Timeline / source / output directory does not exist |
| `INVALID_INPUT` | Invalid extension / existing output with overwrite=false / empty timeline |
| `PATH_NOT_ALLOWED` | Output path is same as input source |
| `UNSUPPORTED_OPERATION` | No video / multiple sources / Transition / 2+ video tracks |
| `PROBE_FAILED` | FFprobe analysis failed |
| `SUBPROCESS_FAILED` | FFmpeg exit code non-zero |
| `SUBPROCESS_TIMEOUT` | FFmpeg timeout (`max(300, duration_sec × 10)` seconds) |
| `DEPENDENCY_MISSING` | ffmpeg / ffprobe not found in PATH or environment variables |

---

### CLI (`clipwright-render`)

Can be run directly from command line. Shares same logic as MCP tool.

```bash
clipwright-render <timeline> <output> [options]
```

**Arguments**

```
clipwright-render <timeline> <output>
    [--dry-run]
    [--video-codec C]
    [--audio-codec C]
    [--width W --height H]
    [--fps F]
    [--crf N]
    [--overwrite]
```

**Example: Verify plan with dry_run before rendering**

```bash
# Verify plan first
clipwright-render timeline.otio out.mp4 --dry-run

# Render if OK
clipwright-render timeline.otio out.mp4 --video-codec libx264 --crf 23
```

**Example: Render with specified resolution**

```bash
clipwright-render timeline.otio out.mp4 --width 1280 --height 720 --fps 29.97
```

---

## Testing

### Unit Tests (FFmpeg Not Required)

```bash
uv run --package clipwright-render pytest clipwright-render/tests/ -m "not integration"
```

### Integration Tests (FFmpeg Required)

Tests that verify single-source concatenation and output using actual FFmpeg. Automatically skipped in environments without FFmpeg.

Set environment variables before running:

```bash
export CLIPWRIGHT_FFMPEG=/path/to/ffmpeg
export CLIPWRIGHT_FFPROBE=/path/to/ffprobe

uv run --package clipwright-render pytest clipwright-render/tests/ -m integration
```

> Integration tests skip if `CLIPWRIGHT_FFMPEG` / `CLIPWRIGHT_FFPROBE` are not set. Set these variables when running in CI.

---

## License

This wrapper package itself is **MIT** licensed.

Since FFmpeg binaries are not bundled, FFmpeg's LGPL v2.1 / GPL v2 redistribution obligations do not apply to this package.
