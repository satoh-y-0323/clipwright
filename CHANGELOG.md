# Changelog

All notable changes to `clipwright` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.32.0] - 2026-07-02

`except Exception` internal-error boundary guards were rolled out across 13 tools so unexpected
exceptions no longer leak absolute paths in error messages (CWE-209), matching the guards already
present in `clipwright-frames`, `clipwright-wrap`, `clipwright-scene`, `clipwright-transition`, and
`clipwright-stabilize`.

### Security

- **`clipwright-bgm` v0.3.1**
- **`clipwright-color` v0.3.1**
- **`clipwright-loudness` v0.3.2**
- **`clipwright-noise` v0.3.2**
- **`clipwright-silence` v0.3.1**
- **`clipwright-trim` v0.2.1**
- **`clipwright-speed` v0.2.1**
- **`clipwright-sequence` v0.2.1**
- **`clipwright-text` v0.2.1**
- **`clipwright-reframe` v0.3.1**
- **`clipwright-transcribe` v0.5.1**
- **`clipwright-overlay` v0.2.1**
- **`clipwright-render` v0.17.1**

## [0.31.0] - 2026-07-02

Path-boundary hardening follow-up for `clipwright-frames` and `clipwright-wrap` (CWE-59 / CWE-209).

### Security (`clipwright-frames` v0.3.2)

- **`scene_timeline` input now rejects symbolic links (CWE-59)** — `mode="scene"` validates the
  caller-supplied scene-timeline OTIO path through the shared
  `clipwright.pathpolicy.validate_source_file` guard, closing a path-boundary bypass where a
  symlinked timeline file could point outside the intended source tree.

### Security (`clipwright-wrap` v0.3.1)

- **`input` now rejects symbolic links (CWE-59)** — subtitle `input` is validated through the
  shared `clipwright.pathpolicy.validate_source_file` guard, closing the same class of
  path-boundary bypass.
- **Extension-error messages no longer echo caller-supplied extensions (CWE-209)** — the
  "unsupported extension" errors for `input`/`output` now use a fixed message instead of
  interpolating the caller-supplied path/extension, and an internal-error boundary guard
  prevents unexpected exceptions from disclosing filesystem paths.

## [0.30.0] - 2026-07-01

Color grading depth — white balance, saturation/contrast/gamma, and 3D-LUT (spec5 Medium-reach entry RESOLVED).

### Added (`clipwright-color` v0.3.0)

- **Auto white-balance measurement**: `clipwright_detect_color` now measures chroma cast by
  extracting `UAVG` and `VAVG` from the `signalstats` filter (same ffprobe pipeline as `YAVG`).
  Deviation of the median `UAVG`/`VAVG` from the neutral point (128 in 8-bit YUV) is converted to
  a per-channel gain via `colorchannelmixer` (neutral 1.0, range [0.0, 4.0]) stored in a new `ColorDirective.white_balance` field. If the chroma
  measurement fails (subprocess error, parse failure, or insufficient samples), the `white_balance`
  field is omitted from the directive, the timeline is saved with the remaining grade fields intact,
  and a `warnings` entry describes the failure (mirrors the existing luma-measurement degradation
  path U-1).

- **Caller saturation / contrast / gamma**: `DetectColorOptions` gains optional `saturation`,
  `contrast`, and `gamma` fields. When supplied, these are written directly into the existing
  `EqParams` fields of `ColorDirective.eq`, which `clipwright-render` already consumes via
  `_append_eq_filter`. When omitted, fields remain at neutral defaults — no behavioural change to
  existing callers.

- **Caller temperature / tint override**: `DetectColorOptions` gains optional `temperature`
  (warm/cool axis) and `tint` (green/magenta axis) fields. When provided, these are used instead of
  the auto-measurement result to populate `ColorDirective.white_balance`, giving the caller direct
  control over the look.

- **Caller 3D-LUT**: `DetectColorOptions` gains an optional `lut` field (path to a `.cube` file).
  The path is validated (existence, regular-file check, no symlinks) at detect time and written into a new
  `ColorDirective.lut` field. All new `ColorDirective` fields are `Optional` with `None` default,
  maintaining backward compatibility with directives written by v0.2.x.

### Added (`clipwright-render` v0.17.0)

- **WB filter stage (`colorchannelmixer`)**: when `ColorDirective.white_balance` is present, a
  `colorchannelmixer` per-channel gain filter is injected before the existing `eq` stage. When absent, the stage is
  a no-op.

- **3D-LUT filter stage (`lut3d`)**: when `ColorDirective.lut` is present, `lut3d=file='…'` is
  injected after the `eq` stage. The `.cube` path is re-validated at render time via
  `clipwright.pathpolicy.validate_source_file` (defence-in-depth; the OTIO is untrusted). When
  absent, the stage is a no-op.

- **Grade application order**: `colorchannelmixer` (WB per-channel gain) → `eq` (saturation / contrast / gamma) →
  `lut3d`. Existing single-field `eq` calls from v0.16.0 and earlier are byte-for-byte identical
  when `white_balance` and `lut` are absent.

### Migration

- **Breaking (development-phase OTIOs only)**: WB directives written under the pre-release
  `colorbalance` scheme (range `[-1, 1]`, neutral `0.0`) must be regenerated by re-running
  `clipwright_detect_color`. Under the old scheme, a neutral white-balance entry stored
  `{r: 0, g: 0, b: 0}`; under the new `colorchannelmixer` gain scheme (neutral `1.0`),
  those values would mean zero gain — i.e. black video. This release now rejects `gain=0`
  with `INVALID_INPUT`, and other legacy WB gain values outside the valid range are likewise
  rejected. Re-run `clipwright_detect_color` against the source media to obtain a fresh OTIO
  with correct `colorchannelmixer` gain values before passing it to `clipwright-render`.
  *Scope*: the `colorbalance`-based WB scheme was never published to PyPI; impact is limited
  to development-phase OTIOs generated during pre-release testing.

### Chain

```
clipwright_detect_color(media="clip.mp4", output="grade.otio",
                        saturation=1.2, contrast=1.05, lut="look.cube")
clipwright_render(timeline="grade.otio", output="graded.mp4")
```

