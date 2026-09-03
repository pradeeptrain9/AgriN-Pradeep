"""Chemical allowlist tests.

Naming a pesticide, dose and pre-harvest interval is the most dangerous output
this system produces. The rule is that nothing unverified is ever served, and a
language model can never introduce a product.
"""

from app.engine.treatments import ChemicalOption, filter_to_allowlist

VERIFIED = [
    ChemicalOption(
        active_ingredient="Tricyclazole",
        product_name="Beam",
        dose="0.6 g/litre of water",
        phi_days=21,
        country="IN",
        notes="Verified against the CIB&RC register.",
    )
]


class TestAllowlistFiltering:
    def test_known_ingredient_is_kept(self):
        kept, rejected = filter_to_allowlist(
            [{"active_ingredient": "Tricyclazole"}], VERIFIED
        )
        assert len(kept) == 1
        assert rejected == []

    def test_unknown_ingredient_is_dropped(self):
        kept, rejected = filter_to_allowlist(
            [{"active_ingredient": "Carbendazim"}], VERIFIED
        )
        assert kept == []
        assert rejected == ["Carbendazim"]

    def test_matching_is_case_insensitive(self):
        kept, _ = filter_to_allowlist([{"active_ingredient": "  tricyclazole "}], VERIFIED)
        assert len(kept) == 1

    def test_model_supplied_dose_is_discarded(self):
        """Even for a matching ingredient, figures come from the verified row."""
        kept, _ = filter_to_allowlist(
            [{
                "active_ingredient": "Tricyclazole",
                "dose": "5 litres per acre",       # invented
                "pre_harvest_interval_days": 0,    # dangerous
            }],
            VERIFIED,
        )
        assert kept[0]["dose"] == "0.6 g/litre of water"
        assert kept[0]["pre_harvest_interval_days"] == 21

    def test_empty_allowlist_rejects_everything(self):
        """A fresh node has no verified rows and must offer no chemicals."""
        kept, rejected = filter_to_allowlist(
            [{"active_ingredient": "Tricyclazole"}, {"active_ingredient": "Mancozeb"}], []
        )
        assert kept == []
        assert len(rejected) == 2

    def test_unnamed_candidate_is_rejected_not_crashed(self):
        kept, rejected = filter_to_allowlist([{}], VERIFIED)
        assert kept == []
        assert rejected == ["(unnamed)"]


class TestSafetyMessaging:
    def test_every_option_carries_a_label_warning(self):
        payload = VERIFIED[0].to_dict()
        assert "Read the product label" in payload["warning"]

    def test_pre_harvest_interval_is_stated_in_the_warning(self):
        payload = VERIFIED[0].to_dict()
        assert "21 days" in payload["warning"]

    def test_serialised_keys_are_explicit(self):
        payload = VERIFIED[0].to_dict()
        assert payload["pre_harvest_interval_days"] == 21
        assert payload["active_ingredient"] == "Tricyclazole"
