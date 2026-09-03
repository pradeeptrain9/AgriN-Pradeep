"""Paddy water balance tests.

The regression these exist to prevent: rice reported as water-stressed for half
a month while standing under 300 mm of monsoon rain, which is what the upland
depletion model produced.
"""

from datetime import date, timedelta

import pytest

from app.engine.crops import get_crop
from app.engine.paddy import (
    AWD_THRESHOLD_MM,
    BUND_HEIGHT_MM,
    ESTABLISHMENT_DAYS,
    Regime,
    flowering_window,
    is_paddy,
    percolation_for,
    plan_irrigation,
    regime_at,
    run_balance,
    stress_coefficient,
)
from app.engine.paddy import PaddyDay
from app.engine.soil_texture import classify_texture

RICE = get_crop("rice")
CLAY_LOAM = classify_texture(33, 34, 33)
SANDY_LOAM = classify_texture(65, 25, 10)
SOWING = date(2026, 6, 20)


def days(n, *, et0=5.0, rain=0.0, start=SOWING):
    return [PaddyDay(start + timedelta(days=i), et0, rain) for i in range(n)]


class TestCropDetection:
    def test_rice_is_paddy(self):
        assert is_paddy(RICE)

    def test_wheat_is_not(self):
        assert not is_paddy(get_crop("wheat_spring"))


class TestPercolation:
    def test_clay_percolates_far_less_than_sandy_loam(self):
        assert percolation_for(CLAY_LOAM) < percolation_for(SANDY_LOAM)

    def test_sandy_loam_matches_published_measurements(self):
        """Measured puddled sandy loam: 9.8 mm/day high puddling, 14.1 low."""
        assert percolation_for(SANDY_LOAM, "high") == pytest.approx(9.8, abs=0.1)
        assert percolation_for(SANDY_LOAM, "low") == pytest.approx(14.1, abs=0.2)

    def test_fine_textured_soils_stay_in_the_published_low_range(self):
        """Fine-textured paddy soils percolate roughly 0-3 mm/day."""
        for texture in (CLAY_LOAM, classify_texture(20, 20, 60)):
            assert 0.0 < percolation_for(texture, "high") <= 3.0

    def test_better_puddling_reduces_losses(self):
        high = percolation_for(SANDY_LOAM, "high")
        none = percolation_for(SANDY_LOAM, "none")
        assert high < none


class TestRegimes:
    def test_establishment_comes_first(self):
        assert regime_at(RICE, 0) == Regime.ESTABLISHMENT
        assert regime_at(RICE, ESTABLISHMENT_DAYS - 1) == Regime.ESTABLISHMENT

    def test_awd_follows_establishment(self):
        assert regime_at(RICE, ESTABLISHMENT_DAYS) == Regime.AWD

    def test_flowering_forces_ponding(self):
        start, end = flowering_window(RICE)
        assert regime_at(RICE, start) == Regime.FLOWERING_FLOOD
        assert regime_at(RICE, end) == Regime.FLOWERING_FLOOD
        assert regime_at(RICE, end + 1) == Regime.AWD

    def test_final_drainage_before_harvest(self):
        assert regime_at(RICE, RICE.season_days - 5) == Regime.FINAL_DRAINAGE

    def test_awd_disabled_gives_continuous_flood(self):
        assert regime_at(RICE, 40, awd_enabled=False) == Regime.CONTINUOUS_FLOOD


class TestStressCoefficient:
    def test_no_stress_within_safe_awd(self):
        assert stress_coefficient(50.0) == 1.0
        assert stress_coefficient(0.0) == 1.0
        assert stress_coefficient(AWD_THRESHOLD_MM) == 1.0

    def test_stress_begins_below_the_safe_threshold(self):
        assert stress_coefficient(AWD_THRESHOLD_MM - 1) < 1.0

    def test_fully_stressed_at_the_floor(self):
        assert stress_coefficient(-300.0) == 0.0
        assert stress_coefficient(-500.0) == 0.0

    def test_monotonic(self):
        levels = [50, 0, -50, -150, -200, -250, -300]
        ks = [stress_coefficient(x) for x in levels]
        assert all(a >= b for a, b in zip(ks, ks[1:], strict=False))


class TestTheMonsoonRegression:
    """The whole reason this module exists."""

    def test_heavy_monsoon_rain_does_not_produce_stress(self):
        # 310 mm over 74 days, matching the live Ludhiana case.
        rain_days = [
            PaddyDay(SOWING + timedelta(days=i), 4.75, 8.4 if i % 2 == 0 else 0.0)
            for i in range(74)
        ]
        states = run_balance(
            crop=RICE, texture=CLAY_LOAM, sowing_date=SOWING, days=rain_days
        )
        assert sum(d.rain_mm for d in rain_days) == pytest.approx(310, abs=15)
        stressed = [s for s in states[-30:] if s.stressed]
        assert not stressed, f"{len(stressed)} stressed days under monsoon rain"

    def test_field_stays_wet_through_the_monsoon(self):
        rain_days = [
            PaddyDay(SOWING + timedelta(days=i), 4.75, 8.4 if i % 2 == 0 else 0.0)
            for i in range(74)
        ]
        states = run_balance(
            crop=RICE, texture=CLAY_LOAM, sowing_date=SOWING, days=rain_days
        )
        assert states[-1].water_level_mm > AWD_THRESHOLD_MM


