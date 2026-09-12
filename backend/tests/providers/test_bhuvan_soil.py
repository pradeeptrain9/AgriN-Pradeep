"""India's own soil map, for the country SoilGrids masks out.

SoilGrids v2 returns null for every property over India, so the chain fell to
its last resort and assumed loam for every field in the country. That is not a
neutral default. Texture sets field capacity and wilting point, so it sets the
size of the water reservoir the irrigation engine divides into: FAO-56 Table 19
gives loam 130 mm of available water per metre and loamy sand 80. Assuming loam
on sandy land overstates the store by more than half and tells a farmer they
have water in the profile that is not there.

Queried at a real Punjab field, Bhuvan says coarse texture -- loamy sand -- and
the fallback had been saying loam.
"""

import pytest

from app.providers import bhuvan, soil
from app.providers.soil import SoilSource, resolve_soil


class TestReadingBhuvansClasses:
    @pytest.mark.parametrize("descr,expected", [
        ("Coarse Texture", "loamy sand"),
        ("Medium Texture", "loam"),
        ("Fine Texture", "clay"),
    ])
    def test_each_class_maps_to_a_usda_texture(self, descr, expected):
        assert bhuvan.texture_from_description(descr).name == expected

    def test_moderately_coarse_is_not_read_as_coarse(self):
        # Longest match first. Reading a sandy loam as loamy sand understates
        # its available water by about a third, which is the direction that
        # hurts -- it would under-water the crop.
        coarse = bhuvan.texture_from_description("Coarse Texture")
        moderate = bhuvan.texture_from_description("Moderately Coarse Texture")
        assert moderate.name != coarse.name
        assert moderate.theta_fc > coarse.theta_fc

    def test_moderately_fine_is_not_read_as_fine(self):
        fine = bhuvan.texture_from_description("Fine Texture")
        moderate = bhuvan.texture_from_description("Moderately Fine Texture")
        assert moderate.name != fine.name

    @pytest.mark.parametrize("descr", ["", "   ", "Rock outcrop", "Water body", None])
    def test_an_unrecognised_class_returns_nothing_rather_than_guessing(self, descr):
        # Habitation, rock and water all appear in national soil maps. None of
        # them has a water-holding capacity worth feeding a crop model.
        assert bhuvan.texture_from_description(descr) is None

    def test_the_mapping_is_case_insensitive(self):
        assert bhuvan.texture_from_description("COARSE TEXTURE") is not None


@pytest.mark.asyncio
class TestWhereBhuvanSitsInTheChain:
    @pytest.fixture(autouse=True)
    def _no_soilgrids(self, monkeypatch):
        # Masked over India in reality; made explicit here.
        async def masked(*_args, **_kwargs):
            return None
        monkeypatch.setattr(soil, "fetch_soilgrids", masked)

    @pytest.fixture
    def bhuvan_says(self, monkeypatch):
        def answer(value):
            async def fetch(*_args, **_kwargs):
                return value
            monkeypatch.setattr(soil.bhuvan, "fetch_texture", fetch)
        return answer

    async def test_it_is_used_when_soilgrids_has_nothing(self, bhuvan_says):
        from app.engine.soil_texture import classify_texture

        bhuvan_says((classify_texture(82, 12, 6), "Coarse Texture"))
        profile = await resolve_soil(lat=30.9, lon=75.8)
        assert profile.source is SoilSource.BHUVAN
        assert profile.texture.name == "loamy sand"

    async def test_a_farmers_own_card_still_wins(self, bhuvan_says):
        # Laboratory analysis of this field beats a national map of the region,
        # and must not be overtaken by adding a source below it.
        from app.engine.soil_texture import classify_texture

        bhuvan_says((classify_texture(82, 12, 6), "Coarse Texture"))
        profile = await resolve_soil(
            lat=30.9, lon=75.8,
            card={"ph": 7.4, "organic_carbon_pct": 0.5,
                  "available_n_kg_ha": 250, "available_p_kg_ha": 20,
                  "available_k_kg_ha": 200},
        )
        assert profile.source is SoilSource.SOIL_HEALTH_CARD

    async def test_a_feel_test_still_wins(self, bhuvan_says):
        # The farmer had their hands in this soil; the map did not.
        from app.engine.soil_texture import classify_texture

        bhuvan_says((classify_texture(82, 12, 6), "Coarse Texture"))
        profile = await resolve_soil(lat=30.9, lon=75.8, feel_test="loam")
        assert profile.source is SoilSource.FEEL_TEST

    async def test_an_outage_falls_through_rather_than_failing(self, monkeypatch):
        async def down(*_args, **_kwargs):
            raise soil.bhuvan.BhuvanUnavailable("Bhuvan unreachable")

        monkeypatch.setattr(soil.bhuvan, "fetch_texture", down)
        profile = await resolve_soil(lat=30.9, lon=75.8)
        assert profile.source is SoilSource.FALLBACK

    async def test_outside_india_falls_back(self, monkeypatch):
        async def nothing_here(*_args, **_kwargs):
            return None

        monkeypatch.setattr(soil.bhuvan, "fetch_texture", nothing_here)
        profile = await resolve_soil(lat=-12.5, lon=-55.7)
        assert profile.source is SoilSource.FALLBACK


class TestItDoesNotOverstateItself:
    def test_confidence_is_not_claimed_to_be_medium(self):
        # The polygon that answers can span several states. It is better than
        # a bare assumption and it is not a measurement of anyone's field.
        assert soil.CONFIDENCE[SoilSource.BHUVAN] == "low"

    def test_a_card_is_still_the_only_high_confidence_source(self):
        high = [s for s, c in soil.CONFIDENCE.items() if c == "high"]
        assert high == [SoilSource.SOIL_HEALTH_CARD]