## [0.29.0] - 2026-06-30

Word-level / karaoke caption timing (spec5 Priority #6 RESOLVED).

### Added (`clipwright-transcribe` v0.5.0)

- **`word_timestamps` option**: `clipwright_transcribe` now accepts
  `word_timestamps: bool = False`. When `true`, a word-level WebVTT artifact
  (`<stem>.words.vtt`) is written alongside the existing SRT/VTT/OTIO outputs.
  Each cue body contains WebVTT inline timestamps (`<HH:MM:SS.mmm>word`) that
  carry the per-word start time. `metadata["clipwright"]["words"]` on the OTIO
  marker gains a `[{text, start, end}]` list for downstream tools. CWE-400:
  inputs exceeding 50 000 words return `INVALID_INPUT` before the artifact is
  generated. `word_timestamps=false` (default) is byte-for-byte identical to
  v0.4.0 — no whisper command changes, no extra artifacts, no additional cost.

### Added (`clipwright-render` v0.16.0)

- **Karaoke burn-in**: `SubtitleOptions` gains `karaoke: bool = False`,
  `highlight_color: str | None = None` (default `#FFFF00`),
  `chars_per_line: int = 42`, and `max_lines: int = 2`.  When `karaoke=true`,
  render parses the word-level WebVTT from `clipwright_transcribe`, groups words
  into lines with a greedy char-budget algorithm, and generates ASS `\k<cs>` tags
  (cs = 1/100 s, accumulated boundary differences for drift-free totals).  The
  generated ASS is burned in via the existing `subtitles` / libass filter path.
  `pix_fmt=yuv420p` is maintained.  CWE-400: the VTT parser rejects inputs
  exceeding 50 000 words or 10 000 cues before any ASS is generated.  ASS
  injection is guarded by escaping `\`, `{`, `}` in word text before `\k` tag
  generation.  `karaoke=false` (default) leaves all existing render calls
  byte-for-byte identical to v0.15.0.

### Chain

```
clipwright_transcribe(word_timestamps=true)  →  <stem>.words.vtt
clipwright_render(subtitle.path=<stem>.words.vtt, subtitle.karaoke=true)  →  output.mp4
```

> **wrap karaoke note:** `clipwright_wrap_captions` karaoke fold-through
> (line-segment-word 3-level mapping) is Phase 2 and is **not** included in this
> release. The `transcribe → render` direct chain is fully functional without it.

## [0.28.0] - 2026-06-29

Latin-script (space-delimited) caption word-wrap support (spec5).

### Added (`clipwright-wrap` v0.3.0)

- **Latin (space-delimited) language word-wrap**: `clipwright_wrap_captions` now accepts
  space-delimited Latin-script languages (`en`, `es`, `fr`, `de`, `it`, `pt`, `nl`) in
  addition to CJK/Thai. Latin cues are wrapped on word boundaries using whitespace
  segmentation; CJK/Thai segmentation (budoux) and output are byte-for-byte unchanged
  (fully backward-compatible). This unblocks the `transcribe → wrap → render` chain for
  English subtitles, which previously hard-errored with a `VALIDATION_ERROR` on the
  `language` parameter.

## [0.27.0] - 2026-06-29

Frame-extraction interval-mode manifest fix (spec5 D2).

### Fixed (`clipwright-frames` v0.3.1)

- **Interval mode now extracts one frame per manifest timestamp, so the manifest matches the
  files on disk.** `extract_frames(mode="interval")` previously computed its `frames.json`
  manifest from the analytic start-aligned grid of `compute_interval_timestamps` (e.g. `[0, 15,
  30, 45, 60, 75]`) while extracting the actual frames with the ffmpeg `fps=1/N` filter, which
  samples at period *midpoints* and emits a different number of frames near the tail. For a clip
  whose length is not an exact multiple of the interval, the manifest listed a final
  `frame_NNNNN.jpg` that was never written, so an agent consuming the manifest would try to open
  a non-existent file. Interval mode now uses the same per-`-ss` single-frame extraction path as
  `scene`/`timestamps` mode, making the extracted-frame list the single source of truth for the
  manifest `count` and frame paths (`manifest.count == number of files on disk`, every manifest
  path exists). The now-unused `build_fps_command` helper was removed.

### Security (`clipwright-frames` v0.3.1)

- **Interval mode is now bounded against frame-count blow-up (CWE-400).** Because per-`-ss`
  extraction spawns one ffmpeg process per frame, a tiny `interval_sec` over a long clip could
  spawn an unbounded number of processes (and write an unbounded number of files). Interval
  extraction now rejects requests that would produce more than a fixed maximum number of frames,
  with an O(1) pre-estimate guard *before* the timestamp list is materialised (preventing memory
  blow-up) plus an exact post-count guard. The error message names the frame count and the limit
  without leaking any filesystem path or subprocess output.

## [0.26.0] - 2026-06-28

Timeline source matching fix across color, loudness, noise, and stabilize tools.

### Fixed (`clipwright` v0.5.0, `clipwright-color` v0.2.1, `clipwright-loudness` v0.3.1, `clipwright-noise` v0.3.1, `clipwright-stabilize` v0.4.1)

- **Relative OTIO media references now resolve against the OTIO directory, not the process CWD.**
  The per-tool inline B-4 timeline-source match block in `color`, `loudness`, `noise`, and
  `stabilize` previously resolved relative media reference paths against the current working
  directory of the MCP server process. When the server was launched from a directory other than
  the one containing the `.otio` file, the resolved path did not match the actual source file,
  causing a spurious `INVALID_INPUT` error on every tool that uses the accumulate/transform
  pipeline. The fix consolidates this into a new shared `check_timeline_source_matches` helper
  in `clipwright` core that resolves relative references against the OTIO file's parent
  directory, consistent with the OTIO specification and the existing `check_media_ref`
  read-side contract. This was a latent regression from spec4 fix #5 that resurfaced when the
  timeline annotation stack was extended in spec5.

## [0.25.0] - 2026-06-28

Stabilize severity estimation and skip-gate (spec5 D3/D6). The shake-severity pipeline had
a structural parsing defect that silently produced `null` on every real `.trf` file; the
median aggregation was also distorted by multi-shot scene-cut spike frames. A new
`recommendation` (`"skip"` / `"apply"`) advisory field surfaces the severity gate so the
calling agent can decide whether stabilisation is worth applying.

### Fixed (`clipwright-stabilize` v0.4.0)

- **Severity estimation now parses real `.trf` files correctly (TRF1 structural fix).**
  The old flat-double scan misread int32 header and field bytes as IEEE-754 doubles
  (~1e308 each), so `sum()` overflowed to `inf` and the `isfinite` guard returned `null`
  unconditionally on all real vidstabdetect output. The parser now correctly reads the
  packed TRF1 binary layout (per-frame prefix + LocalMotion structs), yielding a valid
  severity score from actual footage.
- **Median aggregation is robust to multi-shot scene-cut outliers.** When a `.trf` file
  covers footage with hard scene cuts, the inter-frame displacement spikes at each cut
  inflated the mean severity score. Aggregation now uses the median, making the estimate
  representative of typical shake across the continuous shot.

### Added (`clipwright-stabilize` v0.4.0)

- **`recommendation` field on `detect_shake` response and `StabilizeDirective` (spec5 D3/D6).**
  The tool now returns `recommendation: "skip" | "apply"` — an advisory severity gate that
  indicates whether stabilisation is expected to be beneficial. `"skip"` is returned when
  `severity` is below the threshold for perceptible improvement (low-motion footage such as
  screen captures or a static camera), preventing unnecessary quality degradation from
  `vidstabtransform` overcorrection. `"apply"` is returned when severity suggests the footage
  would benefit. When `severity=null` (parsing failure fallback), `recommendation` defaults
  to `"apply"` with a warning. The recommendation is advisory only; the calling agent makes
  the final decision.
  - `StabilizeDirective.recommendation: Literal["skip", "apply"] | None` (default `None` for
    backward compatibility — existing OTIO timelines without this field remain valid, AC-10).
  - `detect_shake` data envelope gains `recommendation` key alongside `severity`.
  - `summary` text now includes `recommendation=<value>` for at-a-glance agent readability.

> **Suite version note (ADR-REL-1):** Suite tag `v0.24.0` was prepared but not yet pushed to
> the remote (render v0.15.0 + stabilize v0.3.0). To avoid version-reuse confusion, this
> release uses suite tag `v0.25.0`. The `v0.24.0` CHANGELOG section below documents the
> render quality fixes that ship together with this release.

## [0.24.0] - 2026-06-28

Stabilization apply-pass quality fix (spec5 D4). The stabilize render path previously
emitted a defaults-only `vidstabtransform` that left ghost-smear borders, over-smoothed
motion, and looked softer than the source — for an AI-first tool that cannot visually
QA its output, the apply defaults must be good by construction. The filter is now built
with `crop=black` (no prev-frame border fill), `optzoom=1` (optimal static zoom hides the
exposed border), and `unsharp` (restores interpolation softness), and the default
`smoothing` is re-baselined from 30 to 12.

### Fixed (`clipwright-render` v0.15.0)

- **Stabilize apply pass no longer ships degraded output.** The vidstabtransform filter
  is now `...:crop=black:optzoom=1,unsharp=5:5:0.8:3:3:0.4`. To keep `unsharp` (which
  otherwise crashed libvidstab with an access violation on Windows builds), `render` now
  passes `-threads 1` **only** when a stabilize directive is present. The crash root
  cause is [vid.stab #144](https://github.com/georgmartius/vid.stab/issues/144):
  `vsTransformPrepare` corrupts the decoder's reference frames under frame-level codec
  multithreading. Serializing decode (`-threads 1`) avoids it deterministically (cost
  ~+4% on this filter-bound workload) and also clears a residual single-pass crash. A
  real-ffmpeg e2e verifies `ok` + artifact-on-disk + `pix_fmt=yuv420p` and runs a
  crash-regression loop.

### Changed (`clipwright-stabilize` v0.3.0)

- **Default `smoothing` re-baselined 30 → 12** (`DetectShakeOptions.smoothing` and the
  MCP server docstring) to stop over-smoothing handheld footage. An explicit `smoothing`
  value is still honoured unchanged.

## [0.23.0] - 2026-06-27

Cross-tool path-boundary & I/O-contract unification (spec4 #5). All 17 satellite
tools delegate path validation to a new `clipwright.pathpolicy` module in core.
Sources may now reside anywhere readable; outputs may be placed anywhere; symbolic
links are rejected on all path components; absolute paths to existing regular files
are accepted as an escape hatch. The co-location restriction on `clipwright-sequence`
sources and `clipwright-overlay` images is removed.

### Added (`clipwright` core v0.4.0)

- **`clipwright.pathpolicy` module** — five shared path-validation helpers used by
  all 17 satellite tools:
  - `validate_source_file(path)`: asserts existence + regular file + no symlink on
    any path component; raises `FILE_NOT_FOUND` or `PATH_NOT_ALLOWED` (ADR-PP-2 /
    CWE-59).
  - `check_output_not_source(output, sources)`: raises `PATH_NOT_ALLOWED` when the
    output path canonically equals any source (three-stage canonicalisation:
    `resolve() → absolute() → str`).
  - `media_ref_for_otio(source, otio_dir)`: returns a relative POSIX path when the
    source is within the OTIO directory tree (portable round-trip) or an absolute
    path when outside (external reference). Normalises backslashes on Windows.
  - `check_media_ref(ref, otio_dir, kind)`: validates a stored OTIO media / subtitle
    / image reference at materialisation time. Relative refs must resolve within the
    OTIO directory tree (CWE-22 guard). Absolute refs must point to an existing
    regular file with no symlink on any path component (ADR-PP-1 absolute escape
    hatch + ADR-PP-2 / CWE-59).
  - `check_within_boundary(base_dir, target, kind)`: containment guard for
    detect/extract output artifacts (`clipwright-scene`, `clipwright-frames`);
    ensures outputs remain within the designated `output_dir` (DC-GP-002).

### Changed (all 17 satellite tools)

All 17 satellite tools replace their local path-validation code with calls to the
shared `pathpolicy` helpers. The unified boundary rules:

- **Outputs** may be placed anywhere; only `output == any source` is rejected.
- **Sources** (input media) may reside anywhere readable; no co-location restriction.
  Symlinks are rejected on all path components (ADR-PP-2 / CWE-59).
- **OTIO references** stored at annotation time: relative POSIX path for files within
  the OTIO directory tree; absolute path for external files. `clipwright-render`
  validates both forms at materialisation time via `check_media_ref` (ADR-PP-1).
- **`clipwright-sequence`** v0.2.0: the former requirement that all sources be
  co-located under the output OTIO directory is removed (ADR-SEQ-6 relaxed). External
  sources are accepted and stored as absolute paths in the written OTIO.
- **`clipwright-overlay`** v0.2.0: the former requirement that the image file be
  co-located under the output timeline's parent directory is removed (ADR-PP-1).
  External images are accepted and stored as absolute paths.
- **`clipwright-scene`** v0.3.0 and **`clipwright-frames`** v0.3.0: output artifact
  containment within `output_dir` is unchanged in behaviour (DC-GP-002 /
  `check_within_boundary`).

> **Migration note — symlink error code change**: Prior to v0.23.0, passing a path
> whose components contained a symbolic link could return `FILE_NOT_FOUND` (because
> the symlink target might not exist, or the existence check ran before the symlink
> walk). From v0.23.0 onward, all 17 satellite tools uniformly return
> `PATH_NOT_ALLOWED` for any path whose components contain a symlink (ADR-PP-2 /
> CWE-59). Callers that branch on `error.code == "FILE_NOT_FOUND"` to detect
> missing-path conditions must also handle `PATH_NOT_ALLOWED` from v0.23.0 onwards
> to avoid treating symlink-rejection as an unexpected error.

#### Version table — satellite packages (path-validation delegation; no API change)

| Package | Version |
|---------|---------|
| `clipwright-render` | v0.14.0 |
| `clipwright-transcribe` | v0.4.0 |
| `clipwright-silence` | v0.3.0 |
| `clipwright-noise` | v0.3.0 |
| `clipwright-loudness` | v0.3.0 |
| `clipwright-reframe` | v0.3.0 |
| `clipwright-bgm` | v0.3.0 |
| `clipwright-scene` | v0.3.0 |
| `clipwright-frames` | v0.3.0 |
| `clipwright-trim` | v0.2.0 |
| `clipwright-overlay` | v0.2.0 |
| `clipwright-sequence` | v0.2.0 |
| `clipwright-stabilize` | v0.2.0 |
| `clipwright-color` | v0.2.0 |
| `clipwright-speed` | v0.2.0 |
| `clipwright-text` | v0.2.0 |
| `clipwright-transition` | v0.2.0 |
| `clipwright-wrap` | v0.2.0 |

## [0.22.0] - 2026-06-26

Scene-driven frame extraction via `clipwright-frames`. `mode="scene"` gains a `scene_sample`
parameter (default `"midpoint"`) that emits one representative thumbnail per detected shot
interval instead of one frame per scene boundary, enabling contact-sheet workflows.

### Changed (`clipwright-frames` v0.2.0)

- **`scene_sample` parameter for `mode="scene"` (behaviour change — treat as breaking)**: `clipwright_extract_frames` with `mode="scene"` now accepts `scene_sample: "midpoint" | "start" | "boundary"` (default `"midpoint"`). The default is a behaviour change from v0.1.0, which always sampled at each scene boundary:
  - `"midpoint"` *(new default)* — emits one frame at the temporal midpoint of each shot interval, producing N+1 frames for N scene boundaries. Enables "one thumbnail per shot" contact-sheet workflows without manual timestamp computation.
  - `"start"` — emits one frame at the beginning of each shot interval (also N+1 frames for N boundaries).
  - `"boundary"` — emits one frame at each `scene_boundary` marker position, reproducing the pre-0.2.0 behaviour exactly (N frames for N boundaries, where N is the number of detected boundaries).
  - When `scene_sample="midpoint"` or `"start"` and no boundaries are present, one representative frame is extracted from the full clip (single shot, no warning). When `scene_sample="boundary"` and no boundaries are found, the tool returns no frames with a warning (unchanged from v0.1.0).
  - **Migration**: callers that relied on the v0.1.0 per-boundary behaviour should pass `scene_sample="boundary"` explicitly to restore the original output.

## [0.21.0] - 2026-06-25

Motion-tracking reframe. `clipwright-reframe` gains a content-aware `track` fit mode that keeps
a moving subject in frame when converting 16:9 footage to 9:16 vertical, and `clipwright-render`
materialises it as a time-varying, subject-following crop.

### Added (`clipwright-reframe` v0.2.0)

- **Motion-tracking reframe (`mode="track"`)**: at annotation time the tool detects the motion
  centroid over time and writes a normalised keyframe track (`[{t_s, cx, cy}]`, `cx`/`cy` in
  `0..1`) into the reframe directive for `clipwright-render` to materialise as a subject-following
  crop. Detection runs in a separate process using numpy, shipped as an **optional extra**
  (`pip install clipwright-reframe[track]`). When numpy is missing or detection fails, the tool
  **falls back to a static centre crop** (`ok: true`, no error) and emits a warning describing
  how to enable tracking — a vertical video is always produced. The keyframe track is capped at
  **80 keyframes** (an FFmpeg filter-expression length limit); the detector decimates to fit and
  render uses the received track as-is. The existing `crop` / `pad` / `blur_pad` modes and the
  default mode (`pad`) are unchanged. `anchor` / `pad_color` are not used in `track` mode.

### Added (`clipwright-render` v0.13.0)

- **Time-varying crop realisation for the `track` directive**: render materialises the
  motion-centroid keyframe track as a crop-from-source with piecewise-linear `x(t)` / `y(t)`
  centre interpolation, preserving the target aspect ratio, then scales to the target resolution.
  Existing `crop` / `pad` / `blur_pad` realisation is unchanged. A multi-source timeline combined
  with a `track` directive ignores the track and falls back to the existing per-clip cover crop,
  with a warning.

## [0.20.0] - 2026-06-25

Cut-aware caption guidance (spec4 "G"). When silence-cutting and burning transcribed
captions are combined, transcribing the un-cut source first leaves cues anchored to the
original timeline, so cuts that fall mid-phrase fragment the captions. This release adds a
render-side advisory that detects the condition and prescribes the correct order, plus
proactive workflow guidance in the tool descriptions and the README.

### Added (`clipwright-render` v0.12.0)

- **Cut-aware caption fragmentation advisory**: when subtitle re-timing finds that two or
  more caption cues were fragmented by cuts (split or clipped), `clipwright_render` now
  appends a single advisory to `warnings` prescribing the clean order — render the cut
  program first, then transcribe the rendered cut, then burn captions onto it
  (`cut -> render -> transcribe -> burn`). The advisory is additive: existing per-cue
  re-timing warnings, the envelope contract, and all render output are unchanged.

### Changed (`clipwright-render` v0.12.0 / `clipwright-transcribe` v0.3.1 / `clipwright-silence` v0.2.1)

- **Workflow guidance in tool descriptions**: the `clipwright_render`,
  `clipwright_transcribe`, and `clipwright_detect_silence` docstrings now point to the
  recommended "transcribe the cut program, not the original" order for burning captions
  onto silence-cut footage. A new "Recommended Workflows" section in `README.md` /
  `README.ja.md` documents the full chain.

### Fixed (`clipwright-silence` v0.2.1)

- **Reported version drift**: `clipwright_silence.__version__` lagged the packaged version
  (`0.1.1` while the distribution was `0.2.0`), so the `metadata["clipwright"]["version"]`
  written into output timelines under-reported the real version. The in-package version is
  now aligned, so emitted OTIO metadata reports the correct version.

## [0.19.0] - 2026-06-25

### Fixed (`clipwright-scene` v0.2.1)

- **`pyscenedetect` backend compatibility with PySceneDetect 0.7**: `clipwright_detect_scenes`
  previously invoked `scenedetect ... list-scenes -c` and parsed the scene list from stdout.
  The `-c` flag (CSV output to console) was removed in PySceneDetect 0.7, which now writes
  the scene list to a CSV file (`<video>-Scenes.csv`) in an output directory. The backend
  now runs `list-scenes -o <tmpdir> --skip-cuts -q` and reads the generated CSV, restoring
  content-aware scene detection. Users on PySceneDetect 0.7+ who encountered
  `SUBPROCESS_FAILED` from this backend are unblocked by this fix. The ffmpeg backend,
  envelope contract, threshold scaling, and zero-boundary guidance are unchanged.

## [0.18.0] - 2026-06-25

### Fixed (`clipwright-render` v0.11.1)

- Output chroma is now always pinned to `yuv420p` (4:2:0) by passing `-pix_fmt yuv420p`
  once at the encoder input. Previously, transition (xfade) outputs could negotiate to
  `yuvj444p` / H.264 High 4:4:4 Predictive and fail to play in common players (e.g.
  Windows "Movies & TV"). The fix covers all output paths — single-source, multi-source,
  concat, transition, subtitle, overlay, reframe, scale, BGM — for both software
  (libx264) and hardware (NVENC) encoders. Resolution, codec type, and output duration
  are unchanged; color range is not converted.

## [0.17.0] - 2026-06-24

### Added (`clipwright-scene` v0.2.0)

- **Zero-boundary guidance with concrete threshold suggestion**: When
  `clipwright_detect_scenes` returns 0 boundaries, the `summary` and `warnings`
  fields now include a backend-specific, actionable hint:
  - **ffmpeg backend**: suggests a specific halved threshold value (e.g.
    "Try lowering 'threshold' to 0.15 (currently 0.3)") and recommends switching
    to `backend='pyscenedetect'` for gradual or low-contrast cuts.
  - **pyscenedetect backend**: notes that further threshold lowering is unlikely
    to help and suggests the footage may be a single continuous shot.
  - When `threshold` is already at the practical floor (0.05), the hint replaces
    the generic "consider lowering" warning with an explanation that no further
    benefit is expected from lowering.

- **`DEPENDENCY_MISSING` error with install hint for PySceneDetect**: When
  `backend='pyscenedetect'` is requested but the `scenedetect` executable is not
  found, `clipwright_detect_scenes` returns `DEPENDENCY_MISSING` with the hint:
  `"Install PySceneDetect with 'pip install scenedetect', or set
  CLIPWRIGHT_SCENEDETECT to its executable path."` (available as the optional
  extra `clipwright-scene[pyscenedetect]`).

