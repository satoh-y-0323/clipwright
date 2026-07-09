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

### D1. Timeline-source match resolves a relative OTIO media reference against the **process CWD** (regression from spec4 #5)  *High* — **RESOLVED**

**Resolution (shipped — suite v0.26.0):** the per-tool B-4 match block (which
resolved the stored relative `target_url` against the process CWD) was replaced in
all four tools by a single shared core helper
`clipwright.pathpolicy.check_timeline_source_matches(target_url, media_path, otio_dir)`
that resolves a relative reference against the **OTIO file's directory**, not the
CWD, before comparing (reusing `_normalize_sep` + `_canon`). Boundary/symlink
checks stay delegated to `check_media_ref` (called first); the new helper does the
equality check only, and the error message is canonical across all four tools with
no filename interpolation (CWE-209). Verified by a real-stdio-MCP e2e that runs
`detect_color → detect_loudness → detect_noise → detect_shake → render` from a CWD
different from the OTIO directory (14/14 assertions, including a negative control
that a genuinely different media is still rejected with `INVALID_INPUT`). Ships as
`clipwright` 0.5.0 / `clipwright-color` 0.2.1 / `clipwright-loudness` 0.3.1 /
`clipwright-noise` 0.3.1 / `clipwright-stabilize` 0.4.1. CI green on 3 OSes.

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

### D2. `clipwright-frames` interval mode — manifest count overstates the frames actually written  *Medium* — **RESOLVED**

**Resolution (shipped — suite v0.27.0 / `clipwright-frames` v0.3.1):** interval mode now
extracts one frame per `compute_interval_timestamps` value via per-`-ss` single-frame
extraction — the exact path `scene`/`timestamps` mode already use — instead of the `fps=1/N`
filter. The list of successfully extracted frames is the single source of truth for the
`frames.json` `count` and the frame paths, so `manifest.count` always equals the number of
files on disk and every manifest path exists. The now-unused `build_fps_command` helper (and
its tests) were removed. Verified by an integration e2e on a non-multiple clip length (9 s /
4 s, where the old fps-filter path dropped the tail frame) asserting `manifest["count"] ==
len(glob(frame_*.jpg))` and that every manifest path satisfies `os.path.exists`, plus a real
stdio-MCP e2e. Hardening: because per-`-ss` extraction spawns one ffmpeg process per frame, a
frame-count guard (O(1) pre-estimate before list materialisation + exact post-count check)
rejects pathological tiny-`interval_sec`-over-long-clip inputs (CWE-400) without leaking any
path or subprocess output.

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

### D3. `clipwright-stabilize` severity is `null` on real handheld `.trf` data  *High (AI-usability)* — **RESOLVED**

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
keep it advisory. **Re-prioritised from Low to High (AI-usability) by D6**: severity
is the signal an agent needs to *decide whether to stabilise at all*. Without it the
agent stabilises footage that does not need it (the D6 over-stabilization judder), so
fixing severity — and exposing a clear skip recommendation — is now the primary lever
for making stabilize usable by an AI. See D6.

**Resolution (shipped — stabilize 0.4.0)**
The real root cause was a parser fault, not "certain captures". The estimator read
the whole `.trf` body as a flat `float64` array; the structured binary records
(`int32` headers / field values) were misread as ~1e308 doubles, so `sum()`
overflowed to `inf` and the `isfinite` guard returned `None` — on **every** real
binary `.trf`, not just multi-shot. The unit tests passed only because they fed a
fabricated flat-double blob. Two things were fixed:

1. **Correct structural parse + portable formats.** libvidstab serialises the `.trf`
   in **two formats depending on the build**: a binary `TRF1` layout (the Gyan
   Windows ffmpeg) and a **text `VID.STAB` format** (`Frame N (List M [(LM vx vy fx
   fy size contrast match),…])` — Linux apt / macOS brew builds). `_estimate_severity`
   now dispatches on the magic and parses both, extracting the per–local-motion
   translation `(vx, vy)`. Real `.trf` fixtures for **both** formats (the binary one
   from Windows, the text one generated in an Ubuntu 24.04 / libvidstab 1.1.0 Docker
   container) are committed and pin the parser. This was caught by CI: the binary-only
   first cut passed Windows/macOS but failed ubuntu (`severity=None`); the fix was
   validated on real Linux via Docker before re-push.
2. **Outlier-robust aggregation (median).** Severity is the **median** of per-frame
   median translation magnitude, normalised by `_NORM_PX`. Median (not mean) is
   essential because multi-shot footage injects huge apparent motion at scene cuts
   (the real dogfood vlog peaks at ~109 px/frame, which dragged the *mean* to a
   spurious 0.246 → "apply"); its *median* is 1.68 px → 0.033 → correctly **skip**.

A `recommendation` field (`"skip"` / `"apply"`, advisory) was added to the
`detect_shake` response and `StabilizeDirective` (threshold `_SEVERITY_APPLY_THRESHOLD
= 0.08`, calibrated on real fixtures); `severity=null` still safe-defaults to `apply`
with a warning, and the directive is always written so the **agent** makes the final
call (detect/apply separation preserved). See D6.

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
The apply-side filter is built as:
```
vidstabtransform=input={basename}:smoothing={n}:crop=black:optzoom=1,unsharp=5:5:0.8:3:3:0.4
```
and `render` adds `-threads 1` (before `-i`) **only when a stabilize directive is
present** (`plan.stabilize_cwd is not None`, in both the `render_plan` and
`_render_inner` command paths). Each piece addresses one complaint:
- `crop=black` — kills the ghost-smear (no prev-frame border fill).
- `optzoom=1` — optimal static zoom hides the exposed border without the wobbling
  black edges that `optzoom=0` would leave.
