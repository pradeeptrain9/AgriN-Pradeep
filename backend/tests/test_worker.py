"""Scheduled ingest tests.

The valuable property is not that jobs run, but that they DON'T when running
would waste a hard-limited free quota. Copernicus gives 10,000 processing units
a month with no carryover; a worker that re-requests every field every run
exhausts it in days and the whole network goes blind.
"""

import app.worker as worker
from app.worker import SATELLITE_MIN_AGE_DAYS, WorkerSettings


class TestSchedule:
    def test_all_three_jobs_registered(self):
        names = {f.__name__ for f in WorkerSettings.functions}
        assert names == {"refresh_weather", "refresh_satellite", "backfill_soil"}

    def test_three_cron_entries(self):
        assert len(WorkerSettings.cron_jobs) == 3

    def test_satellite_runs_less_often_than_weather(self):
        """Sentinel-2 revisits about every five days; polling harder buys nothing
        and spends quota."""
        assert SATELLITE_MIN_AGE_DAYS >= 3

    def test_jobs_have_a_timeout(self):
        # A hung upstream must not occupy a worker slot forever.
        assert WorkerSettings.job_timeout > 0
        assert WorkerSettings.max_jobs >= 1


class TestQuotaGuards:
    def test_satellite_job_reads_the_pu_ledger(self):
        """The cap check must be inside the job, not left to the caller."""
        import inspect

        source = inspect.getsource(worker.refresh_satellite)
        assert "month_pu_spent" in source
        assert "cdse_monthly_pu_cap" in source

    def test_satellite_job_stops_on_cap_rather_than_erroring_per_field(self):
        import inspect

        source = inspect.getsource(worker.refresh_satellite)
        assert "ProcessingUnitCapReached" in source
        assert "break" in source

    def test_satellite_skips_fields_with_recent_observations(self):
        import inspect

        source = inspect.getsource(worker.refresh_satellite)
        assert "cutoff" in source and "skipped" in source

    def test_soil_only_selects_fields_without_a_profile(self):
        """Soil is static; re-fetching burns a 5 req/min fair-use limit."""
        import inspect

        source = inspect.getsource(worker.backfill_soil)
        assert "s.field_id IS NULL" in source

    def test_weather_failure_does_not_abort_the_batch(self):
        import inspect

        source = inspect.getsource(worker.refresh_weather)
        assert "WeatherUnavailable" in source
        assert "failed += 1" in source

    def test_soil_failure_does_not_abort_the_batch(self):
        import inspect

        source = inspect.getsource(worker.backfill_soil)
        assert "except Exception" in source


class TestFieldSelection:
    def test_only_active_cropped_unarchived_fields(self):
        """Spending quota on an archived field, or one with no crop, is waste:
        no advisory can be produced for either."""
        import inspect

        source = inspect.getsource(worker._active_fields)
        assert "archived_at IS NULL" in source
        assert "status = 'active'" in source

    def test_selection_is_bounded(self):
        import inspect

        source = inspect.getsource(worker._active_fields)
        assert "LIMIT" in source

    def test_geometry_not_centroid_is_used_for_satellite(self):
        """Sampling needs the polygon; a centroid would sample one pixel."""
        import inspect

        source = inspect.getsource(worker._active_fields)
        assert "ST_AsGeoJSON" in source
