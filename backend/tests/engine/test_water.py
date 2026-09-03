from datetime import date, timedelta

import pytest

from app.engine.crops import Stage, get_crop
from app.engine.soil_texture import classify_texture
from app.engine.water import DayInput, plan_irrigation, run_balance

LOAM = classify_texture(40, 40, 20)
SOWING = date(2026, 6, 1)


def series(n: int, *, et0: float, rain: float = 0.0, start: date = SOWING) -> list[DayInput]:
    return [
        DayInput(day=start + timedelta(days=i), et0_mm=et0, rain_mm=rain) for i in range(n)
    ]


class TestConservation:
    def test_depletion_change_matches_fluxes(self):
        """Dr_i - Dr_i-1 == ETa - infiltration + percolation, every single day."""
        crop = get_crop("maize")
        days = [
            DayInput(SOWING + timedelta(days=i), et0_mm=5.0, rain_mm=(20.0 if i % 7 == 0 else 0))
            for i in range(60)
        ]
        states = run_balance(crop=crop, texture=LOAM, sowing_date=SOWING, days=days)

        prev = None
        for state, entry in zip(states, days, strict=True):
            if prev is not None:
                infiltration = entry.rain_mm - state.runoff_mm + entry.irrigation_mm
                expected = prev + state.eta_mm - infiltration + state.percolation_mm
                # Only meaningful while the balance is not clipped at the TAW ceiling.
                if 0 < state.depletion_mm < state.taw_mm:
                    assert state.depletion_mm == pytest.approx(expected, abs=1e-6)
            prev = state.depletion_mm

    def test_depletion_stays_within_zero_and_taw(self):
        crop = get_crop("maize")
        days = [
            DayInput(SOWING + timedelta(days=i), et0_mm=9.0, rain_mm=(80.0 if i == 40 else 0))
            for i in range(120)
        ]
        for state in run_balance(crop=crop, texture=LOAM, sowing_date=SOWING, days=days):
            assert 0.0 <= state.depletion_mm <= state.taw_mm + 1e-9


class TestDryDown:
    def test_no_rain_dries_the_profile(self):
        crop = get_crop("maize")
        states = run_balance(
            crop=crop, texture=LOAM, sowing_date=SOWING, days=series(40, et0=6.0)
        )
        assert states[-1].depletion_mm > states[0].depletion_mm
        assert states[-1].stressed

    def test_stress_coefficient_bounds(self):
        crop = get_crop("maize")
        for state in run_balance(
            crop=crop, texture=LOAM, sowing_date=SOWING, days=series(90, et0=8.0)
        ):
            assert 0.0 <= state.ks <= 1.0
            assert state.eta_mm <= state.etc_mm + 1e-9

    def test_ks_is_one_while_above_raw(self):
        crop = get_crop("maize")
        states = run_balance(
            crop=crop, texture=LOAM, sowing_date=SOWING, days=series(30, et0=2.0)
        )
        for state in states:
            if state.depletion_mm <= state.raw_mm:
                assert state.ks == 1.0


class TestWetting:
    def test_heavy_rain_refills_and_percolates(self):
        crop = get_crop("maize")
        days = series(20, et0=4.0) + [
            DayInput(SOWING + timedelta(days=20), et0_mm=4.0, rain_mm=250.0)
        ]
        states = run_balance(crop=crop, texture=LOAM, sowing_date=SOWING, days=days)
        assert states[-1].depletion_mm == pytest.approx(0.0, abs=1e-9)
        assert states[-1].percolation_mm > 0
        assert states[-1].runoff_mm > 0


class TestCropSensitivity:
    def test_rice_tolerates_less_depletion_than_cotton(self):
        """Rice p=0.20 vs cotton p=0.65: rice must hit its RAW threshold first."""
        days = series(45, et0=6.0)
        rice = run_balance(
            crop=get_crop("rice"), texture=LOAM, sowing_date=SOWING, days=days
        )
        cotton = run_balance(
            crop=get_crop("cotton"), texture=LOAM, sowing_date=SOWING, days=days
        )

        def first_trigger(states):
            return next(
                (i for i, s in enumerate(states) if s.depletion_mm >= s.raw_mm), None
            )

        assert first_trigger(rice) is not None
        assert first_trigger(rice) < first_trigger(cotton)

    def test_stage_progresses_over_the_season(self):
        crop = get_crop("wheat_spring")
        states = run_balance(
            crop=crop, texture=LOAM, sowing_date=SOWING, days=series(140, et0=4.0)
        )
        seen = [s.stage for s in states]
        assert seen[0] is Stage.INITIAL
        assert Stage.MID in seen
        assert seen[-1] is Stage.DONE


class TestPlanIrrigation:
    def test_dry_field_triggers_irrigation_with_gross_depth(self):
        plan = plan_irrigation(
            crop=get_crop("maize"),
            texture=LOAM,
            sowing_date=SOWING,
            history=series(50, et0=7.0),
            forecast=[],
            application_efficiency=0.65,
        )
        assert plan.irrigate_now
        assert plan.recommended_depth_mm > 0
        # Gross accounts for conveyance and application losses.
        assert plan.gross_depth_mm > plan.recommended_depth_mm
        assert plan.gross_depth_mm == pytest.approx(
            plan.recommended_depth_mm / 0.65, abs=0.2
        )

    def test_wet_field_projects_a_future_date(self):
        history = series(15, et0=3.0, rain=6.0)
        forecast = series(10, et0=8.0, start=SOWING + timedelta(days=15))
        plan = plan_irrigation(
            crop=get_crop("maize"),
            texture=LOAM,
            sowing_date=SOWING,
            history=history,
            forecast=forecast,
        )
        if not plan.irrigate_now and plan.days_until_irrigation is not None:
            assert plan.forecast_irrigation_date is not None
            assert plan.days_until_irrigation >= 1

    def test_forecast_rain_suppresses_the_recommendation(self):
        forecast = series(7, et0=4.0, rain=40.0, start=SOWING + timedelta(days=50))
        plan = plan_irrigation(
            crop=get_crop("maize"),
            texture=LOAM,
            sowing_date=SOWING,
            history=series(50, et0=7.0),
            forecast=forecast,
        )
        assert plan.rainfall_next_7d_mm == pytest.approx(280.0)
        assert any("rain is forecast" in n for n in plan.notes)

    def test_soil_moisture_percentage_is_sane(self):
        plan = plan_irrigation(
            crop=get_crop("maize"),
            texture=LOAM,
            sowing_date=SOWING,
            history=series(30, et0=5.0),
            forecast=[],
        )
        assert 0.0 <= plan.soil_moisture_pct <= 100.0

    def test_empty_history_rejected(self):
        with pytest.raises(ValueError):
            plan_irrigation(
                crop=get_crop("maize"),
                texture=LOAM,
                sowing_date=SOWING,
                history=[],
                forecast=[],
            )
