import pytest

from app.engine.crops import get_crop
from app.engine.nutrients import (
    DAP_N,
    DAP_P2O5,
    MOP_K2O,
    UREA_N,
    classify,
    recommend,
)
from app.providers.soil import from_feel_test, from_soil_health_card

WHEAT = get_crop("wheat_spring")
SOYBEAN = get_crop("soybean")
MAIZE = get_crop("maize")


def card(**kw):
    return from_soil_health_card(**kw).to_dict()


class TestFertilityClassification:
    @pytest.mark.parametrize(
        "value,expected", [(200, "low"), (400, "medium"), (700, "high"), (None, "unknown")]
    )
    def test_nitrogen_classes(self, value, expected):
        from app.engine.nutrients import N_CLASS_KG_HA

        assert classify(value, N_CLASS_KG_HA) == expected

    def test_boundaries_are_inclusive_of_medium(self):
        from app.engine.nutrients import N_CLASS_KG_HA

        assert classify(280.0, N_CLASS_KG_HA) == "medium"
        assert classify(560.0, N_CLASS_KG_HA) == "medium"
        assert classify(560.1, N_CLASS_KG_HA) == "high"


class TestFertilityResponse:
    def test_low_fertility_gets_more_than_high(self):
        low = recommend(
            crop=WHEAT,
            soil=card(available_n_kg_ha=200, available_p_kg_ha=8, available_k_kg_ha=90),
        )
        high = recommend(
            crop=WHEAT,
            soil=card(available_n_kg_ha=700, available_p_kg_ha=30, available_k_kg_ha=320),
        )
        assert low.n.mid_kg_ha > high.n.mid_kg_ha
        assert low.p2o5.mid_kg_ha > high.p2o5.mid_kg_ha
        assert low.k2o.mid_kg_ha > high.k2o.mid_kg_ha

    def test_doses_scale_with_target_yield(self):
        small = recommend(crop=WHEAT, soil=card(), target_yield_t_ha=2.0)
        big = recommend(crop=WHEAT, soil=card(), target_yield_t_ha=6.0)
        assert big.p2o5.mid_kg_ha > small.p2o5.mid_kg_ha

    def test_output_is_always_a_range(self):
        plan = recommend(crop=WHEAT, soil=card())
        assert plan.n.low_kg_ha < plan.n.high_kg_ha
        assert plan.p2o5.low_kg_ha < plan.p2o5.high_kg_ha

    def test_dose_never_negative(self):
        plan = recommend(
            crop=WHEAT,
            soil=card(available_n_kg_ha=900, organic_carbon_pct=2.0),
            previous_crop=SOYBEAN,
        )
        assert plan.n.low_kg_ha >= 0


class TestCredits:
    def test_organic_carbon_credits_nitrogen(self):
        without = recommend(crop=WHEAT, soil=card(available_n_kg_ha=400))
        with_oc = recommend(
            crop=WHEAT, soil=card(available_n_kg_ha=400, organic_carbon_pct=1.0)
        )
        assert with_oc.n_credits_kg_ha > without.n_credits_kg_ha
        assert with_oc.n.mid_kg_ha < without.n.mid_kg_ha

    def test_organic_carbon_credit_is_capped(self):
        plan = recommend(
            crop=WHEAT, soil=card(available_n_kg_ha=400, organic_carbon_pct=10.0)
        )
        assert plan.credit_breakdown["organic_carbon"] <= 40.0

    def test_previous_legume_credits_nitrogen(self):
        plan = recommend(crop=WHEAT, soil=card(available_n_kg_ha=400), previous_crop=SOYBEAN)
        assert plan.credit_breakdown["previous_legume"] == pytest.approx(27.0)

    def test_retained_residue_credits_nitrogen(self):
        plan = recommend(
            crop=WHEAT, soil=card(available_n_kg_ha=400), residue_retained=True
        )
        assert plan.credit_breakdown["retained_residue"] == 10.0


class TestLegumeHandling:
    def test_legume_gets_only_a_starter_dose(self):
        plan = recommend(crop=SOYBEAN, soil=card(available_n_kg_ha=200))
        assert plan.n.high_kg_ha <= 25.0
        assert any("fixes its own nitrogen" in n for n in plan.notes)

    def test_legume_still_gets_phosphorus(self):
        plan = recommend(crop=SOYBEAN, soil=card(available_p_kg_ha=8))
        assert plan.p2o5.mid_kg_ha > 0


