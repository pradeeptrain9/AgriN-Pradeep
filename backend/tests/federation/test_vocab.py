"""Vocabulary tests: interoperability fails on terms before it fails on transport."""

from app.engine.crops import CROPS
from app.federation.vocab import (
    CROP_CONCEPTS,
    INDICATORS,
    UNITS,
    crop_uri,
    describe_crop,
)


def test_every_engine_crop_has_a_concept():
    """A crop this node can advise on must be expressible to a peer."""
    missing = set(CROPS) - set(CROP_CONCEPTS)
    assert not missing, f"crops with no AGROVOC mapping: {missing}"


def test_uris_are_resolvable_agrovoc_form():
    for code in CROP_CONCEPTS:
        uri = crop_uri(code)
        assert uri and uri.startswith("http://aims.fao.org/aos/agrovoc/c_")


def test_unknown_crop_passes_through_rather_than_guessing():
    result = describe_crop("dragonfruit")
    assert result["resolved"] is False
    assert result["agrovoc_uri"] is None
    assert result["crop_code"] == "dragonfruit"


def test_wheat_variants_share_one_concept():
    """Spring and winter wheat are one crop to AGROVOC, two to the agronomy."""
    assert crop_uri("wheat_spring") == crop_uri("wheat_winter")


def test_every_indicator_declares_a_unit():
    for name, spec in INDICATORS.items():
        assert spec.get("unit"), f"{name} has no unit"
        assert spec.get("description")


def test_units_cover_the_quantities_we_publish():
    assert UNITS["irrigation_depth"] == "mm"
    assert UNITS["nutrient_rate"] == "kg/ha"
    assert UNITS["ndvi"] == "1"
