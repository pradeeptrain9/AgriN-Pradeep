"""Gate tests.

The failure this guards against: a softmax always sums to one, so a model that
has never seen a cotton leaf still returns a confident-looking answer for one.
"""

import pytest

from app.ai.disease import (
    LOW_DATA_MIN_CONFIDENCE,
    MIN_CONFIDENCE,
    Prediction,
    Route,
    build_diagnosis,
    diagnose_on_device,
    gate,
    normalised_entropy,
)
from app.ai.disease_taxonomy import get_disease, is_crop_supported


def preds(*pairs):
    return [Prediction(c, p) for c, p in pairs]


CONFIDENT = preds(
    ("rice__blast", 0.94), ("rice__brown_spot", 0.03), ("rice__normal", 0.03)
)
SPLIT = preds(
    ("rice__blast", 0.44), ("rice__brown_spot", 0.42), ("rice__normal", 0.14)
)
DIFFUSE = preds(
    ("rice__blast", 0.22), ("rice__brown_spot", 0.20), ("rice__normal", 0.20),
    ("rice__tungro", 0.19), ("rice__hispa", 0.19),
)


class TestEntropy:
    def test_certain_prediction_has_zero_entropy(self):
        assert normalised_entropy([1.0, 0.0, 0.0]) == 0.0

    def test_uniform_prediction_has_entropy_one(self):
        assert normalised_entropy([0.25] * 4) == pytest.approx(1.0)

    def test_normalisation_is_independent_of_class_count(self):
        assert normalised_entropy([0.5, 0.5]) == pytest.approx(
            normalised_entropy([0.25] * 4)
        )

    def test_single_class_is_zero(self):
        assert normalised_entropy([1.0]) == 0.0


class TestCoverageGate:
    """Coverage is checked first and cannot be overridden by a high score."""

    def test_unsupported_crop_is_never_accepted(self):
        assert not is_crop_supported("cotton")
        # A 99% confident, decisive prediction -- and still refused.
        decision = gate(
            preds(("rice__blast", 0.99), ("rice__normal", 0.01)), crop_code="cotton"
        )
        assert decision.route is Route.CLAUDE_VISION
        assert decision.accepted is False
        assert decision.top_class is None
        assert any("not trained on cotton" in r for r in decision.reasons)

    @pytest.mark.parametrize(
        "crop", ["cotton", "chickpea", "groundnut", "sorghum", "pearl_millet",
                 "mustard", "sunflower", "sugarcane", "soybean"]
    )
    def test_every_uncovered_registry_crop_is_refused(self, crop):
        decision = gate(preds(("rice__blast", 0.99)), crop_code=crop)
        assert decision.route is Route.CLAUDE_VISION

    def test_supported_crops_reach_the_confidence_checks(self):
        for crop in ("rice", "maize", "potato"):
            assert is_crop_supported(crop)


class TestConfidenceGate:
    def test_confident_decisive_prediction_accepted(self):
        decision = gate(CONFIDENT, crop_code="rice")
        assert decision.route is Route.ON_DEVICE
        assert decision.accepted
        assert decision.top_class == "rice__blast"

    def test_low_confidence_escalates(self):
        decision = gate(
            preds(("rice__blast", 0.55), ("rice__normal", 0.25), ("rice__hispa", 0.20)),
            crop_code="rice",
        )
        assert decision.route is Route.CLAUDE_VISION
        assert any("below the" in r for r in decision.reasons)

    def test_split_decision_escalates_despite_being_top_class(self):
        """0.44 vs 0.42 is a coin flip, not a diagnosis."""
        decision = gate(SPLIT, crop_code="rice")
        assert decision.route is Route.CLAUDE_VISION
        assert any("split between classes" in r for r in decision.reasons)

    def test_diffuse_distribution_escalates_on_entropy(self):
        decision = gate(DIFFUSE, crop_code="rice")
        assert decision.route is Route.CLAUDE_VISION
        assert any("spread across many classes" in r for r in decision.reasons)

    def test_empty_prediction_escalates(self):
        decision = gate([], crop_code="rice")
        assert decision.route is Route.CLAUDE_VISION

    def test_thresholds_are_reported_for_audit(self):
        decision = gate(CONFIDENT, crop_code="rice")
        assert decision.thresholds["min_confidence"] == MIN_CONFIDENCE


