"""Falling back when a free API's rate limit is not ours to fix.

Open-Meteo limits per IP. On shared hosting that IP belongs to the platform and
is shared with every other tenant, so a node making two calls a day can be
refused over traffic it had no part in -- which is what happened to the first
deployed AgriN node, on its first field. Without a fallback the consequence is
not a degraded advisory but no irrigation advice at all, indefinitely, with
nothing the operator can do about it.

What these pin is that the fallback is a fallback: the better source is always
tried first, sources are never blended, and a total outage says so loudly
rather than returning an empty series that reads like fine weather.
"""

from datetime import date

import pytest

from app.providers import weather_chain
from app.providers.weather import DailyWeather, WeatherUnavailable

START, END = date(2026, 6, 1), date(2026, 6, 3)


def row(source: str, day: int = 1) -> DailyWeather:
    return DailyWeather(
        day=date(2026, 6, day), tmax_c=35.0, tmin_c=25.0, tmean_c=30.0,
        precip_mm=2.0, rad_mj=20.0, rh_mean=50.0, wind2_ms=1.5,
        soil_moist=None, et0_mm=5.0, et0_provider_mm=None, source=source,
    )


def answering(source: str):
    async def fetch(*_args, **_kwargs):
        return [row(source)]
    return fetch


def refusing(message: str = "rate limited (429)"):
    async def fetch(*_args, **_kwargs):
        raise WeatherUnavailable(message)
    return fetch


def breaking(exc: Exception):
    async def fetch(*_args, **_kwargs):
        raise exc
    return fetch


def empty():
    async def fetch(*_args, **_kwargs):
        return []
    return fetch


@pytest.mark.asyncio
class TestArchiveFallback:
    async def test_the_better_source_is_used_when_it_answers(self, monkeypatch):
        # Open-Meteo carries modelled radiation and a finer archive, so it must
        # never be skipped just because a fallback exists.
        monkeypatch.setattr(weather_chain, "ARCHIVE_SOURCES", (
            ("open-meteo", answering("open-meteo")),
            ("nasa-power", answering("nasa-power")),
        ))
        rows = await weather_chain.fetch_archive(30.9, 75.8, START, END)
        assert [r.source for r in rows] == ["open-meteo"]

    async def test_a_rate_limit_falls_through(self, monkeypatch):
        monkeypatch.setattr(weather_chain, "ARCHIVE_SOURCES", (
            ("open-meteo", refusing()),
            ("nasa-power", answering("nasa-power")),
        ))
        rows = await weather_chain.fetch_archive(30.9, 75.8, START, END)
        assert [r.source for r in rows] == ["nasa-power"]

    @pytest.mark.parametrize("failure", [
        TimeoutError("timed out"),
        ValueError("unparseable payload"),
        KeyError("properties"),
    ])
    async def test_any_failure_falls_through_not_just_the_expected_one(
        self, monkeypatch, failure
    ):
        # The bug this mirrors already happened once in the refresh handler:
        # catching only the provider's own exception let everything else
        # escape and take the rest of the work down with it.
        monkeypatch.setattr(weather_chain, "ARCHIVE_SOURCES", (
            ("open-meteo", breaking(failure)),
            ("nasa-power", answering("nasa-power")),
        ))
        rows = await weather_chain.fetch_archive(30.9, 75.8, START, END)
        assert rows[0].source == "nasa-power"

    async def test_a_source_answering_with_no_days_is_treated_as_a_failure(
        self, monkeypatch
    ):
        # An empty series is indistinguishable downstream from a dry week, and
        # a water balance fed nothing quietly reports no irrigation needed.
        monkeypatch.setattr(weather_chain, "ARCHIVE_SOURCES", (
            ("open-meteo", empty()),
            ("nasa-power", answering("nasa-power")),
        ))
        rows = await weather_chain.fetch_archive(30.9, 75.8, START, END)
        assert rows[0].source == "nasa-power"

    async def test_total_outage_raises_and_names_every_source(self, monkeypatch):
        monkeypatch.setattr(weather_chain, "ARCHIVE_SOURCES", (
            ("open-meteo", refusing("rate limited (429)")),
            ("nasa-power", refusing("503 from Langley")),
        ))
        with pytest.raises(WeatherUnavailable) as excinfo:
            await weather_chain.fetch_archive(30.9, 75.8, START, END)
        message = str(excinfo.value)
        assert "open-meteo" in message and "429" in message
        assert "nasa-power" in message and "503" in message


@pytest.mark.asyncio
class TestForecastFallback:
    async def test_the_better_source_wins(self, monkeypatch):
        monkeypatch.setattr(weather_chain, "FORECAST_SOURCES", (
            ("open-meteo", answering("open-meteo")),
            ("met-no", answering("met-no")),
        ))
        rows = await weather_chain.fetch_forecast(30.9, 75.8, days=7)
        assert rows[0].source == "open-meteo"

    async def test_it_falls_through_to_met_norway(self, monkeypatch):
        monkeypatch.setattr(weather_chain, "FORECAST_SOURCES", (
            ("open-meteo", refusing()),
            ("met-no", answering("met-no")),
        ))
        rows = await weather_chain.fetch_forecast(30.9, 75.8, days=7)
        assert rows[0].source == "met-no"

    async def test_total_outage_raises_rather_than_returning_nothing(self, monkeypatch):
        # Returning [] here would write no rows, and /ready would report the
        # field as having no weather with no explanation of why.
        monkeypatch.setattr(weather_chain, "FORECAST_SOURCES", (
            ("open-meteo", refusing()),
            ("met-no", refusing()),
        ))
        with pytest.raises(WeatherUnavailable):
            await weather_chain.fetch_forecast(30.9, 75.8, days=7)


@pytest.mark.asyncio
class TestSourcesAreNeverBlended:
    async def test_one_call_returns_one_source(self, monkeypatch):
        """A series stitched from two models has discontinuities at the joins
        that nothing downstream could detect.

        Measured between these two at a Punjab field: POWER reads ~5 C hotter
        than ERA5 and returns 28% more seasonal rainfall. Splicing them would
        put a step change in the middle of the rainfall total the crop
        suggestions are computed from.
        """
        monkeypatch.setattr(weather_chain, "ARCHIVE_SOURCES", (
            ("open-meteo", answering("open-meteo")),
            ("nasa-power", answering("nasa-power")),
        ))
        rows = await weather_chain.fetch_archive(30.9, 75.8, START, END)
        assert len({r.source for r in rows}) == 1

    async def test_every_row_carries_its_provenance(self, monkeypatch):
        # The source column is how a mixed history is later declared to the
        # farmer rather than presented as one smooth series.
        monkeypatch.setattr(weather_chain, "ARCHIVE_SOURCES", (
            ("open-meteo", refusing()),
            ("nasa-power", answering("nasa-power")),
        ))
        rows = await weather_chain.fetch_archive(30.9, 75.8, START, END)
        assert all(r.source for r in rows)


class TestTheOrderIsDeliberate:
    def test_open_meteo_leads_both_chains(self):
        assert weather_chain.ARCHIVE_SOURCES[0][0] == "open-meteo"
        assert weather_chain.FORECAST_SOURCES[0][0] == "open-meteo"

    def test_each_chain_has_a_fallback_at_all(self):
        assert len(weather_chain.ARCHIVE_SOURCES) >= 2
        assert len(weather_chain.FORECAST_SOURCES) >= 2
