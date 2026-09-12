"""Two different reasons crop health cannot be scored, and only one is a wait.

Health reads NDVI from the sowing date onward, because imagery of bare soil or
of last season's crop says nothing about this one. So a field can hold two
hundred days of perfectly clear observations and still score nothing, simply
because it was sown last week.

The node used to say "No cloud-free satellite observation yet" in both cases.
On the first deployed node that was flatly untrue: it held 24 clear dates of
the farmer's field, having rejected 26 more for cloud, and told them the
satellite had seen nothing. Someone reading that concludes the satellite cannot
see their land and stops waiting for an answer that was days away.
"""

from datetime import date

import pytest

from app.services.advisory import _why_no_health


class FakeResult:
    def __init__(self, mapping):
        self._mapping = mapping

    def mappings(self):
        return self

    def first(self):
        return self._mapping


class FakeDb:
    def __init__(self, clear_views, latest):
        self._row = {"clear_views": clear_views, "latest": latest}

    async def execute(self, *_args, **_kwargs):
        return FakeResult(self._row)


SOWING = date(2026, 9, 1)


@pytest.mark.asyncio
class TestNothingSeenYet:
    async def test_it_says_the_satellite_has_not_seen_the_field(self):
        message = await _why_no_health(FakeDb(0, None), "f-1", SOWING)
        assert "No cloud-free satellite picture" in message

    async def test_it_explains_why_that_can_take_a_while(self):
        # A farmer who knows cloud is the reason will wait; one who thinks the
        # app is broken will not.
        message = await _why_no_health(FakeDb(0, None), "f-1", SOWING)
        assert "cloud" in message.lower()

    async def test_a_null_latest_is_not_formatted_as_a_date(self):
        # Guards the crash this would otherwise be: strftime on None.
        assert await _why_no_health(FakeDb(0, None), "f-1", SOWING)


@pytest.mark.asyncio
class TestSeenButBeforeSowing:
    async def test_it_does_not_claim_the_field_was_never_seen(self):
        # The lie that prompted this. The node held 24 clear views.
        message = await _why_no_health(FakeDb(24, date(2026, 8, 20)), "f-1", SOWING)
        assert "No cloud-free satellite picture" not in message

    async def test_it_says_how_many_clear_views_there_are(self):
        message = await _why_no_health(FakeDb(24, date(2026, 8, 20)), "f-1", SOWING)
        assert "24" in message

    async def test_it_names_both_dates_so_the_farmer_can_check_the_reasoning(self):
        message = await _why_no_health(FakeDb(24, date(2026, 8, 20)), "f-1", SOWING)
        assert "20 August" in message
        assert "1 September" in message

    async def test_it_explains_why_older_pictures_do_not_count(self):
        # Otherwise this reads as an arbitrary refusal to use data it has.
        message = await _why_no_health(FakeDb(24, date(2026, 8, 20)), "f-1", SOWING)
        assert "previous crop" in message

    async def test_it_says_the_wait_is_for_the_next_pass(self):
        message = await _why_no_health(FakeDb(24, date(2026, 8, 20)), "f-1", SOWING)
        assert "next clear pass" in message

    async def test_a_single_view_reads_correctly(self):
        assert "1 clear view" in await _why_no_health(
            FakeDb(1, date(2026, 8, 20)), "f-1", SOWING
        )
