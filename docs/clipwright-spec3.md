# Clipwright — Missing Features & Implementation Roadmap (Round 3)

> Companion to `clipwright-spec.md` and `clipwright-spec2.md`. The features
> catalogued in `clipwright-spec2.md` (scene / frames / speed / text / color /
> stabilize) are now all implemented and shipped (v0.3.0–v0.8.0). This document
> catalogues the **next** set of gaps, identified from a full-suite dogfood run
> in which all 13 current tools were chained over real MCP (stdio) on a 1080p60
> gameplay clip. It provides enough detail for a developer or AI agent to begin
> implementation.

---

## Verification context (what surfaced these gaps)

A 3-minute / 1920×1080 / 60fps gameplay clip was processed end-to-end through the
full tool suite via real stdio MCP:

```
silence → stabilize → color → loudness → noise → scene
        → add_text → add_bgm → render            (edited.mp4)
transcribe → wrap → render(subtitle burn)        (subtitled.mp4)
set_speed(2.0) → render                          (speed2x.mp4)
extract_frames                                   (frames/)
```

All 13 tools succeeded. Three concrete gaps blocked or distorted the workflow and
had to be worked around with raw FFmpeg or pipeline contortions:

1. **No way to select a time range.** The 36-minute source had to be trimmed to a
   3-minute working clip with a hand-written `ffmpeg -ss … -t …` call. Clipwright
   has no tool that turns "keep 10:00–13:00" into an OTIO timeline; only
   `clipwright-silence` produces kept ranges, and it does so by audio analysis,
   not by explicit user intent.
2. **No hardware-accelerated encode.** `clipwright-render` calls FFmpeg with
   software `libx264`. Measured: a pure re-encode of the clip ran at 157 fps on
   CPU (69.0s) vs **538 fps on `h264_nvenc` (20.1s) — ~3.4× faster**. There is no
   validated option path to hardware encoders, and `RenderOptions.crf` is emitted
   as a bare `-crf` flag that NVENC does not accept.
3. **Captions and overlays are not re-timed across cuts/speed.** `render` applies
   silence cuts and `LinearTimeWarp` to the timeline, but subtitle cues (from
   `transcribe`) and `text_overlay` markers are positioned in source-media time
   and are **not** remapped onto the post-edit timeline. The subtitle demo only
   lined up because it was rendered against the *uncut* full-length clip.

---

## How to Read This Document

Each entry follows a fixed structure:

- **Package name** — proposed PyPI / MCP identifier (or "render extension" when no
  new package is warranted)
- **What it does** — one-paragraph description of the tool's responsibility
- **Why it is needed** — the gap it fills, grounded in the verification above
- **MCP tool name(s)** — the `clipwright_<action>` identifiers the tool will expose
- **Implementation hints** — concrete technical notes (FFmpeg filters, OSS, OTIO patterns)
- **Priority** — High / Medium / Low, explained below

### Priority definitions

| Priority | Meaning |
|----------|---------|
| **High** | Blocks common AI-assisted editing workflows today; surfaced as a hard blocker or correctness trap during verification; no in-suite workaround exists |
| **Medium** | Valuable for quality or reach (vertical formats, multi-clip assembly, performance); can be deferred without blocking basics |
| **Low** | Niche use case or largely handled by existing tools with minor extensions/tuning |

---

## High Priority

### `clipwright-trim`  ✅ IMPLEMENTED (v0.1.0, 2026-06-20)

> Shipped as the `clipwright-trim` package (MCP tool `clipwright_trim`). Supports
> `keep` ranges (retained in listed order), `drop` ranges (complement kept), and
> a no-options passthrough that wraps the whole clip into a renderable OTIO.
> Verified end-to-end over real stdio MCP (trim → render duration match).

**What it does**
Builds (or edits) an OTIO timeline from explicit, user-specified time ranges of a
source: "keep 600.0–780.0s", or a list of keep/drop ranges. Produces the same
kept-range OTIO shape that `clipwright-silence` emits, so every downstream tool
and `clipwright-render` consume it unchanged.

