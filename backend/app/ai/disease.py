"""Crop disease diagnosis: on-device gate, escalation, and cloud vision fallback.

The on-device classifier is a small int8 CNN bundled in the app. It is fast and
works with no signal, but it is only as good as its training data, and a softmax
always sums to one: an unfamiliar leaf still produces a confident-looking answer.

So a prediction is accepted only if it clears three independent checks:

  coverage   the field's crop is one the model was actually trained on
  confidence the top probability is high enough
  decisiveness the model is not split between classes -- measured both by the
             margin over the runner-up and by the entropy of the whole
             distribution

Failing any of them routes the photo to cloud vision, which generalises far
better to crops and conditions the CNN never saw. Coverage is checked first and
is not overridable: no confidence score from a model that has never seen cotton
tells you anything about a cotton leaf.
"""

import math
from dataclasses import dataclass, field as dc_field
from enum import Enum

from app.ai.disease_taxonomy import (
    DATASET_SOURCES,
    LOW_CONFIDENCE_CROPS,
    URGENT_CLASSES,
    DiseaseClass,
    classes_for_crop,
    get_disease,
    is_crop_supported,
)

ENGINE_VERSION = "disease-1.0.0"

# Standard thresholds.
MIN_CONFIDENCE = 0.70
MIN_MARGIN = 0.15
MAX_NORMALISED_ENTROPY = 0.50

# Tightened for crops whose training data is thin (wheat).
LOW_DATA_MIN_CONFIDENCE = 0.85
LOW_DATA_MIN_MARGIN = 0.30


class Route(str, Enum):
    ON_DEVICE = "on_device"
    CLOUD_VISION = "cloud_vision"
    INCONCLUSIVE = "inconclusive"
    # A name, on a crop with no verified disease list. Distinct from
    # INCONCLUSIVE because something WAS identified and a call WAS billed --
    # collapsing the two would make the audit log read as though nothing
    # happened -- and distinct from CLOUD_VISION because nothing downstream
    # checked the answer and no treatment is attached to it.
    PROVISIONAL = "provisional"


@dataclass(frozen=True)
class Prediction:
    class_code: str
    probability: float


@dataclass
class GateDecision:
    route: Route
    accepted: bool
    top_class: str | None
    top_probability: float
    margin: float
    normalised_entropy: float
    thresholds: dict[str, float]
    reasons: list[str] = dc_field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "route": self.route.value,
            "accepted": self.accepted,
            "top_class": self.top_class,
            "top_probability": round(self.top_probability, 4),
            "margin": round(self.margin, 4),
            "normalised_entropy": round(self.normalised_entropy, 4),
            "thresholds": self.thresholds,
            "reasons": self.reasons,
        }


def normalised_entropy(probabilities: list[float], n_classes: int | None = None) -> float:
    """Shannon entropy scaled to [0, 1]. 0 is certain, 1 is uniformly undecided.

    `n_classes` must be the number of classes the MODEL has for this crop, not
    the number of probabilities supplied. Clients send only the top few
    predictions to save bandwidth, and normalising by the supplied count would
    make the threshold depend on how many the client happened to send -- a
    peaked 0.78/0.12/0.10 scores 0.62 against three classes but 0.27 against the
    eleven the rice model actually has.

    Any probability mass missing from the supplied list is treated as a single
    residual bucket, so truncation cannot understate uncertainty either.
    """
    values = [p for p in probabilities if p > 0]
    if not values:
        return 0.0

    total = sum(values)
    residual = max(0.0, 1.0 - total)
    if residual > 1e-9:
        values = values + [residual]

    if len(values) <= 1:
        return 0.0

    denominator = max(n_classes or len(values), len(values))
    if denominator <= 1:
        return 0.0

    entropy = -sum(p * math.log(p) for p in values if p > 0)
    return min(1.0, entropy / math.log(denominator))