## [0.16.0] - 2026-06-24

### Added

- **`clipwright-transition` package (v0.1.0)**: New MCP tool `clipwright_add_transition`
  that annotates an OTIO timeline with crossfade / dissolve transitions between adjacent
  clip boundaries. Key characteristics:
  - Parameters: `timeline` (source OTIO path), `output` (destination OTIO path),
    `options.uniform` (a TransitionSpec with `type` and `duration_sec` applied to all
    boundaries) or `options.per_boundary` (a list of per-boundary TransitionSpec objects,
    each with `after_clip_index`, `type`, and `duration_sec`).
  - Transition directives are written to `metadata["clipwright"]["transition"]` in the
    OTIO timeline as a list of per-boundary descriptors (clip index, duration, type).
  - Non-destructive: input media and timeline are never modified; only a new `.otio` is
    written.
  - Does not require `CLIPWRIGHT_FFPROBE` or `CLIPWRIGHT_FFMPEG` (pure OTIO annotation
    tool; no ffprobe calls at annotation time).
  - MCP annotations: `readOnlyHint=true`, `destructiveHint=false`,
    `idempotentHint=true`, `openWorldHint=false`.
  - **v1 limitation**: per-boundary with gaps (not covering all internal clip boundaries)
    returns `UNSUPPORTED_OPERATION`; uniform mode and full per-boundary (all boundaries
    specified) are fully supported.

