# Clipwright — Defects & Missing Features (Round 4)

> Companion to `clipwright-spec.md`, `clipwright-spec2.md`, and
> `clipwright-spec3.md`. The features catalogued in spec3 (trim, hardware
> encode/decode, caption/overlay re-timing, reframe, sequence, overlay,
> GPU transcription wiring, transition, content-aware scene backend) are all
> shipped. This document catalogues the **next** set of gaps, identified from a
> full-suite dogfood run in which all 17 satellite tools + core were chained over
> real stdio MCP on a 52-minute 1080p60 gameplay capture, using the suite's
> "upgrade" backends (Silero VAD, PySceneDetect, NVENC). It records both the
> **defects** that real execution surfaced (which in-process tests had missed) and
> the **missing features / workflow friction** that required workarounds.

---

## Verification context (what surfaced these gaps)

A 52.7-minute / 1920×1080 / 60fps / h264+aac gameplay capture (Hogwarts Legacy,
2.0 GB) was processed end-to-end via real stdio MCP. A 3-minute working window
(10:00–13:00) was first selected with `clipwright-trim` and materialised with
`clipwright-render` (NVENC + hardware decode), then every other tool operated on
that small co-located clip. Six deliverables were produced:

```
trim → render(NVENC + hwdecode)                                  clip.mp4 (180s)
color → loudness → noise → scene → text → overlay → bgm → render  highlight.mp4
silence(VAD) → transcribe → wrap → render(subtitle, retime)       subtitled.mp4 (111s)
set_speed(2.0) → render(subtitle, retime)                         speed2x_sub.mp4 (90s)
sequence(3 ranges) → transition(dissolve) → render               program.mp4 (117s)
reframe(9:16 blur_pad) → render                                   vertical_short.mp4 (1080×1920)
extract_frames(interval)                                          frames/
```

`clipwright-stabilize` was intentionally excluded: this is shake-free
screen-capture footage, where `vidstabtransform` introduces drift and crop.

The "upgrade" backends were exercised directly and their value confirmed on real
data:

- **Silero VAD** kept 111.3 s across 21 speech intervals vs the energy
  `silencedetect` backend's 137.0 s / 39 intervals — VAD targets actual speech
  more precisely on footage with music and sound effects.
- **NVENC** produced the pure-transcode working clip with hardware decode.
- **PySceneDetect** found 11 content-aware scene boundaries where the ffmpeg
  `scdet` backend found **0** on the same continuous-gameplay clip.

Two of those exercises also exposed **defects**: the PySceneDetect backend failed
outright against a current PySceneDetect release, and the transition render path
produced an unplayable 4:4:4 file. Both are detailed below. They are the kind of
fault that only a real-binary, real-MCP run can catch — in-process and mocked
tests passed for both paths.

---

## How to Read This Document

This round has two kinds of entry:

- **Defects** — something that is shipped but broken. Each lists the symptom, the
  root cause, the blast radius, and a contained fix.
- **Missing features / friction** — a capability gap or workflow contortion that
  required a manual workaround during the dogfood. Each follows the spec2/spec3
  structure (What it does / Why it is needed / MCP tool name(s) / Implementation
  hints / Priority).

### Priority definitions

| Priority | Meaning |
|----------|---------|
| **High** | Ships broken output, or blocks/degrades a common AI-assisted editing workflow today; surfaced as a hard failure or a correctness/quality trap during verification |
| **Medium** | Valuable for quality or reach (vertical formats, thumbnails, large-source ergonomics); can be deferred without blocking basics |
| **Low** | Niche, environmental, or already acknowledged as out-of-scope for a prior version |

---

## Defects (found by the round-4 dogfood)

### D1. `clipwright-render` transition path emits unplayable 4:4:4 video  ✅ **FIXED (render 0.11.1)**

> Fixed by pinning output chroma to `yuv420p` once in `_build_ffmpeg_args()` (after
> the `-map` group, before the codec branch), so every output path and both
> software/hardware encoders produce broadly-playable 4:2:0. Verified over real
> stdio MCP: transition output is now `yuvj420p` (Main/High, 4:2:0) on both NVENC
> and libx264; non-transition paths and resolution/duration are unchanged. Color
> range is intentionally not converted. (Originally reported below.)

