# Clipwright — Missing Features & Implementation Roadmap

> Companion to `clipwright-spec.md`. Catalogues capabilities absent from v0.2.0
> and provides enough detail for a developer or AI agent to begin implementation.

---

## How to Read This Document

Each entry follows a fixed structure:

- **Package name** — proposed PyPI / MCP identifier
- **What it does** — one-paragraph description of the tool's responsibility
- **Why it is needed** — the gap it fills in the current editing workflow
- **MCP tool name(s)** — the `clipwright_<action>` identifiers the tool will expose
- **Implementation hints** — concrete technical notes (FFmpeg filters, OSS, OTIO patterns)
- **Priority** — High / Medium / Low, explained below

### Priority definitions

| Priority | Meaning |
|----------|---------|
| **High** | Blocks common AI-assisted editing workflows today; no workaround exists |
| **Medium** | Valuable for quality or versatility; can be deferred without blocking basics |
| **Low** | Niche use case or largely handled by existing tools with minor extensions |

---

## High Priority

### `clipwright-scene`

**What it does**
Detects shot boundaries (scene cuts) in a video file and records each boundary as an OTIO
marker in the output timeline. Each marker carries a `confidence` score and a thumbnail
path extracted at that moment (optional, controlled by a parameter).

**Why it is needed**
`clipwright-silence` identifies quiet intervals but is blind to visual structure.
An AI editing agent cannot know where scenes begin and end without shot detection.
Scene markers let subsequent tools (silence, transcribe, color) scope their analysis
to the relevant cut rather than the whole file. This is the structural backbone
that most editing workflows need before any other annotation pass.

**MCP tool name**
`clipwright_detect_scenes`

**Implementation hints**
- Primary backend: `ffmpeg -vf "select=gt(scene\,{threshold}),showinfo" -vsync vfr`
  — parses stderr for `pts_time` lines to extract boundary timestamps.
