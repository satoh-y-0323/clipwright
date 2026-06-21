# clipwright-sequence

MCP tool that assembles multiple source media files into a single multi-source OTIO
timeline for concatenation by `clipwright-render`.

## Overview

`clipwright-sequence` accepts an ordered list of clip specifications and emits a single
OTIO timeline file (one V1 video track; A1 audio track left empty for BGM or mixing
by the calling agent). The timeline is consumed unchanged by `clipwright-render`, which
concatenates the clips into a single output video.

This fills the gap between single-source tools and multi-clip programs: every other
clipwright tool starts from one `media` file. `clipwright-sequence` is the authoring
primitive that builds a multi-source program OTIO before the final render pass.

**Design principle** (M3 — separation of annotation and realisation):
`clipwright-sequence` writes only the OTIO; it does not transcode or touch media files.
All media processing is deferred to `clipwright-render`.

## Prerequisites

- Python 3.11 or later
- `clipwright` core package (shared types, envelope, OTIO utils)
- `CLIPWRIGHT_FFPROBE` environment variable (or `ffprobe` on `PATH`) — used to probe
  each source's duration and confirm video stream presence before building the timeline

No FFmpeg is needed at sequence-build time; FFmpeg is only invoked by `clipwright-render`
at realisation time.

## MCP Tool: `clipwright_build_sequence`

### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `clips` | `list[SequenceClip]` | required | Ordered list of clip specifications to assemble. Maximum 1000 entries per call (DC-GP-003). |
| `clips[].media` | `string` | required | Path to the source media file. Must be co-located under the output directory (see Co-location Constraint). |
| `clips[].start_sec` | `float \| null` | `null` → `0.0` | Start of the clip in seconds from the beginning of the source. Must be ≥ 0. |
| `clips[].end_sec` | `float \| null` | `null` → full duration | End of the clip in seconds from the beginning of the source. Must be > 0 and ≤ source duration. |
| `output` | `string` | required | Output OTIO timeline file path (`.otio` extension required). The parent directory must exist. |

### Return value

```json
{
  "ok": true,
  "summary": "Assembled a 3-clip sequence (approx total 45.2s) from 2 source(s). Generated sequence.otio. Pass it to clipwright-render to concatenate into a single video.",
  "data": {
    "clip_count": 3,
    "total_duration_sec": 45.2,
    "unique_source_count": 2
  },
  "artifacts": [{"role": "timeline", "path": "sequence.otio", "format": "otio"}],
  "warnings": []
}
```

> **Note on `total_duration_sec`**: This value is an approximate estimate computed as
> the sum of the input clip ranges (DC-AM-003). The rendered output duration may differ
> slightly after per-frame normalisation in `clipwright-render`.

### Error codes

| Code | Cause |
|------|-------|
| `INVALID_INPUT` | `clips` is empty, exceeds 1000 entries, contains an invalid range (`start_sec >= end_sec`, `end_sec > source duration`), or `output` has a non-`.otio` extension |
| `INVALID_INPUT` | `output` parent directory does not exist |
| `INVALID_INPUT` | A source file has no video stream (audio-only file) |
| `INVALID_INPUT` | A source file's frame rate is undetermined (e.g. still-image stream or unusual capture device) |
| `FILE_NOT_FOUND` | A source media path does not exist |
| `PATH_NOT_ALLOWED` | A source file is located outside the output directory tree (co-location violation) |
| `PATH_NOT_ALLOWED` | `output` path and a source media path resolve to the same file |
| `PROBE_FAILED` | ffprobe could not determine a source's duration (corrupted file) |
| `DEPENDENCY_MISSING` | ffprobe binary not found (`CLIPWRIGHT_FFPROBE` unset and not on `PATH`) |

## Co-location Constraint

All source media files **must be located under the same directory as the output `.otio`
file** (or in a recursive subdirectory of it).

