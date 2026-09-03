"""Golden-value tests against the FAO-56 chapter 4 worked examples.

If these drift, every irrigation recommendation in the product is wrong.
Source: Allen et al. (1998), FAO Irrigation and Drainage Paper 56, chapter 4.
https://www.fao.org/4/x0490e/x0490e08.htm
"""

import math

import pytest

from app.engine.et0 import (
    actual_vapour_pressure_from_rh,
    atmospheric_pressure,
    clear_sky_radiation,
    daylight_hours,
    et0_penman_monteith,
    extraterrestrial_radiation,
    net_longwave,
    net_shortwave,
    psychrometric_constant,
    saturation_vapour_pressure,
    solar_radiation_from_sunshine,
    svp_slope,
    wind_speed_at_2m,
)

# 13 deg 44' N -> 13 + 44/60
BANGKOK_LAT = 13 + 44 / 60
# 50 deg 48' N
BRUSSELS_LAT = 50 + 48 / 60


class TestExample17Bangkok:
    """Monthly ET0, Bangkok, April. FAO expects ET0 = 5.72 mm/day."""

    lat = BANGKOK_LAT
    elev = 2.0
    doy = 105  # 15 April
    tmax, tmin = 34.8, 25.6
    ea = 2.85
    u2 = 2.0
    sunshine = 8.5
    # Eq. 43, monthly soil heat flux from the previous month's mean temperature.
    g = 0.14 * (30.2 - 29.2)

    def test_intermediates(self):
        tmean = (self.tmax + self.tmin) / 2
        assert svp_slope(tmean) == pytest.approx(0.246, abs=0.001)
        assert psychrometric_constant(atmospheric_pressure(self.elev)) == pytest.approx(
            0.0674, abs=0.0001
        )

        es = (saturation_vapour_pressure(self.tmax) + saturation_vapour_pressure(self.tmin)) / 2
        assert es == pytest.approx(4.42, abs=0.01)
        assert es - self.ea == pytest.approx(1.57, abs=0.01)

        ra = extraterrestrial_radiation(self.lat, self.doy)
        assert ra == pytest.approx(38.06, abs=0.05)

        n = daylight_hours(self.lat, self.doy)
        assert n == pytest.approx(12.31, abs=0.02)

        rs = solar_radiation_from_sunshine(self.sunshine, n, ra)
        assert rs == pytest.approx(22.65, abs=0.05)

        rso = clear_sky_radiation(ra, self.elev)
        assert rso == pytest.approx(28.54, abs=0.05)

        assert net_shortwave(rs) == pytest.approx(17.44, abs=0.05)
        assert net_longwave(self.tmax, self.tmin, self.ea, rs, rso) == pytest.approx(
            3.11, abs=0.05
        )
        assert self.g == pytest.approx(0.14, abs=0.001)

    def test_et0(self):
        ra = extraterrestrial_radiation(self.lat, self.doy)
        rs = solar_radiation_from_sunshine(self.sunshine, daylight_hours(self.lat, self.doy), ra)
        result = et0_penman_monteith(
            tmax_c=self.tmax,
            tmin_c=self.tmin,
            ea_kpa=self.ea,
            u2_ms=self.u2,
            rs_mj=rs,
            lat_deg=self.lat,
            elevation_m=self.elev,
            doy=self.doy,
            g_mj=self.g,
        )
        assert result.rn == pytest.approx(14.33, abs=0.05)
        assert result.et0_mm == pytest.approx(5.72, abs=0.02)


class TestExample18Brussels:
    """Daily ET0, Brussels, 6 July. FAO expects ET0 = 3.88 mm/day."""

    lat = BRUSSELS_LAT
    elev = 100.0
    doy = 187  # 6 July
    tmax, tmin = 21.5, 12.3
    rh_max, rh_min = 84.0, 63.0
    u10_kmh = 10.0
    sunshine = 9.25

    def test_intermediates(self):
        u2 = wind_speed_at_2m(self.u10_kmh * 1000 / 3600, 10.0)
        assert u2 == pytest.approx(2.078, abs=0.005)

        tmean = (self.tmax + self.tmin) / 2
        assert tmean == pytest.approx(16.9, abs=0.05)
        assert svp_slope(tmean) == pytest.approx(0.122, abs=0.001)
        assert psychrometric_constant(atmospheric_pressure(self.elev)) == pytest.approx(
            0.0666, abs=0.0001
        )

        es = (saturation_vapour_pressure(self.tmax) + saturation_vapour_pressure(self.tmin)) / 2
        assert es == pytest.approx(1.997, abs=0.005)

        ea = actual_vapour_pressure_from_rh(self.tmax, self.tmin, self.rh_max, self.rh_min)
        assert ea == pytest.approx(1.409, abs=0.005)
        assert es - ea == pytest.approx(0.589, abs=0.005)

        ra = extraterrestrial_radiation(self.lat, self.doy)
        assert ra == pytest.approx(41.09, abs=0.05)

        n = daylight_hours(self.lat, self.doy)
        assert n == pytest.approx(16.1, abs=0.05)

        rs = solar_radiation_from_sunshine(self.sunshine, n, ra)
        assert rs == pytest.approx(22.07, abs=0.05)

        rso = clear_sky_radiation(ra, self.elev)
        assert rso == pytest.approx(30.90, abs=0.05)

        assert net_shortwave(rs) == pytest.approx(17.00, abs=0.05)
        assert net_longwave(self.tmax, self.tmin, ea, rs, rso) == pytest.approx(3.71, abs=0.05)

    def test_et0(self):
        ra = extraterrestrial_radiation(self.lat, self.doy)
        rs = solar_radiation_from_sunshine(self.sunshine, daylight_hours(self.lat, self.doy), ra)
        ea = actual_vapour_pressure_from_rh(self.tmax, self.tmin, self.rh_max, self.rh_min)
        result = et0_penman_monteith(
            tmax_c=self.tmax,
            tmin_c=self.tmin,
            ea_kpa=ea,
            u2_ms=wind_speed_at_2m(self.u10_kmh * 1000 / 3600, 10.0),
            rs_mj=rs,
            lat_deg=self.lat,
            elevation_m=self.elev,
            doy=self.doy,
            g_mj=0.0,
        )
        assert result.rn == pytest.approx(13.28, abs=0.05)
        assert result.et0_mm == pytest.approx(3.88, abs=0.02)


class TestEdgeCases:
    def test_polar_night_does_not_explode(self):
        # acos() argument must be clamped or this raises ValueError.
        assert extraterrestrial_radiation(78.0, 355) >= 0.0
        assert daylight_hours(78.0, 355) == pytest.approx(0.0, abs=0.01)

    def test_polar_day(self):
        assert daylight_hours(78.0, 172) == pytest.approx(24.0, abs=0.01)

    def test_cloudiness_ratio_clamped(self):
        # Rs above Rso must not drive Rnl negative.
        rnl = net_longwave(30.0, 20.0, 2.0, rs=35.0, rso=30.0)
        assert rnl > 0

    def test_wind_at_2m_is_identity(self):
        assert wind_speed_at_2m(3.3, 2.0) == 3.3

    def test_svp_matches_table_2_2(self):
        # FAO-56 Table 2.2 spot checks.
        assert saturation_vapour_pressure(20.0) == pytest.approx(2.338, abs=0.002)
        assert saturation_vapour_pressure(30.0) == pytest.approx(4.243, abs=0.002)
        assert not math.isnan(saturation_vapour_pressure(-5.0))
