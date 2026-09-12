"""Suggesting what to grow on a field that has no crop yet.

This is the question a farmer has immediately after walking a boundary, and the
one moment the app previously offered no help at all -- it asked them to type a
crop name into an empty box.

The ranking must stay a ranking. A farmer knows things about their land, their
labour and their buyer that no scoring function does, so the contract here is
"ranked and explained", never "decided".
"""

from datetime import date

import pytest

from app.services.crop_choice import MIN_WEATHER_DAYS, SUGGESTION_VERSION, suggest_crops


class FakeResult:
    def __init__(self, value=None, mapping=None):
        self._value = value
        self._mapping = mapping

    def scalar(self):
        return self._value

    def mappings(self):
        return self

    def first(self):
        return self._mapping


class FakeDb:
    """Answers the two queries suggest_crops makes, in order."""

    def __init__(self, *, days, rain_mm, mean_et0, previous=None,
                 sources="open-meteo", fallback_days=None):
        names = [s for s in sources.split(", ") if s]
        if fallback_days is None:
            # Default: everything that is not the primary source. Tests that
            # care about the threshold pass an explicit count.
            fallback_days = 0 if names == ["open-meteo"] else days
        self._climate = {
            "days": days, "rain_mm": rain_mm, "mean_et0": mean_et0,
            # Which provider the rainfall came from. Not decoration: the
            # sources disagree by tens of per cent on seasonal rainfall, so a
            # total stitched from two of them is a number with a step change
            # in the middle.
            "source_count": len(names),
            "sources": sources,
            "fallback_days": fallback_days,
        }
        self._previous = previous
        self.queries = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.queries.append(sql)
        if "weather_daily" in sql:
            return FakeResult(mapping=self._climate)
        return FakeResult(value=self._previous)


FIELD = {"id": "f-1", "soil": {"source": "soil_health_card", "texture": "loam"}}


async def _suggest(**kwargs):
    db = FakeDb(**kwargs)
    return await suggest_crops(db, field=FIELD, today=date(2026, 9, 12))


@pytest.mark.asyncio
class TestTheSuggestion:
    async def test_returns_a_ranked_list(self):
        out = await _suggest(days=180, rain_mm=600.0, mean_et0=4.8)
        scores = [s["score"] for s in out["suggestions"]]
        assert len(scores) > 1
        assert scores == sorted(scores, reverse=True)

    async def test_every_suggestion_explains_itself(self):
        # A score with no reasoning is a number a farmer cannot argue with, and
        # the whole design of the rotation engine is that it is inspectable.
        out = await _suggest(days=180, rain_mm=600.0, mean_et0=4.8)
        for suggestion in out["suggestions"]:
            assert suggestion["components"]
            assert "seasonal_water_need_mm" in suggestion

    async def test_it_says_the_choice_is_still_the_farmers(self):
        out = await _suggest(days=180, rain_mm=600.0, mean_et0=4.8)
        assert out["choice_is_open"] is True

    async def test_it_shows_what_the_ranking_was_based_on(self):
        out = await _suggest(days=180, rain_mm=600.0, mean_et0=4.8)
        basis = out["based_on"]
        assert basis["rainfall_last_180d_mm"] == 600.0
        assert basis["weather_days"] == 180
        assert basis["mean_et0_mm_day"] == 4.8

    async def test_version_is_reported(self):
        out = await _suggest(days=180, rain_mm=600.0, mean_et0=4.8)
        assert out["version"] == SUGGESTION_VERSION