**Why it is needed**
This is the most basic editing primitive — selecting which parts of a clip to keep
— and it is entirely missing. During verification the 36-minute source could not
be reduced to a working segment with any clipwright tool; a raw `ffmpeg -ss/-t`
call was required, breaking the non-destructive, single-render contract for the
very first step. `clipwright-silence` is the only kept-range producer, but it
selects by audio energy, not by intent. An AI agent told "use the part from 10:00
to 13:00" or "drop the first two minutes" has no tool to express that today.

**MCP tool name(s)**
`clipwright_trim` (alias considered: `clipwright_set_ranges`)

**Implementation hints**
- Pure OTIO construction — no FFmpeg pass needed at detect time. Reuse core's
  `new_timeline` / `add_clip` and the same kept-range structure as
  `clipwright-silence` so `resolve_kept_ranges` in render works without changes.
- Accept `keep` ranges and/or `drop` ranges; compute the complement against the
  probed source duration (probe via core `inspect_media`). Validate ranges are
  within `[0, duration]`, non-overlapping after merge, and non-empty.
- Optionally accept an existing `timeline` to re-trim (intersect new ranges with
  current kept ranges) so it composes after `silence`.
- Time handling via OTIO `RationalTime`/`TimeRange` (no float-seconds round-trips).
- MCP annotations: `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`.
- Options struct:
  ```python
  class TrimOptions(BaseModel):
      keep: list[tuple[float, float]] = []   # (start_sec, end_sec) ranges to keep
      drop: list[tuple[float, float]] = []   # ranges to remove; complement is kept
      padding_sec: float = 0.0               # extend each keep range on both sides
      # exactly one of keep/drop must be non-empty (validated in _trim_inner)
  ```

---

### Hardware-accelerated encode/decode  ✅ IMPLEMENTED (render v0.8.0, 2026-06-20)  *(render extension)*

> Shipped as a `clipwright-render` extension (v0.8.0). Three new `RenderOptions`
> fields: `hw_encoder` (none/auto/nvenc/amf/qsv/vaapi/videotoolbox, default `"none"`),
> `hwaccel_decode` (bool, default `False`), and `quality` (int 0–51, encoder-neutral
> quality knob). `"auto"` uses probe-then-test (checks `ffmpeg -encoders`, runs a
> 1-frame throwaway encode to `-f null -`) and falls back to `libx264` with a
> `warnings[]` entry. Explicit vendor failure returns `UNSUPPORTED_OPERATION`.
> `hwaccel_decode` v1 downloads frames to system memory before CPU filters so all
> existing filter chains remain compatible. Full HW↔HW filtergraph is out of v1 scope.
> **NVENC verified on the maintainer's dev box; AMD AMF / Intel QSV / VAAPI /
> Apple VideoToolbox are experimental — community verification needed.**
> Fully backward compatible.

**What it does**
Lets `clipwright-render` use GPU encoders (NVENC / QSV / VideoToolbox / AMF) and
hardware decode, with encoder-aware rate control so quality flags map correctly
per encoder.

**Why it is needed**
Measured during verification: a pure re-encode was **~3.4× faster on `h264_nvenc`
(20.1s) than on `libx264` (69.0s)**, and even the filter-heavy full edit improved
~22% (212.9s → 165.0s). Today `RenderOptions` accepts an arbitrary
`video_codec` string, but two things block real use: (1) `crf` is emitted as a
bare `-crf`, which NVENC rejects (it uses `-cq`/`-rc`); (2) there is no
`hwaccel` decode path, so the decode side stays on CPU. Transcode- and
export-heavy workflows (the common "just render this faster" case) leave the GPU
idle.

**MCP tool name(s)**
None — extend `clipwright_render` `RenderOptions`.

**Multi-vendor scope**
Hardware encoding is **not** NVIDIA-only. Because clipwright never links a GPU
library and only selects a different FFmpeg encoder string (external-process
policy preserved), supporting every major vendor is cheap. The encoder name is
both vendor- and OS-dependent:

