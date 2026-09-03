import pytest

from app.engine.crops import get_crop
from app.engine.rotation import (
    FAMILY,
    recommend_rotation,
    score_candidate,
    seasonal_water_need_mm,
)


class TestWaterNeed:
    def test_longer_season_needs_more_water(self):
        cane = seasonal_water_need_mm(get_crop("sugarcane"), 5.0)
        millet = seasonal_water_need_mm(get_crop("pearl_millet"), 5.0)
        assert cane > millet

    def test_scales_with_evaporative_demand(self):
        low = seasonal_water_need_mm(get_crop("maize"), 3.0)
        high = seasonal_water_need_mm(get_crop("maize"), 6.0)
        assert high == pytest.approx(2 * low, rel=0.01)

    def test_maize_seasonal_demand_is_agronomically_plausible(self):
        # FAO puts maize seasonal water use around 500-800 mm.
        need = seasonal_water_need_mm(get_crop("maize"), 5.0)
        assert 450 < need < 900


class TestRotationBreak:
    def test_same_crop_scores_zero_and_warns(self):
        result = score_candidate(
            candidate=get_crop("wheat_spring"),
            previous=get_crop("wheat_spring"),
            expected_rainfall_mm=400,
            mean_et0_mm_day=4.0,
            irrigation_available=True,
        )
        assert result.components["rotation_break"] == 0.0
        assert any("Same crop as last season" in w for w in result.warnings)

    def test_same_family_is_penalised(self):
        result = score_candidate(
            candidate=get_crop("maize"),
            previous=get_crop("wheat_spring"),
            expected_rainfall_mm=400,
            mean_et0_mm_day=4.0,
            irrigation_available=True,
        )
        assert FAMILY["maize"] == FAMILY["wheat_spring"] == "poaceae"
        assert result.components["rotation_break"] == pytest.approx(0.35)

    def test_different_family_scores_full(self):
        result = score_candidate(
            candidate=get_crop("chickpea"),
            previous=get_crop("wheat_spring"),
            expected_rainfall_mm=400,
            mean_et0_mm_day=4.0,
            irrigation_available=True,
        )
        assert result.components["rotation_break"] == 1.0
        assert any("breaks pest cycles" in r for r in result.reasons)

    def test_every_crop_has_a_family(self):
        from app.engine.crops import CROPS

        assert set(FAMILY) == set(CROPS)


class TestWaterFit:
    def test_rainfed_shortfall_warns(self):
        result = score_candidate(
            candidate=get_crop("sugarcane"),
            previous=get_crop("wheat_spring"),
            expected_rainfall_mm=200,
            mean_et0_mm_day=5.0,
            irrigation_available=False,
        )
        assert result.components["water_fit"] < 0.5
        assert any("no irrigation" in w for w in result.warnings)

    def test_irrigation_removes_most_of_the_penalty(self):
        kwargs = dict(
            candidate=get_crop("sugarcane"),
            previous=get_crop("wheat_spring"),
            expected_rainfall_mm=200,
            mean_et0_mm_day=5.0,
        )
        dry = score_candidate(**kwargs, irrigation_available=False)
        wet = score_candidate(**kwargs, irrigation_available=True)
        assert wet.components["water_fit"] > dry.components["water_fit"]

    def test_ample_rainfall_is_credited(self):
        result = score_candidate(
            candidate=get_crop("pearl_millet"),
            previous=get_crop("cotton"),
            expected_rainfall_mm=900,
            mean_et0_mm_day=4.0,
            irrigation_available=False,
        )
        assert result.components["water_fit"] == 1.0
        assert any("rainfall alone covers" in r for r in result.reasons)


class TestRecommendation:
    def test_legume_ranks_high_after_a_cereal_in_dry_conditions(self):
        results = recommend_rotation(
            previous_crop_code="wheat_spring",
            expected_rainfall_mm=350,
            mean_et0_mm_day=5.0,
            irrigation_available=False,
        )
        top_codes = [r.crop_code for r in results]
        assert any(get_crop(c).n_fixation_kg_ha > 0 for c in top_codes)

    def test_previous_crop_is_not_top_ranked(self):
        results = recommend_rotation(
            previous_crop_code="maize",
            expected_rainfall_mm=600,
            mean_et0_mm_day=4.5,
            irrigation_available=True,
        )
        assert results[0].crop_code != "maize"

    def test_results_are_sorted_and_capped(self):
        results = recommend_rotation(
            previous_crop_code="rice",
            expected_rainfall_mm=500,
            mean_et0_mm_day=4.0,
            top_n=3,
        )
        assert len(results) == 3
        assert [r.score for r in results] == sorted(
            (r.score for r in results), reverse=True
        )

    def test_every_result_explains_itself(self):
        results = recommend_rotation(
            previous_crop_code="cotton", expected_rainfall_mm=500, mean_et0_mm_day=4.0
        )
        for result in results:
            assert result.reasons or result.warnings
            assert set(result.components) == {
                "nitrogen", "water_fit", "soil_carbon", "rotation_break", "market",
            }

    def test_market_signal_shifts_ranking(self):
        base = recommend_rotation(
            previous_crop_code="wheat_spring",
            expected_rainfall_mm=600,
            mean_et0_mm_day=4.0,
            irrigation_available=True,
            candidates=["chickpea", "potato"],
            top_n=2,
        )
        boosted = recommend_rotation(
            previous_crop_code="wheat_spring",
            expected_rainfall_mm=600,
            mean_et0_mm_day=4.0,
            irrigation_available=True,
            candidates=["chickpea", "potato"],
            market_index={"potato": 1.0},
            top_n=2,
        )
        potato_base = next(r.score for r in base if r.crop_code == "potato")
        potato_boost = next(r.score for r in boosted if r.crop_code == "potato")
        assert potato_boost > potato_base

    def test_no_previous_crop_is_handled(self):
        results = recommend_rotation(
            previous_crop_code=None, expected_rainfall_mm=500, mean_et0_mm_day=4.0
        )
        assert results
        assert any("No previous crop" in reason for r in results for reason in r.reasons)

    def test_serialises(self):
        result = recommend_rotation(
            previous_crop_code="rice", expected_rainfall_mm=500, mean_et0_mm_day=4.0
        )[0].to_dict()
        assert "components" in result and "score" in result
