"""FAO-56 Penman-Monteith reference evapotranspiration.

Pure stdlib on purpose: this is the load-bearing physics of every irrigation
number the app shows a farmer, so it must be independently testable without a
database, a network, or numpy. Equation numbers refer to Allen et al. (1998),
"Crop evapotranspiration", FAO Irrigation and Drainage Paper 56.

Validated against the chapter 4 worked examples:
  Example 17 (Bangkok, monthly)  -> ET0 = 5.72 mm/day
  Example 18 (Brussels, daily)   -> ET0 = 3.88 mm/day
See tests/engine/test_et0.py.
"""

import math
from dataclasses import dataclass

SOLAR_CONSTANT = 0.0820          # MJ m-2 min-1
STEFAN_BOLTZMANN = 4.903e-9      # MJ K-4 m-2 day-1
ALBEDO_GRASS = 0.23


def saturation_vapour_pressure(t_c: float) -> float:
    """e0(T), kPa. Eq. 11."""
    return 0.6108 * math.exp(17.27 * t_c / (t_c + 237.3))


def svp_slope(t_c: float) -> float:
    """Delta, kPa/degC. Eq. 13."""
    return 4098 * saturation_vapour_pressure(t_c) / (t_c + 237.3) ** 2


def atmospheric_pressure(elevation_m: float) -> float:
    """P, kPa. Eq. 7."""
    return 101.3 * ((293 - 0.0065 * elevation_m) / 293) ** 5.26


def psychrometric_constant(pressure_kpa: float) -> float:
    """gamma, kPa/degC. Eq. 8."""
    return 0.665e-3 * pressure_kpa


def wind_speed_at_2m(u_z: float, height_m: float) -> float:
    """Log-law adjustment of wind measured at height_m to 2 m. Eq. 47."""
    if height_m == 2:
        return u_z
    return u_z * 4.87 / math.log(67.8 * height_m - 5.42)


def _solar_declination(doy: int) -> float:
    """delta, rad. Eq. 24."""
    return 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)


def _inverse_relative_distance(doy: int) -> float:
    """dr. Eq. 23."""
    return 1 + 0.033 * math.cos(2 * math.pi * doy / 365)


def _sunset_hour_angle(lat_rad: float, decl: float) -> float:
    """omega_s, rad. Eq. 25, clamped for polar day/night."""
    x = -math.tan(lat_rad) * math.tan(decl)
    return math.acos(max(-1.0, min(1.0, x)))


def extraterrestrial_radiation(lat_deg: float, doy: int) -> float:
    """Ra, MJ m-2 day-1. Eq. 21."""
    lat = math.radians(lat_deg)
    decl = _solar_declination(doy)
    dr = _inverse_relative_distance(doy)
    ws = _sunset_hour_angle(lat, decl)
    return (
        (24 * 60 / math.pi)
        * SOLAR_CONSTANT
        * dr
        * (ws * math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.sin(ws))
    )


def daylight_hours(lat_deg: float, doy: int) -> float:
    """N, hours. Eq. 34."""
    lat = math.radians(lat_deg)
    return 24 / math.pi * _sunset_hour_angle(lat, _solar_declination(doy))


def solar_radiation_from_sunshine(
    sunshine_h: float, daylight_h: float, ra: float, a_s: float = 0.25, b_s: float = 0.50
) -> float:
    """Rs from the Angstrom relation. Eq. 35."""
    return (a_s + b_s * sunshine_h / daylight_h) * ra


def clear_sky_radiation(ra: float, elevation_m: float) -> float:
    """Rso, MJ m-2 day-1. Eq. 37."""
    return (0.75 + 2e-5 * elevation_m) * ra


def net_shortwave(rs: float, albedo: float = ALBEDO_GRASS) -> float:
    """Rns. Eq. 38."""
    return (1 - albedo) * rs


def net_longwave(tmax_c: float, tmin_c: float, ea: float, rs: float, rso: float) -> float:
    """Rnl, MJ m-2 day-1. Eq. 39.

    The Rs/Rso cloudiness term is clamped to 1.0: measured Rs can exceed the
    clear-sky estimate on bright days, which would otherwise drive Rnl negative
    and inflate ET0.
    """
    tmax_k4 = (tmax_c + 273.16) ** 4
    tmin_k4 = (tmin_c + 273.16) ** 4
    cloud = min(rs / rso, 1.0) if rso > 0 else 1.0
    return (
        STEFAN_BOLTZMANN
        * ((tmax_k4 + tmin_k4) / 2)
        * (0.34 - 0.14 * math.sqrt(max(ea, 0.0)))
        * (1.35 * cloud - 0.35)
    )


def actual_vapour_pressure_from_rh(
    tmax_c: float, tmin_c: float, rh_max: float | None, rh_min: float | None
) -> float:
    """ea, kPa. Eq. 17 when both RH extremes are known, else Eq. 19 fallback."""
    es_tmax = saturation_vapour_pressure(tmax_c)
    es_tmin = saturation_vapour_pressure(tmin_c)
    if rh_max is not None and rh_min is not None:
        return (es_tmin * rh_max / 100 + es_tmax * rh_min / 100) / 2
    rh = rh_max if rh_max is not None else rh_min
    if rh is None:
        raise ValueError("need at least one relative humidity value")
    return rh / 100 * (es_tmax + es_tmin) / 2


@dataclass(frozen=True)
class Et0Result:
    et0_mm: float
    ra: float
    rs: float
    rso: float
    rn: float
    delta: float
    gamma: float
    es: float
    ea: float
    vpd: float


def et0_penman_monteith(
    *,
    tmax_c: float,
    tmin_c: float,
    ea_kpa: float,
    u2_ms: float,
    rs_mj: float,
    lat_deg: float,
    elevation_m: float,
    doy: int,
    g_mj: float = 0.0,
) -> Et0Result:
    """Reference ET for a 0.12 m grass surface. Eq. 6.

    g_mj (soil heat flux) is 0 for daily steps; monthly steps pass
    0.14 * (T_month - T_month_minus_1) per Eq. 43.
    """
    tmean = (tmax_c + tmin_c) / 2
    delta = svp_slope(tmean)
    gamma = psychrometric_constant(atmospheric_pressure(elevation_m))

    es = (saturation_vapour_pressure(tmax_c) + saturation_vapour_pressure(tmin_c)) / 2
    vpd = es - ea_kpa

    ra = extraterrestrial_radiation(lat_deg, doy)
    rso = clear_sky_radiation(ra, elevation_m)
    rn = net_shortwave(rs_mj) - net_longwave(tmax_c, tmin_c, ea_kpa, rs_mj, rso)

    numerator = 0.408 * delta * (rn - g_mj) + gamma * (900 / (tmean + 273)) * u2_ms * vpd
    denominator = delta + gamma * (1 + 0.34 * u2_ms)

    return Et0Result(
        et0_mm=numerator / denominator,
        ra=ra,
        rs=rs_mj,
        rso=rso,
        rn=rn,
        delta=delta,
        gamma=gamma,
        es=es,
        ea=ea_kpa,
        vpd=vpd,
    )
