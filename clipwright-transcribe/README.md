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

## MCP Tool

`clipwright_transcribe(media, output, options?)` — Transcribe audio/video file and generate `output.otio` / `output.srt` / `output.vtt`.