def gate(predictions: list[Prediction], *, crop_code: str) -> GateDecision:
    """Decide whether the on-device prediction can be trusted."""
    ranked = sorted(predictions, key=lambda p: p.probability, reverse=True)
    probabilities = [p.probability for p in ranked]
    top = ranked[0] if ranked else None
    runner_up = ranked[1].probability if len(ranked) > 1 else 0.0

    top_prob = top.probability if top else 0.0
    margin = top_prob - runner_up
    n_classes = len(classes_for_crop(crop_code)) or len(probabilities)
    entropy = normalised_entropy(probabilities, n_classes)

    low_data = crop_code in LOW_CONFIDENCE_CROPS
    min_conf = LOW_DATA_MIN_CONFIDENCE if low_data else MIN_CONFIDENCE
    min_margin = LOW_DATA_MIN_MARGIN if low_data else MIN_MARGIN
    thresholds = {
        "min_confidence": min_conf,
        "min_margin": min_margin,
        "max_normalised_entropy": MAX_NORMALISED_ENTROPY,
    }

    reasons: list[str] = []

    # Coverage is checked first and cannot be overridden by a high score.
    if not is_crop_supported(crop_code):
        reasons.append(
            f"The on-device model was not trained on {crop_code}, so its output "
            "carries no information for this crop."
        )
        return GateDecision(
            route=Route.CLOUD_VISION,
            accepted=False,
            top_class=None,
            top_probability=top_prob,
            margin=margin,
            normalised_entropy=entropy,
            thresholds=thresholds,
            reasons=reasons,
        )

    # A prediction naming another crop's disease cannot be about this crop, and
    # must not be weighed on confidence. The app filters by crop prefix before
    # sending, so this should never fire -- but the gate is the authority, and a
    # stale APK or a replayed queue item reaches it directly. Accepting one of
    # these would tell a farmer their maize has a rice disease.
    allowed = {d.code for d in classes_for_crop(crop_code)}
    foreign = [p for p in ranked if p.class_code not in allowed]
    if foreign:
        reasons.append(
            f"The on-device prediction named {foreign[0].class_code}, which is "
            f"not a disease of {crop_code}, so it cannot be about this plant."
        )
        return GateDecision(
            route=Route.CLOUD_VISION, accepted=False, top_class=None,
            top_probability=top_prob, margin=margin, normalised_entropy=entropy,
            thresholds=thresholds, reasons=reasons,
        )

    if not ranked:
        reasons.append("No on-device prediction was supplied.")
        return GateDecision(
            route=Route.CLOUD_VISION, accepted=False, top_class=None,
            top_probability=0.0, margin=0.0, normalised_entropy=0.0,
            thresholds=thresholds, reasons=reasons,
        )

    if low_data:
        reasons.append(
            f"Training data for this crop is limited ({DATASET_SOURCES.get(crop_code, 'unknown')}), "
            "so a higher confidence is required."
        )

    if top_prob < min_conf:
        reasons.append(f"Confidence {top_prob:.0%} is below the {min_conf:.0%} threshold.")
    if margin < min_margin:
        reasons.append(
            f"The model is split between classes (margin {margin:.0%} over the runner-up)."
        )
    if entropy > MAX_NORMALISED_ENTROPY:
        reasons.append(f"The prediction is spread across many classes (entropy {entropy:.2f}).")

    failed = (
        top_prob < min_conf or margin < min_margin or entropy > MAX_NORMALISED_ENTROPY
    )
    if failed:
        return GateDecision(
            route=Route.CLOUD_VISION, accepted=False, top_class=top.class_code,
            top_probability=top_prob, margin=margin, normalised_entropy=entropy,
            thresholds=thresholds, reasons=reasons,
        )

    reasons.append("On-device prediction is confident and decisive.")
    return GateDecision(
        route=Route.ON_DEVICE, accepted=True, top_class=top.class_code,
        top_probability=top_prob, margin=margin, normalised_entropy=entropy,
        thresholds=thresholds, reasons=reasons,
    )


@dataclass
class Diagnosis:
    engine_version: str
    resolved_by: str                  # on_device | cloud_vision | inconclusive
    disease_code: str | None
    label: str | None
    crop_code: str
    confidence: float
    is_healthy: bool
    pathogen_type: str | None
    urgent: bool
    needs_expert_review: bool
    ipm_actions: list[str]
    chemical_options: list[dict]
    gate: dict
    notes: list[str] = dc_field(default_factory=list)
    # A name for a crop this node has no verified list for. Deliberately NOT a
    # disease_code: it keys into no IPM action and no pesticide row, and the
    # client must render it as unconfirmed with no treatment beside it.
    provisional_name: str | None = None

    def to_dict(self) -> dict:
        return {
            "engine_version": self.engine_version,
            "resolved_by": self.resolved_by,
            "disease_code": self.disease_code,
            "label": self.label,
            "crop_code": self.crop_code,
            "confidence": round(self.confidence, 4),
            "is_healthy": self.is_healthy,
            "pathogen_type": self.pathogen_type,
            "urgent": self.urgent,
            "needs_expert_review": self.needs_expert_review,
            "ipm_actions": self.ipm_actions,
            "chemical_options": self.chemical_options,
            "gate": self.gate,
            "notes": self.notes,
            "provisional_name": self.provisional_name,
        }


