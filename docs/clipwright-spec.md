# Clipwright—Design, Requirements & Contract Spec

> A suite of single-purpose, loosely-coupled video editing tools (MCP) designed to be operated by AI agents, not humans at a GUI.
> This document captures the design principles and contract for implementing the **core (clipwright)** and **clipwright-render** first, before adding detector tools.

---

## 0. How to Read This Document

- **§1 (Project Overview) & §2 (Design Principles)** are the fixed foundation. Do not revisit these.
- **§6 (Contract) is critical**. Every tool (core, render, future) must follow it.
- Scope for now: **core + clipwright-render only**. Detector tools (silence, transcribe, etc.) follow once proven on the contract.
- Identifiers, code, and schemas are in English; descriptions in English (following spec conventions).

---

## 1. Project Overview

**Clipwright** is a suite of video editing tools designed with **AI agents as operators**, not human GUI users.

In one sentence: "A craftsperson's toolkit (clipwright) containing single-purpose tools (clipwright-render, clipwright-silence, clipwright-transcribe, etc.) in a unified namespace." Each tool does one thing well, Unix-style.

### Position
- **Tooling, not platforming.** Not building a unified UI or a billing backend. Goal: produce small single-purpose tools called by AI, bound by a shared contract.
- **Thin wrappers, thick adapters.** Core logic (transcription, silence detection, encoding) lives in battle-tested, actively-maintained OSS (FFmpeg, whisper.cpp/faster-whisper, auto-editor, etc.). Clipwright provides the contract and interfaces—it normalizes OSS output to OTIO/SRT, not reimplements.
- **AI assembles edits through a backbone.** The common IR is the industry standard **OpenTimelineIO (OTIO)**—the lingua franca for all tools.

### Namespaces
- `clipwright` – Core (shared library + primitive MCP)
- `clipwright-render` – Bakes OTIO to video (second tool, implemented first)
- `clipwright-transcribe` / `clipwright-silence` / `clipwright-noise` – Future detectors (added following the contract)

---

## 2. Design Principles (Fixed)

1. **AI-first, GUI-free.** Only inputs (media + instructions) and outputs (results) exist. No timeline UI for humans.
2. **Single responsibility.** One tool = one function. No "everything server."
3. **Thin protocol, thick adapter.** MCP surface is minimal. The real work is the adapter layer—normalizing each OSS's native output to OTIO. Recognize that this layer cannot be thin.
4. **Subprocess loose coupling.** External OSS (ffmpeg, whisper, auto-editor, etc.) is **always a separate process**—never linked as a library. Reason: (a) license independence, (b) loose coupling philosophy.
5. **Detect / render split.** Detection tools don't modify media; they return annotations (markers, cut suggestions) in OTIO. Only `clipwright-render` materializes, once, in one pass. This keeps agent loops cheap, fast, and non-destructive.
6. **Path-based media exchange.** Never pass media byte streams between tools via AI context. Input is a path; output is a path + metadata. Heavy data stays on local disk.
7. **Context: sufficient, not minimal.** Results are short summaries (human/AI-readable) + structured data + paths to full artifacts. Provide what's needed to decide, not less. AI reads summaries first; details on demand.
8. **Portable formats.** Subtitles are SRT/VTT/ASS. Edits are OTIO. Don't invent proprietary formats.

---

## 3. Architecture

### 3.1 Overview

```
AI Agent (Claude Code, etc.)
        │  (MCP / stdio)
        ▼
┌────────────────────────────────────────────┐
│ Clipwright Tool Suite (each is stdio MCP)  │
│                                             │
│  clipwright (core)     – project/timeline  │
│                          media inspect +   │
│                          primitives        │
│  clipwright-render     – OTIO → video      │
│                          (FFmpeg)          │
│  clipwright-silence    – detect silence → │ (future)
│                          OTIO markers      │
│  clipwright-transcribe – transcribe →     │ (future)
│                          captions + OTIO  │
└────────────────────────────────────────────┘
        │ Shared contract
        ▼
  Shared IR: OTIO timeline (project "backbone")
  Shared disk: clipwright project directory
        │ Each tool invokes OSS as subprocess
        ▼
  External OSS: ffmpeg, ffprobe, whisper.cpp, auto-editor, …
```

### 3.2 Component Roles

