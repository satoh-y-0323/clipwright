# clipwright-wrap

MCP tool to format subtitle file text (SRT/VTT) at phrase boundaries using BudouX with line wrapping.

## Overview

`clipwright-wrap` takes SRT/VTT subtitle files as input, splits each cue text into phrase units by BudouX, inserts line breaks to fit within specified character count and line count, and outputs the subtitle file in the same format. A pure text formatting tool with no FFmpeg / Whisper dependencies.

## Input/Output

- **Input**: SRT file (`.srt`) or VTT file (`.vtt`)
- **Output**: Subtitle file in same format as input (with phrase boundary line breaks inserted)
- **Timecodes**: Unchanged (no retiming)

## MCP Tool

`clipwright_wrap_captions`

### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `input` | `string` | required | Input subtitle file path (`.srt` / `.vtt`) |
| `output` | `string` | required | Output subtitle file path (same extension as input) |
| `language` | `string` | `"ja"` | Phrase splitting language (`ja` / `zh-hans` / `zh-hant` / `th`) |
| `max_chars` | `int` | `16` | Max characters per line (full-width and half-width both count as 1 character). Positive integer. |
| `max_lines` | `int` | `2` | Max lines per cue. Over-limit cues recorded in warnings (not truncated). Positive integer. |

### Character Count Specification

`max_chars` is **counted uniformly as 1 character each** (both full-width and half-width as one `len()` character). Full-width normalization is a future extension (requirement §8).

## Phrase Wrapping Mechanism

1. Each cue text (if multiple lines, remove line breaks and concatenate) is split into phrases by BudouX
2. Phrase token sequence is greedily packed into one line within `max_chars`
3. Formatted text (multiple lines separated by `\n`) is written back to cue

If a single phrase exceeds `max_chars` alone, place that phrase on one line (no splitting mid-phrase).

## Supported Languages

Supports the following languages for which BudouX provides phrase splitting:

| `language` Value | Language |
|---|---|
| `ja` | Japanese |
| `zh-hans` | Chinese (Simplified) |
| `zh-hant` | Chinese (Traditional) |
| `th` | Thai |

## Dependencies

| Package | Purpose |
|---------|---------|
| `budoux` | Phrase boundary splitting (standard dependency, lightweight model bundled) |
| `clipwright` | Shared types, envelope, errors |
| `mcp[cli]` | MCP server |
| `pydantic` | Parameter validation |

**No FFmpeg / Whisper dependencies** (pure text formatting). `budoux` is a standard dependency bundled with the package, so e2e tests can run continuously without environment variable gating.

## Installation and Startup

```bash
uv add clipwright-wrap
clipwright-wrap
```

Or within a uv workspace:

```bash
uv run --package clipwright-wrap clipwright-wrap
```

## Prerequisites

- Python 3.11 or later
- FFmpeg not required (text formatting only)
