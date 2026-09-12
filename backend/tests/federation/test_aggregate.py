"""k-anonymity tests.

This is the mechanism that stops a federated advisory network becoming a
surveillance network, so it is tested adversarially rather than happily.
"""

import pytest

from app.federation.aggregate import (
    Observation,
    aggregate,
    contains_identifiers,
)


def obs(field_id, district="Ludhiana", crop="rice", indicator="ndvi_anomaly_mean", value=0.1):
    return Observation(field_id=field_id, district=district, crop_code=crop,
                       indicator=indicator, value=value)


class TestThreshold:
    def test_cell_below_k_is_suppressed_entirely(self):
        result = aggregate([obs(f"f{i}") for i in range(4)], k=5)
        assert result.cells == []
        assert result.suppressed_cells == 1

    def test_cell_at_k_is_published(self):
        result = aggregate([obs(f"f{i}") for i in range(5)], k=5)
        assert len(result.cells) == 1
        assert result.cells[0].field_count == 5

    def test_suppression_removes_rather_than_rounds(self):
        """A rounded or fuzzed small cell still leaks. It must be absent."""
        result = aggregate([obs(f"f{i}") for i in range(3)], k=5)
        payload = result.to_dict()
        assert payload["cells"] == []
        assert "Ludhiana" not in str(payload["cells"])

    def test_k_below_two_is_rejected(self):
        with pytest.raises(ValueError):
            aggregate([obs("f1")], k=1)

    def test_repeated_observations_from_one_field_do_not_reach_k(self):
        """Twenty readings from one field is one farmer, not twenty."""
        result = aggregate([obs("f1", value=0.1 * i) for i in range(20)], k=5)
        assert result.cells == []

    def test_one_field_cannot_dominate_the_mean(self):
        many = [obs("loud", value=100.0) for _ in range(50)]
        others = [obs(f"f{i}", value=0.0) for i in range(5)]
        result = aggregate(many + others, k=5)
        # Six distinct fields: one at 100, five at 0 -> mean well below 50.
        assert result.cells[0].field_count == 6
        assert result.cells[0].mean < 20


class TestComplementarySuppression:
    def test_totals_withheld_when_any_cell_suppressed(self):
        """Publishing a total beside a suppressed cell leaks it by subtraction."""
        big = [obs(f"a{i}", district="Ludhiana") for i in range(6)]
        small = [obs(f"b{i}", district="Moga") for i in range(2)]
        result = aggregate(big + small, k=5)
        assert len(result.cells) == 1
        assert result.totals_suppressed
        assert any("subtraction" in n for n in result.notes)

    def test_totals_not_suppressed_when_nothing_was_removed(self):
        result = aggregate([obs(f"f{i}") for i in range(8)], k=5)
        assert not result.totals_suppressed

    def test_empty_result_is_explained(self):
        result = aggregate([obs("f1")], k=5)
        assert any("expected result" in n for n in result.notes)


class TestNoIdentifiers:
    def test_published_cells_carry_no_field_ids(self):
        result = aggregate([obs(f"secret-field-{i}") for i in range(6)], k=5)
        rendered = str(result.to_dict())
        assert "secret-field" not in rendered

    def test_identifier_scan_catches_forbidden_keys(self):
        assert contains_identifiers({"cells": [{"field_id": "x"}]}) == ["cells.0.field_id"]
        assert contains_identifiers({"a": {"geometry": {}}}) == ["a.geometry"]

    @pytest.mark.parametrize(
        "key", ["field_id", "user_id", "phone", "geometry", "centroid", "latitude", "name"]
    )
    def test_every_forbidden_key_is_detected(self, key):
        assert contains_identifiers({key: "value"})

    def test_clean_payload_passes(self):
        result = aggregate([obs(f"f{i}") for i in range(6)], k=5)
        assert contains_identifiers(result.to_dict()) == []


class TestGrouping:
    def test_districts_are_separate_cells(self):
        a = [obs(f"a{i}", district="Ludhiana") for i in range(5)]
        b = [obs(f"b{i}", district="Moga") for i in range(5)]
        result = aggregate(a + b, k=5)
        assert {c.district for c in result.cells} == {"Ludhiana", "Moga"}

    def test_crops_are_separate_cells(self):
        rice = [obs(f"r{i}", crop="rice") for i in range(5)]
        wheat = [obs(f"w{i}", crop="wheat_spring") for i in range(5)]
        result = aggregate(rice + wheat, k=5)
        assert {c.crop_code for c in result.cells} == {"rice", "wheat_spring"}

    def test_mixing_districts_does_not_bypass_the_threshold(self):
        """Four fields in each of two districts is not eight in one."""
        a = [obs(f"a{i}", district="Ludhiana") for i in range(4)]
        b = [obs(f"b{i}", district="Moga") for i in range(4)]
        result = aggregate(a + b, k=5)
        assert result.cells == []
        assert result.suppressed_cells == 2


class TestPublishingRegion:
    """Whose district is this?

    The network the project is for is Indian states exchanging models and
    statistics. Two state nodes both declaring country "IN" are the same node
    as far as a discovery document is concerned, and district names are not
    unique across India -- there is a Bilaspur in three states. A signed cell
    labelled only "Bilaspur" cannot be placed on a map by the peer that
    receives it.
    """

    def test_the_region_travels_inside_the_signature(self):
        import inspect

        from app.api import federation

        source = inspect.getsource(federation.aggregates)
        assert '"country": settings.node_country' in source
        region_line = '"region": settings.node_region'
        assert region_line in source
        # Inside the payload dict, which is what gets sealed -- not added to
        # the envelope afterwards, where it would not be covered by the
        # signature and a peer could rewrite it.
        assert source.index(region_line) < source.index("envelope = seal(")

    def test_a_national_node_omits_the_field_rather_than_sending_an_empty_one(self):
        import inspect

        from app.api import federation

        for func in (federation.aggregates, federation.node_descriptor):
            source = inspect.getsource(func)
            assert "if settings.node_region else {}" in source

    def test_the_setting_defaults_to_empty(self):
        from app.config import Settings

        assert Settings().node_region == ""