- **`clipwright-render` xfade / acrossfade support (v0.11.0)**: `clipwright_render` now
  reads the transition directive from `metadata["clipwright"]["transition"]` in the OTIO
  timeline and materialises crossfades via FFmpeg `xfade` (video) and `acrossfade`
  (audio) filters. Transition segments overlap
  at clip boundaries by `duration_sec`; the filter graph is restructured to feed the
  overlapping tails through the xfade/acrossfade chain before the final concat. Fully
  backward compatible: timelines without a `transition` directive render identically
  to before.

## [0.15.0] - 2026-06-22

### Added (`clipwright-transcribe` v0.3.0)

- **`data.backend` and `data.realtime_factor`**: The `clipwright_transcribe` MCP tool
  envelope now includes `data.backend` (`device`: `cuda | metal | cpu | unknown`,
  `detail`: sanitized fixed device label (CWE-209: no raw stderr / model path); e.g.
  `"CUDA"`, `"Metal"`, `"cpu"`, `""`) and `data.realtime_factor`
  (`audio_duration_sec / whisper_wall_seconds`; values **above 1.0 mean faster than
  realtime**). `data.whisper_wall_seconds` (raw wall-clock seconds spent in the whisper
  subprocess) is also surfaced.
- **`summary` backend reporting**: The one-line `summary` now reports the backend used
  (e.g. `" Backend: cuda (12.5x realtime)."`) for quick inspection without unpacking
  `data`.
