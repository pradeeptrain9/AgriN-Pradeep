"""Model registry tests. A weights file without provenance is unusable and
unsafe for a peer, so cards are mandatory and validated."""

import json

import pytest

from app.federation.registry import (
    InvalidModelCard,
    discover,
    load_model,
    sha256_of,
    validate_card,
)

GOOD_CARD = {
    "name": "disease_v1",
    "task": "crop leaf disease classification",
    "architecture": "MobileNetV3-Small, int8 quantised",
    "classes": ["rice__blast", "rice__normal"],
    "training_data": {"rice": "Paddy Doctor (16,225 field images)"},
    "evaluation": {"in_domain_accuracy": 0.98, "field_accuracy_plantdoc": 0.64,
                   "reported_accuracy": 0.64},
    "limitations": ["Covers rice, maize, potato and wheat only."],
    "intended_use": "First-pass screening with a confidence gate.",
    "not_intended_for": "Unsupervised treatment decisions.",
    "license": "Apache-2.0",
}


class TestCardValidation:
    def test_good_card_passes(self):
        assert validate_card(GOOD_CARD) == []

    @pytest.mark.parametrize("missing", list(GOOD_CARD))
    def test_every_required_field_is_enforced(self, missing):
        card = {k: v for k, v in GOOD_CARD.items() if k != missing}
        assert validate_card(card), f"{missing} was not required"

    def test_accuracy_must_be_reported(self):
        card = {**GOOD_CARD, "evaluation": {"in_domain_accuracy": 0.99}}
        problems = validate_card(card)
        assert any("reported_accuracy" in p for p in problems)

    def test_field_accuracy_requires_in_domain_for_comparison(self):
        """The gap between the two is the interesting number; hiding one hides it."""
        card = {**GOOD_CARD,
                "evaluation": {"field_accuracy_plantdoc": 0.64, "reported_accuracy": 0.64}}
        problems = validate_card(card)
        assert any("generalisation gap" in p for p in problems)

    def test_empty_limitations_rejected(self):
        card = {**GOOD_CARD, "limitations": []}
        assert any("limitations" in p for p in validate_card(card))


class TestLoading:
    def test_card_without_weights_still_loads(self, tmp_path):
        """Publishing a card before the weights exist tells peers what is coming."""
        path = tmp_path / "disease_v1.model_card.json"
        path.write_text(json.dumps(GOOD_CARD))
        model = load_model(path)
        assert model.model_id == "disease_v1"
        assert model.artifact_path is None
        assert model.to_dict()["artifact"]["available"] is False

    def test_weights_are_hashed(self, tmp_path):
        (tmp_path / "disease_v1.tflite").write_bytes(b"fake weights")
        path = tmp_path / "disease_v1.model_card.json"
        path.write_text(json.dumps(GOOD_CARD))
        model = load_model(path)
        assert model.artifact_path is not None
        assert model.sha256 == sha256_of(tmp_path / "disease_v1.tflite")
        assert model.size_bytes == len(b"fake weights")

    def test_invalid_card_raises(self, tmp_path):
        path = tmp_path / "bad.model_card.json"
        path.write_text(json.dumps({"name": "bad"}))
        with pytest.raises(InvalidModelCard):
            load_model(path)

    def test_discover_skips_invalid_cards_rather_than_serving_them(self, tmp_path):
        (tmp_path / "good.model_card.json").write_text(json.dumps(GOOD_CARD))
        (tmp_path / "bad.model_card.json").write_text(json.dumps({"name": "bad"}))
        found = discover(tmp_path)
        assert [m.model_id for m in found] == ["disease_v1"]

    def test_discover_on_missing_directory_is_empty_not_an_error(self, tmp_path):
        assert discover(tmp_path / "nope") == []
