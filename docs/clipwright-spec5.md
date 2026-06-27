# Clipwright — Defects & Missing Features (Round 5)

> Companion to `clipwright-spec.md`, `clipwright-spec2.md`, `clipwright-spec3.md`,
> and `clipwright-spec4.md`. The features and defects catalogued in spec4 (the
> render transition 4:4:4 fix, the PySceneDetect 0.7 fix, content-aware
> motion-tracking reframe, scene-driven frame extraction, and the unified
> `clipwright.pathpolicy` boundary module) are all shipped. This document
> catalogues the **next** set of gaps, identified from the first full-suite
> dogfood run on **handheld, moving-subject footage with a voiceover** — a source
> profile the prior gameplay dogfood (shake-free screen capture, no real handheld
> motion) could not exercise. It records both the **defects** that this run
> surfaced (which in-process and mocked tests had missed) and the **missing
> features / capability gaps** for a general video-editing toolset.

---

## Verification context (what surfaced these gaps)

The spec3/spec4 dogfoods used a 52-minute gameplay capture: shake-free, single
continuous shot, with commentary audio. That profile is exactly wrong for three
shipped-but-never-dogfooded capabilities, so this round used a deliberately
different source:

- **`clipwright-stabilize`** was *intentionally omitted from every prior dogfood*
  ("shake-free game capture, where vidstab introduces drift/crop"). It had never
  run end-to-end on real handheld shake over real MCP.
- **`clipwright-reframe` `mode="track"`** (subject-following crop, shipped in
  spec4) needs a moving subject to mean anything.
- **`clipwright-frames` `scene_sample`** (one thumbnail per shot, shipped in
  spec4) needs real shot cuts.

A composite **handheld travel-vlog** source was built (with ffmpeg only — not a
clipwright operation) from free-license assets:

```
5 Coverr handheld people clips (jog / marina / selfie / watch / breathing)
  -> concatenated to 1920x1080 @ 30fps  (5 distinct shots => real scene cuts
     + genuine handheld motion)
  + a public-domain LibriVox narration muxed as a voiceover
     (real speech => silence / transcribe / wrap / loudness / noise have content)
  + a separate public-domain music bed for clipwright-bgm ducking
```

All assets are CC0 / Public Domain (Coverr free-license clips, LibriVox PD
narration, a Public-Domain-Mark music track). The source (`source_vlog.mp4`,
80.1 s) was never modified. A 77 s working clip was produced first with
`clipwright-trim` → `clipwright-render` (NVENC + hardware decode), and every other
tool operated on that clip, over real stdio MCP.

### What the run confirmed (on real handheld footage)

- **`stabilize`** ran `vidstabdetect` on real handheld shake and `render`
  materialised a `vidstabtransform` pass — its first real end-to-end dogfood.
- **`reframe mode="track"`** produced an 80-keyframe subject-following track
  (`cx` swept 0.364→0.696, i.e. the crop centre followed the subject) and `render`
  materialised a 1080×1920 vertical that keeps the action in frame.
- **`frames mode="scene" scene_sample="midpoint"`** emitted exactly one thumbnail
  per detected shot (12 frames for 12 PySceneDetect boundaries).
- **`render` transition path stayed `yuv420p`** on real footage (the spec4 D1
  4:4:4 fix holds): `program.mp4` (sequence → dissolve → render) is 4:2:0.
- **PySceneDetect** found 12 content-aware boundaries where the ffmpeg `scdet`
  backend found **0** on the same clip (the spec4 D2 fix works on real cuts).
- **`transcribe`** transcribed the real English voiceover (12 segments, CPU
  1.37× realtime).

Two of these exercises also exposed **defects**, and a third capability
(`silence` + caption burn over a non-CJK voiceover) exposed a **friction gap**.
All three are the kind of fault that only a real-binary, real-MCP run on this
source profile can catch — in-process and mocked tests passed for every path.

---

## How to Read This Document

- **Defects** — something shipped but broken. Each lists the symptom, the root
  cause, the blast radius, and a contained fix.
- **Missing features / friction** — a capability gap. Each follows the
  spec2/spec3/spec4 structure (What it does / Why it is needed / MCP tool name(s)
  / Implementation hints / Priority).

### Priority definitions