**Why this mirrors `clipwright-render`'s rule — not a relaxation of it:**
`clipwright-render` enforces a `PATH_NOT_ALLOWED` boundary: every source referenced in
an OTIO timeline must be co-located with the timeline file. If `clipwright-sequence`
were to accept sources from outside that boundary, the produced `.otio` would be valid
at build time but would fail immediately when passed to `clipwright-render`.

By enforcing the same co-location rule at sequence-build time, `clipwright-sequence`
guarantees that any `.otio` it produces will round-trip through `clipwright-render`
without a `PATH_NOT_ALLOWED` error. The constraint is a forward-compatibility guarantee,
not an arbitrary restriction.

Recursive subdirectories are permitted: sources may live anywhere inside the tree rooted
at the output's parent directory.

```
project/
  intro.mp4          ← allowed (same directory)
  footage/
    main.mp4         ← allowed (subdirectory)
    broll.mp4        ← allowed (subdirectory)
  sequence.otio      ← output
```

```
/other/path/clip.mp4  ← PATH_NOT_ALLOWED
```

## Symlink Sources

Symlink sources are **not supported** (DC-AS-005). Resolve symlinks to their real paths
before passing them to this tool.

## Two-Phase Workflow

```
clipwright_build_sequence(clips, output)     # Phase 1 — build OTIO
        │
        ▼  OTIO timeline with ordered V1 clips
clipwright_render(timeline, output_media)    # Phase 2 — concatenate and encode
        │
        ▼  single output video (intro + main + outro, or any sequence)
```

`clipwright-sequence` can be combined with other directive tools before the final render:

```
clipwright_build_sequence → clipwright_detect_color → clipwright_reduce_noise → clipwright_render
```

## MCP Client Registration

Register `clipwright-sequence` as a standalone MCP server in your client configuration
(`.mcp.json` / `claude_desktop_config.json`). `CLIPWRIGHT_FFPROBE` is required.

```json
{
  "mcpServers": {
    "clipwright-sequence": {
      "command": "clipwright-sequence",
      "env": {
        "CLIPWRIGHT_FFPROBE": "/path/to/ffprobe"
      }
    }
  }
}
```

`clipwright-render` (which materialises the OTIO into a video) still requires
`CLIPWRIGHT_FFMPEG`.

## Usage Examples

### Assemble intro + main + outro

```python
# Via MCP call_tool
result = await session.call_tool("clipwright_build_sequence", {
    "clips": [
        {"media": "/project/intro.mp4"},
        {"media": "/project/main.mp4", "start_sec": 10.0, "end_sec": 130.0},
        {"media": "/project/outro.mp4"}
    ],
    "output": "/project/sequence.otio"
})
# Then render
render_result = await session.call_tool("clipwright_render", {
    "timeline": "/project/sequence.otio",
    "output": "/project/final.mp4"
})
```

### Splice two gameplay segments

```python
result = await session.call_tool("clipwright_build_sequence", {
    "clips": [
        {"media": "/project/gameplay.mp4", "start_sec": 60.0, "end_sec": 180.0},
        {"media": "/project/gameplay.mp4", "start_sec": 300.0, "end_sec": 420.0}
    ],
    "output": "/project/highlights.otio"
})
```

The same source file may appear multiple times (with different ranges). Each unique
source is probed exactly once.

### B-roll interleave

```python
result = await session.call_tool("clipwright_build_sequence", {
    "clips": [
        {"media": "/project/interview.mp4", "start_sec": 0.0, "end_sec": 30.0},
        {"media": "/project/broll/cityscape.mp4"},
        {"media": "/project/interview.mp4", "start_sec": 30.0, "end_sec": 60.0}
    ],
    "output": "/project/edited.otio"
})
```

## Installation

Within a uv workspace:

```bash
uv run --package clipwright-sequence clipwright-sequence
```

Or install from PyPI:

```bash
pip install clipwright-sequence
clipwright-sequence
```

## License

MIT — See [LICENSE](../LICENSE) for details.
