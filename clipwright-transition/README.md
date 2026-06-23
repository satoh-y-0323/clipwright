# clipwright-transition

MCP tool that adds transition directives (fade, dissolve, fadeblack, fadewhite) to an
OTIO timeline for realisation by `clipwright-render`.

## Overview

`clipwright-transition` accepts an OTIO timeline (typically produced by
`clipwright-sequence`) and annotates it with transition directives at clip boundaries.
The directives are stored in `metadata["clipwright"]["transition"]` and consumed by
`clipwright-render`, which materialises them as xfade/acrossfade filter chains during
the final encoding pass.

**Design principle** (separation of annotation and realisation):
`clipwright-transition` writes only the OTIO directive; it does not transcode or touch
media files. All media processing is deferred to `clipwright-render`.

No FFmpeg or FFprobe is needed at transition-annotation time.

## Prerequisites

- Python 3.11 or later
- `clipwright` core package (shared types, envelope, OTIO utils)
- An OTIO timeline with two or more video clips on a single V1 track (e.g. produced by
  `clipwright-sequence`)

## MCP Tool: `clipwright_add_transition`

### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `timeline` | `string` | required | Path to the input OTIO timeline file (`.otio` extension required). |
| `output` | `string` | required | Output OTIO timeline file path (`.otio` extension required, must differ from `timeline`). The parent directory must exist. |
| `options` | `AddTransitionOptions` | required | Transition mode: either `uniform` (apply the same transition to all internal boundaries) or `per_boundary` (specify per-boundary transitions). Exactly one must be provided. |
| `options.uniform` | `TransitionSpec \| null` | `null` | Apply a single transition spec to every internal clip boundary. |
| `options.uniform.type` | `string` | required | Transition type: `"fade"`, `"dissolve"`, `"fadeblack"`, or `"fadewhite"`. |
| `options.uniform.duration_sec` | `float` | required | Transition duration in seconds (0 < duration ≤ 5.0). |
| `options.per_boundary` | `list[BoundaryTransition] \| null` | `null` | Per-boundary transition list (max 1000 entries). All internal boundaries must be covered (no partial/gapped specification in v1). |
| `options.per_boundary[].after_clip_index` | `int` | required | Zero-based index of the clip *before* the boundary (≥ 0). |
| `options.per_boundary[].type` | `string` | required | Transition type (same allowlist as `uniform.type`). |
| `options.per_boundary[].duration_sec` | `float` | required | Transition duration in seconds (same constraint as `uniform.duration_sec`). |

### Return value

```json
{
  "ok": true,
  "summary": "Applied dissolve transition (0.5s) to 2 boundary(ies) [uniform mode]. Generated output.otio. Pass it to clipwright-render to materialise the transitions.",
  "data": {
    "boundary_count": 2,
    "mode": "uniform"
  },
  "artifacts": [{"role": "timeline", "path": "output.otio", "format": "otio"}],
  "warnings": []
}
```

### Error codes

| Code | Cause |
|------|-------|
| `INVALID_INPUT` | `output` has a non-`.otio` extension |
| `INVALID_INPUT` | `output` parent directory does not exist |
| `INVALID_INPUT` | `output` resolves to the same path as `timeline` |
| `INVALID_INPUT` | The timeline has fewer than two video clips |
| `INVALID_INPUT` | The timeline has no video track or two or more video tracks |
| `INVALID_INPUT` | The timeline already contains an OTIO Transition object |
| `INVALID_INPUT` | A `per_boundary` index is out of range `[0, n_clips-2]` |
| `INVALID_INPUT` | `per_boundary` contains duplicate `after_clip_index` values |
| `UNSUPPORTED_OPERATION` | `per_boundary` does not cover all internal boundaries (partial/gapped specification is unsupported in v1; use `uniform` or specify all `n_clips-1` boundaries) |
| `FILE_NOT_FOUND` | The `timeline` path does not exist |

## Three-Phase Workflow

```
clipwright_build_sequence(clips, output)          # Phase 1 — build multi-clip OTIO
        │
        ▼  OTIO timeline with ordered V1 clips
clipwright_add_transition(timeline, output, opts)  # Phase 2 — annotate transitions
        │
        ▼  OTIO timeline with transition directives
clipwright_render(timeline, output_media)          # Phase 3 — encode with transitions
        │
        ▼  single output video with crossfades
```

## MCP Client Registration

Register `clipwright-transition` as a standalone MCP server in your client configuration
(`.mcp.json` / `claude_desktop_config.json`). No environment variables are required at
annotation time.

```json
{
  "mcpServers": {
    "clipwright-transition": {
      "command": "clipwright-transition"
    }
  }
}
```

`clipwright-render` (which materialises the transitions into a video) requires
`CLIPWRIGHT_FFMPEG`.

## Usage Examples

### Apply a uniform dissolve between all clips

```python
# Via MCP call_tool
result = await session.call_tool("clipwright_add_transition", {
    "timeline": "/project/sequence.otio",
    "output": "/project/with_transitions.otio",
    "options": {
        "uniform": {"type": "dissolve", "duration_sec": 0.5}
    }
})
# Then render
render_result = await session.call_tool("clipwright_render", {
    "timeline": "/project/with_transitions.otio",
    "output": "/project/final.mp4"
})
```

### Apply per-boundary transitions

```python
result = await session.call_tool("clipwright_add_transition", {
    "timeline": "/project/sequence.otio",
    "output": "/project/with_transitions.otio",
    "options": {
        "per_boundary": [
            {"after_clip_index": 0, "type": "fade", "duration_sec": 1.0},
            {"after_clip_index": 1, "type": "fadeblack", "duration_sec": 0.5}
        ]
    }
})
```

> **Note**: All internal boundaries must be covered in v1. For a 3-clip timeline
> (`after_clip_index` values `0` and `1` are the two internal boundaries), both must
> be specified. Partial/gapped specification will return `UNSUPPORTED_OPERATION`.

## Non-Destructive Guarantee

`clipwright-transition` never modifies the input `timeline` file. It always writes a
new OTIO file to `output`. Existing `metadata["clipwright"]` keys (from other tools)
are preserved; only the `"transition"` key is added.

## Installation

Within a uv workspace:

```bash
uv run --package clipwright-transition clipwright-transition
```

Or install from PyPI:

```bash
pip install clipwright-transition
clipwright-transition
```

## License

MIT — See [LICENSE](../LICENSE) for details.
