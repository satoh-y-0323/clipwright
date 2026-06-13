# Clipwright

> For Japanese, see [README.ja.md](README.ja.md).

MCP server group wrapping FFmpeg/OTIO. Provides primitives to manipulate video editing workflows from AI agents.

## Prerequisite: FFmpeg

Clipwright requires ffprobe (runtime) and ffmpeg (test fixture generation) on PATH. Binaries are not included.

### Installation (Windows / WinGet)

```bash
winget install Gyan.FFmpeg
```

**PATH takes effect after shell restart.** When using with Claude Code, restart the app for PATH to become active.

If you cannot wait for a restart, specify environment variables directly:

```bash
# runtime: ffprobe only
export CLIPWRIGHT_FFPROBE="C:/Users/<user>/AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-8.1.1-full_build/bin/ffprobe.exe"

# test: both ffmpeg + ffprobe (for test fixture generation)
export CLIPWRIGHT_FFMPEG="C:/Users/<user>/AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-8.1.1-full_build/bin/ffmpeg.exe"
```

### Environment Variable Usage

| Variable | Purpose |
|----------|---------|
| `CLIPWRIGHT_FFPROBE` | **Runtime only**. Used by the `clipwright_inspect_media` tool |
| `CLIPWRIGHT_FFMPEG` | **Test only**. Used by the `sample_media` fixture in `conftest.py` |

> Runtime depends only on ffprobe. ffmpeg is used only for test fixture generation (design: [DC-AM-008]).

---

## Development Environment Setup

```bash
# Install dependencies
uv sync --dev

# Run tests (with coverage)
uv run pytest --cov=clipwright --cov-report=term-missing

# lint / format
uv run ruff check src tests
uv run ruff format src tests

# Type checking
uv run mypy src
```

### Integration Test Prerequisites

To run integration tests (tests that actually invoke ffprobe/ffmpeg), ffmpeg / ffprobe must exist on PATH or the following environment variables must be set:

```bash
# Specify path to ffprobe (used by runtime and integration tests)
export CLIPWRIGHT_FFPROBE="/path/to/ffprobe"

# Specify path to ffmpeg (used for test fixture generation)
export CLIPWRIGHT_FFMPEG="/path/to/ffmpeg"
```

If ffmpeg / ffprobe are already registered in PATH, setting environment variables is not required. If neither is found, integration tests are automatically skipped.

---

## Development Notes: MCP Package

### Adopted Package

**Official MCP Python SDK** (`mcp[cli]`) is adopted (ADR-5 confirmed).

```
mcp[cli]>=1.27.2
```

Importable via `from mcp.server.fastmcp import FastMCP`. Verified to work on Python 3.11 / Windows.

### Annotation Syntax (Adopted Version)

```python
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("clipwright")

@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clipwright_inspect_media(path: str) -> dict:
    """Probe a media file and return its information."""
    ...
```

`ToolAnnotations` fields: `title`, `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`

### outputSchema / structured_output

When `mcp.tool(structured_output=True)` is specified, Pydantic model return values are reflected in outputSchema as JSON Schema.

```python
from pydantic import BaseModel

class MediaResult(BaseModel):
    ok: bool
    summary: str

@mcp.tool(structured_output=True)
def clipwright_inspect_media(path: str) -> MediaResult:
    ...
```

---

## MCP Inspector Communication Procedure

How to manually verify the server using MCP Inspector (`@modelcontextprotocol/inspector`).

### Setup (Node.js Required)

```bash
# Verify Node.js is installed
node --version
npx --version
```

### Starting the Server and Connecting

```bash
# Start MCP Inspector and connect the server via stdio
npx @modelcontextprotocol/inspector uv run python -m clipwright.server
```

Browser opens automatically at `http://localhost:5173` (or access manually).

The tool list (`clipwright_init_project` / `clipwright_inspect_media` / `clipwright_read_timeline` / `clipwright_write_timeline`) appears in Inspector, and you can manually execute each tool.

### Expected Behavior

- 4 tools appear in the tool list
- Passing a non-existent path to `clipwright_inspect_media` returns an error envelope with `ok=false`
- If ffprobe is not set in PATH / environment variables, a `DEPENDENCY_MISSING` error is returned

---

## Architecture Overview

```
src/clipwright/
  __init__.py       # Version definition
  schemas.py        # Shared Pydantic types (contract surface)
  envelope.py       # Return value envelope + error formatting
  errors.py         # Error codes + ClipwrightError exception
  process.py        # Subprocess runner (shell=False / timeout required)
  media.py          # ffprobe wrapper
  otio_utils.py     # OTIO helpers
  operations.py     # Declarative edit operation types + application logic
  project.py        # Project directory management
  server.py         # FastMCP server (4 tools exposed)
```

Dependency direction: `schemas / envelope / errors` (contract surface) → `process / media / otio_utils / project` → `operations` → `server`

For details, see [docs/clipwright-spec.md](docs/clipwright-spec.md).

---

## MCP Client Registration

Each clipwright tool is a standalone MCP server. Register them in your MCP client configuration (`.mcp.json` / `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "clipwright": {
      "command": "clipwright-mcp",
      "env": {
        "CLIPWRIGHT_FFMPEG": "/path/to/ffmpeg",
        "CLIPWRIGHT_FFPROBE": "/path/to/ffprobe"
      }
    },
    "clipwright-render": {
      "command": "clipwright-render",
      "env": {
        "CLIPWRIGHT_FFMPEG": "/path/to/ffmpeg"
      }
    }
  }
}
```

Set `CLIPWRIGHT_FFMPEG` and `CLIPWRIGHT_FFPROBE` environment variables if ffmpeg is not in `PATH`.

---

## License

MIT — See [LICENSE](LICENSE) for details.
