# Clipwright Tool Author Contract (CONVENTIONS)

> **Status: canonical.** This is the official tool author contract derived from `docs/clipwright-spec.md` §6 for contributors (human and AI) adding new tools to the suite.
> Source spec: `docs/clipwright-spec.md` (§2 Design Principles, §6 Contract, §11 Future Directions). When the spec changes, it is the canonical source; this document follows.

## 0. What This Is

- **Audience**: You want to add a new tool to the Clipwright suite (including external contributors).
- **Key Promise**: Follow only the **MUST rules** below—and your tool will land on the suite as a first-class Clipwright tool, callable consistently by AI agents alongside others.
- **Design rationale**: See `clipwright-spec.md` (§2 Design Principles, §6 Contract). This document is the shortest path to "what to follow to land on the suite."
- **Philosophy**: Constraints are kept to the minimal core (MUST). Everything else is "describe accurately" (SHOULD) guidance, not a gate.

---

## 1. Minimal Core (MUST)—These Are Essential

The absolute minimum to ship as a Clipwright suite tool. **Five rules only.**

### M1. Naming
- MCP tool name: `clipwright_<action>` (snake_case, verb-first). Examples: `clipwright_render`, `clipwright_detect_silence`.
- Package / CLI name: `clipwright-<tool>` (all lowercase).
- Consistent prefix so AI agents pick the right tool reliably.

### M2. Response Envelope
- Success: `{ ok: true, summary, data, artifacts, warnings }`.
- Failure: `{ ok: false, error: { code, message, hint } }`.
- `message` = what happened; `hint` = next step (concrete action).
- Use shared library `clipwright.envelope` (`ok_result` / `error_result`) for auto-formatting.
- `error.code` must come from the §3 allow-list (shared `ErrorCode`).

### M3. Detect / Render Split
- detect / inspect tools **do not modify media**. Return results as OTIO annotations, subtitles, or analysis data.
- Media realization (re-encoding, concatenation, etc.) is delegated **exclusively to `clipwright-render`**.
- Do not emit video output from your tool (do not proliferate video file writers).