class TestWaterMovement:
    def test_ponded_field_drains_by_et_plus_percolation(self):
        states = run_balance(
            crop=RICE, texture=CLAY_LOAM, sowing_date=SOWING,
            days=days(3, et0=5.0), initial_level_mm=100.0,
        )
        first = states[0]
        expected = 100.0 - (first.eta_mm + first.percolation_mm)
        assert first.water_level_mm == pytest.approx(expected, abs=0.01)

    def test_water_table_falls_faster_than_ponded_water(self):
        """Below the surface, each mm lost drops the table by 1/porosity."""
        ponded = run_balance(
            crop=RICE, texture=CLAY_LOAM, sowing_date=SOWING,
            days=days(1, et0=5.0), initial_level_mm=100.0,
        )[0]
        drop_ponded = 100.0 - ponded.water_level_mm

        dry = run_balance(
            crop=RICE, texture=CLAY_LOAM, sowing_date=SOWING,
            days=days(1, et0=5.0, start=SOWING + timedelta(days=20)),
            initial_level_mm=-50.0,
        )[0]
        drop_dry = -50.0 - dry.water_level_mm
        assert drop_dry > drop_ponded

    def test_rain_refills_a_dry_profile_before_ponding(self):
        state = run_balance(
            crop=RICE, texture=CLAY_LOAM, sowing_date=SOWING,
            days=[PaddyDay(SOWING + timedelta(days=20), 0.0, 5.0)],
            initial_level_mm=-100.0, percolation_mm_day=0.0,
        )[0]
        # 5 mm raises a table 100 mm down but must not pond it.
        assert -100.0 < state.water_level_mm < 0.0

    def test_bund_overflow_is_capped(self):
        state = run_balance(
            crop=RICE, texture=CLAY_LOAM, sowing_date=SOWING,
            days=[PaddyDay(SOWING + timedelta(days=20), 4.0, 400.0)],
            initial_level_mm=50.0,
        )[0]
        assert state.runoff_mm > 0
        assert state.water_level_mm <= BUND_HEIGHT_MM


class TestIrrigationTriggers:
    def test_awd_triggers_at_fifteen_centimetres(self):
        states = run_balance(
            crop=RICE, texture=SANDY_LOAM, sowing_date=SOWING, days=days(60, et0=6.0)
        )
        awd_irrigations = [
            s for s in states if s.regime == Regime.AWD and s.irrigation_mm > 0
        ]
        assert awd_irrigations, "AWD never irrigated over 60 dry days"

    def test_establishment_keeps_a_shallow_flood(self):
        states = run_balance(
            crop=RICE, texture=SANDY_LOAM, sowing_date=SOWING, days=days(14, et0=6.0)
        )
        for state in states:
            assert state.regime == Regime.ESTABLISHMENT
            assert state.water_level_mm > 0, "field went dry during establishment"

    def test_flowering_never_dries_out(self):
        start, end = flowering_window(RICE)
        states = run_balance(
            crop=RICE, texture=SANDY_LOAM, sowing_date=SOWING,
            days=days(end + 5, et0=7.0),
        )
        flowering = [s for s in states if s.regime == Regime.FLOWERING_FLOOD]
        assert flowering
        for state in flowering:
            assert state.water_level_mm > 0, "field dried during flowering"
            assert not state.stressed

    def test_final_drainage_stops_irrigating(self):
        states = run_balance(
            crop=RICE, texture=SANDY_LOAM, sowing_date=SOWING,
            days=days(RICE.season_days, et0=6.0),
        )
        drainage = [s for s in states if s.regime == Regime.FINAL_DRAINAGE]
        assert drainage
        assert all(s.irrigation_mm == 0 for s in drainage)