- **GPU / CUDA acceleration guidance**: New `## GPU / CUDA Acceleration` section in
  `clipwright-transcribe/README.md` explains how to use a CUDA or Metal whisper.cpp
  build via `CLIPWRIGHT_WHISPER` (no code or parameter changes required). `data.backend`
  and `data.realtime_factor` fields enable runtime verification of the GPU path.

### Changed (`clipwright-transcribe` v0.3.0)

- **Version reconciliation**: `clipwright-transcribe` `__init__.py` and `pyproject.toml`
  versions unified to `0.3.0` (previously `0.1.1` / `0.2.0` respectively).

## [0.14.0] - 2026-06-22

### Added

- **`clipwright-overlay` package (v0.1.0)**: New MCP tool `clipwright_add_overlay`
  that annotates an OTIO timeline with a static image overlay (PNG/JPEG logo,
  watermark, lower-third graphic, end card) for a specified time range.
  Key characteristics:
  - Parameters: `image_path`, `start_sec`, `duration_sec`, `x` (default `(W-w)/2`),
    `y` (default `(H-h)/2`), `scale` (default `1.0`, range `(0, 8]`), `opacity`
    (default `1.0`, range `[0, 1]`), `fade_in_sec` (default `0.3`),
    `fade_out_sec` (default `0.3`).
  - `image_path` must be a `.png`, `.jpg`, `.jpeg`, or `.webp` file co-located
    under the output OTIO timeline's parent directory (same co-location boundary as
    `clipwright-render` sources, enabling round-trip portability). The path is stored
    as a POSIX relative path in the OTIO marker, so projects remain portable when
    moved between directories.
  - Maximum 64 image overlays per timeline (DC-GP-002).
  - Accumulate pattern: each call appends a new `image_overlay` marker
    (`image_0`, `image_1`, …) to the first video track (V1). Duplicate detection
    (idempotency) prevents adding the same overlay twice.
  - `x` / `y` accept FFmpeg overlay position expressions (e.g. `(W-w)/2`,
    `main_w-overlay_w-10`). Characters `: ; [ ] , '` and control characters are
    prohibited to prevent filtergraph injection.
  - Subprocess-free at annotation time; all FFmpeg calls are deferred to
    `clipwright-render`.
  - Non-destructive: input media and timeline are never modified; only a new `.otio`
    is written.
  - MCP annotations: `readOnlyHint=true`, `destructiveHint=false`,
    `idempotentHint=true`, `openWorldHint=false`.
  - Error codes: `PATH_NOT_ALLOWED`, `FILE_NOT_FOUND`, `INVALID_INPUT`,
    `UNSUPPORTED_OPERATION`.