- **clipwright (core)** has two faces:
  - **Shared library** (imported by tools): OTIO read/write, media probe, response envelope, error formatting, subprocess runner, project management utilities and types. Tools build on this foundation.
  - **Primitive MCP server**: Exposes basic operations—project init, media inspect, timeline read/write—to AI agents.
- **clipwright-render** is a standalone MCP/CLI. Takes OTIO timeline, materializes with FFmpeg in one (minimal) pass. The only "destructive" tool (creates new media files).

### 3.3 State Model (Project Structure)

MCP calls are stateless by nature, so state lives **on disk in the project directory**. OTIO timeline is the "backbone"; all tools read/write it and coordinate.

```
<project_dir>/
  clipwright.json          # Manifest (version, creation info, settings)
  timeline.otio            # Backbone. All edits decisions centralized here.
  sources/                 # Source media (or path references; copy optional)
  artifacts/               # Intermediate artifacts (captions, analysis, etc.)
    captions.srt
    silence.json
  outputs/                 # render's final outputs
    final.mp4
```

- Tools read `timeline.otio`, add annotations (markers/clips), write back.
- Source media is **referenced**, not contained (OTIO design supports this).

---

## 4. Intermediate Representation (OTIO) Contract

OTIO is the common language. No proprietary edit formats.

### 4.1 Adoption Policy
- Library: `opentimelineio` (PyPI, Apache-2.0, official Python bindings, 0.18 series).
- One OTIO file per timeline: `timeline.otio`.
- Export uses OTIO's adapter mechanism (FCPXML, CMX3600 EDL, etc. are future options).

### 4.2 Expressing "Keep" / "Discard"
- Detection tools don't modify media; they return results as **OTIO annotations**.
- Recommended forms:
  - Cut suggestions or keep-ranges: list as **clips / gaps** in the timeline, OR
  - Detection events (silence, filler, scene boundaries, etc.): place as **markers** at the time, store metadata in `marker.metadata["clipwright"]` (type, confidence, etc.).
- Fix the choice per tool type and enforce it as contract (example: silence generates "keep-range clips"; transcribe generates "subtitle markers + external SRT").

### 4.3 Metadata Namespace
- All Clipwright-written metadata goes under `metadata["clipwright"]`. Avoids collisions with other tools and formats.
  ```json
  { "clipwright": { "tool": "clipwright-silence", "version": "0.1.0", "kind": "keep", "confidence": 0.92 } }
  ```

### 4.4 Time Representation
- Use OTIO's `opentime` (RationalTime / TimeRange). Never use float seconds. Prevents frame precision loss and framerate mismatch errors.

---

## 5. Core (clipwright) Requirements

Core is the foundation for all other tools. Once solid, detectors scale under the same contract.

### 5.1 Shared Library Exports
- **OTIO helpers**: Create/read/save timelines, add clips/gaps/markers, read/write `metadata["clipwright"]`.
- **Media probe**: Call `ffprobe` as subprocess, return **structured** resolution, duration, fps, codecs, audio tracks, etc.
- **Response envelope**: Common formatter for all tool responses (→ §6.3).
- **Error formatting**: Helper to create actionable error messages (→ §6.4).
- **Subprocess runner**: Safe, consistent external CLI execution (arg array, shell=False, timeout, stderr collection, exit code check).
- **Project management**: Init, detect, read/write project directories and manifests.
- **Common types (schemas)**: Pydantic definitions for `MediaRef`, `TimeRange`, `Artifact`, `ToolResult`, shared by all tools.

### 5.2 Primitive MCP Server (Minimal)
- `clipwright_init_project` – Create and initialize project directory.
- `clipwright_inspect_media` – Return structured metadata for a media file (AI sees media properties before render).
- `clipwright_read_timeline` – Return summary of `timeline.otio` (clip count, duration, marker list, path; not full dump).
- `clipwright_write_timeline` – Apply edit operations to timeline, save (or validate only).

> Note: Core MCP is basic operations only. Media realization is render's job.

### 5.3 Non-Functional Requirements
- If external dependencies (ffmpeg, ffprobe) are missing, return **explicit, actionable errors on startup or first call** ("ffmpeg not found in PATH. Install with `brew install ffmpeg`, etc.").
- Annotate all public tools per §6.2.
- Standardize responses to §6.3 envelope.

---

## 6. Contract ★ Critical

