"""Disease class taxonomy and per-disease integrated pest management advice.

DATASET REALITY (verified 2026-09-02). The original plan assumed PlantVillage
would carry the disease model. It cannot: PlantVillage covers 14 crops -- apple,
bell pepper, cherry, corn, grape, orange, peach, potato, raspberry, soybean,
squash, strawberry, tomato, blueberry -- and contains **no rice and no wheat**,
the two staples this network exists to serve. Against our 14-crop registry it
overlaps on maize, potato and soybean only.

The dataset stack is therefore crop-led, not dataset-led:

  rice     Paddy Doctor -- 16,225 smartphone images from real fields in Tamil
           Nadu at 40-80 days crop age, 13 classes. Field conditions, which is
           what the app will actually see.
  maize    PlantVillage (lab-biased) + PlantDoc for field realism.
  potato   PlantVillage + PlantDoc.
  wheat    Thin. WFD2020 is ~2,414 images across five fungal diseases; the
           CGIAR/Zindi set is rust-only from Ethiopia and Tanzania. Expect
           materially lower accuracy and say so in the UI.

Every other crop in the registry -- cotton, chickpea, groundnut, sorghum, pearl
millet, mustard, sunflower, sugarcane -- has no curated dataset behind it here.

That is why coverage gates the on-device model rather than confidence alone. A
softmax always sums to one, so a classifier that has never seen cotton will
still return a confident rice label for a cotton leaf. Crops outside
SUPPORTED_CROPS must never reach the on-device classifier.

IPM advice below is cultural and preventive: resistant varieties, spacing,
drainage, residue handling, balanced nitrogen. It carries no dosages, so it is
safe to ship without per-country verification. Chemical recommendations are a
separate, verified-only path -- see engine/treatments.py.
"""

from dataclasses import dataclass, field as dc_field

# Crops the on-device classifier is trained for. Anything else is out of
# distribution and must be routed away from it.
# Crops the SHIPPED on-device model can actually answer -- not crops that have
# a disease taxonomy, which is a different and larger set.
#
# This listed maize, potato and wheat while the only trained weights in this
# repository are rice-only (models/rice_disease.labels.json is ten rice
# classes). The gate treated those crops as covered, so a confident prediction
# for one of them would have been accepted on device with no trained model
# behind it. Keep this in step with the labels file, not with the taxonomy.
SUPPORTED_CROPS = frozenset({"rice"})

# Crops with thin training data; diagnoses are returned with a lowered ceiling.
LOW_CONFIDENCE_CROPS = frozenset({"wheat_spring", "wheat_winter"})

DATASET_SOURCES = {
    "rice": "Paddy Doctor (16,225 field images, 13 classes)",
    "maize": "PlantVillage + PlantDoc",
    "potato": "PlantVillage + PlantDoc",
    "wheat_spring": "WFD2020 + CGIAR rust (limited)",
    "wheat_winter": "WFD2020 + CGIAR rust (limited)",
}


@dataclass(frozen=True)
class DiseaseClass:
    code: str                 # stable identifier, crop-scoped
    crop_code: str
    label_en: str
    is_healthy: bool = False
    pathogen_type: str = "fungal"   # fungal | bacterial | viral | insect | none
    ipm_actions: tuple[str, ...] = dc_field(default_factory=tuple)
    notes: str = ""


_RICE_HYGIENE = (
    "Remove and destroy infected stubble and volunteer plants after harvest.",
    "Avoid excess nitrogen; heavy early N makes the canopy dense and disease-prone.",
)

