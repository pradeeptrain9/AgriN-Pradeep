"""MET Norway forecast adapter: the fallback when Open-Meteo will not answer.

The Norwegian Meteorological Institute publishes a global forecast free, with
no API key and no registration, under a CC-BY licence. Their one hard
requirement is a User-Agent that identifies the application and gives a way to
contact whoever runs it; requests without one are refused, and that is fair --
it is how a public service protects itself without charging.

The gap, and it matters: Locationforecast publishes temperature, humidity,
wind and precipitation, and **no solar radiation**. FAO-56 Penman-Monteith
needs Rs, so it is estimated from cloud cover -- treating the daytime cloud
fraction as the complement of sunshine hours and applying the Angstrom
relation, Eq. 35. The daily temperature range, Eq. 50, is the fallback for the
tail of the forecast where cloud cover is missing.

Measured against Open-Meteo's modelled radiation over seven days at a Punjab
field, seasonal ET0 came out about 8% high. That is one site over one week and
is not strong evidence; it was not tuned further, because tuning empirical
coefficients against a sample that size fits noise rather than climate. The
direction is worth knowing: 8% high biases irrigation advice toward watering
more than needed, which in a state whose groundwater is already falling is not
a harmless direction to be wrong in.

Every row is stamped `met-no` so the weaker basis travels with the number
instead of being assumed away.

Wind is reported at 10 m and corrected to 2 m with the FAO-56 logarithmic
profile, the same as the Open-Meteo path.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx

from app.engine.et0 import (
    actual_vapour_pressure_from_rh,
    daylight_hours,
    et0_penman_monteith,
    extraterrestrial_radiation,
    saturation_vapour_pressure,
    solar_radiation_from_sunshine,
    solar_radiation_from_temperature_range,
    wind_speed_at_2m,
)
from app.providers.weather import DailyWeather, WeatherUnavailable

URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"

SOURCE = "met-no"

# MET Norway requires identification and a contact route. Theirs is a public
# service funded by Norwegian taxpayers; anonymous scraping is how it gets
# taken away from everyone.
USER_AGENT = (
    "AgriN/0.1 (digital public good, agriculture advisory node; "
    "https://github.com/agrin-network)"
)

# Beyond about ten days the model has hourly entries only for the first two,
# and the tail is coarse enough that a daily aggregate is mostly noise.
MAX_FORECAST_DAYS = 10


@dataclass
class _DayBucket:
    temps: list[float]
    humidities: list[float]
    winds: list[float]
    # (utc_hour, cloud_percent). The hour is kept because cloud at midnight
    # says nothing about how much sun a crop saw.
    clouds: list[tuple[float, float]]
    precip_mm: float


def _bucket_by_day(timeseries: list[dict]) -> dict[date, _DayBucket]:
    buckets: dict[date, _DayBucket] = defaultdict(
        lambda: _DayBucket(
            temps=[], humidities=[], winds=[], clouds=[], precip_mm=0.0
        )
    )

    for entry in timeseries:
        stamp = entry.get("time")
        if not stamp:
            continue
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        bucket = buckets[moment.date()]

        instant = (entry.get("data", {}).get("instant", {}).get("details", {}))
        if (temp := instant.get("air_temperature")) is not None:
            bucket.temps.append(float(temp))
        if (rh := instant.get("relative_humidity")) is not None:
            bucket.humidities.append(float(rh))
        if (wind := instant.get("wind_speed")) is not None:
            bucket.winds.append(float(wind))
        if (cloud := instant.get("cloud_area_fraction")) is not None:
            utc_hour = moment.hour + moment.minute / 60.0
            bucket.clouds.append((utc_hour, float(cloud)))

        # Exactly one precipitation window per entry, or the same rain is
        # counted twice. The model gives hourly entries for the first two days
        # and six-hourly after that, so preferring the 1-hour window when it
        # exists covers each period once and only once.
        data = entry.get("data", {})
        for window in ("next_1_hours", "next_6_hours"):
            details = data.get(window, {}).get("details")
            if details and details.get("precipitation_amount") is not None:
                bucket.precip_mm += float(details["precipitation_amount"])
                break

    return buckets


def _daytime_sunshine_fraction(
    clouds: list[tuple[float, float]], lat: float, lon: float, doy: int
) -> float | None:
    """n/N from cloud cover, counting only the hours the sun is actually up.

    Averaging cloud across the whole day was the first attempt and it was
    wrong: half those samples are night, when cloud cover has no bearing on how
    much radiation reached the crop. At a Punjab field that error put seasonal
    ET0 14% high, having previously been 18% low from the temperature-range
    method -- an over-watering bias replacing an under-watering one.

    The daylight window is solar noon +/- half the day length. Solar noon in
    UTC is 12:00 shifted by longitude at 15 degrees per hour; that ignores the
    equation of time, worth up to a quarter of an hour, which is far below the
    accuracy of anything else here.
    """
    if not clouds:
        return None

    length = daylight_hours(lat, doy)
    solar_noon_utc = 12.0 - lon / 15.0
    start, end = solar_noon_utc - length / 2, solar_noon_utc + length / 2

    def lit(hour: float) -> bool:
        # The window can cross midnight UTC for eastern longitudes.
        for shift in (-24.0, 0.0, 24.0):
            if start <= hour + shift <= end:
                return True
        return False

    daytime = [cloud for hour, cloud in clouds if lit(hour)]
    if not daytime:
        return None

    return max(0.0, 1.0 - (sum(daytime) / len(daytime)) / 100.0)


def _build(payload: dict, lat: float, lon: float, days: int) -> list[DailyWeather]:
    properties = payload.get("properties", {})
    timeseries = properties.get("timeseries", [])
    if not timeseries:
        raise WeatherUnavailable("MET Norway returned an empty timeseries")

    elevation = 0.0
    coords = payload.get("geometry", {}).get("coordinates", [])
    if len(coords) >= 3 and isinstance(coords[2], (int, float)):
        elevation = float(coords[2])

    buckets = _bucket_by_day(timeseries)
    today = datetime.now().date()
    horizon = today + timedelta(days=min(days, MAX_FORECAST_DAYS))

    out: list[DailyWeather] = []
    for day in sorted(buckets):
        if day < today or day > horizon:
            continue
        bucket = buckets[day]
        if len(bucket.temps) < 4:
            # A part-covered day -- typically the far tail, or today when the
            # forecast starts mid-afternoon. Its max and min are not the day's,
            # and a wrong Tmax feeds straight into the radiation estimate.
            continue

        tmax, tmin = max(bucket.temps), min(bucket.temps)
        tmean = sum(bucket.temps) / len(bucket.temps)

        rh = sum(bucket.humidities) / len(bucket.humidities) if bucket.humidities else None
        if rh is not None:
            ea = actual_vapour_pressure_from_rh(tmax, tmin, rh, None)
        else:
            ea = saturation_vapour_pressure(tmin)

        u10 = sum(bucket.winds) / len(bucket.winds) if bucket.winds else None
        u2 = wind_speed_at_2m(u10, 10.0) if u10 is not None else 2.0

        # No radiation in this product, so it has to be estimated -- but the
        # forecast does carry cloud cover, which is a far more direct proxy for
        # sunshine than the daily temperature range.
        #
        # Measured at a Punjab field against Open-Meteo's modelled radiation:
        # Eq. 50 from temperature range ran about 18% low on seasonal ET0,
        # which for irrigation advice is the harmful direction -- a farmer told
        # their crop needs less water than it does. Treating cloud cover as the
        # complement of sunshine hours (n/N = 1 - cloud fraction) and using the
        # Angstrom relation, Eq. 35, tracks much closer.
        #
        # Temperature range stays as the fallback for the far tail of the
        # forecast, where cloud cover is sometimes absent.
        doy = day.timetuple().tm_yday
        ra = extraterrestrial_radiation(lat, doy)
        n_over_n = _daytime_sunshine_fraction(bucket.clouds, lat, lon, doy)
        if n_over_n is not None:
            length = daylight_hours(lat, doy)
            rs = solar_radiation_from_sunshine(n_over_n * length, length, ra)
        else:
            rs = solar_radiation_from_temperature_range(
                tmax, tmin, ra, elevation_m=elevation
            )

        result = et0_penman_monteith(
            tmax_c=tmax,
            tmin_c=tmin,
            ea_kpa=ea,
            u2_ms=u2,
            rs_mj=rs,
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
                precip_mm=bucket.precip_mm,
                rad_mj=rs,
                rh_mean=rh,
                wind2_ms=u2,
                soil_moist=None,
                et0_mm=max(0.0, result.et0_mm),
                et0_provider_mm=None,
                source=SOURCE,
            )
        )
    return out


async def fetch_forecast(
    lat: float, lon: float, *, days: int = 10, client: httpx.AsyncClient | None = None
) -> list[DailyWeather]:
    """Forecast from today forward. MET Norway is useful for about ten days."""
    # Their terms ask for coordinates truncated to four decimals, which is
    # ~10 m -- finer than any forecast model resolves, and it lets them cache.
    params = {"lat": round(lat, 4), "lon": round(lon, 4)}

    owns = client is None
    client = client or httpx.AsyncClient(headers={"User-Agent": USER_AGENT})
    try:
        response = await client.get(
            URL, params=params, timeout=30.0, headers={"User-Agent": USER_AGENT}
        )
        if response.status_code != 200:
            raise WeatherUnavailable(
                f"MET Norway request failed ({response.status_code}): "
                f"{response.text[:200]}"
            )
        return _build(response.json(), lat, lon, days)
    except httpx.HTTPError as exc:
        raise WeatherUnavailable(f"MET Norway unreachable: {exc}") from exc
    finally:
        if owns:
            await client.aclose()
