# clipwright-export

MCP tools that export OTIO timelines to NLE exchange formats (EDL / FCPXML)
and chapter sidecar files (YouTube text / ffmetadata), so AI rough cuts can
hand off to human editors and video publishing workflows.

## Overview

`clipwright-export` reads an existing OTIO timeline (read-only) and writes a
new sidecar file — an NLE exchange format or a chapter sidecar. It never
modifies the source OTIO or referenced media.

## MCP Tools

- `clipwright_export_timeline` — export an OTIO timeline to EDL or FCPXML for
  import into an NLE (Premiere / Resolve / Final Cut Pro).
- `clipwright_export_chapters` — export `clipwright` markers (e.g.
  `scene_boundary` from `clipwright-scene`) to a YouTube chapter list or an
  ffmpeg `ffmetadata` chapter file.

(Full parameter tables and usage details are added when the tool modules are
implemented.)

## Dependencies

| Package | Purpose |
|---------|---------|
| `clipwright` | Shared types, envelope, errors |
| `mcp[cli]` | MCP server |
| `pydantic` | Parameter validation |
| `opentimelineio` | OTIO timeline read |
| `otio-cmx3600-adapter` | EDL exchange format adapter |
| `otio-fcpx-xml-adapter` | FCPXML exchange format adapter |

## Installation and Startup

```bash
uv add clipwright-export
clipwright-export
```

Or within a uv workspace:

```bash
uv run --package clipwright-export clipwright-export
```

## Prerequisites

- Python 3.11 or later

## MCP Client Registration

Register this tool in your MCP client configuration (`.mcp.json` /
`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "clipwright-export": {
      "command": "clipwright-export",
      "env": {}
    }
  }
}
```