The covenant all tools (core, render, future) must follow. Aim: someone adding a tool needs to read only this section.

### 6.1 Naming
- Tool name: `clipwright_<action>` (snake_case, verb-first). Examples: `clipwright_render`, `clipwright_inspect_media`, `clipwright_detect_silence`.
- Package/distrib name: `clipwright-<tool>` (example: `clipwright-render`). CLI command same, all lowercase.
- Consistent prefix helps AI agents pick the right tool.

### 6.2 Annotations (MCP)
All tools must have these. Detect/render split is visible in types.
- Detection / inspect tools: `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`.
- `clipwright-render`: `readOnlyHint: false` (creates new files), `destructiveHint: false` (input/OTIO unchanged), `idempotentHint: true` (same input → same output).
- Tools touching external processes/network: set `openWorldHint` honestly.

### 6.3 Response Envelope (All Tools)
Always return summaries + structured data + artifact paths in this form.

```jsonc
{
  "ok": true,
  "summary": "Detected 47 silent intervals, 3m 12s total. Longest ~8s near 02:14.",  // 1–2 sentences AI can act on
  "data": {
    // Tool-specific structured result (lightweight; big lists go to files)
  },
  "artifacts": [
    { "role": "timeline", "path": "<project>/timeline.otio", "format": "otio" },
    { "role": "output",   "path": "<project>/outputs/final.mp4", "format": "mp4" }
  ],
  "warnings": []
}
```
- `summary` includes essentials (counts, duration, max values, etc.). Don't skimp.
- Huge details (full cut lists) go to OTIO/JSON files in `artifacts`, not `data`. AI fetches on demand.
- Use MCP's `outputSchema` / `structuredContent` when possible so clients understand structure.

### 6.4 Errors
- On failure: `{ "ok": false, "error": { "code": "...", "message": "...", "hint": "..." } }`.
- `message`: what happened. `hint`: next step (concrete action). Both required.

### 6.5 Subprocess Discipline
- External tools run with arg arrays (`shell=False`). Never concatenate user/AI input into shell strings.
- Validate file paths before passing.
- All separate processes. No library linking (preserve license independence).

### 6.6 File I/O
- Input: receive existing paths (not byte streams).
- Output: write to `outputs/` or `artifacts/`, return paths via `artifacts`.
- Never destroy source media or OTIO (always generate new files, even for render).

### 6.7 Transport
- **stdio** by default (local operation, large files stay on disk, fast).

---

## 7. clipwright-render Requirements

Second tool to build. Takes OTIO, materializes with FFmpeg—the only tool that writes media.

### 7.1 Input
- `timeline` (path to OTIO file) + `output` (output path) + options (codec, resolution, subtitle burn-in, etc.).

### 7.2 Behavior
- Resolve OTIO clip ranges (keep intervals), **execute FFmpeg once (or minimal times)** to concat, trim, output. No redundant re-encodes.
- Supported now: single-source interval extraction + concat (silence cut realization), basic trim, passthrough. Future: multi-source concat, subtitle burn-in, transitions.
- **Non-destructive**: input and OTIO unchanged; new files in `outputs/`.

### 7.3 Dry-Run (Preview) Mode
- When `dry_run: true`: return what *would* happen—planned FFmpeg command (or filter plan), number of kept ranges, estimated output duration and size—without actually rendering.
- Lets AI iterate timelines cheaply, verify, then commit (real render). Maximizes the detect/render split benefit.

### 7.4 Return Value
- Success: §6.3 envelope. `summary` includes total duration, output size, clip count, etc. `artifacts` contains output video and timeline.

---

## 8. Technology Stack Decisions

- **Language: Python (FastMCP) recommended.** In general, TypeScript MCP SDK is more mature, but this project has OTIO's **official Python binding** and aligns with expected OSS ecosystem (auto-editor is Python, whisper.cpp has many Python wrappers). Python has the least friction when OTIO is the backbone.
  - Trade-off: TypeScript has SDK maturity and type ergonomics. Here, OTIO integration ease takes priority.
- **MCP framework**: Python SDK / FastMCP. `@mcp.tool`, Pydantic schema definitions, annotation attachment.
- **Schemas**: Pydantic for input (constraints, descriptions, examples). Define `outputSchema` where possible.
- **External dependencies**: `opentimelineio` (Apache-2.0), `ffmpeg`/`ffprobe` (**not bundled**; pre-install required).
- **Verification**: `python -m py_compile`, MCP Inspector (`npx @modelcontextprotocol/inspector`) for connectivity.

