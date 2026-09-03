# rice_disease_v2 — withdrawn, evaluation invalid

Trained 2026-09-03 on Paddy Doctor + Mendeley set 1, "held out" on Mendeley set 2.

Reported at the time:
  in-domain 0.826, holdout 0.991

**Both numbers are invalid.** `check_leakage.py` found that 87.8% of the holdout
was byte-identical to training images, and 12.2% of the validation split as well.
The two Mendeley rice datasets (`fwcj7stb8r` and `dwtn3c6w6p`) are the same
images published under two DOIs, so moving one into training contaminated the
other.

The tell was in the numbers before the check confirmed it: holdout accuracy
(0.991) exceeded in-domain accuracy (0.826). A model does not do better on data
it has never seen. That ordering should be treated as evidence of leakage until
proven otherwise.

Two further defects in the source data:
  - Mendeley set 1 is 19% internally duplicated (5,932 images, 4,794 unique)
  - the same file is labelled `Blast` in one set and `Leafsmut` in the other

Weights and card are kept here for the record only. They are not served by the
model registry and must not be deployed.