| Vendor | Windows | Linux (portable) | AV1 support |
|--------|---------|------------------|-------------|
| NVIDIA | `h264_nvenc` / `hevc_nvenc` / `av1_nvenc` | same | Ada (RTX 40xx)+ |
| AMD Radeon | `h264_amf` / `hevc_amf` / `av1_amf` | VAAPI (`*_vaapi`) | RDNA3+ |
| Intel Arc / iGPU | `h264_qsv` / `hevc_qsv` / `av1_qsv` | QSV or VAAPI | Arc (strong AV1) |
| Apple | — | — (`*_videotoolbox` on macOS) | M3+ |

On Linux, prefer **VAAPI** for Intel/AMD (one portable path); on Windows use the
vendor-specific encoders. Hide this matrix behind `auto` detection (below) so the
caller never has to know their GPU vendor or OS.

**The verification problem (and how the design solves it)**
The maintainer can realistically verify only NVENC (the dev box GPU). The risk is
shipping AMD/Intel/VAAPI paths that have never run on real hardware. CI cannot
help — GitHub Actions runners have no GPU. The design must therefore be
**self-validating at runtime** rather than relying on the maintainer owning every
GPU:

1. **Probe-then-test, don't assume.** Presence in `ffmpeg -encoders` is necessary
   but not sufficient (the encoder may exist in the build while the GPU/driver is
   absent or disabled — e.g. QSV present but iGPU off, AMF present but no driver).
   Confirm with a **tiny throwaway test encode** (e.g. 1s `testsrc` → null muxer
   with the candidate encoder) and accept the encoder only if it succeeds.
2. **`auto` + guaranteed software fallback.** `hw_encoder="auto"` walks available
   encoders in priority order, runs the test encode, and uses the first that
   passes; on any init/encode failure during the real render it falls back to
   `libx264` and appends a `warnings[]` entry. This guarantees the render *always
   completes* even on an untested vendor.
3. **Honest labelling.** Mark NVENC as verified-on-dev and AMF/QSV/VAAPI/
   VideoToolbox as **experimental / community-verification needed** in README and
   tool docs. Do not claim "verified" for paths no one has run.
4. **CI tests arg-construction only.** Unit-test the per-encoder argument mapping
   with a mocked FFmpeg (assert the right `-c:v` / rate-control flags are emitted);
   leave real-hardware confirmation to the runtime probe + users.

**Implementation hints**
- Add an `encoder`/`hwaccel` abstraction rather than leaking raw codec strings:
  ```python
  class RenderOptions(BaseModel):
      ...
      # "auto" = detect+test-encode, pick best available, fall back to software.
      hw_encoder: Literal[
          "none", "auto", "nvenc", "amf", "qsv", "vaapi", "videotoolbox"
      ] = "none"
      hwaccel_decode: bool = False
      quality: int | None = None   # encoder-neutral; mapped per encoder
  ```
- Map `quality`/`crf` per encoder: x264/x265 → `-crf`; NVENC → `-cq` (+ `-rc vbr`);
  QSV/VAAPI → `-global_quality` (+ `-rc_mode`); AMF → `-qp_i/-qp_p` or `-rc`.
  Do **not** emit `-crf` when a hardware encoder is selected (NVENC/AMF/QSV reject it).
- Capability check sequence: `_resolve_hw_encoder(requested)` →
  (a) list `ffmpeg -encoders`; (b) for the candidate(s), run a 1-frame test encode
  to `-f null -`; (c) return the first that succeeds, else error (explicit vendor)
  or fall back to `libx264` (`auto`). Mirror the existing `libvidstab`/font
  capability-check pattern; cache the probe result per process.
