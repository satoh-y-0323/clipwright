"""test_schemas.py — Validation of ExportTimelineOptions / ExportChaptersOptions.

Contract surface (schemas) targets ~100% coverage (CONVENTIONS §test coverage).

These tests were written before clipwright_export.schemas existed (TDD Red,
per architecture-report-20260710-161944.md §3 (ADR-EX-2) / §9.1, where every
test in this module failed at collection time with ModuleNotFoundError).
Now that schemas.py has landed, this module serves as the contract
regression suite.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from clipwright_export.schemas import ExportChaptersOptions, ExportTimelineOptions


class TestExportTimelineOptions:
    def test_accepts_edl(self) -> None:
        opts = ExportTimelineOptions(format="edl")
        assert opts.format == "edl"

    def test_accepts_fcpxml(self) -> None:
        opts = ExportTimelineOptions(format="fcpxml")
        assert opts.format == "fcpxml"

    def test_rejects_invalid_format(self) -> None:
        """format is a Literal["edl", "fcpxml"]; other values raise ValidationError."""
        with pytest.raises(ValidationError):
            ExportTimelineOptions(format="mov")

    def test_format_is_required(self) -> None:
        """format has no default per architecture-report §3.1 (確定事項2)."""
        with pytest.raises(ValidationError):
            ExportTimelineOptions()

    def test_rejects_unknown_field(self) -> None:
        """model_config extra="forbid" (template convention, e.g. frames schemas)."""
        with pytest.raises(ValidationError):
            ExportTimelineOptions(format="edl", bogus_field="nope")


class TestExportChaptersOptions:
    def test_accepts_youtube(self) -> None:
        opts = ExportChaptersOptions(format="youtube")
        assert opts.format == "youtube"

    def test_accepts_ffmetadata(self) -> None:
        opts = ExportChaptersOptions(format="ffmetadata")
        assert opts.format == "ffmetadata"

    def test_rejects_invalid_format(self) -> None:
        """format is a Literal["youtube", "ffmetadata"]; other values raise ValidationError."""
        with pytest.raises(ValidationError):
            ExportChaptersOptions(format="mov")

    def test_format_is_required(self) -> None:
        with pytest.raises(ValidationError):
            ExportChaptersOptions()

    def test_marker_kind_default(self) -> None:
        """ADR-EX-2: marker_kind defaults to 'scene_boundary'."""
        opts = ExportChaptersOptions(format="youtube")
        assert opts.marker_kind == "scene_boundary"

    def test_marker_kind_accepts_arbitrary_string(self) -> None:
        """ADR-EX-2: marker_kind is a free-form string, not a Literal enum."""
        opts = ExportChaptersOptions(format="youtube", marker_kind="caption")
        assert opts.marker_kind == "caption"

    def test_rejects_unknown_field(self) -> None:
        """model_config extra="forbid" (template convention, e.g. frames schemas)."""
        with pytest.raises(ValidationError):
            ExportChaptersOptions(format="youtube", bogus_field="nope")