| Priority | Meaning |
|----------|---------|
| **High** | Ships broken output, or blocks/degrades a common AI-assisted editing workflow today; surfaced as a hard failure or a correctness trap during verification |
| **Medium** | Valuable for quality or reach; can be deferred without blocking basics |
| **Low** | Niche, environmental, or already acknowledged as out-of-scope for a prior version |

---

## Defects (found by the round-5 dogfood)

### D1. Timeline-source match resolves a relative OTIO media reference against the **process CWD** (regression from spec4 #5)  *High*

**Symptom**
The canonical "stack detect annotations onto one timeline" workflow —
`detect_color → detect_loudness → detect_noise → detect_scenes(timeline=…) → …` —
fails at the **second** step. `clipwright_detect_loudness`, given
`media=…/clip.mp4` and `timeline=…/h1.otio` (the OTIO that
`clipwright_detect_color` just wrote *for that same `clip.mp4`*), returns:

```
INVALID_INPUT: Timeline source file does not match input media.
               timeline source: clip.mp4 / media: clip.mp4
```

Both sides name the identical file, yet the equality check fails. In the dogfood
this cascaded: `noise`, `scene(timeline=…)`, `text`, `overlay`, `bgm`, and the
final `render` all failed with `FILE_NOT_FOUND` (each consumed an OTIO the prior,
failed step never wrote), so **`highlight.mp4` was never produced**.

**Root cause**
After the spec4 #5 path-policy unification, OTIO media references for files inside
the OTIO directory tree are stored as **relative POSIX paths** (`media_ref_for_otio`),
e.g. `target_url: "clip.mp4"`. The per-tool "B-4" match check was **not** updated
for relative refs — it resolves the stored `target_url` against the **current
working directory**:

```python
# clipwright-loudness/src/clipwright_loudness/loudness.py  (~L388-409)
target_url = next(iter(urls))           # "clip.mp4"  (relative to the OTIO dir)
tl_source = Path(target_url).resolve()  # -> <CWD>/clip.mp4   <-- WRONG base
media_resolved = media_path.resolve()   # -> <OTIO dir>/clip.mp4
if tl_source != media_resolved:         # mismatch whenever CWD != OTIO dir
    raise ClipwrightError(INVALID_INPUT, "Timeline source file does not match…")
```

The relative `target_url` must be resolved against the **OTIO file's directory**
(the timeline path's parent), not the CWD. The dogfood ran from
`clipwright_test/` while the OTIO + media lived in `_dogfood_spec5/`, so the
relative resolve pointed at the wrong directory and the equality failed.

**Blast radius**
The same buggy pattern (`Path(target_url).resolve()` against CWD) exists in **four
tools** — every "media + existing timeline" annotator that validates the source:

- `clipwright-color`   (`color.py` ~L350) — latent: only fires when given a timeline
- `clipwright-loudness`(`loudness.py` ~L392)
- `clipwright-noise`   (`noise.py` ~L326)
- `clipwright-stabilize`(`stabilize.py` ~L332)

Any agent that builds a multi-annotation timeline and runs the tools from a
directory other than the OTIO's directory hits this. It passed in the spec3
dogfood only because that era stored **absolute** `target_url`s (pre-path-policy),
so `resolve()` was absolute==absolute. The spec4 path-boundary e2e exercised
`render`'s reader (which resolves relative refs against the OTIO dir correctly via
`check_media_ref`), not this separate tool-local match check — which is why CI
stayed green.

**Contained fix**
Resolve the relative `target_url` against the timeline directory before comparing,
in all four tools (and reuse a single shared helper rather than re-implementing
per tool):

```python
tl_path = Path(target_url)
if not tl_path.is_absolute():
    tl_path = (Path(timeline).parent / tl_path)
tl_source = tl_path.resolve()
```

Add a regression e2e that runs `color → loudness` (or any media+timeline pair)
**from a CWD different from the OTIO directory**, asserting `ok: true`. Consider
folding this comparison into `clipwright.pathpolicy` so it cannot drift per tool.

---

### D2. `clipwright-frames` interval mode — manifest count overstates the frames actually written  *Medium*

**Symptom**
`extract_frames(mode="interval", interval_sec=15)` on the 77 s clip reports
`Extracted 6 frame(s)` and writes a `frames.json` manifest with `count: 6` listing
`frame_00000.jpg … frame_00005.jpg` — but only **five** files exist on disk
(`frame_00000 … frame_00004`). The manifest references `frame_00005.jpg`, which
was never created. (The `scene` mode manifest, by contrast, matched disk exactly:
12 == 12.)

