"""test_schemas.py — Full tests for DetectNoiseOptions / DenoiseDirective / AfftdnParams.

Target coverage for contract surface (schemas) is effectively 100% (CONVENTIONS §TestCoverage).

Verification points:
  - DetectNoiseOptions: valid values / defaults / invalid ValidationError for backend/strength
  - AfftdnParams: range constraints / defaults / invalid ValidationError for nr/nf/nt
  - DenoiseDirective: strict validation / model_dump roundtrip / invalid kind/backend
  - Confirm that the track field does not exist (ADR-N7 deprecation check)
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from clipwright_noise.schemas import AfftdnParams, DenoiseDirective, DetectNoiseOptions

# ===========================================================================
# DetectNoiseOptions
# ===========================================================================


class TestDetectNoiseOptionsDefaults:
    """Verify default construction and default values."""

    def test_defaults_backend_is_afftdn(self) -> None:
        opts = DetectNoiseOptions()
        assert opts.backend == "afftdn"

    def test_defaults_strength_is_medium(self) -> None:
        opts = DetectNoiseOptions()
        assert opts.strength == "medium"

    def test_build_with_no_args(self) -> None:
        opts = DetectNoiseOptions()
        assert opts.backend == "afftdn"
        assert opts.strength == "medium"


class TestDetectNoiseOptionsBackend:
    """Valid and invalid value tests for the backend field."""

    def test_backend_afftdn_accepted(self) -> None:
        opts = DetectNoiseOptions(backend="afftdn")
        assert opts.backend == "afftdn"

    def test_backend_deepfilternet_accepted(self) -> None:
        opts = DetectNoiseOptions(backend="deepfilternet")
        assert opts.backend == "deepfilternet"

    @pytest.mark.parametrize(
        "invalid",
        ["ffmpeg", "whisper", "AFFTDN", "", "afftdn2", "none", "deepfilter"],
    )
    def test_invalid_backend_raises_validation_error(self, invalid: str) -> None:
        with pytest.raises(ValidationError):
            DetectNoiseOptions(backend=invalid)  # type: ignore[arg-type]


class TestDetectNoiseOptionsStrength:
    """Valid and invalid value tests for the strength field."""

    @pytest.mark.parametrize("s", ["light", "medium", "strong"])
    def test_valid_strength_accepted(self, s: str) -> None:
        opts = DetectNoiseOptions(strength=s)
        assert opts.strength == s

    @pytest.mark.parametrize(
        "invalid",
        ["extreme", "max", "LOW", "MEDIUM", "", "weak", "high"],
    )
    def test_invalid_strength_raises_validation_error(self, invalid: str) -> None:
        with pytest.raises(ValidationError):
            DetectNoiseOptions(strength=invalid)  # type: ignore[arg-type]


class TestDetectNoiseOptionsNoTrackField:
    """Confirm that the track field does not exist (ADR-N7: deprecated)."""

    def test_track_field_does_not_exist(self) -> None:
        """The track field must not exist in schemas."""
        assert "track" not in DetectNoiseOptions.model_fields, (
            "ADR-N7: track field is deprecated. It must not be present in DetectNoiseOptions."
        )

    def test_extra_track_kwarg_does_not_create_field(self) -> None:
        """Passing track=0 must not create a track attribute on the DetectNoiseOptions instance.

        Pydantic v2 ignores extra fields by default.
        No exception is raised, but confirm that the instance has no track field.
        """
        opts = DetectNoiseOptions(track=0)  # type: ignore[call-arg]
        assert not hasattr(opts, "track"), (
            "ADR-N7: track is deprecated. A track attribute must not appear on the instance."
        )


class TestDetectNoiseOptionsCombinations:
    """Exhaustive test of all valid combinations."""

    @pytest.mark.parametrize("backend", ["afftdn", "deepfilternet"])
    @pytest.mark.parametrize("strength", ["light", "medium", "strong"])
    def test_all_valid_combinations_accepted(self, backend: str, strength: str) -> None:
        opts = DetectNoiseOptions(backend=backend, strength=strength)
        assert opts.backend == backend
        assert opts.strength == strength


# ===========================================================================
# AfftdnParams
# ===========================================================================


class TestAfftdnParamsDefaults:
    """Verify the default value of nt."""

    def test_nt_default_is_w(self) -> None:
        p = AfftdnParams(nr=12.0, nf=-50.0)
        assert p.nt == "w"


class TestAfftdnParamsNr:
    """Range constraint [0.01, 97] for the nr field."""

    @pytest.mark.parametrize("nr", [0.01, 6.0, 12.0, 24.0, 50.0, 97.0])
    def test_valid_nr_accepted(self, nr: float) -> None:
        p = AfftdnParams(nr=nr, nf=-50.0)
        assert p.nr == pytest.approx(nr)

    def test_nr_at_lower_boundary_0_01_accepted(self) -> None:
        p = AfftdnParams(nr=0.01, nf=-50.0)
        assert p.nr == pytest.approx(0.01)

    def test_nr_at_upper_boundary_97_accepted(self) -> None:
        p = AfftdnParams(nr=97.0, nf=-50.0)
        assert p.nr == pytest.approx(97.0)

    def test_nr_zero_rejected(self) -> None:
        """nr=0.0 violates ge=0.01."""
        with pytest.raises(ValidationError):
            AfftdnParams(nr=0.0, nf=-50.0)

    def test_nr_below_lower_boundary_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AfftdnParams(nr=-1.0, nf=-50.0)

    def test_nr_above_upper_boundary_rejected(self) -> None:
        """nr=98.0 violates le=97."""
        with pytest.raises(ValidationError):
            AfftdnParams(nr=98.0, nf=-50.0)

    def test_nr_97_01_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AfftdnParams(nr=97.01, nf=-50.0)


class TestAfftdnParamsNf:
    """Range constraint [-80, -20] for the nf field."""

    @pytest.mark.parametrize("nf", [-80.0, -70.0, -50.0, -30.0, -20.0])
    def test_valid_nf_accepted(self, nf: float) -> None:
        p = AfftdnParams(nr=12.0, nf=nf)
        assert p.nf == pytest.approx(nf)

    def test_nf_at_lower_boundary_neg80_accepted(self) -> None:
        p = AfftdnParams(nr=12.0, nf=-80.0)
        assert p.nf == pytest.approx(-80.0)

    def test_nf_at_upper_boundary_neg20_accepted(self) -> None:
        p = AfftdnParams(nr=12.0, nf=-20.0)
        assert p.nf == pytest.approx(-20.0)

    def test_nf_below_neg80_rejected(self) -> None:
        """nf=-81.0 violates ge=-80."""
        with pytest.raises(ValidationError):
            AfftdnParams(nr=12.0, nf=-81.0)

    def test_nf_above_neg20_rejected(self) -> None:
        """nf=-10.0 violates le=-20."""
        with pytest.raises(ValidationError):
            AfftdnParams(nr=12.0, nf=-10.0)

    def test_nf_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AfftdnParams(nr=12.0, nf=0.0)

    def test_nf_positive_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AfftdnParams(nr=12.0, nf=10.0)


class TestAfftdnParamsNt:
    """Literal["w", "v"] constraint for the nt field."""

    def test_nt_w_accepted(self) -> None:
        p = AfftdnParams(nr=12.0, nf=-50.0, nt="w")
        assert p.nt == "w"

    def test_nt_v_accepted(self) -> None:
        p = AfftdnParams(nr=6.0, nf=-40.0, nt="v")
        assert p.nt == "v"

    @pytest.mark.parametrize("invalid_nt", ["x", "W", "V", "white", "vinyl", "", "n"])
    def test_invalid_nt_rejected(self, invalid_nt: str) -> None:
        with pytest.raises(ValidationError):
            AfftdnParams(nr=12.0, nf=-50.0, nt=invalid_nt)  # type: ignore[arg-type]


class TestAfftdnParamsStrengthMapping:
    """Confirm that the fixed strength→nr mapping values (light=6/medium=12/strong=24) are accepted by the schema."""

    def test_nr_6_accepted_for_light_strength(self) -> None:
        p = AfftdnParams(nr=6.0, nf=-50.0)
        assert p.nr == pytest.approx(6.0)

    def test_nr_12_accepted_for_medium_strength(self) -> None:
        p = AfftdnParams(nr=12.0, nf=-50.0)
        assert p.nr == pytest.approx(12.0)

    def test_nr_24_accepted_for_strong_strength(self) -> None:
        p = AfftdnParams(nr=24.0, nf=-50.0)
        assert p.nr == pytest.approx(24.0)


# ===========================================================================
# DenoiseDirective
# ===========================================================================


class TestDenoiseDirectiveAfftdn:
    """DenoiseDirective construction for the afftdn backend."""

    def test_construct_afftdn_directive(self) -> None:
        d = DenoiseDirective(
            tool="clipwright-noise",
            version="0.1.0",
            kind="denoise",
            backend="afftdn",
            params={"nr": 12.0, "nf": -50.0, "nt": "w"},
            measured_noise_floor_db=-55.0,
        )
        assert d.backend == "afftdn"
        assert d.kind == "denoise"
        assert d.measured_noise_floor_db == pytest.approx(-55.0)
        assert d.params == {"nr": 12.0, "nf": -50.0, "nt": "w"}

    def test_measured_noise_floor_db_optional_defaults_none(self) -> None:
        d = DenoiseDirective(
            tool="clipwright-noise",
            version="0.1.0",
            kind="denoise",
            backend="afftdn",
            params={"nr": 12.0, "nf": -50.0, "nt": "w"},
        )
        assert d.measured_noise_floor_db is None

    def test_tool_and_version_stored(self) -> None:
        d = DenoiseDirective(
            tool="clipwright-noise",
            version="1.2.3",
            kind="denoise",
            backend="afftdn",
            params={},
        )
        assert d.tool == "clipwright-noise"
        assert d.version == "1.2.3"


class TestDenoiseDirectiveDeepfilternet:
    """DenoiseDirective construction for the deepfilternet backend (params={} fixed)."""

    def test_construct_deepfilternet_with_empty_params(self) -> None:
        d = DenoiseDirective(
            tool="clipwright-noise",
            version="0.1.0",
            kind="denoise",
            backend="deepfilternet",
            params={},
        )
        assert d.backend == "deepfilternet"
        assert d.params == {}
        assert d.measured_noise_floor_db is None


class TestDenoiseDirectiveValidationErrors:
    """ValidationError on invalid values."""

    def test_invalid_kind_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            DenoiseDirective(
                tool="t",
                version="0.1.0",
                kind="noise",  # type: ignore[arg-type]
                backend="afftdn",
                params={},
            )

    def test_invalid_backend_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            DenoiseDirective(
                tool="t",
                version="0.1.0",
                kind="denoise",
                backend="unknown",  # type: ignore[arg-type]
                params={},
            )

    @pytest.mark.parametrize(
        "invalid_backend",
        ["ffmpeg", "deepfilter", "AFFTDN", ""],
    )
    def test_various_invalid_backends_rejected(self, invalid_backend: str) -> None:
        with pytest.raises(ValidationError):
            DenoiseDirective(
                tool="t",
                version="0.1.0",
                kind="denoise",
                backend=invalid_backend,  # type: ignore[arg-type]
                params={},
            )


class TestDenoiseDirectiveModelDump:
    """Roundtrip consistency of model_dump → reconstruct."""

    def test_model_dump_roundtrip_afftdn(self) -> None:
        d = DenoiseDirective(
            tool="clipwright-noise",
            version="0.1.0",
            kind="denoise",
            backend="afftdn",
            params={"nr": 12.0, "nf": -50.0, "nt": "w"},
        )
        d2 = DenoiseDirective(**d.model_dump())
        assert d2.backend == d.backend
        assert d2.params == d.params
        assert d2.kind == d.kind
        assert d2.tool == d.tool
        assert d2.version == d.version
        assert d2.measured_noise_floor_db == d.measured_noise_floor_db

    def test_model_dump_roundtrip_deepfilternet(self) -> None:
        d = DenoiseDirective(
            tool="clipwright-noise",
            version="0.1.0",
            kind="denoise",
            backend="deepfilternet",
            params={},
            measured_noise_floor_db=-42.5,
        )
        d2 = DenoiseDirective(**d.model_dump())
        assert d2.backend == "deepfilternet"
        assert d2.params == {}
        assert d2.measured_noise_floor_db == pytest.approx(-42.5)

    def test_model_dump_includes_all_fields(self) -> None:
        d = DenoiseDirective(
            tool="clipwright-noise",
            version="0.1.0",
            kind="denoise",
            backend="afftdn",
            params={"nr": 6.0, "nf": -60.0, "nt": "v"},
            measured_noise_floor_db=-65.0,
        )
        dumped = d.model_dump()
        assert "tool" in dumped
        assert "version" in dumped
        assert "kind" in dumped
        assert "backend" in dumped
        assert "params" in dumped
        assert "measured_noise_floor_db" in dumped


class TestDenoiseDirectiveAfftdnParamsRevalidation:
    """Schema check for the scenario where render re-validates via AfftdnParams(**params)."""

    def test_afftdn_params_can_be_validated_from_directive_params(self) -> None:
        d = DenoiseDirective(
            tool="clipwright-noise",
            version="0.1.0",
            kind="denoise",
            backend="afftdn",
            params={"nr": 24.0, "nf": -40.0, "nt": "w"},
        )
        # render re-validates with AfftdnParams(**d.params)
        afftdn = AfftdnParams(**d.params)
        assert afftdn.nr == pytest.approx(24.0)
        assert afftdn.nf == pytest.approx(-40.0)
        assert afftdn.nt == "w"


class TestDenoiseDirectiveMaxLength:
    """Confirm that tool / version fields have a max_length=64 constraint (SR-L-1)."""

    def test_tool_at_max_length_64_accepted(self) -> None:
        long_tool = "t" * 64
        d = DenoiseDirective(
            tool=long_tool,
            version="0.1.0",
            kind="denoise",
            backend="afftdn",
            params={},
        )
        assert len(d.tool) == 64

    def test_tool_over_max_length_rejected(self) -> None:
        """tool with 65+ characters raises ValidationError (SR-L-1)."""
        with pytest.raises(ValidationError):
            DenoiseDirective(
                tool="t" * 65,
                version="0.1.0",
                kind="denoise",
                backend="afftdn",
                params={},
            )

    def test_version_at_max_length_64_accepted(self) -> None:
        long_version = "1" * 64
        d = DenoiseDirective(
            tool="clipwright-noise",
            version=long_version,
            kind="denoise",
            backend="afftdn",
            params={},
        )
        assert len(d.version) == 64

    def test_version_over_max_length_rejected(self) -> None:
        """version with 65+ characters raises ValidationError (SR-L-1)."""
        with pytest.raises(ValidationError):
            DenoiseDirective(
                tool="clipwright-noise",
                version="1" * 65,
                kind="denoise",
                backend="afftdn",
                params={},
            )


class TestDenoiseDirectiveMeasuredNoiseFloor:
    """Range constraints and inf/nan rejection for measured_noise_floor_db (SR-L-3)."""

    def test_measured_valid_minus_100_accepted(self) -> None:
        d = DenoiseDirective(
            tool="t",
            version="0.1.0",
            kind="denoise",
            backend="afftdn",
            params={},
            measured_noise_floor_db=-100.0,
        )
        assert d.measured_noise_floor_db == pytest.approx(-100.0)

    def test_measured_zero_accepted(self) -> None:
        d = DenoiseDirective(
            tool="t",
            version="0.1.0",
            kind="denoise",
            backend="afftdn",
            params={},
            measured_noise_floor_db=0.0,
        )
        assert d.measured_noise_floor_db == pytest.approx(0.0)

    def test_measured_positive_rejected(self) -> None:
        """Noise floor cannot be positive (noise floor is 0 dB or below)."""
        with pytest.raises(ValidationError):
            DenoiseDirective(
                tool="t",
                version="0.1.0",
                kind="denoise",
                backend="afftdn",
                params={},
                measured_noise_floor_db=1.0,
            )

    def test_measured_below_minus_200_rejected(self) -> None:
        """Values below -200 dB are physically meaningless and are rejected."""
        with pytest.raises(ValidationError):
            DenoiseDirective(
                tool="t",
                version="0.1.0",
                kind="denoise",
                backend="afftdn",
                params={},
                measured_noise_floor_db=-201.0,
            )

    def test_measured_inf_rejected(self) -> None:
        """inf is rejected (SR-L-3)."""
        import math

        with pytest.raises(ValidationError):
            DenoiseDirective(
                tool="t",
                version="0.1.0",
                kind="denoise",
                backend="afftdn",
                params={},
                measured_noise_floor_db=math.inf,
            )

    def test_measured_neg_inf_rejected(self) -> None:
        """-inf is rejected (SR-L-3)."""
        import math

        with pytest.raises(ValidationError):
            DenoiseDirective(
                tool="t",
                version="0.1.0",
                kind="denoise",
                backend="afftdn",
                params={},
                measured_noise_floor_db=-math.inf,
            )

    def test_measured_nan_rejected(self) -> None:
        """nan is rejected (SR-L-3)."""
        import math

        with pytest.raises(ValidationError):
            DenoiseDirective(
                tool="t",
                version="0.1.0",
                kind="denoise",
                backend="afftdn",
                params={},
                measured_noise_floor_db=math.nan,
            )

    def test_measured_none_accepted(self) -> None:
        """None is valid (fallback when measurement is unavailable)."""
        d = DenoiseDirective(
            tool="t",
            version="0.1.0",
            kind="denoise",
            backend="afftdn",
            params={},
            measured_noise_floor_db=None,
        )
        assert d.measured_noise_floor_db is None
