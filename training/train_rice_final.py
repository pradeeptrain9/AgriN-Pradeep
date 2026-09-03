"""Train the rice disease classifier and export int8 TFLite.

Reports two accuracies and treats the second as the real one:

  in-domain    held-out slice of Paddy Doctor. Shares camera, region, season
               and background with training, so it flatters the model.
  cross-dataset the Mendeley rice set: different collectors, different region,
               never trained on. This is what a farmer's phone is closer to.

Class imbalance is ~5:1 (normal 1499, bacterial_panicle_blight 286). Left
unweighted the model learns to skip the rare classes, which are also the ones a
farmer most needs named. Class weights are applied inversely to frequency.
"""

import json
import pathlib
import sys

import numpy as np
import tensorflow as tf

IMAGE_SIZE = 224
BATCH = 32
SEED = 1337
PREPARED = pathlib.Path("data/prepared_final")
OUT_DIR = pathlib.Path("../backend/models")


def load(split: str, shuffle: bool):
    return tf.keras.utils.image_dataset_from_directory(
        PREPARED / split,
        image_size=(IMAGE_SIZE, IMAGE_SIZE),
        batch_size=BATCH,
        label_mode="categorical",
        shuffle=shuffle,
        seed=SEED,
    )


def augmentation():
    """Aggressive on purpose: the target is a cheap phone camera outdoors."""
    return tf.keras.Sequential([
        tf.keras.layers.RandomFlip("horizontal_and_vertical"),
        tf.keras.layers.RandomRotation(0.25),
        tf.keras.layers.RandomZoom(0.25),
        tf.keras.layers.RandomTranslation(0.15, 0.15),
        tf.keras.layers.RandomContrast(0.4),
        tf.keras.layers.RandomBrightness(0.3),
    ], name="field_augmentation")


def build(num_classes: int):
    base = tf.keras.applications.MobileNetV3Small(
        input_shape=(IMAGE_SIZE, IMAGE_SIZE, 3),
        include_top=False, weights="imagenet", include_preprocessing=True,
    )
    base.trainable = False
    inputs = tf.keras.Input((IMAGE_SIZE, IMAGE_SIZE, 3))
    x = base(inputs, training=False)
    x = tf.keras.layers.GlobalAveragePooling2D()(x)
    x = tf.keras.layers.Dropout(0.3)(x)
    outputs = tf.keras.layers.Dense(num_classes, activation="softmax")(x)
    return tf.keras.Model(inputs, outputs), base


def class_weights(classes: list[str]) -> dict[int, float]:
    counts = [
        len(list((PREPARED / "train" / c).glob("*"))) for c in classes
    ]
    total = sum(counts)
    return {i: total / (len(counts) * n) for i, n in enumerate(counts) if n}


def evaluate_cross(model, train_classes: list[str]) -> dict:
    """Score only the classes the cross-dataset actually contains.

    The cross set has 4 of the 10 classes. Accuracy is computed over its images
    against the full 10-way softmax, so a prediction of any absent class counts
    as wrong -- which is the correct, harsh reading.
    """
    cross_dir = PREPARED / "test"
    present = sorted(d.name for d in cross_dir.iterdir() if d.is_dir())
    ds = tf.keras.utils.image_dataset_from_directory(
        cross_dir, image_size=(IMAGE_SIZE, IMAGE_SIZE), batch_size=BATCH,
        label_mode="int", shuffle=False,
    )
    index = {name: train_classes.index(name) for name in present}
    remap = np.array([index[name] for name in present])

    correct = total = 0
    per_class: dict[str, list[int]] = {name: [0, 0] for name in present}
    for images, labels in ds:
        predicted = np.argmax(model.predict(images, verbose=0), axis=1)
        truth = remap[labels.numpy()]
        correct += int((predicted == truth).sum())
        total += len(truth)
        for t, p in zip(truth, predicted):
            name = train_classes[t]
            per_class[name][1] += 1
            if t == p:
                per_class[name][0] += 1
    return {
        "accuracy": correct / total if total else 0.0,
        "images": total,
        "classes_evaluated": present,
        "per_class_accuracy": {
            k: round(v[0] / v[1], 4) for k, v in per_class.items() if v[1]
        },
    }


def quantise(model, classes: list[str]) -> tuple[bytes, str]:
    """Quantise, then PROVE the artifact runs before returning it.

    Full-integer quantisation (TFLITE_BUILTINS_INT8 with uint8 I/O) produced a
    model that failed at `allocate_tensors` with "Node 119 failed to prepare":
    MobileNetV3's hard-swish and squeeze-excite blocks do not survive an
    int8-only op set. Falling back to int8 weights with float activations keeps
    most of the size saving and actually runs.

    Strategies are tried in order and each is smoke-tested; the first that both
    converts AND allocates AND returns a sane probability vector wins. An
    artifact that cannot be loaded is worse than a larger one that can.
    """
    calib = sorted((PREPARED / "test").rglob("*.jpg"))[:200]

    def representative():
        for path in calib:
            raw = tf.io.read_file(str(path))
            image = tf.image.decode_image(raw, channels=3, expand_animations=False)
            image = tf.image.resize(image, (IMAGE_SIZE, IMAGE_SIZE))
            yield [tf.cast(image, tf.float32)[None, ...].numpy()]

    def full_int8(c):
        c.optimizations = [tf.lite.Optimize.DEFAULT]
        c.representative_dataset = representative
        c.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
        c.inference_input_type = tf.uint8
        c.inference_output_type = tf.uint8

    def int8_with_fallback(c):
        c.optimizations = [tf.lite.Optimize.DEFAULT]
        c.representative_dataset = representative
        c.target_spec.supported_ops = [
            tf.lite.OpsSet.TFLITE_BUILTINS_INT8, tf.lite.OpsSet.TFLITE_BUILTINS,
        ]

    def dynamic_range(c):
        c.optimizations = [tf.lite.Optimize.DEFAULT]

    for name, configure in (
        ("int8", full_int8),
        ("int8-weights-float-activations", int8_with_fallback),
        ("dynamic-range", dynamic_range),
    ):
        try:
            converter = tf.lite.TFLiteConverter.from_keras_model(model)
            configure(converter)
            blob = converter.convert()
            verify_runs(blob, len(classes))
            print(f"quantisation strategy accepted: {name}")
            return blob, name
        except Exception as exc:
            print(f"quantisation strategy '{name}' rejected: {str(exc)[:140]}")
    raise RuntimeError("no quantisation strategy produced a runnable model")