- On explicit-vendor failure, return an actionable error+hint
  (e.g. "h264_qsv is present but failed to initialise; the Intel GPU/driver may be
  unavailable. Use hw_encoder='auto' or 'none'.").
- `hwaccel_decode`: prepend `-hwaccel cuda` / `-hwaccel qsv` / `-hwaccel vaapi`
  before the relevant `-i`. Note interplay with CPU filters (vidstab/eq/drawtext)
  that need frames in system memory — insert `hwdownload`/`format` or fall back to
  CPU decode when a CPU-only filter is in the graph.
- Document that hardware encoders trade some size/quality fidelity for speed
  (verification: NVENC default rate control produced a much smaller file than
  `libx264 -crf 21` — not a quality-matched comparison).

---

### Caption & overlay re-timing across edits  ✅ IMPLEMENTED (v0.7.0, 2026-06-20)  *(render extension / core helper)*

> Shipped as a `clipwright-render` extension (v0.7.0). `retime_markers="auto"`
> (default) re-times `.srt` subtitle cues and `text_overlay` markers to program time
> whenever the timeline contains cuts or `LinearTimeWarp` warps. Writes a
> non-destructive `{output_stem}.retimed.srt` alongside the output. Identity timelines
> are a no-op. Cut-spanning cues are split; drop-range cues are dropped with a
> `warnings[]` entry. `.vtt`/`.ass` and multi-source timelines are skipped with a warning.

**What it does**
Remaps subtitle cues and `text_overlay` marker timings from source-media time onto
the post-edit timeline so that, after silence cuts and `LinearTimeWarp`, burned
captions and overlays still land on the right frames.

**Why it is needed**
A correctness trap confirmed during verification: `render` re-times the program
(it drops silence ranges and applies speed warps) but leaves subtitle cues and
text-overlay markers in original source time. The subtitle demo only worked
because it was rendered against the *uncut* clip; combining `transcribe` output
with `silence` cuts in a single render would have placed every cue at the wrong
time (and cut-away cues would vanish). There is currently no tool or render path
that reconciles the two.

**MCP tool name(s)**
None for the render-side burn (extend `clipwright_render`). A standalone helper
`clipwright_remap_captions` (in `clipwright-wrap` or core) can emit a re-timed
`.srt` for cases where an external subtitle file is preferred.

**Implementation hints**
- Build the source→program time mapping from the same kept-range list render
  already computes (`resolve_kept_ranges`) plus any `LinearTimeWarp` scalars.
  For each source instant `t_src` in a kept range starting the program offset
  `t_prog`, `t_prog = (t_src - range.start)/speed + cumulative_kept_before`.
- For `text_overlay` markers: translate `start_sec`/`duration_sec` through the same
  mapping before emitting the `drawtext enable='between(t, …)'` window. Drop or
  clip overlays whose source window falls entirely inside a removed range.
- For subtitle burn: when `options.subtitle` is set **and** the timeline has cuts
  or warps, re-time the cue list to program time before passing to the `subtitles`
  filter (or expose `clipwright_remap_captions` to produce the re-timed `.srt`).
- Add a `warnings[]` entry whenever cues/overlays are dropped or shifted, so the
  calling agent knows the program was re-timed.
- Tests must assert cue alignment using `RationalTime` math, not float seconds.

---

## Medium Priority

### `clipwright-reframe` ✅ IMPLEMENTED (v0.1.0)

**What it does**
Crops, pads, scales, and rotates video to a target aspect ratio / resolution —
e.g. converting 16:9 landscape to 9:16 vertical (with optional blurred-letterbox
background) for shorts/reels, or 1:1 square. Annotates the OTIO and lets render
realise it.

**Why it is needed**
`render` can change `width`/`height` but only by stretching to the target box; it
cannot crop-to-fill, pad, or re-frame to a different aspect ratio. Vertical and
square formats dominate social distribution, and the source in verification was
16:9 — there is no path to a 9:16 deliverable today.

**MCP tool name(s)**
`clipwright_reframe`

**Implementation hints**
- Store a reframe directive in `metadata["clipwright"]["reframe"]`:
  `{"target_w": 1080, "target_h": 1920, "mode": "crop"|"pad"|"blur_pad", "anchor": "center"}`.
- render filter mapping:
  - crop-to-fill: `scale` to cover + `crop` to target.
  - pad (letterbox): `scale` to fit + `pad` to target with a fill colour.
  - blur_pad (popular for vertical): split → blurred scaled background +
    foreground `overlay` centered.
- Compose cleanly with existing `eq`/`drawtext`/`vidstabtransform` filters in the
  chain (reframe should run before drawtext so text positions resolve against the
  final frame size).
- Options struct mirrors the directive; validate target dims ≥ 2 and even.

---

### `clipwright-sequence`  ✅ IMPLEMENTED (v0.1.0, 2026-06-22)  (multi-source assembly)

> Shipped as the `clipwright-sequence` package (MCP tool `clipwright_build_sequence`).
> Accepts up to 1000 ordered `SequenceClip` entries (source path + optional
> `start_sec` / `end_sec`). Emits a single V1 video track OTIO timeline consumed by
> `clipwright-render` with no changes.  Co-location constraint matches render's
> boundary: sources must live under the output `.otio` parent directory (recursive
> subdirs allowed — ADR-SEQ-6).  Symlink sources unsupported (DC-AS-005).
> `total_duration_sec` is approximate (DC-AM-003).  Requires `CLIPWRIGHT_FFPROBE`.

**What it does**
Assembles a single OTIO timeline from multiple source files in order — intro +
main + outro, or interleaved b-roll — each with its own optional in/out range.

**Why it is needed**
`clipwright-render` already concatenates multiple sources (`unique_sources_in_order`
/ multi-input `-i` path), but **no satellite tool builds a multi-source timeline**.
Every current tool starts from a single `media` file. During verification the BGM
had to be generated as a standalone file and attached via `add_bgm`; there is no
way to, say, prepend a title card clip or splice two gameplay segments into one
program.

**MCP tool name(s)**
`clipwright_build_sequence`

**Implementation hints**
- Pure OTIO construction over core `add_clip`; append clips (each a source + source
  range) to the V1 track in order; optionally carry per-clip transition hints (see
  `clipwright-transition`).
- Validate every source exists and lives under the timeline directory (render's
  boundary check requires co-location — mirrored, not relaxed).
- This is largely a thin authoring layer over capabilities render already has;
  most effort is path/boundary validation and OTIO bookkeeping.
- MCP annotations: `readOnlyHint: true` (writes only the OTIO, not media).

---

### `clipwright-overlay`  ✅ IMPLEMENTED (v0.1.0, 2026-06-22)  (image / logo / watermark)

**What it does**
Overlays a static image (PNG logo, watermark, lower-third graphic, end card) onto
the video at a position/size/opacity for a time range. Image counterpart to the
existing text overlay.

**Why it is needed**
`clipwright-text` covers on-screen text, but channel logos, watermarks, and
graphic lower-thirds are standard in published video and have no path today.

**MCP tool name(s)**
`clipwright_add_overlay`

**Implementation hints**
- Store overlay markers like text overlays:
  `metadata["clipwright"]["kind"] = "image_overlay"` with
  `{image_path, x, y, scale, opacity, start_sec, duration_sec, fade_in_sec, fade_out_sec}`.
  `image_path` is stored as a POSIX relative path from the output timeline's parent
  directory (round-trip portability — V2-3).
- render: reconstruct the absolute image path from the relative stored path, add
  the image as an extra `-i` after BGM (preserving `bgm_index = len(input_sources)`
  invariant — ADR-OV-5). For each overlay, emit two filter segments:
  ```
  [{N}:v]scale=iw*{scale}:-2,format=rgba,colorchannelmixer=aa={opacity},
  fade=t=in:st={start}:d={fade_in}:alpha=1,fade=t=out:st={end-fade_out}:d={fade_out}:alpha=1[ov{i}];
  {base}[ov{i}]overlay=x='{x}':y='{y}':enable='between(t,{start},{end})'[outvimg{i}]
  ```
  `scale=iw*{scale}:-2` (even-rounding for yuv420p). `colorchannelmixer=aa={opacity}`
  sets constant opacity (aa does not accept time-varying expressions — G1 confirmed).
  `fade:alpha=1` multiplies the existing alpha, ramping 0 → opacity → 0.
  `x`/`y` are single-quoted (consistent with `enable` and drawtext — G5 confirmed).
  Insert this chain after `drawtext` so image overlays appear on top of text.
- Validate `image_path` is `.png`/`.jpg`/`.jpeg`/`.webp`, exists, and is within the
  timeline directory (same boundary policy as sources/subtitles). x/y allowlist:
  `^[A-Za-z0-9_()+\-*/. ]+$` (prohibits `:`, `;`, `[`, `]`, `,`, `'`, control chars).
- Maximum 64 image overlays per timeline (scale `(0, 8]`).

---

### GPU / CUDA transcription  ✅ IMPLEMENTED (v0.3.0, 2026-06-22)  *(transcribe wiring + docs)*

**What it does**
Allows `clipwright-transcribe` to use a CUDA-enabled `whisper.cpp` build (or
Metal on macOS) for GPU-accelerated speech-to-text. Transparent to callers:
point `CLIPWRIGHT_WHISPER` at a GPU build; no code or parameter changes needed.

**Why it is needed**
Verification used a CPU `whisper.cpp` build: 180s of audio took 136s. Transcription
is one of the three dominant costs in the suite, and a GPU build is typically
several times faster. The binary is already env-configurable
(`CLIPWRIGHT_WHISPER`), so this is mostly capability detection + documentation, not
new architecture.

**MCP tool name(s)**
None — same `clipwright_transcribe`; honours a GPU-capable binary transparently.

**Implementation summary (v0.3.0)**
- `data.backend` (`device`: `cuda | metal | cpu | unknown`, `detail`: raw whisper
  device string) and `data.realtime_factor` (`whisper_wall_seconds /
  audio_duration_sec`) surfaced in the transcribe envelope.
- `data.whisper_wall_seconds` (raw wall-clock seconds in the whisper subprocess)
  also included.
- `summary` now reports the backend used for quick MCP-level inspection.
- `## GPU / CUDA Acceleration` section added to `clipwright-transcribe/README.md`
  with per-platform CUDA/Metal binary acquisition guidance and runtime verification
  instructions.
- External-process / license-independence rule maintained: `faster-whisper` and
  CTranslate2 are **not** imported; any whisper-cli-compatible binary works.

---

## Low Priority

### `clipwright-transition`

**What it does**
Inserts crossfades / dissolves (and audio crossfades) between adjacent clips or at
scene boundaries, instead of hard cuts.

**Why it is needed**
All current cuts are hard cuts. Dissolves between scenes/segments are a common
polish step, especially once `clipwright-sequence` enables multi-clip programs.
Deferred because hard cuts are an acceptable default and this depends on sequence
assembly landing first.

**MCP tool name(s)**
`clipwright_add_transition`

**Implementation hints**
- Store transition directives between clips; render maps to `xfade` (video) +
  `acrossfade` (audio) filters, which require overlapping/adjacent segment handling
  in the filter graph (more complex than concat).
- Start with `fade`/`dissolve` of a fixed duration; expand types later.

---

### Content-aware scene detection / threshold tuning  *(scene refinement)*

**What it does**
Improves `clipwright-detect-scenes` recall on low-cut content via a content-aware
backend and/or adaptive thresholds.

**Why it is needed**
On the continuous-gameplay verification clip, `detect_scenes` returned **0
boundaries** at the default threshold (it emitted a "consider lowering the
threshold" warning). For screen-capture / single-shot footage the FFmpeg
`select=gt(scene,…)` heuristic is weak. `clipwright-spec2.md` already proposed an
optional `pyscenedetect` backend; it appears not to have been wired.

**MCP tool name(s)**
None — extend `clipwright_detect_scenes`.

**Implementation hints**
- Add the `backend: Literal["ffmpeg", "pyscenedetect"]` option from spec2 (content
  detector handles gradual/low-contrast cuts better), invoked as an external
  process (`scenedetect` CLI) to keep license independence.
- Optionally auto-suggest a lower threshold in `summary`/`hint` when 0 boundaries
  are found, rather than only warning.

---

## Won't Implement (and Why)

| Feature | Reason |
|---------|---------|
| Dedicated format-conversion tool | `clipwright-render` accepts arbitrary `video_codec`/`audio_codec`/container + (soon) hardware encoders. A separate tool adds no contract value. |
| Auto-highlight / auto-edit "one-click" tool | Composing silence + scene + transcribe into highlight selection is the **AI agent's** job; clipwright provides the primitives, the agent orchestrates. Baking policy into a tool fights the AI-first design. |
| Multi-cam sync | Requires timecode / waveform alignment — complex, niche, out of scope. |
| In-tool GPU library linking (CUDA/NVENC as a Python dep) | Violates the license-independence rule; GPU acceleration is reached only by invoking external FFmpeg/whisper binaries. |

---

## Dependency Map (current 13 tools + proposed)

```
clipwright (core)
  ├─ clipwright-silence        ← shipped
  ├─ clipwright-trim           ← NEW (High); manual kept-ranges, same OTIO shape as silence
  ├─ clipwright-scene          ← shipped (content-aware backend = Low refinement)
  ├─ clipwright-frames         ← shipped
  ├─ clipwright-transcribe     ← ✅ IMPLEMENTED (v0.3.0, 2026-06-22); GPU/CUDA wiring + data.backend/realtime_factor
  ├─ clipwright-wrap           ← shipped (may host clipwright_remap_captions)
  ├─ clipwright-loudness       ← shipped
  ├─ clipwright-noise          ← shipped
  ├─ clipwright-color          ← shipped
  ├─ clipwright-speed          ← shipped
  ├─ clipwright-text           ← shipped
  ├─ clipwright-stabilize      ← shipped (skip for screen-capture sources)
  ├─ clipwright-bgm            ← shipped
  ├─ clipwright-reframe        ← ✅ IMPLEMENTED (v0.1.0); aspect/crop/pad, vertical formats
  ├─ clipwright-sequence       ← ✅ IMPLEMENTED (v0.1.0); multi-source assembly over render's existing concat
  ├─ clipwright-overlay        ← ✅ IMPLEMENTED (v0.1.0, 2026-06-22); image/logo/watermark overlay
  ├─ clipwright-transition     ← NEW (Low); xfade/acrossfade
  └─ clipwright-render         ← shipped; extend for HW encode/decode, caption/overlay re-timing,
                                  reframe, image overlay, transitions
```

---

## render Extension Checklist (new)

`clipwright-render` is the single materialisation point, so most new features land
as render filter/option work:

- [x] Hardware encoders, multi-vendor (`nvenc`/`amf`/`qsv`/`vaapi`/`videotoolbox`)
      with `auto` probe-then-test detection + guaranteed `libx264` fallback, and
      encoder-aware rate control (map quality → `-cq`/`-crf`/`-global_quality`/`-qp`;
      never `-crf` for hardware encoders). NVENC verified-on-dev; others experimental.
- [x] Hardware decode (`-hwaccel cuda`/`qsv`/`vaapi`) with CPU-filter fallback / `hwdownload`
- [x] Re-time `text_overlay` markers through the kept-range + `LinearTimeWarp` map
- [x] Re-time / remap subtitle cues to program time when cuts or warps are present
- [x] `reframe` metadata → `scale`/`crop`/`pad`/`overlay` (blur-pad) filter chain
- [x] `image_overlay` markers → extra `-i` + `overlay` filter with opacity/fade (render v0.10.0)
- [ ] `xfade` / `acrossfade` for transitions (depends on sequence assembly)
```
