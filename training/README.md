# Disease model training

Produces the int8 TFLite classifier bundled in the Android app.

## Why not PlantVillage alone

The original plan assumed PlantVillage. It covers 14 crops — apple, bell pepper,
cherry, corn, grape, orange, peach, potato, raspberry, soybean, squash,
strawberry, tomato, blueberry — and contains **no rice and no wheat**. Against
the AgriN crop registry it overlaps on maize, potato and soybean only, so a
PlantVillage-trained model could not diagnose the two staples this network
exists to serve.

It also has a well-documented bias problem: uniform lab backgrounds, single
detached leaves, even lighting. Models score ~99% on its own test split and fall
apart on field photos.

## Dataset stack

| crop | dataset | images | notes |
|---|---|---|---|
| rice | [Paddy Doctor](https://ieee-dataport.org/documents/paddy-doctor-visual-image-dataset-automated-paddy-disease-classification-and-benchmarking) | 16,225 | 13 classes, smartphone photos from real fields in Tamil Nadu at 40–80 days crop age. Matches deployment conditions. |
| maize | PlantVillage (corn subset) | ~3,850 | 4 classes, lab-biased |
| potato | PlantVillage (potato subset) | ~2,150 | 3 classes, lab-biased |
| wheat | WFD2020 + CGIAR/Zindi rust | ~2,400 | 5–6 classes. **Thin.** Expect materially lower accuracy. |
| all | [PlantDoc](https://github.com/pratikkayal/PlantDoc-Dataset) | 2,598 | Field-condition images. **Held out entirely as the honest test set.** |

Everything else in the crop registry — cotton, chickpea, groundnut, sorghum,
pearl millet, mustard, sunflower, sugarcane — has no curated dataset here. Those
crops are excluded from `SUPPORTED_CROPS` and never reach the on-device model;
they route to cloud diagnosis instead. See `app/ai/disease_taxonomy.py`.

## The split rule

**Split by source, never randomly.** A random split over PlantVillage leaks
near-duplicate frames of the same leaf into both train and test, which is how
the 99% figures happen. The rule here:

- train / val: Paddy Doctor + PlantVillage + WFD2020, split by source image id
- test: **PlantDoc only**, plus a held-out Paddy Doctor field subset

The number reported to users is the PlantDoc number. The in-domain number is
recorded too, and the gap between them is the interesting quantity — it is the
best available estimate of how much worse the model gets in a real field.

## Running

Free Colab T4 is enough (~2 hours).

```bash
python training/train_disease.py --data ./data --epochs 30 --export models/disease_v1.tflite
```

Outputs `disease_v1.tflite` (int8, ~2.5 MB) plus `disease_v1.model_card.json`,
which the federation model registry serves verbatim.

## Getting the data

Paddy Doctor and PlantVillage need a Kaggle account (`~/.kaggle/kaggle.json`).
PlantDoc clones from GitHub. `python training/fetch_datasets.py --help` prints
the exact commands; it deliberately does not download anything on import.


---

# Result: rice_disease_v1 — trained, evaluated, REJECTED

Trained 2026-09-03 on Paddy Doctor (10,407 images, 10 classes including `normal`),
evaluated against the Mendeley rice set (5,932 images, different collectors and
region, never trained on).

| metric | value |
|---|---|
| in-domain (Paddy Doctor val) | **0.701** |
| cross-dataset (Mendeley) | **0.159** |
| chance level on the 4 cross-dataset classes | 0.25 |

**Cross-dataset accuracy is below chance. The model is not deployed.**

It has not collapsed onto one class — predictions spread across all ten at
near-random rates — so it is genuinely confused rather than degenerate. It
learned the Paddy Doctor collection's camera, framing, background and growth
stage rather than disease morphology.

The decisive evidence is the divergence under longer training:

| | in-domain | cross-dataset |
|---|---|---|
| 10 epochs | 0.545 | 0.280 |
| 24 epochs | 0.701 | **0.159** |

Training longer made it better at the dataset and **worse at the task**. That is
the signature of dataset overfitting, and it is exactly what the cross-dataset
protocol exists to detect. Reporting the in-domain 0.701 alone would have looked
like acceptable progress.

## What would actually fix it

1. **Train across collections, not one.** Combine Paddy Doctor with the Mendeley
   sets and hold out a third source entirely. A model that never sees more than
   one camera cannot learn to ignore the camera.
2. **Break the background dependency** — segment or crop to the leaf, and
   composite lesions onto varied backgrounds during augmentation.
3. **More capacity and more epochs on a GPU.** 24 CPU epochs was still improving
   at cutoff, so it is undertrained on top of everything else.
4. **Per-class thresholds.** `bacterial_leaf_blight` scored 0.020 cross-dataset;
   some classes may never be safe to report at all.

## Effect on the app

None. No weights are bundled, `isModelLoaded()` returns false, and every photo
routes to server diagnosis exactly as it does for an unsupported crop. The
coverage-and-confidence gate in `app/ai/disease.py` was built for precisely this
state.

## Export note

Full-integer quantisation produced an artifact that failed `allocate_tensors`
with *"Node 119 failed to prepare"* — MobileNetV3's hard-swish and squeeze-excite
blocks do not survive an int8-only op set. Two int8 strategies were rejected and
dynamic-range accepted. Every candidate is now smoke-tested by loading it and
running one inference before acceptance, so an unloadable artifact can no longer
be published.


---

# v2: multi-collection retrain — withdrawn, evaluation invalid

The v1 diagnosis was that training on a single collection taught the model that
collection's camera rather than the disease. The fix was to train on two
collections and hold out a third.

It reported **in-domain 0.826, holdout 0.991**. Both numbers are invalid.

## What went wrong

The two Mendeley rice datasets (`fwcj7stb8r` and `dwtn3c6w6p`) are **the same
images published under two DOIs**. Moving one into training contaminated the
other:

| split | leaked from training |
|---|---|
| v2 holdout | **87.8%** |
| v2 validation | 12.2% |

The tell was visible before the check confirmed it: **holdout accuracy exceeded
in-domain accuracy**. A model does not perform better on data it has never seen.
That ordering should be treated as evidence of leakage until proven otherwise.

## Two further defects in the source data

- Mendeley set 1 is **19% internally duplicated** — 5,932 images, 4,794 unique.
- The same file is labelled `Blast` in one dataset and `Leafsmut` in the other.
  At least one of those labels is wrong, and there is no way to tell which.

## What survived

Paddy Doctor and Mendeley share **zero** images, so they are genuinely
independent collections. That makes **v1's cross-dataset figure of 0.159 valid**,
and it remains the only sound generalisation estimate for this task. v1's
in-domain 0.701 is inflated by about 1% (16 leaked validation images, from
Paddy Doctor's own 74 internal duplicates).

## The durable fix

`check_leakage.py` now runs before training and **aborts on a leaking split**:

```bash
python check_leakage.py data/prepared_multi
```

It reports cross-split hash collisions, within-split duplicates, and images filed
under more than one label. A contaminated split can no longer quietly produce a
model card, because an accuracy number from a leaking split is worse than no
number — it looks like success.

## What is actually needed

A **third genuinely independent collection**. Two collections cannot both train a
multi-collection model and measure whether multi-collection training helped. The
open Mendeley rice datasets do not provide one; they are re-releases of each
other. Candidates worth checking for independence before use: the Paddy Doctor
IEEE DataPort release (13 classes, larger than the Kaggle mirror), and
field-collected imagery from a partner node in another country — which is
precisely what the federation model registry exists to make possible.


---

# Shipping model: rice_disease v1.0.0

Third attempt. The first two are documented above and both failed honestly.

## Design

Deduplicate everything by content hash, then split so no image can straddle a
boundary. `check_leakage.py` runs before training and aborts otherwise.

| split | images | source |
|---|---|---|
| train | 11,896 | Paddy Doctor + 65% of Mendeley set 1 |
| val | 1,554 | Paddy Doctor, held out |
| test | 1,677 | Mendeley slice B, hash-disjoint |

Leakage gate: **PASSED** — zero cross-split overlap, zero within-split duplicates.

## Results

| metric | value |
|---|---|
| accuracy of predictions SHOWN to a farmer | **0.981** |
| coverage | 44.4% answered on device |
| raw model | 0.786 |
| escalated would have been | 0.630 |
| in-domain (Paddy val, 10 classes) | 0.822 |
| same-collection test (Mendeley B, 4 classes) | 0.994 |

The gate is what makes this shippable. Raw accuracy of 0.786 is not good enough
to show a farmer; 0.981 is. The cost is that 56% of photos need connectivity or
return inconclusive.

Per-class raw accuracy is uneven — `bacterial_leaf_blight` 0.500, `hispa` 0.600,
`normal` 0.633 — and the gate absorbs exactly those. **Zero of 18 healthy leaves
were shown a disease label** after gating, against 37% before.

## Honest limits

- Mendeley B is the **same collection** as part of training and covers 4 of 10
  classes. It is not a cross-collection estimate and must not be quoted as one.
- No cross-collection number exists for this model. Field performance in an
  unseen region is unmeasured.
- Rice only. Every other crop is refused by the coverage gate.

## On-device verification

Two bugs only a device exposed:

**Excluding `libtensorflowlite_gpu_jni.so` crashed the app.** It was excluded to
save 1.25 MB per ABI on the reasoning that only the CPU delegate is used. But
`libVisionCameraTflite.so` links against it, so `dlopen` failed at load with
`UnsatisfiedLinkError` and took the process down. The dependency is resolved at
link time, not when a delegate is chosen. The exclusion is reverted, with the
reason recorded in `android/app/build.gradle` so nobody re-applies it.

**Nearest-neighbour resampling cost 16 points of confidence.** Measured on one
held-out leaf: bilinear 0.873, nearest 0.715. Training used `tf.image.resize`,
which is bilinear and stretches rather than letterboxes, so the client now does
exactly that. On device the same leaf went from 0.54 confidence (escalated) to
**0.89 (answered on the phone, correctly, as Brown spot)**. Capture sizes were
also reduced to 448/640 px so the picker does not resample before we do.
