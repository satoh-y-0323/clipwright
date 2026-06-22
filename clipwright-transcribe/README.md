# clipwright-transcribe

MCP tool to transcribe audio/video files and generate SRT/VTT captions and OTIO timeline.

## External Binaries / Files

This tool requires the following external binaries/files to exist in the execution environment. **They are not installed via pip**, so obtain them separately.

### whisper.cpp Binary

Used for transcription.

- Place `whisper-cli` (or the binary name appropriate for your environment) on PATH, or specify the full path in the `CLIPWRIGHT_WHISPER` environment variable.
- Obtain: Build from https://github.com/ggerganov/whisper.cpp, or use release binaries.

```
export CLIPWRIGHT_WHISPER=/path/to/whisper-cli
```

### ggml Model File

Speech recognition model (`.bin` file) used by whisper.cpp.

- Specify the full path to the model file in the `CLIPWRIGHT_WHISPER_MODEL` environment variable. Can be overridden by the `model_path` parameter at tool invocation.
- Obtain: Download from https://huggingface.co/ggerganov/whisper.cpp etc.

```
export CLIPWRIGHT_WHISPER_MODEL=/path/to/ggml-base.bin
```

### ffmpeg

Required to convert audio to 16kHz mono WAV (input format for whisper.cpp).

- Place `ffmpeg` on PATH, or specify the full path in the `CLIPWRIGHT_FFMPEG` environment variable.

```
export CLIPWRIGHT_FFMPEG=/path/to/ffmpeg
```

## Environment Variables Summary

| Environment Variable | Purpose | Required |
|---|---|---|
| `CLIPWRIGHT_WHISPER` | Path to whisper.cpp binary (required if not on PATH) | Conditional |
| `CLIPWRIGHT_WHISPER_MODEL` | Path to ggml model file (`model_path` parameter takes precedence) | Conditional |
| `CLIPWRIGHT_FFMPEG` | Path to ffmpeg binary (required if not on PATH) | Conditional |

## GPU / CUDA Acceleration

`clipwright-transcribe` supports GPU-accelerated transcription transparently: simply point
`CLIPWRIGHT_WHISPER` at a CUDA or Metal build of whisper.cpp — no code or parameter changes
are required.

### Obtaining a CUDA / Metal Binary

| Platform | How to obtain |
|---|---|
| **Windows (CUDA)** | Download `whisper-cublas-*-bin-x64.zip` from [whisper.cpp Releases](https://github.com/ggerganov/whisper.cpp/releases). Extract and set `CLIPWRIGHT_WHISPER` to the full path of `whisper-cli.exe`. |
| **Linux (CUDA)** | Build from source with `-DGGML_CUDA=ON`: `cmake -B build -DGGML_CUDA=ON && cmake --build build -j --config Release`. Binary is at `build/bin/whisper-cli`. |
| **macOS (Metal)** | `brew install whisper-cpp` installs a Metal-accelerated build automatically. |

```bash
# Windows CUDA example
export CLIPWRIGHT_WHISPER=/path/to/whisper-cublas/whisper-cli.exe

# macOS Metal example (after brew install whisper-cpp)
export CLIPWRIGHT_WHISPER=/opt/homebrew/bin/whisper-cli
```

### Confirming GPU / Backend Usage

The tool envelope includes `data.backend` and `data.realtime_factor` so you can verify the
device actually used at runtime:

```json
{
  "data": {
    "backend": {
      "device": "cuda",
      "detail": "CUDA0 (NVIDIA GeForce RTX 4090)"
    },
    "realtime_factor": 0.08,
    "whisper_wall_seconds": 14.2
  }
}
```

- `data.backend.device` — one of `cuda`, `metal`, `cpu`, or `unknown`.
- `data.realtime_factor` — `whisper_wall_seconds / audio_duration_sec`. A value well below
  `1.0` confirms the GPU path is active (CPU builds typically produce values near or above
  `1.0` for large models).
- `data.whisper_wall_seconds` — raw wall-clock seconds spent in the whisper subprocess.

`summary` also reports the backend used (e.g. `"backend: cuda (CUDA0 …)"`), so the
information is visible in the one-line MCP response without unpacking `data`.

### Note on Python GPU Libraries

`clipwright-transcribe` does **not** import `faster-whisper`, CTranslate2, or any CUDA
Python library. Transcription is always invoked as an external subprocess
(`CLIPWRIGHT_WHISPER`), keeping GPU acceleration completely separate from the package
install and preserving license independence. Any whisper-cli-compatible binary — CPU,
CUDA, Metal, ROCm — can be used by updating the environment variable alone.

## MCP Tool

`clipwright_transcribe(media, output, options?)` — Transcribe audio/video file and generate `output.otio` / `output.srt` / `output.vtt`.
