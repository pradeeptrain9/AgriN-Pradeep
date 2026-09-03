"""Multi-collection splits for the rice model.

v1 failed because it trained on one collection and therefore learned that
collection's camera, framing and background. Cross-dataset accuracy came out at
0.159 against a 0.25 chance line, and training longer made it worse.

The fix is structural, not a hyperparameter:

  TRAIN     Paddy Doctor (Kaggle) + Mendeley set 1
            Two independent collections, different regions and cameras. A model
            that sees two cameras has some chance of learning to ignore the
            camera; one that sees a single camera has none.

  HOLD OUT  Mendeley set 2, never trained on, different collectors again.
            This is the honest number.

Leafsmut appears only in the third collection and has no class in the AgriN
taxonomy, so it is excluded: accuracy cannot be scored for a class the model was
never taught.
"""

import pathlib
import random
import shutil

SEED = 1337
VAL_FRACTION = 0.15
OUT = pathlib.Path("data/prepared_multi")

PADDY = pathlib.Path("data/raw/paddy-disease-classification/train_images")
MEND1 = pathlib.Path("data/raw/rice/Rice Leaf Disease Images")
MEND2 = pathlib.Path("data/raw/rice2/rice leaf diseases dataset")

PADDY_MAP = {
    "normal": "rice__normal", "blast": "rice__blast", "brown_spot": "rice__brown_spot",
    "bacterial_leaf_blight": "rice__bacterial_leaf_blight",
    "bacterial_leaf_streak": "rice__bacterial_leaf_streak",
    "bacterial_panicle_blight": "rice__bacterial_panicle_blight",
    "downy_mildew": "rice__downy_mildew", "hispa": "rice__hispa",
    "tungro": "rice__tungro", "dead_heart": "rice__stem_borer",
}
MEND1_MAP = {
    "Blast": "rice__blast", "Brownspot": "rice__brown_spot",
    "Bacterialblight": "rice__bacterial_leaf_blight", "Tungro": "rice__tungro",
}
# Leafsmut deliberately absent: no taxonomy class, so it cannot be scored.
MEND2_MAP = {
    "Brownspot": "rice__brown_spot", "Bacterialblight": "rice__bacterial_leaf_blight",
}

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def images_in(directory: pathlib.Path) -> list[pathlib.Path]:
    if not directory.exists():
        return []
    return sorted(p for p in directory.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)


def copy_to(files: list[pathlib.Path], destination: pathlib.Path, prefix: str) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    for path in files:
        # Prefix with the collection so provenance survives into the split and a
        # future audit can tell which source an image came from.
        shutil.copy2(path, destination / f"{prefix}__{path.name}")
    return len(files)


def build() -> dict:
    if OUT.exists():
        shutil.rmtree(OUT)
    rng = random.Random(SEED)
    counts: dict[str, dict[str, int]] = {"train": {}, "val": {}, "holdout": {}}

    for root, mapping, prefix in (
        (PADDY, PADDY_MAP, "paddy"),
        (MEND1, MEND1_MAP, "mend1"),
    ):
        for folder, label in sorted(mapping.items()):
            files = images_in(root / folder)
            if not files:
                continue
            rng.shuffle(files)
            cut = int(len(files) * (1 - VAL_FRACTION))
            counts["train"][label] = counts["train"].get(label, 0) + copy_to(
                files[:cut], OUT / "train" / label, prefix)
            counts["val"][label] = counts["val"].get(label, 0) + copy_to(
                files[cut:], OUT / "val" / label, prefix)

    for folder, label in sorted(MEND2_MAP.items()):
        files = images_in(MEND2 / folder)
        if files:
            counts["holdout"][label] = copy_to(files, OUT / "holdout" / label, "mend2")

    return counts


if __name__ == "__main__":
    result = build()
    for split, classes in result.items():
        total = sum(classes.values())
        print(f"\n{split}: {total} images, {len(classes)} classes")
        for label, n in sorted(classes.items(), key=lambda x: -x[1]):
            print(f"  {label:<36} {n}")
    print("\nTrain draws on TWO collections; holdout is a THIRD, never trained on.")
