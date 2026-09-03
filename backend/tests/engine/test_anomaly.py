from datetime import date, timedelta

import pytest

from app.engine.anomaly import (
    ALERT_RESIDUAL,
    IndexPoint,
    assess,
    expected_ndvi,
)
from app.engine.crops import Stage, get_crop

MAIZE = get_crop("maize")          # 30/40/50/30
SOWING = date(2026, 6, 1)


def points(pairs, valid=1.0):
    return [IndexPoint(SOWING + timedelta(days=d), v, valid) for d, v in pairs]


class TestExpectedCurve:
    def test_starts_near_bare_soil(self):
        assert expected_ndvi(MAIZE, 0) == pytest.approx(0.15, abs=0.01)

    def test_peaks_during_mid_season(self):
        assert expected_ndvi(MAIZE, 90) == pytest.approx(MAIZE.ndvi_peak)

    def test_declines_through_senescence(self):
        assert expected_ndvi(MAIZE, 145) < expected_ndvi(MAIZE, 110)

    def test_monotonic_rise_to_peak(self):
        values = [expected_ndvi(MAIZE, d) for d in range(0, 121)]
        assert all(b >= a - 1e-9 for a, b in zip(values, values[1:], strict=False))

    def test_never_exceeds_peak(self):
        assert max(expected_ndvi(MAIZE, d) for d in range(0, 200)) <= MAIZE.ndvi_peak + 1e-9

    def test_rice_curve_is_not_driven_by_its_high_kc_ini(self):
        """Flooded rice has Kc_ini 1.05 from open water; NDVI must still start bare."""
        rice = get_crop("rice")
        assert expected_ndvi(rice, 0) == pytest.approx(0.15, abs=0.01)
        assert rice.kc_at(0) > 1.0


class TestSeverity:
    def test_healthy_crop_is_ok(self):
        # Mid-season maize should sit at its peak of 0.88, not merely be rising.
        obs = points([(60, 0.72), (65, 0.80), (70, 0.86), (75, 0.88)])
        health = assess(crop=MAIZE, sowing_date=SOWING, ndvi=obs, today=SOWING + timedelta(days=77))
        assert health.severity == "ok"
        assert health.residual > ALERT_RESIDUAL

    def test_far_below_curve_alerts(self):
        obs = points([(60, 0.30), (65, 0.29), (70, 0.28), (75, 0.27)])
        health = assess(crop=MAIZE, sowing_date=SOWING, ndvi=obs, today=SOWING + timedelta(days=77))
        assert health.severity == "alert"
        assert health.residual <= ALERT_RESIDUAL

    def test_slightly_below_curve_is_watch(self):
        target = expected_ndvi(MAIZE, 75) - 0.09
        obs = points([(65, target), (70, target), (75, target)])
        health = assess(crop=MAIZE, sowing_date=SOWING, ndvi=obs, today=SOWING + timedelta(days=76))
        assert health.severity == "watch"


class TestCloudHonesty:
    def test_no_observations_is_unknown_not_zero(self):
        health = assess(crop=MAIZE, sowing_date=SOWING, ndvi=[], today=SOWING + timedelta(days=60))
        assert health.severity == "unknown"
        assert health.latest_ndvi is None
        assert health.observations_used == 0
        assert health.is_stale

    def test_stale_series_is_flagged_and_not_reported_as_ok(self):
        """A month-old image must never present as a healthy field."""
        obs = points([(40, 0.55), (45, 0.60)])
        health = assess(crop=MAIZE, sowing_date=SOWING, ndvi=obs, today=SOWING + timedelta(days=90))
        assert health.is_stale
        assert health.days_since_observation == 45
        assert health.severity != "ok"
        assert any("days old" in n for n in health.notes)

    def test_partial_cloud_cover_is_disclosed(self):
        obs = points([(70, 0.75), (75, 0.78)], valid=0.65)
        health = assess(crop=MAIZE, sowing_date=SOWING, ndvi=obs, today=SOWING + timedelta(days=76))
        assert any("cloud-free" in n for n in health.notes)

    def test_alert_still_raised_when_stale(self):
        obs = points([(40, 0.20), (45, 0.19)])
        health = assess(crop=MAIZE, sowing_date=SOWING, ndvi=obs, today=SOWING + timedelta(days=90))
        assert health.severity == "alert"


class TestSmoothing:
    def test_single_point_has_no_trend(self):
        health = assess(
            crop=MAIZE, sowing_date=SOWING, ndvi=points([(70, 0.70)]),
            today=SOWING + timedelta(days=71),
        )
        assert health.trend_per_day is None
        assert health.smoothed_ndvi == pytest.approx(0.70)

    def test_smoothed_value_stays_within_observed_range(self):
        obs = points([(50, 0.40), (56, 0.65), (62, 0.55), (68, 0.72), (74, 0.68)])
        health = assess(crop=MAIZE, sowing_date=SOWING, ndvi=obs, today=SOWING + timedelta(days=75))
        values = [p.value for p in obs]
        assert min(values) <= health.smoothed_ndvi <= max(values)

    def test_rising_series_has_positive_trend(self):
        obs = points([(50, 0.35), (56, 0.45), (62, 0.55), (68, 0.65)])
        health = assess(crop=MAIZE, sowing_date=SOWING, ndvi=obs, today=SOWING + timedelta(days=69))
        assert health.trend_per_day > 0

    def test_falling_canopy_during_growth_is_noted(self):
        obs = points([(50, 0.70), (56, 0.60), (62, 0.50), (68, 0.40)])
        health = assess(crop=MAIZE, sowing_date=SOWING, ndvi=obs, today=SOWING + timedelta(days=69))
        assert health.trend_per_day < 0
        assert any("falling" in n for n in health.notes)


class TestWaterStress:
    def test_low_ndmi_flags_stress(self):
        ndvi = points([(70, 0.70), (75, 0.72)])
        ndmi = points([(70, 0.05), (75, 0.04)])
        health = assess(
            crop=MAIZE, sowing_date=SOWING, ndvi=ndvi, ndmi=ndmi,
            today=SOWING + timedelta(days=76),
        )
        assert health.water_stress_flag
        assert any("water stress" in n for n in health.notes)

    def test_healthy_ndmi_does_not_flag(self):
        ndvi = points([(70, 0.70)])
        ndmi = points([(70, 0.35)])
        health = assess(
            crop=MAIZE, sowing_date=SOWING, ndvi=ndvi, ndmi=ndmi,
            today=SOWING + timedelta(days=71),
        )
        assert not health.water_stress_flag


def test_post_season_pauses_scoring():
    obs = points([(160, 0.20)])
    health = assess(crop=MAIZE, sowing_date=SOWING, ndvi=obs, today=SOWING + timedelta(days=161))
    assert health.stage is Stage.DONE
    assert any("past its expected season" in n for n in health.notes)


def test_serialises_to_plain_json_types():
    obs = points([(70, 0.70), (75, 0.72)])
    payload = assess(
        crop=MAIZE, sowing_date=SOWING, ndvi=obs, today=SOWING + timedelta(days=76)
    ).to_dict()
    assert payload["stage"] == "mid"
    assert isinstance(payload["as_of"], str)