- `unsharp=5:5:0.8:3:3:0.4` — restores detail lost to interpolation (libvidstab's
  documented companion step).
- default `smoothing` re-baselined **30 → 12** (synced across `plan.py`
  `_DEFAULT_STABILIZE_SMOOTHING` / `_RenderStabilize.smoothing`, `clipwright-stabilize`
  `DetectShakeOptions.smoothing`, and the MCP server docstring).

All filter/flag additions are static literals — the CWE-78 surface
(`_validate_stabilize_basename` allowlist, `smoothing` Pydantic bound) is unchanged.

**Root cause of the crash (confirmed) → why `-threads 1`**
The first cut tried to ship `unsharp` and crashed on Windows (Gyan ffmpeg 8.1.1)
with `0xC0000005` (ACCESS_VIOLATION). Empirical bisection plus the upstream report
pinned it: **[vid.stab issue #144](https://github.com/georgmartius/vid.stab/issues/144)**
— `vsTransformPrepare` writes into the caller-owned frame buffer when in-place and
copy calls are mixed, **corrupting the decoder's reference frames (B-frame sources)**.
This is a use-after-free exposed by **frame-level codec multithreading**, not a
clipwright bug and not `stdin` (PIPE crashes identically). The evidence:
- `-threads 1` (serialize decoder frame threading) → **0/27 crashes**; `-filter_threads 1`
  alone → no effect (13/15 still crash). So the `-threads` axis is the fix.
- Downstream-filter type only changes the *probability* (unsharp 13/15, hflip 3/15,
  setpts/null/copy ~0) because heavier consumers widen the in-place/copy race window.
- Cost of `-threads 1` is ~+4% on this filter-bound workload (render is filter-bound),
  so serializing the stabilize render is cheap.
`-threads 1` therefore lets `unsharp` come back **and** eliminates the residual
single-pass crash too. A real-handheld e2e (`test_stabilize_e2e.py`, real ffmpeg)
verifies `ok` + artifact-on-disk + `pix_fmt=yuv420p` and runs a crash-regression
loop (15×) that stays green.

**Spun-off backlog**:
- **Upstream tracking: vid.stab #144** — open, library unmaintained
  ([#133 "new maintainer needed"](https://github.com/georgmartius/vid.stab/issues)).
  ffmpeg 8.1.1 is already the latest, so there is no upgrade that fixes it; the
  `-threads 1` workaround stands until upstream is fixed. (The earlier "two-pass
  unsharp pre-pass" idea is no longer needed — `-threads 1` keeps single-pass.)
- **Optional: a render-wide threads policy / explicit `RenderPlan` field** if more
  stabilize-only global options accrue (currently `plan.stabilize_cwd is not None`
  is reused as the condition).
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

### D6. Stabilize is unusable-by-default on low-motion footage — over-stabilization + no severity gate  *High (AI-usability)*  — **RESOLVED**

**Symptom**
After D4 (Option B) shipped, re-running the *same* real dogfood clip (`clip.mp4`,
a calm hand-held selfie vlog) through the new code still produced output that "is
not at a usable level": slight residual ghosting and a choppy / "swimming" judder,
even though D4 objectively improved sharpness and removed the edge smear. The owner
correctly flagged that this clip may simply be **the wrong kind of footage** — the
stabilizer's real use case is fast-action footage with genuine shake at normal
playback speed, not a slow, near-static vlog.

**Root cause (confirmed by objective measurement) — this is footage-fit, not a tunable bug**
- **No speed/frame bug.** Source, pre-D4 output, and D4 output are all identical
  30 fps / 2310 frames / 77 s. The "slow" impression is the *content* (a calm
  vlog), not a render speed regression.
- **The judder is over-stabilization of low-motion frames.** `mpdecimate` (drops
  frames near-identical to the previous one) keeps **1860 / 2310** unique frames in
  the source — i.e. ~450 frames are essentially static (it is a low-motion clip) —
  but **2214 / 2310** in the stabilized output. The stabilizer nudges even the
  static frames by sub-pixel amounts to chase tiny detected motion, so frames that
  were identical now differ slightly frame-to-frame. That micro-warp reads as
  "swimming" / judder. This is inherent to stabilising footage that does not need
  it; it cannot be removed by parameter tuning without also disabling the correction
  that footage which *does* shake actually needs.
- **D4 itself is a correct improvement** for footage that needs stabilisation:
  sharpness (Laplacian variance, 7-frame mean) went original **8.5** → pre-D4 **7.4**
  (softer than the source — matches the "looks soft" complaint) → D4 **14.0**
  (restored and beyond), with no crash and `yuv420p` output.

**Decision: do NOT tune to this dogfood clip.** A calm selfie vlog is out of the
stabiliser's target domain; optimising parameters for it would overfit and degrade
the real use case (action footage). The bad experience here is "stabilisation was
applied to footage that did not need it," and the right AI-first fix is to let the
agent *decide not to stabilise* such footage — i.e. a working severity signal
(D3), not a parameter change.

**Plan (next session — via the standard C3 workflow, not ad-hoc):**
1. **Validate D4 on representative footage.** Acquire / synthesise footage in the
   stabiliser's actual domain: strong, high-frequency translation+rotation shake at
   normal playback speed (action-cam / running style). Measure that stabilisation
   *reduces* inter-frame motion on footage that needs it (e.g. residual-motion or
   inter-frame-difference metric before/after), not just that it runs. Real action
   footage is preferred over synthetic where available.
2. **Promote and fix D3 (severity gating) as the primary AI-usability fix.** Make
   `detect_shake` return a usable `severity` (and ideally a recommendation flag) so
   the calling agent can skip stabilisation on low-shake footage. A working severity
   would have prevented this entire bad experience. Re-prioritised from *Low* to
   *High (AI-usability)* on the strength of this finding. See D3.
3. Only after (1) confirms D4 on representative footage and (2) gives the agent a
   skip signal, decide whether the suite stabilize release (currently staged as
   `v0.24.0`, render 0.15.0 / stabilize 0.3.0) ships as-is or with the severity gate
   folded in.

**Resolution (shipped — stabilize 0.4.0)**
Both plan steps were executed via the standard C3 workflow. The fix is the one the
decision called for — a working severity signal so the **agent** declines to
stabilise footage that does not need it — not a parameter change to this clip:

1. **D3 severity now works (and is the gate).** With the corrected, format-portable
   parser and the **median** aggregation (see D3), the calm dogfood vlog measures
   `severity = 0.033` → `recommendation = "skip"`; genuinely shaky footage measures
   higher → `"apply"`. Crucially, the median makes this clip read as *low motion*
   despite the scene-cut spikes that fooled the mean — exactly the "this footage does
   not need stabilising" signal that was missing. The agent can now skip it.
2. **D4 validated on footage that needs it.** A `@pytest.mark.integration` D6 test
   stabilises synthetic high-shake footage and confirms stabilisation *reduces*
   motion by re-running `vidstabdetect` on the **output**: residual severity drops
   `0.427 → 0.167` (~61 %). (Inter-frame pixel diff is unusable here because `optzoom`
   zoom-in and `unsharp` perturb every pixel; re-measuring residual shake is
   zoom/sharpen-invariant.) The calm clip stays in the `skip` domain and is left
   untouched. D4 is confirmed correct on its target domain; this clip was simply out
   of domain, as the owner flagged.

The bad experience ("stabilisation applied to footage that did not need it") is now
prevented by construction: an AI reads `recommendation` and skips. No parameters were
overfit to the dogfood clip.

**Release status:** shipped as suite **v0.25.0** (stabilize 0.4.0 / render 0.15.0).
The earlier `v0.24.0` tag was never pushed, so the suite version was rolled forward to
v0.25.0 to bundle the D4 render fix (render 0.15.0) with the D3/D6 stabilize fix
(stabilize 0.4.0) in a single PyPI publish. CI is green on all three OSes (the
binary-only first cut failed ubuntu and was fixed — see D3), reviews are clean, and
the §4 real-MCP e2e passes.

---

### D7. `accumulate` tools validate their source media with plain `.exists()` — symlink components are followed, not rejected (CWE-59)  *Low*

**Symptom / root cause**
Surfaced while shipping the boundary-guard rollout (suite v0.32.0). The pathpolicy
hardening in v0.31.0 routed `frames` `scene_timeline` and `wrap` `input` through
`clipwright.pathpolicy.validate_source_file` (islink-before-resolve rejection over
**all** path components, ADR-PP-2). The **accumulate**-type tools were not part of
that pass and still validate their source inputs with a plain existence check that
**follows** symlinks:
- `clipwright-bgm/src/clipwright_bgm/bgm.py` (~L107/L115): `timeline_path.exists()`
  and `bgm_path.exists()` only.
- `clipwright-overlay/src/clipwright_overlay/overlay.py` (~L176–182):
  `Path(options.image_path).resolve().exists()` — `resolve()` follows any symlink
  before the existence check, so a symlinked image (or a symlink in an intermediate
  directory) passes.

A source path that is (or traverses) a symlink is therefore accepted at annotation
time instead of being rejected with `PATH_NOT_ALLOWED`, inconsistent with the
scene_timeline / wrap-input contract now shipped. Same CWE-59 class the maintainer
chose to guard in v0.31.0.

**Not a defect (recorded to avoid re-triage):** overlay accepting an image *outside*
the OTIO tree is **intentional** per ADR-PP-1 (co-location restriction removed;
`media_ref_for_otio` stores an absolute ref for outside-tree images, and `render`
re-validates via `check_media_ref` at materialisation). The stale
`overlay_e2e_smoke.py` Scenario 5 (which still expects `PATH_NOT_ALLOWED` for an
outside-tree image) is an outdated **test expectation**, not a product bug — update
the smoke, do not "fix" the behaviour.

**Contained fix**
Route accumulate-tool source inputs through `validate_source_file` (existence +
regular-file + symlink-component rejection), catching `FILE_NOT_FOUND` and
re-wrapping to each tool's existing basename-only message (CWE-209) the same way
frames/wrap do. Audit both source inputs of `bgm` (existing OTIO `timeline_path` and
new `bgm_path`) and overlay's `image_path`. Keep the outside-tree acceptance
(ADR-PP-1) intact — this change only adds the symlink guard, it does not restrict
location. *Low* — matches the threat model (single local stdio process) and the
priority the scene_timeline / wrap-input backlog items carried; it is a consistency
hardening, not an active-exploit fix.

**Resolution (shipped — suite v0.34.0)**
D7's own text and fix (shipped in suite v0.33.0 per CHANGELOG) covered only the
**accumulate**-category tools' source inputs — `bgm` (`timeline_path` + `bgm_path`) and
`overlay` (`image_path`) — routing them through `validate_source_file` so a symlinked
source component is rejected with `PATH_NOT_ALLOWED` instead of followed. This release
(suite v0.34.0) completes the same CWE-59 guard for the **transform**-category counterpart:
`clipwright-transition` (v0.2.1), `clipwright-speed` (v0.2.2), and `clipwright-text`
(v0.2.2) now validate their input timeline through the shared guard instead of a plain
`.exists()`, closing the full defect class across both tool categories. In the same pass
the `validate_source_file` + `FILE_NOT_FOUND`→basename-rewrap idiom (previously copy-pasted
across the accumulate call sites) was DRY-consolidated into the new core helper
`clipwright.pathpolicy.validate_source_or_basename(path, *, message, hint,
error_code=ErrorCode.FILE_NOT_FOUND)` (`clipwright` v0.6.0), with `PATH_NOT_ALLOWED`
propagating unchanged. See the two Cross-cutting backlog resolutions below.

---

## Missing Features / Friction

### Caption line-wrapping for space-delimited (Latin) languages  *Medium*  *(wrap)* — **RESOLVED**

**Resolution (shipped — clipwright-wrap v0.3.0 / suite v0.28.0):** `clipwright_wrap_captions`
now accepts space-delimited Latin-script languages (`en`, `es`, `fr`, `de`, `it`, `pt`, `nl`)
in addition to CJK/Thai. Latin cues are wrapped on whitespace word boundaries using the
existing `max_chars`/`max_lines` shaping; CJK/Thai (budoux) segmentation and output are
byte-for-byte unchanged. The `transcribe → wrap → render` chain is unblocked for English
subtitles. Latin word-wrap runs in-process (whitespace split); the budoux subprocess is only
invoked on CJK/Thai.

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

### Word-level / karaoke caption timing  *Medium*  *(transcribe / wrap / render)*  — **RESOLVED**

**Resolution (shipped — `clipwright-transcribe` v0.5.0 / `clipwright-render`
v0.16.0 / suite v0.29.0):**

- **`clipwright-transcribe`**: new `word_timestamps: bool = False` option. When
  `true`, emits a word-level WebVTT artifact (`<stem>.words.vtt`) with WebVTT
  inline timestamps (`<HH:MM:SS.mmm>word`) and adds
  `metadata["clipwright"]["words"]` (`[{text, start, end}]`) to the OTIO marker.
  Existing SRT / VTT / OTIO outputs are byte-for-byte unchanged with
  `word_timestamps=false` (default). CWE-400: inputs > 50 000 words return
  `INVALID_INPUT`. Ships as `clipwright-transcribe` v0.5.0.

- **`clipwright-render`**: new `SubtitleOptions` fields `karaoke: bool = False`,
  `highlight_color: str | None = None` (default `#FFFF00`),
  `chars_per_line: int = 42`, `max_lines: int = 2`.  When `karaoke=true`,
  render parses the word-level WebVTT, groups words into lines with a greedy
  char-budget algorithm, generates ASS `\k<cs>` tags (cs = 1/100 s,
  accumulated boundary differences for drift-free totals), and burns via the
  existing `subtitles` / libass path.  `pix_fmt=yuv420p` is maintained.  ASS
  injection guarded (escape `\`, `{`, `}`).  CWE-400: parser rejects > 50 000
  words or > 10 000 cues.  `karaoke=false` (default) leaves all existing render
  calls byte-for-byte identical to v0.15.0.  Ships as `clipwright-render`
  v0.16.0.

**Chain:**
```
clipwright_transcribe(word_timestamps=true)  →  <stem>.words.vtt
clipwright_render(subtitle.path=<stem>.words.vtt, subtitle.karaoke=true)  →  output.mp4
```

**Phase 2 — `clipwright-wrap` karaoke fold-through (out of scope for this
release):** `clipwright_wrap_captions` line-segment-word 3-level mapping for
karaoke is a more complex integration requiring a new carrier format or
fold-through contract and is deferred to a future release.  The `transcribe →
render` direct karaoke chain is fully functional without it.

---

*Original entry (retained for reference):*

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

### Color grading depth: LUT / white-balance / saturation / contrast  *Medium*  *(color extension)*  — **RESOLVED** (shipped — `clipwright-color` v0.3.0 / `clipwright-render` v0.17.0 / suite v0.30.0)

**Resolution:** `clipwright_detect_color` now measures chroma cast (`UAVG`/`VAVG` from
`signalstats`) and stores auto white-balance as `ColorDirective.white_balance` (per-channel gains
via `colorchannelmixer`, neutral 1.0, range [0.0, 4.0]). Caller-supplied `saturation`, `contrast`, `gamma` are accepted via
`DetectColorOptions` and written into the existing `EqParams` block. An optional caller-provided
`.cube` path is validated and stored as `ColorDirective.lut`. `clipwright-render` applies the
directive in a fixed three-stage order: `colorchannelmixer` (WB per-channel gain) → `eq` (saturation/contrast/gamma)
→ `lut3d`. All new `ColorDirective` fields are `Optional`; v0.2.x directives are
backward-compatible. WB measurement failures degrade gracefully (field omitted, warning emitted).

**What it does** *(original spec)*
Extends `detect_color` / the render color stage beyond a luma brightness offset to
full primary grading: white balance, saturation, contrast, and 3D-LUT application.

**Why it is needed** *(original spec)*
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

### Picture-in-picture / video-on-video overlay  *Medium*  *(overlay extension)*  — **RESOLVED**

**Resolution (shipped — `clipwright-overlay` v0.3.0 / `clipwright-render` v0.18.0 /
suite v0.35.0):** New MCP tool `clipwright_add_pip` (accumulate type) annotates an
OTIO timeline with a `pip_overlay` marker referencing a second video source
(`.mp4`/`.mkv`/`.mov`/`.webm`, must contain a video stream). Options mirror
`clipwright_add_overlay`'s placement vocabulary (`start_sec`/`duration_sec`/`x`/`y`/
`opacity`/`fade_in_sec`/`fade_out_sec`) plus PiP-specific fields: `media_start_sec`
(source trim offset — playback length is always the placement `duration_sec`, no
separate source-duration field), default `scale=0.3` (distinct from
`clipwright_add_overlay`'s `1.0`, since PiP sources are typically already
full-resolution), and optional audio mixing (`mix_audio`, `audio_volume`,
`ducking.enabled`/`threshold`/`ratio` — sidechain-compresses the main/BGM track
against the PiP audio, mirroring `clipwright_place_bgm`'s ducking). Up to 4 PiP
overlays may be accumulated per timeline. `clipwright-render` composites the PiP
video (topmost layer, after image overlays) and, when audio mixing is requested,
time-windows the PiP audio (`adelay`/`apad`/`atrim`) into the program mix. Reuses
`clipwright.pathpolicy` path-boundary/symlink validation and the `image_overlay`
accumulate/idempotent pattern; no new `clipwright` core dependency. Verified via
real stdio MCP + FFmpeg execution end-to-end (not unit tests alone — several
filter-graph wiring bugs were only caught by this real-execution testing).

*Original entry (retained for reference):*

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

## Cross-cutting backlog (promoted from maintainer notes)

These two items were carried in the maintainer's session backlog rather than as
spec5 entries. They are catalogued here, with a priority, so they live in the same
tracked surface as the gaps above. One completes an already-shipped feature; the
other is a performance track that refines the two GPU rows in *Out of scope* below.

### Karaoke fold-through in `clipwright-wrap` (line-wrapped word-synced captions)  *Low–Medium*  *(wrap extension)* — **Phase 2, deferred**

**What it does**
Lets the line-wrapping stage (`clipwright_wrap_captions`) carry per-word timing
through the wrap, so one caption can be **both** line-broken (budoux for CJK /
greedy for Latin) **and** word-synced ("karaoke") in the same burn. Today the two
are mutually exclusive.

**Why it is needed**
The shipped karaoke path (suite v0.29.0) is the **direct**
`transcribe(word_timestamps=true) → render(karaoke=true)` chain: `render` groups
words into lines itself with a flat char-budget and never routes through `wrap`.
That is fine for Latin (libass breaks Latin on spaces), but for **CJK karaoke** —
the Japanese short-form audience that is the project's home turf — you want budoux
phrase-boundary line-breaking *and* per-word highlight at once, and no path does
both. `wrap` operates on cue-/segment-level SRT/VTT and discards word timings, so
routing karaoke through it loses the `\k` timing.

**MCP tool name(s)**
None — extend `clipwright_wrap_captions` to accept the word-level WebVTT carrier
(the `<HH:MM:SS.mmm>word` inline-timestamp format `transcribe` already emits) and
re-emit a *wrapped* word-level carrier (or an ASS `\k` script) that preserves each
word's start/end across the inserted line breaks.

**Implementation hints**
- Requires a **3-level line ↔ segment ↔ word mapping**: parse the word carrier,
  run the existing budoux/greedy line-breaker on the word *sequence* (not the flat
  string), then re-attach each word's `(start, end)` to its post-wrap line. This is
  the "new carrier format / fold-through contract" the v0.29.0 release notes named
  as the reason it was deferred.
- Keep the direct `transcribe → render` karaoke chain unchanged; this is an
  *additional* CJK-friendly path, not a replacement.
- Reuse `captions.py` `wrap_cue_lines` / `_merge_to_max_lines` on a word-token
  list; `render` already owns ASS `\k` generation, so `wrap` could stop at
  "wrapped word-VTT" and let `render` burn it.

**Priority rationale** — *Low–Medium*: completes a shipped Medium feature and
serves the project's core JP short-form audience, but the common case (Latin
karaoke, and CJK *or* karaoke separately) already works, so it ranks below the four
open Medium reach features (color depth / PiP / NLE interop / translation).

---

### Full-GPU filtergraph (keep frames on-GPU through CUDA-native filters)  *Low*  *(render / perf)* — **bounded upside; partly environmental**

**What it does**
Runs the render filter chain on the GPU end-to-end — decode → CUDA-native filters
(`scale_cuda`, `overlay_cuda`, `transpose_cuda`, crop/pad) → NVENC encode — instead
of the current download-to-system-memory model, eliminating the per-frame
`hwdownload`/`hwupload` round-trips.

**Why it is needed / what already ships**
NVENC **encode** (`RenderOptions(hw_encoder=…)`) and **decode**
(`hwaccel_decode=True`) are already wired (suite v0.11.0 / render 0.8.0). But the
filters still run on CPU: `-hwaccel cuda` decodes on the GPU and ffmpeg implicitly
downloads each frame to system memory before the first non-CUDA filter (there are
**no** `scale_cuda`/`overlay_cuda`/`hwdownload` nodes in `plan.py` today). Measured
on a 180 s 1080p60 edit, NVENC cuts *pure* encode 3.4× (69 s → 20 s) but a *full*
edit only −22 % (213 s → 165 s), because ~87 % of the time is filter-bound and
stays on CPU.

**The catch (why this is Low, not a quick win)**
The **heaviest** filters in a real edit — `vidstabtransform` (stabilize), `afftdn`
(noise), `drawtext`/libass (captions), `eq`/`curves` (color) — have **no CUDA
equivalents** in ffmpeg. A full-GPU graph can only keep scaling/overlay/transpose/
crop on the GPU, which are not the bottleneck. So the realistic win is confined to
scale-/overlay-heavy, filter-light edits, and even there it competes with the
existing `hwaccel_decode` path. This is why it stays a perf track, not a workflow
blocker.

**MCP tool name(s)**
None — an opt-in `gpu_filtergraph` mode on `clipwright_render` that, when the graph
contains *only* CUDA-mappable filters, emits the `_cuda` filter variants and skips
the `hwdownload`; otherwise falls back to the current download model with a warning
(so a single non-CUDA filter never silently disables it).

**Implementation hints**
- Detect CUDA-mappable-only graphs at plan time; map `scale`→`scale_cuda`,
  `overlay`→`overlay_cuda`, `transpose`→`transpose_cuda`, and insert
  `hwupload_cuda`/`hwdownload` only at the GPU↔CPU boundary.
- Probe encoder/decoder availability with the existing `encoders.py` machinery;
  keep the libx264 / system-memory fallback as the safe default (consistent with
  `hw_encoder='auto'`).
- **Separate, also-environmental:** a CUDA `whisper.cpp` build for transcribe speed
  is a *build* swap (`CLIPWRIGHT_WHISPER` → CUDA build), not a clipwright code
  change — see *Out of scope* below; kept distinct from this filtergraph item.

**Priority rationale** — *Low*: a performance optimisation with a structurally
bounded ceiling (the bottleneck filters have no CUDA path), not a capability gap or
correctness issue, and it ships no broken output. Narratively the maintainer's
"video filter stage on GPU" headline, but per the spec5 priority definitions it
sits at Low.

---

### DRY the `validate_source_file` + `FILE_NOT_FOUND`→basename re-wrap into a core helper  *Low (maintainability)*  *(core / pathpolicy)*

Surfaced by code review during the D7 fix. The idiom

```python
try:
    validate_source_file(src)
except ClipwrightError as exc:
    if exc.code == ErrorCode.FILE_NOT_FOUND:
        raise ClipwrightError(FILE_NOT_FOUND, f"{Kind} file not found: {name}", hint) from None
    raise  # PATH_NOT_ALLOWED propagates
```

is now copy-pasted across **5 sites** (`wrap` input ×1, `bgm` timeline/bgm ×2, `overlay`
image/timeline ×2), plus a sibling variant in `frames` that re-wraps to `INVALID_INPUT`.
project-conventions requires path validation to live in `clipwright.pathpolicy` and not be
re-implemented per tool, so the re-wrap boilerplate is a candidate for a shared helper
(e.g. `pathpolicy.validate_source_or_basename(path, *, message, hint)` returning None and
re-raising the basename-safe `FILE_NOT_FOUND`, with `PATH_NOT_ALLOWED` propagating).

**Why deferred (not folded into D7):** the fix is maintainability-only (no behaviour change,
no correctness/security defect), and a proper DRY pass must touch **core (a version bump) plus
the already-shipped `wrap.py`** to retrofit all five sites symmetrically — a cross-cutting
refactor that should not enlarge the blast radius of a Low security patch. Do it as its own
scoped task (core helper + retrofit `wrap`/`bgm`/`overlay`, evaluate the `frames` INVALID_INPUT
variant) with its own review/CI cycle. *Low (maintainability)*: pure de-duplication.

**Resolution (shipped — suite v0.34.0)**
The core helper `clipwright.pathpolicy.validate_source_or_basename(path, *, message, hint,
error_code=ErrorCode.FILE_NOT_FOUND)` was added to `clipwright` v0.6.0. It runs
`validate_source_file` and, on `FILE_NOT_FOUND`, re-raises a basename-only message (CWE-209)
while letting `PATH_NOT_ALLOWED` (symlink) propagate unchanged. The idiom was retrofitted onto
all **6** duplicated call sites — `wrap` input ×1, `bgm` (`timeline_path` + `bgm_path`) ×2, and
`overlay` (`image_path` + `timeline`) ×2, plus `frames` — as a pure refactor with zero behaviour
change. The `error_code` parameter accommodates the `frames` variant that re-wraps to a different
code. Ships with `clipwright-wrap` v0.3.2, `clipwright-bgm` v0.3.3, `clipwright-overlay` v0.2.3,
and `clipwright-frames` v0.3.3, all raising their `clipwright` dependency floor to `>=0.6.0`.

### `transform` tools validate their timeline input with plain `.exists()` — symlink components followed (CWE-59)  *Low*  *(transition / speed / text …)*

Flagged by security review during D7. The D7 fix covered the **accumulate** tools' source inputs
(bgm, overlay). The **transform** tools (`transition`, `speed`, `text`, and any other OTIO→OTIO
tool) still validate their input timeline with a plain `.exists()` that follows symlinks — the
same CWE-59 class. Route their timeline input through `validate_source_file` with the same
basename re-wrap (naturally combines with the core-helper backlog item above). *Low*: same threat
model (single local stdio process) and priority as D7; a consistency hardening across the
remaining tool category.

**Resolution (shipped — suite v0.34.0)**
`clipwright-transition` (v0.2.1), `clipwright-speed` (v0.2.2), and `clipwright-text` (v0.2.2)
now validate their input timeline through the shared
`clipwright.pathpolicy.validate_source_or_basename` helper (the core-helper backlog item above)
instead of a plain `.exists()`. `PATH_NOT_ALLOWED` now fires correctly for a symlinked timeline
input (or a symlink in any intermediate directory component), matching the accumulate-tool
contract D7 established. This closes the CWE-59 class across the remaining `transform`-category
tools with no behaviour change for existing valid inputs.

---

## Out of scope / environmental (not gaps)

| Item | Reason |
|------|--------|
| GPU transcription speed | `transcribe` ran CPU at 1.37× realtime because the box has a CPU `whisper.cpp` build; pointing `CLIPWRIGHT_WHISPER` at a CUDA build is the fix (spec3 wiring). Not a clipwright gap. (The broader GPU ambition is now tracked as the *Full-GPU filtergraph* backlog entry above; the whisper part stays environmental.) |
| Music / voiceover sourcing | Providing the music bed and narration is the agent's job; clipwright mixes (`add_bgm`) and burns. Baking generators in fights the AI-first design. |
| Logo / overlay asset creation | Providing the PNG is the agent's job; `add_overlay` consumed it. (A *video* PiP source is a real gap — see above.) |
| Auto-highlight / one-click edit | Composing silence + scene + loudness into highlight selection remains the agent's job (spec3/spec4). A *scoring* primitive could assist but the decision stays orchestration. |
| HW decode for filter-heavy graphs | Known spec3/spec4 limit (CPU filters force `hwdownload`); recorded in spec4. Now tracked with a priority as the *Full-GPU filtergraph* backlog entry above (Low). |

---

## Tool coverage in this dogfood

18 satellite tools + core were exercised over real stdio MCP on handheld vlog
footage. The "create" path of every tool worked; the failures below are the
media+timeline match regression (D1) and the interval manifest mismatch (D2).

```
core        ✓                      speed       ✓
trim        ✓                      text        ✗ cascade from D1 (works standalone)
render      ✓ (NVENC+hwdecode;     overlay     ✗ cascade from D1 (works standalone)
              transition=yuv420p;   bgm         ✗ cascade from D1 (works standalone)
              karaoke=true
              v0.16.0 RESOLVED)
silence     ✓ (VAD 8 / energy 5)   reframe     ✓ NEW mode="track" (80 kf, follows subject)
scene       ✓ (psd 12 / ffmpeg 0)  sequence    ✓
frames      ✓ scene_sample;        transition  ✓ (4:2:0 confirmed on real footage)
              ✓ interval manifest (D2 RESOLVED: per-ss, manifest==disk)
                                     stabilize ~ apply params fixed (D4 RESOLVED:
                                                    crop=black/optzoom=1/smoothing=12/
                                                    unsharp + -threads 1 for vid.stab
                                                    #144); but unusable-by-default on
                                                    low-motion footage — needs severity
                                                    gate (D6/D3, next session)
transcribe  ✓ (en, CPU 1.37x)      color       ✓ create path; ✗ with timeline (D1)
            ✓ word_timestamps=true  loudness ✗ (D1)
              (word VTT + OTIO words
               v0.5.0 RESOLVED)
wrap        ✓ (Latin en/es/fr/de/it/pt/nl RESOLVED v0.28.0)
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

1. **D1 — timeline-source match resolves a relative ref against CWD** (High) —
   **RESOLVED** (shipped — suite v0.26.0): broke the multi-annotation timeline
   workflow across `color` / `loudness` / `noise` / `stabilize` (regression from the
   spec4 #5 path-policy unification). Fixed by folding the per-tool B-4 match into a
   shared core helper `clipwright.pathpolicy.check_timeline_source_matches` that
   resolves the relative `target_url` against the OTIO directory (not the CWD);
   boundary/symlink stays delegated to `check_media_ref`, error message canonical
   across all four tools with no filename leak (CWE-209). Verified by a real-MCP e2e
   running the detect chain → render from a CWD ≠ the OTIO directory (14/14). Ships
   as core 0.5.0 / color 0.2.1 / loudness 0.3.1 / noise 0.3.1 / stabilize 0.4.1.
2. **D6 + D3 — stabilize is unusable-by-default; needs a severity gate** (High,
   AI-usability) — **RESOLVED** (shipped — stabilize 0.4.0): D4 had fixed the
   apply-side *parameters*, but the real dogfood clip still looked bad *because it
   does not need stabilising* — a calm vlog, not action footage (over-stabilization
   of low-motion frames; footage-fit, not a tunable bug — so we did **not** tune to
   the clip). The AI-first fix is D3: `detect_shake` now returns a usable `severity`
   plus a `recommendation` (`"skip"`/`"apply"`) so the agent itself declines to
   stabilise low-shake footage. Root cause of the old `severity=null` was that
   `_estimate_severity` read the whole `.trf` body as a flat `float64` array, so the
   structured fields overflowed `sum()` to `inf` → non-finite → `None`. The parser
   now dispatches on the `.trf` magic — binary `TRF1` (Windows Gyan ffmpeg) or text
   `VID.STAB` (Linux/macOS builds) — and aggregates per-frame translation by
   **median** (scene cuts inject ~109 px spikes that corrupt the mean). The directive
   is always written (detect/apply separation; advisory only). Verified by re-running
   `vidstabdetect` on stabilised output (residual severity 0.427→0.167) and real-MCP
   e2e (severity non-null, recommendation, artifact paths exist); cross-platform
   validated on real Linux libvidstab via Docker before push. CI green on 3 OSes.
3. **D4 — stabilize apply pass ships degraded output** (High) — **RESOLVED**:
   shipped `vidstabtransform=...:smoothing=12:crop=black:optzoom=1,unsharp=5:5:0.8:3:3:0.4`
   plus `-threads 1` on stabilize renders only. Fixes all three apply-side complaints
   (ghosting=`crop=black`, over-smoothing=`smoothing=12`, softness=`unsharp`; sharpness
   7.4→14.0). Root cause of the original `unsharp` crash confirmed as
   [vid.stab #144](https://github.com/georgmartius/vid.stab/issues/144) (frame-thread
   race corrupting B-frame refs → 0xC0000005); `-threads 1` (0/27, ~+4% cost) is the
   workaround and also clears the residual single-pass crash. Real ffmpeg e2e verifies
   `ok`/artifact/`yuv420p` + a 15× crash-regression loop. Code on `main`; shipped
   together with the D3/D6 severity gate as suite **v0.25.0** (render 0.15.0 /
   stabilize 0.4.0). Spun off: vid.stab #144 upstream tracking and **D5**.
4. **D2 — frames interval manifest overcounts vs fps-filter output** (Medium) —
   **RESOLVED** (shipped — suite v0.27.0 / `clipwright-frames` v0.3.1): interval mode
   now extracts one frame per `compute_interval_timestamps` value via per-`-ss` single-frame
   extraction (the same path `scene`/`timestamps` use), so the extracted-frame list is the
   single source of truth and `manifest.count == len(glob(frame_*.jpg))` with every manifest
   path existing. The unused `build_fps_command` was removed. Hardened against per-`-ss`
   process blow-up with a frame-count guard (pre-estimate + exact, CWE-400). Verified by a
   non-multiple-length integration e2e (9 s / 4 s) and a real stdio-MCP e2e.
5. **Caption wrap for Latin languages** (Medium) — **RESOLVED** (shipped —
   clipwright-wrap v0.3.0 / suite v0.28.0): `clipwright_wrap_captions` now accepts
   space-delimited Latin-script languages (`en`, `es`, `fr`, `de`, `it`, `pt`, `nl`);
   Latin cues are wrapped on whitespace word boundaries using the existing
   `max_chars`/`max_lines` shaping. CJK/Thai (budoux) unchanged.
6. **Word-level/karaoke captions** (Medium) — **RESOLVED** (shipped —
   `clipwright-transcribe` v0.5.0 / `clipwright-render` v0.16.0 / suite v0.29.0):
   `word_timestamps=true` on transcribe emits a word-level WebVTT artifact;
   `subtitle.karaoke=true` on render burns ASS `\k` word-synced captions.
   **`clipwright-wrap` karaoke fold-through is Phase 2 (deferred).**
   **Color grading depth** (Medium) — **RESOLVED** (shipped — `clipwright-color` v0.3.0 /
   `clipwright-render` v0.17.0 / suite v0.30.0): WB (`colorchannelmixer` per-channel gain), saturation/contrast/gamma
   (`eq`), and 3D-LUT (`lut3d`) across the color detect → render pipeline.
   Remaining reach/quality features: **video PiP · NLE interop · subtitle translation**
   (Medium): for a general editing suite.
7. **Cross-cutting backlog (promoted from maintainer notes):**
   **wrap karaoke fold-through** (Low–Medium) — completes the v0.29.0 karaoke
   feature with a line ↔ segment ↔ word mapping so CJK captions can be both
   budoux-wrapped and word-synced; below the four open Medium reach features.
   **Full-GPU filtergraph** (Low) — keep frames on-GPU through CUDA-native filters;
   NVENC encode + HW decode already ship (v0.11.0), but the bottleneck filters
   (`vidstabtransform`/`afftdn`/libass/`eq`) have no CUDA path, so the upside is
   structurally bounded (refines the two GPU rows in *Out of scope*).
8. **D5 — render raw stderr in `SUBPROCESS_FAILED` (CWE-209)** (Low) · **two-pass
   `unsharp` pre-pass · residual libvidstab build crash · diarization · Ken Burns ·
   export presets** (Low–Medium): follow-ups.
9. **D7 — accumulate tools (`bgm`, `overlay`) validate source media with plain
   `.exists()`/`resolve().exists()`, so symlink components are followed not rejected
   (CWE-59)** (Low) — surfaced during the v0.32.0 boundary-guard rollout; the
   v0.31.0 pathpolicy hardening (frames `scene_timeline` / wrap `input` →
   `validate_source_file`) did not cover the accumulate-type source inputs. Route
   `bgm` (`timeline_path` + `bgm_path`) and overlay (`image_path`) through
   `validate_source_file`, keeping ADR-PP-1 outside-tree acceptance intact. (The
   stale `overlay_e2e_smoke.py` Scenario 5 that still expects `PATH_NOT_ALLOWED` for
   an outside-tree image is a test-expectation bug, not a product defect.)
   **Resolved in suite v0.34.0** — see D7 and the two backlog entries above (the
   accumulate guard shipped in v0.33.0; v0.34.0 completes the transform-category
   counterpart and DRY-consolidates the shared `validate_source_or_basename` helper).