@pytest.mark.asyncio
class TestHonestyAboutInputs:
    async def test_no_weather_is_declared_not_hidden(self):
        # Water fit is the heaviest weight in the model. Ranking crops with no
        # rainfall data and presenting it as advice would be a guess in a suit.
        out = await _suggest(days=0, rain_mm=0.0, mean_et0=0.0)
        assert any("No weather data" in g for g in out["gaps"])

    async def test_thin_weather_is_declared(self):
        out = await _suggest(days=MIN_WEATHER_DAYS - 1, rain_mm=40.0, mean_et0=4.0)
        assert any("provisional" in g for g in out["gaps"])

    async def test_enough_weather_raises_no_weather_gap(self):
        out = await _suggest(days=180, rain_mm=600.0, mean_et0=4.8)
        assert not any("weather" in g.lower() for g in out["gaps"])

    async def test_it_still_answers_with_no_weather_at_all(self):
        # Refusing outright would leave the farmer where they started. Rank on
        # the engine's default evaporative demand, and say so.
        out = await _suggest(days=0, rain_mm=0.0, mean_et0=0.0)
        assert out["suggestions"]
        assert out["based_on"]["mean_et0_mm_day"] == 4.5

    async def test_fallback_soil_is_declared(self):
        db = FakeDb(days=180, rain_mm=600.0, mean_et0=4.8)
        out = await suggest_crops(
            db,
            field={"id": "f-1", "soil": {"source": "fallback"}},
            today=date(2026, 9, 12),
        )
        assert any("Soil Health Card" in g for g in out["gaps"])


@pytest.mark.asyncio
class TestRotationAwareness:
    async def test_the_previous_crop_is_read_and_reported(self):
        out = await _suggest(
            days=180, rain_mm=600.0, mean_et0=4.8, previous="rice"
        )
        assert out["based_on"]["previous_crop"] == "Rice (paddy)"

    async def test_repeating_the_same_crop_scores_worse_than_breaking(self):
        # Growing rice after rice lets pests and pathogens carry straight over.
        after_rice = await _suggest(
            days=180, rain_mm=900.0, mean_et0=4.8, previous="rice"
        )
        ranked = {s["crop_code"]: s for s in after_rice["suggestions"]}
        if "rice" in ranked:
            best = after_rice["suggestions"][0]
            assert ranked["rice"]["components"]["rotation_break"] <= (
                best["components"]["rotation_break"]
            )

    async def test_an_unknown_previous_crop_does_not_break_the_ranking(self):
        out = await _suggest(
            days=180, rain_mm=600.0, mean_et0=4.8, previous="not_a_crop"
        )
        assert out["suggestions"]
        assert out["based_on"]["previous_crop"] is None

    async def test_no_history_is_handled(self):
        out = await _suggest(days=180, rain_mm=600.0, mean_et0=4.8, previous=None)
        assert out["based_on"]["previous_crop"] is None
        assert out["suggestions"]


@pytest.mark.asyncio
class TestWaterFitBites:
    """Water fit carries the largest weight, so it must actually show.

    Note what this does NOT assert: that a dry field and a wet field rank
    different crops first. They may not -- a legume can lead on nitrogen in
    both. What must differ is that the dry field says, on every candidate, that
    the rain does not cover the crop's need. A farmer choosing under a warning
    is making an informed choice; a farmer choosing under a bare score is not.
    """

    async def test_a_dry_field_warns_on_the_top_suggestion(self):
        dry = await _suggest(days=180, rain_mm=80.0, mean_et0=6.0)
        top = dry["suggestions"][0]
        assert top["warnings"], "a crop recommended into drought must say so"
        assert any("water need" in w for w in top["warnings"])

    async def test_a_wet_field_does_not_invent_a_warning(self):
        wet = await _suggest(days=180, rain_mm=1400.0, mean_et0=4.0)
        assert wet["suggestions"][0]["warnings"] == []

    async def test_the_same_crop_scores_far_lower_when_the_rain_is_not_there(self):
        dry = await _suggest(days=180, rain_mm=80.0, mean_et0=6.0)
        wet = await _suggest(days=180, rain_mm=1400.0, mean_et0=4.0)
        dry_by_code = {s["crop_code"]: s for s in dry["suggestions"]}
        wet_by_code = {s["crop_code"]: s for s in wet["suggestions"]}
        shared = set(dry_by_code) & set(wet_by_code)
        assert shared, "expected at least one crop ranked in both climates"
        for code in shared:
            assert dry_by_code[code]["score"] < wet_by_code[code]["score"]
            assert (
                dry_by_code[code]["components"]["water_fit"]
                < wet_by_code[code]["components"]["water_fit"]
            )


