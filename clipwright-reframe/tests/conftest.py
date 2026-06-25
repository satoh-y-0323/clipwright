"""Shared fixtures for clipwright-reframe tests."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def tmp_media(tmp_path: Path) -> Path:
    """Create and return a dummy media file for testing (mp4 extension stub)."""
    path = tmp_path / "video.mp4"
    path.write_bytes(b"dummy media")
    return path


# ---------------------------------------------------------------------------
# Shared fake NamedTemporaryFile classes (CR-L-2).
#
# track_cli tests need to intercept tempfile.NamedTemporaryFile to inject
# synthetic raw bytes or track which paths were created.  Previously each test
# defined its own local _FakeTmpFile / _TrackingTmpFile inner class; that
# pattern duplicated the same code in four places.
#
# These module-level fixtures provide the same behaviour via pytest fixtures
# that return factory callables, so test methods can use them without repeating
# the class definitions.
# ---------------------------------------------------------------------------


class _FakeTmpFileBase:
    """Wraps a real NamedTemporaryFile (delete=False) with a compatible API.

    Subclasses may override _on_create() to customise behaviour.
    """

    _orig_ntf = staticmethod(tempfile.NamedTemporaryFile)

    def __init__(self, raw_bytes: bytes = b"", **kwargs: Any) -> None:
        self._real = self._orig_ntf(delete=False, suffix=".raw")
        self.name: str = self._real.name
        if raw_bytes:
            self._real.write(raw_bytes)
            self._real.flush()
        self._real.close()

    def close(self) -> None:
        pass

    def __enter__(self) -> _FakeTmpFileBase:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


@pytest.fixture
def fake_tmp_file_factory() -> Any:
    """Return a factory that creates a FakeTmpFile pre-populated with raw_bytes.

    Usage::
        def test_foo(fake_tmp_file_factory):
            factory = fake_tmp_file_factory(raw_bytes=b"...")
            with patch("tempfile.NamedTemporaryFile", side_effect=factory):
                ...
    """

    def _factory(raw_bytes: bytes = b"") -> Any:
        class _FakeTmpFile(_FakeTmpFileBase):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(raw_bytes=raw_bytes, **kwargs)

        return _FakeTmpFile

    return _factory


@pytest.fixture
def tracking_tmp_file_factory() -> Any:
    """Return a factory that records created temp file paths.

    Usage::
        def test_foo(tracking_tmp_file_factory):
            created, factory = tracking_tmp_file_factory()
            with patch("tempfile.NamedTemporaryFile", side_effect=factory):
                ...
            # created is a list of str paths that were opened.
    """

    def _factory() -> tuple[list[str], Any]:
        created: list[str] = []

        class _TrackingTmpFile(_FakeTmpFileBase):
            def __init__(self, **kwargs: Any) -> None:
                super().__init__(raw_bytes=b"", **kwargs)
                created.append(self.name)

        return created, _TrackingTmpFile

    return _factory


# ---------------------------------------------------------------------------
# OTIO AnyDictionary / AnyVector native-type patch (module-level, applied once)
#
# OTIO stores Python list/dict values as C++ AnyVector/AnyDictionary via
# pybind11.  When reading metadata back from an OTIO file, these types are
# returned as C++ objects with the following problems:
#
#   1. isinstance(AnyVector_instance, list) → False
#      (pybind11 type does not subclass list)
#
#   2. Underlying C++ object destroyed after parent Timeline is GC'd,
#      causing ValueError: "Underlying C++ AnyVector object has been destroyed"
#      when the AnyVector is held beyond the Timeline's lifetime.
#
# Fix: patch AnyDictionary.__getitem__ to recursively convert AnyDictionary →
# dict and AnyVector → list at access time (deep, eager materialization).
# This is applied AFTER a dummy OTIO write/read cycle that forces the plugin
# manifest (adapter registry) to be fully initialised, so the patch does not
# interfere with OTIO's own internal use of AnyDictionary during adapter
# discovery.
#
# The patch is narrow: only AnyDictionary.__getitem__ is replaced; OTIO's
# internal adapter/manifest/hook system calls get() or iterates the manifest
# AnyDictionaries using C++ code paths that do not go through Python
# __getitem__, so those paths are unaffected.
# ---------------------------------------------------------------------------


def _patch_otio_anydictionary_getitem() -> None:
    """Patch AnyDictionary.__getitem__ to return native Python types.

    Must be called AFTER a dummy OTIO write cycle so the plugin manifest is
    already initialised (adapter discovery uses __getitem__ internally via C++,
    but the Python-visible manifest AnyDictionaries are populated first).
    """
    try:
        import opentimelineio._otio as _otio
    except ImportError:
        return  # OTIO not available; skip

    # Capture the original __getitem__ in a closure so _to_native can call it
    # directly (avoiding infinite recursion through the patched version).
    _orig_gi = _otio.AnyDictionary.__getitem__

    def _to_native(obj: object) -> object:
        """Recursively convert AnyDictionary/AnyVector to plain Python types."""
        t = type(obj).__name__
        if t == "AnyDictionary":
            # Use _orig_gi to avoid re-entering the patched __getitem__.
            return {k: _to_native(_orig_gi(obj, k)) for k in obj}  # type: ignore[call-overload, arg-type]
        if t == "AnyVector":
            return [_to_native(x) for x in obj]  # type: ignore[union-attr]
        return obj

    def _new_gi(self: object, key: str) -> object:
        result = _orig_gi(self, key)  # type: ignore[call-overload]
        return _to_native(result)

    _otio.AnyDictionary.__getitem__ = _new_gi  # type: ignore[method-assign]


def _ensure_otio_initialised() -> None:
    """Force OTIO adapter/manifest initialisation via a dummy write/read cycle.

    AnyDictionary.__getitem__ must be patched AFTER OTIO has discovered and
    registered its adapters (otio_json etc.).  The adapter discovery code reads
    the built-in manifest JSON via C++ code that does NOT go through the Python
    __getitem__, but a dummy write/read cycle ensures the Python-side manifest
    cache is warm before we replace __getitem__.
    """
    try:
        import os
        import tempfile

        import opentimelineio as otio
    except ImportError:
        return

    with tempfile.NamedTemporaryFile(suffix=".otio", delete=False) as f:
        dummy_path = f.name
    try:
        otio.adapters.write_to_file(otio.schema.Timeline(), dummy_path)
        otio.adapters.read_from_file(dummy_path)
    except Exception:  # noqa: BLE001
        pass  # initialisation is best-effort; patch will still apply
    finally:
        import contextlib

        with contextlib.suppress(OSError):
            os.unlink(dummy_path)


# Apply patches at module import time.
# Order matters: initialise OTIO first, then patch __getitem__.
_ensure_otio_initialised()
_patch_otio_anydictionary_getitem()
