"""Weather with a fallback, because a free API's rate limit is not ours to fix.

Open-Meteo is the primary source and should stay that way: it is a single call
per field per range, it carries modelled solar radiation so ET0 is computed
rather than estimated, and its archive is ERA5 at a resolution the others do
not match.

What it is not is reliable on shared hosting. Its limits are per IP, and on a
platform like Render the outbound IP belongs to the platform and is shared with
every other tenant on it -- so a node making two calls a day can be refused
because of traffic it has no part in. That is exactly what happened to the
first deployed AgriN node, on its first field, and no amount of retrying fixes
it. A digital public good that other countries are meant to run on whatever
hosting they can afford cannot have its irrigation advice hostage to somebody
else's quota.

So each half of the problem has a keyless, free, public-institution fallback:

    archive    Open-Meteo (ERA5)  ->  NASA POWER
    forecast   Open-Meteo         ->  MET Norway

They are not equivalent, and pretending otherwise would be the real failure.
Measured at one Punjab field: POWER reads about 5 C hotter than ERA5 and
returns 28% more seasonal rainfall; MET Norway's ET0 runs about 8% high
because its radiation is estimated from cloud cover. Those differences change
advice -- 28% on rainfall is the gap between "the rain covers this crop" and
"it does not".

Which is why every row carries its `source`, why the sources are never blended
within a single call, and why a field whose history comes from more than one
of them says so in the advisory rather than presenting one smooth series.
"""

from __future__ import annotations

import logging
from datetime import date

import httpx

from app.providers import met_no, nasa_power
from app.providers import weather as open_meteo
from app.providers.weather import DailyWeather, WeatherUnavailable

logger = logging.getLogger(__name__)

# Ordered best-first. The first source that answers wins outright; results are
# never merged across sources, because a series stitched from two models has
# discontinuities at the joins that no consumer downstream could detect.
ARCHIVE_SOURCES = (
    ("open-meteo", open_meteo.fetch_archive),
    ("nasa-power", nasa_power.fetch_archive),
)

FORECAST_SOURCES = (
    ("open-meteo", open_meteo.fetch_forecast),
    ("met-no", met_no.fetch_forecast),
)


async def fetch_archive(
    lat: float, lon: float, start: date, end: date,
    *, client: httpx.AsyncClient | None = None,
) -> list[DailyWeather]:
    """Daily history, from the best source that will answer."""
    failures: list[str] = []
    for name, fetch in ARCHIVE_SOURCES:
        try:
            # Deliberately not sharing the caller's client: it carries
            # Open-Meteo's User-Agent, and MET Norway refuses requests that do
            # not identify themselves honestly.
            rows = await fetch(lat, lon, start, end)
            if rows:
                if failures:
                    logger.warning(
                        "weather archive fell back to %s after %s",
                        name, "; ".join(failures),
                    )
                return rows
            failures.append(f"{name}: returned no days")
        except WeatherUnavailable as exc:
            failures.append(f"{name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - one bad source must not end the chain
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

    raise WeatherUnavailable("no archive source answered -- " + "; ".join(failures))


async def fetch_forecast(
    lat: float, lon: float, *, days: int = 16,
    client: httpx.AsyncClient | None = None,
) -> list[DailyWeather]:
    """Forecast, from the best source that will answer."""
    failures: list[str] = []
    for name, fetch in FORECAST_SOURCES:
        try:
            rows = await fetch(lat, lon, days=days)
            if rows:
                if failures:
                    logger.warning(
                        "weather forecast fell back to %s after %s",
                        name, "; ".join(failures),
                    )
                return rows
            failures.append(f"{name}: returned no days")
        except WeatherUnavailable as exc:
            failures.append(f"{name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

    raise WeatherUnavailable("no forecast source answered -- " + "; ".join(failures))
