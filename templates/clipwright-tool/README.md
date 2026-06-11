# clipwright-__TOOL__

(TODO: Describe in one sentence what this tool does. Example: MCP tool that detects ~ and returns OTIO/JSON annotation.)

## Overview

(TODO: Overview of input, processing, output. Clarify if detect/inspect type or render type.)

## MCP Tool

`clipwright___ACTION__`

### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `input` | `string` | required | Input file path (existing file) |
| `output` | `string` | required | Output artifact path (newly generated, different from input) |
| `example_threshold` | `float` | `0.5` | (TODO: Replace with actual parameters) |

## Dependencies

| Package | Purpose |
|---------|---------|
| `clipwright` | Shared types, envelope, errors |
| `mcp[cli]` | MCP server |
| `pydantic` | Parameter validation |

(If wrapping external OSS via subprocess, add here and clarify PATH prerequisites and installation steps in README.)

## Installation and Startup

```bash
uv add clipwright-__TOOL__
clipwright-__TOOL__
```

Or within a uv workspace:

```bash
uv run --package clipwright-__TOOL__ clipwright-__TOOL__
```

## Prerequisites

- Python 3.11 or later
- (If external OSS required, clarify PATH prerequisites here)