**Root cause**
Interval mode uses **two different sampling models** that disagree at the tail:

- The **manifest / count** is computed analytically by
  `compute_interval_timestamps(duration=77, interval=15)` → a *start-aligned* grid
  `[0, 15, 30, 45, 60, 75]` = **6** timestamps.
- The **frames** are extracted by the ffmpeg **`fps=1/15` filter**, which samples
  at period *midpoints* (≈ 7.5, 22.5, 37.5, 52.5, 67.5 s) and so emits **5**
  frames for a 77 s clip (the 6th midpoint, ≈ 82.5 s, is past EOF).

Because the manifest is derived from the analytic grid while the files come from
the fps filter, the count and the final path disagree. This is the same class of
fault as the spec3-era ffmpeg `%05d` 1-vs-0 numbering mismatch — manifest and
extractor must share one source of truth.

**Blast radius**
Every interval-mode extraction whose clip length is not an exact multiple of the
interval (the common case). An AI consuming the manifest will attempt to read a
file that does not exist.

**Contained fix**
Make interval mode extract one frame per `compute_interval_timestamps` value via
per-timestamp `-ss` single-frame extraction (exactly how `scene`/`timestamps`
mode already works and stays consistent), instead of the `fps=1/N` filter. That
makes the manifest the single source of truth and guarantees manifest == disk.
Alternatively, build the manifest from the actually-written files (glob after
ffmpeg) — but unifying on per-`-ss` extraction is preferred for consistency with
scene mode. Add an e2e asserting `manifest.count == len(glob(frame_*.jpg))` and
that every manifest path exists, for a non-multiple clip length.

---

### D3. `clipwright-stabilize` severity is `null` on real handheld `.trf` data  *Low*

**Symptom**
`detect_shake` on the real handheld clip succeeded and wrote a valid binary
`.trf`, but recorded `severity: null` with the warning *"Could not estimate shake
severity from the .trf file."* Severity is the one field an agent would use to
decide *whether* to stabilise, and it is unavailable precisely on the real
handheld footage where it is most wanted.

**Root cause (suspected)**
The severity estimator parses the `.trf` produced by `vidstabdetect`; it returns
`None` on this real, multi-shot `.trf`. Severity is documented as best-effort
(the stabilize smoke test tolerates `null`), so this is not a hard failure, but it
means the advisory signal is effectively absent in the real case.

**Contained fix**
Treat as a follow-up: verify the `.trf` parser against the current libvidstab
output format and a multi-shot transform file; if the format shifted, update the
parser; otherwise document that severity can be `null` for certain captures and
keep it advisory. Low priority — stabilisation itself works.

---

### D4. `clipwright-stabilize` apply pass ships degraded output — ghosting, resolution loss, over-smoothing (defaults-only `vidstabtransform`)  *High* — **RESOLVED**

**Symptom**
On real handheld footage the stabilized result is **worse to watch than the
original**: visible smeared "ghost" trails at the frame edges, softer/lower
apparent resolution, and a "rubber-band / swimming" feel where deliberate camera
motion is sucked out. The materialise pass runs and returns `ok: true` — there is
no signal in the envelope that the output is degraded.

**Root cause**
The render apply stage builds the `vidstabtransform` filter with **only two
parameters** and leaves every other knob at the ffmpeg default
(`clipwright-render/src/clipwright_render/plan.py:3159-3160`):

```python
vst = f"vidstabtransform=input={stabilize_basename}:smoothing={stabilize_smoothing}"
```

Each defaulted knob maps directly to one of the three complaints:

- **Ghosting / smear** ← `crop=keep` (the vidstabtransform default). It fills the
  border area exposed by stabilisation with **content from previous frames**,
  producing the classic vidstab smear trail. `crop=black` (letterbox the exposed
  edge) removes it.
- **Resolution loss / softness** ← `optzoom=1` (default) auto-zooms **in** to hide
  the exposed borders, i.e. upscales a crop → resolution drop; plus bilinear
  `interpol` softens, and there is **no `unsharp` recovery pass** (the libvidstab
  docs explicitly recommend a trailing `unsharp` because the transform softens the
  image). No `unsharp` appears anywhere in the filter chain.
