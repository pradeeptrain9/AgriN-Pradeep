"""NASA POWER archive adapter: the fallback when Open-Meteo will not answer.

Why a second archive source exists at all. Open-Meteo is free, keyless and
excellent, and it rate-limits per IP. On shared hosting the outbound IP belongs
to the platform and is shared with every other tenant on it, so a node can be
refused having made no requests of its own -- which is exactly what happened to
the first deployed AgriN node, on its first field. A digital public good that
other countries are meant to deploy on whatever hosting they can afford cannot
have a single point of failure sitting in someone else's rate limiter.

POWER suits this better than most alternatives:

  * Free, no API key, no registration, and run by NASA Langley rather than a
    company that may change its terms.
  * The `AG` community is built for exactly this: it publishes T2M_MAX,
    T2M_MIN, PRECTOTCORR, ALLSKY_SFC_SW_DWN, RH2M and WS2M, which between them
    are every input FAO-56 Penman-Monteith needs -- including real solar
    radiation, so ET0 here is computed the same way as from Open-Meteo rather
    than estimated.
  * WS2M is already wind at 2 m, so no logarithmic height correction is
    applied and none of its error is inherited.

What it is worse at: latency. POWER's daily product lags real time by a few
days, sometimes more for radiation. That is fine for the 200-day history this
node keeps to characterise a field's climate, and useless for a forecast --
which is why this module implements the archive only, and met_no.py covers the
other half.

Missing values come back as -999, not null, and feeding one of those into a
water balance would put a field several hundred millimetres into deficit. They
are dropped, not defaulted.
"""

from __future__ import annotations

from datetime import date, datetime

import httpx

from app.engine.et0 import (
    actual_vapour_pressure_from_rh,
    et0_penman_monteith,
    saturation_vapour_pressure,
)
from app.providers.weather import DailyWeather, WeatherUnavailable

URL = "https://power.larc.nasa.gov/api/temporal/daily/point"

SOURCE = "nasa-power"

# POWER's sentinel for "no value". Not null, and not obviously wrong to
# arithmetic -- which is what makes it dangerous.
FILL_VALUE = -999.0

PARAMETERS = (
    "T2M_MAX",            # °C
    "T2M_MIN",            # °C
    "T2M",                # °C, daily mean
    "PRECTOTCORR",        # mm/day, bias-corrected precipitation
    "ALLSKY_SFC_SW_DWN",  # MJ m-2 day-1 under the AG community
    "RH2M",               # %
    "WS2M",               # m/s at 2 m
)


def _clean(value: float | None) -> float | None:
    if value is None:
        return None
    # Compared with a tolerance rather than equality: the API has been seen to
    # return -999.0 and -999.00 in different fields of the same response.
    return None if abs(value - FILL_VALUE) < 0.01 else float(value)


def _build(payload: dict, lat: float) -> list[DailyWeather]:
    properties = payload.get("properties", {}).get("parameter", {})
    if not properties:
        raise WeatherUnavailable("NASA POWER returned no parameters")

    elevation = 0.0
    geometry = payload.get("geometry", {}).get("coordinates", [])
    if len(geometry) >= 3 and isinstance(geometry[2], (int, float)):
        elevation = float(geometry[2])

    days = sorted(properties.get("T2M_MAX", {}).keys())
    out: list[DailyWeather] = []

    for stamp in days:
        def value(name: str, key: str = stamp) -> float | None:
            return _clean(properties.get(name, {}).get(key))

        tmax, tmin = value("T2M_MAX"), value("T2M_MIN")
        rad = value("ALLSKY_SFC_SW_DWN")
        if tmax is None or tmin is None or rad is None:
            # A day without temperature or radiation cannot produce an ET0 the
            # water balance could use. Skipping leaves a gap, which the
            # advisory already reports; inventing one would not be visible.
            continue

        day = datetime.strptime(stamp, "%Y%m%d").date()
        tmean = value("T2M")
        tmean = tmean if tmean is not None else (tmax + tmin) / 2

        rh = value("RH2M")
        if rh is not None:
            # POWER publishes a daily mean, not the two extremes, so this takes
            # the Eq. 19 branch: ea = RHmean/100 * (e0(Tmax) + e0(Tmin))/2.
            ea = actual_vapour_pressure_from_rh(tmax, tmin, rh, None)
        else:
            # FAO-56 arid-climate approximation, the same fallback the
            # Open-Meteo adapter uses when dewpoint is missing.
            ea = saturation_vapour_pressure(tmin)

        # WS2M is already at 2 m, which is the height FAO-56 wants.
        u2 = value("WS2M")

        result = et0_penman_monteith(
            tmax_c=tmax,
            tmin_c=tmin,
            ea_kpa=ea,
            u2_ms=u2 if u2 is not None else 2.0,
            rs_mj=rad,
            lat_deg=lat,
            elevation_m=elevation,
            doy=day.timetuple().tm_yday,
        )

        out.append(
            DailyWeather(
                day=day,
                tmax_c=tmax,
                tmin_c=tmin,
                tmean_c=tmean,
                precip_mm=value("PRECTOTCORR") or 0.0,
                rad_mj=rad,
                rh_mean=rh,
                wind2_ms=u2,
                soil_moist=None,   # POWER's soil moisture is a different product
                et0_mm=max(0.0, result.et0_mm),
                et0_provider_mm=None,
                source=SOURCE,
            )
        )
    return out


async def fetch_archive(
    lat: float,
    lon: float,
    start: date,
    end: date,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[DailyWeather]:
    """Daily history for one point. Lags real time by a few days."""
    params = {
        "parameters": ",".join(PARAMETERS),
        "community": "AG",
        "latitude": lat,
        "longitude": lon,
        "start": start.strftime("%Y%m%d"),
        "end": end.strftime("%Y%m%d"),
        "format": "JSON",
    }

    owns = client is None
    client = client or httpx.AsyncClient()
    try:
        response = await client.get(URL, params=params, timeout=60.0)
        if response.status_code != 200:
            raise WeatherUnavailable(
                f"NASA POWER request failed ({response.status_code}): "
                f"{response.text[:200]}"
            )
        return _build(response.json(), lat)
    except httpx.HTTPError as exc:
        raise WeatherUnavailable(f"NASA POWER unreachable: {exc}") from exc
    finally:
        if owns:
            await client.aclose()
