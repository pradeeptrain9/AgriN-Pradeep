"""Asking Copernicus only for what is not already stored.

Every tap of "Update from satellite" used to request the full two hundred days.
On the deployed node two consecutive taps each spent 1.67 processing units to
re-fetch the same period and rewrite the same seventy-two observations.

Processing units are the free Copernicus quota, and that quota is the only
thing standing between a twenty-five farmer pilot and no crop health at all.
Units scale with area times time span, so the width of this window is the
whole cost.
"""

from datetime import date, timedelta

import pytest

from app.services import ingest


class Recorder:
    """Captures the date range actually requested."""

    def __init__(self):
        self.start: date | None = None
        self.end: date | None = None

    async def __call__(self, *, cdse, geometry, start, end):
        self.start, self.end = start, end

        class Result:
            observations = []
            processing_units = 0.5
            intervals_returned = 0
            intervals_rejected = 0

        return Result()


class FakeDb:
    """Answers the two scalar queries ingest_satellite makes, in order:
    the latest observation date, then when Copernicus was last asked."""

    def __init__(self, latest, last_checked=None):
        self._answers = [latest, last_checked]
        self.executed = []

    async def scalar(self, *_args, **_kwargs):
        return self._answers.pop(0) if self._answers else None

    async def execute(self, statement, params=None):
        self.executed.append(str(statement))

    async def commit(self):
        pass


@pytest.fixture
def sentinel(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(ingest.sentinel_provider, "fetch_indices", recorder)
    monkeypatch.setattr(
        ingest.sentinel_provider, "CdseClient", lambda *a, **k: object()
    )

    async def no_spend(_db):
        return 0.0

    monkeypatch.setattr(ingest, "month_pu_spent", no_spend)

    class Settings:
        cdse_client_id = "id"
        cdse_client_secret = "secret"
        cdse_monthly_pu_cap = 9000.0

    monkeypatch.setattr(ingest, "get_settings", lambda: Settings())
    return recorder


GEOMETRY = {"type": "Polygon", "coordinates": [[[77.0, 13.0], [77.001, 13.0],
                                                [77.001, 13.001], [77.0, 13.0]]]}


@pytest.mark.asyncio
class TestTheWindowNarrowsOnceThereIsHistory:
    async def test_a_field_with_no_observations_pulls_the_full_history(self, sentinel):
        # The one time the wide window is right: there is nothing to build on.
        await ingest.ingest_satellite(FakeDb(None), "f-1", GEOMETRY, history_days=200)
        assert (sentinel.end - sentinel.start).days == 200

    async def test_a_field_with_recent_history_asks_for_days_not_months(self, sentinel):
        latest = date.today() - timedelta(days=3)
        await ingest.ingest_satellite(FakeDb(latest), "f-1", GEOMETRY, history_days=200)
        assert (sentinel.end - sentinel.start).days <= ingest.RESCAN_OVERLAP_DAYS + 3

    async def test_it_overlaps_rather_than_resuming_exactly(self, sentinel):
        # Sentinel-2 scenes are reprocessed, and a date already stored can
        # later improve. The insert is an upsert, so re-reading a few days
        # costs a little quota and corrects them.
        latest = date.today() - timedelta(days=2)
        await ingest.ingest_satellite(FakeDb(latest), "f-1", GEOMETRY)
        assert sentinel.start < latest

    async def test_a_long_dormant_field_does_not_pull_more_than_the_window(
        self, sentinel
    ):
        # A field last seen two years ago must not quietly request two years.
        latest = date.today() - timedelta(days=730)
        await ingest.ingest_satellite(FakeDb(latest), "f-1", GEOMETRY, history_days=200)
        assert (sentinel.end - sentinel.start).days <= 200


@pytest.mark.asyncio
class TestNothingToDo:
    async def test_a_second_tap_within_a_revisit_spends_nothing(self, sentinel):
        # Sentinel-2 passes every five days or so. Tapping Update twice in an
        # afternoon asks a question whose answer cannot have changed.
        from datetime import datetime, timezone

        checked = datetime.now(timezone.utc) - timedelta(hours=1)
        result = await ingest.ingest_satellite(
            FakeDb(date.today() - timedelta(days=3), last_checked=checked),
            "f-1", GEOMETRY,
        )
        assert result["processing_units"] == 0.0
        assert result["skipped_checked_recently"] == 1
        assert sentinel.start is None, "Copernicus should not have been called"

    async def test_a_tap_the_next_day_does_look(self, sentinel):
        # The guard must not stop a farmer getting a fresh answer tomorrow.
        from datetime import datetime, timezone

        checked = datetime.now(timezone.utc) - timedelta(hours=20)
        await ingest.ingest_satellite(
            FakeDb(date.today() - timedelta(days=3), last_checked=checked),
            "f-1", GEOMETRY,
        )
        assert sentinel.start is not None

    async def test_a_field_never_queried_is_not_skipped(self, sentinel):
        await ingest.ingest_satellite(FakeDb(None, last_checked=None), "f-1", GEOMETRY)
        assert sentinel.start is not None

    async def test_skipping_is_visible_in_the_result(self, sentinel):
        # It ends up in the ingest log line, so an operator can tell a skip
        # from a failure. Both otherwise write zero observations.
        from datetime import datetime, timezone

        result = await ingest.ingest_satellite(
            FakeDb(date.today(), last_checked=datetime.now(timezone.utc)),
            "f-1", GEOMETRY,
        )
        assert result["observations"] == 0
        assert result["skipped_checked_recently"] == 1
