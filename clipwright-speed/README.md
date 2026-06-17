# clipwright-speed

**AI-facing MCP tool** that applies a `LinearTimeWarp` speed change to clips
in an [OpenTimelineIO](https://opentimelineio.readthedocs.io/) (OTIO) timeline.

## What it does

`clipwright_set_speed` reads an existing OTIO timeline, attaches (or replaces)
a `LinearTimeWarp` effect on the target clip(s) using the given speed multiplier,
and writes the result to a new OTIO file. The input timeline is never modified
(non-destructive). Applying the same speed twice replaces rather than stacks
the warp (idempotent).

## Tool: `clipwright_set_speed`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `timeline` | `string` | yes | Path to the input `.otio` timeline file |
| `output` | `string` | yes | Path for the output `.otio` file (must differ from `timeline`) |
| `options.speed` | `float` | yes | Playback speed multiplier (0.25–8.0). Values > 1.0 speed up, < 1.0 slow down. |
| `options.clip_index` | `int \| null` | no | Zero-based clip index in V1 (gaps excluded). `null` applies to all clips. |

### Speed range

Valid range is `0.25` to `8.0` inclusive. Values outside this range return an
`INVALID_INPUT` error with a hint.

### Idempotency

Applying `clipwright_set_speed` twice with the same speed on the same clip
results in exactly one `LinearTimeWarp` effect — no stacking.

### Non-destructive

The input timeline file is never modified. All changes go to the `output` path.

### Foreign warps

Pre-existing `LinearTimeWarp` effects that were **not** created by
`clipwright-speed` (i.e., lacking `metadata["clipwright"]`) are preserved.

## MCP Registration Example

```json
{
  "mcpServers": {
    "clipwright-speed": {
      "command": "clipwright-speed"
    }
  }
}
```

Or with an explicit Python path:

```json
{
  "mcpServers": {
    "clipwright-speed": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "clipwright_speed.server"]
    }
  }
}
```

## Prerequisites

- Python 3.11+
- [opentimelineio](https://pypi.org/project/opentimelineio/) 0.18+
- [clipwright](https://pypi.org/project/clipwright/) 0.3.0+ (core package)

## License

MIT