- **`clipwright-render` image_overlay support (v0.10.0)**: `clipwright_render` now
  reads `image_overlay` markers from the OTIO timeline and materialises them into
  video. For each overlay the render pipeline:
  - Adds the image file as an extra `-i` input (after BGM, preserving the existing
    `bgm_index = len(input_sources)` invariant).
  - Inserts a two-segment FFmpeg filter chain per overlay (after `drawtext`, so image
    overlays appear on top of text):
    ```
    [{N}:v]scale=iw*{scale}:-2,format=rgba,colorchannelmixer=aa={opacity},
    fade=t=in:st={start}:d={fade_in}:alpha=1,fade=t=out:st={end-fade_out}:d={fade_out}:alpha=1[ov{i}];
    {base}[ov{i}]overlay=x='{x}':y='{y}':enable='between(t,{start},{end})'[outvimg{i}]
    ```
  - `scale=iw*{scale}:-2` (even-rounding for yuv420p compatibility).
  - `colorchannelmixer=aa={opacity}` sets constant opacity; `fade:alpha=1` multiplies
    the existing alpha, ramping it 0 → opacity → 0 over the fade windows.
  - `x` / `y` are single-quoted inside the overlay filter (consistent with `enable`
    and `drawtext`).
  - Backward compatible: existing render calls without `image_overlay` markers
    produce identical output.
  - Corrupt or undecodable image files cause `SUBPROCESS_FAILED` with a basename-only
    message and an actionable hint (CWE-209 compliant).

## [0.13.0] - 2026-06-22

### Added

- **`clipwright-sequence` package (v0.1.0)**: New MCP tool `clipwright_build_sequence`
  that assembles an ordered list of source media files into a single multi-source OTIO
  timeline (single V1 video track, A1 left empty) for concatenation by `clipwright-render`.
  Key characteristics:
  - Each `SequenceClip` entry specifies a source media path and an optional sub-range
    (`start_sec` / `end_sec`); omitting either defaults to the beginning / full source
    duration respectively.
  - Maximum 1000 clips per call (DC-GP-003).
  - All source files must be co-located under the output `.otio` file's parent
    directory (recursive subdirectories allowed). This mirrors `clipwright-render`'s
    source co-location boundary so that a sequence-produced `.otio` round-trips
    through render without `PATH_NOT_ALLOWED` errors (ADR-SEQ-6).
  - Symlink sources are unsupported; resolve symlinks before passing to this tool
    (DC-AS-005).
  - `total_duration_sec` in the result `data` is an approximate estimate based on
    the input clip ranges; the rendered output duration may differ slightly after
    per-frame normalization (DC-AM-003).
  - Non-destructive: input media files and existing OTIO files are never modified;
    only the new `.otio` is written.
  - Requires `CLIPWRIGHT_FFPROBE` (ffprobe is used to probe each source's duration
    and confirm video stream presence before building the timeline).
  - MCP annotations: `readOnlyHint=true`, `destructiveHint=false`,
    `idempotentHint=true`, `openWorldHint=false`.