---

## 9. License & Distribution

- **Clipwright code: permissive (MIT or Apache-2.0).**
- **FFmpeg binaries: not bundled.** README clearly states "ffmpeg on PATH required." No distribution bundling = no LGPL/GPL propagation obligations.
- Each OSS integrated (auto-editor, etc.): check licenses individually, always use subprocess calls (avoid library linking).
- Future commercialization or bundled installers: engage a licensing expert at that time.

---

## 10. MVP Scope & Phases

### Phase 1 (now) – Foundation
1. Repository init, `clipwright` core (shared library + primitive MCP).
   - OTIO helpers, ffprobe-based `inspect_media`, project management, response envelope, error formatting, subprocess runner, common Pydantic types.
2. `clipwright-render` (with dry-run).
   - Minimum: "concat kept OTIO ranges from one source to output"—proves detect/render split.

### Phase 2 – Contract Validation
3. `clipwright-silence` (auto-editor or thin silence-detection wrapper).
   - Implement "detect silence → annotate as keep-ranges in OTIO" and verify `silence → render` composition works with contract alone (contract dogfooding).

### Phase 3 – Scale
4. `clipwright-transcribe` (whisper.cpp / faster-whisper wrapper, SRT/VTT + OTIO markers).
5. Add tools as needed (noise removal, etc.) under the same contract.
6. (Optional) Consider integrated platform UX once usage patterns emerge. Preserve core "tool suite" philosophy.

---

## 11. Future Direction & Contribution

- **Elevate §6 (contract) to a standalone public document** for tool authors. Enable external contributions under the same discipline.
- **Each tool gets evals**: Beyond MCP Inspector connectivity, prove "AI can solve real tasks" with independent, read-only, multi-tool scenarios.
- **Scale under `clipwright-*` namespace**: Add detectors. Maintain stable render as the single materializer.

---

## 11.5 OSS Evaluation & Adobe Integration (Added 2026-06-10)

Decision criteria: candidate OSS must align with §2 (design principles), §6 (contract), §9 (license).

### Adopted (OSS, subprocess-ready, OTIO/SRT normalized)

- **Silero VAD (MIT) – Upper layer for `clipwright-silence`**: Beyond ffmpeg's `silencedetect` (volume threshold), ML-based speech detection. Higher accuracy with BGM/ambient noise. Key difference: `silencedetect` judges **volume** (misses coughs as silent, keeps them); VAD judges **speech/non-speech** (can remove coughs as non-speech). → Use as `clipwright-silence` backend (detection logic already isolated in adapter function, easy to swap). Python library—wrap in small CLI subprocess to honor §2.4. Note: linguistic filler ("um", "uh") stays (requires transcribe-based detection to remove). Coughs, breath sounds: VAD's domain.
- **Whisper (whisper.cpp, MIT) – Transcription**: §1/§10 default choice. C++ binary is perfect for subprocess decoupling. Output SRT/VTT + OTIO markers.
- **BudouX (Apache-2.0) – Subtitle line-breaking**: Natural CJK line breaks (Japanese/CJK phrase boundaries). Pure text transform (no media change). Thin CLI wrapper → `clipwright-*` tool. Post-process transcribe output (SRT).

### Not Adopted (Philosophy mismatch)

