"""Training-methodology tests.

These guard the two decisions that keep the reported accuracy honest: splitting
by source rather than randomly, and reporting the field number.
"""

import json
import pathlib

from training.train_disease import TrainConfig, source_split, write_model_card


def paths(*names):
    return [pathlib.Path(n) for n in names]


class TestSourceSplit:
    def test_same_source_never_straddles_the_split(self):
        """Near-duplicate frames of one leaf must not appear in both halves."""
        files = paths(
            *[f"leafA_{i}.jpg" for i in range(10)],
            *[f"leafB_{i}.jpg" for i in range(10)],
            *[f"leafC_{i}.jpg" for i in range(10)],
            *[f"leafD_{i}.jpg" for i in range(10)],
        )
        train, val = source_split(files, val_fraction=0.25)

        def sources(items):
            return {p.stem.rsplit("_", 1)[0] for p in items}

        assert sources(train).isdisjoint(sources(val))

    def test_all_files_are_kept(self):
        files = paths(*[f"leaf{g}_{i}.jpg" for g in "ABCDEFGH" for i in range(4)])
        train, val = source_split(files)
        assert len(train) + len(val) == len(files)

    def test_split_is_deterministic(self):
        files = paths(*[f"leaf{g}_{i}.jpg" for g in "ABCDEFGH" for i in range(4)])
        assert source_split(files)[0] == source_split(files)[0]

    def test_validation_is_non_empty_for_realistic_input(self):
        files = paths(*[f"leaf{g}_{i}.jpg" for g in "ABCDEFGHIJ" for i in range(4)])
        _, val = source_split(files, val_fraction=0.2)
        assert val


class TestModelCard:
    def test_reports_field_accuracy_not_in_domain(self, tmp_path):
        export = tmp_path / "disease_v1.tflite"
        card_path = write_model_card(
            str(export), classes=["rice__blast", "rice__normal"],
            in_domain_accuracy=0.991, plantdoc_accuracy=0.642,
            size_bytes=2_500_000,
            config=TrainConfig(data_dir="d", export_path=str(export)),
        )
        card = json.loads(card_path.read_text())
        # The honest number is the one users see.
        assert card["evaluation"]["reported_accuracy"] == 0.642
        assert card["evaluation"]["in_domain_accuracy"] == 0.991

    def test_states_crop_limitations(self, tmp_path):
        export = tmp_path / "m.tflite"
        card_path = write_model_card(
            str(export), classes=["rice__blast"], in_domain_accuracy=0.9,
            plantdoc_accuracy=0.7, size_bytes=1,
            config=TrainConfig(data_dir="d", export_path=str(export)),
        )
        card = json.loads(card_path.read_text())
        limitations = " ".join(card["limitations"])
        assert "rice, maize, potato and wheat only" in limitations
        assert "Wheat training data is thin" in limitations

    def test_declares_the_split_rule(self, tmp_path):
        export = tmp_path / "m.tflite"
        card_path = write_model_card(
            str(export), classes=["x"], in_domain_accuracy=0.9,
            plantdoc_accuracy=0.7, size_bytes=1,
            config=TrainConfig(data_dir="d", export_path=str(export)),
        )
        card = json.loads(card_path.read_text())
        assert "never random" in card["split_rule"]

    def test_card_is_registry_ready_json(self, tmp_path):
        export = tmp_path / "m.tflite"
        card_path = write_model_card(
            str(export), classes=["a", "b"], in_domain_accuracy=0.9,
            plantdoc_accuracy=0.7, size_bytes=2_500_000,
            config=TrainConfig(data_dir="d", export_path=str(export)),
        )
        card = json.loads(card_path.read_text())
        for key in ("name", "classes", "training_data", "evaluation", "license",
                    "intended_use", "not_intended_for"):
            assert key in card