class TestThinDataCrops:
    """Wheat has ~2.4k training images against rice's 16k. Demand more."""

    def test_same_confidence_accepted_for_rice_but_refused_for_wheat(self):
        """0.78 clears rice's 0.70 bar but not wheat's 0.85."""
        rice = gate(
            preds(("rice__blast", 0.78), ("rice__brown_spot", 0.12), ("rice__normal", 0.10)),
            crop_code="rice",
        )
        wheat = gate(
            preds(("wheat__leaf_rust", 0.78), ("wheat__stripe_rust", 0.12),
                  ("wheat__healthy", 0.10)),
            crop_code="wheat_spring",
        )
        assert rice.route is Route.ON_DEVICE
        assert wheat.route is Route.CLAUDE_VISION
        assert wheat.thresholds["min_confidence"] == LOW_DATA_MIN_CONFIDENCE
        assert rice.thresholds["min_confidence"] == MIN_CONFIDENCE

    def test_wheat_accepted_when_very_confident(self):
        strong = preds(
            ("wheat__leaf_rust", 0.95), ("wheat__stripe_rust", 0.03),
            ("wheat__healthy", 0.02),
        )
        assert gate(strong, crop_code="wheat_spring").route is Route.ON_DEVICE

    def test_thin_data_is_disclosed_in_reasons(self):
        decision = gate(
            preds(("wheat__leaf_rust", 0.95), ("wheat__healthy", 0.05)),
            crop_code="wheat_spring",
        )
        assert any("Training data for this crop is limited" in r for r in decision.reasons)


class TestDiagnosisAssembly:
    def test_accepted_prediction_produces_a_diagnosis(self):
        decision, diagnosis = diagnose_on_device(CONFIDENT, crop_code="rice")
        assert diagnosis is not None
        assert diagnosis.disease_code == "rice__blast"
        assert diagnosis.resolved_by == "on_device"
        assert diagnosis.ipm_actions

    def test_rejected_prediction_returns_no_diagnosis(self):
        decision, diagnosis = diagnose_on_device(SPLIT, crop_code="rice")
        assert diagnosis is None
        assert decision.route is Route.CLAUDE_VISION

    def test_urgent_disease_always_flags_expert_review(self):
        decision = gate(
            preds(("potato__late_blight", 0.97), ("potato__healthy", 0.03)),
            crop_code="potato",
        )
        diagnosis = build_diagnosis(
            disease=get_disease("potato__late_blight"), crop_code="potato",
            confidence=0.97, resolved_by="on_device", decision=decision,
        )
        assert diagnosis.urgent
        assert diagnosis.needs_expert_review
        assert any("Act today" in n for n in diagnosis.notes)

    def test_bacterial_disease_never_offers_a_fungicide(self):
        decision = gate(
            preds(("rice__bacterial_leaf_blight", 0.93), ("rice__normal", 0.07)),
            crop_code="rice",
        )
        diagnosis = build_diagnosis(
            disease=get_disease("rice__bacterial_leaf_blight"), crop_code="rice",
            confidence=0.93, resolved_by="on_device", decision=decision,
            chemical_options=[{"active_ingredient": "tricyclazole"}],
        )
        assert diagnosis.chemical_options == []
        assert any("do not control bacterial" in n for n in diagnosis.notes)
        assert diagnosis.needs_expert_review

    def test_claude_resolved_diagnosis_always_needs_review(self):
        decision = gate(SPLIT, crop_code="rice")
        diagnosis = build_diagnosis(
            disease=get_disease("rice__brown_spot"), crop_code="rice",
            confidence=0.6, resolved_by="claude_vision", decision=decision,
        )
        assert diagnosis.needs_expert_review

    def test_inconclusive_diagnosis_names_no_disease(self):
        decision = gate(DIFFUSE, crop_code="rice")
        diagnosis = build_diagnosis(
            disease=None, crop_code="rice", confidence=0.22,
            resolved_by="inconclusive", decision=decision,
        )
        assert diagnosis.disease_code is None
        assert diagnosis.needs_expert_review
        assert any("extension officer" in n for n in diagnosis.notes)

    def test_healthy_leaf_does_not_nag_about_chemicals(self):
        decision = gate(
            preds(("rice__normal", 0.96), ("rice__blast", 0.04)), crop_code="rice"
        )
        diagnosis = build_diagnosis(
            disease=get_disease("rice__normal"), crop_code="rice",
            confidence=0.96, resolved_by="on_device", decision=decision,
        )
        assert diagnosis.is_healthy
        assert not any("No chemical treatment" in n for n in diagnosis.notes)

    def test_missing_chemicals_is_explained_not_silent(self):
        decision = gate(CONFIDENT, crop_code="rice")
        diagnosis = build_diagnosis(
            disease=get_disease("rice__blast"), crop_code="rice",
            confidence=0.94, resolved_by="on_device", decision=decision,
            chemical_options=[],
        )
        assert any("No chemical treatment is shown" in n for n in diagnosis.notes)

    def test_serialises(self):
        _, diagnosis = diagnose_on_device(CONFIDENT, crop_code="rice")
        payload = diagnosis.to_dict()
        assert payload["disease_code"] == "rice__blast"
        assert payload["gate"]["route"] == "on_device"


