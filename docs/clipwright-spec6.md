# Clipwright — NLE Interop: Start Timecode + Audio Layout (Round 6)

> Companion to `clipwright-spec.md` through `clipwright-spec5.md`. This document
> covers a single feature, shipped in one release: reflecting a source clip's
> start timecode and per-stream audio layout into generated OTIO so a timeline
> imports cleanly into DaVinci Resolve, instead of the prior spec5 catalogue's
> mix of defects and capability gaps. It is written from the shipped
> implementation (`clipwright.nle_interop`, `clipwright-render`'s relativization,
> `clipwright-export`'s EDL audio-track guard), not from the pre-implementation
> design proposal.

## Background

Reported by [@in3omnia](https://github.com/in3omnia) — the same reporter as
Issue #1 (spec5's D-series duration-drift fix) — in
[GitHub Issue #2](https://github.com/satoh-y-0323/clipwright/issues/2), with a
verified sample implementation attached (tested against DaVinci Resolve with
`.mp4`/`.mov`/`.mxf` sources in 1×2ch/2×1ch/8×1ch audio layouts, no exceptions,
warnings, or "Media Offline"). Two problems were reported:

1. **"Media Offline"**: business/broadcast source media (MXF etc.) commonly
   starts at a non-zero timecode (e.g. `01:00:00:00`); clipwright's generated
   OTIO always described clips as starting at frame 0, so Resolve's media
   matching (which is timecode-based) failed to relink the source.
2. **Missing audio streams/channels**: multi-stream, multi-channel audio (e.g.
   an 8-stream × 1-channel MXF) had no track/channel layout information in the
   generated OTIO, so Resolve did not expand the audio.

## 1. Timecode-origin media coordinate system (ADR-NI-1)

For source media that carries a start timecode, clipwright now represents
**both** a Clip's `source_range` and its `ExternalReference.available_range`
in **timecode-origin** frame coordinates (not 0-origin file-relative
coordinates), and sets `timeline.global_start_time` to the first V1 clip's
start timecode. This is OTIO's own intended semantics for these fields, and
it's the only representation Resolve's source-timecode-based media matching
can read correctly — shifting only `global_start_time` while leaving
`source_range` 0-origin does not resolve "Media Offline", because Resolve
matches per-clip against `source_range`, not the timeline-level field.

**render's job is to undo this at the last moment.** Because every downstream
render code path (cut-list construction, retiming, subtitle/overlay
re-timing) consumes `source_range` through the single `resolve_kept_ranges`
(video) / `resolve_bgm` (audio) chokepoint, relativization is applied exactly
once, there:

```
rel_start = source_range.start_time − available_range.start_time
```

implemented as `clipwright_render.plan._relativize_source_range_to_file_seconds`.
When `available_range` is `None` or starts at frame 0 (no NLE conform ran, or
the source has no timecode), the subtraction is a no-op and every existing
render code path — and the entire pre-existing render test suite — is
unaffected (NFR-1). `source_range.start_time < available_range.start_time`
(a source clip timecode earlier than the media's own start — a malformed or
adversarial timeline) is rejected as `INVALID_INPUT` with a fixed-literal
message (no interpolated OTIO values, consistent with clipwright's CWE-209
posture).

## 2. `clipwright.nle_interop` — the one-time conform step (ADR-NI-3)

All timecode/audio-layout logic lives in one new core module,
`src/clipwright/nle_interop.py`, exposing a single entry point:

```python
def conform_timeline_for_nle(
    timeline: otio.schema.Timeline,
    media_infos: Mapping[str, MediaInfo],
) -> list[str]:  # warnings, merge into the caller's ToolResult.warnings
```

Every *create*-type tool that builds a fresh OTIO timeline
(`trim`, `silence`, `transcribe`, `sequence`, `stabilize`, `loudness`, `noise`,
`color`, `reframe`) calls this once, immediately before `save_timeline`, with a
`media_infos` map keyed by the exact `target_url` string each Clip's
`ExternalReference` was built with (i.e. the same value the tool's own
`media_ref_for_otio` call produced — matching is a literal string comparison,
no normalization). This keeps the ~11 lines of Resolve-specific wire-format
knowledge out of every tool and in one place that a future Premiere/FCP conform
could sit beside.

`conform_timeline_for_nle` never raises — every tool calls it inside its
existing outer `except Exception` boundary, and a helper that could throw
there would defeat that guard. Timeline shapes outside its input contract
(no V1/video track, a clip with no `ExternalReference`, a clip with no
`source_range`, a `target_url` absent from `media_infos`) are each skipped
with a warning string rather than raised (see §5, `DC-GP-003`).

### 2.1 What it does, per call

1. **Idempotency guard (§2.2).** If `timeline.metadata["Resolve_OTIO"]`
   already exists, return `[]` immediately — a second conform on an
   already-conformed timeline is a pure no-op.
2. **Shift every Clip on every track** (not just V1 — see §4 on why this
   changed from the original V1-only design) whose `target_url` resolves to a
   `MediaInfo` with a start timecode `resolve_start_time` can parse: both
   `source_range.start_time` and the referenced
   `ExternalReference.available_range.start_time` are shifted forward by that
   timecode.
3. **Set `timeline.global_start_time`** from the first V1 **Clip** item (Gaps
   excluded) that was actually shifted — its post-shift
   `available_range.start_time` (ADR-NI-11). If V1 has no Clip at all,
   `global_start_time` is left unset with a warning.
4. **Mirror V1's item sequence onto N audio tracks** (§3), one per audio
   stream the source media(s) carry.
5. **Stamp `Resolve_OTIO` metadata** (§2.2) — Link Group IDs on V1 clips and
   their audio mirrors, plus the timeline-level version marker — applied
   **unconditionally**, even when audio mirroring degenerated to a
   skip+warning, so idempotency (step 1) is guaranteed after any first
   successful call (see §4).

### 2.2 `resolve_start_time` and fallback safety (ADR-NI-6, ADR-NI-13a)

```python
def resolve_start_time(media_info: MediaInfo) -> otio.opentime.RationalTime | None
```

Returns `None` — never raises — for any input it cannot confidently resolve:
no `start_timecode` string, no `duration` (rate source), the audio-only
sentinel rate (`1000.0`, meaning "no video stream" — see §4's rate-derivation
note), or a timecode string/rate `otio.opentime.from_timecode` rejects. Every
caller treats `None` as "leave this clip at its 0-origin, unshifted" — the
same as media with no timecode at all — which is what makes the whole feature
backward compatible by construction (NFR-1): a source with no usable timecode
behaves identically to a pre-conform timeline, just with an unconditional
`Resolve_OTIO` version marker and, if the source has audio, mirrored audio
tracks (audio-layout expansion is independent of whether timecode shifting
happened).

**Broadcast-rate handling (ADR-NI-13a).** Before calling `from_timecode`,
the rate is snapped with
`otio.opentime.RationalTime.nearest_valid_timecode_rate(rate)`. This was
added after a real-`otio` spike (0.18.1) established that `from_timecode`
**rejects** a rounded decimal broadcast rate (`29.97`, `23.976` —
`SMPTE timecode does not support this rate`) but **accepts** the exact
rational value the rate actually is (`30000/1001`, `24000/1001` —
including drop-frame timecode text, e.g. `01:00:00;00`). Because
clipwright's own rate source (`ffprobe`'s `avg_frame_rate`, parsed as an
exact `fps_num/fps_den` fraction — see §4's ADR-NI-12 note) is already the
exact rational form, this snap is defensive against float-representation
drift rather than a required conversion for clipwright's own data; it costs
nothing for rates that are already exact/valid. **Practical consequence:
NTSC 29.97, 23.976p, and drop-frame timecode are all in scope — no known
limitation exists for these rates.** No hand-rolled drop-frame arithmetic
was written; this relies entirely on OTIO's own timecode-rate machinery.

## 3. Audio track mirroring and the `Resolve_OTIO` wire format (ADR-NI-5, ADR-NI-10 rev. 2)

### 3.1 `Resolve_OTIO` is an explicit exception to the `metadata["clipwright"]` convention

Per `CONVENTIONS.md` §2, all clipwright-authored OTIO metadata lives under
`metadata["clipwright"]`. `Resolve_OTIO` is the one deliberate exception: its
key names and value shapes are dictated by DaVinci Resolve itself, and are
**transcribed verbatim** from Issue #2's verified sample implementation so
that Resolve can read them with zero clipwright-specific knowledge. The wire
format, exactly as written:

| Location | `Resolve_OTIO` value |
|---|---|
| `timeline.metadata` | `{"Resolve OTIO Meta Version": "1.0"}` |
| Each mirrored Audio `Track.metadata` | `{"Audio Type": "Mono"}` or `{"Audio Type": "Stereo"}` |
| Each mirrored audio `Clip.metadata` | `{"Channels": [{"Source Channel ID": c, "Source Track ID": stream_idx}, ...], "Link Group ID": k}` |
| Each V1 `Clip.metadata` | `{"Link Group ID": k}` (same `k` as its audio mirror(s)) |

`Source Channel ID` is `0..channels-1` within one audio stream; `Source Track
ID` is the 0-based ordinal of the audio *stream* among the source's audio
streams (not a channel count). `Audio Type` is `Mono` for a 1-channel stream
and `Stereo` for a 2-channel stream; anything else (3+ channels — 5.1 etc.) is
mapped to `Mono` with a warning, matching Issue #2's verified range (1ch/2ch
only — multichannel layout names beyond that are an explicit known limitation,
not attempted).

### 3.2 Link Group ID numbering (ADR-NI-11)

`Link Group ID` = the V1 Clip's 1-based ordinal among V1's **Clip** items only
(Gaps are not counted). This numbering is computed once, by a single function
(`_clip_ordinals`) keyed by `id(clip)`, and that same mapping is used both when
stamping the V1 clip and when stamping its audio mirror(s) — so the two sides
cannot disagree about which clip is "clip 3." A `silence`-style timeline with
Gaps between kept segments therefore still numbers its Link Groups
1, 2, 3, ... in V1-Clip order, skipping Gaps. This matches Issue #2's own
sample (a single clip, `Link Group ID: 1`).

### 3.3 The A1 adoption rule (ADR-NI-10 rev. 2)

An early design draft assumed every *create* tool left the Audio (A1) track
empty (the historical "A1 is empty" contract this document formally revises).
A code audit during implementation found that is not true: **five** of the
nine conform-wired tools — `stabilize`, `loudness`, `noise`, `color`,
`reframe` — already place a full-length audio mirror clip on A1 via their
shared `_add_full_clip` helper (this predates NLE interop and is covered by
its own existing test, `test_available_range.py::test_a1_clip_available_range_is_set`).
Only `trim`, `silence`, `transcribe`, `sequence` genuinely leave A1 empty.

Rather than degrade the five `_add_full_clip` tools to "skip audio mirroring,
warn every call" (which would make NLE interop effectively non-functional for
more than half its target tools), `_mirror_audio_tracks` inspects any existing
non-empty Audio track and decides between two outcomes:

- **Adoption (no warning)** — if the existing track's item sequence is an
  exact item-for-item mirror of V1 (`_a1_mirrors_v1`: same item count, same
  Clip/Gap kind at each position, and for Clips the same `target_url` and the
  same `source_range` — precisely the shape `_add_full_clip` produces), that
  track is **adopted** as the stream-0 mirror: its existing clips are stamped
  with `Resolve_OTIO` `Channels`/`Link Group ID` in place (no clip is created
  or replaced), the track gets `Audio Type`, and any further audio streams
  (`A2..AN`) are appended fresh from V1.
- **Skip + warning** — if a non-empty existing Audio track does **not** mirror
  V1 (e.g. a `clipwright_add_bgm` track from a prior accumulate step, or any
  other unrelated structure), audio mirroring is skipped entirely for that
  timeline: no new tracks, no `Resolve_OTIO` audio metadata. The timecode
  shift (§1) and the timeline-level idempotency marker (§3.4) are still
  applied regardless — only the audio-layout expansion degrades.
- An existing **empty** Audio track (the `trim`/`silence`/`transcribe`/
  `sequence` case) is reused directly for stream #0, same as if it didn't
  exist.

**Net effect: all nine conform-wired tools get full audio-layout support** —
the original design's "reframe is excluded from audio metadata" limitation
was withdrawn once this adoption path was added; it is not a limitation in
the shipped feature.

### 3.4 Idempotency marker is unconditional (ADR-NI-10)

Even on the degraded "skip + warning" path above, `_apply_resolve_metadata`
still unconditionally stamps `timeline.metadata["Resolve_OTIO"]` with the
version marker once the timecode-shift pass has run. This guarantees a second
`conform_timeline_for_nle` call against that same timeline is always a no-op
(§2.1 step 1) — including on the degraded path — so a timecode double-shift
can never occur structurally, regardless of which audio-mirroring branch a
given timeline took.

## 4. Design notes carried over from the architecture review

- **All-track shift, not V1-only (ADR-NI-10).** The original design shifted
  only V1 clips; because `stabilize`/`loudness`/`noise`/`color`/`reframe`
  already place an audio mirror clip directly on A1 (§3.3), that clip must be
  shifted too or the timeline ends up with inconsistent coordinate systems
  between V1 and A1. The shift pass therefore walks every track's every Clip,
  keyed by the same `media_infos` lookup.
- **`duration.rate` derivation (ADR-NI-12).** `resolve_start_time` interprets
  the timecode string against `media_info.duration.rate`. This is safe
  because `MediaInfo.duration.rate` is derived (in `media.py`) from the first
  *video* stream's `avg_frame_rate`; the only clips `conform_timeline_for_nle`
  ever shifts are ones with a resolved `MediaInfo` from a video-carrying
  source (audio-only media uses the `1000.0` sentinel rate and is excluded up
  front), so there is no separate rate source to reconcile.
- **`media_infos` key contract and silent-mismatch visibility (ADR-NI-9).**
  Because matching is a literal `target_url` string comparison, a caller that
  builds the map with a different helper/argument set than it used when
  writing the Clip's `ExternalReference` would silently no-op. To make that
  class of mistake visible instead of silent, `conform_timeline_for_nle`
  warns both directions: a Clip whose `target_url` has no `media_infos` entry
  ("clip media not found in media_infos"), and a `media_infos` entry no Clip
  ever referenced ("media_infos entry was not referenced by any clip").
- **Input-contract robustness (DC-GP-003).** `conform_timeline_for_nle`'s
  docstring states its input contract explicitly: a well-formed timeline as a
  clipwright *create* tool produces (a V1 track exists; clips normally carry
  an `ExternalReference` with a `source_range`). Anything outside that shape
  (a `MissingReference` clip, a clip with no `source_range`, a V1-less
  timeline) is skipped with a warning, never raised.

## 5. `clipwright-export` EDL audio-track guard (ADR-NI-7)

Per-stream audio track expansion (§3) can put more than two Audio tracks on a
timeline; the CMX3600 EDL adapter (`cmx_3600`) only supports up to two audio
tracks and otherwise raises `NotSupportedError` at write time, which would
turn every multi-stream-audio EDL export into a hard failure via
write-then-verify. `clipwright_export_timeline`'s `edl` path now **removes
all Audio tracks from the write-time deep copy** before calling the adapter
(the source OTIO on disk is never touched), after emitting its pre-existing
"were not written to the EDL" warning (now also reporting the removed track
count). Full removal — not "keep the first two" — was chosen deliberately:
keeping an arbitrary two of N tracks would be an arbitrary, silently lossy
choice with no principled way to pick which two, and it would contradict the
existing warning's own wording that no audio is carried into the EDL. `fcpxml`
export is unaffected — every Audio track (and its `Resolve_OTIO` metadata) is
written as-is.

## 6. Known limitations

- **`speed` (`LinearTimeWarp`) desync.** Applying `clipwright-speed` after
  audio-layout expansion changes V1's timing via a `LinearTimeWarp`, but the
  mirrored audio tracks' clips are not independently retimed to match — an
  NLE reopening the timeline after a `speed` edit may show V1 and its audio
  mirrors out of sync. **`clipwright-render`'s own output is unaffected** —
  render only consumes `[0:a]` from the *source media* directly (see below)
  and applies the same `LinearTimeWarp`-derived retiming to both video and
  audio at materialization time, so a rendered MP4 through a `speed` edit is
  correct regardless of this NLE-side desync. This is an NLE-side round-trip
  limitation only.
- **`render` does not consume the mirrored audio tracks.** `render` builds its
  program audio from `[0:a]` of each *source media file* directly (via
  `resolve_kept_ranges`/`resolve_bgm`, which only scan Video- and
  `kind=="bgm"`-tagged tracks); the per-stream Audio tracks §3 adds are
  read-only NLE furniture for external tools like Resolve. This is
  intentional — the Issue #2 scope is NLE interop, not multi-stream audio
  mixing inside `clipwright-render` itself — and is verified by an e2e that
  confirms rendered output's audio stream count/codec matches the
  single-source `[0:a]` path, unaffected by however many Audio tracks the
  timeline carries.
- **EDL export drops all audio (unchanged, now also for interop-expanded
  tracks — §5).** Already an existing, documented EDL limitation; per-stream
  expansion does not change it, only makes it apply to more timelines.
- **FCPXML does not round-trip `timeline.global_start_time`.** Writing an
  FCPXML with a non-zero `global_start_time` succeeds, but reading the
  written file back does not reproduce it (observed directly against the
  `fcpxml` adapter). Whether DaVinci Resolve itself honors the written
  sequence/record start timecode on **import** (as opposed to OTIO's own
  round-trip) is a Resolve-real-machine verification item, not something
  provable from the adapter alone.
- **Multi-source, mixed-timecode `sequence` per-clip matching is unverified
  on real Resolve.** `global_start_time` only ever reflects the *first* V1
  clip's timecode; clipwright's working assumption is that Resolve matches
  each subsequent clip against its own `source_range`/`available_range`
  (which are independently shifted per-source, §1) rather than relying on
  `global_start_time` for anything beyond the first clip. This is the
  single highest-value item for a DaVinci Resolve real-machine verification
  pass (alongside single-source and 8×1ch-audio timelines) before relying on
  this feature for mixed-source-timecode sequences in production.

## 7. Revision of the prior "A1 is empty" assumption

Design work prior to this feature (and in this document's antecedents)
informally treated V1-only Clip placement (A1 empty) as clipwright's create-
tool contract. §3.3 formally revises that: **five of nine** conform-wired
tools (`stabilize`, `loudness`, `noise`, `color`, `reframe`) place a
full-length mirror clip on A1 by design (pre-dating this feature, and covered
by their own existing `available_range` test). `clipwright.nle_interop`
accommodates both shapes (empty A1, and A1-mirrors-V1) without special-casing
per tool — see the adoption rule in §3.3.
