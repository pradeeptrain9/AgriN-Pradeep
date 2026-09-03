"""Soil source-chain tests. No network: the SoilGrids parser is tested against
captured response shapes, including the all-null shape India actually returns.
"""

import pytest

from app.providers.soil import (
    SoilSource,
    fallback_profile,
    from_feel_test,
    from_soil_health_card,
    parse_soilgrids,
    texture_from_feel,
)


def _layer(name, d_factor, values):
    return {
        "name": name,
        "unit_measure": {"d_factor": d_factor},
        "depths": [
            {"label": label, "values": {"mean": v}} for label, v in values
        ],
    }


BRAZIL_RESPONSE = {
    "properties": {
        "layers": [
            _layer("clay", 10, [("0-5cm", 222), ("5-15cm", 227)]),
            _layer("sand", 10, [("0-5cm", 612), ("5-15cm", 607)]),
            _layer("silt", 10, [("0-5cm", 166), ("5-15cm", 165)]),
            _layer("phh2o", 10, [("0-5cm", 50), ("5-15cm", 51)]),
            _layer("soc", 10, [("0-5cm", 195), ("5-15cm", 192)]),
        ]
    }
}

# India returns exactly this: correct structure, every mean null.
INDIA_RESPONSE = {
    "properties": {
        "layers": [
            _layer("clay", 10, [("0-5cm", None), ("5-15cm", None)]),
            _layer("sand", 10, [("0-5cm", None), ("5-15cm", None)]),
            _layer("silt", 10, [("0-5cm", None), ("5-15cm", None)]),
        ]
    }
}


class TestSoilGridsParsing:
    def test_applies_d_factor(self):
        values = parse_soilgrids(BRAZIL_RESPONSE)
        # 222 and 227 cg/kg over 5 cm and 10 cm depth weights.
        assert values["clay"] == pytest.approx((222 * 5 + 227 * 10) / 15 / 10, abs=0.01)
        assert values["phh2o"] == pytest.approx((50 * 5 + 51 * 10) / 15 / 10, abs=0.01)

    def test_depth_weighting_favours_thicker_layers(self):
        payload = {"properties": {"layers": [_layer("soc", 10, [("0-5cm", 100), ("5-15cm", 200)])]}}
        # Weighted toward the 10 cm layer, so above the arithmetic mean of 15.
        assert parse_soilgrids(payload)["soc"] > 15.0

    def test_null_layers_are_dropped_not_zeroed(self):
        """The India case. Nulls must vanish, never become 0."""
        values = parse_soilgrids(INDIA_RESPONSE)
        assert values == {}
        assert "clay" not in values

    def test_partial_nulls_keep_the_good_depths(self):
        payload = {
            "properties": {
                "layers": [_layer("clay", 10, [("0-5cm", 300), ("5-15cm", None)])]
            }
        }
        assert parse_soilgrids(payload)["clay"] == pytest.approx(30.0)


class TestFeelTest:
    def test_maps_answers_to_textures(self):
        assert texture_from_feel("gritty_no_ball").name == "sand"
        assert texture_from_feel("sticky_long_ribbon").name == "clay"
        assert texture_from_feel("medium_ribbon").name == "clay loam"

    def test_accepts_a_direct_texture_name(self):
        assert texture_from_feel("silt_loam").name == "silt loam"

    def test_rejects_nonsense(self):
        with pytest.raises(ValueError):
            texture_from_feel("tastes_purple")

    def test_profile_is_medium_confidence_and_says_why(self):
        profile = from_feel_test("gritty_ball_no_ribbon")
        assert profile.source is SoilSource.FEEL_TEST
        assert profile.confidence == "medium"
        assert profile.notes


class TestSoilHealthCard:
    def test_card_is_high_confidence(self):
        profile = from_soil_health_card(
            texture_hint="clay_loam",
            ph=7.8,
            organic_carbon_pct=0.45,
            available_n_kg_ha=210.0,
            available_p_kg_ha=18.0,
            available_k_kg_ha=260.0,
        )
        assert profile.source is SoilSource.SOIL_HEALTH_CARD
        assert profile.confidence == "high"
        assert profile.available_n_kg_ha == 210.0

    def test_organic_carbon_percent_converts_to_g_per_kg(self):
        profile = from_soil_health_card(organic_carbon_pct=0.5)
        assert profile.soc_g_kg == pytest.approx(5.0)

    def test_explicit_fractions_beat_the_texture_hint(self):
        profile = from_soil_health_card(
            texture_hint="sand", sand_pct=20, silt_pct=20, clay_pct=60
        )
        assert profile.texture.name == "clay"


class TestFallback:
    def test_fallback_is_low_confidence_and_names_the_reason(self):
        profile = fallback_profile("SoilGrids has no coverage at this location")
        assert profile.source is SoilSource.FALLBACK
        assert profile.confidence == "low"
        assert "no coverage" in profile.notes[0]

    def test_fallback_still_yields_a_usable_texture(self):
        # The water balance must never be left without a texture.
        assert fallback_profile("x").texture.theta_fc > 0


def test_serialisation_carries_provenance():
    payload = from_feel_test("clay_loam").to_dict()
    assert payload["source"] == "feel_test"
    assert payload["confidence"] == "medium"
    assert payload["texture"] == "clay loam"