class TestEntropyNormalisationBug:
    """Regression: entropy must not depend on how many predictions the client sends.

    Clients truncate to top-k to save bandwidth on a slow connection. Normalising
    by the supplied count made a peaked distribution look uncertain and wrongly
    escalated confident on-device answers.
    """

    def test_truncated_top_k_matches_full_distribution(self):
        full = preds(
            ("rice__blast", 0.78), ("rice__brown_spot", 0.12), ("rice__normal", 0.10),
            ("rice__tungro", 0.0), ("rice__hispa", 0.0), ("rice__leaf_roller", 0.0),
        )
        top3 = preds(
            ("rice__blast", 0.78), ("rice__brown_spot", 0.12), ("rice__normal", 0.10)
        )
        assert gate(full, crop_code="rice").route is gate(top3, crop_code="rice").route

    def test_peaked_three_class_prediction_is_accepted_for_rice(self):
        decision = gate(
            preds(("rice__blast", 0.78), ("rice__brown_spot", 0.12), ("rice__normal", 0.10)),
            crop_code="rice",
        )
        assert decision.route is Route.ON_DEVICE
        assert decision.normalised_entropy < 0.5

    def test_missing_probability_mass_counts_as_uncertainty(self):
        """Top-1 only, summing to 0.4: the other 0.6 is unexplained."""
        truncated = normalised_entropy([0.4], n_classes=11)
        assert truncated > 0.0

    def test_normalisation_uses_crop_class_count(self):
        probs = [0.78, 0.12, 0.10]
        # Rice has more classes than wheat, so the same spread looks more certain.
        assert normalised_entropy(probs, 11) < normalised_entropy(probs, 6)

    def test_entropy_never_exceeds_one(self):
        assert normalised_entropy([0.2] * 5, n_classes=2) <= 1.0


class TestAuditLogging:
    """The stored on-device label must be the top prediction, not the first one
    the client happened to send. The client posts the full softmax in label
    order, so taking element zero records an arbitrary class."""

    def test_top_prediction_is_the_max_not_the_first(self):
        posted = preds(
            ("rice__bacterial_leaf_blight", 0.044),   # alphabetically first
            ("rice__brown_spot", 0.891),              # actually the prediction
            ("rice__normal", 0.065),
        )
        top = max(posted, key=lambda p: p.probability)
        assert top.class_code == "rice__brown_spot"
        assert posted[0].class_code != top.class_code

    def test_gate_agrees_with_the_max(self):
        posted = preds(
            ("rice__bacterial_leaf_blight", 0.044),
            ("rice__brown_spot", 0.891),
            ("rice__normal", 0.065),
        )
        decision = gate(posted, crop_code="rice")
        assert decision.top_class == "rice__brown_spot"
        assert decision.route is Route.ON_DEVICE
