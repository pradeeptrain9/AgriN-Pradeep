"""Splits for the shipping rice model, deduplicated by content hash.

Two failures forced this design:

  v1  trained on one collection, so it learned that collection's camera.
      Cross-dataset accuracy 0.159 against a 0.25 chance line. That number was
      valid -- Paddy Doctor and Mendeley share zero images.

  v2  trained on two collections and "held out" a third that turned out to be
      the same images under a second DOI. 87.8% of the holdout was byte-identical
      to training data, so both its numbers were meaningless.

What is actually available: two independent collections, one of which is 19%
internally duplicated and has a near-identical twin published separately.

This builds:

  train  Paddy Doctor (10 classes) + Mendeley slice A (4 classes)
         Two cameras, so the model has some chance of learning to ignore
         the camera rather than memorising it.
  val    Paddy Doctor held-out slice
  test   Mendeley slice B

Every image is hashed first and assigned to exactly ONE split, so a duplicate
cannot straddle the boundary. Slice A and B are disjoint by hash, and the twin
dataset is excluded entirely rather than trusted.

HONEST LIMIT, stated here because the model card repeats it: `test` comes from
the same collection as part of `train`, so it measures within-collection
generalisation. It is NOT a cross-collection estimate. Producing one needs a
third genuinely independent collection, which the open rice datasets do not
provide.
"""

import hashlib
import pathlib
import random
import shutil
from collections import defaultdict

SEED = 1337
PADDY_VAL_FRACTION = 0.15
MENDELEY_TEST_FRACTION = 0.35
OUT = pathlib.Path("data/prepared_final")

PADDY = pathlib.Path("data/raw/paddy-disease-classification/train_images")
MENDELEY = pathlib.Path("data/raw/rice/Rice Leaf Disease Images")

PADDY_MAP = {
    "normal": "rice__normal", "blast": "rice__blast", "brown_spot": "rice__brown_spot",
    "bacterial_leaf_blight": "rice__bacterial_leaf_blight",
    "bacterial_leaf_streak": "rice__bacterial_leaf_streak",
    "bacterial_panicle_blight": "rice__bacterial_panicle_blight",
    "downy_mildew": "rice__downy_mildew", "hispa": "rice__hispa",
    "tungro": "rice__tungro", "dead_heart": "rice__stem_borer",
}
MENDELEY_MAP = {
    "Blast": "rice__blast", "Brownspot": "rice__brown_spot",
    "Bacterialblight": "rice__bacterial_leaf_blight", "Tungro": "rice__tungro",
}

SUFFIXES = {".jpg", ".jpeg", ".png"}


def unique_by_hash(root: pathlib.Path, mapping: dict) -> dict[str, list[tuple[str, pathlib.Path]]]:
    """One representative file per content hash, grouped by label.

    Deduplicating BEFORE splitting is the only way a duplicate cannot end up on
    both sides. Mendeley is 19% duplicated, so this removes roughly 1,100 images.
    """
    seen: set[str] = set()
    by_label: dict[str, list[tuple[str, pathlib.Path]]] = defaultdict(list)
    for folder, label in sorted(mapping.items()):
        directory = root / folder
        if not directory.exists():
            continue
        for path in sorted(p for p in directory.iterdir() if p.suffix.lower() in SUFFIXES):
            digest = hashlib.md5(path.read_bytes()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            by_label[label].append((digest, path))
    return by_label


def copy(files, destination: pathlib.Path, prefix: str) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    for _, path in files:
        shutil.copy2(path, destination / f"{prefix}__{path.name}")
    return len(files)


def build() -> dict:
    if OUT.exists():
        shutil.rmtree(OUT)
    rng = random.Random(SEED)
    counts: dict[str, dict[str, int]] = defaultdict(dict)

    paddy = unique_by_hash(PADDY, PADDY_MAP)
    for label, files in paddy.items():
        rng.shuffle(files)
        cut = int(len(files) * (1 - PADDY_VAL_FRACTION))
        counts["train"][label] = counts["train"].get(label, 0) + copy(
            files[:cut], OUT / "train" / label, "paddy")
        counts["val"][label] = copy(files[cut:], OUT / "val" / label, "paddy")

    mendeley = unique_by_hash(MENDELEY, MENDELEY_MAP)
    for label, files in mendeley.items():
        rng.shuffle(files)
        cut = int(len(files) * MENDELEY_TEST_FRACTION)
        counts["test"][label] = copy(files[:cut], OUT / "test" / label, "mend")
        counts["train"][label] = counts["train"].get(label, 0) + copy(
            files[cut:], OUT / "train" / label, "mend")

    return counts


if __name__ == "__main__":
    result = build()
    for split in ("train", "val", "test"):
        classes = result.get(split, {})
        print(f"\n{split}: {sum(classes.values())} images, {len(classes)} classes")
        for label, n in sorted(classes.items(), key=lambda x: -x[1]):
            print(f"  {label:<36} {n}")
    print("\nAll images deduplicated by content hash before splitting.")
