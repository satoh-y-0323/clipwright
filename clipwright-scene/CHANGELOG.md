# Changelog

All notable changes to `clipwright-scene` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-06-16

### Added

- **`clipwright_detect_scenes` MCP tool**: Detects shot boundaries (scene transitions) in a
  video file and writes them as OTIO markers into a new or existing OpenTimelineIO timeline.
- **FFmpeg backend** (default): Uses the `scdet` filter via `ffmpeg -vf scdet=threshold=...`.
  Supports both the new FFmpeg 8.x `lavfi.scd.score: X, lavfi.scd.time: Y` output format and
  the legacy `pts_time=X score=Y` format (auto-detected).
- **PySceneDetect backend** (optional): Invokes the `scenedetect` CLI as an alternative backend.
  Requires the `pyscenedetect` optional dependency group.
- **Configurable options** (`DetectScenesOptions`): `threshold` (0.0–1.0, default 0.3),
  `min_scene_duration` (seconds, default 1.0), `backend` (`ffmpeg` | `pyscenedetect`).
- **OTIO marker output**: Each detected boundary is written as a green OTIO marker at the
  transition frame, with `confidence` score, `backend`, and `tool` stored under
  `marker.metadata["clipwright"]`.
- **Timeline augmentation**: Accepts an optional `timeline` argument to add markers to an
  existing `.otio` file; without it, creates a new single-clip timeline from the media.
- **MCP annotations**: `readOnlyHint=False` (writes OTIO output), `destructiveHint=False`
  (input media never modified), `idempotentHint=True`.
- **Path boundary check**: Output path and optional timeline path are validated against a
  directory boundary to prevent path traversal.
- **Security hardening**: `DetectScenesOptions` uses `ConfigDict(extra="forbid",
  allow_inf_nan=False)`. Threshold values expanded into FFmpeg/PySceneDetect arguments are
  validated as Pydantic-constrained floats and locked by regex tests.
