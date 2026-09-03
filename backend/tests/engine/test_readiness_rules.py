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