DISEASES: dict[str, DiseaseClass] = {
    # ------------------------------------------------------------------ rice
    "rice__normal": DiseaseClass(
        code="rice__normal", crop_code="rice", label_en="Healthy leaf",
        is_healthy=True, pathogen_type="none",
        ipm_actions=("No disease seen. Keep monitoring weekly.",),
    ),
    "rice__blast": DiseaseClass(
        code="rice__blast", crop_code="rice", label_en="Rice blast",
        pathogen_type="fungal",
        ipm_actions=(
            "Grow a blast-resistant variety where one is available locally.",
            "Split nitrogen into smaller doses instead of one heavy application.",
            "Keep a thin film of water in the field; do not let it dry out fully "
            "during the vulnerable stages.",
            *_RICE_HYGIENE,
        ),
        notes="Diamond-shaped lesions with grey centres and brown margins.",
    ),
    "rice__brown_spot": DiseaseClass(
        code="rice__brown_spot", crop_code="rice", label_en="Brown spot",
        pathogen_type="fungal",
        ipm_actions=(
            "Brown spot is usually a sign of poor soil fertility, especially "
            "potassium. Correct the nutrient plan.",
            "Treat seed before sowing and use healthy seed.",
            *_RICE_HYGIENE,
        ),
        notes="Often indicates nutrient-starved soil rather than a spray problem.",
    ),
    "rice__bacterial_leaf_blight": DiseaseClass(
        code="rice__bacterial_leaf_blight", crop_code="rice",
        label_en="Bacterial leaf blight", pathogen_type="bacterial",
        ipm_actions=(
            "Use a resistant variety; this is the main control.",
            "Drain the field for a few days where the crop stage allows it.",
            "Reduce nitrogen. Do not spray fungicide, it does not act on bacteria.",
            *_RICE_HYGIENE,
        ),
    ),
    "rice__bacterial_leaf_streak": DiseaseClass(
        code="rice__bacterial_leaf_streak", crop_code="rice",
        label_en="Bacterial leaf streak", pathogen_type="bacterial",
        ipm_actions=(
            "Use clean, treated seed.",
            "Avoid injuring leaves during weeding and transplanting.",
            "Fungicides do not control bacterial disease.",
        ),
    ),
    "rice__bacterial_panicle_blight": DiseaseClass(
        code="rice__bacterial_panicle_blight", crop_code="rice",
        label_en="Bacterial panicle blight", pathogen_type="bacterial",
        ipm_actions=(
            "Avoid dense planting and excess nitrogen.",
            "Use healthy seed from an unaffected field.",
        ),
    ),
    "rice__tungro": DiseaseClass(
        code="rice__tungro", crop_code="rice", label_en="Tungro virus",
        pathogen_type="viral",
        ipm_actions=(
            "Tungro is spread by green leafhopper. Control the insect, not the virus.",
            "Remove and destroy infected plants early.",
            "Synchronise planting with neighbouring fields to break the insect cycle.",
        ),
    ),
    "rice__downy_mildew": DiseaseClass(
        code="rice__downy_mildew", crop_code="rice", label_en="Downy mildew",
        pathogen_type="fungal",
        ipm_actions=("Improve drainage.", "Use treated seed.", *_RICE_HYGIENE),
    ),
    "rice__hispa": DiseaseClass(
        code="rice__hispa", crop_code="rice", label_en="Rice hispa",
        pathogen_type="insect",
        ipm_actions=(
            "Clip and destroy heavily infested leaf tips.",
            "Avoid excess nitrogen, which attracts the beetle.",
            "Hand-collect adults early in the morning in small plots.",
        ),
    ),
    "rice__leaf_roller": DiseaseClass(
        code="rice__leaf_roller", crop_code="rice", label_en="Leaf roller",
        pathogen_type="insect",
        ipm_actions=(
            "Encourage natural enemies; avoid broad-spectrum sprays that kill them.",
            "Avoid excess nitrogen.",
        ),
    ),
    "rice__stem_borer": DiseaseClass(
        code="rice__stem_borer", crop_code="rice", label_en="Stem borer",
        pathogen_type="insect",
        ipm_actions=(
            "Cut stubble low at harvest and destroy it to kill overwintering larvae.",
            "Use pheromone traps to time any intervention.",
            "Release Trichogramma where it is available locally.",
        ),
        notes="Covers black, white and yellow stem borer classes.",
    ),
    # ----------------------------------------------------------------- maize
    "maize__healthy": DiseaseClass(
        code="maize__healthy", crop_code="maize", label_en="Healthy leaf",
        is_healthy=True, pathogen_type="none",
        ipm_actions=("No disease seen. Keep monitoring weekly.",),
    ),
    "maize__gray_leaf_spot": DiseaseClass(
        code="maize__gray_leaf_spot", crop_code="maize", label_en="Grey leaf spot",
        pathogen_type="fungal",
        ipm_actions=(
            "Rotate away from maize for at least one season.",
            "Bury or remove infected residue; the fungus survives on it.",
            "Avoid very dense planting, which keeps the canopy wet.",
        ),
    ),
    "maize__common_rust": DiseaseClass(
        code="maize__common_rust", crop_code="maize", label_en="Common rust",
        pathogen_type="fungal",
        ipm_actions=(
            "Plant a resistant hybrid where available.",
            "Sow early to escape the peak rust period.",
        ),
    ),
    "maize__northern_leaf_blight": DiseaseClass(
        code="maize__northern_leaf_blight", crop_code="maize",
        label_en="Northern leaf blight", pathogen_type="fungal",
        ipm_actions=(
            "Rotate with a non-cereal crop.",
            "Remove or plough in infected residue.",
            "Use a resistant hybrid.",
        ),
    ),
    # ---------------------------------------------------------------- potato
    "potato__healthy": DiseaseClass(
        code="potato__healthy", crop_code="potato", label_en="Healthy leaf",
        is_healthy=True, pathogen_type="none",
        ipm_actions=("No disease seen. Keep monitoring weekly.",),
    ),
    "potato__early_blight": DiseaseClass(
        code="potato__early_blight", crop_code="potato", label_en="Early blight",
        pathogen_type="fungal",
        ipm_actions=(
            "Rotate out of potato and tomato for two seasons.",
            "Keep the crop well fed; weak plants are hit hardest.",
            "Remove infected lower leaves early.",
        ),
    ),
    "potato__late_blight": DiseaseClass(
        code="potato__late_blight", crop_code="potato", label_en="Late blight",
        pathogen_type="fungal",
        ipm_actions=(
            "Act fast. Late blight can destroy a crop within days in cool, wet weather.",
            "Destroy infected plants and volunteer potatoes immediately.",
            "Earth up ridges well so spores cannot reach the tubers.",
            "Contact your extension officer today; this disease usually needs "
            "immediate coordinated action.",
        ),
        notes="Highest-urgency class in the taxonomy.",
    ),
    # ----------------------------------------------------------------- wheat
    "wheat__healthy": DiseaseClass(
        code="wheat__healthy", crop_code="wheat_spring", label_en="Healthy leaf",
        is_healthy=True, pathogen_type="none",
        ipm_actions=("No disease seen. Keep monitoring weekly.",),
    ),
    "wheat__leaf_rust": DiseaseClass(
        code="wheat__leaf_rust", crop_code="wheat_spring", label_en="Leaf rust",
        pathogen_type="fungal",
        ipm_actions=(
            "Grow a rust-resistant variety; this is the single most effective step.",
            "Sow on time. Late-sown wheat is hit harder by rust.",
            "Remove volunteer wheat plants that carry rust between seasons.",
        ),
    ),
    "wheat__stripe_rust": DiseaseClass(
        code="wheat__stripe_rust", crop_code="wheat_spring", label_en="Stripe rust",
        pathogen_type="fungal",
        ipm_actions=(
            "Grow a resistant variety.",
            "Report early outbreaks; stripe rust spreads regionally on the wind.",
            "Avoid excess nitrogen late in the season.",
        ),
    ),
    "wheat__stem_rust": DiseaseClass(
        code="wheat__stem_rust", crop_code="wheat_spring", label_en="Stem rust",
        pathogen_type="fungal",
        ipm_actions=(
            "Grow a resistant variety and report outbreaks to the extension service.",
            "Sow early to escape the peak period.",
        ),
    ),
    "wheat__septoria": DiseaseClass(
        code="wheat__septoria", crop_code="wheat_spring",
        label_en="Septoria leaf blotch", pathogen_type="fungal",
        ipm_actions=(
            "Rotate away from wheat for a season.",
            "Bury infected residue.",
            "Avoid very early sowing in high-rainfall areas.",
        ),
    ),
    "wheat__powdery_mildew": DiseaseClass(
        code="wheat__powdery_mildew", crop_code="wheat_spring",
        label_en="Powdery mildew", pathogen_type="fungal",
        ipm_actions=(
            "Avoid dense sowing and excess nitrogen.",
            "Grow a resistant variety where available.",
        ),
    ),
}

# Diseases that warrant immediate action regardless of confidence.
URGENT_CLASSES = frozenset({"potato__late_blight", "rice__blast"})


def get_disease(code: str) -> DiseaseClass:
    if code not in DISEASES:
        raise KeyError(f"unknown disease class: {code!r}")
    return DISEASES[code]


def classes_for_crop(crop_code: str) -> list[DiseaseClass]:
    """Wheat variants share one trained class set."""
    normalised = "wheat_spring" if crop_code.startswith("wheat") else crop_code
    return [d for d in DISEASES.values() if d.crop_code == normalised]


def is_crop_supported(crop_code: str) -> bool:
    return crop_code in SUPPORTED_CROPS
