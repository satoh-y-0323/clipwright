# Changelog

All notable changes to `clipwright` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-06-14

### Added

- **Typed output schema**: Tool return type changed from generic `dict[str, Any]` to
  a typed `ToolResult` envelope. FastMCP now emits a typed `outputSchema` with explicit
  property definitions instead of the generic `additionalProperties: true` form.
- **`clipwright-mcp` console script**: Added `clipwright.server:main` entry point so the
  MCP server can be launched over stdio via `clipwright-mcp` without running Python directly.
- **`to_tool_result(d)` helper**: New `clipwright.envelope.to_tool_result` function converts
  raw dicts (from satellite tools or cross-process calls) to typed `ToolResult` instances
  via `ToolResult.model_validate`.

### Changed

- **Unified `ToolResult` envelope**: `ToolResult` is now a single model that carries both
  success (`ok=True`) and error (`ok=False`) responses. `summary` is now `str | None = None`
  (optional, to support error-only results). `error: ToolError | None = None` field added.
  Using a union (`ToolResult | ToolErrorResult`) was avoided because FastMCP 1.27.2 activates
  `wrap_output=True` for union return types, which wraps `structuredContent` in a `result` key
  and breaks the wire contract.
- **`structuredContent` and `content`** now include all `ToolResult` fields with null/empty
  defaults for absent fields (e.g. `error: null` on success, `summary: null` on error).
  FastMCP 1.27.2 has no API to exclude these fields. This change is additive and does not
  break existing parsers that only read the fields they expect.
- **`Artifact` extra keys ignored**: Added `model_config = ConfigDict(extra="ignore")` to
  `Artifact` so that dicts with additional metadata keys (e.g. from satellite tools) can be
  coerced to `Artifact` without raising `ValidationError` (M-002).

### Removed

- **`ToolErrorResult`**: Removed from `clipwright.schemas`. Success and error envelopes are
  now unified in `ToolResult`. Code that previously imported `ToolErrorResult` must be updated
  to use `ToolResult` with `ok=False`.

<!-- TODO: add compare link once v0.1.1 and v0.2.0 tags are pushed -->