@pytest.mark.asyncio
class TestWhereTheWeatherCameFrom:
    """Open-Meteo rate-limits per IP, and on shared hosting that IP belongs to
    the platform -- so a node can be refused over traffic it had no part in and
    fall back to NASA POWER or MET Norway.

    Those sources are not interchangeable. Measured at one Punjab field, POWER
    returns 28% more seasonal rainfall than ERA5, which is the difference
    between "the rain covers this crop" and "it does not". The farmer is shown
    a single number here; when it rests on a weaker or mixed basis, the screen
    has to say so.
    """

    async def test_a_mixed_history_is_declared(self):
        # Half the window from each: a seasonal rainfall total genuinely built
        # from two models that disagree by tens of per cent.
        out = await _suggest(
            days=180, rain_mm=600.0, mean_et0=4.8,
            sources="nasa-power, open-meteo", fallback_days=90,
        )
        gap = next((g for g in out["gaps"] if "more than one provider" in g), None)
        assert gap is not None
        assert "approximate" in gap

    async def test_a_fallback_only_history_is_declared(self):
        out = await _suggest(days=180, rain_mm=600.0, mean_et0=4.8,
                             sources="nasa-power")
        assert any("coarser grid" in g for g in out["gaps"])

    async def test_the_usual_source_raises_nothing(self):
        # A node working normally must not nag about provenance on every visit.
        out = await _suggest(days=180, rain_mm=600.0, mean_et0=4.8)
        assert not any("provider" in g or "coarser" in g for g in out["gaps"])

    async def test_it_still_answers_on_a_fallback(self):
        # Declaring a weaker basis is not a reason to withhold the ranking --
        # that would leave the farmer exactly where they started.
        out = await _suggest(days=180, rain_mm=600.0, mean_et0=4.8,
                             sources="nasa-power")
        assert out["suggestions"]

    async def test_no_weather_at_all_does_not_claim_a_source(self):
        out = await _suggest(days=0, rain_mm=0.0, mean_et0=0.0, sources="")
        assert not any("coarser" in g for g in out["gaps"])
        assert any("No weather data" in g for g in out["gaps"])


@pytest.mark.asyncio
class TestTheWarningStaysWorthReading:
    """The archive and the forecast fall back independently.

    The common case on a rate-limited node is that the archive is fine and only
    today's forecast row came from elsewhere -- one day out of a hundred and
    eighty. Warning that the rainfall total is approximate because of that
    would be technically true and practically corrosive: a warning that fires
    when nothing is wrong is one a farmer learns to scroll past, and it is then
    gone on the day it would have mattered.
    """

    async def test_a_single_fallback_day_is_not_worth_mentioning(self):
        out = await _suggest(
            days=180, rain_mm=600.0, mean_et0=4.8,
            sources="met-no, open-meteo", fallback_days=1,
        )
        assert not any("more than one provider" in g for g in out["gaps"])

    async def test_a_material_share_is_mentioned(self):
        out = await _suggest(
            days=180, rain_mm=600.0, mean_et0=4.8,
            sources="nasa-power, open-meteo", fallback_days=60,
        )
        assert any("more than one provider" in g for g in out["gaps"])

    async def test_the_threshold_is_a_share_not_a_count(self):
        # Twelve days matters in a fortnight of history and does not in six
        # months, so the test has to be relative.
        thin = await _suggest(days=20, rain_mm=60.0, mean_et0=4.8,
                              sources="nasa-power, open-meteo", fallback_days=12)
        long = await _suggest(days=180, rain_mm=600.0, mean_et0=4.8,
                              sources="nasa-power, open-meteo", fallback_days=12)
        assert any("more than one provider" in g for g in thin["gaps"])
        assert not any("more than one provider" in g for g in long["gaps"])