class TestProductConversion:
    def test_dap_nitrogen_is_subtracted_from_urea(self):
        plan = recommend(crop=MAIZE, soil=card(available_p_kg_ha=8, available_n_kg_ha=300))
        dap = plan.products["dap"]
        urea = plan.products["urea"]
        n_from_dap = dap * DAP_N
        assert plan.products["n_supplied_by_dap"] == pytest.approx(n_from_dap)
        # Total N delivered by both products matches the recommended dose.
        assert urea * UREA_N + n_from_dap == pytest.approx(plan.n.mid_kg_ha, abs=0.5)

    def test_dap_meets_the_phosphorus_dose(self):
        plan = recommend(crop=MAIZE, soil=card(available_p_kg_ha=8))
        assert plan.products["dap"] * DAP_P2O5 == pytest.approx(plan.p2o5.mid_kg_ha, abs=0.5)

    def test_mop_meets_the_potassium_dose(self):
        plan = recommend(crop=MAIZE, soil=card(available_k_kg_ha=90))
        assert plan.products["mop"] * MOP_K2O == pytest.approx(plan.k2o.mid_kg_ha, abs=0.5)


class TestSplits:
    def test_nitrogen_is_split_not_dumped_basal(self):
        plan = recommend(crop=MAIZE, soil=card(available_n_kg_ha=300))
        splits = plan.splits
        assert len(splits) == 3
        basal_n = splits[0]["n_kg_ha"]
        assert basal_n < plan.n.mid_kg_ha
        assert sum(s["n_kg_ha"] for s in splits) == pytest.approx(plan.n.mid_kg_ha, abs=0.5)

    def test_phosphorus_and_potassium_go_basal_only(self):
        plan = recommend(crop=MAIZE, soil=card(available_p_kg_ha=8, available_k_kg_ha=90))
        assert plan.splits[0]["p2o5_kg_ha"] == pytest.approx(plan.p2o5.mid_kg_ha, abs=0.1)
        assert all(s["p2o5_kg_ha"] == 0 for s in plan.splits[1:])

    def test_split_timing_follows_crop_stages(self):
        plan = recommend(crop=MAIZE, soil=card())
        days = [s["days_after_sowing"] for s in plan.splits]
        assert days == sorted(days)
        assert days[-1] < MAIZE.season_days


class TestProvenanceAndRegenerative:
    def test_low_confidence_soil_is_disclosed(self):
        plan = recommend(crop=WHEAT, soil=from_feel_test("clay_loam").to_dict())
        assert plan.soil_confidence == "medium"
        assert any("estimated, not measured" in n for n in plan.notes)

    def test_removal_coefficients_are_disclosed_as_indicative(self):
        plan = recommend(crop=WHEAT, soil=card())
        assert any("indicative" in n for n in plan.notes)

    def test_low_organic_carbon_triggers_manure_advice(self):
        plan = recommend(crop=WHEAT, soil=card(organic_carbon_pct=0.3))
        assert any("farmyard manure" in a for a in plan.regenerative_actions)

    def test_residue_burning_is_discouraged(self):
        plan = recommend(crop=WHEAT, soil=card(), residue_retained=False)
        assert any("burning" in a for a in plan.regenerative_actions)

    def test_alkaline_soil_changes_phosphorus_placement(self):
        plan = recommend(crop=WHEAT, soil=card(ph=8.6))
        assert any("bands near the seed" in a for a in plan.regenerative_actions)

    def test_acid_soil_triggers_lime_advice(self):
        plan = recommend(crop=WHEAT, soil=card(ph=5.0))
        assert any("lime" in a for a in plan.regenerative_actions)

    def test_nitrogen_class_falls_back_to_organic_carbon(self):
        plan = recommend(crop=WHEAT, soil=card(organic_carbon_pct=0.3))
        assert plan.n_class == "low"
        assert any("inferred from organic carbon" in n for n in plan.notes)

    def test_serialises(self):
        payload = recommend(crop=WHEAT, soil=card(available_n_kg_ha=300)).to_dict()
        assert payload["fertility_class"]["n"] == "medium"
        assert "products_kg_ha" in payload