## [0.12.0] - 2026-06-21

### Added

- **`clipwright-reframe` package (v0.1.0)**: New MCP tool `clipwright_reframe` that
  annotates a reframe directive (target resolution / fit mode / anchor) to
  `metadata["clipwright"]["reframe"]` in an OTIO timeline. The directive is applied
  as an FFmpeg filter chain by `clipwright-render` in a single render pass. Three fit
  modes are supported:
  - `crop` — scale to cover, then crop to the target aspect ratio (content at the
    edges may be lost; controlled by `anchor`).
  - `pad` — scale to fit (letterbox / pillarbox), then pad with a solid color
    (configurable via `pad_color`, default `"black"`).
  - `blur_pad` — scale the foreground to fit and overlay it over a blurred,
    cover-scaled background; popular for 16:9 → 9:16 vertical conversions for
    Shorts / Reels.
  - `anchor` controls the crop / pad alignment (9-direction: `top-left`, `top`,
    `top-right`, `left`, `center`, `right`, `bottom-left`, `bottom`, `bottom-right`).
  - Target dimensions (`target_w` / `target_h`) must be even and in the range 2–7680.
  - Accepts an optional existing `timeline` path; appends the directive to it
    (accumulate pattern, compatible with `clipwright-color`, `clipwright-stabilize`).
  - Non-destructive: only a new OTIO file is written; source media is never modified.

- **`clipwright-render` reframe support (v0.9.0)**: `clipwright_render` now reads the
  `reframe` directive from OTIO timeline metadata and inserts the corresponding FFmpeg
  filter chain (`scale`/`crop`/`pad`/`split→blur→overlay`) into the filtergraph before
  `drawtext`, so text positions resolve against the final frame size. Fully backward
  compatible: existing render calls without a reframe directive behave identically.

## [0.11.0] - 2026-06-20

### Added

- **`clipwright-render` hardware-accelerated encode/decode (v0.8.0)**: `clipwright_render`
  now supports GPU encoders and hardware-accelerated decode via three new `RenderOptions`
  fields:
  - `hw_encoder` (`"none"` / `"auto"` / `"nvenc"` / `"amf"` / `"qsv"` / `"vaapi"` /
    `"videotoolbox"`, default `"none"`): selects the hardware encoder.
    `"auto"` uses probe-then-test detection (checks `ffmpeg -encoders`, runs a
    1-frame throwaway encode to `-f null -`) and picks the first available vendor;
    falls back to `libx264` with a `warnings[]` entry if no hardware encoder is usable.
    Explicitly naming a vendor (e.g. `"nvenc"`) that fails to initialise returns
    `UNSUPPORTED_OPERATION` with an actionable hint. Render always completes.
  - `hwaccel_decode` (`bool`, default `false`): prepends `-hwaccel cuda/qsv/vaapi`
    before the input. v1 scope: frames are downloaded to system memory
    (`hwdownload`/`format`) before CPU filters (vidstab / eq / drawtext) so all
    existing filter chains remain compatible. Full HW↔HW filtergraph is out of v1 scope.
  - `quality` (`int` 0–51, optional): encoder-neutral quality knob. When unset,
    `crf` is used as before. For software encoders maps to `-crf`; for NVENC to
    `-cq` (+ `-rc vbr`); for QSV/VAAPI to `-global_quality`; for AMF to `-qp_i/-qp_p`.
    `-crf` is never emitted for hardware encoders.
  - **Verification status**: NVENC (`h264_nvenc` / `hevc_nvenc`) is **verified on the
    maintainer's dev box** (RTX-class GPU, Windows). AMD AMF, Intel QSV, VAAPI, and
    Apple VideoToolbox are **experimental — community verification needed**.
  - Fully backward compatible: existing calls without `hw_encoder` / `hwaccel_decode`
    / `quality` render identically to before (software `libx264` path unchanged).

## [0.10.0] - 2026-06-20

### Added

- **`clipwright-render` caption & overlay re-timing (v0.7.0)**: `clipwright_render`
  now re-times burned-in captions and text overlays from source-media time onto the
  post-edit program timeline. When the timeline contains silence cuts or
  `LinearTimeWarp` speed changes, subtitle cues (`.srt`) and `text_overlay` markers
  no longer land at the wrong frames. Key behaviours:
  - `retime_markers` option: `"auto"` (default) — re-time whenever the timeline
    contains cuts or warps; `"off"` — skip re-timing unconditionally (legacy behaviour).
  - **Non-destructive subtitle output**: when cues are re-timed a new file
    `{output_stem}.retimed.srt` is written alongside the rendered video; the original
    `.srt` is never modified.
  - **Identity timelines** (no cuts, no warps, single clip at 1× speed) produce no
    `.retimed.srt` and add no processing overhead.
  - **Cut-spanning cues/overlays** are split at cut boundaries; cues/overlays that
    fall entirely inside a removed range are dropped with a `warnings[]` entry.
  - **Format support**: `.srt` only. `.vtt` and `.ass` are skipped with a
    `warnings[]` entry (not yet supported).
  - **Multi-source timelines** (more than one distinct source file) are skipped with
    a `warnings[]` entry.
  - Fully backward compatible: existing render calls without subtitle options behave
    identically.

## [0.9.0] - 2026-06-20

### Added