- **Over-correction / swimming** ← `smoothing=30` (default;
  `clipwright-stabilize/.../schemas.py:27` and `plan.py:105`). At 30 fps that is a
  ±30-frame ≈ 2-second window, aggressive enough to absorb intentional pans and
  produce the rubber-band rebound.

This is **not an inherent vidstab limitation** — it is a minimal first-cut filter
string that never tuned the apply-side parameters. (Related but separate: the
render `yuv420p`/4:4:4 pinning known-issue can compound the perceived quality on
some edit paths.)

**Blast radius**
Every `detect_shake → render` materialisation. Because clipwright is an **AI-only**
tool — the caller is an agent that cannot visually QA the MP4 and only sees the
`ok: true` envelope — a tool that silently emits output worse than its input is a
correctness trap: the agent will "stabilise" and ship a degraded clip with full
confidence. For an AI-first product the apply defaults must be good *by
construction*, because there is no human in the loop to reject the bad frame.

**Contained fix**
Tune the apply-side `vidstabtransform` and recover sharpness, and re-baseline the
default smoothing:

1. Set `crop=black` (kill the ghosting) and `optzoom=1:zoom=0` reviewed against an
   explicit, bounded crop budget — or expose `crop` as an option defaulting to
   `black` — so borders are never filled from prior frames.
