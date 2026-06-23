# Clipwright

> For Japanese, see [README.ja.md](README.ja.md).

MCP server group wrapping FFmpeg/OTIO. Provides primitives to manipulate video editing workflows from AI agents.

## Prerequisite: FFmpeg

Clipwright requires ffprobe (runtime) and ffmpeg (test fixture generation) on PATH. Binaries are not included.

> **`clipwright-stabilize` requires an ffmpeg build compiled with libvidstab (`--enable-libvidstab`).**
> Standard package-manager builds (apt/brew/choco/WinGet) may not include libvidstab.
> If libvidstab is absent, `clipwright_detect_shake` returns `UNSUPPORTED_OPERATION` with
> guidance on installing a libvidstab-enabled build.

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

## Prerequisite: clipwright-transcribe (whisper-cli)

`clipwright-transcribe` requires the **whisper.cpp** binary (`whisper-cli`) and a ggml
model file. They are not installed via pip — obtain them separately.

### whisper-cli Binary

| Platform | How to install |
|---|---|
| **Windows** | Download the pre-built binary from [whisper.cpp Releases](https://github.com/ggerganov/whisper.cpp/releases) → `whisper-bin-x64.zip` (CPU) or `whisper-cublas-*-bin-x64.zip` (CUDA). Extract and place `whisper-cli.exe` in a directory on `PATH`, or set `CLIPWRIGHT_WHISPER`. |
| **macOS** | `brew install whisper-cpp` — installs `whisper-cli` on PATH automatically. |
| **Linux** | Build from source: `git clone https://github.com/ggerganov/whisper.cpp && cd whisper.cpp && cmake -B build && cmake --build build -j --config Release` — binary is at `build/bin/whisper-cli`. |

```bash
# If whisper-cli is not on PATH, set the full path:
export CLIPWRIGHT_WHISPER=/path/to/whisper-cli
```

### ggml Model File

Download a model (e.g. `ggml-base.bin`) from [Hugging Face](https://huggingface.co/ggerganov/whisper.cpp).

```bash
export CLIPWRIGHT_WHISPER_MODEL=/path/to/ggml-base.bin
```

If neither `CLIPWRIGHT_WHISPER` nor a `whisper-cli` binary on PATH is found, the
`clipwright_transcribe` tool returns `DEPENDENCY_MISSING` and integration tests are
automatically skipped.

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

## Available Tools

| Package | MCP Tool | Description |
|---------|----------|-------------|
| `clipwright` (core) | `clipwright_inspect_media` | Probe a media file and return codec / duration / stream info |
| `clipwright` (core) | `clipwright_init_project` | Initialize a project directory with an empty OTIO timeline |
| `clipwright` (core) | `clipwright_read_timeline` | Read an OTIO timeline file and return its structure |
| `clipwright` (core) | `clipwright_write_timeline` | Write an OTIO timeline back to disk |
| `clipwright-silence` | `clipwright_detect_silence` | Detect silent regions in audio via FFmpeg `silencedetect` and annotate OTIO markers |
| `clipwright-loudness` | `clipwright_measure_loudness` | Measure EBU R128 loudness (integrated LUFS / true-peak) via FFmpeg |
| `clipwright-noise` | `clipwright_reduce_noise` | Annotate OTIO timeline with FFmpeg `afftdn` noise-reduction settings |
| `clipwright-transcribe` | `clipwright_transcribe` | Transcribe audio to text via whisper-cli and write word-level OTIO markers. Transparently uses CUDA / Metal whisper.cpp builds (point `CLIPWRIGHT_WHISPER` at a GPU build); `data.backend.device` and `data.realtime_factor` confirm the device and speed at runtime |
| `clipwright-bgm` | `clipwright_place_bgm` | Write BGM placement annotations (volume / fade / ducking) to OTIO timeline |
| `clipwright-render` | `clipwright_render` | Realize OTIO edit operations (trim / concat / filters / LinearTimeWarp speed changes / drawtext overlays) to an output media file via FFmpeg. Re-times `.srt` subtitle cues and `text_overlay` markers to program time when the timeline contains silence cuts or speed warps (`retime_markers="auto"` by default); writes a non-destructive `{output_stem}.retimed.srt` alongside the output. `.vtt`/`.ass` and multi-source timelines are skipped with a warning. Supports hardware-accelerated encode (`hw_encoder`: none/auto/nvenc/amf/qsv/vaapi/videotoolbox) and GPU decode (`hwaccel_decode`). NVENC verified on dev; AMF/QSV/VAAPI/VideoToolbox experimental. |
| `clipwright-speed` | `clipwright_set_speed` | Annotate a clip with a speed multiplier via OTIO `LinearTimeWarp`; materialized by `clipwright-render` |
| `clipwright-text` | `clipwright_add_text` | Annotate an OTIO timeline with text overlay settings (drawtext); rendered to video by `clipwright-render` |
| `clipwright-wrap` | `clipwright_wrap_text` | Wrap long text lines with line-break annotations in OTIO timeline |
| `clipwright-scene` | `clipwright_detect_scenes` | Detect shot boundaries via FFmpeg `scdet` or PySceneDetect (`backend='pyscenedetect'`) and write OTIO markers. When 0 boundaries are found the tool returns a concrete threshold-halving suggestion and, for the ffmpeg backend, recommends switching to pyscenedetect for gradual/low-contrast cuts. Install PySceneDetect with `pip install scenedetect` (or `clipwright-scene[pyscenedetect]`); set `CLIPWRIGHT_SCENEDETECT` to the executable path if not on PATH |
| `clipwright-frames` | `clipwright_extract_frames` | Extract still frames from video at specified times, scene boundaries, or fixed intervals; writes images, OTIO markers, and a JSON manifest |
| `clipwright-color` | `clipwright_detect_color` | Measure average luma via FFmpeg `signalstats` and write an `eq` color-correction directive to OTIO timeline metadata; applied in a single render pass by `clipwright-render` |
| `clipwright-stabilize` | `clipwright_detect_shake` | Analyse camera shake via FFmpeg `vidstabdetect` (requires libvidstab), write a `.trf` motion-analysis file and a stabilize directive to OTIO timeline metadata; applied as `vidstabtransform` in a single render pass by `clipwright-render` |
| `clipwright-trim` | `clipwright_trim` | Build a kept-range OTIO timeline from explicit keep/drop time ranges (or pass through the whole clip); concatenated by `clipwright-render`. The basic "select which parts to keep" primitive |
| `clipwright-reframe` | `clipwright_reframe` | Annotate a reframe directive (target resolution / fit mode / anchor) to OTIO timeline metadata; applied as an FFmpeg filter chain by `clipwright-render`. Three fit modes: `crop` (scale-to-cover + crop), `pad` (scale-to-fit + solid-color letterbox/pillarbox, configurable `pad_color`), `blur_pad` (foreground-over-blurred-background, popular for 16:9 → 9:16 vertical Shorts/Reels). `target_w` / `target_h` must be even (2–7680). `anchor` controls alignment (9-direction, default `center`) |
| `clipwright-sequence` | `clipwright_build_sequence` | Assemble multiple source media files into a single multi-source OTIO timeline (V1 video track) for concatenation by `clipwright-render`. Each clip can specify an optional `start_sec` / `end_sec` sub-range; omitting them uses the full source duration. All sources must be co-located under the output directory (recursive subdirs allowed). Non-destructive: input media is never modified. |
| `clipwright-overlay` | `clipwright_add_overlay` | Annotate an OTIO timeline with an image overlay (PNG/JPEG logo, watermark, lower-third graphic) at a specified position, scale, and opacity for a time range. Supports fade-in/fade-out via FFmpeg `fade:alpha=1` filter chain. The image file must be co-located under the output timeline's parent directory. Rendered to video by `clipwright-render`, which adds the image as an extra `-i` and inserts a `scale/format=rgba/colorchannelmixer/fade/overlay` filter chain into the filtergraph (after `drawtext`, so the image overlay appears on top). Non-destructive: input media and timeline are never modified. |
| `clipwright-transition` | `clipwright_add_transition` | Annotate an OTIO timeline with crossfade / dissolve transitions (FFmpeg `xfade` for video, `acrossfade` for audio) at adjacent clip boundaries. Specify `options.uniform` (a TransitionSpec with type and duration_sec) or `options.per_boundary` (a list of per-boundary specs). Non-destructive: only a new OTIO file is written. Rendered by `clipwright-render`. v1 limitation: partial per-boundary (not covering all clip boundaries) is UNSUPPORTED_OPERATION; use uniform mode or specify all boundaries. |

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
    },
    "clipwright-bgm": {
      "command": "clipwright-bgm"
    },
    "clipwright-scene": {
      "command": "clipwright-scene",
      "env": {
        "CLIPWRIGHT_FFMPEG": "/path/to/ffmpeg",
        "CLIPWRIGHT_FFPROBE": "/path/to/ffprobe"
      }
    },
    "clipwright-frames": {
      "command": "clipwright-frames",
      "env": {
        "CLIPWRIGHT_FFMPEG": "/path/to/ffmpeg",
        "CLIPWRIGHT_FFPROBE": "/path/to/ffprobe"
      }
    },
    "clipwright-speed": {
      "command": "clipwright-speed"
    },
    "clipwright-text": {
      "command": "clipwright-text"
    },
    "clipwright-color": {
      "command": "clipwright-color",
      "env": {
        "CLIPWRIGHT_FFMPEG": "/path/to/ffmpeg",
        "CLIPWRIGHT_FFPROBE": "/path/to/ffprobe"
      }
    },
    "clipwright-stabilize": {
      "command": "clipwright-stabilize",
      "env": {
        "CLIPWRIGHT_FFMPEG": "/path/to/ffmpeg",
        "CLIPWRIGHT_FFPROBE": "/path/to/ffprobe"
      }
    },
    "clipwright-trim": {
      "command": "clipwright-trim",
      "env": {
        "CLIPWRIGHT_FFPROBE": "/path/to/ffprobe"
      }
    },
    "clipwright-reframe": {
      "command": "clipwright-reframe"
    },
    "clipwright-sequence": {
      "command": "clipwright-sequence",
      "env": {
        "CLIPWRIGHT_FFPROBE": "/path/to/ffprobe"
      }
    },
    "clipwright-overlay": {
      "command": "clipwright-overlay"
    },
    "clipwright-transition": {
      "command": "clipwright-transition"
    }
  }
}
```

> Note: `clipwright-transition` does not require `CLIPWRIGHT_FFPROBE` or `CLIPWRIGHT_FFMPEG` (pure OTIO annotation tool).

> Note: `clipwright-scene` requires `CLIPWRIGHT_FFMPEG` for the ffmpeg backend (default). When using `backend='pyscenedetect'`, the `scenedetect` CLI must be installed (`pip install scenedetect`) or its path set via `CLIPWRIGHT_SCENEDETECT`. The optional extra `clipwright-scene[pyscenedetect]` installs PySceneDetect automatically.

> Note: `clipwright-sequence` requires `CLIPWRIGHT_FFPROBE` because `inspect_media` uses ffprobe to probe each source's duration and video stream before building the OTIO timeline.

> Note: `clipwright-overlay` does not require FFmpeg at annotation time (subprocess-free). FFmpeg is only invoked by `clipwright-render` when the overlay is materialised into video.

> Note: `clipwright-frames` lists both `CLIPWRIGHT_FFMPEG` (frame extraction) and `CLIPWRIGHT_FFPROBE` (video-stream detection and duration probing via `inspect_media`), so both variables must be configured.

> Note: `clipwright-color` requires `CLIPWRIGHT_FFPROBE` because `inspect_media` uses ffprobe to validate video stream presence before measuring brightness.

> Note: `clipwright-stabilize` requires `CLIPWRIGHT_FFMPEG` compiled with `--enable-libvidstab`. Both `CLIPWRIGHT_FFMPEG` and `CLIPWRIGHT_FFPROBE` must be set because `inspect_media` validates video stream presence before running vidstabdetect.

Set `CLIPWRIGHT_FFMPEG` and `CLIPWRIGHT_FFPROBE` environment variables if ffmpeg is not in `PATH`.

---

## License

MIT — See [LICENSE](LICENSE) for details.
