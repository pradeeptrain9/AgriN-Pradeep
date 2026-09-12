"""Open-Meteo weather adapter.

Free, no API key, but non-commercial use only and rate limited to roughly
10,000 calls/day, 5,000/hour, 600/minute. One call covers a whole field for a
whole date range, so a node stays far inside that even at pilot scale.

Open-Meteo publishes its own `et0_fao_evapotranspiration`, but we compute ET0
ourselves with app.engine.et0 and keep theirs only as a cross-check. The
irrigation advice must trace back to code we can test against the FAO worked
examples, not to a remote service that may change.

Daily relative humidity aggregates do not exist in the API, so actual vapour
pressure is derived from hourly dewpoint (FAO-56 Eq. 14: ea = e0(Tdew)), which
is the preferred method anyway.
"""

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from app.engine.et0 import (
    et0_penman_monteith,
    saturation_vapour_pressure,
    wind_speed_at_2m,
)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "precipitation_sum",
    "shortwave_radiation_sum",
    "et0_fao_evapotranspiration",
]
_BASE_HOURLY = ["dew_point_2m", "relative_humidity_2m", "wind_speed_10m"]

# The forecast model and the ERA5 archive expose different soil layer bands.
# Asking the archive for a forecast-only band returns a column of nulls rather
# than an error, so the two must be kept apart deliberately.
FORECAST_SOIL_VAR = "soil_moisture_9_to_27cm"
ARCHIVE_SOIL_VAR = "soil_moisture_7_to_28cm"

FORECAST_HOURLY_VARS = [*_BASE_HOURLY, FORECAST_SOIL_VAR]
ARCHIVE_HOURLY_VARS = [*_BASE_HOURLY, ARCHIVE_SOIL_VAR]

USER_AGENT = "AgriN/0.1 (digital public good; agriculture advisory node)"


class WeatherUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class DailyWeather:
    day: date
    tmax_c: float
    tmin_c: float
    tmean_c: float
    precip_mm: float
    rad_mj: float
    rh_mean: float | None
    wind2_ms: float | None
    soil_moist: float | None
    et0_mm: float                # ours, FAO-56
    et0_provider_mm: float | None  # Open-Meteo's, for cross-check
    source: str = "open-meteo"


def _aggregate_hourly(times: list[str], values: list[float | None]) -> dict[date, list[float]]:
    buckets: dict[date, list[float]] = defaultdict(list)
    for stamp, value in zip(times, values, strict=True):
        if value is None:
            continue
        buckets[datetime.fromisoformat(stamp).date()].append(value)
    return buckets


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _build(payload: dict, lat: float, soil_var: str) -> list[DailyWeather]:
    elevation = payload.get("elevation", 0.0) or 0.0
    daily = payload.get("daily", {})
    hourly = payload.get("hourly", {})

    times = hourly.get("time", [])
    dew = _aggregate_hourly(times, hourly.get("dew_point_2m", [])) if times else {}
    rh = _aggregate_hourly(times, hourly.get("relative_humidity_2m", [])) if times else {}
    wind = _aggregate_hourly(times, hourly.get("wind_speed_10m", [])) if times else {}
    soil = _aggregate_hourly(times, hourly.get(soil_var, [])) if times else {}

    out: list[DailyWeather] = []
    for i, day_str in enumerate(daily.get("time", [])):
        day = date.fromisoformat(day_str)

        def pick(key: str, idx: int = i) -> float | None:
            series = daily.get(key) or []
            return series[idx] if idx < len(series) else None

        tmax, tmin = pick("temperature_2m_max"), pick("temperature_2m_min")
        rad = pick("shortwave_radiation_sum")
        if tmax is None or tmin is None or rad is None:
            # Incomplete day (usually the tail of a forecast window); skip it
            # rather than feeding None into the water balance.
            continue

        tmean = pick("temperature_2m_mean")
        tmean = tmean if tmean is not None else (tmax + tmin) / 2

        dew_mean = _mean(dew.get(day, []))
        if dew_mean is not None:
            ea = saturation_vapour_pressure(dew_mean)
        else:
            # Fall back to the FAO-56 arid-climate approximation ea = e0(Tmin).
            ea = saturation_vapour_pressure(tmin)

        wind_mean = _mean(wind.get(day, []))
        u2 = wind_speed_at_2m(wind_mean, 10.0) if wind_mean is not None else 2.0

        result = et0_penman_monteith(
            tmax_c=tmax,
            tmin_c=tmin,
            ea_kpa=ea,
            u2_ms=u2,
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
                precip_mm=pick("precipitation_sum") or 0.0,
                rad_mj=rad,
                rh_mean=_mean(rh.get(day, [])),
                wind2_ms=u2,
                soil_moist=_mean(soil.get(day, [])),
                et0_mm=max(0.0, result.et0_mm),
                et0_provider_mm=pick("et0_fao_evapotranspiration"),
            )
        )
    return out