2. Append an `unsharp` pass after `vidstabtransform` to restore detail lost to
   interpolation (libvidstab's documented companion step).
3. Lower the default `smoothing` (e.g. 10–15) so intentional motion survives; keep
   it agent-overridable via `DetectShakeOptions.smoothing` (already 0–1000).
4. Validate with a real-handheld before/after e2e and, ideally, an objective
   sharpness/crop metric so regressions in apply quality are caught without a human
   viewer. Consider surfacing an apply-side **quality/crop-budget warning** in the
   render envelope so the agent gets an in-band signal when stabilisation had to
   crop or zoom heavily.

**Resolution (shipped)**
The apply-side filter is now built as:
```
vidstabtransform=input={basename}:smoothing={n}:crop=black:optzoom=1
```
- `crop=black` (kills the ghost-smear), `optzoom=1` (optimal static zoom hides the
  exposed border without the wobbling black edges that `optzoom=0` would leave),
  and the default `smoothing` re-baselined **30 → 12** (synced across
  `plan.py` `_DEFAULT_STABILIZE_SMOOTHING` / `_RenderStabilize.smoothing`,
  `clipwright-stabilize` `DetectShakeOptions.smoothing`, and the MCP server
  docstring). All additions are static literals — the CWE-78 surface
  (`_validate_stabilize_basename` allowlist, `smoothing` Pydantic bound) is unchanged.
- **`unsharp` was dropped** despite the contained-fix plan above. A real-binary e2e
  on Windows (Gyan ffmpeg 8.1.1) showed that **chaining any post-process filter
  after `vidstabtransform` in the same filtergraph crashes libvidstab with
  `0xC0000005` (ACCESS_VIOLATION)** — measured 13/15 runs with `unsharp`, vs 1/15
  without, vs 0/20 when `vidstabtransform` runs as its own pass. `unsharp`/`cas`/
  `smartblur` and `format`-between / `filter_threads=1` all reproduced the crash;
  `stdin=DEVNULL` was disproven (PIPE crashes at the same rate). For an AI-first
  tool that cannot visually QA output, **not crashing outranks marginal sharpness**,
  and `crop=black` already removes the dominant softness source (the prev-frame
  smear). A real-handheld e2e (`test_stabilize_e2e.py`, real ffmpeg) now verifies
  `ok` + artifact-on-disk + `pix_fmt=yuv420p`; a regression guard asserts `unsharp`
  is **absent** from the filter graph so the crash cannot be reintroduced.

**Spun-off backlog** (out of this fix's scope):
- **Sharpness recovery via a two-pass `unsharp` pre-pass** — the only crash-free way
  to keep sharpening is to run `vidstabtransform` as its own pass, then `unsharp` in
  a second pass (0/20 crashes). That is a render single-pass-architecture change
  (same class as the deferred DeepFilterNet multi-pass), so it is deferred.
- **Residual ~7% single-pass `vidstabtransform` crash** on this Gyan ffmpeg build
  (libvidstab build bug, present even with `unsharp` removed and on the pre-D4
  `crop=keep` path) — the e2e `skip`-guards it; tracked as a build-specific known issue.
- **D5 (security): `render` returns raw `SUBPROCESS_FAILED` messages** — see below.

---

### D5. `clipwright-render` leaks raw subprocess stderr in `SUBPROCESS_FAILED` envelopes (CWE-209)  *Low*

**Symptom / root cause**
`render.py` (`except ClipwrightError as exc: return error_result(exc.code, exc.message, exc.hint)`)
passes the raw `ClipwrightError.message` — which for `SUBPROCESS_FAILED` embeds
`stderr[:200]` from `process.run()` — straight into the MCP envelope. ffmpeg stderr
can contain absolute input paths, so the error surface can leak filesystem detail
to the agent/caller. This is pre-existing and **render-wide** (every subprocess
failure, not just stabilize); it was surfaced while reviewing the D4 e2e, which
originally keyed its Windows-crash detection off the leaked exit-code string. The
e2e was decoupled to detect by `error.code == "SUBPROCESS_FAILED"` instead, so no
clipwright code now depends on the leak.

**Contained fix**
Apply the existing `safe_subprocess_message` helper (already used on the
silence/VAD/wrap seams) to `SUBPROCESS_FAILED` before returning from `render`.
Because it changes render's global error contract, do it as its own scoped change
(check render tests that assert on error-message content). Low priority — no code
depends on the raw message and the leak is path-only.

---

## Missing Features / Friction

### Caption line-wrapping for space-delimited (Latin) languages  *Medium*  *(wrap)*

**What it does**
Lets `clipwright_wrap_captions` wrap captions for languages that break on spaces
(English, etc.), not only CJK/Thai.

**Why it is needed**
`wrap_captions` exists to insert line breaks where a language has no spaces
(budoux-style CJK/Thai segmentation). Its `language` option is constrained to
`^(ja|zh-hans|zh-hant|th)$`, so calling it on the dogfood's **English** voiceover
is a hard `validation error`. An AI that runs the natural `transcribe → wrap →
burn` chain on English captions hits a wall with no in-band hint that the right
move is to skip wrap (libass wraps Latin on spaces at render time anyway). For an
AI-first product, a tool that hard-errors on the most common caption language is
a friction trap.

**MCP tool name(s)**
None — extend `clipwright_wrap_captions`: either accept space-delimited languages
and wrap on word boundaries to `max_chars`/`max_lines`, or accept them as an
explicit **passthrough** (re-emit unchanged) so the chain never breaks. At minimum,
make the rejection message prescribe the fix ("Latin captions need no wrapping;
pass the raw .srt straight to render").

**Implementation hints**
- Reuse the existing `max_chars`/`max_lines` shaping; for space-delimited input,
  greedy word-wrap is sufficient and dependency-free.
- Keep CJK/Thai on the budoux path; branch on language class.

---

### Word-level / karaoke caption timing  *Medium*  *(transcribe / wrap / render)*

**What it does**
Emits per-word timestamps and burns word-synced ("karaoke") captions that
highlight each word as it is spoken, instead of one static block per segment.

**Why it is needed**
Whisper already exposes word timestamps; word-synced captions are the baseline
look for short-form social video (the same audience the 9:16 reframe targets).
Today the burn is segment-level only, which caps the perceived quality of the
shorts use case.

**MCP tool name(s)**
None — surface word timestamps from `transcribe`, carry them through `wrap`, and
add a karaoke styling mode to `render`'s subtitle path (ASS `\k` tags).

---

### Color grading depth: LUT / white-balance / saturation / contrast  *Medium*  *(color extension)*

**What it does**
Extends `detect_color` / the render color stage beyond a luma brightness offset to
full primary grading: white balance, saturation, contrast, and 3D-LUT application.

**Why it is needed**
`detect_color` currently measures luma and writes a brightness offset; that is a
correction, not a grade. A general editing suite is expected to do basic look
work (warm/cool balance, saturation, a LUT for a consistent look across shots).
This is the difference between "fix exposure" and "grade the video."

**MCP tool name(s)**
None — extend `clipwright_detect_color` (or a sibling `apply_look`) to annotate WB
/ saturation / contrast / `lut3d` directives; render maps them to the existing
`eq`/`curves`/`lut3d` filters.

**Implementation hints**
- Keep detection (measure) separate from the look directive (apply), per the
  detect/apply split. A LUT is a caller-provided `.cube` path (asset = agent's job).

---

### Picture-in-picture / video-on-video overlay  *Medium*  *(overlay extension)*

**What it does**
Composites a **second video** (webcam, reaction, B-roll inset) over the main
track at a position/size/time window — not just a static image watermark.

**Why it is needed**
`add_overlay` consumes a PNG (watermark/logo), which the dogfood used. But
picture-in-picture (screen + webcam, reaction insets, split-screen) is a staple of
tutorial/streamer/reaction edits and has no primitive today. The agent can only
inset still images.

**MCP tool name(s)**
None — extend `clipwright_add_overlay` to accept a video `media` source (with its
own in/out range and optional audio mix), or add `clipwright_add_pip`. Render
composes it with the existing `overlay` filter plus a scaled second input.

---

### NLE interop export: FCPXML / EDL + chapters/markers sidecar  *Medium*  *(render / core)*

**What it does**
Exports the OTIO program as an editor-interchange format (FCPXML / EDL / OTIO
itself) and/or a chapters/markers sidecar (scene boundaries + titles), so an edit
decided by clipwright can be opened in Premiere/Resolve/FCP or published as
YouTube chapters.

**Why it is needed**
clipwright already *is* an OTIO pipeline; the program timeline is rich (cuts,
transitions, scene boundaries, captions) but only ever materialises to a flat MP4.
A common real workflow is "let the AI rough-cut, then finish in an NLE," and
publishing chapters from detected scenes is a cheap, high-value composition that
exists nowhere today.

**MCP tool name(s)**
A new `clipwright_export_edl` / `clipwright_export_chapters` (transform: OTIO →
sidecar), reusing OTIO's adapters where possible.

---

### Subtitle translation  *Medium*  *(transcribe extension)*

**What it does**
Produces a translated subtitle track (e.g. English audio → Japanese captions) in
addition to the source-language transcript.

**Why it is needed**
Whisper can translate-to-English natively; broader caption localisation (any
source → a chosen target) is a high-reach feature for a captioning pipeline and a
natural extension of the existing `transcribe → wrap → burn` chain.

**MCP tool name(s)**
None — add a `translate_to` option to `clipwright_transcribe` (or a sibling
`translate_captions` that takes an `.srt`). Keep any external translation engine
out-of-process (license independence), consistent with the FFmpeg/whisper policy.

---

### Speaker diarization in transcribe  *Low–Medium*  *(transcribe extension)*

**What it does**
Labels transcript segments by speaker ("who spoke when") for interviews / multi-
person dialogue.

**Why it is needed**
Single-speaker transcription is solved; multi-speaker content (interviews,
podcasts-to-video) needs speaker turns for usable captions and for cut decisions.
Out of scope for a first pass but a clear reach gap.

**MCP tool name(s)**
None — extend `clipwright_transcribe` (optional diarization backend, separate
process).

---

### Pan-zoom (Ken Burns) on stills and B-roll  *Low*  *(render / reframe)*

**What it does**
A time-varying zoom/pan over a static frame or a held shot (the "Ken Burns"
effect), reusing the same keyframed-crop machinery that `reframe mode="track"`
already materialises.

**Why it is needed**
Adds motion to otherwise static shots/thumbnails; cheap given the time-varying
crop expressions render already supports for tracking.

**MCP tool name(s)**
None — a `mode="kenburns"` directive consumed by render's existing keyframed crop.

---

### Social export presets (platform sizing/bitrate, GIF)  *Low–Medium*  *(render extension)*

**What it does**
One-call render presets for common targets (TikTok/Reels/Shorts dimensions +
bitrate caps, animated GIF, web-optimised MP4) instead of hand-specifying every
encode option.

**Why it is needed**
The dogfood produced a 9:16 vertical via `reframe`, but matching each platform's
exact size/bitrate/duration constraints is still manual. A preset layer reduces
the agent's error surface for distribution.

**MCP tool name(s)**
None — a `preset` option on `clipwright_render` mapping to vetted encode settings.

---

## Out of scope / environmental (not gaps)

| Item | Reason |
|------|--------|
| GPU transcription speed | `transcribe` ran CPU at 1.37× realtime because the box has a CPU `whisper.cpp` build; pointing `CLIPWRIGHT_WHISPER` at a CUDA build is the fix (spec3 wiring). Not a clipwright gap. |
| Music / voiceover sourcing | Providing the music bed and narration is the agent's job; clipwright mixes (`add_bgm`) and burns. Baking generators in fights the AI-first design. |
| Logo / overlay asset creation | Providing the PNG is the agent's job; `add_overlay` consumed it. (A *video* PiP source is a real gap — see above.) |
| Auto-highlight / one-click edit | Composing silence + scene + loudness into highlight selection remains the agent's job (spec3/spec4). A *scoring* primitive could assist but the decision stays orchestration. |
| HW decode for filter-heavy graphs | Known spec3/spec4 limit (CPU filters force `hwdownload`); recorded in spec4. |

---

## Tool coverage in this dogfood

18 satellite tools + core were exercised over real stdio MCP on handheld vlog
footage. The "create" path of every tool worked; the failures below are the
media+timeline match regression (D1) and the interval manifest mismatch (D2).

```
core        ✓                      speed       ✓
trim        ✓                      text        ✗ cascade from D1 (works standalone)
render      ✓ (NVENC+hwdecode;     overlay     ✗ cascade from D1 (works standalone)
              transition=yuv420p)   bgm         ✗ cascade from D1 (works standalone)
silence     ✓ (VAD 8 / energy 5)   reframe     ✓ NEW mode="track" (80 kf, follows subject)
scene       ✓ (psd 12 / ffmpeg 0)  sequence    ✓
frames      ✓ scene_sample;        transition  ✓ (4:2:0 confirmed on real footage)
              ✗ interval manifest (D2)  stabilize ✓ NEW; quality fixed (D4 RESOLVED:
                                                    crop=black/optzoom=1/smoothing=12,
                                                    unsharp dropped); severity=null (D3)
transcribe  ✓ (en, CPU 1.37x)      color       ✓ create path; ✗ with timeline (D1)
wrap        — N/A (CJK/Thai only; English friction)   loudness ✗ (D1)
                                    noise       ✗ (D1 cascade; same latent bug)
```

**Deliverables produced:** `clip.mp4`, `program.mp4`, `stabilized.mp4`,
`vertical_track.mp4`, `frames_scene/` (12), `frames_interval/` (5, but manifest
claims 6 — D2).
**Not produced:** `highlight.mp4` (D1), `subtitled.mp4` / `speed2x_sub.mp4`
(blocked by the wrap-language friction; the clean fix is to burn the raw `.srt`
directly for Latin captions).

---

## Priority summary

1. **D1 — timeline-source match resolves a relative ref against CWD** (High):
   breaks the multi-annotation timeline workflow across `color` / `loudness` /
   `noise` / `stabilize`; regression from the spec4 #5 path-policy unification.
   Contained fix: resolve `target_url` against the OTIO directory (ideally folded
   into `clipwright.pathpolicy`).
2. **D4 — stabilize apply pass ships degraded output** (High) — **RESOLVED**:
   shipped `vidstabtransform=...:smoothing=12:crop=black:optzoom=1` (ghosting +
   over-smoothing fixed, clean frame via optimal static zoom). `unsharp` was
   **dropped** after a real-binary e2e showed any post-`vidstabtransform` filter
   crashes libvidstab on Windows Gyan ffmpeg (0xC0000005, 13/15); not-crashing
   outranks marginal sharpness for an AI-first tool. Real ffmpeg e2e verifies
   `ok`/artifact/`yuv420p` + an `unsharp`-absence regression guard. Spun off:
   two-pass `unsharp` pre-pass, residual ~7% libvidstab build crash, and **D5**.
3. **D2 — frames interval manifest overcounts vs fps-filter output** (Medium):
   manifest lists a non-existent final frame; extract interval frames per
   `compute_interval_timestamps` via `-ss` so manifest == disk (as scene mode does).
4. **Caption wrap for Latin languages** (Medium): `wrap_captions` hard-errors on
   English; accept space-delimited wrapping or an explicit passthrough.
5. **Word-level/karaoke captions · color-grading depth · video PiP · NLE
   interop · subtitle translation** (Medium): reach/quality features for a general
   editing suite.
6. **D3 — stabilize severity null** (Low) · **D5 — render raw stderr in
   `SUBPROCESS_FAILED` (CWE-209)** (Low) · **two-pass `unsharp` pre-pass · residual
   libvidstab build crash · diarization · Ken Burns · export presets** (Low–Medium):
   follow-ups.
