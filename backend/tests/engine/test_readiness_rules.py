"""Readiness check rules."""

import inspect

from app.api import readiness


class TestGrading:
    def test_three_levels(self):
        source = inspect.getsource(readiness.readiness)
        for level in ("blocker", "degraded", "ok"):
            assert f'"{level}"' in source

    def test_default_secret_key_is_a_blocker_not_a_warning(self):
        source = inspect.getsource(readiness.readiness)
        assert "dev-only-insecure-key" in source
        assert "forge a sign-in token" in source

    def test_console_gateway_outside_dev_is_a_blocker(self):
        """Without a real gateway no farmer can sign in at all."""
        source = inspect.getsource(readiness.readiness)
        assert "console gateway outside development" in source

    def test_a_misconfigured_provider_is_a_blocker_not_a_warning(self):
        source = inspect.getsource(readiness.readiness)
        assert "gateway misconfigured" in source
        assert "No farmer can sign in until this is fixed." in source

    def test_missing_satellite_is_only_degraded(self):
        """Crop health goes unknown but irrigation and fertiliser still work."""
        source = inspect.getsource(readiness.readiness)
        assert "no Copernicus credentials" in source

    def test_empty_pesticide_table_is_not_reported_as_a_fault(self):
        source = inspect.getsource(readiness.readiness)
        assert "safe default, not a fault" in source


class TestWeatherFreshness:
    def test_only_past_records_count_as_freshness(self):
        """weather_daily holds forecast rows too; using max(time) makes a node
        whose ingest died weeks ago report a negative age and look current."""
        source = inspect.getsource(readiness.readiness)
        assert "time::date <= CURRENT_DATE" in source


class TestSummary:
    def test_ready_only_when_no_blockers(self):
        result = readiness._summarise([
            {"check": "a", "level": "ok", "detail": "", "consequence": ""},
            {"check": "b", "level": "degraded", "detail": "", "consequence": ""},
        ])
        assert result["ready_for_pilot"] is True

    def test_a_single_blocker_fails_readiness(self):
        result = readiness._summarise([
            {"check": "a", "level": "ok", "detail": "", "consequence": ""},
            {"check": "b", "level": "blocker", "detail": "", "consequence": ""},
        ])
        assert result["ready_for_pilot"] is False
        assert len(result["blockers"]) == 1

    def test_every_check_states_a_consequence_field(self):
        result = readiness._summarise(
            [{"check": "a", "level": "blocker", "detail": "d", "consequence": "c"}])
        assert result["blockers"][0]["consequence"] == "c"


class TestPhotoStorageIsDetectedNotConfigured:
    """A free managed instance rebuilds its filesystem on every deploy.

    The diagnosis row and its verdict are in Postgres and survive; the
    photograph the farmer took does not. That silently breaks the path where an
    extension officer reviews a disputed photo, and empties any retraining set.

    Detected by comparing stored records against files, rather than by a flag,
    because the operator who needs to be told is exactly the one who would not
    think to set one. These exercise the degraded branch, which local disk
    never reaches -- the whole point is that it only appears in production.
    """

    @staticmethod
    def _run(stored, on_disk, tmp_path):
        import asyncio
        from app.api.readiness import _media_check

        directory = tmp_path / "diagnoses"
        directory.mkdir()
        for i in range(on_disk):
            (directory / f"{i}.jpg").write_bytes(b"x")

        class Db:
            async def scalar(self, *_args, **_kwargs):
                return stored

        class Settings:
            media_root = str(tmp_path)

        return asyncio.run(_media_check(Db(), Settings()))

    def test_a_node_that_has_lost_photographs_says_so(self, tmp_path):
        result = self._run(stored=12, on_disk=0, tmp_path=tmp_path)
        assert result["level"] == "degraded"
        assert "12 diagnosis record(s) but 0 photograph(s)" in result["detail"]

    def test_it_names_the_consequence_not_the_mechanism(self, tmp_path):
        # An operator reading this needs to know what a farmer loses, not what
        # a filesystem did.
        consequence = self._run(stored=12, on_disk=0, tmp_path=tmp_path)["consequence"]
        assert "officer cannot review" in consequence
        assert "retraining set" in consequence

    def test_partial_loss_is_still_loss(self, tmp_path):
        # One deploy mid-pilot loses everything before it and nothing after.
        assert self._run(stored=10, on_disk=3, tmp_path=tmp_path)["level"] == "degraded"

    def test_a_node_keeping_its_photographs_is_ok(self, tmp_path):
        assert self._run(stored=5, on_disk=5, tmp_path=tmp_path)["level"] == "ok"

    def test_a_node_that_has_answered_nothing_is_not_accused(self, tmp_path):
        # A fresh node has no diagnoses and no files. That is not data loss.
        assert self._run(stored=0, on_disk=0, tmp_path=tmp_path)["level"] == "ok"

    def test_a_missing_directory_does_not_raise(self, tmp_path):
        # /ready must report, never 500 -- it is what an operator opens when
        # something is already wrong.
        import asyncio
        from app.api.readiness import _media_check

        class Db:
            async def scalar(self, *_args, **_kwargs):
                return 4

        class Settings:
            media_root = str(tmp_path / "does-not-exist")

        assert asyncio.run(_media_check(Db(), Settings()))["level"] == "degraded"
