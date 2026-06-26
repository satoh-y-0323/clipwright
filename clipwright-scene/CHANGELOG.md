# Changelog

All notable changes to `clipwright-scene` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-06-26

### Changed

- **Internal: boundary helper consolidated into core** — The local `_check_within_boundary`
  helper in `detect.py` has been replaced by `clipwright.pathpolicy.check_within_boundary`
  (introduced in `clipwright>=0.4.0`). Artifact-containment behaviour is identical; only
  the implementation is consolidated. Requires `clipwright>=0.4.0`.

## [0.2.1] - 2026-06-25

### Fixed

- **`pyscenedetect` backend compatibility with PySceneDetect 0.7**: The backend previously
  invoked `scenedetect ... list-scenes -c` and parsed scene boundaries from stdout. The `-c`
  (CSV-to-console) flag was removed in PySceneDetect 0.7, which writes the scene list to a
  CSV file instead. The backend now runs `list-scenes -o <tmpdir> --skip-cuts -q` and reads
  the generated `<video>-Scenes.csv`, restoring content-aware scene detection against
  PySceneDetect 0.7+. The ffmpeg backend, envelope, threshold scaling, and zero-boundary
  guidance are unchanged.

## [0.2.0] - 2026-06-24

### Added

- **Zero-boundary guidance**: The tool now returns a `hint` in results when no scenes are
  detected, suggesting concrete threshold values to try (e.g. lower the `threshold` from the
  default 0.3 toward 0.1–0.15 for high-motion content, or switch to the `pyscenedetect` backend
  for content where FFmpeg's `scdet` filter misses cuts).
- **`DEPENDENCY_MISSING` error for missing `scenedetect` CLI**: When `backend="pyscenedetect"`
  is requested but the `scenedetect` CLI is not found on PATH or at the path given by the
  `CLIPWRIGHT_SCENEDETECT` environment variable, the tool now returns a structured error
  `{ ok: false, error: { code: "DEPENDENCY_MISSING", message: ..., hint: ... } }` with an
  explicit install hint (`pip install scenedetect[opencv]` or `uv add clipwright-scene[pyscenedetect]`)
  instead of a raw `FileNotFoundError`.

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