def build_diagnosis(
    *,
    disease: DiseaseClass | None,
    crop_code: str,
    confidence: float,
    resolved_by: str,
    decision: GateDecision,
    chemical_options: list[dict] | None = None,
    extra_notes: list[str] | None = None,
    provisional_name: str | None = None,
) -> Diagnosis:
    notes = list(extra_notes or [])
    chemical_options = chemical_options or []

    if disease is None and provisional_name:
        # Named, but on a crop with no verified list. Everything that could
        # become an instruction stays empty: no code, no IPM action, no
        # chemical. The caveats are already in extra_notes, written by the
        # module that produced the name.
        return Diagnosis(
            engine_version=ENGINE_VERSION,
            resolved_by=resolved_by,
            disease_code=None,
            label=None,
            crop_code=crop_code,
            confidence=confidence,
            is_healthy=False,
            pathogen_type=None,
            urgent=False,
            needs_expert_review=True,
            ipm_actions=[],
            chemical_options=[],
            gate=decision.to_dict(),
            notes=notes,
            provisional_name=provisional_name,
        )

    if disease is None:
        notes.append(
            "The photo could not be identified with enough confidence to name a "
            "disease. Show the plant to your extension officer."
        )
        return Diagnosis(
            engine_version=ENGINE_VERSION,
            resolved_by=Route.INCONCLUSIVE.value,
            disease_code=None,
            label=None,
            crop_code=crop_code,
            confidence=confidence,
            is_healthy=False,
            pathogen_type=None,
            urgent=False,
            needs_expert_review=True,
            ipm_actions=[],
            chemical_options=[],
            gate=decision.to_dict(),
            notes=notes,
        )

    urgent = disease.code in URGENT_CLASSES
    # Anything not resolved on device, anything urgent, and anything bacterial or
    # viral (where sprays rarely help and misdiagnosis is costly) gets a human.
    needs_review = (
        urgent
        or resolved_by != Route.ON_DEVICE.value
        or disease.pathogen_type in ("bacterial", "viral")
    )

    if disease.pathogen_type in ("bacterial", "viral") and chemical_options:
        chemical_options = []
        notes.append(
            "Fungicides do not control bacterial or viral disease, so no spray is "
            "offered here."
        )
    if not chemical_options and not disease.is_healthy:
        notes.append(
            "No chemical treatment is shown. Either none is verified for your "
            "country and crop in this node, or none is appropriate. The practices "
            "above are the first line of control."
        )
    if urgent:
        notes.append("This disease can move very fast. Act today.")

    return Diagnosis(
        engine_version=ENGINE_VERSION,
        resolved_by=resolved_by,
        disease_code=disease.code,
        label=disease.label_en,
        crop_code=crop_code,
        confidence=confidence,
        is_healthy=disease.is_healthy,
        pathogen_type=disease.pathogen_type,
        urgent=urgent,
        needs_expert_review=needs_review,
        ipm_actions=list(disease.ipm_actions),
        chemical_options=chemical_options,
        gate=decision.to_dict(),
        notes=notes,
    )


def diagnose_on_device(
    predictions: list[Prediction], *, crop_code: str, chemical_lookup=None
) -> tuple[GateDecision, Diagnosis | None]:
    """Run the gate. Returns (decision, diagnosis-or-None).

    A None diagnosis means the caller must escalate to cloud vision.
    """
    decision = gate(predictions, crop_code=crop_code)
    if not decision.accepted or decision.top_class is None:
        return decision, None

    disease = get_disease(decision.top_class)
    options = chemical_lookup(disease) if chemical_lookup else []
    diagnosis = build_diagnosis(
        disease=disease,
        crop_code=crop_code,
        confidence=decision.top_probability,
        resolved_by=Route.ON_DEVICE.value,
        decision=decision,
        chemical_options=options,
    )
    return decision, diagnosis