async def _get(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    """Fetch with backoff, and say what actually went wrong when it does not.

    The 429 branch used to `continue` without recording anything, so a node
    that was rate-limited on all three attempts raised
    "open-meteo request failed: None" -- a message that names neither the cause
    nor anything an operator could act on. Rate limiting is the *expected*
    failure on shared hosting, where the outbound IP belongs to the platform
    and is shared with every other tenant on it, so it is the one case that
    most needed saying out loud.

    Retry-After is honoured when the server sends it. Open-Meteo's limits are
    per-minute and per-hour; doubling from one second reaches four, which is
    not a serious attempt to wait out either.
    """
    last_error: Exception | None = None

    for attempt in range(3):
        try:
            response = await client.get(url, params=params, timeout=30.0)
            if response.status_code == 429:
                last_error = WeatherUnavailable(
                    "rate limited (429). On shared hosting the outbound IP is "
                    "the platform's and is shared with other tenants, so this "
                    "can happen without this node having made any requests of "
                    "its own."
                )
                await asyncio.sleep(_retry_after(response, attempt))
                continue
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:  # transient network/5xx
            last_error = exc
            await asyncio.sleep(2 ** attempt)

    detail = f"{type(last_error).__name__}: {last_error}" if last_error else (
        "no attempt succeeded and none reported why, which should not happen"
    )
    raise WeatherUnavailable(f"open-meteo request failed: {detail}")


def _retry_after(response: httpx.Response, attempt: int) -> float:
    """Seconds to wait, preferring the server's own instruction."""
    header = response.headers.get("Retry-After", "")
    try:
        # Capped: a provider asking for ten minutes should not hold a farmer's
        # refresh open. Better to fail, say why, and let them tap again.
        return min(float(header), 30.0)
    except ValueError:
        return float(2 ** attempt)


async def fetch_forecast(
    lat: float, lon: float, *, days: int = 16, client: httpx.AsyncClient | None = None
) -> list[DailyWeather]:
    """Forecast from today forward. Open-Meteo caps this at 16 days."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": ",".join(DAILY_VARS),
        "hourly": ",".join(FORECAST_HOURLY_VARS),
        "wind_speed_unit": "ms",
        "timezone": "auto",
        "forecast_days": min(days, 16),
    }
    owns = client is None
    client = client or httpx.AsyncClient(headers={"User-Agent": USER_AGENT})
    try:
        return _build(await _get(client, FORECAST_URL, params), lat, FORECAST_SOIL_VAR)
    finally:
        if owns:
            await client.aclose()


async def fetch_archive(
    lat: float,
    lon: float,
    start: date,
    end: date,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[DailyWeather]:
    """ERA5 reanalysis. Lags real time by about five days."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": ",".join(DAILY_VARS),
        "hourly": ",".join(ARCHIVE_HOURLY_VARS),
        "wind_speed_unit": "ms",
        "timezone": "auto",
    }
    owns = client is None
    client = client or httpx.AsyncClient(headers={"User-Agent": USER_AGENT})
    try:
        return _build(await _get(client, ARCHIVE_URL, params), lat, ARCHIVE_SOIL_VAR)
    finally:
        if owns:
            await client.aclose()