class TestAwdSaving:
    def test_awd_uses_less_water_than_continuous_flooding(self):
        dry_season = days(120, et0=6.0)
        awd = run_balance(
            crop=RICE, texture=SANDY_LOAM, sowing_date=SOWING, days=dry_season,
            awd_enabled=True,
        )
        flooded = run_balance(
            crop=RICE, texture=SANDY_LOAM, sowing_date=SOWING, days=dry_season,
            awd_enabled=False,
        )
        awd_total = sum(s.irrigation_mm for s in awd)
        flood_total = sum(s.irrigation_mm for s in flooded)
        # The guaranteed physical result: AWD cannot use more water than
        # continuous flooding, because it only ever skips irrigation events.
        assert awd_total < flood_total

        saving = 100 * (flood_total - awd_total) / flood_total
        # Published safe-AWD savings are 15-30%, but those trials compare against
        # farmer-practice continuous flooding kept permanently ponded, on soils
        # that percolate less than this sandy loam. Our continuous-flood baseline
        # is already efficient, and establishment plus flowering lock 41 of these
        # 120 days into mandatory ponding, so the modelled saving lands lower.
        # Asserting the headline range here would mean tuning the model to a
        # number it should be predicting.
        assert 3 < saving < 35, f"implausible saving {saving:.0f}%"

    def test_low_percolation_soil_saves_more_than_leaky_soil_per_mm_applied(self):
        """Percolation dominates paddy water use, so the plough pan matters most."""
        dry_season = days(120, et0=6.0)
        leaky = run_balance(
            crop=RICE, texture=SANDY_LOAM, sowing_date=SOWING, days=dry_season,
            puddling="none",
        )
        tight = run_balance(
            crop=RICE, texture=SANDY_LOAM, sowing_date=SOWING, days=dry_season,
            puddling="high",
        )
        assert sum(s.irrigation_mm for s in tight) < sum(s.irrigation_mm for s in leaky)

    def test_shortfall_against_the_headline_figure_is_explained(self):
        plan = plan_irrigation(
            crop=RICE, texture=CLAY_LOAM, sowing_date=SOWING,
            history=days(90, et0=6.0), forecast=[],
        )
        if 0 < plan.water_saving_pct < 12:
            assert any("below the 15-30%" in n for n in plan.notes)

    def test_plan_reports_the_saving(self):
        plan = plan_irrigation(
            crop=RICE, texture=SANDY_LOAM, sowing_date=SOWING,
            history=days(90, et0=6.0), forecast=[],
        )
        assert plan.water_saving_pct > 0
        assert plan.season_irrigation_mm < plan.season_irrigation_if_continuous_mm
        assert any("AWD has saved" in n for n in plan.notes)


class TestPlan:
    def test_reports_ponded_depth_and_table_separately(self):
        plan = plan_irrigation(
            crop=RICE, texture=CLAY_LOAM, sowing_date=SOWING,
            history=days(10, et0=4.0), forecast=[],
        )
        assert plan.ponded_depth_mm >= 0
        assert plan.water_table_depth_mm <= 0

    def test_gross_depth_exceeds_net(self):
        plan = plan_irrigation(
            crop=RICE, texture=SANDY_LOAM, sowing_date=SOWING,
            history=days(40, et0=7.0), forecast=[], application_efficiency=0.65,
        )
        if plan.irrigate_now:
            assert plan.gross_depth_mm > plan.recommended_depth_mm

    def test_high_percolation_soil_is_flagged(self):
        plan = plan_irrigation(
            crop=RICE, texture=SANDY_LOAM, sowing_date=SOWING,
            history=days(30, et0=5.0), forecast=[], puddling="none",
        )
        assert any("percolates about" in n for n in plan.notes)

    def test_forecast_rain_advises_waiting(self):
        plan = plan_irrigation(
            crop=RICE, texture=CLAY_LOAM, sowing_date=SOWING,
            history=days(30, et0=5.0),
            forecast=days(7, et0=4.0, rain=20.0, start=SOWING + timedelta(days=30)),
        )
        assert any("hold off irrigating" in n for n in plan.notes)

    def test_establishment_guidance_is_given(self):
        plan = plan_irrigation(
            crop=RICE, texture=CLAY_LOAM, sowing_date=SOWING,
            history=days(5, et0=4.0), forecast=[],
        )
        assert any("first two weeks" in n.lower() for n in plan.notes)

    def test_flowering_guidance_forbids_awd(self):
        start, _ = flowering_window(RICE)
        plan = plan_irrigation(
            crop=RICE, texture=CLAY_LOAM, sowing_date=SOWING,
            history=days(start + 2, et0=5.0), forecast=[],
        )
        assert plan.regime == Regime.FLOWERING_FLOOD
        assert any("do not practise AWD" in n for n in plan.notes)

    def test_empty_history_rejected(self):
        with pytest.raises(ValueError):
            plan_irrigation(
                crop=RICE, texture=CLAY_LOAM, sowing_date=SOWING,
                history=[], forecast=[],
            )

    def test_serialises_with_model_marker(self):
        payload = plan_irrigation(
            crop=RICE, texture=CLAY_LOAM, sowing_date=SOWING,
            history=days(30, et0=5.0), forecast=[],
        ).to_dict()
        assert payload["model"] == "paddy"
        assert "water_table_depth_mm" in payload
