"""Refuse to train on a leaking split.

Written after a split that reported 0.991 on a "held-out" set of which 100% was
byte-identical to training data. The two Mendeley rice datasets turned out to be
the same images re-released under a second DOI, so moving one into training
silently contaminated the other.

An evaluation number from a leaking split is worse than no number: it looks like
success. This check runs before training and exits non-zero, so a contaminated
split cannot quietly produce a model card.

Three things are checked:
  1. no image hash appears in both train and any evaluation split
  2. duplicates WITHIN a split are reported (they inflate val scores)
  3. no hash is filed under two different labels
"""

import hashlib
import pathlib
import sys
from collections import defaultdict

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def index(root: pathlib.Path) -> dict[str, list[pathlib.Path]]:
    found: dict[str, list[pathlib.Path]] = defaultdict(list)
    if not root.exists():
        return found
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            found[hashlib.md5(path.read_bytes()).hexdigest()].append(path)
    return found


def check(prepared: pathlib.Path, train_split: str = "train") -> int:
    splits = {d.name: index(d) for d in sorted(prepared.iterdir()) if d.is_dir()}
    if train_split not in splits:
        print(f"no '{train_split}' split under {prepared}")
        return 2

    train = splits[train_split]
    problems = 0

    print(f"{'split':<12} {'images':>8} {'unique':>8} {'dupes':>7}")
    for name, table in splits.items():
        total = sum(len(v) for v in table.values())
        print(f"{name:<12} {total:>8} {len(table):>8} {total - len(table):>7}")

    for name, table in splits.items():
        if name == train_split:
            continue
        overlap = set(train) & set(table)
        if overlap:
            share = 100 * len(overlap) / max(len(table), 1)
            print(f"\nLEAK: {len(overlap)} unique images ({share:.1f}% of '{name}') "
                  f"are byte-identical to training data")
            for h in list(overlap)[:3]:
                print(f"   {train[h][0].parent.name}/{train[h][0].name}"
                      f"  ==  {table[h][0].parent.name}/{table[h][0].name}")
            problems += 1
        else:
            print(f"\nclean: '{name}' shares no image with '{train_split}'")

    for name, table in splits.items():
        conflicts = [h for h, paths in table.items()
                     if len({p.parent.name for p in paths}) > 1]
        if conflicts:
            print(f"\nLABEL CONFLICT in '{name}': {len(conflicts)} images filed "
                  "under more than one class")
            for h in conflicts[:3]:
                print(f"   {[f'{p.parent.name}/{p.name}' for p in table[h]]}")
            problems += 1

    if problems:
        print(f"\nFAILED: {problems} problem(s). Any accuracy from this split is invalid.")
    else:
        print("\nPASSED: splits are disjoint and consistently labelled.")
    return 1 if problems else 0


if __name__ == "__main__":
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "data/prepared_multi")
    raise SystemExit(check(root))
