"""One failing data source must not take the others down with it.

Soil, weather and satellite come from three unrelated providers -- SoilGrids,
Open-Meteo and Copernicus -- and a farmer tapping "Update from satellite" gets
no error either way, because the refresh runs in the background after the 202.

So the failure mode this guards against is silent and confusing: weather raises
something that is not WeatherUnavailable, the task dies, satellite never runs,
and the node ends up with no weather AND no satellite. Nothing on the phone and
nothing in /ready says which of the two actually broke -- and the one that did
break is invisible because the one that didn't is equally empty.
"""

import pytest

from app.api import advisory
from app.providers.sentinel import ProcessingUnitCapReached, SentinelUnavailable
from app.providers.weather import WeatherUnavailable

GEOMETRY = {"type": "Polygon", "coordinates": [[[77.0, 13.0], [77.001, 13.0],
                                                [77.001, 13.001], [77.0, 13.0]]]}


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


@pytest.fixture
def ran(monkeypatch):
    """Record which sources were attempted, and let each be made to fail."""
    attempted: list[str] = []
    failures: dict[str, Exception] = {}

    def source(name):
        async def run(*_args, **_kwargs):
            attempted.append(name)
            if name in failures:
                raise failures[name]
            return {}
        return run

    monkeypatch.setattr(advisory, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(advisory, "ingest_soil", source("soil"))
    monkeypatch.setattr(advisory, "ingest_weather", source("weather"))
    monkeypatch.setattr(advisory, "ingest_satellite", source("satellite"))
    return attempted, failures


async def _refresh():
    await advisory._refresh_field("f-1", 13.0, 77.0, GEOMETRY)


@pytest.mark.asyncio
class TestEverySourceIsAttempted:
    async def test_all_three_run_when_nothing_fails(self, ran):
        attempted, _ = ran
        await _refresh()
        assert attempted == ["soil", "weather", "satellite"]

    async def test_satellite_still_runs_after_weather_raises_unexpectedly(self, ran):
        # The exact bug: an httpx timeout or a database error is not
        # WeatherUnavailable, so the old handler let it escape and kill the
        # task before satellite was ever attempted.
        attempted, failures = ran
        failures["weather"] = TimeoutError("upstream took too long")
        await _refresh()
        assert "satellite" in attempted

    async def test_weather_still_runs_after_soil_raises(self, ran):
        attempted, failures = ran
        failures["soil"] = RuntimeError("SoilGrids is a beta service")
        await _refresh()
        assert attempted == ["soil", "weather", "satellite"]

    @pytest.mark.parametrize("boom", [
        WeatherUnavailable("open-meteo 503"),
        TimeoutError("timed out"),
        ValueError("garbage payload"),
        KeyError("daily"),
    ])
    async def test_no_exception_type_escapes(self, ran, boom):
        # A background task that raises dies silently. Whatever weather throws,
        # satellite must still get its turn.
        attempted, failures = ran
        failures["weather"] = boom
        await _refresh()
        assert attempted == ["soil", "weather", "satellite"]

    async def test_the_task_itself_never_raises(self, ran):
        _, failures = ran
        for name in ("soil", "weather", "satellite"):
            failures[name] = RuntimeError("everything is down")
        await _refresh()  # must not raise


@pytest.mark.asyncio
class TestTheLogSaysWhichAndWhy:
    """stdout is the only place these failures surface, so it has to be read
    by a person hours later with no other evidence."""

    async def test_an_unexpected_failure_names_the_exception_type(self, ran, capsys):
        # "weather failed: " with an empty message is what several exceptions
        # print, and it tells an operator nothing at all.
        _, failures = ran
        failures["weather"] = TimeoutError()
        await _refresh()
        assert "TimeoutError" in capsys.readouterr().out

    async def test_an_expected_outage_is_not_dressed_up_as_a_fault(self, ran, capsys):
        # Copernicus budget spent, or upstream down. Not a bug in this node,
        # and an operator should not go hunting for one.
        _, failures = ran
        failures["satellite"] = ProcessingUnitCapReached("monthly cap")
        await _refresh()
        out = capsys.readouterr().out
        assert "unavailable" in out
        assert "FAILED" not in out

    async def test_the_counts_are_logged_not_just_the_word_ok(self, ran, capsys, monkeypatch):
        """A satellite run that finds nothing but cloud writes no observations,
        spends processing units, raises nothing, and used to log the same word
        as a run that worked. Crop health then reads "not known" with no way to
        tell a cloudy fortnight from a broken pipeline."""
        from app.api import advisory as module

        async def cloudy(*_args, **_kwargs):
            return {"observations": 0, "intervals_returned": 12,
                    "intervals_rejected_for_cloud": 12}

        monkeypatch.setattr(module, "ingest_satellite", cloudy)
        await _refresh()
        out = capsys.readouterr().out
        assert "observations=0" in out
        assert "intervals_rejected_for_cloud=12" in out

    async def test_success_is_logged_too(self, ran, capsys):
        # Otherwise silence is ambiguous: did it work, or did the task never
        # run at all? That ambiguity is what made this bug hard to see.
        await _refresh()
        out = capsys.readouterr().out
        for name in ("soil", "weather", "satellite"):
            assert f"{name} ok" in out

    async def test_sentinel_outage_is_expected_not_a_fault(self, ran, capsys):
        _, failures = ran
        failures["satellite"] = SentinelUnavailable("no cloud-free scene")
        await _refresh()
        assert "FAILED" not in capsys.readouterr().out
