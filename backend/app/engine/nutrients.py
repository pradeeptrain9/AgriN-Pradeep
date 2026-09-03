"""Nutrient recommendation from crop removal, soil fertility and credits.

Approach: maintenance-plus-adjustment.

    dose = (crop removal at the target yield)
           x (soil fertility factor from the soil test)
           - (nitrogen credited from organic carbon and the previous legume)

This is deliberately not a yield-response model. Fitting one needs multi-season
trial data per district, which a prototype does not have, and a fabricated
response curve would be worse than an honest maintenance figure.

Everything comes out as a RANGE with a stated confidence, because the removal
coefficients themselves are ranges (see crops.NutrientRemoval) and because the
soil source may be anything from a laboratory card to a fallback assumption.
The app must never render a single decimal figure as though it were measured.
"""

from dataclasses import dataclass, field as dc_field

from app.engine.crops import Crop

ENGINE_VERSION = "nutrients-1.0.0"

# Indian Soil Health Card fertility classes (ICAR standard methods).
#   N  by alkaline KMnO4, P by Olsen, K by neutral normal ammonium acetate.
N_CLASS_KG_HA = (280.0, 560.0)
P_CLASS_KG_HA = (10.0, 24.6)
K_CLASS_KG_HA = (108.0, 280.0)
OC_CLASS_PCT = (0.5, 0.75)

# Applied to the removal-based requirement.
FERTILITY_FACTOR = {"low": 1.25, "medium": 1.0, "high": 0.75, "unknown": 1.0}

# Indicative in-season N mineralised per 1% topsoil organic carbon, kg/ha.
N_PER_OC_PERCENT = 20.0
MAX_OC_N_CREDIT = 40.0

# Share of a legume's fixed N that carries to the following crop.
LEGUME_RESIDUAL_FRACTION = 0.30

# Straight fertiliser nutrient contents, percent by weight.
UREA_N = 0.46
DAP_N, DAP_P2O5 = 0.18, 0.46
MOP_K2O = 0.60


def classify(value: float | None, thresholds: tuple[float, float]) -> str:
    if value is None:
        return "unknown"
    low, high = thresholds
    if value < low:
        return "low"
    if value <= high:
        return "medium"
    return "high"


@dataclass
class NutrientDose:
    low_kg_ha: float
    high_kg_ha: float

    @property
    def mid_kg_ha(self) -> float:
        return (self.low_kg_ha + self.high_kg_ha) / 2

    def to_dict(self) -> dict:
        return {
            "low_kg_ha": round(self.low_kg_ha, 1),
            "high_kg_ha": round(self.high_kg_ha, 1),
            "mid_kg_ha": round(self.mid_kg_ha, 1),
        }


@dataclass
class NutrientPlan:
    engine_version: str
    crop_code: str
    target_yield_t_ha: float
    soil_source: str
    soil_confidence: str
    n_class: str
    p_class: str
    k_class: str
    n: NutrientDose
    p2o5: NutrientDose
    k2o: NutrientDose
    n_credits_kg_ha: float
    credit_breakdown: dict[str, float]
    products: dict[str, float]
    splits: list[dict]
    regenerative_actions: list[str]
    notes: list[str] = dc_field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "engine_version": self.engine_version,
            "crop_code": self.crop_code,
            "target_yield_t_ha": self.target_yield_t_ha,
            "soil_source": self.soil_source,
            "soil_confidence": self.soil_confidence,
            "fertility_class": {"n": self.n_class, "p": self.p_class, "k": self.k_class},
            "n": self.n.to_dict(),
            "p2o5": self.p2o5.to_dict(),
            "k2o": self.k2o.to_dict(),
            "n_credits_kg_ha": round(self.n_credits_kg_ha, 1),
            "credit_breakdown": {k: round(v, 1) for k, v in self.credit_breakdown.items()},
            "products_kg_ha": {k: round(v, 1) for k, v in self.products.items()},
            "splits": self.splits,
            "regenerative_actions": self.regenerative_actions,
            "notes": self.notes,
        }


def _products_for(n_kg: float, p_kg: float, k_kg: float) -> dict[str, float]:
    """Convert nutrient doses into straight fertiliser quantities.

    DAP is applied to meet phosphorus; the nitrogen it carries is subtracted
    before sizing the urea, which farmers routinely forget and over-apply.
    """
    dap = p_kg / DAP_P2O5 if p_kg > 0 else 0.0
    n_from_dap = dap * DAP_N
    urea = max(0.0, (n_kg - n_from_dap)) / UREA_N
    mop = k_kg / MOP_K2O if k_kg > 0 else 0.0
    return {
        "dap": dap,
        "urea": urea,
        "mop": mop,
        "n_supplied_by_dap": n_from_dap,
    }


def _splits(crop: Crop, n_kg: float, p_kg: float, k_kg: float) -> list[dict]:
    """Split nitrogen across the season; phosphorus and potassium go basal.

    Splitting N is the single highest-leverage efficiency change available to a
    smallholder: one full basal dose of urea is largely lost to volatilisation
    and leaching before the crop can take it up.
    """
    ini, dev, mid, _ = crop.stage_days
    return [
        {
            "when": "basal (at sowing)",
            "days_after_sowing": 0,
            "n_kg_ha": round(n_kg * 0.4, 1),
            "p2o5_kg_ha": round(p_kg, 1),
            "k2o_kg_ha": round(k_kg, 1),
        },
        {
            "when": "first top dressing (active tillering / branching)",
            "days_after_sowing": ini + dev // 2,
            "n_kg_ha": round(n_kg * 0.35, 1),
            "p2o5_kg_ha": 0.0,
            "k2o_kg_ha": 0.0,
        },
        {
            "when": "second top dressing (before flowering)",
            "days_after_sowing": ini + dev + mid // 3,
            "n_kg_ha": round(n_kg * 0.25, 1),
            "p2o5_kg_ha": 0.0,
            "k2o_kg_ha": 0.0,
        },
    ]