### M4. Subprocess for External OSS
- External OSS (ffmpeg, whisper, budoux, VAD, etc.) is called as **a separate process**, not imported as a library.
- **Why**: License independence (GPL/LGPL don't propagate) + loose coupling.
- Even OSS that is only a Python library can be wrapped in a **thin CLI shim** (`wrap_cli.py`, `vad_cli.py` as examples). Don't limit your OSS choices.
- This constraint both restricts *how* you use OSS and expands *which* OSS you can use (why FFmpeg is usable).

### M5. Input/Output and Non-Destructive
- Input: receive **existing file paths** (not byte streams).
- Output: generate **new files** (`outputs/` or `artifacts/`), return paths via `artifacts`.
- **Never overwrite source media, OTIO, or inputs.** Reject `output == input`.

> These five are the entire MUST list. No other rule will block your implementation.

---

## 2. Describe Accurately (SHOULD / Guidance, Not a Gate)

Don't enforce values, but **write them truthfully** for AI and peer tools.

- **Attach annotations precisely**: detect/inspect get `readOnlyHint:true` / `destructiveHint:false`; render gets `readOnlyHint:false`.
  - `openWorldHint`: be honest about your tool's scope. Local-only? `false`. Touches network/external API? `true`. **No required value** (see §4).
- **Summary must earn judgment**: 1–2 sentences, with counts, duration, max values. Don't skimp. Dump huge details (full cut lists) to `artifacts` files; AI fetches them if needed.
- **OTIO metadata goes in `metadata["clipwright"]` namespace**. Times use OTIO `opentime` (RationalTime / TimeRange), not float seconds.
- **Subprocess discipline details**: args array (`shell=False`), `timeout` mandatory, stderr collected, exit code checked, failures converted to M2 errors. Core `clipwright.process.run` handles this.
- **Reuse common types**: `MediaRef`, `TimeRange`, `Artifact`, `ToolResult` (from `clipwright.schemas`). Don't redefine per tool.
- **Missing deps are fixable**: If ffmpeg is absent, return `DEPENDENCY_MISSING` with install instructions in `hint`.

---

## 3. Error Codes (ErrorCode Allow-List)

For M2's `error.code`, choose from the shared `ErrorCode` list (core `clipwright.errors.ErrorCode`). **Don't create tool-specific codes; first check if existing codes fit.** (If you must add, contribute it to core for all tools to share.)

| code | Meaning | Primary Use |
|---|---|---|
| `INVALID_INPUT` | Argument validation failed | Extension mismatch, param out of range, malformed data |
| `FILE_NOT_FOUND` | Input path does not exist | Missing source media or OTIO (message: basename only, recommended) |
| `PATH_NOT_ALLOWED` | Path validation failed | Path traversal, outside allowed directory |
| `DEPENDENCY_MISSING` | External tool/dependency not found | ffmpeg/ffprobe/OSS not installed (install instructions in `hint`) |
| `SUBPROCESS_FAILED` | External process exited non-zero | ffmpeg, whisper, etc. failure |
| `SUBPROCESS_TIMEOUT` | External process timed out | Timeout exceeded |
| `PROBE_FAILED` | ffprobe output parse error | Media analysis anomaly |
| `OTIO_ERROR` | OTIO read/write/parse failure | Corrupted or inconsistent timeline.otio |
| `PROJECT_NOT_FOUND` | clipwright.json not found | Project not initialized |
| `PROJECT_EXISTS` | Project exists at init target | Prevent double-init |
| `TRACK_NOT_FOUND` | Track index exceeds count | Out-of-bounds timeline operation |
| `UNSUPPORTED_OPERATION` | Unknown or unimplemented operation | Unknown op in write_timeline, etc. |
| `INTERNAL` | Unexpected internal error | Generic message; stack in stderr/logs only; `hint` says "report with repro steps" |

Operational guidelines (SHOULD):
- **Distinguish input errors**: use `INVALID_INPUT` / `FILE_NOT_FOUND` / `PATH_NOT_ALLOWED` so AI knows whether it's a fixable user mistake or an environment issue.
- Normalize external OSS failures to `SUBPROCESS_FAILED` / `SUBPROCESS_TIMEOUT`; **never dump full stderr in `message`** (prevent leaking secrets/paths).
- **For network tools (§4): currently use existing codes (YAGNI—zero network tools today).** When the first one lands, normalize as:
  - Connection failure / HTTP error / unreachable → `SUBPROCESS_FAILED` (external API calls via CLI shim = subprocess).
  - Timeout → `SUBPROCESS_TIMEOUT`.
  - Auth/API key misconfiguration → `DEPENDENCY_MISSING` (setup steps in `hint`).
  - Add `NETWORK_ERROR` etc. to core `ErrorCode` only when necessity is proven in the first network-tool PR (§4).
- Add to core `ErrorCode` only when existing codes genuinely don't fit.

---

## 4. Network / Online Tools (Explicitly Permitted)

- **Clipwright is not local-only.** You may add tools that use cloud APIs (cloud transcription, online caption translation, etc.).
- In that case, mark **`openWorldHint: true`** honestly (so AI can reason about cost, non-determinism, reachability).
- Additional considerations (SHOULD):
  - Never leak secrets, auth tokens, URLs, or input values in `summary` / `data` / error `message`.
  - Provide `hint` text for timeout and failure cases (retry guidance, offline alternatives if any).
  - Offer offline fallback backends and parameter-switch between them (example: silence's `silencedetect` vs. VAD).
  - **Error codes: use existing §3 codes for now (YAGNI)**. Normalize as: unreachable/HTTP error → `SUBPROCESS_FAILED`, timeout → `SUBPROCESS_TIMEOUT`, auth/API key issues → `DEPENDENCY_MISSING`. Add `NETWORK_ERROR` etc. to core `ErrorCode` only when the first network tool proves necessity (don't preemptively edit `errors.py`).
- **Scope of M4**: M4 is "don't link OSS as libraries." External **API calls** (HTTP) aren't library links, so M4 doesn't apply to network tools. Your network tool needs M1/M2/M3/M5 + `openWorldHint:true`.

---

## 5. How to Create a New Tool (Scaffold)

- **Copy the template**: `templates/clipwright-tool/` is a working skeleton (MUST M1–M5 satisfied). Follow `templates/README.md` substitution steps (`__TOOL__` / `__ACTION__` / `__Action__` replacements → rename files → register in workspace). No cookiecutter; plain copy + string substitution.
- **Reference existing tools**: `clipwright-silence`, `clipwright-transcribe`, `clipwright-wrap` are same-type examples. For thin CLI wrappers around OSS, see `vad_cli.py`, `wrap_cli.py`.
- **Package layout (src/):**
  ```
  clipwright-<tool>/
    pyproject.toml          # license = MIT/Apache-2.0, clipwright as dependency
    src/clipwright_<tool>/
      __init__.py
      <tool>.py             # Orchestration (validate → call OSS → normalize OTIO/subtitles)
      <tool>_cli.py         # (if needed) Thin CLI wrapper around OSS subprocess
      schemas.py            # Tool-specific input Pydantic (reuse clipwright.schemas for common types)
      server.py             # FastMCP @mcp.tool + annotations + stdio startup
    tests/
  ```
- **Validation flow**: `ruff format` / `ruff check` / `mypy` / `pytest`. **Contract layer (schemas, return formatting) near 100%**. Finally, MCP Inspector for basic connectivity.
- **Transport**: stdio default (`mcp.run(transport="stdio")`).


---

## 6. Tool Evaluation (Can AI Solve Real Tasks?) – SHOULD

MCP Inspector connectivity (§5) only confirms the tool starts and responds. **Whether AI can actually use it to solve real problems** is separate—and we recommend lightweight evals for each tool (see spec §11).

- **Evaluation design** (per spec §11):
  - **End-to-end, not unit-level**: Don't test the tool in isolation. Model a real workflow (example: inspect media → detect silence → render to output).
  - **Read-only by default**: Evals don't destroy source media or OTIO (aligned with M5). Outputs go to temp directories.
  - **Require tool composition**: Verify the AI picks the right sequence and chains tools correctly. Single-tool return checks belong in unit tests.
- **Judge mechanically**: Validate that the final output meets expectations using quantities and structure, not free-form AI reasoning. Count output duration, clips, subtitle lines—extract from envelope `summary` / `data` / `artifacts`.
- **Where**: Place evaluation scenarios and lightweight fixtures in the tool repo's `evals/` (or `tests/eval/`). Running in CI is optional.
- **Not a gate**: Evals are quality-improvement guidance, not a landing requirement. Meet §1's MUST, and your tool lands on the suite.

> If eval design grows complex later, split it to an independent `EVAL-GUIDE` (§8 future). For now, this section's essentials suffice (YAGNI).

---

## 7. Pre-PR Self-Check

**MUST (5 items, mandatory)**
- [ ] M1: Tool name `clipwright_<action>` / package `clipwright-<tool>`
- [ ] M2: Success returns `{ok,summary,data,artifacts,warnings}` / failure returns `{ok:false,error:{code,message,hint}}`
- [ ] M3: detect/inspect don't modify media (realization delegated to render)
- [ ] M4: External OSS run as separate processes (no library linking)
- [ ] M5: Input is paths, non-destructive, output newly generated (reject `output==input`)

**SHOULD (recommended)**
- [ ] Annotations match reality (`openWorldHint:true` if network-using)
- [ ] Summary is 1–2 actionable sentences; large details → artifacts files
- [ ] OTIO metadata in `metadata["clipwright"]` namespace, times via `opentime`
- [ ] Subprocess discipline (arg arrays, timeout, stderr, exit code)
- [ ] Reuse common types `MediaRef`/`TimeRange`/`Artifact`/`ToolResult`
- [ ] Pass ruff / mypy / pytest / MCP Inspector
- [ ] (Optional) Provide eval (§6: end-to-end, read-only, real workflow)

---

## 8. References & Future Work

- **Source**: `docs/clipwright-spec.md` §2 (Design Principles) / §4 (OTIO) / §6 (Contract) / §9 (License) / §11 (Future Directions).
- **Template**: `templates/clipwright-tool/` (§5). Copy it for new tools.
- **Decisions settled**:
  - [x] Placement: elevated to root `CONVENTIONS.md` (aligned with spec §12). This is the canonical version.
  - [x] ErrorCode allow-list published → §3 (13 total codes).
  - [x] Network tool error strategy → end of §3 operations, and §4. YAGNI: use `SUBPROCESS_FAILED` / `SUBPROCESS_TIMEOUT` / `DEPENDENCY_MISSING` for now; consider custom codes only when first network tool ships. Don't preemptively edit `errors.py`.
  - [x] eval guidance → §6 as self-contained SHOULD chapter (not a gate). No separate guide yet (YAGNI).
  - [x] Scaffold templating → `templates/clipwright-tool/` (cookiecutter-free; plain copy + string substitution).
- **Open (to be resolved in implementation)**:
  - [ ] If §6 (eval) grows complex, split to independent `EVAL-GUIDE`.
  - [ ] When first network tool ships, decide whether `NETWORK_ERROR` etc. belong in `ErrorCode` (§3/§4).
