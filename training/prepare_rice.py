"""Build train/val/cross-test splits for the rice disease model.

Two datasets, used for two different jobs:

  Paddy Doctor (Kaggle)   10,407 smartphone field images, 10 classes including
                          `normal`. Train and validate here.
  Mendeley rice set        5,932 images, 4 classes, collected by a different
                          group in a different region. NEVER trained on; used
                          only as a cross-dataset test.

The cross-dataset test is the honest number. A model evaluated only on a
held-out slice of its own dataset shares that dataset's camera, lighting,
growth stage and background, and flatters itself accordingly. The gap between
the two figures is the best available estimate of what happens in a real field.

SPLIT CAVEAT: Paddy Doctor filenames are bare sequential ids (100001.jpg), so
they carry no source-leaf grouping. The by-source split used for PlantVillage
cannot be applied, and the train/val split here is a stratified random one.
That is defensible because these are field photographs rather than many frames
of one detached leaf, but it does mean the in-domain figure is optimistic and
should not be quoted on its own.
"""

import pathlib
import random
import shutil

SEED = 1337
VAL_FRACTION = 0.15

PADDY_ROOT = pathlib.Path("data/raw/paddy-disease-classification/train_images")
MENDELEY_ROOT = pathlib.Path("data/raw/rice/Rice Leaf Disease Images")
OUT = pathlib.Path("data/prepared")

# Paddy Doctor folder -> AgriN taxonomy code (app/ai/disease_taxonomy.py).
PADDY_TO_TAXONOMY = {
    "normal": "rice__normal",
    "blast": "rice__blast",
    "brown_spot": "rice__brown_spot",
    "bacterial_leaf_blight": "rice__bacterial_leaf_blight",
    "bacterial_leaf_streak": "rice__bacterial_leaf_streak",
    "bacterial_panicle_blight": "rice__bacterial_panicle_blight",
    "downy_mildew": "rice__downy_mildew",
    "hispa": "rice__hispa",
    "tungro": "rice__tungro",
    # "Dead heart" is the symptom a stem borer produces, not a separate disease.
    "dead_heart": "rice__stem_borer",
}

# Mendeley folder -> taxonomy. Only the four classes that overlap.
MENDELEY_TO_TAXONOMY = {
    "Blast": "rice__blast",
    "Brownspot": "rice__brown_spot",
    "Bacterialblight": "rice__bacterial_leaf_blight",
    "Tungro": "rice__tungro",
}


def _copy(files: list[pathlib.Path], destination: pathlib.Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    for path in files:
        shutil.copy2(path, destination / path.name)
    return len(files)


def build() -> dict:
    if OUT.exists():
        shutil.rmtree(OUT)
    rng = random.Random(SEED)
    counts: dict[str, dict[str, int]] = {"train": {}, "val": {}, "cross_test": {}}

    for folder, label in sorted(PADDY_TO_TAXONOMY.items()):
        source = PADDY_ROOT / folder
        if not source.exists():
            continue
        images = sorted(p for p in source.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
        rng.shuffle(images)
        cut = int(len(images) * (1 - VAL_FRACTION))
        counts["train"][label] = _copy(images[:cut], OUT / "train" / label)
        counts["val"][label] = _copy(images[cut:], OUT / "val" / label)

    for folder, label in sorted(MENDELEY_TO_TAXONOMY.items()):
        source = MENDELEY_ROOT / folder
        if not source.exists():
            continue
        images = sorted(p for p in source.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
        counts["cross_test"][label] = _copy(images, OUT / "cross_test" / label)

    return counts


if __name__ == "__main__":
    result = build()
    for split, classes in result.items():
        total = sum(classes.values())
        print(f"\n{split}: {total} images across {len(classes)} classes")
        for label, n in sorted(classes.items(), key=lambda x: -x[1]):
            print(f"  {label:<36} {n}")
    print("\nCross-test classes are a subset; they exist in the Mendeley set only.")