- **`clipwright-trim` package (v0.1.0)**: New MCP tool `clipwright_trim` that builds a
  kept-range OTIO timeline from explicit time ranges. Specify `keep` ranges (segments to
  retain, in listed order) or `drop` ranges (segments to remove; the complement is kept);
  with no options it passes the whole clip through as a single renderable clip. Output is the
  same kept-range shape produced by `clipwright-silence`, so `clipwright-render` concatenates
  the segments with no changes. This fills the most basic editing gap — selecting which parts
  of a clip to keep — which previously had no in-suite path. Non-destructive: only a new OTIO
  file is written; the source media is never modified. Requires `CLIPWRIGHT_FFPROBE` to read
  the source duration.

## [0.8.0] - 2026-06-18

### Added

- **`clipwright-stabilize` package (v0.1.0)**: New MCP tool `clipwright_detect_shake` that
  analyses camera shake in a video file using FFmpeg `vidstabdetect` (requires an ffmpeg build
  compiled with `--enable-libvidstab`). Generates a binary `.trf` motion-analysis file alongside
  the output OTIO timeline. A `StabilizeDirective` is written to
  `metadata["clipwright"]["stabilize"]` recording `trf_path`, `shakiness`, `accuracy`,
  `smoothing`, and best-effort `severity` (0.0–1.0, `null` when the binary `.trf` cannot be
  parsed). The annotation is non-destructive; the `vidstabtransform` filter pass is materialized
  in a single render pass by `clipwright-render`. If libvidstab is absent, the tool returns
  `UNSUPPORTED_OPERATION` with installation guidance.
- **`clipwright-render` stabilize support (v0.6.0)**: `clipwright_render` now realizes
  stabilization annotations written by `clipwright_detect_shake`. The `vidstabtransform` filter
  is injected immediately after the `trim` stage and before `setpts` for each clip, ensuring
  stabilization is applied to source frames before any timing adjustments (speed changes, etc.).
  The `.trf` file is resolved via `cwd + relative basename` to work around vid.stab's inability
  to parse Windows absolute paths in filtergraph strings. Fully backward compatible: timelines
  without a `stabilize` directive render identically to before.

## [0.7.0] - 2026-06-18

### Added

- **`clipwright-color` package (v0.1.0)**: New MCP tool `clipwright_detect_color` that measures
  average luma (brightness) in a video file using FFmpeg `signalstats` and writes an `eq`
  color-correction directive to `metadata["clipwright"]["color"]` in an OTIO timeline. The
  directive specifies a derived `brightness` offset (`(target_luma - measured_luma) / 255`,
  clamped to `[-1, 1]`) alongside neutral `contrast`, `saturation`, and `gamma` values.
  The annotation is non-destructive; the `eq` filter pass is materialized in a single render
  pass by `clipwright-render`.
- **`clipwright-render` color eq support (v0.5.0)**: `clipwright_render` now realizes color
  correction annotations written by `clipwright_detect_color`. The `eq` filter is injected
  after the scale stage and before any subtitle/drawtext burn-in, applying brightness, contrast,
  saturation, and gamma adjustments in a single FFmpeg pass. Fully backward compatible: timelines
  without a `color` directive render identically to before.

## [0.6.0] - 2026-06-18

### Added

- **`clipwright-text` package (v0.1.0)**: New MCP tool `clipwright_add_text` that annotates an
  OTIO timeline with text overlay settings (position, font, size, color, timing). The annotation
  is non-destructive; the `drawtext` filter pass is materialized in a single render pass by
  `clipwright-render`.
- **`clipwright-render` drawtext support (v0.4.0)**: `clipwright_render` now realizes text
  overlay annotations written by `clipwright_add_text`, applying them via the FFmpeg `drawtext`
  filter in a single render pass.

## [0.5.0] - 2026-06-17

### Added

- **`clipwright-speed` package (v0.1.0)**: New MCP tool `clipwright_set_speed` that annotates a
  clip with a speed multiplier by writing an OTIO `LinearTimeWarp` effect. The annotation is
  non-destructive; the actual `setpts`/`atempo` filter pass is materialized in a single render
  pass by `clipwright-render`.
- **`clipwright-render` LinearTimeWarp support (v0.3.0)**: `clipwright_render` now realizes
  `LinearTimeWarp` effects written by `clipwright_set_speed`. Video timing is adjusted via the
  `setpts` filter and audio pitch-corrected via `atempo`, both applied in a single FFmpeg pass.

## [0.4.0] - 2026-06-17

### Added

- **`clipwright-frames` package (v0.1.0)**: New MCP tool `clipwright_extract_frames` for still-frame
  extraction from video. Supports three extraction modes — `interval` (fixed interval in seconds),
  `scene` (one frame per scene boundary from a `clipwright-scene` OTIO timeline), and `timestamps`
  (explicit list of timestamp positions). Writes extracted images to an output directory and returns
  OTIO markers and a JSON manifest as artifacts.

## [0.3.0] - 2026-06-16

### Added

- **`clipwright` core (v0.3.0)**: Added `otio_utils.get_markers()` to collect markers across
  tracks, optionally filtered by clipwright kind.
- **`clipwright-scene` package (v0.1.0)**: New MCP tool `clipwright_detect_scenes` for shot
  boundary detection. Detects scene transitions via FFmpeg's `scdet` filter (default) or
  PySceneDetect (optional backend) and writes detected boundaries as OTIO markers into a new
  or existing timeline. Supports configurable `threshold` (0–1), `min_scene_duration` (seconds),
  and `backend` (`ffmpeg` | `pyscenedetect`).
- **FFmpeg 8.x `scdet` output format support** (`clipwright-scene`): Added dual-regex parsing
  for the new `lavfi.scd.score: X, lavfi.scd.time: Y` format introduced in FFmpeg 8.x alongside
  the legacy `pts_time=X score=Y` format. The parser tries the new format first and falls back
  to the legacy format automatically.

### Changed

- **MCP `call_tool()` test protocol**: All package test suites (`clipwright-scene`,
  `clipwright-silence`, `clipwright-loudness`, `clipwright-noise`, `clipwright-transcribe`,
  `clipwright-bgm`, `clipwright-wrap`) now invoke tools via `mcp.call_tool()` (FastMCP test
  client) instead of calling Python functions directly. Tests now exercise the full MCP wire
  path including input validation, schema coercion, and `structuredContent` serialization.

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
