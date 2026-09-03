import pytest

from app.engine.soil_texture import (
    classify_texture,
    runoff_mm,
    total_available_water,
)


class TestTextureTriangle:
    @pytest.mark.parametrize(
        "sand,silt,clay,expected",
        [
            (90, 5, 5, "sand"),
            (80, 12, 8, "loamy sand"),
            (65, 25, 10, "sandy loam"),
            (40, 40, 20, "loam"),
            (20, 60, 20, "silt loam"),
            (5, 88, 7, "silt"),
            (60, 15, 25, "sandy clay loam"),
            (33, 34, 33, "clay loam"),
            (10, 57, 33, "silty clay loam"),
            (50, 5, 45, "sandy clay"),
            (5, 47, 48, "silty clay"),
            (20, 20, 60, "clay"),
        ],
    )
    def test_known_points(self, sand, silt, clay, expected):
        assert classify_texture(sand, silt, clay).name == expected

    def test_fractions_are_normalised(self):
        # SoilGrids returns g/kg, so callers may pass values that do not sum to 100.
        a = classify_texture(400, 400, 200)
        b = classify_texture(40, 40, 20)
        assert a.name == b.name

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            classify_texture(0, 0, 0)


class TestWaterHolding:
    def test_taw_scales_with_root_depth(self):
        loam = classify_texture(40, 40, 20)
        assert total_available_water(loam, 1.0) == pytest.approx(130.0, abs=0.1)
        assert total_available_water(loam, 0.5) == pytest.approx(65.0, abs=0.1)

    def test_sand_holds_less_than_clay_loam(self):
        sand = classify_texture(90, 5, 5)
        clay_loam = classify_texture(33, 34, 33)
        assert total_available_water(sand, 1.0) < total_available_water(clay_loam, 1.0)


class TestRunoff:
    def test_no_rain_no_runoff(self):
        assert runoff_mm(0, "B") == 0.0

    def test_small_rain_fully_absorbed(self):
        # Below the initial abstraction Ia = 0.2S, nothing runs off.
        assert runoff_mm(5.0, "A") == 0.0

    def test_heavy_rain_runs_off_more_on_clay(self):
        assert runoff_mm(100.0, "D") > runoff_mm(100.0, "A")

    def test_runoff_never_exceeds_rainfall(self):
        for group in "ABCD":
            for rain in (1, 10, 50, 100, 300):
                assert 0 <= runoff_mm(rain, group) <= rain