**Symptom**
`program.mp4` (sequence → transition(dissolve) → render) would not open in common
consumer players (Windows "Movies & TV" reported *"unsupported encoding
settings"*). The file is `yuvj444p` / **H.264 High 4:4:4 Predictive**, a chroma
format most hardware/consumer decoders reject. The other five deliverables are
`yuv420p`/`yuvj420p` (Main/High) and play everywhere.

**Root cause**
The source clip is `yuvj420p`, but the `xfade` filter graph negotiates to 4:4:4,
and `clipwright-render` never pins the output chroma format. Re-rendering the same
`seq_tr.otio` with `hw_encoder="none"` (libx264) produces 4:4:4 **as well**, so
this is **not** NVENC-specific — both encoders accept 4:4:4 and faithfully encode
whatever the filter graph hands them. The non-transition concat path happens to
preserve 4:2:0, which is why this never surfaced before transitions were exercised
on real footage.

**Blast radius**
Every render that goes through the transition (xfade/acrossfade) path, for every
user, on every encoder. The resulting deliverable is silently unplayable in
mainstream players.

**Contained fix**
Append `format=yuv420p` to the final video filter chain (or pass `-pix_fmt
yuv420p` on the output) in `clipwright-render`. Safest to pin 4:2:0 on **all**
output paths, not just transition, so the suite's deliverables are universally
playable regardless of source chroma. Add a regression test that asserts the
emitted ffmpeg args include the chroma pin, and an e2e check that probes
`pix_fmt == yuv420p` on a transition render. Consider exposing an opt-out
(`pix_fmt` option) only if a 4:4:4 master is ever explicitly requested.

---

### D2. `clipwright-scene` PySceneDetect backend is incompatible with PySceneDetect 0.7  ✅ **FIXED (scene 0.2.1)**

> Fixed by switching `_detect_with_pyscenedetect()` from stdout parsing
> (`list-scenes -c`, removed in 0.7) to reading the CSV file written by
> `list-scenes -o <tmpdir> --skip-cuts -q` (parsed by the unchanged
> `parse_pyscenedetect_csv`). A missing/unreadable CSV falls back to zero
> boundaries. Verified over real stdio MCP against PySceneDetect 0.7: the backend
> now returns content-aware boundaries (9 on the gameplay clip vs 0 for ffmpeg);
> the ffmpeg backend and DEPENDENCY_MISSING path are unchanged. README notes
> "Verified with PySceneDetect 0.7+"; the optional dependency is now `>=0.7`.
> (Originally reported below.)

**Symptom**
`clipwright_detect_scenes` with `backend="pyscenedetect"` returns
`SUBPROCESS_FAILED` against PySceneDetect 0.7 (the current release). In the
dogfood this also cascaded: the highlight pipeline (`scene → text → overlay →
bgm → render`) failed at the scene step and produced no `highlight.mp4` until the
chain was re-run with the ffmpeg backend.

**Root cause**
`_detect_with_pyscenedetect` invokes
`scenedetect -i <media> detect-content --threshold <t> list-scenes -c` and parses
the **stdout** as CSV. PySceneDetect 0.7's `list-scenes` has **no `-c` option**
(`Error: No such option '-c'`); it writes the CSV to a **file**
(`$VIDEO_NAME-Scenes.csv`) and prints only a human-readable table to stdout. The
backend was only ever validated with mocked CLI-arg assertions plus the
`DEPENDENCY_MISSING` path — it had **never been run against a real `scenedetect`
binary** before this dogfood.

**Note**
The parser itself is correct: PySceneDetect 0.7's CSV file carries the expected
`Start Time (seconds)` header, and a manual run found 11 content-aware boundaries
on the clip (vs 0 for the ffmpeg backend). Only the stdout-vs-file invocation is
wrong.

**Contained fix**
Drop `-c`; run `list-scenes -o <tempdir> [--skip-cuts] [-q]`, then read
`<tempdir>/<media_stem>-Scenes.csv` and feed it to the existing
`parse_pyscenedetect_csv`; clean up the tempdir. Update the mocked arg-assertion
test to match, and add a real-binary e2e (gated behind a `scenedetect`-present
marker) so this class of fault is caught in future. Pin a minimum supported
PySceneDetect version in the README's optional-dependency note.

---

## Missing Features / Friction

### `clipwright-silence` cut-aware caption alignment (or caption-aware cutting)  *High*  *(silence/transcribe/render interplay)*

**What it does**
Reconciles silence-cutting with transcription so that burning captions onto a
silence-cut program does not shred the cues. Either (a) snap silence cut points to
caption-cue boundaries so cuts never fall mid-cue, or (b) offer a transcription /
caption mode that is aware of the post-cut timeline.

**Why it is needed**
The two most common edit operations in this suite — removing dead air
(`silence`) and burning transcribed subtitles (`transcribe` → `wrap` →
`render`) — fight each other today. In the dogfood, VAD cuts split the 22 cues so
badly that the render emitted **20+ warnings** ("clipped at cut boundary",
"split across cut boundary into N windows", "shifted by −Ns"). The re-timing
itself is correct (this is exactly what spec3's caption re-timing was built for),
but the *result* is fragmented captions because cue timing is anchored to the
uncut audio while the cuts slice through phrases. An AI agent currently has to
hand-orchestrate a "cut first, then transcribe the cut program" order to avoid
this — there is no primitive that makes the common single-pass combination clean.

**Design stance: tools vs. a user-authored skill**
This gap decomposes into two problems that have *different* right homes, and that
split is the whole design decision:

1. **Ordering** — "cut → render the cut program → transcribe the rendered cut →
   burn" avoids the fragmentation entirely. This is pure composition knowledge:
   the AI can already achieve a clean result by calling existing tools in the
   right order. Per this suite's standing boundary (cf. the *Out of scope* note
   that composing `silence + scene + transcribe` "remains the agent's job"),
   baking the ordering into a new tool would violate single-responsibility. It
   belongs in orchestration, **not** in a primitive.
2. **Snapping** — when a genuine *single pass* is required, nudge silence cut
   points onto caption-cue boundaries so cuts never fall mid-cue. The AI cannot do
   this by orchestration alone (silence has no cue input), so this is the one part
   that legitimately needs a tool capability.

The UX trap to avoid: making each user *author* the orchestration skill themselves
offloads the hardest part (the implicit composition knowledge) onto every user and
only helps sophisticated ones — a regression for an AI-first product whose value is
"an AI can just use it correctly." So the orchestration knowledge should be carried
by clipwright (hints + a shipped reference skill), not invented per user.

**MCP tool name(s)**
None required for the recommended layers below. Only the (later, optional) snapping
primitive touches a tool: extend `clipwright_detect_silence` with an optional
cue-boundary input, or add a `clipwright_remap_captions`-style helper (spec3 noted
it for `wrap`/core) to re-segment cues to the cut grid.

**Implementation hints (layered — top layers help every AI user with zero setup)**
- **Layer 1 — the tool teaches the right usage (highest priority, low cost).**
  When `clipwright-render` detects cue fragmentation (the same condition that emits
  the 20+ re-timing warnings), make the warning `hint` *prescribe the clean order*
  ("this source cuts through captions; render the cut program first, then
  transcribe the rendered cut, then burn"). This is a quality improvement to an
  existing hint, not a new responsibility, and lets even an AI with no skill
  self-correct. This is the primary win.
- **Layer 2 — ship a reference editing skill / doc, don't make users invent one.**
  The full "cut → render → transcribe → burn" workflow belongs in a skill, but
  clipwright should *distribute* it as an official reference that users can adopt
  or fork to control MCP usage — rather than requiring each user to write it.
- **Layer 3 — snapping primitive, only when single-pass is a hard requirement.**
  Accept an optional `.srt`/cue list in `clipwright-silence`; when a proposed cut
  falls inside a cue, nudge it to the nearest inter-cue gap within a tolerance, or
  drop the cut if no gap is near. Deprioritised: once Layer 1 lands, demand for a
  true single-pass should drop sharply.
- Keep all timing in `RationalTime`; reuse the kept-range map render already
  computes for re-timing.

---

### Content-aware / subject-tracking reframe  *Medium–High*  *(reframe extension)*

**What it does**
Drives the crop window for aspect-ratio conversion by content (motion /
saliency / face or HUD tracking) instead of a fixed anchor, so that converting
16:9 → 9:16 keeps the action in frame as it moves.

**Why it is needed**
`clipwright-reframe` today offers `crop` (static anchor), `pad`, and `blur_pad`.
The dogfood's `vertical_short.mp4` used `blur_pad` (centred), which is safe but
leaves the gameplay action drifting out of the 9:16 window whenever it moves off
centre. Vertical short-form is a primary distribution target, and competing tools
(auto-reframe in Premiere / Descript / Opus) make dynamic tracking the baseline
expectation. Static reframe meaningfully caps the quality of the shorts use case.

**MCP tool name(s)**
None — extend `clipwright_reframe` with a tracking mode; render realises a
time-varying crop window.

**Implementation hints**
- Detect a per-time region of interest (e.g. via ffmpeg scene/motion stats, or an
  external saliency/face CLI invoked as a separate process to preserve
  license-independence) and store a keyframed crop-centre track in
  `metadata["clipwright"]["reframe"]`.
- render maps the keyframed centre to a `crop` with time-varying `x`/`y`
  expressions (interpolated), composed before drawtext/overlay as today.
- Start with motion-centroid tracking (no ML dependency); leave face/HUD tracking
  as a later opt-in. May overlap "out of scope" concerns (cf. multi-cam) — scope
  to motion-only first.

---

### Scene-driven frame extraction  *Medium*  *(frames extension)*

**What it does**
Lets `clipwright_extract_frames` emit one representative frame per detected scene
by consuming a scene-annotated OTIO, instead of only interval/keyframe sampling.

**Why it is needed**
`scene` produces boundary annotations and `frames` extracts stills, but there is
no way to combine them into the obvious "one thumbnail per shot" contact sheet —
the agent must read the OTIO, compute midpoints, and call frames at explicit
timestamps by hand. With the content-aware scene backend now finding real shot
boundaries, scene-driven thumbnails are a natural, cheap composition.

**MCP tool name(s)**
None — add a `mode="scenes"` to `clipwright_extract_frames` that reads scene
boundaries from the timeline/OTIO.

**Implementation hints**
- Accept a scene-annotated `timeline` (or a boundary list); for each scene emit a
  frame at the scene midpoint (or first frame). Reuse the existing extraction
  path; only timestamp selection changes.
- Mirrors how other tools already consume `metadata["clipwright"]` annotations.

---

### Large-source working-clip ergonomics (trim co-location)  *Medium*  *(trim / path policy)*

**What it does**
Makes it ergonomic to carve a small working clip out of a large external source
without copying the source or scattering outputs.

**Why it is needed**
`clipwright-trim` requires its output `.otio` to live in the **same directory as
the media**. For a 2.0 GB source that lives outside the working tree, the dogfood
had to write the trim `.otio` next to the source (in the source's directory) and
have render write the working `clip.mp4` into the work dir — a contortion just to
select a 3-minute window. A 36-/52-minute raw capture is exactly the common case
(spec3's whole motivation for adding `trim`), and the co-location rule fights it.

**MCP tool name(s)**
None — relax `clipwright_trim`'s output-location rule, or add an explicit staging
concept.

**Implementation hints**
- Allow the trim output `.otio` to live in any writable directory while still
  referencing the source by absolute path (render already accepts output anywhere,
  so the asymmetry is the issue). Keep the output≠source and existence checks.
- If co-location is a deliberate safety boundary, document the "trim writes beside
  source, render materialises into the work dir" staging pattern explicitly, and
  consider a `working_dir` option that stages a small proxy.

---

### Cross-tool path-boundary & I/O-contract consistency  *Medium*  *(suite-wide DX)*

**What it does**
Unifies the per-tool path-boundary rules and clarifies each tool's media-vs-
timeline input contract so an agent orchestrating many tools does not have to
memorise per-tool exceptions.

**Why it is needed**
Building the dogfood's ~20-call pipeline by hand exposed three inconsistent
boundary policies: `trim` requires *output beside media*; `sequence`/`overlay`
require *sources under the output `.otio` directory*; `render` allows *output
anywhere (only output≠source)*. Layered on top is the create / accumulate /
transform distinction (which tools take `media`, which take `timeline`, which take
both) — non-obvious and easy to get wrong across a long chain. This is friction
for the AI-first design: the orchestrating agent is the user, and inconsistent
contracts raise its error rate.

**MCP tool name(s)**
None — a cross-cutting convention + docs change, possibly a shared validation
helper in core.

**Implementation hints**
- Document one boundary policy and converge tools onto it (e.g. "sources may live
  anywhere readable; outputs go to the caller-chosen dir; output≠any source").
- Standardise the input-contract vocabulary in tool docstrings: every tool states
  whether it *creates* (media→new OTIO), *accumulates* (media+timeline→OTIO), or
  *transforms* (timeline→OTIO), reusing the existing distinct-OTIO rule.

---

### Hardware decode for filter-heavy graphs  *Low*  *(render extension; already acknowledged)*

**What it does**
Keeps decode on the GPU even when CPU filters (eq/drawtext/overlay/xfade) are in
the graph, via full HW↔HW filtergraphs or targeted `hwdownload` placement.

**Why it is needed**
spec3 scoped `hwaccel_decode` v1 to download frames to system memory before CPU
filters, so the common filter-heavy render (color + text + overlay + bgm) still
decodes on CPU. In the dogfood, hardware decode was therefore only used on the
pure-transcode working-clip render. This is a known, already-documented limit;
recorded here for completeness as the remaining HW-acceleration gap.

**MCP tool name(s)**
None — extend `clipwright_render`.

---

## Out of scope / environmental (not gaps)

| Item | Reason |
|------|--------|
| GPU transcription speed | `transcribe` ran CPU at 1.2× realtime (149.6 s for 180 s) only because the test box has a CPU `whisper.cpp` build. The GPU/Metal wiring shipped in spec3 (transparent, env-selected); pointing `CLIPWRIGHT_WHISPER` at a CUDA build is the fix. Not a clipwright gap. |
| BGM / music generation | Sourcing a music bed is the agent's job; clipwright provides the mixing/ducking primitive (`add_bgm`), which worked. Baking a generator in fights the AI-first design. |
| Overlay/logo asset creation | Providing the PNG is the agent's job; `add_overlay` consumed it correctly. |
| Auto-highlight / one-click edit | Composing silence + scene + transcribe into highlight selection remains the agent's job (spec3). |

---

## Tool coverage in this dogfood

All 17 satellite tools + core were exercised over real stdio MCP. 15 worked
cleanly; `scene` worked on the ffmpeg backend but failed on the PySceneDetect
backend (D2); `render` worked except that the transition path emitted unplayable
4:4:4 (D1). `stabilize` was intentionally skipped (shake-free capture).

```
core         ✓    speed        ✓
trim         ✓    text         ✓
silence      ✓ (VAD + energy)  overlay      ✓
scene        ✓ ffmpeg / ✗ pyscenedetect (D2)   bgm     ✓
frames       ✓    reframe      ✓
transcribe   ✓ (CPU)           sequence     ✓
wrap         ✓    transition   ✓ annotate / render output 4:4:4 (D1)
loudness     ✓    render       ✓ except transition chroma (D1)
noise        ✓    stabilize    — intentionally omitted (game capture)
color        ✓
```

---

## Priority summary

1. ~~**D1 — render transition 4:4:4** (High): ships unplayable deliverables; contained `format=yuv420p` fix.~~ ✅ **FIXED (render 0.11.1)** — `-pix_fmt yuv420p` pinned in `_build_ffmpeg_args()`.
2. **G — cut-aware caption alignment** (High): the silence+subtitle combination degrades caption quality today. Split by home: the *ordering* fix is orchestration, not a tool — so Layer 1 makes render's fragmentation hint prescribe the clean "cut → render → transcribe → burn" order (helps every AI user, zero setup), Layer 2 ships that workflow as an official reference skill rather than making each user author one, and Layer 3 adds a cue-boundary snapping input to `silence` only if a true single pass is ever required.
3. ~~**D2 — scene PySceneDetect 0.7 incompat** (Medium): the content-aware backend is dead against current PySceneDetect; contained CSV-file fix.~~ ✅ **FIXED (scene 0.2.1)** — `list-scenes -o <tmpdir>` CSV-file read.
4. **Content-aware reframe** (Medium–High): static vertical reframe caps shorts quality.
5. **Scene-driven frames** / **trim ergonomics** / **path-policy consistency** (Medium): composition and DX gaps.
6. **HW decode for filter graphs** (Low): known spec3 limit.