- Alternative backend: [PySceneDetect](https://github.com/Breakthrough/PySceneDetect)
  (`scenedetect` CLI) for content-aware detection. Keep as an optional `backend`
  parameter (`ffmpeg` | `pyscenedetect`).
- Output: OTIO timeline with one `Marker` per boundary. Store in
  `metadata["clipwright"]["kind"] = "scene_boundary"` and
  `metadata["clipwright"]["confidence"] = 0.0–1.0`.
- MCP annotations: `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`.
- Options struct:
  ```python
  class DetectScenesOptions(BaseModel):
      threshold: float = 0.3          # scene change score 0.0–1.0
      min_scene_duration: float = 1.0 # seconds; merge shorter cuts
      backend: Literal["ffmpeg", "pyscenedetect"] = "ffmpeg"
      extract_thumbnails: bool = False # write JPEG to artifacts/
  ```

---

### `clipwright-frames`

**What it does**
Extracts representative still images from a video file at fixed time intervals,
at scene boundaries (if a `scene_timeline` is provided), or at user-specified
timestamps. Returns a list of image file paths alongside their timestamps as
OTIO markers.

**Why it is needed**
Multimodal AI models (Claude, GPT-4o, etc.) can reason about video content
only when they have visual samples. Currently clipwright provides no way to
produce images from video. Frame extraction makes the visual layer inspectable:
agents can call `clipwright_extract_frames` first, examine thumbnails, then decide
what editing operations are appropriate. It also enables thumbnail generation for
chapter cards and preview strips.

**MCP tool name**
`clipwright_extract_frames`

**Implementation hints**
- Use `ffmpeg -vf fps=1/{interval} -q:v 2 artifacts/%05d.jpg` for interval mode.
- For scene-boundary mode: accept an OTIO path (`scene_timeline`), read its
  `scene_boundary` markers, seek to each timestamp with
  `-ss {ts} -frames:v 1 -q:v 2`.
- For timestamp mode: iterate a user-supplied list.
- Output: list of `Artifact` dicts `{"path": "...", "timestamp_sec": 3.14}`;
  also embed as OTIO markers on the returned timeline for downstream tools.
- MCP annotations: `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`.
- Options struct:
  ```python
  class ExtractFramesOptions(BaseModel):
      mode: Literal["interval", "scene", "timestamps"] = "interval"
      interval_sec: float = 10.0
      scene_timeline: str | None = None  # OTIO path for scene mode
      timestamps: list[float] = []       # seconds, for timestamps mode
      format: Literal["jpeg", "png"] = "jpeg"
      quality: int = 2                   # ffmpeg -q:v
  ```

---

## Medium Priority

### `clipwright-speed`

**What it does**
Annotates an OTIO timeline's clips with a speed multiplier using OTIO's native
`LinearTimeWarp` effect. A multiplier > 1 produces time-lapse; < 1 produces
slow motion. `clipwright-render` reads the warp effects and applies the correct
FFmpeg filters.

**Why it is needed**
Speed ramping is one of the most common operations in highlight reels and
instructional videos. Currently OTIO clips have no speed annotation and `render`
has no filter path for it. Without this, speed changes require the user to
pre-process the file externally and lose the non-destructive, single-render
philosophy.

**MCP tool name**
`clipwright_set_speed`

**Implementation hints**
- OTIO provides `LinearTimeWarp` as a built-in effect class:
  ```python
  import opentimelineio as otio
  warp = otio.schema.LinearTimeWarp(time_scalar=0.5)
  clip.effects.append(warp)
  ```
  Use native OTIO effects, not `metadata["clipwright"]` fields, so that
  other OTIO-aware tools honour the warp correctly.
- `clipwright-render` needs a new code path: detect `LinearTimeWarp` on each clip
  and apply `setpts=PTS/{scalar}` (video) + chain of `atempo` filters (audio).
  Note: `atempo` is limited to 0.5×–2.0×; for wider ranges, chain multiple
  instances (e.g., 0.25× = `atempo=0.5,atempo=0.5`).
- Options struct:
  ```python
  class SetSpeedOptions(BaseModel):
      speed: float          # multiplier: 0.25–8.0 recommended range
      clip_index: int | None = None   # None = apply to all clips
      start_sec: float | None = None  # apply within range only
      end_sec: float | None = None
  ```

---

### `clipwright-text`

**What it does**
Adds text overlay annotations to an OTIO timeline: title cards, lower thirds,
and arbitrary on-screen text with configurable position, typography, and
timing. `clipwright-render` materialises these via FFmpeg's `drawtext` filter.

**Why it is needed**
`clipwright-transcribe` + `clipwright-wrap` handle burned-in caption text derived
from speech. But title sequences, section headings, speaker name cards, and
call-to-action overlays are entirely absent. These are standard elements in
instructional, interview, and social-media videos.

**MCP tool name**
`clipwright_add_text`

**Implementation hints**
- Store text overlays in OTIO markers with
  `metadata["clipwright"]["kind"] = "text_overlay"` and fields:
  `text`, `x`, `y`, `font_size`, `font_color`, `box_color`,
  `start_sec`, `duration_sec`, `fade_in_sec`, `fade_out_sec`.
- `clipwright-render` reads all `text_overlay` markers and chains
  `drawtext` filters:
  ```
  drawtext=text='Hello':x=100:y=50:fontsize=48:fontcolor=white:
           enable='between(t\,3\,8)':alpha='if(lt(t\,3.5),(t-3)/0.5,1)'
  ```
- For font lookup on Windows: default to `C\:/Windows/Fonts/Arial.ttf`;
  allow override via `font_path` option.
- Options struct:
  ```python
  class AddTextOptions(BaseModel):
      text: str
      start_sec: float
      duration_sec: float
      x: str = "(w-tw)/2"          # ffmpeg expression; default: centered
      y: str = "h-th-40"           # default: lower third
      font_size: int = 48
      font_color: str = "white"
      box: bool = False
      box_color: str = "black@0.5"
      fade_in_sec: float = 0.3
      fade_out_sec: float = 0.3
      font_path: str | None = None
  ```

---

### `clipwright-color`

**What it does**
Measures video brightness, contrast, and colour temperature using FFmpeg's
`signalstats` filter. Records per-clip correction hints (brightness offset,
contrast multiplier, saturation, gamma) as OTIO metadata. `clipwright-render`
applies the `eq` filter to realise corrections in the output.

**Why it is needed**
Footage shot in varying lighting conditions (e.g., multiple takes, outdoor/indoor
cut) has inconsistent exposure. Loudness normalisation for audio already follows
this detect-then-render pattern; colour correction is the video equivalent.
Without it, an AI agent has no non-destructive path to colour balance a sequence.

**MCP tool name**
`clipwright_detect_color`

**Implementation hints**
- Use `ffmpeg -vf "signalstats=stat=tout+vrep+brng,metadata=print"` to extract
  `YAVG` (luma average), `UDIF`, `VDIF` per frame; aggregate per clip.
- Derive suggested `eq` parameters from measured vs. target luma:
  `brightness = (target_luma - measured_luma) / 255.0`
- Store in `metadata["clipwright"]["color"]`:
  `{"brightness": 0.05, "contrast": 1.1, "saturation": 1.0, "gamma": 1.0}`
- `clipwright-render` applies: `eq=brightness={b}:contrast={c}:saturation={s}:gamma={g}`.
- Options struct:
  ```python
  class DetectColorOptions(BaseModel):
      target_luma: float = 128.0     # 0–255; target average brightness
      per_clip: bool = True          # annotate per-clip vs. whole-file
      sample_interval_sec: float = 1.0
  ```

---

## Low Priority

### `clipwright-stabilize`

**What it does**
Two-phase video stabilisation: (1) `clipwright_detect_shake` analyses motion
vectors and writes a `.trf` analysis file + OTIO shake-severity annotation;
(2) `clipwright-render` reads the annotation and applies `vidstabtransform` in
the render pass.

**Why it is needed**
Handheld and action footage is common in vlog and tutorial content. Stabilisation
must remain non-destructive (detect/apply split) and compatible with the
single-render philosophy.

**MCP tool name**
`clipwright_detect_shake`

**Implementation hints**
- Phase 1 (detect): `ffmpeg -vf vidstabdetect=result={trf_path}:shakiness={s}:accuracy={a}`.
  Store `trf_path` in `metadata["clipwright"]["stabilize"]["trf_path"]`.
  Measure average motion vector magnitude; store as `severity` (0.0–1.0) in OTIO marker.
- Phase 2 (render): `clipwright-render` checks for `stabilize` metadata on clips
  and prepends `vidstabtransform=input={trf_path}:smoothing=30` to the filter chain.
- **Prerequisite**: FFmpeg build must include `libvidstab`. Document this in README
  (analogous to the existing FFmpeg PATH requirement).
- Options struct:
  ```python
  class DetectShakeOptions(BaseModel):
      shakiness: int = 5     # 1–10; libvidstab parameter
      accuracy: int = 15     # 1–15
      smoothing: int = 30    # applied at render time
  ```

---

## Won't Implement (and Why)

| Feature | Reason |
|---------|---------|
| Dedicated format conversion tool | `clipwright-render` already accepts arbitrary `video_codec` / `audio_codec` / container options. A separate tool adds no contract value. |
| Chapter management tool | `clipwright core`'s `clipwright_write_timeline` with `add_marker` operations covers this. No new tool needed. |
| Audio-only export | Render to `.mp3` / `.aac` by setting `video_codec: none` (or equivalent). Extend render options rather than add a tool. |
| Multi-cam sync | Requires timecode / waveform alignment — complex, niche, out of scope for v1. |

---

## Dependency Map (updated)

```
clipwright (core)
  └─ clipwright-silence        ← existing
  └─ clipwright-scene          ← NEW (High)
  └─ clipwright-frames         ← NEW (High); optionally reads scene output
  └─ clipwright-transcribe     ← existing
  └─ clipwright-loudness       ← existing
  └─ clipwright-noise          ← existing
  └─ clipwright-color          ← NEW (Medium)
  └─ clipwright-speed          ← NEW (Medium); writes to OTIO, render honours it
  └─ clipwright-text           ← NEW (Medium); writes to OTIO, render honours it
  └─ clipwright-stabilize      ← IMPLEMENTED (v0.1.0); two-phase, render honours it (v0.6.0)
  └─ clipwright-bgm            ← existing
  └─ clipwright-wrap           ← existing
  └─ clipwright-render         ← existing; extended for speed/text/color/stabilize (all implemented)
```

---

## render Extension Checklist

Before new tools are useful end-to-end, `clipwright-render` needs to handle:

- [x] `LinearTimeWarp` effect → `setpts` + `atempo` filter chain
- [x] `text_overlay` OTIO markers → `drawtext` filter chain
- [x] `color` OTIO metadata → `eq` filter
- [x] `stabilize.trf_path` OTIO metadata → `vidstabtransform` filter