def recommend(
    *,
    crop: Crop,
    soil: dict,
    target_yield_t_ha: float | None = None,
    previous_crop: Crop | None = None,
    residue_retained: bool = False,
) -> NutrientPlan:
    """Build a nutrient plan. `soil` is a providers.soil.SoilProfile.to_dict()."""
    target = target_yield_t_ha or crop.nutrient.typical_yield_t_ha
    notes: list[str] = []

    n_class = classify(soil.get("available_n_kg_ha"), N_CLASS_KG_HA)
    p_class = classify(soil.get("available_p_kg_ha"), P_CLASS_KG_HA)
    k_class = classify(soil.get("available_k_kg_ha"), K_CLASS_KG_HA)

    oc_pct = soil.get("organic_carbon_pct")
    if n_class == "unknown" and oc_pct is not None:
        # Organic carbon is a reasonable stand-in for nitrogen supply when the
        # card reports OC but not available N.
        n_class = classify(oc_pct, OC_CLASS_PCT)
        notes.append("Nitrogen class inferred from organic carbon, not a direct N test.")

    removal = crop.nutrient
    base_n = (removal.n[0] * target, removal.n[1] * target)
    base_p = (removal.p2o5[0] * target, removal.p2o5[1] * target)
    base_k = (removal.k2o[0] * target, removal.k2o[1] * target)

    # Nitrogen credits.
    credits: dict[str, float] = {}
    if oc_pct is not None:
        credits["organic_carbon"] = min(oc_pct * N_PER_OC_PERCENT, MAX_OC_N_CREDIT)
    if previous_crop is not None and previous_crop.n_fixation_kg_ha > 0:
        credits["previous_legume"] = (
            previous_crop.n_fixation_kg_ha * LEGUME_RESIDUAL_FRACTION
        )
    if residue_retained:
        credits["retained_residue"] = 10.0
    total_credit = sum(credits.values())

    fn, fp, fk = (
        FERTILITY_FACTOR[n_class],
        FERTILITY_FACTOR[p_class],
        FERTILITY_FACTOR[k_class],
    )

    n_dose = NutrientDose(
        max(0.0, base_n[0] * fn - total_credit), max(0.0, base_n[1] * fn - total_credit)
    )
    p_dose = NutrientDose(base_p[0] * fp, base_p[1] * fp)
    k_dose = NutrientDose(base_k[0] * fk, base_k[1] * fk)

    # A legume fixes most of its own nitrogen; only a starter dose is justified.
    if crop.n_fixation_kg_ha > 0:
        starter = min(20.0, n_dose.low_kg_ha)
        n_dose = NutrientDose(starter, min(25.0, max(starter, n_dose.high_kg_ha)))
        notes.append(
            f"{crop.label_en} fixes its own nitrogen; only a starter dose is advised. "
            "Ensure seed is inoculated with the correct rhizobium."
        )

    products = _products_for(n_dose.mid_kg_ha, p_dose.mid_kg_ha, k_dose.mid_kg_ha)
    splits = _splits(crop, n_dose.mid_kg_ha, p_dose.mid_kg_ha, k_dose.mid_kg_ha)

    regenerative: list[str] = []
    if oc_pct is not None and oc_pct < OC_CLASS_PCT[0]:
        regenerative.append(
            "Organic carbon is low. Apply 5 t/ha of well-rotted farmyard manure or "
            "compost; this raises the nitrogen supplied by the soil itself each season."
        )
    if not residue_retained:
        regenerative.append(
            "Retain crop residue rather than burning it. Burning loses nearly all "
            "the nitrogen and the organic matter."
        )
    if previous_crop is None or previous_crop.n_fixation_kg_ha == 0:
        regenerative.append(
            "Include a legume in the next rotation to cut the following crop's "
            "nitrogen requirement."
        )
    if soil.get("ph") is not None and soil["ph"] > 8.2:
        regenerative.append(
            "Soil is alkaline. Place phosphorus in bands near the seed rather than "
            "broadcasting it, since it fixes rapidly at this pH."
        )
        notes.append("Alkaline soil reduces phosphorus availability.")
    if soil.get("ph") is not None and soil["ph"] < 5.5:
        regenerative.append("Soil is acidic. Have lime requirement assessed locally.")

    confidence = soil.get("confidence", "low")
    if confidence in ("low", "medium"):
        notes.append(
            "Soil values are estimated, not measured. Enter a Soil Health Card to "
            "tighten this recommendation."
        )
    notes.append(
        f"Removal coefficients are {removal.confidence} ({removal.source}); the "
        "range reflects real variation, so treat it as guidance and confirm locally."
    )

    return NutrientPlan(
        engine_version=ENGINE_VERSION,
        crop_code=crop.code,
        target_yield_t_ha=target,
        soil_source=soil.get("source", "unknown"),
        soil_confidence=confidence,
        n_class=n_class,
        p_class=p_class,
        k_class=k_class,
        n=n_dose,
        p2o5=p_dose,
        k2o=k_dose,
        n_credits_kg_ha=total_credit,
        credit_breakdown=credits,
        products=products,
        splits=splits,
        regenerative_actions=regenerative,
        notes=notes,
    )