- **After Effects scripting for caption compositing, MOGRT, Premiere via AE**: AE/Premiere are proprietary paid GUI apps, violating §2.1 (GUI-free), §2.4 + §9 (OSS subprocess decoupling + license independence), and §2.8 (portable formats; don't invent proprietary). MOGRT is Adobe-proprietary. → Not a `clipwright-*` tool.

### Adobe Integration Aligned with Spec

OTIO enables **OTIO → FCPXML / AAF / EDL** via standard OTIO adapters (§4.1 future option). Premiere Pro / After Effects users can finish editing in their NLE. Clipwright delivers the skeleton (cuts, captions positions) in OTIO/portable format; detailed animation/grading lives in the NLE (natural extension of detect/render split).

---

## 11.6 Finishing Pipeline Vision (Merge, Loudness, BGM) (Added 2026-06-11)

> **Goal**: Settle the approach for **finishing features** (merge multi-video, normalize loudness across clips, add BGM) within existing design principles (§2), contract (§6), detect/render split (§2.5). **`clipwright-noise` (Phase 3-5, implemented, commit 19c161c) is the proven pattern**; feature② is nearly a copy.

### Overall Strategy (Critical)

All three features involve media realization (re-encode, concat, mix). By §2.5 / contract M3, **realization concentrates in `clipwright-render` alone**. No new media-writer tools. Two patterns:

- **A. Render extensions** (realization itself): ① merge, ③ BGM mix.
- **B. New detect/annotate tool + render applies** (measure/place, no media change): ② loudness normalization. Same shape as `clipwright-noise`.

All decisions live in OTIO; **render applies in one ffmpeg pass** (avoid double-encoding, max quality):

```
One timeline.otio:
  ├ V1: Multi-source clips (=merge①) + cuts (silence)
  ├ A1: Dialog (loudness norm by ②, denoise by denoise flag)
  ├ A2: BGM clip + mix instructions (③: volume/fade/ducking)
  └ metadata["clipwright"]: denoise + loudness instructions
        ↓ clipwright_render (dry_run preview → real render)
  Final video: cut + concat + denoise + loudness norm + BGM mix in 1 pass
```

All via ffmpeg built-in filters—**zero new binary dependencies** (M4: arg arrays, shell=False, timeout via `clipwright.process.run`). Input: paths only (M5). Output: new files. No new ErrorCode needed (existing §4 list suffices).

---

### ① Merge (Concat Multi-Video) – Render Extension

- **Shape**: `clipwright-render` extension (no new tool). Spec §7.2 already notes "future: multi-source concat."
- **Current constraint**: `clipwright-render/.../plan.py:resolve_kept_ranges` requires all clips from same `target_url` (single source); multiple sources hit `UNSUPPORTED_OPERATION`. **Extend to multi-source.**
- **OTIO representation**: V1 track with clips from multiple sources (each `Clip.media_reference.target_url` may differ). Boundary validation `_check_source_within_timeline_dir` requires "all sources under timeline dir"; merge sources must be in same project dir.
- **Implementation (plan.py / render.py)**:
  - Drop "single-source" check in `resolve_kept_ranges`; assign input index per source (`-i src0 -i src1 …`).
  - Extend `build_plan` `filter_complex`: each clip → `[i:v]trim` / `[i:a]atrim` (i=source index) → all → `concat=n=N:v=1:a=1`. Generalize current single-source (fixed `[0:v]`).
  - **Resolution/fps/SAR mismatch**: concat needs uniform input specs. Insert `scale`/`fps`/`setsar` before clips (output spec = `RenderOptions.width/height/fps` or first clip). Major implementation cost for ①.
- **Timeline build**: Use existing `clipwright_write_timeline` (repeat `add_clip` for multi-source). No special tool.

### ② Loudness Normalization (EBU R128, Peak Matching) – New Detect Tool + Render Apply

- **Shape**: **New package `clipwright-loudness` (detect/annotate type)** + render applies. Nearly a copy of `clipwright-noise` (template `templates/clipwright-tool/` + noise reference).
- **Expected MCP**: `clipwright_detect_loudness(media, output, options=None, timeline=None)` (annotations: readOnly/non-destructive/idempotent/openWorld=false).
- **Analysis layer**: Call ffmpeg `ebur128` (EBU R128 integrated loudness LUFS / true peak) or `volumedetect` (mean/max dB) as subprocess. Same pattern as noise's `astats` (**verify real ffmpeg output fields on hardware**—lesson from noise DC-AS-004).
- **Instruction (OTIO annotation)**: `metadata["clipwright"]["loudness"]` gets `{ tool, version, kind:"loudness", mode:"loudnorm"|"peak", target_lufs:-16.0|target_peak_db:-1.0, measured:{...}, scope:"per_clip"|"track" }`. **Per-clip loudness uniformity**: `scope:"per_clip"` means measure each clip individually → apply per-clip gain (finer than noise's "track-wide").
- **Render apply**: `build_plan` reads instruction, applies gain to each clip's audio (`loudnorm=I=-16:TP=-1:LRA=11` linear params, or simple `volume={gain}dB`). Same "insert filter into audio chain" as denoise injection—**single pass maintained**.
  - Note: Strict EBU R128 is two-stage (measure → apply). **Detect tool measures**, render applies measured values as linear params (same two-stage division as noise: "detect measures, render applies").
- **Validation (e2e)**: Post-apply loudness/peak converges to target (same e2e shape as noise's -3dB test; include negative controls).

### ③ BGM (Mix) – Render Extension + Timeline Placement

- **Shape**: `clipwright-render` extension (audio mix) + way to place BGM in timeline.
- **OTIO representation**: **Second audio track A2** (existing `new_timeline` makes V1/A1; add A2) with BGM clips (`target_url`=BGM file) + `metadata["clipwright"]["bgm"]` with `{ volume_db, fade_in_sec, fade_out_sec, ducking:{enabled, threshold, ratio} }`.
- **Placement**: Use existing `clipwright_write_timeline` `add_clip` (to A2). Optionally create thin `clipwright_add_bgm(timeline, bgm, output, options)` helper (read-only, non-destructive); not required.
- **Render apply (build_plan)**:
  - Mix main audio `[outa]` + BGM `[bgm:a]` via `amix=inputs=2` (or `amerge`). Prefix BGM with `volume` / `afade`.
  - **Ducking** (auto-reduce BGM during dialog): `sidechaincompress` (sidechain main audio, compress BGM). Optional v1; start with fixed volume + fade.
  - Match duration: BGM < main → `aloop`/`apad`; longer → `atrim`.
- **Input**: BGM path (M5). Same project dir assumption as merge sources (boundary validation).

---

### Order of Attack & Cautions

| Feature | Shape | Key FFmpeg | Effort | Notes |
|---|---|---|---|---|
| ② Loudness | New `clipwright-loudness` + render | `ebur128`/`loudnorm`/`volume` | Low–Mid | **Quickest: copy noise. Start here.** |
| ① Merge | Render extension | `concat` + `scale`/`fps` unify | Mid | Crux: drop single-source check. Resolution unify costs. |
| ③ BGM | Render ext. + timeline place | `amix`/`afade`/`sidechaincompress` | Mid | A2 track easy; ducking optional follow-up. |

- **Caution (render scope creep)**: ① + ③ add concat generalization + audio mixing. Spec says "keep render stable"; enforce **backward compatibility** (no instruction/multi-source = old behavior). Follow `clipwright-noise` denoise pattern: "optional args + no instruction = legacy" discipline.
- **Approach**: Repeat noise cycle: hearing → design → design audit → parallel-agents implement → code/security review to zero. Design audit has high ROI for pre-impl gap detection (noise caught 21 issues pre-implementation).

---

## 12. Claude Code: First Steps

1. Set up repo and environment (Python, FastMCP, opentimelineio, verify ffprobe/ffmpeg exist). Recommended layout:
   ```
   clipwright/                 # Core: shared library + primitive MCP
     pyproject.toml            # license = MIT/Apache-2.0
     clipwright/
       __init__.py
       otio_utils.py           # OTIO read/write/annotation helpers
       media.py                # ffprobe wrapper (structured output)
       project.py              # project dir / manifest
       process.py              # subprocess runner (shell=False, timeout)
       envelope.py             # response envelope / error formatting
       schemas.py              # common Pydantic types (MediaRef, TimeRange, ToolResult, …)
       server.py               # Primitive MCP (init_project, inspect_media, read/write_timeline)
   clipwright-render/          # Second tool (separate package)
     pyproject.toml
     clipwright_render/
       __init__.py
       render.py               # OTIO → FFmpeg plan → execute (dry_run-ready)
       server.py               # MCP (clipwright_render)
   CONVENTIONS.md              # Tool author contract (§6 extracted)
   README.md                   # ffmpeg requirement, install, usage
   ```
2. Fix core common types (`schemas.py`) and response envelope (`envelope.py`) first. This is the contract layer for all tools.
3. Implement `inspect_media` (ffprobe) and OTIO helpers; ship primitive MCP.
4. Implement `clipwright-render`: first, `dry_run` returns FFmpeg plan + estimated output; then real rendering.
5. MCP Inspector connectivity check → validate with real media: concat kept intervals to one file.

> Before starting implementation, confirm `clipwright` and `clipwriter-render` names are available on PyPI, npm, GitHub org, and domain registrars. If available, reserve PyPI project names early.
