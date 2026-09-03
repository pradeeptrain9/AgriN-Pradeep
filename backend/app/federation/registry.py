"""Model registry: how nodes share trained models instead of raw data.

The cooperation this network is for happens here. A node that has trained a rice
disease classifier on 16,000 local field photos can publish the weights and the
model card; a peer node pulls it, evaluates it against its own held-out data, and
either adopts it or does not. No farmer photograph ever crosses a border.

A model card is mandatory and is served alongside every artifact, because a
weights file with no provenance is unusable and unsafe: a peer needs to know what
crops it covers, what data it saw, how it was evaluated, and what it must not be
used for. Cards follow the same shape `training/train_disease.py` writes.
"""

import hashlib
import pathlib
from dataclasses import dataclass

REQUIRED_CARD_FIELDS = (
    "name",
    "task",
    "architecture",
    "classes",
    "training_data",
    "evaluation",
    "limitations",
    "intended_use",
    "not_intended_for",
    "license",
)


@dataclass(frozen=True)
class RegisteredModel:
    model_id: str
    version: str
    card: dict
    artifact_path: pathlib.Path | None
    sha256: str | None
    size_bytes: int | None

    def to_dict(self, *, include_card: bool = True) -> dict:
        payload = {
            "model_id": self.model_id,
            "version": self.version,
            "artifact": {
                "available": self.artifact_path is not None,
                "sha256": self.sha256,
                "size_bytes": self.size_bytes,
                "format": "tflite",
            },
        }
        if include_card:
            payload["card"] = self.card
        return payload


class InvalidModelCard(ValueError):
    pass


def validate_card(card: dict) -> list[str]:
    """A card missing provenance is rejected rather than published incomplete."""
    problems = [f"missing required field: {f}" for f in REQUIRED_CARD_FIELDS if f not in card]

    evaluation = card.get("evaluation") or {}
    if "reported_accuracy" not in evaluation:
        problems.append(
            "evaluation.reported_accuracy is required: a peer cannot judge a model "
            "whose accuracy is unstated"
        )
    # The in-domain figure alone is the number that makes lab-trained plant
    # disease models look far better than they are.
    if "field_accuracy_plantdoc" in evaluation and "in_domain_accuracy" not in evaluation:
        problems.append(
            "evaluation must report in_domain_accuracy alongside field accuracy so "
            "the generalisation gap is visible"
        )
    if not card.get("limitations"):
        problems.append("limitations must be non-empty: every model has them")
    return problems


def sha256_of(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_model(card_path: pathlib.Path) -> RegisteredModel:
    """Load a model card and its sibling artifact, if present."""
    import json

    card = json.loads(card_path.read_text())
    problems = validate_card(card)
    if problems:
        raise InvalidModelCard("; ".join(problems))

    artifact = card_path.with_suffix("")
    if artifact.suffix != ".tflite":
        artifact = card_path.parent / f"{card.get('name')}.tflite"

    if artifact.exists():
        return RegisteredModel(
            model_id=card["name"], version=str(card.get("version", "1")), card=card,
            artifact_path=artifact, sha256=sha256_of(artifact),
            size_bytes=artifact.stat().st_size,
        )
    # A card without weights is still worth publishing: peers can see what this
    # node is training and what it measured before deciding to wait for it.
    return RegisteredModel(
        model_id=card["name"], version=str(card.get("version", "1")), card=card,
        artifact_path=None, sha256=None, size_bytes=None,
    )


def discover(models_dir: pathlib.Path) -> list[RegisteredModel]:
    """Every valid model card in a directory. Invalid cards are skipped, not served."""
    if not models_dir.exists():
        return []
    found: list[RegisteredModel] = []
    for card_path in sorted(models_dir.glob("*.model_card.json")):
        try:
            found.append(load_model(card_path))
        except (InvalidModelCard, ValueError):
            continue
    return found