def verify_runs(blob: bytes, num_classes: int) -> None:
    """Load the artifact and run one inference. Raises if it cannot."""
    interpreter = tf.lite.Interpreter(model_content=blob)
    interpreter.allocate_tensors()          # this is what failed before
    inp = interpreter.get_input_details()[0]
    out = interpreter.get_output_details()[0]

    shape = tuple(inp["shape"])
    sample = np.zeros(shape, dtype=inp["dtype"])
    interpreter.set_tensor(inp["index"], sample)
    interpreter.invoke()
    result = interpreter.get_tensor(out["index"])[0]

    if result.shape[-1] != num_classes:
        raise RuntimeError(f"output width {result.shape[-1]} != {num_classes} classes")
    probabilities = result.astype(np.float32)
    if probabilities.max() <= 0:
        raise RuntimeError("model returned an all-zero output")


def main() -> None:
    # A leaking split produces a number that looks like success. Refuse to train
    # on one rather than discovering it after the model card is written.
    from check_leakage import check

    if check(PREPARED) != 0:
        print("\nAborting: the split leaks. Fix it before training.")
        raise SystemExit(1)

    epochs_frozen = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    epochs_tune = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    train_ds = load("train", shuffle=True)
    classes = train_ds.class_names
    print(f"classes ({len(classes)}): {classes}")
    val_ds = load("val", shuffle=False)

    augment = augmentation()
    train_ds = train_ds.map(
        lambda x, y: (augment(x, training=True), y), num_parallel_calls=tf.data.AUTOTUNE
    ).prefetch(tf.data.AUTOTUNE)
    val_ds = val_ds.prefetch(tf.data.AUTOTUNE)

    weights = class_weights(classes)
    print("class weights:", {classes[i]: round(w, 2) for i, w in weights.items()})

    model, base = build(len(classes))
    loss = tf.keras.losses.CategoricalCrossentropy(label_smoothing=0.1)
    model.compile(tf.keras.optimizers.Adam(1e-3), loss=loss, metrics=["accuracy"])
    model.fit(train_ds, validation_data=val_ds, epochs=epochs_frozen,
              class_weight=weights, verbose=2)

    base.trainable = True
    for layer in base.layers[:-80]:
        layer.trainable = False
    model.compile(tf.keras.optimizers.Adam(1e-4), loss=loss, metrics=["accuracy"])
    checkpoint = pathlib.Path("best_final.keras")
    model.fit(
        train_ds, validation_data=val_ds, epochs=epochs_tune, class_weight=weights,
        verbose=2,
        callbacks=[
            tf.keras.callbacks.ModelCheckpoint(
                checkpoint, monitor="val_accuracy", save_best_only=True, verbose=0),
            tf.keras.callbacks.ReduceLROnPlateau(
                monitor="val_accuracy", factor=0.3, patience=3, min_lr=1e-6, verbose=1),
            tf.keras.callbacks.EarlyStopping(
                monitor="val_accuracy", patience=6, restore_best_weights=True,
                verbose=1),
        ],
    )
    if checkpoint.exists():
        model = tf.keras.models.load_model(checkpoint)
        print("restored best-validation checkpoint")

    in_domain = model.evaluate(val_ds, return_dict=True, verbose=0)["accuracy"]
    cross = evaluate_cross(model, classes)
    print(f"\nin-domain (Paddy Doctor val): {in_domain:.4f}")
    print(f"cross-dataset (Mendeley)    : {cross['accuracy']:.4f} over {cross['images']} images")
    for name, acc in cross["per_class_accuracy"].items():
        print(f"   {name:<34} {acc:.3f}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tflite, precision = quantise(model, classes)
    artifact = OUT_DIR / "rice_disease.tflite"
    artifact.write_bytes(tflite)
    (OUT_DIR / "rice_disease.labels.json").write_text(json.dumps(classes, indent=2))
    print(f"\nwrote {artifact} ({len(tflite)/1e6:.2f} MB, {precision})")

    json.dump({
        "classes": classes, "in_domain_accuracy": float(in_domain),
        "cross": cross, "size_bytes": len(tflite),
    }, open(OUT_DIR / "rice_disease.metrics.json", "w"), indent=2)


if __name__ == "__main__":
    main()
